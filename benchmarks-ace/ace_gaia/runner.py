"""ACE online mode driven by the OpenHands evaluation loop.

The outer loop stays OpenHands'. ``Evaluation.run`` keeps owning workspace
lifecycle, retries, ``output.jsonl``, metrics and error capture. What is ported
here is ACE's *per-sample* logic — ``ACE._train_single_sample`` — because that is
the entirety of ACE's feedback loop; the surrounding ``_offline_train`` /
``_online_train_and_test`` machinery only adds epochs, evaluation windows and
``best_playbook`` tracking, none of which apply to a single pass with no split.

The call sequence below mirrors ``ace/ace.py`` step for step:

    1. generate (pre-train answer)
    2. on failure: up to ``max_num_rounds`` of
         reflect -> update_bullet_counts -> regenerate -> re-check, break if correct
    3. on success: reflect once, for bullet tagging only
    4. every ``curator_frequency`` steps: curate -> apply operations
    5. optionally: BulletpointAnalyzer dedup

Two properties are load-bearing and easy to break:

**The reported score is the pre-train answer.** Reflection rounds consult ground
truth to decide whether to retry, so the post-train answer measures "did the
agent get there once told it was wrong up to three times", not task performance.
``test_result["score"]`` is therefore the round-0 result; the post-train outcome
is recorded alongside it under separate keys.

**Instances that never produced an answer are not ACE samples.** ACE has no error
path — ``_train_single_sample`` has no ``try``/``except``, and an empty answer
scores as an ordinary wrong one, which would send the Reflector to diagnose a
Docker failure and let the Curator distil a bullet from it. That bullet would
then sit in every later task's context. Here the harness's own error handling
runs unchanged and ACE is skipped entirely for such instances.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from pydantic import ConfigDict, Field, PrivateAttr

from benchmarks.gaia.run_infer import GAIAEvaluation
from benchmarks.utils.models import EvalInstance, EvalOutput
from openhands.sdk import get_logger
from openhands.sdk.context import AgentContext

from .data_processor import GAIADataProcessor
from .trace import compact_trace
from .vendor.ace.core.curator import Curator
from .vendor.ace.core.generator import Generator
from .vendor.ace.core.reflector import Reflector
from .vendor.llm import ACEClient
from .vendor.logger import log_bullet_usage
from .vendor.playbook_utils import (
    extract_playbook_bullets,
    get_next_global_id,
    get_playbook_stats,
    update_bullet_counts,
)


logger = get_logger(__name__)


# Copied verbatim from ``ACE._initialize_empty_playbook`` (ace/ace.py). The
# Curator selects a section by name when emitting ADD operations, and
# ``get_section_slug`` maps those names to bullet-ID prefixes, so the headers
# must match the reference exactly.
EMPTY_PLAYBOOK = """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""


# ACE's config defaults (ace/ace.py:_extract_config_params), carried over as-is.
ACE_DEFAULTS: dict[str, Any] = {
    "max_num_rounds": 3,
    "curator_frequency": 1,
    "playbook_token_budget": 80000,
    "json_mode": False,
    "no_ground_truth": False,
    "use_bulletpoint_analyzer": False,
    "bulletpoint_analyzer_threshold": 0.90,
    "max_tokens": 4096,
}


# Ported verbatim from ``GENERATOR_PROMPT`` (ace/prompts/generator.py). The
# playbook-usage lines cross over; the JSON output contract does not, because
# GAIA scoring depends on the <solution> tag.
PLAYBOOK_PREAMBLE = """\
# Curated playbook

You have a curated playbook of strategies and insights, accumulated from earlier
tasks. Use it as follows:

- Read the playbook carefully and apply relevant strategies, formulas, and insights
- Pay attention to common mistakes listed in the playbook and avoid them
- If the playbook contains relevant code snippets or formulas, use them appropriately
- Double-check your calculations and logic before providing the final answer

Each line in the playbook has a bullet_id in square brackets, along with counts of
how often that bullet has previously proved helpful or harmful.

## Required: report which bullets you used

Every bulletpoint in the playbook that was relevant or helpful for answering this
question, you MUST report by its bullet_id. End your final message with the tag
below, after your <solution> tag. This is required on every task, including when
you used nothing and including when the playbook is empty:

    <bullets_used>ctx-00003, err-00007</bullets_used>
    <bullets_used></bullets_used>

The tag does not affect your answer and is never scored. Omitting it entirely is
an error; an empty tag is a valid and expected answer.
"""

