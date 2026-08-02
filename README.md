# Pixibot 🤖

A dynamic multi-agent software-development system. A **TPM** agent plans a task
and emits a **projection** (JSON) that instantiates a team of reasoning agents
(topology · LLD · programmer · tester) that coordinate over a **SQLite
blackboard** by addressed messages, watched by an **Observer** that
de-obfuscates decisions.

See **[DESIGN.md](DESIGN.md)** for the full architecture and decision log.

## Commands

| Command | What it does |
|---|---|
| `python3 -m pixibot` | Start the shell (talks to the project overseer) |
| `python3 -m pixibot --provider anthropic` | Force a provider (anthropic\|gemini\|openrouter\|offline) |
| `python3 -m pixibot --objective "..."` | Build the objective, then drop into the shell |
| `python3 -m pixibot.run` | Offline demo — print an Observer report |
| `python3 -m unittest discover -s tests -t .` | Run the test suite |

**Inside the shell** — bare words, no slash; **↑/↓ recall history**, left/right edit:

| Command | What it does |
|---|---|
| `ls [dir]` · `cd <dir>` · `pwd` · `cat <file>` · `tree` | navigate the project files |
| `build <objective>` | plan + run a build |
| `build-from <file.md>` | build from a filled intake form (`form` shows it) |
| `revise <feedback>` | re-plan from feedback |
| `tell <agent> <msg>` | non-blocking steering directive |
| `ask <agent> <question>` | ask an agent's spokesperson (never pauses it) |
| `status` · `agents` · `report` | project status · agent list · full run report |
| `provider [name]` · `hard [on\|off]` | switch model provider · hard-dev routing |
| `help` · `clear` · `exit` | help · clear screen · leave |
| *anything else* | a question to the project overseer |

## Status

Milestones **M1–M17 complete**, 87 unit tests green — see `DESIGN.md` §16. It
builds real, tested code on disk with a multi-agent team (verified live on
Claude), driven from a friendly shell with a fresh workspace per build and live
per-agent progress. Runs offline (mock) or live against Anthropic / Gemini /
OpenRouter.

### Use the shell

```bash
python3 -m pixibot            # opens the shell; talks to the overseer by default
```

Navigate with `ls`/`cd`/`cat`; `build <objective>` to plan + run; `ask <agent>
<q>` to interrogate an agent (read-only spokesbot, never pauses it); `tell
<agent> <msg>` to steer; `revise <feedback>` to iterate. ↑/↓ recall history.
Offline it runs a canned multi-agent mock; with a provider key it runs live and
streams.

## Providers

Pixibot runs against any of these — pick with `--provider <name>` at launch, or
`/provider <name>` mid-session. With no flag it auto-detects from your env keys.

| Provider | Set up | Cost |
|---|---|---|
| **Gemini** | Free key at aistudio.google.com → `export GEMINI_API_KEY=AIza...` | Free tier |
| **OpenRouter** | Key at openrouter.ai → `export OPENROUTER_API_KEY=sk-or-...` | Pay-per-token (many models) |
| **Anthropic** | `ANTHROPIC_API_KEY`, or `ant auth login` + API credits | Pay-per-token (Claude) |
| **offline** | nothing — canned multi-agent mock | Free |

Model-per-depth mappings live in `pixibot/config.py` (edit the `*_DEPTH_MODELS`
dicts to taste). Hard-development (`/hard on`) routes principal agents to the
strongest model of the active provider.

## Output: real files

Agents write actual files to a per-run workspace at `~/pixibot-workspace/<run>/`
(the CLI uses run `cli`). They read each other's files and run commands there —
e.g. the tester runs `pytest`. Coordination is a deterministic dependency chain
(architect → programmer → tester → …) so every agent participates.


## Layout

- `pixibot/` — the harness package
  - `blackboard.py` — SQLite append-only event log (the shared substrate, §13)
  - `context_manager.py` — message-driven activation scheduler (§9)
  - `schema.py` — input/projection validation + bounded repair loop (§6/§7)
  - `model.py` — `Model` abstraction: `MockModel` (offline) / `AnthropicModel`
  - `agent.py`, `factory.py`, `tools.py` — the stateless reasoning agent (§9)
  - `tpm.py`, `orchestrator.py`, `run.py` — plan → materialize → run pipeline
  - `runtime.py` — Docker-per-run executor + local fallback (§11)
  - `observer.py` — message DAG + run report (§11)
  - `gates.py`, `standards.py` — mechanical checkpoint gate + standards (§10)
  - `config.py` — depth → model / effort mapping (§8)
- `standards/` — versioned quality standards agents read on demand (§10)
- `docker/` — per-run workspace image
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
