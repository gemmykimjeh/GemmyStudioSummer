"""tau_bench — tool-agent-user simulation (retail + airline).

(a) License:        MIT (sierra-research/tau-bench).
(b) Infrastructure: REAL mode drives the upstream tau-bench simulator (cloned to
                    external/tau-bench) and an LLM user-simulator via litellm —
                    needs API keys for the user-sim provider (and the agent
                    model). MOCK fallback needs nothing.
(c) Scoring:        upstream final-DB-state reward (data-hash match to the gold
                    action sequence) + required-output substring check. 0/1 per
                    task; the harness aggregates the official pass^k.
(d) Official repo:  https://github.com/sierra-research/tau-bench

Two modes, selected by ``real`` (default True):

* ``real=True``  — the actual upstream tasks (retail + airline test splits),
  simulator, LLM user, and reward. NOT tagged ``[MOCK]``. Requires the repo at
  external/tau-bench and ``pip install -e ".[tau_bench]"``.
* ``real=False`` — a compact in-memory reproduction (SHAPE-FAITHFUL MOCK, not
  upstream), tagged ``[MOCK]`` everywhere, so the pipeline runs offline with no
  keys. Kept as a fallback / smoke-test path.

The user-simulator model is configured separately from the agent model
(``user_model`` / ``user_provider`` / ``user_strategy``), so you can drive a
Claude agent with, e.g., a GPT-4o user.
"""

from __future__ import annotations

from harness.benchmark import Benchmark, Env
from harness.benchmarks._tau_upstream import load_upstream
from harness.benchmarks._util import interleave
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_REAL_DOMAINS = ("retail", "airline")


def _infer_provider(model: str) -> str:
    m = model.lower()
    if "claude" in m:
        return "anthropic"
    if "gemini" in m:
        return "gemini"
    if "mistral" in m:
        return "mistral"
    return "openai"  # gpt-*, o1/o3, default


# =====================================================================
# REAL upstream adapter
# =====================================================================
class TauBenchRealEnv(Env):
    """Wraps an upstream tau-bench ``Env`` behind the generic tool interface.

    Conversational: as in upstream, the agent talks to the user with plain text
    (routed through ``respond``), not a tool — so ``tools()`` exposes only the
    domain tools, and the episode runs until the user ends the chat.
    """

    conversational = True

    def __init__(self, task: Task, tau_env, action_cls) -> None:
        self.task = task
        self.tau_env = tau_env
        self._Action = action_cls
        self._reset_done = False
        self._opening = ""
        self._done = False
        self._final_reward: float | None = None
        self._reward_info = None

    def _ensure_reset(self) -> None:
        if not self._reset_done:
            resp = self.tau_env.reset(task_index=self.tau_env.task_index)
            self._opening = resp.observation
            self._reset_done = True

    def observation(self) -> str:
        # Triggers the user simulator's opening line (an LLM call in llm mode).
        self._ensure_reset()
        return self._opening

    def instructions(self) -> str:
        rules = "\n".join(f"- {r}" for r in getattr(self.tau_env, "rules", []))
        wiki = getattr(self.tau_env, "wiki", "") or ""
        return f"{wiki}\n\n# Domain rules\n{rules}".strip()

    def tools(self) -> list[ToolSpec]:
        # Domain tools only. Talking to the user is plain text via respond().
        return [
            ToolSpec(
                name=info["function"]["name"],
                description=info["function"].get("description", ""),
                parameters=info["function"].get(
                    "parameters", {"type": "object", "properties": {}}),
            )
            for info in self.tau_env.tools_info
        ]

    def _step(self, name: str, arguments: dict) -> str:
        """Drive one upstream action, tracking done/reward. Returns observation."""
        if self._done:
            return "[the conversation has ended]"
        resp = self.tau_env.step(self._Action(name=name, kwargs=arguments or {}))
        if resp.done and not self._done:
            self._done = True
            self._final_reward = resp.reward
            self._reward_info = resp.info.reward_info
        obs = str(resp.observation)
        if "###STOP###" in obs:
            obs = obs.replace("###STOP###", "").strip() or \
                "[the user has ended the conversation]"
        return obs

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        obs = self._step(name, arguments)
        is_error = obs.startswith("Error") or obs.startswith("Unknown action")
        return ToolResult(output=obs, is_error=is_error)

    def respond(self, text: str) -> str:
        # RESPOND_ACTION_NAME upstream is "respond".
        return self._step("respond", {"content": text})

    def episode_done(self) -> bool:
        return self._done

    def snapshot(self) -> dict:
        return {"data_hash": self.tau_env.get_data_hash(), "done": self._done}

    def reward(self):
        """Return (reward, reward_info) for scoring, computed at most once."""
        if self._done and self._final_reward is not None:
            return self._final_reward, self._reward_info
        res = self.tau_env.calculate_reward()
        return res.reward, res.info

    def user_cost(self) -> float:
        try:
            return float(self.tau_env.user.get_total_cost() or 0.0)
        except Exception:  # noqa: BLE001 - human user / cost unavailable
            return 0.0


