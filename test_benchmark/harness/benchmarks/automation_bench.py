"""automation_bench — cross-app business automation (zapier/AutomationBench).

(a) License:        MIT (zapier/AutomationBench).
(b) Infrastructure: REAL mode drives the upstream public task set (6 business
                    domains x 100 + a 200-task `simple` baseline) with a
                    fully-simulated SaaS world (47 tools) — no external SaaS
                    accounts, and (unlike tau-bench) no user-simulator LLM, so
                    scoring runs offline. Needs the repo at
                    external/AutomationBench + `pip install -e ".[automation_bench]"`.
                    MOCK fallback needs nothing.
(c) Scoring:        DETERMINISTIC final-state assertions (no LLM judge).
                    ``partial_credit`` = fraction of assertions satisfied;
                    ``task_completed_correctly`` (0/1) = 1 only if every
                    assertion passes -> Result.success.
(d) Official repo:  https://github.com/zapier/AutomationBench

Modes, selected by ``real`` (default True):

* ``real=True``  — upstream tasks, simulated world, REST-style `api` toolset
  (api_search / api_fetch / base64_encode), deterministic assertion scoring.
  NOT tagged ``[MOCK]``.
* ``real=False`` — a compact in-memory reproduction (SHAPE-FAITHFUL MOCK),
  tagged ``[MOCK]``, for offline smoke-tests with no repo.

The `simple` domain (200 baseline tasks) is excluded from the official score:
enable it with ``include_simple=True`` and it is tagged ``baseline_only`` in
metadata so ``report`` and the domain pass-rate omit it.

Public vs official: this is the PUBLIC task set. The official AutomationBench
leaderboard is scored on a private held-out set, so local numbers won't match
it 1:1 — surfaced in ``report`` and the README.
"""

from __future__ import annotations

import json

from harness.benchmark import Benchmark, Env
from harness.benchmarks._automation_upstream import (
    load_upstream,
    strip_none_values,
    upstream_available,
)
from harness.benchmarks._util import interleave
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

# Heuristic keywords for detecting a self-reported "I finished" claim (req 4).
_CLAIM_KWS = (
    "done", "completed", "complete", "success", "finished", "all set",
    "taken care of", "i've ", "i have ", "successfully", "handled", "sent the",
)


def _claims_success(text: str | None) -> bool:
    return bool(text) and any(kw in text.lower() for kw in _CLAIM_KWS)


