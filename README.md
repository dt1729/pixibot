# Pixibot 🤖

A dynamic multi-agent software-development system. A **TPM** agent plans a task
and emits a **projection** (JSON) that instantiates a team of reasoning agents
(topology · LLD · programmer · tester) that coordinate over a **SQLite
blackboard** by addressed messages, watched by an **Observer** that
de-obfuscates decisions.

See **[DESIGN.md](DESIGN.md)** for the full architecture and decision log.

## Status

Early build — see the milestone table in `DESIGN.md` §16. Currently: blackboard.

## Layout

- `pixibot/` — the harness package
  - `blackboard.py` — SQLite append-only event log (the shared substrate, §13)
  - `config.py` — depth → model / effort mapping (§8)
- `standards/` — versioned quality standards agents read on demand (§10)
- `tests/` — unit tests (stdlib `unittest`)

## Development

Requires Python 3.12+. The blackboard and its tests use **only the standard
library**, so they run with no install:

```bash
python3 -m unittest discover -s tests -t .
```

Agent milestones (M5+) need the Anthropic SDK:

```bash
pip install -r requirements.txt        # inside a venv, or:
pip install --break-system-packages anthropic
```