# =====================================================================
# MOCK fallback (SHAPE-FAITHFUL — not upstream)
# =====================================================================
import copy  # noqa: E402
import json  # noqa: E402

_MOCK_POLICY = (
    "You are a retail customer-support agent. Policy: you may only cancel or "
    "modify orders whose status is 'pending'. Never cancel or modify an order "
    "that has already 'shipped'. Confirm each completed action via message_user."
)


def _mock_initial_db() -> dict:
    return {
        "users": {
            "U100": {"name": "Alice", "email": "alice@example.com",
                     "orders": ["O1001", "O1002"]},
            "U200": {"name": "Bob", "email": "bob@example.com",
                     "orders": ["O2001"]},
        },
        "orders": {
            "O1001": {"user_id": "U100", "status": "pending",
                      "items": ["widget"], "address": "1 Alpha St"},
            "O1002": {"user_id": "U100", "status": "shipped",
                      "items": ["gadget"], "address": "1 Alpha St"},
            "O2001": {"user_id": "U200", "status": "pending",
                      "items": ["gizmo"], "address": "9 Beta Ave"},
        },
    }


class TauBenchMockEnv(Env):
    """In-memory retail domain (shape-faithful mock, not upstream)."""

    def __init__(self, task: Task) -> None:
        self.task = task
        self.db = copy.deepcopy(_mock_initial_db())
        self._user_responses = list(task.metadata.get("user_responses", []))
        self._user_idx = 0

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name="get_user_details",
                     description="Look up a user's profile and order ids.",
                     parameters={"type": "object",
                                 "properties": {"user_id": {"type": "string"}},
                                 "required": ["user_id"]}),
            ToolSpec(name="get_order_details",
                     description="Look up an order's status, items, and address.",
                     parameters={"type": "object",
                                 "properties": {"order_id": {"type": "string"}},
                                 "required": ["order_id"]}),
            ToolSpec(name="cancel_order",
                     description="Cancel a pending order (fails for shipped).",
                     parameters={"type": "object",
                                 "properties": {"order_id": {"type": "string"},
                                                "reason": {"type": "string"}},
                                 "required": ["order_id"]}),
            ToolSpec(name="modify_order_address",
                     description="Change the shipping address of a pending order.",
                     parameters={"type": "object",
                                 "properties": {"order_id": {"type": "string"},
                                                "address": {"type": "string"}},
                                 "required": ["order_id", "address"]}),
            ToolSpec(name="message_user",
                     description="Send a message to the user and read the reply.",
                     parameters={"type": "object",
                                 "properties": {"content": {"type": "string"}},
                                 "required": ["content"]}),
        ]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        try:
            return handler(arguments)
        except KeyError as exc:
            return ToolResult(output=f"missing argument: {exc}", is_error=True)

    def _tool_get_user_details(self, args):
        u = self.db["users"].get(args["user_id"])
        return ToolResult(output=json.dumps(u)) if u else \
            ToolResult(output="user not found", is_error=True)

    def _tool_get_order_details(self, args):
        o = self.db["orders"].get(args["order_id"])
        return ToolResult(output=json.dumps(o)) if o else \
            ToolResult(output="order not found", is_error=True)

    def _tool_cancel_order(self, args):
        oid = args["order_id"]
        o = self.db["orders"].get(oid)
        if not o:
            return ToolResult(output="order not found", is_error=True)
        if o["status"] == "shipped":
            return ToolResult(output="policy violation: cannot cancel a shipped "
                                     "order", is_error=True)
        if o["status"] == "cancelled":
            return ToolResult(output=f"{oid} is already cancelled")
        o["status"] = "cancelled"
        return ToolResult(output=f"{oid} cancelled")

    def _tool_modify_order_address(self, args):
        oid = args["order_id"]
        o = self.db["orders"].get(oid)
        if not o:
            return ToolResult(output="order not found", is_error=True)
        if o["status"] == "shipped":
            return ToolResult(output="policy violation: cannot modify a shipped "
                                     "order", is_error=True)
        o["address"] = args["address"]
        return ToolResult(output=f"{oid} address updated")

    def _tool_message_user(self, args):
        if self._user_idx < len(self._user_responses):
            reply = self._user_responses[self._user_idx]
            self._user_idx += 1
        else:
            reply = "That's everything, thank you."
        return ToolResult(output=reply)

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return _MOCK_POLICY

    def snapshot(self) -> dict:
        return {"orders": {oid: {"status": o["status"], "address": o["address"]}
                           for oid, o in self.db["orders"].items()}}