# =====================================================================
# REAL upstream adapter
# =====================================================================
class AutomationRealEnv(Env):
    """Simulated SaaS world exposed to the agent via the REST-style api toolset."""

    conversational = False  # the agent acts then stops; no user dialogue

    def __init__(self, task: Task, world, up: dict, system_prompt: str,
                 max_tool_calls: int = 50) -> None:
        self.task = task
        self.world = world
        self._up = up
        self._system_prompt = system_prompt
        self.max_tool_calls = max_tool_calls
        self._calls = 0

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return self._system_prompt

    def tools(self) -> list[ToolSpec]:
        # The generic REST toolset. `world` is injected by us, not the model.
        return [
            ToolSpec(
                name="api_search",
                description="Search available API endpoints by keyword to "
                            "discover the URL/params to use with api_fetch.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "Space-separated keywords."},
                        "top_k": {"type": "integer",
                                  "description": "Max results (default 5)."},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="api_fetch",
                description="Call an API endpoint by full URL, routing to the "
                            "world-state mutation. Discover URLs via api_search.",
                parameters={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string",
                                   "description": "GET/POST/PUT/PATCH/DELETE."},
                        "url": {"type": "string", "description": "Full API URL."},
                        "params": {"type": "string",
                                   "description": "Query params as a JSON string."},
                        "body": {"type": "string",
                                 "description": "Request body as a JSON string."},
                    },
                    "required": ["method", "url"],
                },
            ),
            ToolSpec(
                name="base64_encode",
                description="Encode text to base64url (needed for Gmail body "
                            "fields). Local helper; calls no API.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
        ]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        self._calls += 1
        if self._calls > self.max_tool_calls:
            return ToolResult(
                output=f"[tool-call budget of {self.max_tool_calls} exceeded]",
                is_error=True,
            )
        args = arguments or {}
        try:
            if name == "api_search":
                out = self._up["api_search"](
                    query=args.get("query", ""), top_k=int(args.get("top_k", 5)))
            elif name == "api_fetch":
                out = self._up["api_fetch"](
                    self.world, args.get("method", "GET"), args.get("url", ""),
                    args.get("params"), args.get("body"))
            elif name == "base64_encode":
                out = self._up["base64_encode"](args.get("text", ""))
            else:
                return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        except UnicodeDecodeError as exc:  # pragma: no cover - windows cp949
            raise RuntimeError(
                "AutomationBench read a UTF-8 file under a non-UTF-8 locale. "
                "Run in Python UTF-8 mode (set PYTHONUTF8=1 or `python -X utf8`)."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surface tool errors to agent
            return ToolResult(output=f"Error: {type(exc).__name__}: {exc}",
                              is_error=True)
        text = str(out)
        return ToolResult(output=text, is_error=text.lstrip().startswith("Error"))

    def num_tool_calls(self) -> int:
        return self._calls


# =====================================================================
# MOCK fallback (SHAPE-FAITHFUL — not upstream)
# =====================================================================
def _mock_initial_state() -> dict:
    return {
        "crm": {"contacts": {
            "alice": {"name": "Alice", "email": "alice@example.com"},
            "bob": {"name": "Bob", "email": "bob@example.com"},
            "carol": {"name": "Carol", "email": "carol@acme.com"},
        }},
        "tracker": [], "calendar": [], "email": [],
    }


class AutomationMockEnv(Env):
    """Mock multi-app workspace (shape-faithful, not upstream)."""

    def __init__(self, task: Task) -> None:
        self.task = task
        self.state = _mock_initial_state()

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name="crm_find_contact",
                     description="Find a CRM contact by name.",
                     parameters={"type": "object",
                                 "properties": {"name": {"type": "string"}},
                                 "required": ["name"]}),
            ToolSpec(name="tracker_create_task",
                     description="Create a task in the tracker.",
                     parameters={"type": "object",
                                 "properties": {"title": {"type": "string"},
                                                "assignee": {"type": "string"},
                                                "due": {"type": "string"}},
                                 "required": ["title"]}),
            ToolSpec(name="calendar_create_event",
                     description="Create a calendar event (date YYYY-MM-DD).",
                     parameters={"type": "object",
                                 "properties": {"title": {"type": "string"},
                                                "date": {"type": "string"}},
                                 "required": ["title", "date"]}),
            ToolSpec(name="send_email",
                     description="Send an email.",
                     parameters={"type": "object",
                                 "properties": {"to": {"type": "string"},
                                                "subject": {"type": "string"},
                                                "body": {"type": "string"}},
                                 "required": ["to", "subject"]}),
        ]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        try:
            return handler(arguments)
        except KeyError as exc:
            return ToolResult(output=f"missing argument: {exc}", is_error=True)

    def _tool_crm_find_contact(self, args):
        c = self.state["crm"]["contacts"].get(str(args["name"]).strip().lower())
        return ToolResult(output=json.dumps(c)) if c else \
            ToolResult(output=f"no contact named {args['name']!r}", is_error=True)

    def _tool_tracker_create_task(self, args):
        self.state["tracker"].append({"title": args["title"],
                                      "assignee": args.get("assignee", ""),
                                      "due": args.get("due", "")})
        return ToolResult(output=f"created task {args['title']!r}")

    def _tool_calendar_create_event(self, args):
        self.state["calendar"].append({"title": args["title"], "date": args["date"]})
        return ToolResult(output=f"created event {args['title']!r}")

    def _tool_send_email(self, args):
        self.state["email"].append({"to": args["to"], "subject": args["subject"],
                                    "body": args.get("body", "")})
        return ToolResult(output=f"email sent to {args['to']}")

    def snapshot(self) -> dict:
        return {"tracker": list(self.state["tracker"]),
                "calendar": list(self.state["calendar"]),
                "email": list(self.state["email"])}


