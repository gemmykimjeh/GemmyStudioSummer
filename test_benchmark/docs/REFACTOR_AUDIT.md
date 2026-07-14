# Refactor audit — boundary tightening (structure only, behavior unchanged)

Scope: encapsulate agent/bench-specific logic inside adapters; remove leaks,
duplication, and dead code from common code (`runner.py`, `registry.py`,
`agent.py`, `benchmark.py`, `run.py`, `schema.py`, `compare.py`). No new
features. **This is the audit — nothing has been changed yet.**

## 1. Leak audit (findings)

### 1a. Name-based branches in common code — NONE ✅
No `if agent == "hermes"` / `if benchmark == "tau_bench"` (or `"claude"` /
`"anthropic"` / `"tau"` / `"automation"`) branches exist in `runner.py`,
`registry.py`, `benchmark.py`, `agent.py`, `schema.py`. The only name strings in
common code are **docstring examples** (e.g. `--agent claude_sdk` in help text)
and **registry keys** — both allowed. `runner.py` has zero tau/automation traces.

### 1b. Adapter logic outside methods — NONE significant ✅
Module-level code in adapter files (pricing table, task builders, mock DB
factories) is adapter-local and belongs there. Nothing adapter-specific sits in
common-module top level or in `runner.run()`.

### 1c. Generic mechanisms that look like leaks but aren't
- **`mock` / `[MOCK]` tagging** (`Benchmark.mock`, `Result.mock`, runner/CLI
  tag). This is a *generic* benchmark property (any shape-faithful mock), not a
  per-bench branch — already correctly abstracted. Keep. (Tidy: `runner`/`run`
  use `getattr(benchmark, "mock", False)`; since the base class defines
  `mock = False`, this can be a plain `benchmark.mock` — cosmetic.)
- **`Env.conversational` / `respond()` / `episode_done()`** — a generic dialogue
  protocol on the base, implemented by conversational envs (tau real) and
  consumed generically by `claude_sdk` and the MCP bridge. Correctly abstracted.

### 1d. CLI benchmark-specific option *names* (minor, soft leak)
`run.py` defines `--real/--no-real`, `--split`, `--user-model`,
`--user-provider`, `--user-strategy` — vocabulary only tau/automation use. These
are **not branches**: they're collected into `benchmark_kwargs` and routed via
`registry._filter_kwargs`, which drops kwargs an adapter's `__init__` doesn't
accept. So a new benchmark is unaffected.
**Recommendation: keep as-is.** Removing/generalizing them (e.g. a generic
`--bench-opt k=v`) would change the documented command surface and risks
breaking the external contract the task says to preserve. Documented here as a
known, generically-routed convenience.

### 1e. Duplication (promote to shared helpers)
| # | Duplicated logic | Where | Action |
|---|---|---|---|
| D1 | Round-robin domain interleave (`zip_longest`, drop `None`) | `tau_bench._real_load_tasks`, `automation_bench._real_load_tasks` | Extract `interleave(lists)` helper (2 uses) |
| D2 | External-repo path resolve + existence guard + `sys.path` insert + clone-hint `RuntimeError` | `_tau_upstream.load_upstream`, `_automation_upstream.load_upstream` | Extract `external_repo_on_path(default, env_var, marker, clone_hint)` (2 uses); each loader keeps its own imports |

### 1f. Dead code / unused
| # | Item | Where | Action |
|---|---|---|---|
| X1 | `self._server = uvicorn_server = None` — `self._server` never read, local never used | `agents/_mcp_bridge.py:48` | Delete the line |
| X2 | `import json` unused | `agents/claude_sdk.py:19` | Delete the import |

### 1g. Already handled in a prior step
`messages_to_trajectory` (ReAct transcript → `Trajectory`) was already extracted
to `agents/_react.py` and is shared. No action.

## 2. Intentionally NOT changed (YAGNI / avoid over-abstraction)
- **No `RealMockBenchmark` mixin.** The `self.real`/`self.mock = not real` idiom
  and the `load_tasks/setup/score` real-vs-mock dispatch repeat across the two
  benchmarks, but their `__init__` signatures diverge (tau: `user_model`/`split`/
  `domains`; automation: `domains`/`include_simple`/`max_tool_calls`) and their
  mock envs/scorers differ. A shared base would add indirection for a 2-line
  idiom — not worth it at 2 benchmarks.
- **No shared mock-scorer.** tau uses nested-dict subset matching; automation
  uses list-contains predicates — deliberately different ("no universal grader").
- **Agent `run()` epilogue** (`traj.final_state = env.snapshot()`,
  `traj.wall_time = ...`) repeats in 3 agents but is 2 trivial lines each;
  extracting a helper saves little and adds an import. *Optional* — listed for
  your call, default leave as-is.

## 3. Proposed changes (pending approval)
1. Delete dead code X1, X2.
2. Add `harness/benchmarks/_util.py::interleave(lists)`; use it in both
   `_real_load_tasks` (D1).
3. Add `external_repo_on_path(...)` (shared by the two `_*_upstream.py`) (D2).
4. Cosmetic: `benchmark.mock` instead of `getattr(...)` in `runner`/`run`.
5. Docs: add crisp "add a new agent" / "add a new benchmark" checklists to the
   README (which methods to implement + where to register).

## 4. Verification (on completion)
- Full test suite green **before and after** (the suite has grown to **52**
  tests across the real-integration steps — up from the 46 noted in the brief;
  all 52 must stay green as the behavior-invariance proof).
- `grep` shows no agent/bench name strings in common code beyond docstring
  examples and registry keys.
- README contains both add-agent / add-bench checklists.

## 5. Changes made (what → where → why)

Approved and applied (structure only; behavior unchanged, **52/52 tests pass**
before and after):

| Item | What | Where → to | Why |
|---|---|---|---|
| D1 | Round-robin domain interleave | duplicated blocks in `tau_bench._real_load_tasks` + `automation_bench._real_load_tasks` → new `harness/benchmarks/_util.py::interleave()` | remove copy-paste; single tested implementation (2 call sites) |
| D2 | External-repo path resolve + existence guard + `sys.path` insert + clone-hint | duplicated in `_tau_upstream.load_upstream` + `_automation_upstream.load_upstream` → `harness/benchmarks/_util.py::external_repo_on_path()` | one place for the repo-locating contract; each loader keeps its own imports |
| X1 | Dead line `self._server = uvicorn_server = None` (never read) | removed from `agents/_mcp_bridge.py.__init__` | dead code |
| X2 | Unused `import json` | removed from `agents/claude_sdk.py` | dead import |
| Mock | `getattr(benchmark, "mock", False)` → `benchmark.mock` | `runner.py` (2 sites) + `run.py` (1 site) | base class defines `mock=False`; the defensive `getattr` was noise |
| Docs | Add-agent / add-bench 6-line checklists | `README.md` | "one file + one decorator" is now explicit |

**Kept as-is** (per audit §2, YAGNI): CLI benchmark-specific flags (routed
generically via `registry._filter_kwargs`, not branches); no `RealMockBenchmark`
mixin (init signatures + scorers diverge); no shared mock-scorer; agent `run()`
epilogue left inline (2 trivial lines × 3, not worth a helper).

**Net effect:** common code (`runner`/`registry`/`agent`/`benchmark`/`schema`)
contains no agent/bench name-based logic branches — only docstring examples and
registry keys. A new agent or benchmark is now genuinely "add one adapter file +
register".
