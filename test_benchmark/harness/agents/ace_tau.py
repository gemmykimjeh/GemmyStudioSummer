"""ace_tau — ACE on tau_bench with a LITERAL ReAct loop (Thought/Action/Observation).

tau_bench is a multi-turn customer-service dialogue with tools, scored by a 0/1
upstream reward. Per the ACE paper this is the "ReAct + ACE" setting, so the base
agent here is a *literal* ReAct loop (Yao et al. 2023): the model emits
``Thought: ...`` then ``Action: <verb>[<arg>]`` as plain text, the loop executes
the action and feeds back ``Observation: ...`` — NOT native function-calling.

Action space over the generic Env:
  - each benchmark tool:            Action: get_order_details[{"order_id": "O1"}]
  - talk to the user (conversational): Action: respond_to_user["<message>"]
  - end:                            Action: finish[]

The ACE learning is ACE's own code, unchanged: playbook injected into the system
prompt, then after the episode the upstream reward (``env.reward()`` — the
execution-feedback signal, no ground-truth labels, AppWorld/online style) drives
ACE's Reflector → update_bullet_counts → Curator → BulletpointAnalyzer.

Playbook starts EMPTY and carries across tasks → run with ``--concurrency 1``.
All ace imports are lazy so registry autoload never needs the ace repo.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_ACTION_RE = re.compile(r"Action:\s*([A-Za-z_]\w*)\s*\[")


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


def _parse_action(text: str):
    """Extract the LAST `Action: verb[arg]` from a ReAct turn (bracket-matched)."""
    matches = list(_ACTION_RE.finditer(text))
    if not matches:
        return None, None
    last = matches[-1]
    verb = last.group(1)
    start = last.end()  # just after '['
    depth, i = 1, start
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        i += 1
    arg = text[start:i - 1] if depth == 0 else text[start:]
    return verb, arg.strip()


@register_agent("ace_tau")
class ACETauAgent(Agent):
    """Literal ReAct loop + ace Reflector/Curator/analyzer (upstream-reward feedback)."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        max_steps: int = 30,
        max_tokens: int = 4096,
        api_provider: str = "anthropic",
        ace_path: str = r"C:\GemmyStudioSummer\ReAct",
        playbook_out: str = "ace_playbook_tau.txt",
        curator_frequency: int = 1,
        token_budget: int = 80000,
        use_bulletpoint_analyzer: bool = True,
        dedup_threshold: float = 0.80,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.api_provider = api_provider
        self.ace_path = ace_path
        self.playbook_out = playbook_out
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self.use_bulletpoint_analyzer = use_bulletpoint_analyzer
        self.dedup_threshold = dedup_threshold
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        self._ready = False
        self._lock = threading.Lock()
        self._client = None
        self._generator = None
        self._reflector = None
        self._curator = None
        self._analyzer = None
        self._helpers = None
        self.playbook = ""
        self.next_global_id = 1
        self._step = 0
        self._log_dir = None

    # -- one-time setup ----------------------------------------------------
    def _ensure(self) -> None:
        if self._ready:
            return
        if self.ace_path and self.ace_path not in sys.path:
            sys.path.insert(0, self.ace_path)
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(os.path.join(self.ace_path, ".env"))
            load_dotenv()
        except Exception:  # noqa: BLE001
            pass
        if not self._api_key:
            self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; ace_tau cannot run. Put it in "
                f"{os.path.join(self.ace_path, '.env')} or the environment.")
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError('ace_tau needs the anthropic SDK: pip install -e ".[claude]".') from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)

        try:
            from ace import Generator, Reflector, Curator, BulletpointAnalyzer  # noqa: PLC0415
            from utils import initialize_clients  # noqa: PLC0415
            from playbook_utils import (  # noqa: PLC0415
                get_next_global_id, update_bullet_counts,
                get_playbook_stats, extract_playbook_bullets,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_tau could not import the ace repo from {self.ace_path!r} ({exc}).") from exc

        _gen_c, ref_c, cur_c = initialize_clients(self.api_provider)
        # ACE's Generator: reused here only for its bullet-id citation extraction
        # (_extract_bullet_ids). The multi-step ReAct loop below IS the agentic
        # generator (playbook-conditioned, looped) — the AppWorld setting in the
        # paper likewise runs a ReAct loop, not the single-shot Generator prompt.
        self._generator = Generator(_gen_c, self.api_provider, self.model, self.max_tokens)
        self._reflector = Reflector(ref_c, self.api_provider, self.model, self.max_tokens)
        self._curator = Curator(cur_c, self.api_provider, self.model, self.max_tokens)
        if self.use_bulletpoint_analyzer:
            self._analyzer = BulletpointAnalyzer(cur_c, self.model, self.max_tokens)
        self._helpers = {
            "update_bullet_counts": update_bullet_counts,
            "get_playbook_stats": get_playbook_stats,
            "extract_playbook_bullets": extract_playbook_bullets,
        }
        self.playbook = _EMPTY_PLAYBOOK
        self.next_global_id = get_next_global_id(self.playbook)
        self._log_dir = os.path.abspath(os.path.join("ace_tau_run", "detailed_llm_logs"))
        os.makedirs(self._log_dir, exist_ok=True)
        self._ready = True

    # -- Agent API ---------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        self._ensure()
        with self._lock:  # shared playbook -> run with --concurrency 1
            return self._run_locked(task, env)

    def _run_locked(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        trace, cited = self._react(task, env, traj)
        self._adapt(task, env, traj, trace, cited)
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- (a) LITERAL ReAct loop (Thought/Action/Observation) ---------------
    def _react(self, task: Task, env: Env, traj: Trajectory) -> str:
        client = self._client
        specs = env.tools()
        tool_names = {t.name for t in specs}
        conversational = getattr(env, "conversational", False)

        tool_desc = "\n".join(
            f"- {t.name}[<json args>]: {t.description}" for t in specs) or "(no tools)"
        system = (
            "You solve the task by REASONING and ACTING in the ReAct format. On "
            "every turn output EXACTLY one Thought line and one Action line:\n"
            "Thought: <your step-by-step reasoning>\n"
            "Action: <verb>[<arg>]\n\n"
            "Available actions:\n" + tool_desc + "\n"
            + ('- respond_to_user["<message>"]: say something to the customer and get their reply\n'
               if conversational else "")
            + "- finish[]: stop when the task is fully resolved\n\n"
            + 'For tool actions put a JSON object in the brackets, e.g. '
            'Action: get_order_details[{"order_id": "O1001"}]. '
            "Emit ONE Action per turn, then wait for the Observation."
        )
        extra = env.instructions()
        if extra:
            system += f"\n\n# Domain policy (follow exactly)\n{extra}"
        if self.playbook.strip():
            system += ("\n\n# PLAYBOOK — lessons from previous tasks. Apply the relevant "
                       "ones and cite the ids you use, e.g. [err-00001].\n" + self.playbook)

        obs0 = env.observation()
        traj.add(Step(type="user_message", text=obs0))
        transcript = f"Task / conversation so far:\n{obs0}\n"
        in_p, out_p = _price_for(self.model)
        trace_parts = [obs0]
        last_text = None
        cited: set[str] = set()  # playbook ids the agent cited across ReAct turns

        for _ in range(self.max_steps):
            resp = client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system,
                messages=[{"role": "user", "content": transcript
                           + "\nRespond with the next Thought and Action."}])
            u = resp.usage
            traj.tokens += (u.input_tokens or 0) + (u.output_tokens or 0)
            traj.cost += ((u.input_tokens or 0) * in_p + (u.output_tokens or 0) * out_p) / 1_000_000
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            traj.add(Step(type="assistant_message", text=text))
            trace_parts.append(text)
            transcript += "\n" + text + "\n"
            # ACE's own citation extraction, applied to the actual generation turn.
            cited.update(self._generator._extract_bullet_ids(text, use_json_mode=False))

            verb, arg = _parse_action(text)
            if verb is None:
                # No action emitted. In a dialogue, treat the whole text as a user message.
                if conversational and text and not env.episode_done():
                    reply = env.respond(text)
                    last_text = text
                    transcript += f"Observation: {reply}\n"
                    trace_parts.append(f"Observation: {reply}")
                    traj.add(Step(type="user_message", text=reply))
                    if env.episode_done():
                        break
                    continue
                break

            if verb == "finish":
                break
            if verb == "respond_to_user" and conversational:
                msg = arg
                try:
                    if msg and msg[0] in "\"'":
                        msg = json.loads(msg) if msg[0] == '"' else msg.strip("'")
                except Exception:  # noqa: BLE001
                    msg = arg.strip().strip('"').strip("'")
                reply = env.respond(msg)
                last_text = msg
                obs = reply
                traj.add(Step(type="user_message", text=reply))
            elif verb in tool_names:
                try:
                    args = json.loads(arg) if arg.strip().startswith("{") else {}
                except Exception:  # noqa: BLE001
                    args = {}
                r = env.call_tool(verb, args)
                obs = r.output
                traj.add(Step(type="tool_call", name=verb, arguments=args,
                              output=r.output, is_error=r.is_error))
            else:
                obs = (f"Invalid action {verb!r}. Valid: {sorted(tool_names)}"
                       + (", respond_to_user, finish" if conversational else ", finish"))
                traj.add(Step(type="error", name=verb, output=obs, is_error=True))

            transcript += f"Observation: {obs}\n"
            trace_parts.append(f"Observation: {obs}")
            if conversational and env.episode_done():
                break

        traj.final_output = last_text
        return "\n".join(trace_parts), sorted(cited)

    # -- (b) upstream reward (data_processor role) + ACE reflect/count/curate
    def _adapt(self, task: Task, env: Env, traj: Trajectory, trace: str,
               bullet_ids: list) -> None:
        H = self._helpers
        step_id = f"tau_s_{self._step + 1}"
        try:
            reward, info = env.reward()
            # tau_bench's upstream calculate_reward() is DESTRUCTIVE: it reloads the
            # DB and replays the gold actions, mutating the env. The harness only
            # caches the result when the episode ended via the user (`_done`); if
            # the agent finished otherwise, calling reward() twice (here + the
            # benchmark's own score()) recomputes on a corrupted state and yields a
            # wrong (often false-PASS) official score. Cache our first, correct-on-
            # the-agent's-actual-state result so score() reads it instead.
            if hasattr(env, "_final_reward"):
                env._done = True
                env._final_reward = reward
                env._reward_info = info
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_tau] reward() failed on {task.id}: {exc}")
            reward, info = 0.0, None
        success = reward >= 1.0
        environment_feedback = (
            f"Episode reward = {reward} ({'SUCCESS' if success else 'FAILURE'}). "
            f"reward_info: {str(info)[:800]}")

        bullets_used = H["extract_playbook_bullets"](self.playbook, bullet_ids)
        try:
            reflection, bullet_tags, _ = self._reflector.reflect(
                question=task.prompt, reasoning_trace=trace[:6000],
                predicted_answer=(traj.final_output or "")[:2000], ground_truth=None,
                environment_feedback=environment_feedback, bullets_used=bullets_used,
                use_ground_truth=False, use_json_mode=False,
                call_id=f"{step_id}_reflect", log_dir=self._log_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_tau] reflect failed on {task.id}: {exc}")
            self._step += 1
            return

        if bullet_tags:
            self.playbook = H["update_bullet_counts"](self.playbook, bullet_tags)

        self._step += 1
        n_ops = 0
        if self._step % self.curator_frequency == 0:
            try:
                stats = H["get_playbook_stats"](self.playbook)
                self.playbook, self.next_global_id, ops, _ = self._curator.curate(
                    current_playbook=self.playbook, recent_reflection=reflection,
                    question_context=(task.prompt or "")[:2000], current_step=self._step,
                    total_samples=max(self._step, 1), token_budget=self.token_budget,
                    playbook_stats=stats, use_ground_truth=False, use_json_mode=False,
                    call_id=step_id, log_dir=self._log_dir, next_global_id=self.next_global_id)
                n_ops = len(ops)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_tau] curate failed on {task.id}: {exc}")

        chars_before = len(self.playbook)
        if self._analyzer is not None:
            try:
                self.playbook = self._analyzer.analyze(
                    playbook=self.playbook, threshold=self.dedup_threshold, merge=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_tau] analyzer failed on {task.id}: {exc}")

        try:
            with open(self.playbook_out, "w", encoding="utf-8") as f:
                f.write(self.playbook)
        except Exception:  # noqa: BLE001
            pass
        print(f"[ace_tau] task {self._step}: reward={reward} success={success} "
              f"bullets_cited={len(bullet_ids)} tags={len(bullet_tags)} "
              f"curator_ops={n_ops} playbook_chars={chars_before}->{len(self.playbook)}")


_EMPTY_PLAYBOOK = """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""
