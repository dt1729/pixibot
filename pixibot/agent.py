"""ReasoningAgent — a stateless agent that runs a tool loop per activation.

Statelessness (DESIGN.md §9): each activation rebuilds context from the
blackboard — the inbox events that woke it plus the sections it reads — runs the
model→tool loop until the model stops calling tools, and writes results back to
the blackboard. Nothing is held in memory between activations.

Its ``runner`` method plugs directly into ``ContextManager``.
"""

from __future__ import annotations

import json
from typing import Optional

from .blackboard import KIND_ARTIFACT, KIND_CONTROL, KIND_MESSAGE, Blackboard, Event
from .logbook import get as _get_logger

_log = _get_logger("agent")
from .context_manager import STATE_DONE
from .model import Model, ModelResponse
from .tools import DEFAULT_TOOL_DEFS, default_tool_impls


class ReasoningAgent:
    def __init__(
        self,
        agent_id: str,
        model: Model,
        *,
        system_prompt: str = "",
        role: Optional[str] = None,
        depth: Optional[str] = None,
        scope: Optional[str] = None,
        reads: tuple[str, ...] = (),
        tool_defs: Optional[list[dict]] = None,
        tool_impls: Optional[dict] = None,
        max_iters: int = 14,
        workspace=None,
        on_event=None,
        objective: str = "",
    ):
        self.agent_id = agent_id
        self.model = model
        self.system_prompt = system_prompt
        self.objective = objective  # the run's goal — persists across ALL activations
        self.role = role
        self.depth = depth
        self.scope = scope
        self.reads = tuple(reads)
        self.workspace = workspace
        self.on_event = on_event  # on_event(agent_id, kind, detail) — live activity feed
        self.read_denylist: tuple = ()  # sections this role may not read (adoption D)
        self.tool_defs = tool_defs if tool_defs is not None else DEFAULT_TOOL_DEFS
        self.tool_impls = tool_impls if tool_impls is not None else default_tool_impls()
        self.max_iters = max_iters

    # -- context rebuild (stateless) -----------------------------------------
    def _initial_messages(self, bb: Blackboard, events: list[Event]) -> list[dict]:
        parts: list[str] = []
        if self.objective:
            # Re-stated every activation so a re-woken stateless agent never forgets
            # what the team is building (a status ping must not reset the goal).
            parts.append("PROJECT OBJECTIVE (what this whole team is building — this holds "
                         f"for every one of your activations, do NOT invent a new task):\n{self.objective}")
        for e in events:
            tag = e.kind if e.kind != "message" else f"from {e.from_agent}"
            parts.append(f"[{tag}] {e.payload}")
        if self.workspace is not None:
            files = self.workspace.list_files()
            parts.append("[workspace files]\n" + ("\n".join(files) if files
                                                   else "(empty — you may be the first agent)"))
            parts.append("Use read_file(path) to read any of these, list_files() to refresh, "
                         "write_artifact(section=<path>, content=...) to create/replace a file, "
                         "and run_bash(command) to run it.")
        for section in self.reads:
            ev = bb.read_section(section)
            if ev is not None:
                parts.append(f"[blackboard:{section}]\n{ev.payload}")
        return [{"role": "user", "content": "\n\n".join(parts) or "(no input)"}]

    @staticmethod
    def _assistant_content(resp: ModelResponse) -> list[dict]:
        content: list[dict] = []
        if resp.text:
            content.append({"type": "text", "text": resp.text})
        for tc in resp.tool_calls:
            content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
        return content or [{"type": "text", "text": ""}]

    def _run_tool(self, tc, bb: Blackboard) -> str:
        impl = self.tool_impls.get(tc.name)
        if impl is None:
            return f"error: unknown tool '{tc.name}'"
        try:
            return impl(self, bb, tc.input)
        except Exception as exc:  # surface failures to the model, don't crash the loop
            return f"error running {tc.name}: {exc}"

    # -- the activation loop -------------------------------------------------
    # roles whose turn is worthless unless they leave a file behind
    _PRODUCER_ROLES = ("topology", "lld", "test-designer", "programmer", "tester")

    _NUDGE = ("You are about to end your turn without creating any file in the workspace. "
              "Your role's deliverable IS a file. The workspace being empty is normal — YOU "
              "create the initial files. Stop exploring and write your deliverable NOW with "
              "write_artifact(section=<path>, content=<full file contents>). Do not just "
              "describe it.")

    def runner(self, bb: Blackboard, agent_id: str, events: list[Event]) -> str:
        before = self.workspace.snapshot() if self.workspace is not None else None
        # Stateful: resume this agent's own transcript so it remembers what it did
        # and why across activations; a fresh activation just starts a new one.
        prior = bb.load_memory(agent_id)
        if prior:
            try:
                messages = json.loads(prior)
                messages.append(self._initial_messages(bb, events)[0])  # latest inbox+state
            except (ValueError, IndexError):
                messages = self._initial_messages(bb, events)
        else:
            messages = self._initial_messages(bb, events)
        last_text = self._tool_loop(bb, messages, self.max_iters)
        # Guarantee delivery: if a producing role wrote nothing — whether it stopped
        # early OR ran out of iterations exploring — force one dedicated delivery pass.
        if self._needs_nudge(bb, before):
            messages.append({"role": "user", "content": self._NUDGE})
            self._emit("say", "(nudged: no file produced — forcing deliverable)")
            last_text = self._tool_loop(bb, messages, max(4, self.max_iters // 2)) or last_text
        self._persist_memory(bb, messages)
        self._log_file_changes(bb, before)
        self._report_completion(bb, before, last_text)
        return STATE_DONE

    def _persist_memory(self, bb: Blackboard, messages: list[dict]) -> None:
        try:
            bb.save_memory(self.agent_id, json.dumps(messages))
        except Exception:  # memory is best-effort; never fail the run over it
            _log.exception("%s: failed to persist memory", self.agent_id)

    def _tool_loop(self, bb: Blackboard, messages: list[dict], iters: int) -> str:
        """Run model→tool turns until the model stops calling tools or `iters` runs out.
        Returns the model's last non-empty text (the agent's own summary)."""
        last_text = ""
        for i in range(iters):
            try:
                resp = self.model.generate(
                    system=self.system_prompt, messages=messages, tools=self.tool_defs
                )
            except Exception:
                _log.exception("%s: model.generate failed on turn %d", self.agent_id, i + 1)
                raise
            _log.info("%s turn %d: stop=%s tools=%d text=%dc think=%dc",
                      self.agent_id, i + 1, resp.stop_reason, len(resp.tool_calls),
                      len(resp.text), len(getattr(resp, "thinking", "")))
            if getattr(resp, "thinking", "").strip():
                self._emit("think", resp.thinking.strip())
                self._log_thinking(bb, resp.thinking.strip())
            if resp.text.strip():
                last_text = resp.text.strip()
                self._emit("say", last_text)
            for tc in resp.tool_calls:
                self._emit("tool", f"{tc.name}({self._tool_summary(tc.input)})")
            messages.append({"role": "assistant", "content": self._assistant_content(resp)})
            if resp.stop_reason != "tool_use" or not resp.tool_calls:
                return last_text
            results = []
            for tc in resp.tool_calls:
                out = self._run_tool(tc, bb)
                first = out.splitlines()[0][:140] if out else ""
                _log.info("%s tool %s(%s) -> %s",
                          self.agent_id, tc.name, self._tool_summary(tc.input), first)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": out,
                })
            messages.append({"role": "user", "content": results})
        return last_text

    def _report_completion(self, bb: Blackboard, before, summary: str) -> None:
        """Post a visible completion report to the overseer so the user (and the TPM
        spokesbot) can see what each agent actually did — not just that it's DONE."""
        files = self.workspace.changed_since(before) if self.workspace is not None else []
        parts = [summary.strip() or "(finished; no summary given)"]
        if files:
            parts.append("Files: " + ", ".join(sorted(files)[:20]))
        try:
            bb.send(self.agent_id, "\n".join(parts), to="tpm", kind=KIND_MESSAGE)
        except Exception:
            pass

    def _needs_nudge(self, bb: Blackboard, before) -> bool:
        """True when a producing role has written nothing this turn AND has never
        delivered before. On a re-activation (e.g. answering an escalation), the
        agent already has a deliverable, so we don't force a pointless rewrite."""
        if self.workspace is None or self.role not in self._PRODUCER_ROLES:
            return False
        if self.workspace.changed_since(before):
            return False
        delivered_before = any(e.kind == KIND_ARTIFACT
                               for e in bb.history(from_agent=self.agent_id))
        return not delivered_before

    def _log_thinking(self, bb: Blackboard, thinking: str) -> None:
        """Persist reasoning to the blackboard as a control event so a second
        Pixibot instance (or `report`) can inspect what an agent was thinking."""
        try:
            bb.send(self.agent_id, f"[thinking]\n{thinking}", kind=KIND_CONTROL)
        except Exception:
            pass

    def _emit(self, kind: str, detail: str) -> None:
        if self.on_event:
            try:
                self.on_event(self.agent_id, kind, detail)
            except Exception:
                pass

    @staticmethod
    def _tool_summary(inp: dict) -> str:
        for key in ("section", "path", "command", "to"):
            if key in inp:
                return str(inp[key])[:80]
        return ""

    def _log_file_changes(self, bb: Blackboard, before) -> None:
        """Record every file this agent created/changed as an artifact — however it
        wrote them (write_artifact OR run_bash) — so the blackboard/Observer see them."""
        if self.workspace is None:
            return
        for path in self.workspace.changed_since(before):
            content = self.workspace.read_file(path)
            try:
                bb.send(self.agent_id, content if content is not None else "",
                        kind=KIND_ARTIFACT, section=path, enforce_scope=False)
            except Exception:  # never let logging break the run
                pass
