# Incidents

What went wrong, what it cost, and what now prevents it. One entry per defect that
survived into a run or into a commit — not a changelog, and not a list of bugs fixed
during normal work.

The point is the last line of each entry: a defect that produced only a fix is likely to
come back.

---

## 001 — A video-viewer request built a URL shortener (M17)

**What happened.** A new build inherited the previous run's files in the shared workspace.
The architect read the leftover mock files, concluded the project was a URL shortener, and
the whole team built one.

**Why it survived.** The workspace was per-CLI, not per-build, and nothing in the agent's
context said which files were *this* task's.

**Prevention.** Every build allocates a fresh `run_id` and its own workspace directory
(decision 45); prior runs stay on disk for recovery. The run objective is restated in every
activation, so a stale file cannot redefine the task.

---

## 002 — Three message-shape crashes from the stateful transcript (M28–M33)

**What happened.** Live agents died mid-run with 400s: an empty text block, a `tool_use`
with no matching `tool_result` (a turn cut off at `max_tokens`), and a binary file read as
UTF-8. Later, a `max_tokens` truncation mid-`write_artifact` ended a turn with the file
never written and the agent reporting DONE.

**Why it survived.** The seam between a persisted transcript and a live API was exercised
only by `MockModel`, which accepts anything. The tests asserted on behaviour the mock could
produce.

**Prevention.** `model.py` sanitises every outgoing request (drop empty blocks, drop
dangling `tool_use`); the agent loop runs tools whenever a response carries them, whatever
the stop reason, so every `tool_use` gets a result; a truncated turn is retried with an
explicit notice, bounded. Each has a regression test written against the *shape* rather
than the mock.

---

## 003 — An agent read the sibling workspace and the API key file (M19)

**What happened.** An agent's `run_bash` wandered: `cat ../other-run/...`, then
`~/ant_key.txt`.

**Why it survived.** `run_bash` was a plain subprocess with the parent environment and no
path screening.

**Prevention.** Commands are screened for escapes (parent traversal, absolute system paths,
key-file names) before they run; the child's cwd, `HOME` and `TMPDIR` are the workspace and
every secret-bearing variable is withheld (`runtime.confined_env`); a container is used when
one is available (decision 53). Path containment resolves `..` **and** symlinks before
comparing (M37) — the string-prefix check it replaced let a symlink inside the tree name a
file outside it.

---

## 004 — The TUI's build path forked from the shell's (M38)

**What happened.** The TUI carried its own copy of build-from parsing, input construction
and engine creation. A change to the engine factory's signature broke the TUI at runtime
while the plain shell kept working — and the suite stayed green, because the TUI's only
test exercised a string helper.

**Why it survived.** Two front ends, two copies of the sequence, and no test through the
one that was hard to drive.

**Prevention.** The TUI renders and nothing else: every command goes through the same
`ChatSession` the plain shell uses, with the session's hooks pointed at pane writers.
Completion moved to `pixibot/lib/completion.py`, where it is a pure function with tests.
The session's hook contract has a test asserting that a build receives them.

---

## 005 — Two of four benchmark arms measured the same thing (M40)

**What happened.** The first `STANDARD_ARMS` draft had `auto-gate` as
`DEFAULT_FEATURES | {auto_gate}` and `all` as `ALL_FEATURES` — identical sets. A quarter of
the ablation matrix was duplicated work.

**Why it survived.** Nothing; it was caught by a test written in the same commit
(`test_the_standard_arms_are_distinct_feature_sets`).

**Prevention.** That test. It is cheap and it is the kind of mistake that is invisible in
review: the names differ, so the table looks right.

---

## 006 — Two defects in the lld-seed plugin, found by using it here

**What happened.** (a) The contract gate derives the repo root from the session's cwd
rather than from the file being written, so a session whose cwd is not the repo root — a
Windows session editing a repo inside WSL, for instance — gets an unwaivable "no contract"
denial for a contract that exists. (b) `templates/FOLDER-CLAUDE.md.template` puts the
literal `manual` sentinel inside its header comment, and `merge_claude_md.py` matches the
first sentinel to the last closing one with DOTALL — so a generated folder file preserves
itself whole and can never be refreshed.

**Why it survived.** 137 plugin tests exercise the library functions with synthetic input,
never the real hook payload or the shipped template — the plugin's own incident 002.

**Prevention here.** The `CLAUDE.md` files in this repo keep the sentinel out of the header
and carry the `manual` block at the foot only; a merge of identical content reports
`unchanged` and a hand-edited manual block survives (verified). Both defects belong
upstream in `dt1729/claude_lld`.

---

## 007 — The simulation provider blocked an agent on its first live build (M47)

**What happened.** The first full build through `claude -p` ran 156 seconds and ended with
the programmer BLOCKED: `CliFailedError: the CLI reported an error: error_max_turns`. The
architect had already written `ARCHITECTURE.md`, so the run died with work in hand.

**Why it survived.** Two reasons, and the unit tests could not see either:

* `--max-turns 2` was copied from a one-shot judge, where the model answers in a single
  turn. An agent prompt is long enough that the CLI spends a turn answering and may need
  another to fit the output schema.
* `parse_envelope` checked `is_error` **before** it looked at the result, so a CLI that
  errored *after* producing a usable answer had that answer thrown away.

Every unit test used a runner double that returned a well-formed envelope, so neither
path was exercised. The defects were only reachable against the real CLI.

**Prevention.** The turn budget is 6 and is a constructor argument. `parse_envelope`
parses first and raises only when there is no answer to keep; an error that still carried
an answer is logged at WARNING and used. Both have tests. The second live build passed the
gate, negative control included.

**Also learned, not a defect.** A `claude -p` turn carries about 20,000 input tokens of
Claude Code's own system prompt even with `--system-prompt` replacing it. The default
per-agent ceiling of 40,000 therefore buys two turns, and all three agents in that build
ended BUDGET_EXHAUSTED rather than DONE. The work still landed, but per-agent budgets are
calibrated for API calls, not for the CLI's fixed overhead: raise them when simulating.

---

## 008 — BUDGET_EXHAUSTED silently stopped the dependency cascade (M47, fixed M48)

**What happened.** Seen in the same live build. `ContextManager.step` wakes an agent's
dependents only when its terminal state is `DONE`. M46 made `BUDGET_EXHAUSTED` reachable
for the first time, so a spent agent no longer wakes the agent waiting on it. The chain
survived that build only because the `default` arm has continuous testing on, which
re-activates readers on an artifact write — a different code path.

**Why it survived.** `BUDGET_EXHAUSTED` was a state DESIGN.md had named since M1 and
nothing ever set, so no test covered what the scheduler does with it. The same gap applies
to `BLOCKED`: a crashed agent has never woken its dependents either, which means one crash
stalls everything downstream of it.

**Prevention.** The scheduler's notion of "finished" now covers every terminal state:
`TERMINAL_STATES` is DONE, BLOCKED and BUDGET_EXHAUSTED, and all three wake dependents and
count toward `completed()`. A dependent that runs with what exists beats a run that stalls
behind a crashed or spent agent, and a checkpoint declared after one now falls due instead
of never. Three tests cover it, and all three fail when the set is narrowed back to DONE.
