"""End-to-end offline demo of the wired system (`python -m pixibot.demo`).

Shows: the intake form -> a multi-agent build (topology -> programmer -> tester
via dependency handshakes) -> a read-only spokesbot chat -> a non-blocking
directive that resumes the agent -> a revision that adds a new agent.
All offline (MockModel) — no API key needed.
"""

from __future__ import annotations

import os
import tempfile

from . import intake, mockrun
from .blackboard import Blackboard
from .engine import Engine
from .interaction import Broker


def _hr(title: str) -> None:
    print("\n" + "=" * 66 + f"\n{title}\n" + "=" * 66)


def main() -> None:
    _hr("1) The intake form the TPM hands you")
    print(intake.render_form())

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    bb = Blackboard(path, run_id="demo")
    objective = "a URL shortener service"
    engine = Engine(bb, mockrun.mock_tpm_model(objective), mockrun.mock_model_factory())

    _hr(f"2) Build: {objective}  (dependency-chain cascade, real files on disk)")
    res = engine.build_objective(objective)
    print("agents:", [a["id"] for a in res["projection"]["agents"]], " steps:", res["steps"])
    print("workspace:", res["workspace"])
    print("files written to disk:", engine.workspace.list_files())
    for section in ("topology", "impl/f1", "tests/f1"):
        ev = bb.read_section(section)
        print(f"  {section}: {ev.payload if ev else '(none)'}")

    _hr("3) Talk to the programmer (read-only spokesbot — never pauses it)")
    broker = Broker(bb)  # offline: shows the brokered context snapshot
    print(broker.talk("prog-f1", "what did you build?"))

    _hr("4) Steer: /tell prog-f1 'use a dataclass'  (non-blocking; agent resumes)")
    steps = engine.tell("prog-f1", "use a dataclass")
    print(f"resumed {steps} step(s)")
    print("  impl/f1  ->", bb.read_section("impl/f1").payload)
    print("  tests/f1 ->", bb.read_section("tests/f1").payload, "(tester re-ran automatically)")

    _hr("5) Revise from demo feedback: 'add API docs'  (TPM adds a new agent)")
    steps = engine.revise("add API docs")
    print(f"revised {steps} step(s); agents now:", [a["agent_id"] for a in bb.list_agents()])
    docs = bb.read_section("docs/f1")
    print("  docs/f1  ->", docs.payload if docs else "(none)")

    _hr("6) Final Observer report")
    print(engine.report())

    bb.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.unlink(path + s)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