def _mock_tasks() -> list[Task]:
    dom = {"domain": "crm(mock)"}
    return [
        Task(id="auto-001", benchmark="automation_bench",
             prompt="Create a tracker task titled 'Follow up with Acme' "
                    "assigned to alice.",
             metadata={**dom,
                       "goal": [{"store": "tracker",
                                 "match": {"title": "Follow up with Acme",
                                           "assignee": "alice"}}],
                       "oracle_actions": [{"tool": "tracker_create_task",
                                           "arguments": {"title": "Follow up with Acme",
                                                         "assignee": "alice"}}]}),
        Task(id="auto-002", benchmark="automation_bench",
             prompt="Send an email to bob@example.com with subject 'Welcome aboard'.",
             metadata={**dom,
                       "goal": [{"store": "email",
                                 "match": {"to": "bob@example.com",
                                           "subject": "Welcome aboard"}}],
                       "oracle_actions": [{"tool": "send_email",
                                           "arguments": {"to": "bob@example.com",
                                                         "subject": "Welcome aboard",
                                                         "body": "Glad to have you!"}}]}),
        Task(id="auto-003", benchmark="automation_bench",
             prompt="Look up 'Carol', then schedule 'Call Carol' on 2026-07-10.",
             metadata={**dom,
                       "goal": [{"store": "calendar",
                                 "match": {"title": "Call Carol",
                                           "date": "2026-07-10"}}],
                       "oracle_actions": [
                           {"tool": "crm_find_contact", "arguments": {"name": "Carol"}},
                           {"tool": "calendar_create_event",
                            "arguments": {"title": "Call Carol", "date": "2026-07-10"}}]}),
    ]


def _mock_predicate_met(snapshot: dict, predicate: dict) -> bool:
    records = snapshot.get(predicate["store"], [])
    match = predicate["match"]
    return any(all(rec.get(k) == v for k, v in match.items()) for rec in records)


