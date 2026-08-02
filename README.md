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
| `python3 -m pixibot` | Start the chatbot (talking to the TPM) |
| `python3 -m pixibot --objective "..."` | Build the objective, then drop into chat |
| `python3 -m pixibot --db run.db` | Use a specific blackboard file |
| `python3 -m pixibot.run` | Offline demo — print an Observer report |
| `python3 -m unittest discover -s tests -t .` | Run the test suite |

**Inside the chat:**

| Command | What it does |
|---|---|
| `<text>` | Talk to the current agent (default: `tpm`) |
| `@<agent> <text>` | Talk to a specific agent's spokesbot (never pauses it) |
| `@<agent>` | Switch the current agent |
| `/at <agent>` | Switch the current agent |
| `/build <objective>` | Plan + run a build |
| `/revise <feedback>` | Re-plan from demo feedback (adds/changes agents) |
| `/tell <agent> <text>` | Send a non-blocking steering directive |
| `/hard [on\|off]` | Toggle hard-development routing (principal → Fable 5) |
| `/provider [name]` | Switch model provider (anthropic \| gemini \| openrouter \| offline) |
| `/form` | Show the build-request intake form |
| `/agents` | List agents on the blackboard |
| `/report` | Print the Observer run report |
| `/help` | Show help |
| `/quit` | Exit |

## Status

Milestones **M1–M10 complete**, 42 unit tests green — see `DESIGN.md` §16. The
full pipeline runs offline against a mock model; the real-Claude path is wired
but needs the SDK + an API key.

Run the offline demo:

```bash
python3 -m pixibot.run        # prints an Observer report for a mock run
```

### Chat with it (CLI)

```bash
python3 -m pixibot            # chatbot: TPM by default
```

Inside the chat: `/build <objective>` to plan+run, `@<agent> <question>` to talk
to a specific agent (via a cheap read-only *spokesbot* that never pauses the
working agent), `/tell <agent> <directive>` to steer (non-blocking), `/agents`,
`/report`, `/quit`. Offline it shows each agent's context snapshot; with
`ANTHROPIC_API_KEY` set, spokesbots become live Haiku conversations and `/build`
plans against real Claude.

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
