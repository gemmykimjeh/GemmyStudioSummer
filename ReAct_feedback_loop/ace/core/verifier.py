"""
Verifier agent for ACE system (4th agent, EDV default-reject auditor).

One LLM call, ideally a DIFFERENT model from the Reflector (EDV finding:
same-model self-verification does not help). Gates each reflection stream before
the Curator commits anything: checks it is true-to-trajectory and level-fit for
its target playbook, and non-redundant. Rejected streams are dropped (no-op);
accepted streams yield a tightened `verified_reflection` handed to the Curator.
"""

import json
from typing import Any, Dict, Optional

from ..prompts.verifier import VERIFIER_PROMPT
from playbook_utils import extract_json_from_text
from llm import timed_llm_call


def _stream_to_text(refl: Any) -> str:
    """Normalize a reflection stream (dict or str) to prompt text."""
    if refl is None:
        return "(no reflection produced)"
    if isinstance(refl, str):
        return refl
    try:
        return json.dumps(refl, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(refl)


class Verifier:
    """Default-reject auditor gating reflection streams before curation."""

    def __init__(self, api_client, api_provider, model: str, max_tokens: int = 4096):
        self.api_client = api_client
        self.api_provider = api_provider
        self.model = model
        self.max_tokens = max_tokens

    def run(
        self,
        refl_concrete: Any,
        refl_abstract: Any,
        trajectory: str,
        abstract_pb: str,
        concrete_pb: str,
        use_json_mode: bool = False,
        call_id: str = "verify",
        log_dir: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Audit both streams.

        Returns:
            {"concrete": {"accepted": bool, "reason": str,
                          "verified_reflection": str},
             "abstract": {...}}
            On any failure the stream defaults to rejected (default-reject).
        """
        default = {
            "concrete": {"accepted": False, "reason": "default-reject", "verified_reflection": ""},
            "abstract": {"accepted": False, "reason": "default-reject", "verified_reflection": ""},
        }

        prompt = VERIFIER_PROMPT.format(
            trajectory=trajectory or "(empty trajectory)",
            refl_concrete=_stream_to_text(refl_concrete),
            refl_abstract=_stream_to_text(refl_abstract),
            concrete_playbook=concrete_pb or "(empty)",
            abstract_playbook=abstract_pb or "(empty)",
        )

        response, _ = timed_llm_call(
            self.api_client,
            self.api_provider,
            self.model,
            prompt,
            role="verifier",
            call_id=call_id,
            max_tokens=self.max_tokens,
            log_dir=log_dir,
            use_json_mode=use_json_mode,
        )

        if response.startswith("INCORRECT_DUE_TO_EMPTY_RESPONSE"):
            print("⏭️  Verifier empty response → default-reject both streams")
            return default

        parsed = extract_json_from_text(response)
        if not isinstance(parsed, dict):
            print("❌ Verifier JSON parse failed → default-reject both streams")
            return default

        result = {}
        for stream in ("concrete", "abstract"):
            node = parsed.get(stream, {})
            if not isinstance(node, dict):
                node = {}
            accepted = bool(node.get("accepted", False))
            verified = node.get("verified_reflection", "") or ""
            # Guard: accepted but empty verified text is treated as a reject.
            if accepted and not verified.strip():
                accepted = False
            result[stream] = {
                "accepted": accepted,
                "reason": node.get("reason", ""),
                "verified_reflection": verified,
            }

        print(f"  Verifier: concrete={'ACCEPT' if result['concrete']['accepted'] else 'reject'}, "
              f"abstract={'ACCEPT' if result['abstract']['accepted'] else 'reject'}")
        return result