# =====================================================================
# Dispatcher
# =====================================================================
@register_benchmark("automation_bench")
class AutomationBench(Benchmark):
    """AutomationBench adapter. REAL upstream by default; MOCK via real=False."""

    def __init__(
        self,
        real: bool = True,
        domains: tuple[str, ...] | None = None,
        include_simple: bool = False,
        max_tool_calls: int = 50,
    ) -> None:
        self.real = real
        self.mock = not real  # drives the [MOCK] tag
        self.domains = tuple(domains) if domains else None  # None -> PUBLIC
        self.include_simple = include_simple
        self.max_tool_calls = max_tool_calls

    # -- task loading -----------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        tasks = self._real_load_tasks() if self.real else _mock_tasks()
        return tasks[:limit] if limit is not None else tasks

    def _real_load_tasks(self) -> list[Task]:
        up = load_upstream()
        domains = list(self.domains) if self.domains else list(up["PUBLIC_DOMAINS"])
        baseline_domains: list[str] = []
        if self.include_simple and "simple" in up["DOMAINS"]:
            baseline_domains = ["simple"]

        per_domain: list[list[Task]] = []
        for domain in domains + baseline_domains:
            ds = up["get_domain_dataset"](domain)
            baseline = domain in baseline_domains
            rows: list[Task] = []
            for i in range(len(ds)):
                row = ds[i]
                prompt = row["prompt"]
                if isinstance(prompt, str):
                    prompt = json.loads(prompt)
                user_msg = next(
                    (m["content"] for m in prompt if m.get("role") == "user"), "")
                rows.append(Task(
                    id=f"{domain}-{row.get('example_id', i)}",
                    benchmark="automation_bench",
                    prompt=user_msg,
                    metadata={"domain": domain, "index": i,
                              "task_name": row.get("task", ""),
                              "baseline_only": baseline},
                ))
            per_domain.append(rows)

        # Round-robin interleave so a small --limit spans domains.
        return interleave(per_domain)

    # -- setup ------------------------------------------------------------
    def setup(self, task: Task) -> Env:
        if not self.real:
            return AutomationMockEnv(task)
        up = load_upstream()
        ds = up["get_domain_dataset"](task.metadata["domain"])
        row = ds[task.metadata["index"]]
        info = row["info"]
        if isinstance(info, str):
            info = json.loads(info)
        prompt = row["prompt"]
        if isinstance(prompt, str):
            prompt = json.loads(prompt)
        system_prompt = next(
            (m["content"] for m in prompt if m.get("role") == "system"), "")

        initial_state = strip_none_values(info["initial_state"])
        assertions = info.get("assertions", [])
        zapier_tools = info.get("zapier_tools", [])
        # Stash what score() needs (avoids re-reading the dataset there).
        task.metadata["_initial_state"] = initial_state
        task.metadata["_assertions"] = assertions

        world = up["WorldState"](**initial_state)
        world.meta.allowed_services = up["compute_allowed_services"](
            initial_state, assertions, zapier_tools)
        return AutomationRealEnv(task, world, up, system_prompt,
                                 max_tool_calls=self.max_tool_calls)

    # -- scoring ----------------------------------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        if self.real:
            return self._score_real(task, trajectory, env)
        return self._score_mock(task, trajectory, env)

    def _score_real(self, task, trajectory, env: AutomationRealEnv) -> Result:
        up = load_upstream()
        state = {
            "world": env.world,
            "info": {"assertions": task.metadata.get("_assertions", [])},
            "initial_state": task.metadata.get("_initial_state", {}),
        }
        pc = up["partial_credit"](state)          # 0.0..1.0
        tcc = up["task_completed_correctly"](state)  # 0.0 or 1.0
        success = tcc == 1.0

        # (req 4) self-report vs actual: agent claims completion but failed.
        claimed = _claims_success(trajectory.final_output)
        false_success = claimed and not success

        return Result(
            task_id=task.id, benchmark=self.name, agent="",
            success=success, score=float(pc),
            metrics={
                "domain": task.metadata["domain"],
                "task_name": task.metadata.get("task_name", ""),
                "baseline_only": task.metadata.get("baseline_only", False),
                "partial_credit": float(pc),
                "task_completed_correctly": float(tcc),
                "num_assertions": len(task.metadata.get("_assertions", [])),
                "num_tool_calls": env.num_tool_calls(),
                "claimed_success": claimed,
                "false_success_claim": false_success,
            },
            cost=trajectory.cost, wall_time=trajectory.wall_time,
        )

    def _score_mock(self, task, trajectory, env: AutomationMockEnv) -> Result:
        snapshot = env.snapshot()
        preds = task.metadata.get("goal", [])
        matched = sum(1 for p in preds if _mock_predicate_met(snapshot, p))
        total = len(preds)
        return Result(
            task_id=task.id, benchmark=self.name, agent="",
            success=(matched == total), score=(matched / total if total else 1.0),
            metrics={"domain": task.metadata.get("domain", "crm(mock)"),
                     "predicates_matched": matched, "predicates_total": total,
                     "num_tool_calls": len(trajectory.tool_calls())},
            cost=trajectory.cost, wall_time=trajectory.wall_time,
        )

    # -- custom report ----------------------------------------------------
    def report(self, results: list[Result]) -> str | None:
        if not self.real:
            return None
        from collections import defaultdict
        official = [r for r in results if not r.metrics.get("baseline_only")]
        if not official:
            return None
        by_domain: dict[str, list[Result]] = defaultdict(list)
        for r in official:
            by_domain[r.metrics.get("domain", "?")].append(r)

        lines = ["=== AutomationBench domain breakdown (public task set) ==="]
        lines.append(f"  {'domain':<12} {'n':>4} {'pass_rate':>10} "
                     f"{'false_success':>14}")
        for domain in sorted(by_domain):
            rs = by_domain[domain]
            n = len(rs)
            pr = sum(1 for r in rs if r.success) / n
            fs = sum(1 for r in rs if r.metrics.get("false_success_claim")) / n
            lines.append(f"  {domain:<12} {n:>4} {pr:>10.3f} {fs:>14.3f}")
        n = len(official)
        pr = sum(1 for r in official if r.success) / n
        fs = sum(1 for r in official if r.metrics.get("false_success_claim")) / n
        claimed = sum(1 for r in official if r.metrics.get("claimed_success"))
        lines.append(f"  {'OFFICIAL':<12} {n:>4} {pr:>10.3f} {fs:>14.3f}")
        lines.append("")
        lines.append(f"  official pass rate (task_completed_correctly): {pr:.1%}")
        lines.append(f"  false-success-claim rate: {fs:.1%} "
                     f"({sum(1 for r in official if r.metrics.get('false_success_claim'))}"
                     f"/{n}; of {claimed} completion claims)")
        base = [r for r in results if r.metrics.get("baseline_only")]
        if base:
            bpr = sum(1 for r in base if r.success) / len(base)
            lines.append(f"  simple baseline (excluded from score): "
                         f"pass_rate={bpr:.3f} over {len(base)}")
        lines.append("")
        lines.append("  NOTE: this is the PUBLIC task set. The official "
                     "AutomationBench leaderboard is scored on a private "
                     "held-out set, so these numbers are directional and will "
                     "not match the official leaderboard 1:1.")
        return "\n".join(lines)


# Re-exported for tests / external checks.
__all__ = ["AutomationBench", "AutomationRealEnv", "AutomationMockEnv",
           "upstream_available"]