def _mock_tasks() -> list[Task]:
    common = {"benchmark": "tau_bench"}
    dom = {"domain": "retail(mock)", "split": "mock"}
    return [
        Task(id="tau-mock-001", **common,
             prompt="Hi, this is Alice (U100). Please cancel my order O1001.",
             metadata={**dom,
                       "goal": {"orders": {"O1001": {"status": "cancelled"}}},
                       "required_outputs": ["O1001"],
                       "user_responses": ["Yes please.", "No, that's all."],
                       "oracle_actions": [
                           {"tool": "cancel_order",
                            "arguments": {"order_id": "O1001", "reason": "x"}},
                           {"tool": "message_user",
                            "arguments": {"content": "Order O1001 is cancelled."}}]}),
        Task(id="tau-mock-002", **common,
             prompt="This is Bob (U200). Change O2001's address to '42 New Rd'.",
             metadata={**dom,
                       "goal": {"orders": {"O2001": {"address": "42 New Rd"}}},
                       "user_responses": ["Correct.", "That's all."],
                       "oracle_actions": [
                           {"tool": "modify_order_address",
                            "arguments": {"order_id": "O2001",
                                          "address": "42 New Rd"}},
                           {"tool": "message_user",
                            "arguments": {"content": "Address updated."}}]}),
        Task(id="tau-mock-003", **common,
             prompt="Alice here (U100). Cancel order O1002 for me.",
             metadata={**dom,
                       "goal": {"orders": {"O1002": {"status": "shipped"}}},
                       "required_outputs": ["cannot", "shipped"],
                       "user_responses": ["Oh I see.", "That's all."],
                       "oracle_actions": [
                           {"tool": "cancel_order",
                            "arguments": {"order_id": "O1002", "reason": "x"}},
                           {"tool": "message_user",
                            "arguments": {"content": "Sorry, I cannot cancel "
                                          "O1002 because it has shipped."}}]}),
        Task(id="tau-mock-004", **common,
             prompt="This is Bob (U200). Please cancel O2001.",
             metadata={**dom,
                       "goal": {"orders": {"O2001": {"status": "cancelled"}}},
                       "user_responses": ["Yes.", "Nothing else."],
                       "oracle_actions": [
                           {"tool": "cancel_order",
                            "arguments": {"order_id": "O2001", "reason": "x"}},
                           {"tool": "message_user",
                            "arguments": {"content": "O2001 has been cancelled."}}]}),
    ]


def _leaf_paths(d: dict, prefix=()):
    leaves = []
    for k, v in d.items():
        if isinstance(v, dict):
            leaves.extend(_leaf_paths(v, prefix + (k,)))
        else:
            leaves.append((prefix + (k,), v))
    return leaves


_MISSING = object()


def _get_path(d, path):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return _MISSING
        cur = cur[key]
    return cur


