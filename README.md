# Pixibot 🤖

A dynamic multi-agent software-development system. A **TPM** agent plans a task and emits a
**projection** (JSON) that instantiates a team of reasoning agents (topology · LLD ·
programmer · tester) which coordinate over a **SQLite blackboard** by addressed messages,
write real files in a sandboxed workspace, and are judged by a **mechanical gate** before a
**human checkpoint** decides whether the run is done.

See **[DESIGN.md](DESIGN.md)** for the architecture and the decision log, and
**[docs/contracts/](docs/contracts/)** for one contract per module — responsibility,
interface, seam, invariants, error modes, logging and tests.

## Install

Python 3.12+. The core needs only the Anthropic SDK; everything else is an extra.

```bash
pip install -e .                 # core
pip install -e ".[compat,tui]"   # + OpenAI-compatible providers, + split-pane TUI
pip install -e ".[dev]"          # + ruff, mypy, pre-commit, import-linter, pytest
pre-commit install               # the gates run before every commit
```

Offline mode needs no key at all:

```bash
python3 -m pixibot --provider offline      # canned multi-agent mock, full wiring
python3 -m pixibot.demo                    # six-stage tour of the architecture
```

## Commands

| Command | What it does |
|---|---|
| `python3 -m pixibot` | Start the shell (split-pane TUI on a TTY, plain shell otherwise) |
| `python3 -m pixibot --provider anthropic` | Force a provider (anthropic\|gemini\|openrouter\|offline) |
| `python3 -m pixibot --objective "..."` | Build the objective, then drop into the shell |
| `python3 -m pixibot --review` | Pause at each checkpoint for a demo and your verdict |
| `python3 -m pixibot --sandbox docker` | Require a container for agent commands (see below) |
| `python3 -m pixibot --plain` / `--tui` | Force the plain shell / the split-pane view |
| `python3 -m pixibot.demo` | Offline tour: build → chat → steer → revise → report |
| `python3 -m pixibot.benchmark --provider offline` | Compare the topology arms |
| `python3 -m pytest -q` | Run the test suite (223 tests) |

**Inside the shell** — bare words, no slash; ↑/↓ recall history, Tab completes commands,
workspace paths and agent ids:

| Command | What it does |
|---|---|
| `ls [dir]` · `cd <dir>` · `pwd` · `cat <file>` · `tree` | navigate the project files |
| `build <objective>` | plan + run a build |
| `build-from <file.md>` | build from a filled intake form (`form` shows it) |
| `revise <feedback>` | re-plan from feedback |
| `tell <agent> <msg>` | non-blocking steering directive |
| `ask <agent> <question>` | ask an agent's spokesperson (never pauses it) |
| `gate` | run the mechanical gate over the workspace now |
| `updates` · `think <agent>` | raw agent reports · an agent's latest reasoning |
| `status` · `agents` · `report` | project status · agent list · full run report |
| `provider [name]` · `hard [on\|off]` | switch model provider · hard-dev routing |
| `help` · `clear` · `exit` | help · clear screen · leave |
| *anything else* | a question to the project overseer |

## How a run ends

1. The team works until it **quiesces** (a full scheduler pass activates nobody).
2. The **mechanical gate** derives its own checks from the files the agents wrote —
   `pytest` for Python, configure/build/`ctest` for CMake, `make` (and `make test` when
   declared) or a compile pass for C/C++ — and runs them in the same sandbox the agents used.
3. A red gate never reaches you: the failures go back to the team as revision feedback,
   bounded by `max_gate_revises`.
4. With `--review`, a green gate shows you the checkpoint's **demo** and asks. Enter
   accepts; anything else is feedback and the team re-plans with it (bounded by
   `max_review_rounds`).

## Providers

Pick with `--provider <name>` at launch or `provider <name>` mid-session; with no flag
Pixibot auto-detects from your environment, cheapest key first.

| Provider | Set up | Cost |
|---|---|---|
| **Gemini** | Free key at aistudio.google.com → `export GEMINI_API_KEY=AIza...` | Free tier |
| **OpenRouter** | Key at openrouter.ai → `export OPENROUTER_API_KEY=sk-or-...` | Pay-per-token (many models) |
| **Anthropic** | `ANTHROPIC_API_KEY` | Pay-per-token (Claude) |
| **offline** | nothing — canned multi-agent mock | Free |

