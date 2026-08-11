"""Entry point for a GAIA run with ACE's feedback loop enabled.

Mirrors ``benchmarks.gaia.run_infer.main`` so every existing GAIA flag keeps
working, and adds the ACE knobs. ACE runs in its online mode: one sequential
pass over the selected instances, the playbook accumulating as it goes.

    source .llm_config/tavily.env
    uv run python -m ace_gaia.run_ace \\
        .llm_config/sonnet45.json \\
        --level 2023_all --split validation --workspace docker \\
        --select pilot_ids.txt --num-workers 1 \\
        --output-dir outputs/gaia-ace-pilot
"""

from __future__ import annotations

import json

from benchmarks.gaia.config import INFER_DEFAULTS
from benchmarks.utils.args_parser import get_parser
from benchmarks.utils.critics import create_critic
from benchmarks.utils.evaluation_utils import (
    construct_eval_output_dir,
    generate_error_logs_summary,
    get_default_on_result_writer,
)
from benchmarks.utils.llm_config import load_llm_config
from benchmarks.utils.models import EvalMetadata
from openhands.sdk import get_logger

from .runner import ACE_DEFAULTS, ACEGAIAEvaluation


logger = get_logger(__name__)


def main() -> None:
    parser = get_parser()
    parser.add_argument(
        "--level",
        type=str,
        help="GAIA level to evaluate (e.g., 2023_level1, 2023_level2, 2023_level3, 2023_all)",
    )
    parser.add_argument(
        "--ace-max-num-rounds",
        type=int,
        default=ACE_DEFAULTS["max_num_rounds"],
        help=(
            "ACE reflection rounds after a wrong answer. Each round is a full "
            "re-run of the agent with the reflection injected, stopping early "
            "once the answer is correct. Does not affect the reported score, "
            "which is always the pre-train (round 0) answer. (default: 3)"
        ),
    )
    parser.add_argument(
        "--ace-curator-frequency",
        type=int,
        default=ACE_DEFAULTS["curator_frequency"],
        help="Run the Curator every N instances (default: 1)",
    )
    parser.add_argument(
        "--ace-token-budget",
        type=int,
        default=ACE_DEFAULTS["playbook_token_budget"],
        help="Playbook token budget reported to the Curator (default: 80000)",
    )
    parser.add_argument(
        "--ace-use-bulletpoint-analyzer",
        action="store_true",
        help=(
            "Enable ACE's embedding-based dedup/merge pass over the playbook. "
            "Off by default, matching ACE's own default; needs "
            "sentence-transformers and faiss."
        ),
    )
    parser.add_argument(
        "--ace-analyzer-threshold",
        type=float,
        default=ACE_DEFAULTS["bulletpoint_analyzer_threshold"],
        help="Cosine similarity threshold for the dedup pass (default: 0.90)",
    )
    parser.set_defaults(**INFER_DEFAULTS)
    # GAIA's default is 30 workers. ACE mutates one playbook in place across
    # instances, so the pass has to be sequential.
    parser.set_defaults(num_workers=1)
    # GAIA's default is 3 critic runs. ACE already retries a failed instance
    # through its reflection rounds; nesting the harness's loop around that
    # multiplies agent runs without adding signal. The check below still fires
    # if someone passes a different value explicitly.
    parser.set_defaults(n_critic_runs=1)
    args = parser.parse_args()

    critic = create_critic(args)
    logger.info(f"Using critic: {type(critic).__name__}")

    if args.n_critic_runs < 1:
        raise ValueError(f"n_critic_runs must be >= 1, got {args.n_critic_runs}")
    if args.n_critic_runs != 1:
        # ACE already retries a failed instance through its reflection rounds.
        # Nesting the harness's critic loop around that multiplies agent runs
        # without adding signal, and muddies which attempt produced the score.
        raise ValueError(
            f"ACE runs require --n-critic-runs 1 (got {args.n_critic_runs}); "
            "use --ace-max-num-rounds for ACE's own retry loop."
        )

    llm = load_llm_config(args.llm_config_path)
    logger.info("Using LLM config: %s", llm.model_dump_json(indent=2))
    if llm.temperature is None:
        logger.warning(
            "LLM temperature is unset, which means provider default (~1.0), not 0.0. "
            "Run-to-run variance at that setting is large enough to swamp any ACE effect."
        )

    dataset_description = f"gaia-{args.level}-{args.split}-ace"

    structured_output_dir = construct_eval_output_dir(
        base_dir=args.output_dir,
        dataset_name=dataset_description,
        model_name=llm.model,
        max_iterations=args.max_iterations,
        eval_note=args.note,
    )

    metadata = EvalMetadata(
        llm=llm,
        dataset=args.dataset,
        dataset_split=args.split,
        max_iterations=args.max_iterations,
        eval_output_dir=structured_output_dir,
        details={"level": args.level},
        eval_limit=args.n_limit,
        n_critic_runs=args.n_critic_runs,
        critic=critic,
        selected_instances_file=args.select,
        max_retries=args.max_retries,
        workspace_type=args.workspace,
        enable_delegation=args.enable_delegation,
        agent_type=args.agent_type,
    )

    ace_config = {
        "max_num_rounds": args.ace_max_num_rounds,
        "curator_frequency": args.ace_curator_frequency,
        "playbook_token_budget": args.ace_token_budget,
        "use_bulletpoint_analyzer": args.ace_use_bulletpoint_analyzer,
        "bulletpoint_analyzer_threshold": args.ace_analyzer_threshold,
    }
    logger.info("ACE config: %s", json.dumps(ace_config, indent=2))

    evaluator = ACEGAIAEvaluation(
        metadata=metadata,
        num_workers=args.num_workers or 1,
        ace_config=ace_config,
    )

    evaluator.run(on_result=get_default_on_result_writer(evaluator.output_path))

    generate_error_logs_summary(structured_output_dir)

    logger.info("Evaluation completed!")
    logger.info(f"Results written to: {evaluator.output_path}")
    logger.info(f"Playbook: {structured_output_dir}/ace/playbooks/playbook_latest.md")
    print(json.dumps({"output_json": str(evaluator.output_path)}))


if __name__ == "__main__":
    main()