REFLECTION_PREAMBLE = """\
# Reflection on your previous attempt

A previous attempt at this exact task was incorrect. The following is a diagnosis
of what went wrong. Take it into account; do not repeat the same mistake.
"""


class ACEGAIAEvaluation(GAIAEvaluation):
    """GAIA evaluation with ACE's feedback loop wrapped around each instance."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ace_config: dict[str, Any] = Field(default_factory=dict)

    _playbook: str = PrivateAttr(default=EMPTY_PLAYBOOK)
    _reflector: Any = PrivateAttr(default=None)
    _curator: Any = PrivateAttr(default=None)
    _analyzer: Any = PrivateAttr(default=None)
    _bullet_extractor: Any = PrivateAttr(default=None)
    _data_processor: Any = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default=None)
    _step: int = PrivateAttr(default=0)
    _total_samples: int = PrivateAttr(default=0)
    _ace_dir: str = PrivateAttr(default="")
    _log_dir: str = PrivateAttr(default="")
    _playbook_dir: str = PrivateAttr(default="")
    _rounds_dir: str = PrivateAttr(default="")
    _usage_log_path: str = PrivateAttr(default="")
    # Per-instance injection, read back by ``_build_agent``. Safe only because
    # the train pass is sequential; ``run`` refuses to start otherwise.
    _injection: tuple[str, str] = PrivateAttr(default=("", "(empty)"))
    # The round-0 EvalOutput, kept so the reported score and history describe
    # the same attempt even after later rounds overwrite ``out``.
    _round0_output: Any = PrivateAttr(default=None)

    # ------------------------------------------------------------------ setup

    def model_post_init(self, __context) -> None:
        super().model_post_init(__context)

        if self.num_workers != 1:
            raise ValueError(
                f"ACE requires sequential execution but num_workers={self.num_workers}. "
                "The playbook is mutated in place between instances and the Curator "
                "receives the step index, so concurrent instances would train against "
                "inconsistent playbooks. Re-run with --num-workers 1."
            )

        cfg = {**ACE_DEFAULTS, **(self.ace_config or {})}
        self.ace_config = cfg

        self._lock = threading.Lock()
        self._data_processor = GAIADataProcessor()

        self._ace_dir = os.path.join(self.metadata.eval_output_dir, "ace")
        self._log_dir = os.path.join(self._ace_dir, "llm_calls")
        self._playbook_dir = os.path.join(self._ace_dir, "playbooks")
        self._rounds_dir = os.path.join(self._ace_dir, "rounds")
        self._usage_log_path = os.path.join(self._ace_dir, "bullet_usage.jsonl")
        for path in (self._ace_dir, self._log_dir, self._playbook_dir, self._rounds_dir):
            os.makedirs(path, exist_ok=True)

        client = ACEClient.from_sdk_llm(self.metadata.llm)
        max_tokens = cfg["max_tokens"]
        self._reflector = Reflector(client, None, client.model, max_tokens=max_tokens)
        self._curator = Curator(client, None, client.model, max_tokens=max_tokens)
        # Instantiated only for its bullet-ID extraction; no LLM calls are made
        # through it, since the OpenHands agent replaces ACE's Generator.
        self._bullet_extractor = Generator(None, None, client.model)

        if cfg["use_bulletpoint_analyzer"]:
            from .vendor.ace.core.bulletpoint_analyzer import BulletpointAnalyzer

            self._analyzer = BulletpointAnalyzer(client, client.model, max_tokens)

        # Resume: pick up an existing playbook rather than starting empty, so a
        # restarted run does not relearn from scratch against a partially
        # completed output.jsonl.
        latest = os.path.join(self._playbook_dir, "playbook_latest.md")
        if os.path.exists(latest):
            with open(latest, "r", encoding="utf-8") as f:
                self._playbook = f.read()
            logger.info(
                "ACE: resumed playbook from %s (%d bullets)",
                latest,
                get_playbook_stats(self._playbook).get("total_bullets", 0),
            )

    def prepare_instances(self) -> list[EvalInstance]:
        instances = super().prepare_instances()
        self._total_samples = len(instances)
        return instances

    # -------------------------------------------------------------- injection

    def _build_agent(self, instance: EvalInstance):
        """Attach the playbook (and any reflection) as the agent's context.

        ``system_message_suffix`` feeds ``Agent.dynamic_context``, which the SDK
        emits as a second, uncached system content block — so per-task content
        here does not disturb cross-conversation prompt caching of the static
        system prompt.
        """
        agent = super()._build_agent(instance)
        playbook, reflection = self._injection
        suffix = self._render_suffix(playbook, reflection)
        if not suffix:
            return agent
        try:
            return agent.model_copy(
                update={"agent_context": AgentContext(system_message_suffix=suffix)}
            )
        except Exception as e:  # noqa: BLE001
            # ACP agents do not carry an AgentContext. Failing loudly here would
            # be worse than running without the playbook, but a silent no-op
            # would invalidate the experiment, so it is an error-level log.
            logger.error("ACE: could not attach playbook to agent: %s", e)
            return agent

    @staticmethod
    def _render_suffix(playbook: str, reflection: str) -> str:
        parts = [PLAYBOOK_PREAMBLE, "", playbook.strip()]
        if reflection and reflection != "(empty)":
            parts += ["", REFLECTION_PREAMBLE, "", reflection.strip()]
        return "\n".join(parts)

    def _build_instruction(self, instance: EvalInstance) -> str:
        """Restate the bullet-usage requirement at the end of the instruction.

        ACE's Generator reports the bullets it used as a required JSON output
        field, so compliance is schema-enforced. That contract cannot be reused
        here because GAIA scoring depends on the <solution> tag, which leaves
        only a soft request — and the smoke run showed a soft request at the tail
        of GAIA's instruction block being dropped exactly when the playbook was
        non-empty.

        So it is stated twice: in full next to the playbook (where ACE puts it,
        in ``GENERATOR_PROMPT``) and as this one-line reminder immediately after
        the answer-format rules, which are the last thing the agent reads.
        """
        instruction = super()._build_instruction(instance)
        instruction += (
            "IMPORTANT: Your final message MUST end with the <bullets_used> tag "
            "described in your system context, listing the playbook bullet_ids you "
            "used — or <bullets_used></bullets_used> if you used none. Include it on "
            "every task. It is not scored and does not affect your answer.\n"
            "For example: <solution> 42 </solution> <bullets_used>ctx-00003, err-00007"
            "</bullets_used>\n"
        )
        return instruction

    def _extract_bullet_ids(self, raw_answer: str) -> tuple[list[str], bool]:
        """Parse the bullet-usage report.

        Returns ``(bullet_ids, reported)``. The second value distinguishes "the
        agent said it used nothing" (empty tag) from "the agent never answered
        the question" (no tag). Both yield an empty list and identical downstream
        behaviour, so without this flag a silent compliance failure is
        indistinguishable from an honest empty citation — which is exactly how
        the first smoke run looked healthy while the attribution layer was dead.

        ACE's own extractor tolerates non-JSON replies by regex-scanning for
        ``[abc-00001]``-shaped IDs, so a missing tag still degrades to that
        rather than to a hard failure.
        """
        if not raw_answer:
            return [], False
        section = raw_answer
        match = re.search(r"<bullets_used>(.*?)</bullets_used>", raw_answer, re.DOTALL)
        if match:
            section = match.group(1)
        ids = self._bullet_extractor._extract_bullet_ids_regex(section)
        if not ids and match:
            # Tag present but IDs unbracketed, e.g. "ctx-00003, err-00007".
            ids = re.findall(r"\b([a-z]{3,}-\d{5})\b", section)
        # Keep only IDs that exist in the playbook the agent was shown.
        known = [bid for bid in dict.fromkeys(ids) if f"[{bid}]" in self._playbook]
        return known, match is not None

    # ------------------------------------------------------------ the loop

    def evaluate_instance(
        self, instance: EvalInstance, workspace
    ) -> EvalOutput:
        """One ACE training step, wrapped around one GAIA instance."""
        with self._lock:
            self._step += 1
            step = self._step

        question = instance.data.get("Question", "")
        target = instance.data.get("Final answer", "")
        context = self._question_context(instance)
        cfg = self.ace_config
        use_gt = not cfg["no_ground_truth"]
        use_json_mode = cfg["json_mode"]

        logger.info(
            "ACE step %d/%d: instance=%s, playbook bullets=%d",
            step,
            self._total_samples,
            instance.id,
            get_playbook_stats(self._playbook).get("total_bullets", 0),
        )

        playbook_at_start = self._playbook

        # ---- STEP 1: initial generation (pre-train) -----------------------
        self._injection = (playbook_at_start, "(empty)")
        out = super().evaluate_instance(instance, workspace)

        # ---- E: infrastructure failures are not ACE samples ---------------
        if out.error:
            logger.warning(
                "ACE: instance %s errored (%s); skipping reflection and curation",
                instance.id,
                out.error,
            )
            out.test_result["ace_skipped"] = "instance_error"
            return out

        pre_train_answer = out.test_result.get("model_answer", "")
        pre_train_correct = bool(out.test_result.get("score", False))
        bullet_ids, bullets_reported = self._extract_bullet_ids(
            out.test_result.get("model_answer_raw", "")
        )
        if not bullets_reported:
            # Not fatal — ACE treats an empty citation as valid — but it means
            # no bullet can be tagged this step, so the helpful/harmful counters
            # stay frozen. Logged loudly so a run-wide compliance failure is
            # visible while the run is still cheap to abort.
            logger.warning(
                "ACE: instance %s omitted the <bullets_used> tag; no bullets can "
                "be tagged this step (playbook had %d bullets)",
                instance.id,
                get_playbook_stats(playbook_at_start).get("total_bullets", 0),
            )
        self._save_round(instance.id, 0, out, bullet_ids, bullets_reported)

        final_answer = pre_train_answer
        is_correct = pre_train_correct
        reflection_content = "(empty)"
        rounds_run = 0

        # ---- STEP 2: reflection rounds ------------------------------------
        if not is_correct:
            for round_num in range(cfg["max_num_rounds"]):
                rounds_run = round_num + 1
                logger.info(
                    "ACE: reflection round %d/%d for %s",
                    rounds_run,
                    cfg["max_num_rounds"],
                    instance.id,
                )

                playbook_bullets = extract_playbook_bullets(self._playbook, bullet_ids)

                reflection_content, bullet_tags, _ = self._reflector.reflect(
                    question=question,
                    reasoning_trace=compact_trace(out.history),
                    predicted_answer=final_answer,
                    ground_truth=target if use_gt else None,
                    environment_feedback="Predicted answer does not match ground truth",
                    bullets_used=playbook_bullets,
                    use_ground_truth=use_gt,
                    use_json_mode=use_json_mode,
                    call_id=f"{instance.id}_step{step}_round{round_num}",
                    log_dir=self._log_dir,
                )

                if bullet_tags:
                    self._playbook = update_bullet_counts(self._playbook, bullet_tags)

                # Regenerate with the reflection in context. The workspace is
                # reused across rounds: the harness owns its lifecycle and a
                # fresh GAIA workspace costs minutes of image and file setup.
                # The conversation is new each round, so the agent's context is
                # clean; only files written by an earlier round persist.
                self._injection = (self._playbook, reflection_content)
                out = super().evaluate_instance(instance, workspace)

                if out.error:
                    logger.warning(
                        "ACE: regeneration round %d for %s errored; stopping rounds",
                        rounds_run,
                        instance.id,
                    )
                    break

                final_answer = out.test_result.get("model_answer", "")
                bullet_ids, bullets_reported = self._extract_bullet_ids(
                    out.test_result.get("model_answer_raw", "")
                )
                self._save_round(
                    instance.id, rounds_run, out, bullet_ids, bullets_reported
                )

                if self._data_processor.answer_is_correct(final_answer, target):
                    is_correct = True
                    break
        else:
            # ---- STEP 3: reflect on a correct answer, for tagging only ----
            playbook_bullets = extract_playbook_bullets(self._playbook, bullet_ids)
            reflection_content, bullet_tags, _ = self._reflector.reflect(
                question=question,
                reasoning_trace=compact_trace(out.history),
                predicted_answer=final_answer,
                ground_truth=target if use_gt else None,
                environment_feedback="Predicted answer matches ground truth",
                bullets_used=playbook_bullets,
                use_ground_truth=use_gt,
                use_json_mode=use_json_mode,
                call_id=f"{instance.id}_step{step}_reflect_on_correct",
                log_dir=self._log_dir,
            )
            if bullet_tags:
                self._playbook = update_bullet_counts(self._playbook, bullet_tags)

        # ---- STEP 4: curate ------------------------------------------------
        operations: list[dict] = []
        if reflection_content != "(empty)" and step % cfg["curator_frequency"] == 0:
            try:
                self._playbook, _, operations, _ = self._curator.curate(
                    current_playbook=self._playbook,
                    recent_reflection=reflection_content,
                    question_context=context,
                    current_step=step,
                    total_samples=self._total_samples or step,
                    token_budget=cfg["playbook_token_budget"],
                    playbook_stats=get_playbook_stats(self._playbook),
                    use_ground_truth=use_gt,
                    use_json_mode=use_json_mode,
                    call_id=f"{instance.id}_step{step}_curate",
                    log_dir=self._log_dir,
                    next_global_id=get_next_global_id(self._playbook),
                )
            except Exception as e:  # noqa: BLE001
                # A curator failure must not take down the run; the playbook is
                # simply left unchanged for this step.
                logger.error("ACE: curator failed at step %d: %s", step, e)

        # ---- STEP 5: optional dedup ---------------------------------------
        if self._analyzer is not None:
            try:
                self._playbook = self._analyzer.analyze(
                    self._playbook,
                    threshold=cfg["bulletpoint_analyzer_threshold"],
                    merge=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("ACE: bulletpoint analyzer failed at step %d: %s", step, e)

        # ---- persist -------------------------------------------------------
        self._save_playbook(step, instance.id)
        try:
            log_bullet_usage(
                self._usage_log_path,
                1,
                step,
                {"question": question, "context": context},
                bullet_ids,
                playbook=playbook_at_start,
                reflection_content=reflection_content,
                is_correct=pre_train_correct,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("ACE: failed to log bullet usage at step %d: %s", step, e)

        # ---- report --------------------------------------------------------
        # The pre-train EvalOutput is returned so that ``score`` and ``history``
        # describe the same attempt: the one made before ground truth was
        # consulted for this instance.
        return self._finalize(
            step=step,
            pre_train_answer=pre_train_answer,
            pre_train_correct=pre_train_correct,
            post_train_answer=final_answer,
            post_train_correct=is_correct,
            rounds_run=rounds_run,
            bullet_ids=bullet_ids,
            bullets_reported=bullets_reported,
            operations=operations,
            playbook_at_start=playbook_at_start,
        )

    # ----------------------------------------------------------- reporting

    def _finalize(self, *, step, pre_train_answer, pre_train_correct,
                  post_train_answer, post_train_correct, rounds_run, bullet_ids,
                  bullets_reported, operations, playbook_at_start) -> EvalOutput:
        """Rebuild the round-0 output and annotate it with ACE bookkeeping."""
        out = self._round0_output
        out.test_result.update(
            {
                "score": pre_train_correct,
                "model_answer": pre_train_answer,
                "ace_step": step,
                "ace_pre_train_answer": pre_train_answer,
                "ace_pre_train_correct": pre_train_correct,
                "ace_post_train_answer": post_train_answer,
                "ace_post_train_correct": post_train_correct,
                "ace_reflection_rounds": rounds_run,
                "ace_bullet_ids_used": bullet_ids,
                # False means the agent never emitted the tag, so an empty
                # bullet list says nothing about what it actually used.
                "ace_bullets_reported": bullets_reported,
                "ace_bullets_in_playbook": get_playbook_stats(playbook_at_start).get(
                    "total_bullets", 0
                ),
                "ace_curator_operations": len(operations),
            }
        )
        return out

    def _save_round(
        self,
        instance_id: str,
        round_num: int,
        out: EvalOutput,
        bullet_ids: list[str],
        bullets_reported: bool = False,
    ) -> None:
        """Persist each round's trace and answer.

        The harness's event-persistence callback is keyed by (run, instance,
        attempt), so reflection rounds would otherwise overwrite each other and
        only the last one would survive on disk.
        """
        if round_num == 0:
            self._round0_output = out
        path = os.path.join(self._rounds_dir, f"{instance_id}_round{round_num}.json")
        payload = {
            "instance_id": instance_id,
            "round": round_num,
            "model_answer": out.test_result.get("model_answer"),
            "model_answer_raw": out.test_result.get("model_answer_raw"),
            "score": out.test_result.get("score"),
            "bullet_ids_used": bullet_ids,
            "bullets_reported": bullets_reported,
            "trace": compact_trace(out.history),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.error("ACE: failed to save round %d for %s: %s", round_num, instance_id, e)

    def _save_playbook(self, step: int, instance_id: str) -> None:
        snapshot = os.path.join(
            self._playbook_dir, f"playbook_step{step:04d}_{instance_id}.md"
        )
        latest = os.path.join(self._playbook_dir, "playbook_latest.md")
        for path in (snapshot, latest):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._playbook)
            except Exception as e:  # noqa: BLE001
                logger.error("ACE: failed to save playbook to %s: %s", path, e)

    @staticmethod
    def _question_context(instance: EvalInstance) -> str:
        """The ``context`` slot of ACE's prompts: GAIA's attachment information."""
        question = instance.data.get("Question", "")
        file_name = instance.data.get("file_name", "")
        attachment = f"Attached file: {file_name}" if file_name else "No attached file."
        return f"Question: {question}\n\n{attachment}"
