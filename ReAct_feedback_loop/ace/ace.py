"""
ACE (Agent-Curator-Environment) System
Main orchestrator class for training and testing with playbook-based learning.

This module coordinates three agents:
- Generator: Produces answers using playbook knowledge
- Reflector: Analyzes outputs and tags bullets
- Curator: Updates the playbook based on feedback
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

from .core import (
    Generator, Reflector, Curator, BulletpointAnalyzer,
    auto_distill, format_window,
)
from playbook_utils import *
from logger import *
from utils import *

# ---------------------------------------------------------------------------
# ABSTRACT window size (tumbling). The abstract playbook is built from CROSS-TASK
# patterns, so abstract curation runs once per k tasks over the k distilled
# episodes (concrete curation stays per-task). Change k HERE only.
# ---------------------------------------------------------------------------
ABSTRACT_WINDOW_K = 5


class ACE:
    """
    Main ACE system orchestrator.
    
    Manages the training loop where:
    1. Generator produces answers using playbook
    2. Reflector analyzes answers and tags bullets
    3. Curator updates playbook based on feedback
    
    """
    
    def __init__(
        self,
        api_provider: str,
        generator_model: str,
        reflector_model: str,
        curator_model: str,
        max_tokens: int = 4096,
        initial_playbook: Optional[str] = None,
        use_bulletpoint_analyzer: bool = False,
        bulletpoint_analyzer_threshold: float = 0.90,
        initial_abstract_playbook: Optional[str] = None,
        initial_concrete_playbook: Optional[str] = None,
        abstract_window_k: int = ABSTRACT_WINDOW_K,
        initial_single_playbook: Optional[str] = None,
        rulebook_path: Optional[str] = None,
        compress_every_k: int = ABSTRACT_WINDOW_K,
    ):
        """
        Initialize the ACE system.

        Args:
            api_provider: API provider for LLM calls
            generator_model: Model name for generator
            reflector_model: Model name for reflector
            curator_model: Model name for curator
            max_tokens: Maximum tokens for LLM calls
            initial_playbook: Initial playbook content (optional; back-compat →
                seeds the CONCRETE playbook when no concrete seed is given)
            use_bulletpoint_analyzer: Whether to use bulletpoint analyzer for deduplication
            bulletpoint_analyzer_threshold: Similarity threshold for bulletpoint analyzer (0-1)
            initial_abstract_playbook: seed content for the ABSTRACT playbook
                (AEL semantic-memory store); empty w/ section headers if None.
            initial_concrete_playbook: seed content for the CONCRETE playbook
                (stock ACE store); empty w/ section headers if None.

        The Generator always reads BOTH playbooks (a labeled two-part view via
        `_merged_playbook`); there is no Thompson selector in this version.
        """
        # Initialize API clients
        generator_client, reflector_client, curator_client = initialize_clients(api_provider)

        # Initialize the three (now four) agents
        self.generator = Generator(generator_client, api_provider, generator_model, max_tokens)
        self.reflector = Reflector(reflector_client, api_provider, reflector_model, max_tokens)
        self.curator = Curator(curator_client, api_provider, curator_model, max_tokens)

        # Initialize bulletpoint analyzer if requested and available
        self.use_bulletpoint_analyzer = use_bulletpoint_analyzer
        self.bulletpoint_analyzer_threshold = bulletpoint_analyzer_threshold
        
        if use_bulletpoint_analyzer:
            self.bulletpoint_analyzer = BulletpointAnalyzer(
                curator_client, 
                curator_model, 
                max_tokens
            )
            print(f"✓ BulletpointAnalyzer initialized (threshold={bulletpoint_analyzer_threshold})")
        else:
            self.bulletpoint_analyzer = None
        
        # Store configuration
        self.generator_client = generator_client
        self.reflector_client = reflector_client
        self.curator_client = curator_client
        self.max_tokens = max_tokens

        # --- Dual playbooks --------------------------------------------------
        # Both stores START FROM a predefined seed playbook (not a freshly
        # generated empty skeleton). Resolution order per store:
        #   explicit arg  >  legacy initial_playbook (concrete only)  >
        #   packaged seed file (ace/seeds/*)  >  empty split skeleton.
        # The packaged seeds carry immutable (protected) starter bullets split
        # into concrete- vs abstract-appropriate sections (feedback_loop.md §9).
        concrete_seed = initial_concrete_playbook or initial_playbook or self._default_concrete_seed()
        self.concrete_pb = concrete_seed
        self.abstract_pb = initial_abstract_playbook or self._default_abstract_seed()

        # Seed bullets are immutable → capture their IDs so the curator's
        # protected-id guard never edits/removes them.
        self.concrete_protected_ids = get_bullet_ids(self.concrete_pb)
        self.abstract_protected_ids = get_bullet_ids(self.abstract_pb)

        # Independent global bullet-ID counters, one per store.
        self.next_global_id = get_next_global_id(self.concrete_pb)
        self.next_global_id_abstract = get_next_global_id(self.abstract_pb)

        # Abstract playbook is built from CROSS-TASK patterns: distill each task
        # into an episodic record, buffer them, and curate the abstract store once
        # per k tasks (tumbling window). Buffer is persisted → survives warm-start
        # (a crash mid-window must NOT restart the count).
        self.abstract_window_k = abstract_window_k
        self._abstract_buffer = []   # list[dict] episodic records for the open window

        # `self.playbook` is kept as a live alias of the concrete store so all
        # existing test/eval/save code paths keep working unchanged.
        self.playbook = self.concrete_pb
        self.best_playbook = self.playbook
        self.best_abstract_playbook = self.abstract_pb

        # --- Single-playbook mode (rulebook + ONE sectioned playbook) -------
        # Canonical feedback_loop v2 path. An IMMUTABLE rulebook (merged former
        # seeds; never added-to / edited / deleted) is shown every task and takes
        # precedence over everything. The learned `single_pb` holds both concrete
        # and abstract bullets, distinguished by the predefined SECTION each is
        # filed under (the section header is the bullet's "tag"). Grown by ONE
        # dual reflector + ONE (section-aware) curator, counted by the grader-
        # aligned weights, pruned by net-harmful, and compressed every k tasks.
        # Coexists with the dual attrs above; a caller picks a path.
        self._rulebook_path = rulebook_path or os.path.join(
            os.path.dirname(__file__), "rulebook.txt")
        self.rulebook = self._read_rulebook()
        self.single_pb = initial_single_playbook or self._empty_single_skeleton()
        self.next_single_id = get_next_global_id(self.single_pb)
        self.compress_every_k = compress_every_k
        self._tasks_since_compress = 0
    
    def _initialize_empty_playbook(self) -> str:
        """Initialize an empty playbook with standard (stock ACE) sections.

        Kept for back-compat. The dual-playbook path uses the split
        concrete/abstract skeletons + packaged seeds below instead.
        """
        return """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""

    # Packaged seed playbooks live next to this module: ace/seeds/*.txt
    _SEED_DIR = os.path.join(os.path.dirname(__file__), "seeds")

    def _read_seed(self, filename: str, fallback: str) -> str:
        """Read a packaged seed playbook; fall back to an empty skeleton."""
        path = os.path.join(self._SEED_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                print(f"✓ Loaded packaged seed playbook: {path}")
                return content
        except OSError as e:
            print(f"Warning: could not read seed {path} ({e}); using empty skeleton")
        return fallback

    def _read_rulebook(self) -> str:
        """Read the IMMUTABLE rulebook (merged former seeds). Never mutated."""
        try:
            with open(self._rulebook_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                print(f"✓ Loaded rulebook: {self._rulebook_path}")
                return content
        except OSError as e:
            print(f"Warning: could not read rulebook {self._rulebook_path} ({e})")
        return "# RULEBOOK (empty)"

    def _empty_single_skeleton(self) -> str:
        """Empty single playbook, pre-seeded with the predefined SECTION taxonomy
        that acts as the bullet 'tags'. Concrete-type sections hold specific,
        situation-tied rules; abstract-type sections hold general principles. The
        curator files each learned bullet under the right predefined section."""
        return (
            "## OUTPUT FORMAT & STRUCTURE RULES\n\n"
            "## TOOL & API USAGE\n\n"
            "## FORMULAS & CALCULATIONS\n\n"
            "## CODE SNIPPETS & TEMPLATES\n\n"
            "## COMMON MISTAKES TO AVOID\n\n"
            "## VERIFICATION CHECKLIST\n\n"
            "## GENERAL PRINCIPLES\n\n"
            "## PROBLEM-SOLVING HEURISTICS\n\n"
            "## TRANSFERABLE STRATEGIES\n\n"
            "## FAILURE PATTERNS & RECOVERY\n\n"
            "## SELF-VERIFICATION HABITS\n\n"
            "## OTHERS\n"
        )

    def _default_concrete_seed(self) -> str:
        """Predefined CONCRETE seed: specific, situation-tied starter rules."""
        return self._read_seed("seed_concrete_playbook.txt",
                               self._empty_concrete_skeleton())

    def _default_abstract_seed(self) -> str:
        """Predefined ABSTRACT seed: general, transferable starter principles."""
        return self._read_seed("seed_abstract_playbook.txt",
                               self._empty_abstract_skeleton())

    def _empty_concrete_skeleton(self) -> str:
        """Concrete-flavored empty skeleton (specific, situation-tied sections)."""
        return """## OUTPUT FORMAT & STRUCTURE RULES

## TOOL & API USAGE

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## VERIFICATION CHECKLIST

## OTHERS"""

    def _empty_abstract_skeleton(self) -> str:
        """Abstract-flavored empty skeleton (general, transferable sections)."""
        return """## GENERAL PRINCIPLES

## PROBLEM-SOLVING HEURISTICS

## TRANSFERABLE STRATEGIES

## FAILURE PATTERNS & RECOVERY

## SELF VERIFICATION HABITS

## OTHERS"""
    
    def _extract_config_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract common configuration parameters.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Dictionary with extracted parameters
        """
        return {
            'num_epochs': config.get('num_epochs', 1),
            'max_num_rounds': config.get('max_num_rounds', 3),
            'curator_frequency': config.get('curator_frequency', 1),
            'eval_steps': config.get('eval_steps', 100),
            'save_steps': config.get('save_steps', 50),
            'token_budget': config.get('playbook_token_budget', 80000),
            'task_name': config.get('task_name', 'default'),
            'use_json_mode': config.get('json_mode', False),
            'no_ground_truth': config.get('no_ground_truth', False),
            'save_dir': config.get('save_dir', './results'),
            'test_workers': config.get('test_workers', 20),
            'use_bulletpoint_analyzer': config.get('use_bulletpoint_analyzer', False),
            'bulletpoint_analyzer_threshold': config.get('bulletpoint_analyzer_threshold', 0.90),
        }
    
    def _setup_paths(self, save_dir: str, task_name: str, mode: str) -> Tuple[str, str]:
        """
        Setup logging paths and directories.
        
        Args:
            save_dir: Base path for saving results
            task_name: task name
            mode: 'offline', 'online', or 'eval_only'
            
        Returns:
            Tuple of (usage_log_path, playbook_dir)
        """
        # Create timestamped run folder
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder = f"ace_run_{timestamp}_{task_name}_{mode}"
        save_path = os.path.join(save_dir, run_folder)
        os.makedirs(save_path, exist_ok=True)
        log_dir = os.path.join(save_path, "detailed_llm_logs")
        os.makedirs(log_dir, exist_ok=True)

        if mode == "eval_only":
            return save_path, log_dir

        usage_log_path = os.path.join(save_path, "bullet_usage_log.jsonl")
        playbook_dir = os.path.join(save_path, "intermediate_playbooks")
        os.makedirs(playbook_dir, exist_ok=True)
        
        return save_path, usage_log_path, playbook_dir, log_dir
    
    def run(
        self,
        mode: str,
        train_samples: Optional[List[Dict[str, Any]]] = None,
        val_samples: Optional[List[Dict[str, Any]]] = None,
        test_samples: Optional[List[Dict[str, Any]]] = None,
        data_processor = None,
        config: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Main entrypoint for running ACE system in different modes.
        
        Args:
            mode: Run mode - 'offline', 'online', or 'eval_only'
            train_samples: Training samples (required for offline mode)
            val_samples: Validation samples (required for offline mode)
            test_samples: Test samples (required for online and eval_only modes)
            data_processor: Data processor instance for the task
            config: Configuration dictionary
            
        Returns:
            Dictionary with results depending on the mode
        """
        # Validate inputs
        if mode not in ['offline', 'online', 'eval_only']:
            raise ValueError(f"Invalid mode: {mode}. Must be 'offline', 'online', or 'eval_only'")
        
        if mode == 'offline' and (train_samples is None or val_samples is None):
            raise ValueError("Offline mode requires train_samples and val_samples")
        
        if mode == 'online' and test_samples is None:
            raise ValueError("Online mode requires test_samples")
        
        if mode == 'eval_only' and test_samples is None:
            raise ValueError("eval_only mode requires test_samples")
        
        # Extract configuration
        config_params = self._extract_config_params(config)
        task_name = config_params['task_name']
        save_dir = config_params['save_dir']
        
        # Setup paths based on mode
        if mode == 'eval_only':
            save_path, log_dir = self._setup_paths(save_dir, task_name, mode)
            usage_log_path = None
            playbook_dir = None
        else:
            save_path, usage_log_path, playbook_dir, log_dir = self._setup_paths(save_dir, task_name, mode)
        
        # Save configuration
        config_path = os.path.join(save_path, "run_config.json")
        with open(config_path, "w") as f:
            json.dump({
                "task_name": task_name,
                "mode": mode,
                "generator_model": self.generator.model,
                "reflector_model": self.reflector.model,
                "curator_model": self.curator.model,
                "config": config,
            }, f, indent=2)
        
        # Print initial banner
        print(f"\n{'='*60}")
        print(f"ACE SYSTEM - {mode.upper().replace('_', ' ')} MODE")
        print(f"{'='*60}")
        print(f"Task: {task_name}")
        if mode == 'offline':
            print(f"Train samples: {len(train_samples)}")
            print(f"Validation samples: {len(val_samples)}")
            if test_samples:
                print(f"Test samples: {len(test_samples)}")
        elif mode == 'online':
            print(f"Test samples (used for training and testing): {len(test_samples)}")
        else:  # eval_only
            print(f"Test samples: {len(test_samples)}")
        print(f"{'='*60}\n")
        
        # Execute based on mode
        results = {}
        
        if mode == 'offline':
            # OFFLINE MODE WORKFLOW
            # 1. Run initial test if test_samples provided
            if test_samples:
                print(f"\n{'='*60}")
                print(f"INITIAL TEST (before training)")
                print(f"{'='*60}\n")
                initial_test_results = self._run_test(
                    test_samples=test_samples,
                    data_processor=data_processor,
                    playbook=self.playbook,
                    config=config,
                    log_dir=log_dir,
                    save_path=save_path,
                    prefix="initial"
                )
                results['initial_test_results'] = initial_test_results
                print(f"Initial Test Accuracy: {initial_test_results['accuracy']:.3f}\n")
            
            # 2. Run offline training
            print(f"\n{'='*60}")
            print(f"STARTING OFFLINE TRAINING")
            print(f"{'='*60}\n")
            training_results = self._offline_train(
                train_samples=train_samples,
                val_samples=val_samples,
                data_processor=data_processor,
                config=config,
                save_path=save_path,
                usage_log_path=usage_log_path,
                playbook_dir=playbook_dir,
                log_dir=log_dir
            )
            results['training_results'] = training_results
            
            # 3. Run final test if test_samples provided
            if test_samples:
                print(f"\n{'='*60}")
                print(f"FINAL TEST (with best playbook)")
                print(f"{'='*60}\n")
                final_test_results = self._run_test(
                    test_samples=test_samples,
                    data_processor=data_processor,
                    playbook=self.best_playbook,
                    config=config,
                    log_dir=log_dir,
                    save_path=save_path,
                    prefix="final"
                )
                results['final_test_results'] = final_test_results
                print(f"Final Test Accuracy: {final_test_results['accuracy']:.3f}\n")
        
        elif mode == 'online':
            # ONLINE MODE WORKFLOW
            # 1. Run initial test
            print(f"\n{'='*60}")
            print(f"INITIAL TEST (before training)")
            print(f"{'='*60}\n")
            initial_test_results = self._run_test(
                test_samples=test_samples,
                data_processor=data_processor,
                playbook=self.playbook,
                config=config,
                log_dir=log_dir,
                save_path=save_path,
                prefix="initial"
            )
            results['initial_test_results'] = initial_test_results
            print(f"Initial Test Accuracy: {initial_test_results['accuracy']:.3f}\n")
            
            # 2. Run online training and testing
            print(f"\n{'='*60}")
            print(f"STARTING ONLINE TRAIN AND TEST")
            print(f"{'='*60}\n")
            online_results = self._online_train_and_test(
                test_samples=test_samples,
                data_processor=data_processor,
                config=config,
                save_path=save_path,
                usage_log_path=usage_log_path,
                playbook_dir=playbook_dir,
                log_dir=log_dir
            )
            results['online_test_results'] = online_results
        
        else:  # eval_only
            # EVAL ONLY MODE WORKFLOW
            print(f"\n{'='*60}")
            print(f"RUNNING TEST")
            print(f"{'='*60}\n")
            test_results = self._run_test(
                test_samples=test_samples,
                data_processor=data_processor,
                playbook=self.playbook,
                config=config,
                log_dir=log_dir,
                save_path=save_path,
                prefix="test"
            )
            results['test_results'] = test_results
        
        # Save consolidated results
        final_results_path = os.path.join(save_path, "final_results.json")
        with open(final_results_path, "w") as f:
            json.dump(results, f, indent=2)
        
        # Print final summary
        print(f"\n{'='*60}")
        print(f"RUN COMPLETE")
        print(f"{'='*60}")
        print(f"Mode: {mode.upper().replace('_', ' ')}")
        if mode == 'offline':
            print(f"Best Validation Accuracy: {results['training_results']['best_validation_accuracy']:.3f}")
            if test_samples:
                print(f"Initial Test Accuracy: {results['initial_test_results']['accuracy']:.3f}")
                print(f"Final Test Accuracy: {results['final_test_results']['accuracy']:.3f}")
        elif mode == 'online':
            print(f"Initial Test Accuracy: {results['initial_test_results']['accuracy']:.3f}")
            print(f"Final Test Accuracy: {results['online_test_results']['accuracy']:.3f}")
        else:  # eval_only
            print(f"Test Accuracy: {results['test_results']['accuracy']:.3f}")
        print(f"Results saved to: {save_path}")
        print(f"{'='*60}\n")
        
        return results
    
    def _run_test(
        self,
        test_samples: List[Dict[str, Any]],
        data_processor,
        playbook: str,
        config: Dict[str, Any],
        log_dir: str,
        save_path: str,
        prefix: str = "test"
    ) -> Dict[str, Any]:
        """
        Run testing
        
        Args:
            test_samples: List of test samples
            data_processor: Data processor instance for the task
            playbook: Playbook to use for testing
            config: Configuration dictionary
            log_dir: Directory for detailed logs
            save_path: Path to save results
            prefix: Prefix for saved files (e.g., 'initial', 'final', 'test')
            
        Returns:
            Dictionary with test results
        """
        config_params = self._extract_config_params(config)
        use_json_mode = config_params['use_json_mode']
        test_workers = config_params['test_workers']
        
        test_results, test_error_log = evaluate_test_set(
            data_processor,
            self.generator,
            playbook,
            test_samples,
            self.max_tokens,
            log_dir,
            max_workers=test_workers,
            use_json_mode=use_json_mode
        )

        # Save test results
        test_results_path = os.path.join(save_path, f"{prefix}_test_results.json")
        with open(test_results_path, "w") as f:
            json.dump({
                "test_results": test_results,
                "error_log": test_error_log,
            }, f, indent=2)
        
        return test_results
    
    def _train_single_sample(
        self,
        task_dict: Dict[str, Any],
        data_processor,
        step_id: str,
        epoch: int,
        step: int,
        usage_log_path: str,
        log_dir: str,
        config_params: Dict[str, Any],
        total_samples: int
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Train on a single sample with reflection and curation.
        
        Args:
            task_dict: Sample dictionary with question, context, target
            data_processor: Data processor for evaluation
            step_id: Identifier string for this step (e.g., "train_e_1_s_10" or "online_train_w_1_s_5")
            epoch: Current epoch number
            step: Current step number
            usage_log_path: Path for bullet usage logging
            log_dir: Path for logging directory
            config_params: Configuration parameters dictionary
            total_samples: Total number of samples in dataset
            
        Returns:
            Tuple of (pre_train_answer, post_train_answer, tracking_dict)
        """
        # Extract configuration
        max_num_rounds = config_params['max_num_rounds']
        curator_frequency = config_params['curator_frequency']
        token_budget = config_params['token_budget']
        use_json_mode = config_params['use_json_mode']
        no_ground_truth = config_params['no_ground_truth']
        # Extract sample data
        question = task_dict.get("question", "")
        context = task_dict.get("context", "")
        target = task_dict.get("target", "")

        # STEP 0: the Generator always reads BOTH playbooks (labeled two-part
        # view). No Thompson selector — both are shown, both learn.
        arm = "both"
        shown_playbook = self._merged_playbook()
        # Rebind the live alias so the existing generation + reflection-round
        # block operates on the shown (merged) view.
        self.playbook = shown_playbook

        # STEP 1: Initial generation (pre-train)
        print("Generating initial answer...")
        gen_response, bullet_ids, call_info = self.generator.generate(
            question=question,
            playbook=self.playbook,
            context=context,
            reflection="(empty)",
            use_json_mode=use_json_mode,
            call_id=f"{step_id}_gen_initial",
            log_dir=log_dir
        )
        
        # Extract answer and check correctness
        final_answer = extract_answer(gen_response)
        is_correct = data_processor.answer_is_correct(final_answer, target)
        pre_train_answer = final_answer

        print(f"Correct: {is_correct}")

        # Log bullet usage
        log_bullet_usage(usage_log_path, epoch, step, task_dict, bullet_ids,
                       playbook=self.playbook, is_correct=is_correct)
        
        # Track pre-train result
        tracking_dict = {
            "pre_train_result": {
                "final_answer": final_answer,
                "is_correct": is_correct,
                "playbook_num_tokens": count_tokens(self.playbook),
                "playbook_length": len(self.playbook)
            }
        }
        
        reflection_content = "(empty)"
        
        # STEP 2: Reflection and regeneration
        if not is_correct:
            # For incorrect answers - iterate reflection rounds
            for round_num in range(max_num_rounds):
                print(f"Reflection round {round_num + 1}/{max_num_rounds}")
                
                # Get bullets for reflector
                playbook_bullets = extract_playbook_bullets(
                    self.playbook, bullet_ids
                )
                
                # Reflect on error
                reflection_content, bullet_tags, _ = self.reflector.reflect(
                    question=question,
                    reasoning_trace=gen_response,
                    predicted_answer=final_answer,
                    ground_truth=target if not no_ground_truth else None,
                    environment_feedback="Predicted answer does not match ground truth",
                    bullets_used=playbook_bullets,
                    use_ground_truth=not no_ground_truth,
                    use_json_mode=use_json_mode,
                    call_id=f"{step_id}_round_{round_num}",
                    log_dir=log_dir
                )
                
                # (Counting is centralized in _dual_learn's merged reflect; the
                # round reflect here only drives regeneration below.)

                # Regenerate with reflection
                gen_response, bullet_ids, _ = self.generator.generate(
                    question=question,
                    playbook=self.playbook,
                    context=context,
                    reflection=reflection_content,
                    use_json_mode=use_json_mode,
                    call_id=f"{step_id}_post_reflect_round_{round_num}",
                    log_dir=log_dir
                )
                
                final_answer = extract_answer(gen_response)
                
                if data_processor.answer_is_correct(final_answer, target):
                    print(f"Corrected after reflection round {round_num + 1}!")
                    is_correct = True
                    break
        
        else:
            # For correct answers - still run reflector to tag helpful bullets
            playbook_bullets = extract_playbook_bullets(
                self.playbook, bullet_ids
            )
            
            reflection_content, bullet_tags, _ = self.reflector.reflect(
                question=question,
                reasoning_trace=gen_response,
                predicted_answer=final_answer,
                ground_truth=target if not no_ground_truth else None,
                environment_feedback="Predicted answer matches ground truth",
                bullets_used=playbook_bullets,
                use_ground_truth=not no_ground_truth,
                use_json_mode=use_json_mode,
                call_id=f"{step_id}_reflect_on_correct",
                log_dir=log_dir
            )
            
            # (Counting is centralized in _dual_learn's merged reflect.)

            # Log with reflection
            log_bullet_usage(usage_log_path, epoch, step, task_dict, bullet_ids,
                           playbook=self.playbook,
                           reflection_content=reflection_content,
                           is_correct=is_correct)

        # STEP 2 + 3: dual-playbook learning (ONE merged reflector call → 2
        # curators), gated by curator_frequency exactly like stock ACE. Both
        # playbooks learn every curator step.
        if step % curator_frequency == 0:
            self._dual_learn(
                question=question,
                context=context,
                gen_response=gen_response,
                final_answer=final_answer,
                is_correct=is_correct,
                target=target,
                bullet_ids=bullet_ids,
                arm=arm,
                step=step,
                step_id=step_id,
                total_samples=total_samples,
                config_params=config_params,
                log_dir=log_dir,
            )

        # STEP 4: Post-curator generation — evaluate against the concrete store
        # (the stock ACE playbook / eval target). Re-point the live alias.
        self.playbook = self.concrete_pb
        gen_response, _, _ = self.generator.generate(
            question=question,
            playbook=self.playbook,
            context=context,
            reflection="(empty)",
            use_json_mode=use_json_mode,
            call_id=f"{step_id}_post_curate",
            log_dir=log_dir
        )
        
        final_answer = extract_answer(gen_response)
        post_train_answer = final_answer
        
        post_train_is_correct = data_processor.answer_is_correct(final_answer, target)
        tracking_dict["post_train_result"] = {
            "final_answer": final_answer,
            "is_correct": post_train_is_correct,
            "playbook_num_tokens": count_tokens(self.playbook),
            "playbook_length": len(self.playbook)
        }
        
        return pre_train_answer, post_train_answer, tracking_dict

    # ======================================================================
    # SINGLE-PLAYBOOK MODE (feedback_loop v2): rulebook + one tagged playbook.
    # The dual methods above are kept but unused by this path.
    # ======================================================================
    def _single_view(self) -> str:
        """Generator view: the learned PLAYBOOK (bullets filed under predefined
        sections) first, then the IMMUTABLE RULEBOOK last (recency) as must-follow
        ground rules. The playbook block is shown only once it holds at least one
        learned bullet (bare section headers are hidden)."""
        parts = []
        pb = (self.single_pb or "").strip()
        has_bullets = any(parse_playbook_line(l) for l in pb.splitlines())
        # Ablation switch (`show_playbook=False`): hide the LEARNED playbook from the
        # generator while everything else — the rulebook, reflect, counting, curation —
        # keeps running. Measures what the learned playbook adds ON TOP of the rulebook.
        if has_bullets and getattr(self, "show_playbook", True):
            parts.append(
                "## LEARNED PLAYBOOK — hints from past tasks, filed under predefined "
                "sections (specific-rule sections vs general-principle sections). "
                "Ignore any that do not fit the current task.\n" + pb)
        parts.append(
            "## RULEBOOK — immutable, must-follow on EVERY task; these take "
            "precedence over everything above.\n" + self.rulebook.strip())
        return "\n\n".join(parts)

    def _compress_playbook(self) -> None:
        """Periodic cross-task compress pass over the single playbook: semantic
        dedup/merge + prune net-harmful. (Concrete->abstract generalize/promote
        is a future extension point.)"""
        before = len(self.single_pb)
        self.single_pb = self._maybe_dedup(self.single_pb)
        self.single_pb, pruned = prune_harmful_bullets(self.single_pb)
        print(f"  [compress] single playbook {before}->{len(self.single_pb)} chars"
              + (f"; pruned {len(pruned)}" if pruned else ""))

    def _single_learn(self, question, context, gen_response, final_answer,
                      is_correct, target, bullet_ids, step, step_id,
                      total_samples, config_params, log_dir,
                      environment_feedback=None, score=None, penalty_weight=0.0,
                      allow_growth=True):
        """One-timescale single-playbook learning for one task:
        ONE dual reflect (concrete + abstract lesson in a single pass) → ONE
        tag-aware curator (ADD tagged / DELETE re-learned disclaimers) with
        grader-aligned counting + net-harmful prune; compress every k tasks.

        ``allow_growth=False`` (v6, used on already-good tasks) keeps the parts
        that make the playbook BETTER — the reflect, its helpful/harmful bullet
        tagging, the grader-aligned counting, and the net-harmful prune — but
        skips the curator, which is the only step that makes the playbook BIGGER.
        A task that already scored well has little to teach; distilling "lessons"
        from it mostly adds bulk and post-hoc rationalisation, while its evidence
        about which existing bullets helped or hurt is still worth collecting."""
        token_budget = config_params['token_budget']
        use_json_mode = config_params['use_json_mode']
        no_ground_truth = config_params['no_ground_truth']

        feedback = environment_feedback or (
            "Predicted answer matches ground truth" if is_correct
            else "Predicted answer does not match ground truth")
        # The immutable RULEBOOK is appended at the END of the reflector and
        # curator inputs too (recency = best recall) so both diagnose/curate
        # against the ground rules — but must never treat them as editable.
        rb_block = ("\n\n## RULEBOOK — immutable ground rules; respect them, and "
                    "NEVER add, edit, delete, or target these ids:\n" + self.rulebook.strip())
        used_bullets = extract_playbook_bullets(self._single_view(), bullet_ids) + rb_block

        # ONE reflect — the dual reflector still "thinks concrete AND abstract",
        # but both lessons feed ONE tagged playbook. Also tags cited bullets.
        reflection, bullet_tags, _ = self.reflector.reflect(
            question=question, reasoning_trace=gen_response,
            predicted_answer=final_answer,
            ground_truth=target if not no_ground_truth else None,
            environment_feedback=feedback, bullets_used=used_bullets,
            use_ground_truth=not no_ground_truth, use_json_mode=use_json_mode,
            call_id=f"{step_id}_reflect_single", log_dir=log_dir, mode="dual")

        # Grader-aligned counting: opinion tags → score-weighted (helpful_w,
        # harmful_w); penalty blames every cited bullet. Legacy ±1 when score None.
        if bullet_tags:
            def _align(tags):
                if score is None:
                    return tags
                r = 2.0 * max(0.0, min(1.0, score)) - 1.0
                out = []
                for t in tags:
                    tag = (t.get('tag') or 'neutral')
                    hw = max(r, 0.0) if tag == 'helpful' else 0.0
                    # A reflector 'harmful' tag reflects the bullet's CAUSAL effect, so it
                    # must count even on a high-scoring task. The score-weighted term
                    # (max(-r,0)) alone zeroes out on passing tasks, letting a net-harmful
                    # bullet accumulate 'helpful' forever (the fmt-00037 disclaimer bug).
                    # Add a score-independent floor so an explicit harmful tag always bites.
                    HARMFUL_FLOOR = 0.25
                    aw = (max(-r, 0.0) + HARMFUL_FLOOR) if tag == 'harmful' else 0.0
                    aw += penalty_weight
                    out.append({**t, 'helpful_w': round(hw, 3), 'harmful_w': round(aw, 3)})
                return out
            self.single_pb = update_bullet_counts(self.single_pb, _align(bullet_tags))

        # ONE tag-aware curator: ADD tagged insights / DELETE re-learned
        # capability-disclaimer bullets; then dedup + prune net-harmful.
        # The curator is the ONLY step that grows the playbook, so it is what
        # allow_growth gates; pruning still runs either way.
        if reflection and reflection.strip():
            if allow_growth:
                stats = get_playbook_stats(self.single_pb)
                self.single_pb, self.next_single_id, _, _ = self.curator.curate(
                    current_playbook=self.single_pb, recent_reflection=reflection,
                    question_context=context + rb_block, current_step=step,
                    total_samples=total_samples, token_budget=token_budget,
                    playbook_stats=stats, use_ground_truth=not no_ground_truth,
                    use_json_mode=use_json_mode, call_id=f"{step_id}_curate_single",
                    log_dir=log_dir, next_global_id=self.next_single_id,
                    mode="single", protected_ids=None)
                self.single_pb = self._maybe_dedup(self.single_pb)
            else:
                print("  [v6] playbook growth skipped (task already scored well); "
                      "counting + prune still applied")
            self.single_pb, pruned = prune_harmful_bullets(self.single_pb)
            if pruned:
                print(f"  [prune] single: removed {len(pruned)} net-harmful bullet(s): {pruned}")
            # v6.1: a bullet telling the writer to consult the RUBRIC can never be
            # acted on — the generator never sees one — yet the reflector kept
            # deriving them because it DOES see the grading feedback. Flat word ban;
            # the reflector and curator prompts are told the rule so they phrase
            # lessons without it (the useful content survives, the crutch does not).
            self.single_pb, dropped = sanitize_playbook(self.single_pb)
            if dropped:
                print(f"  [sanitize] removed {len(dropped)} rubric-referencing bullet(s): {dropped}")

        # Cross-task compress every k tasks.
        self._tasks_since_compress += 1
        if self._tasks_since_compress >= self.compress_every_k:
            self._compress_playbook()
            self._tasks_since_compress = 0

        self.playbook = self.single_pb  # eval-facing alias

    def _merged_playbook(self) -> str:
        """Generator-only view of BOTH playbooks. Learned bullets stay grouped
        under their concrete/abstract banner (different mindsets); ALL seed
        (protected) bullets are hoisted into ONE trailing STANDING RULES block —
        seeds sit closest to the query because recency dominates instruction-
        following. Curator views are unchanged (each gets its raw store string)."""
        return render_for_generator([
            ("########## CONCRETE PLAYBOOK — specific, situation-tied rules. "
             "APPLY DIRECTLY when a rule matches the current situation. ##########",
             self.concrete_pb, self.concrete_protected_ids),
            ("########## ABSTRACT PLAYBOOK — general principles & mindset. "
             "Let these GUIDE your overall approach and judgment (not literal steps). ##########",
             self.abstract_pb, self.abstract_protected_ids),
        ])

    def _save_dual_artifacts(self, save_path: str) -> None:
        """Persist both final playbooks next to the stock concrete artifacts
        (which are still saved as final_playbook.txt)."""
        try:
            with open(os.path.join(save_path, "final_concrete_playbook.txt"), "w") as f:
                f.write(self.concrete_pb)
            with open(os.path.join(save_path, "final_abstract_playbook.txt"), "w") as f:
                f.write(self.abstract_pb)
            with open(os.path.join(save_path, "abstract_window_buffer.json"), "w") as f:
                json.dump(self._abstract_buffer, f, indent=2, ensure_ascii=False)
            print(f"✓ Saved dual playbooks (+ abstract window buffer) to {save_path}")
        except Exception as e:
            print(f"Warning: failed to save dual artifacts: {e}")

    def _maybe_dedup(self, playbook: str) -> str:
        """Run the FAISS bulletpoint de-dup on one playbook if enabled."""
        if self.use_bulletpoint_analyzer and self.bulletpoint_analyzer:
            print(f"  Running BulletpointAnalyzer (threshold={self.bulletpoint_analyzer_threshold})...")
            return self.bulletpoint_analyzer.analyze(
                playbook=playbook,
                threshold=self.bulletpoint_analyzer_threshold,
                merge=True,
            )
        return playbook

    def _dual_learn(
        self,
        question: str,
        context: str,
        gen_response: str,
        final_answer: str,
        is_correct: bool,
        target: str,
        bullet_ids: List[str],
        arm: str,
        step: int,
        step_id: str,
        total_samples: int,
        config_params: Dict[str, Any],
        log_dir: str,
        environment_feedback: Optional[str] = None,
        score: Optional[float] = None,
        penalty_weight: float = 0.0,
    ) -> None:
        """Two-timescale dual-playbook learning for one step.

        PER TASK (concrete, fast): one CONCRETE reflect → concrete Curator; the
            same reflect's bullet_tags drive helpful/harmful counting on both stores.
        PER k TASKS (abstract, slow): each task is distilled (non-LLM) into an
            episodic record buffered in a tumbling window; when the window fills,
            ONE abstract reflect reads the k records for CROSS-TASK patterns →
            abstract Curator, then the buffer is cleared.
        Seed bullets are protected; per-playbook FAISS dedup after each curation.
        """
        token_budget = config_params['token_budget']
        use_json_mode = config_params['use_json_mode']
        no_ground_truth = config_params['no_ground_truth']

        print(f"\n--- Dual learning at step {step} ---")

        # Rich grader feedback (rubric score + missed criteria) when available;
        # else a generic string. Used by both the reflect and the episodic record.
        feedback = environment_feedback or (
            "Predicted answer matches ground truth" if is_correct
            else "Predicted answer does not match ground truth")

        # Generator was shown BOTH playbooks (arm="both"); pull the cited bullets
        # from that merged view for the reflector's context.
        shown_pb = self._merged_playbook() if arm == "both" else (
            self.abstract_pb if arm == "abstract" else self.concrete_pb)
        used_bullets = extract_playbook_bullets(shown_pb, bullet_ids)

        # ---- PER-TASK: CONCRETE reflect → concrete Curator + counting ----
        concrete_reflection, bullet_tags, _ = self.reflector.reflect(
            question=question,
            reasoning_trace=gen_response,
            predicted_answer=final_answer,
            ground_truth=target if not no_ground_truth else None,
            environment_feedback=feedback,
            bullets_used=used_bullets,
            use_ground_truth=not no_ground_truth,
            use_json_mode=use_json_mode,
            call_id=f"{step_id}_reflect_concrete",
            log_dir=log_dir,
            mode="concrete",
        )
        # Grader-aligned counting: convert the reflector's opinion tags into
        # score-weighted (helpful_w, harmful_w) increments so the counts track the
        # ACTUAL rubric score — credit a 'helpful' tag only when the task scored
        # above the midpoint, blame a 'harmful' tag only when it scored below —
        # and blame EVERY cited bullet by `penalty_weight` when the grader docked
        # points via a triggered penalty / required-miss. Legacy ±1 when score is
        # None (other benchmarks / callers that pass no numeric score).
        if bullet_tags:
            def _align(tags):
                if score is None:
                    return tags
                r = 2.0 * max(0.0, min(1.0, score)) - 1.0   # [-1, 1]
                aligned = []
                for t in tags:
                    tag = (t.get('tag') or 'neutral')
                    hw = max(r, 0.0) if tag == 'helpful' else 0.0
                    aw = max(-r, 0.0) if tag == 'harmful' else 0.0
                    aw += penalty_weight
                    aligned.append({**t, 'helpful_w': round(hw, 3),
                                    'harmful_w': round(aw, 3)})
                return aligned
            weighted = _align(bullet_tags)
            self.concrete_pb = update_bullet_counts(self.concrete_pb, weighted)
            self.abstract_pb = update_bullet_counts(self.abstract_pb, weighted)

        if concrete_reflection and concrete_reflection.strip():
            stats = get_playbook_stats(self.concrete_pb)
            self.concrete_pb, self.next_global_id, _, _ = self.curator.curate(
                current_playbook=self.concrete_pb,
                recent_reflection=concrete_reflection,
                question_context=context,
                current_step=step,
                total_samples=total_samples,
                token_budget=token_budget,
                playbook_stats=stats,
                use_ground_truth=not no_ground_truth,
                use_json_mode=use_json_mode,
                call_id=f"{step_id}_curate_concrete",
                log_dir=log_dir,
                next_global_id=self.next_global_id,
                mode="concrete",
                protected_ids=self.concrete_protected_ids,
            )
            self.concrete_pb = self._maybe_dedup(self.concrete_pb)
            # Actuator for the grader-aligned counts: evict learned bullets the
            # score signal has proven net-harmful (seeds are protected).
            self.concrete_pb, _pruned_c = prune_harmful_bullets(
                self.concrete_pb, self.concrete_protected_ids)
            if _pruned_c:
                print(f"  [prune] concrete: removed {len(_pruned_c)} net-harmful "
                      f"learned bullet(s): {_pruned_c}")

        # ---- Distill this task into an episodic record; buffer it ----
        record = auto_distill(gen_response, question, feedback, is_correct)
        self._abstract_buffer.append(record)

        # ---- PER-k: abstract curation over the tumbling window ----
        if len(self._abstract_buffer) >= self.abstract_window_k:
            window_text = format_window(self._abstract_buffer)
            abstract_response = self.reflector.reflect_abstract_window(
                window_text=window_text,
                k=self.abstract_window_k,
                use_json_mode=use_json_mode,
                call_id=f"{step_id}_reflect_abstract_window",
                log_dir=log_dir,
            )
            parsed = extract_json_from_text(abstract_response)
            abstract_ins = (parsed.get("abstract_insights") if isinstance(parsed, dict)
                            else abstract_response) or ""
            if isinstance(abstract_ins, str) and abstract_ins.strip():
                stats = get_playbook_stats(self.abstract_pb)
                self.abstract_pb, self.next_global_id_abstract, _, _ = self.curator.curate(
                    current_playbook=self.abstract_pb,
                    recent_reflection=abstract_ins,
                    question_context=f"cross-task window of {self.abstract_window_k} recent tasks",
                    current_step=step,
                    total_samples=total_samples,
                    token_budget=token_budget,
                    playbook_stats=stats,
                    use_ground_truth=not no_ground_truth,
                    use_json_mode=use_json_mode,
                    call_id=f"{step_id}_curate_abstract",
                    log_dir=log_dir,
                    next_global_id=self.next_global_id_abstract,
                    mode="abstract",
                    protected_ids=self.abstract_protected_ids,
                )
                self.abstract_pb = self._maybe_dedup(self.abstract_pb)
                self.abstract_pb, _pruned_a = prune_harmful_bullets(
                    self.abstract_pb, self.abstract_protected_ids)
                if _pruned_a:
                    print(f"  [prune] abstract: removed {len(_pruned_a)} net-harmful "
                          f"learned bullet(s): {_pruned_a}")
            print(f"  [abstract] window full (k={self.abstract_window_k}) → curated, buffer cleared")
            self._abstract_buffer = []   # tumbling: reset
        else:
            print(f"  [abstract] window {len(self._abstract_buffer)}/{self.abstract_window_k} (no abstract curation yet)")

        # Keep the eval-facing alias pointing at the concrete store.
        self.playbook = self.concrete_pb

    def _offline_train(
        self,
        train_samples: List[Dict[str, Any]],
        val_samples: List[Dict[str, Any]],
        data_processor,
        config: Dict[str, Any],
        save_path: str,
        usage_log_path: str,
        playbook_dir: str,
        log_dir: str
    ) -> Dict[str, Any]:
        """
        Run offline training
        
        Args:
            train_samples: List of training samples
            val_samples: List of validation samples
            data_processor: Data processor instance for the task
            config: Configuration dictionary
            save_path: Path to save results
            usage_log_path: Path for bullet usage logging
            playbook_dir: Directory for intermediate playbooks
            log_dir: Directory for detailed logs
            
        Returns:
            Dictionary with training results
        """
        # Extract configuration using helper
        config_params = self._extract_config_params(config)
        task_name = config_params['task_name']
        num_epochs = config_params['num_epochs']
        eval_steps = config_params['eval_steps']
        save_steps = config_params['save_steps']
        test_workers = config_params['test_workers']
        use_json_mode = config_params['use_json_mode']
        curator_frequency = config_params['curator_frequency']
        
        # Initialize tracking
        results = []
        pre_train_post_train_results = []
        error_logs = []
        best_accuracy = 0.0
        self.best_playbook = self.playbook

        print(f"Total epochs: {num_epochs}")
        print(f"Train samples per epoch: {len(train_samples)}")
        print(f"Val samples: {len(val_samples)}")
        print(f"Curator frequency: every {curator_frequency} steps")
        print(f"Evaluation frequency: every {eval_steps} steps\n")
        
        # Training loop
        for epoch in range(1, num_epochs + 1):
            print(f"\n{'='*60}")
            print(f"EPOCH {epoch}/{num_epochs}")
            print(f"{'='*60}")
            
            epoch_answers_pre_train = []
            epoch_targets_pre_train = []
            epoch_answers_post_train = []
            epoch_targets_post_train = []
            
            for step, task_dict in enumerate(train_samples):
                step += 1
                print(f"\n--- Step {step}/{len(train_samples)} ---")
                
                target = task_dict.get("target", "")
                
                # Use helper method for training single sample
                pre_train_answer, post_train_answer, tracking_dict = self._train_single_sample(
                    task_dict=task_dict,
                    data_processor=data_processor,
                    step_id=f"train_e_{epoch}_s_{step}",
                    epoch=epoch,
                    step=step,
                    usage_log_path=usage_log_path,
                    log_dir=log_dir,
                    config_params=config_params,
                    total_samples=len(train_samples)
                )
                
                # Collect answers for accuracy calculation
                epoch_answers_pre_train.append(pre_train_answer)
                epoch_targets_pre_train.append(target)
                epoch_answers_post_train.append(post_train_answer)
                epoch_targets_post_train.append(target)
                
                # Track pre-train and post-train results
                pre_train_post_train_result = {
                    "epoch": epoch,
                    "step": step,
                    "target": target,
                    **tracking_dict
                }
                pre_train_post_train_results.append(pre_train_post_train_result)
                
                # Save intermediate playbook
                if step % save_steps == 0:
                    intermediate_path = os.path.join(
                        playbook_dir, f"epoch_{epoch}_step_{step}_playbook.txt"
                    )
                    with open(intermediate_path, "w") as f:
                        f.write(self.playbook)
                
                # Periodic evaluation
                if step % eval_steps == 0:
                    print(f"\n{'='*40}")
                    print(f"EVALUATION AT EPOCH {epoch}, STEP {step}")
                    print(f"{'='*40}")
                    
                    # Compute training accuracies
                    pre_train_accuracy = data_processor.evaluate_accuracy(
                        epoch_answers_pre_train, epoch_targets_pre_train
                    )
                    post_train_accuracy = data_processor.evaluate_accuracy(
                        epoch_answers_post_train, epoch_targets_post_train
                    )
                    
                    # Validation evaluation
                    val_results = {}
                    if val_samples:
                        val_results, val_error_log = evaluate_test_set(
                            data_processor, self.generator, self.playbook, 
                            val_samples, self.max_tokens, log_dir, 
                            max_workers=test_workers, use_json_mode=use_json_mode
                        )
                    
                    result = {
                        "epoch": epoch,
                        "step": step,
                        "train_result": {
                            "pre_train_accuracy": pre_train_accuracy,
                            "post_train_accuracy": post_train_accuracy
                        },
                        "val_result": val_results,
                        "playbook_num_tokens": count_tokens(self.playbook),
                        "playbook_length": len(self.playbook),
                        "playbook_stats": get_playbook_stats(self.playbook)
                    }
                    results.append(result)
                    error_logs.append({
                        "epoch": epoch,
                        "step": step,
                        "val_results": val_results,
                        "error_log": val_error_log
                    })

                    # Track best playbook
                    if val_results:
                        acc = val_results["accuracy"]
                        if acc > best_accuracy:
                            best_accuracy = acc
                            self.best_playbook = self.playbook
                            print(f"🎉 New best accuracy: {best_accuracy:.3f}")
                    
                    # Save results
                    results_path = os.path.join(save_path, "train_results.json")
                    with open(results_path, "w") as f:
                        json.dump({
                            "best_accuracy": best_accuracy,
                            "results": results,
                        }, f, indent=2)
                    
                    error_logs_path = os.path.join(save_path, "val_results.json")
                    with open(error_logs_path, "w") as f:
                        json.dump(error_logs, f, indent=2)
            
            # End of epoch - save final playbook
            epoch_playbook_path = os.path.join(
                playbook_dir, f"epoch_{epoch}_final_playbook.txt"
            )
            with open(epoch_playbook_path, "w") as f:
                f.write(self.playbook)

        # Save training results
        results_path = os.path.join(save_path, "train_results.json")
        with open(results_path, "w") as f:
            json.dump({
                "best_accuracy": best_accuracy,
                "results": results,
            }, f, indent=2)
        
        pre_train_post_train_results_path = os.path.join(save_path, "pre_train_post_train_results.json")
        with open(pre_train_post_train_results_path, "w") as f:
            json.dump(pre_train_post_train_results, f, indent=2)
        
        # Save final playbook
        final_playbook_path = os.path.join(save_path, f"final_playbook.txt")
        with open(final_playbook_path, "w") as f:
            f.write(self.playbook)
        
        # Save best playbook
        best_playbook_path = os.path.join(save_path, f"best_playbook.txt")
        with open(best_playbook_path, "w") as f:
            f.write(self.best_playbook)

        # Save both playbooks (concrete + abstract).
        self._save_dual_artifacts(save_path)

        print(f"\n{'='*60}")
        print(f"OFFLINE TRAINING COMPLETE")
        print(f"{'='*60}")
        print(f"Best Validation Accuracy: {best_accuracy:.3f}")
        print(f"{'='*60}\n")

        return {"best_validation_accuracy": best_accuracy}

    
    def test(
        self,
        test_samples: List[Dict[str, Any]],
        data_processor,
        playbook,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run testing with the playbook (backward compatibility wrapper).
        
        Args:
            test_samples: List of test samples
            data_processor: Data processor instance for the task
            playbook: Playbook to be used for generator
            config: Configuration dictionary
            
        Returns:
            Dictionary with test results
        """
        # Temporarily set the playbook
        old_playbook = self.playbook
        self.playbook = playbook
        
        # Use the run method
        results = self.run(
            mode='eval_only',
            test_samples=test_samples,
            data_processor=data_processor,
            config=config
        )
        
        # Restore old playbook
        self.playbook = old_playbook
        
        # Return in the old format for backward compatibility
        return {
            "test_results": results['test_results'],
            "error_log": results.get('test_error_log', {}),
            "playbook": playbook
        }
    
    def _online_train_and_test(
        self,
        test_samples: List[Dict[str, Any]],
        data_processor,
        config: Dict[str, Any],
        save_path: str,
        usage_log_path: str,
        playbook_dir: str,
        log_dir: str
    ) -> Dict[str, Any]:
        """
        Run online training and testing
        
        Args:
            test_samples: List of samples to train and test on
            data_processor: Data processor instance for the task
            config: Configuration dictionary
            save_path: Path to save results
            usage_log_path: Path for bullet usage logging
            playbook_dir: Directory for intermediate playbooks
            log_dir: Directory for detailed logs
            
        Returns:
            Dictionary with training results, test results, and final playbook
        """
        # Extract configuration using helper
        config_params = self._extract_config_params(config)
        num_epochs = config_params['num_epochs']
        
        # Validate configuration
        if num_epochs != 1:
            raise ValueError(f"online_train_and_test requires num_epochs=1, got {num_epochs}")
        
        # Extract additional parameters
        curator_frequency = config_params['curator_frequency']
        task_name = config_params['task_name']
        save_steps = config_params['save_steps']
        use_json_mode = config_params['use_json_mode']
        test_workers = config_params['test_workers']
        online_eval_frequency = config.get('online_eval_frequency', 100)  # Get from config
        
        # Initialize tracking
        train_results = []
        pre_train_post_train_results = []
        
        # Test tracking - accumulate across all windows
        correct_count_sample_based = 0
        correct_count = 0
        total_count = 0
        all_test_errors = []
        window_test_results = []
        print(f"Total samples: {len(test_samples)}")
        print(f"Window size: {online_eval_frequency}")
        print(f"Number of windows: {(len(test_samples) + online_eval_frequency - 1) // online_eval_frequency}")
        print(f"Curator frequency: every {curator_frequency} steps")
        
        # Split samples into windows
        num_windows = (len(test_samples) + online_eval_frequency - 1) // online_eval_frequency
        
        epoch = 1  # Always 1 epoch
        global_step = 0
        
        for window_idx in range(num_windows):
            start_idx = window_idx * online_eval_frequency
            end_idx = min((window_idx + 1) * online_eval_frequency, len(test_samples))
            window_samples = test_samples[start_idx:end_idx]
            
            print(f"\n{'='*60}")
            print(f"WINDOW {window_idx + 1}/{num_windows}")
            print(f"Samples {start_idx} to {end_idx - 1}")
            print(f"{'='*60}")
            
            # =================================================================
            # STEP 1: TEST on window with current playbook (before training)
            # =================================================================
            print(f"\n--- Testing window {window_idx + 1} with current playbook ---")
            
            # Use evaluate_test_set for parallel evaluation
            window_test_results_dict, window_test_error_log = evaluate_test_set(
                data_processor,
                self.generator,
                self.playbook,
                window_samples,
                self.max_tokens,
                log_dir,
                max_workers=test_workers,
                use_json_mode=use_json_mode
            )
            
            # Extract results
            window_accuracy = window_test_results_dict['accuracy']
            window_correct = window_test_results_dict['correct']
            window_total = window_test_results_dict['total']
            correct_count_sample_based += window_correct
            correct_count += window_accuracy * window_total
            total_count += window_total
            
            # Add errors with window and global index information
            for error in window_test_error_log['errors']:
                all_test_errors.append({
                    "window": window_idx + 1,
                    "global_index": start_idx + error['index'],
                    "prediction": error['prediction'],
                    "ground_truth": error['ground_truth']
                })
            
            window_test_results.append({
                "window": window_idx + 1,
                "start_idx": start_idx,
                "end_idx": end_idx,
                "window_accuracy": window_accuracy,
                "window_correct": window_correct,
                "window_total": window_total
            })
            
            # Calculate cumulative test accuracy so far
            cumulative_test_accuracy = correct_count / total_count
            
            print(f"Window {window_idx + 1} test accuracy: {window_accuracy:.3f}")
            print(f"Cumulative test accuracy so far: {cumulative_test_accuracy:.3f} "
                  f"({total_count} samples)")
            
            # =================================================================
            # STEP 2: TRAIN on window (same as offline_train)
            # =================================================================
            print(f"\n--- Training on window {window_idx + 1} ---")
            
            epoch_answers_pre_train = []
            epoch_targets_pre_train = []
            epoch_answers_post_train = []
            epoch_targets_post_train = []
            
            for local_step, task_dict in enumerate(window_samples):
                global_step += 1
                local_step += 1
                
                print(f"\n--- Window {window_idx + 1}, Step {local_step}/{len(window_samples)} "
                      f"(Global step {global_step}) ---")
                
                target = task_dict.get("target", "")
                
                # Use helper method for training single sample
                pre_train_answer, post_train_answer, tracking_dict = self._train_single_sample(
                    task_dict=task_dict,
                    data_processor=data_processor,
                    step_id=f"online_train_s_{global_step}",
                    epoch=epoch,
                    step=global_step,
                    usage_log_path=usage_log_path,
                    log_dir=log_dir,
                    config_params=config_params,
                    total_samples=len(test_samples)
                )
                
                # Collect answers for accuracy calculation
                epoch_answers_pre_train.append(pre_train_answer)
                epoch_targets_pre_train.append(target)
                epoch_answers_post_train.append(post_train_answer)
                epoch_targets_post_train.append(target)
                
                # Track pre-train and post-train results
                pre_train_post_train_result = {
                    "window": window_idx + 1,
                    "global_step": global_step,
                    "target": target,
                    **tracking_dict
                }
                pre_train_post_train_results.append(pre_train_post_train_result)
                
                # Save intermediate playbook
                if global_step % save_steps == 0:
                    intermediate_path = os.path.join(
                        playbook_dir, f"step_{global_step}_playbook.txt"
                    )
                    with open(intermediate_path, "w") as f:
                        f.write(self.playbook)
            
            # End of window - compute training accuracies for this window
            pre_train_accuracy = data_processor.evaluate_accuracy(
                epoch_answers_pre_train, epoch_targets_pre_train
            )
            post_train_accuracy = data_processor.evaluate_accuracy(
                epoch_answers_post_train, epoch_targets_post_train
            )
            
            window_train_result = {
                "window": window_idx + 1,
                "global_step": global_step,
                "train_result": {
                    "pre_train_accuracy": pre_train_accuracy,
                    "post_train_accuracy": post_train_accuracy
                },
                "cumulative_test_accuracy": cumulative_test_accuracy,
                "playbook_num_tokens": count_tokens(self.playbook),
                "playbook_length": len(self.playbook),
                "playbook_stats": get_playbook_stats(self.playbook)
            }
            train_results.append(window_train_result)
            
            print(f"\nWindow {window_idx + 1} training complete:")
            print(f"  Pre-train accuracy: {pre_train_accuracy:.3f}")
            print(f"  Post-train accuracy: {post_train_accuracy:.3f}")
            
            # Save window playbook
            window_playbook_path = os.path.join(
                playbook_dir, f"window_{window_idx + 1}_final_playbook.txt"
            )
            with open(window_playbook_path, "w") as f:
                f.write(self.playbook)
        
        # All windows complete
        print(f"\n{'='*60}")
        print(f"ONLINE TRAIN AND TEST COMPLETE")
        print(f"{'='*60}")
        
        # Calculate final cumulative test accuracy
        assert total_count == len(test_samples)
        final_test_accuracy = correct_count / total_count
        
        test_results = {
            "accuracy": final_test_accuracy,
            "correct": correct_count_sample_based,
            "total": total_count,
            "window_results": window_test_results
        }
        
        test_error_log = {
            "accuracy": final_test_accuracy,
            "errors": all_test_errors
        }

        # Save test results
        test_results_path = os.path.join(save_path, "test_results.json")
        with open(test_results_path, "w") as f:
            json.dump({
                "test_accuracy": final_test_accuracy,
                "test_results": test_results,
                "test_error_log": test_error_log
            }, f, indent=2)
        
        # Save training results (per window)
        train_results_path = os.path.join(save_path, "train_results.json")
        with open(train_results_path, "w") as f:
            json.dump({"train_results": train_results}, f, indent=2)
        
        # Save pre-train/post-train results
        pre_train_post_train_results_path = os.path.join(save_path, "pre_train_post_train_results.json")
        with open(pre_train_post_train_results_path, "w") as f:
            json.dump(pre_train_post_train_results, f, indent=2)
        
        # Save final playbook
        final_playbook_path = os.path.join(save_path, f"final_playbook.txt")
        with open(final_playbook_path, "w") as f:
            f.write(self.playbook)

        # Save both playbooks (concrete + abstract).
        self._save_dual_artifacts(save_path)

        print(f"\n{'='*60}")
        print(f"ONLINE TRAINING AND TESTING COMPLETE")
        print(f"{'='*60}")
        print(f"Final Test Accuracy: {final_test_accuracy:.3f}")
        print(f"{'='*60}\n")
        
        return {
            "accuracy": final_test_accuracy,
            "correct": correct_count_sample_based,
            "total": total_count,
        }