# =====================================================================
# Dispatcher
# =====================================================================
@register_benchmark("tau_bench")
class TauBench(Benchmark):
    """τ-bench adapter. REAL upstream by default; MOCK fallback via real=False."""

    def __init__(
        self,
        real: bool = True,
        split: str = "test",
        domains: tuple[str, ...] | None = None,
        user_model: str = "gpt-4o",
        user_provider: str | None = None,
        user_strategy: str = "llm",
    ) -> None:
        self.real = real
        self.mock = not real  # drives the [MOCK] tag in runner/CLI/CSV
        self.split = split
        self.domains = tuple(domains) if domains else _REAL_DOMAINS
        self.user_model = user_model
        self.user_provider = user_provider or _infer_provider(user_model)
        self.user_strategy = user_strategy

    # -- task loading -----------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        tasks = self._real_load_tasks() if self.real else _mock_tasks()
        return tasks[:limit] if limit is not None else tasks

    def _real_load_tasks(self) -> list[Task]:
        get_env, _Action, _US = load_upstream()
        per_domain: list[list[Task]] = []
        for domain in self.domains:
            try:
                # human user => no LLM call; just to read the task list.
                probe = get_env(domain, user_strategy="human", user_model="x",
                                task_split=self.split, task_index=0)
            except ValueError:
                continue  # e.g. airline has no train/dev split
            n = len(probe.tasks)
            per_domain.append([
                Task(
                    id=f"{domain}-{self.split}-{i:03d}",
                    benchmark="tau_bench",
                    prompt=(f"You are a customer-service agent for the {domain} "
                            "domain. Assist the user through the conversation, "
                            "using the available tools and following the domain "
                            "policy. Use `respond` to talk to the user."),
                    metadata={"domain": domain, "split": self.split,
                              "task_index": i},
                )
                for i in range(n)
            ])
        # Round-robin interleave so a small --limit still spans domains.
        return interleave(per_domain)

    # -- setup ------------------------------------------------------------
    def setup(self, task: Task) -> Env:
        if not self.real:
            return TauBenchMockEnv(task)
        get_env, Action, _US = load_upstream()
        tau_env = get_env(
            task.metadata["domain"],
            user_strategy=self.user_strategy,
            user_model=self.user_model,
            user_provider=self.user_provider,
            task_split=task.metadata["split"],
            task_index=task.metadata["task_index"],
        )
        return TauBenchRealEnv(task, tau_env, Action)

    # -- scoring ----------------------------------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        if self.real:
            return self._score_real(task, trajectory, env)
        return self._score_mock(task, trajectory, env)

    def _score_real(self, task, trajectory, env: TauBenchRealEnv) -> Result:
        reward, info = env.reward()
        user_cost = env.user_cost()
        metrics: dict = {
            "domain": task.metadata["domain"],
            "split": task.metadata["split"],
            "reward": reward,
            "agent_cost": trajectory.cost,
            "user_cost": user_cost,
            "agent_tokens": trajectory.tokens,
            "num_tool_calls": len(trajectory.tool_calls()),
        }
        if info is not None:
            if hasattr(info, "r_actions"):
                metrics["r_actions"] = info.r_actions
            if hasattr(info, "r_outputs"):
                metrics["r_outputs"] = info.r_outputs
                metrics["outputs"] = getattr(info, "outputs", {})
        return Result(
            task_id=task.id, benchmark=self.name, agent="",
            success=(reward >= 1.0), score=float(reward), metrics=metrics,
            cost=trajectory.cost + user_cost, wall_time=trajectory.wall_time,
        )

    def _score_mock(self, task, trajectory, env: TauBenchMockEnv) -> Result:
        snapshot = env.snapshot()
        goal = task.metadata.get("goal", {})
        leaves = _leaf_paths(goal)
        matched_state = sum(1 for p, exp in leaves if _get_path(snapshot, p) == exp)
        required = task.metadata.get("required_outputs", [])
        said = " ".join(
            (s.arguments.get("content") or "")
            for s in trajectory.tool_calls() if s.name == "message_user"
        ).lower()
        matched_out = sum(1 for sub in required if sub.lower() in said)
        total = len(leaves) + len(required)
        matched = matched_state + matched_out
        return Result(
            task_id=task.id, benchmark=self.name, agent="",
            success=(matched == total), score=(matched / total if total else 1.0),
            metrics={"domain": task.metadata.get("domain", "retail(mock)"),
                     "state_matched": matched_state, "state_total": len(leaves),
                     "output_matched": matched_out, "output_total": len(required),
                     "num_tool_calls": len(trajectory.tool_calls())},
            cost=trajectory.cost, wall_time=trajectory.wall_time,
        )
