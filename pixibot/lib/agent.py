"""ReasoningAgent — a stateless agent that runs a tool loop per activation.

DESIGN.md §1 defines an agent as *one config passed to the same loop*. That is
literal here: an agent is an :class:`AgentSpec` (who it is), a :class:`RunContext`
(what every agent in this run shares) and a :class:`Toolset` (how it affects the
world). The loop is this class.

Statelessness (DESIGN.md §9): each activation rebuilds context from the
blackboard — the inbox events that woke it, its own stored transcript, and the
sections it reads — runs the model→tool loop until the model stops calling
tools, and writes results back to the blackboard.

Its ``runner`` method plugs directly into ``ContextManager``.

Contract: docs/contracts/pixibot.lib.agent.md
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..blackboard import KIND_ARTIFACT, KIND_CONTROL, KIND_MESSAGE, Blackboard, Event
from ..model import Model, ModelResponse, ToolCall
from .context_manager import STATE_DONE
from .logbook import get as _get_logger
from .tools import DEFAULT_TOOL_DEFS, default_tool_impls

if TYPE_CHECKING:  # avoids an import cycle at runtime (tools -> agent attributes)
    from ..workspace import Workspace

_log = _get_logger("agent")

# on_event(agent_id, kind, detail): the live activity feed the panes render.
EventHook = Callable[[str, str, str], None]
# tool(agent, blackboard, input) -> result text shown to the model
ToolImpl = Callable[["ReasoningAgent", Blackboard, dict[str, Any]], str]


@dataclass(frozen=True)
class AgentSpec:
    """Who one agent is: its identity, prompt, budget scope and read access."""

    agent_id: str
    model: Model
    system_prompt: str = ""
    role: str | None = None
    depth: str | None = None
    scope: str | None = None             # sections it may write (scope-as-write-lock)
    reads: tuple[str, ...] = ()          # sections pulled into its context
    read_denylist: tuple[str, ...] = ()  # sections it may NOT read (adoption D)
    max_iters: int = 14


@dataclass(frozen=True)
class RunContext:
    """What every agent in one run shares: where files live, the goal, the feed.

    One object instead of three parameters threaded through engine ->
    materialize -> register_one -> agent.
    """

    workspace: Workspace | None = None
    on_event: EventHook | None = None
    objective: str = ""


@dataclass(frozen=True)
class Toolset:
    """The tools an agent may call: definitions the model sees, impls we run."""

    defs: list[dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_TOOL_DEFS))
    impls: Mapping[str, ToolImpl] = field(default_factory=default_tool_impls)


class ReasoningAgent:
    """The shared loop. One instance per projection agent."""

    def __init__(self, spec: AgentSpec, run: RunContext | None = None,
                 tools: Toolset | None = None):
        self.spec = spec
        self.run = run or RunContext()
        self.tools = tools or Toolset()

    # Attributes the harness reads off an agent (tool impls, scheduler, tests).
    @property
    def agent_id(self) -> str:
        return self.spec.agent_id

    @property
    def role(self) -> str | None:
        return self.spec.role

    @property
    def workspace(self) -> Workspace | None:
        return self.run.workspace

    @property
    def read_denylist(self) -> tuple[str, ...]:
        return self.spec.read_denylist

    # -- context rebuild (stateless) -----------------------------------------
    def _initial_messages(self, bb: Blackboard, events: list[Event]) -> list[dict[str, Any]]:
        parts: list[str] = []
        if self.run.objective:
            # Re-stated every activation so a re-woken stateless agent never forgets
            # what the team is building (a status ping must not reset the goal).
            parts.append("PROJECT OBJECTIVE (what this whole team is building — this "
                         "holds for every one of your activations, do NOT invent a new "
                         f"task):\n{self.run.objective}")
        for e in events:
            tag = e.kind if e.kind != "message" else f"from {e.from_agent}"
            parts.append(f"[{tag}] {e.payload}")
        ws = self.run.workspace
        if ws is not None:
            files = ws.list_files()
            parts.append("[workspace files]\n" + ("\n".join(files) if files
                                                  else "(empty — you may be the first agent)"))
            parts.append("Use read_file(path) to read any of these, list_files() to refresh, "
                         "write_artifact(section=<path>, content=...) to create/replace a file, "
                         "and run_bash(command) to run it.")
        for section in self.spec.reads:
            ev = bb.read_section(section)
            if ev is not None:
                parts.append(f"[blackboard:{section}]\n{ev.payload}")
        return [{"role": "user", "content": "\n\n".join(parts) or "(no input)"}]

    @staticmethod
    def _assistant_content(resp: ModelResponse) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if resp.text:
            content.append({"type": "text", "text": resp.text})
        for tc in resp.tool_calls:
            content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
        return content  # may be empty (no text, no tools) — caller skips appending it

    def _run_tool(self, tc: ToolCall, bb: Blackboard) -> str:
        impl = self.tools.impls.get(tc.name)
        if impl is None:
            return f"error: unknown tool '{tc.name}'"
        try:
            return impl(self, bb, tc.input)
        except Exception as exc:  # noqa: BLE001 - surface failures to the model, don't crash
            return f"error running {tc.name}: {exc}"

    # -- the activation loop -------------------------------------------------
    # roles whose turn is worthless unless they leave a file behind
    _PRODUCER_ROLES = ("topology", "lld", "test-designer", "programmer", "tester")

    _NUDGE = ("You are about to end your turn without creating any file in the workspace. "
              "Your role's deliverable IS a file. The workspace being empty is normal — YOU "
              "create the initial files. Stop exploring and write your deliverable NOW with "
              "write_artifact(section=<path>, content=<full file contents>). Do not just "
              "describe it.")

    _MAX_TRUNCATIONS = 2
    _TRUNCATED = (
        "Your previous response was CUT OFF at the output token limit — so any file you "
        "were writing was never saved. Do not repeat that attempt verbatim. Write ONE "
        "file at a time with write_artifact, keep each file focused (split large modules "
        "into smaller ones), and think briefly. Start with the single most important file "
        "now.")

    def runner(self, bb: Blackboard, agent_id: str, events: list[Event]) -> str:
        ws = self.run.workspace
        before = ws.snapshot() if ws is not None else None
        messages = self._resume_or_start(bb, agent_id, events)
        last_text = self._tool_loop(bb, messages, self.spec.max_iters)
        # Guarantee delivery: if a producing role wrote nothing — whether it stopped
        # early OR ran out of iterations exploring — force one dedicated delivery pass.
        if self._needs_nudge(bb, before):
            messages.append({"role": "user", "content": self._NUDGE})
            _log.warning("%s: NUDGE — no file produced, forcing a delivery pass", self.agent_id)
            self._emit("say", "(nudged: no file produced — forcing deliverable)")
            last_text = self._tool_loop(bb, messages, max(4, self.spec.max_iters // 2)) or last_text
        self._persist_memory(bb, messages)
        self._log_file_changes(bb, before)
        self._report_completion(bb, before, last_text)
        return STATE_DONE

    def _resume_or_start(self, bb: Blackboard, agent_id: str,
                         events: list[Event]) -> list[dict[str, Any]]:
        """Stateful: resume this agent's own transcript so it remembers what it did
        and why across activations; a first activation just starts one."""
        prior = bb.load_memory(agent_id)
        if not prior:
            return self._initial_messages(bb, events)
        try:
            messages: list[dict[str, Any]] = json.loads(prior)
            messages.append(self._initial_messages(bb, events)[0])  # latest inbox+state
        except (ValueError, IndexError):
            return self._initial_messages(bb, events)
        return messages

    def _persist_memory(self, bb: Blackboard, messages: list[dict[str, Any]]) -> None:
        try:
            bb.save_memory(self.agent_id, json.dumps(messages))
        except Exception:  # noqa: BLE001 - memory is best-effort; never fail the run over it
            _log.exception("%s: failed to persist memory", self.agent_id)

    # -- one model turn ------------------------------------------------------
    def _generate(self, messages: list[dict[str, Any]], turn: int) -> ModelResponse:
        try:
            resp = self.spec.model.generate(
                system=self.spec.system_prompt, messages=messages, tools=self.tools.defs
            )
        except Exception:
            _log.exception("%s: model.generate failed on turn %d", self.agent_id, turn)
            raise
        _log.info("%s turn %d: stop=%s tools=%d text=%dc think=%dc",
                  self.agent_id, turn, resp.stop_reason, len(resp.tool_calls),
                  len(resp.text), len(resp.thinking))
        return resp

    def _observe(self, bb: Blackboard, resp: ModelResponse) -> None:
        """Surface a turn to the live panes and the blackboard."""
        if resp.thinking.strip():
            self._emit("think", resp.thinking.strip())
            self._log_thinking(bb, resp.thinking.strip())
        if resp.text.strip():
            self._emit("say", resp.text.strip())
        for tc in resp.tool_calls:
            self._emit("tool", f"{tc.name}({self._tool_summary(tc.input)})")

    def _run_tools(self, bb: Blackboard, calls: list[ToolCall]) -> list[dict[str, Any]]:
        results = []
        for tc in calls:
            out = self._run_tool(tc, bb)
            first = out.splitlines()[0][:140] if out else ""
            _log.info("%s tool %s(%s) -> %s",
                      self.agent_id, tc.name, self._tool_summary(tc.input), first)
            results.append({"type": "tool_result", "tool_use_id": tc.id, "content": out})
        return results

    def _tool_loop(self, bb: Blackboard, messages: list[dict[str, Any]], iters: int) -> str:
        """Run model→tool turns until the model stops calling tools or `iters` runs out.
        Returns the model's last non-empty text (the agent's own summary)."""
        last_text = ""
        truncated = 0
        for i in range(iters):
            resp = self._generate(messages, i + 1)
            self._observe(bb, resp)
            last_text = resp.text.strip() or last_text
            assistant = self._assistant_content(resp)
            if assistant:  # never append an empty assistant turn (the API rejects it)
                messages.append({"role": "assistant", "content": assistant})
            # Run the tools whenever there are any — even if stop_reason isn't "tool_use"
            # (e.g. max_tokens mid-tool-use) — so every tool_use gets a tool_result and
            # we never persist a dangling tool_use (which the API rejects with a 400).
            if resp.tool_calls:
                messages.append({"role": "user", "content": self._run_tools(bb, resp.tool_calls)})
            if resp.stop_reason == "max_tokens":
                # Cut off at the output limit — often mid-tool-input, so the file was
                # never written. Tell the model and let it retry rather than accepting
                # a truncated turn as "finished".
                truncated += 1
                _log.warning("%s: response truncated at max_tokens (%d/%d)",
                             self.agent_id, truncated, self._MAX_TRUNCATIONS)
                self._emit("say", "(output limit hit — retrying with a smaller write)")
                if truncated > self._MAX_TRUNCATIONS:
                    return last_text
                messages.append({"role": "user", "content": self._TRUNCATED})
                continue
            if resp.stop_reason != "tool_use" or not resp.tool_calls:
                return last_text
        return last_text

    # -- reporting -----------------------------------------------------------
    def _report_completion(self, bb: Blackboard, before: dict[str, float] | None,
                           summary: str) -> None:
        """Post a visible completion report to the overseer so the user (and the TPM
        spokesbot) can see what each agent actually did — not just that it's DONE."""
        ws = self.run.workspace
        files = ws.changed_since(before) if ws is not None else []
        parts = [summary.strip() or "(finished; no summary given)"]
        if files:
            parts.append("Files: " + ", ".join(sorted(files)[:20]))
        try:
            bb.send(self.agent_id, "\n".join(parts), to="tpm", kind=KIND_MESSAGE)
        except Exception:  # noqa: BLE001 - a report must never fail an activation
            _log.exception("%s: failed to post completion report", self.agent_id)

    def _needs_nudge(self, bb: Blackboard, before: dict[str, float] | None) -> bool:
        """True when a producing role has written nothing this turn AND has never
        delivered before. On a re-activation (e.g. answering an escalation), the
        agent already has a deliverable, so we don't force a pointless rewrite."""
        ws = self.run.workspace
        if ws is None or self.spec.role not in self._PRODUCER_ROLES:
            return False
        if ws.changed_since(before):
            return False
        delivered_before = any(e.kind == KIND_ARTIFACT
                               for e in bb.history(from_agent=self.agent_id))
        return not delivered_before

    def _log_thinking(self, bb: Blackboard, thinking: str) -> None:
        """Persist reasoning to the blackboard as a control event so a second
        Pixibot instance (or `report`) can inspect what an agent was thinking."""
        try:
            bb.send(self.agent_id, f"[thinking]\n{thinking}", kind=KIND_CONTROL)
        except Exception:  # noqa: BLE001 - transparency must never fail a run
            _log.exception("%s: failed to log thinking", self.agent_id)

    def _emit(self, kind: str, detail: str) -> None:
        if self.run.on_event:
            try:
                self.run.on_event(self.agent_id, kind, detail)
            except Exception:  # noqa: BLE001 - a UI hook must never fail a run
                _log.exception("%s: on_event hook raised", self.agent_id)

    @staticmethod
    def _tool_summary(inp: dict[str, Any]) -> str:
        for key in ("section", "path", "command", "to"):
            if key in inp:
                return str(inp[key])[:80]
        return ""

    def _log_file_changes(self, bb: Blackboard, before: dict[str, float] | None) -> None:
        """Record every file this agent created/changed as an artifact — however it
        wrote them (write_artifact OR run_bash) — so the blackboard/Observer see them."""
        ws = self.run.workspace
        if ws is None:
            return
        for path in ws.changed_since(before):
            try:
                content = ws.read_file(path)
                if content is None:  # binary / unreadable as text — not an artifact
                    continue
                bb.send(self.agent_id, content, kind=KIND_ARTIFACT,
                        section=path, enforce_scope=False)
            except Exception:  # noqa: BLE001 - never let logging break the run
                _log.exception("%s: failed to log file change %s", self.agent_id, path)