Everything provider-specific is one `_Profile` entry in `pixibot/providers.py`: base URL,
key variable, depth → model table, hard-development model, spokesbot model. Adding a
provider is that entry and nothing else. `hard on` routes principal agents to the active
provider's strongest model at `xhigh` effort.

## Output: real files, in a sandbox

Agents write actual files to a per-run workspace at `~/pixibot-workspace/<run>/` and run
commands there — the tester really runs `pytest`. Where those commands run is chosen by
`--sandbox`:

| Mode | Behaviour |
|---|---|
| `auto` (default) | a container per command when a Docker daemon answers, else a confined host subprocess (with a warning) |
| `docker` | container or nothing — fails loudly if no daemon |
| `local` | confined host subprocess: cwd, `HOME` and `TMPDIR` pinned to the workspace, every secret-bearing variable withheld |

Build the image once: `docker build -t pixibot-run:latest docker/`. Either way, paths are
confined to the workspace (`..` and symlinks are resolved before the check) and commands
that try to leave the tree are refused before they run.

## Benchmarking the arms

The greenfield-topology adoptions are on/off arms, so they can be measured rather than
believed:

```bash
python3 -m pixibot.benchmark --provider offline --arms bare,default,auto-gate,both
python3 -m pixibot.benchmark --provider anthropic --only lru-cache --out bench.json
```

A pass is a **green mechanical gate** and nothing else — "no tests to run" is not a pass.
`--tasks` takes your own JSON list of `{id, objective, language?, hard?}`.

## Layout

- `pixibot/` — the **public surface** (one contract each in `docs/contracts/`)
  - `engine.py` — one steerable run: build, resume, tell, revise, gate, report
  - `run.py` — the one-shot pipeline; `providers.py` — which models a run uses
  - `blackboard.py` — SQLite append-only event log, the shared substrate (§13)
  - `workspace.py` — the path-confined project directory; `runtime.py` — where commands run
  - `gates.py` / `checkpoints.py` — the mechanical verdict and the human one (§10)
  - `model.py` — `Model`, `MockModel`, `AnthropicModel`, `OpenAICompatModel`
  - `schema.py`, `intake.py` — strict-input validation and the markdown request form
  - `observer.py`, `interaction.py` — run report; talk to agents without pausing them
  - `cli.py`, `tui.py`, `benchmark.py`, `demo.py` — the entry points
  - `lib/` — **internals** (private): the agent loop, scheduler, orchestrator, planner,
    prompt building, tool bodies, terminal styling, run log, completion, offline models
- `standards/` — the judgment half of the coding standards, injected into agent prompts
- `docs/contracts/` — one contract per module; `docs/incidents.md` — what went wrong and why
- `docker/` — the per-run sandbox image
- `scripts/check_contracts.py` — fails the commit if a public module has no contract
- `tests/` — behaviour through the interfaces (`tests/support.py` holds the shared fixture)

## Development

The gates are not advisory — they run on every commit and must pass:

```bash
ruff check pixibot tests          # lint: complexity 10, max-args 6, annotations required
python3 -m mypy pixibot           # --strict, no exemptions
lint-imports                      # the dependency directions each contract states
python3 scripts/check_contracts.py
python3 -m pytest -q
```

Contract before implementation: no new module, class or public function without a contract
in `docs/contracts/`. The mechanical rules live in `pyproject.toml` and `.importlinter`;
`standards/*.md` keeps only what a tool cannot check.

## Status

**M1–M42 complete.** 223 tests green; ruff, `mypy --strict`, import contracts and contract
coverage all clean. Real multi-agent Claude runs produce structured, tested projects in a
sandboxed per-run workspace, driven from a shell or a split-pane TUI, with multi-provider
models, prompt caching, stateful self-correcting agents, a language-aware mechanical gate,
a human checkpoint gate, and a benchmark harness over the topology arms. See `DESIGN.md`
§16 for what is next.
