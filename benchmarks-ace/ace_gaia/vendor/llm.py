"""Transport shim standing in for the reference repo's ``llm.timed_llm_call``.

The reference implementation is bound to a SambaNova client, an API-key mixer
and a 1000-retry policy. Only the transport differs here — the Reflector and
Curator classes, their prompts, and their response handling are untouched.

Two behaviours of the original are preserved deliberately because ACE branches
on them:

* An empty model reply returns the ``INCORRECT_DUE_TO_EMPTY_RESPONSE`` sentinel
  rather than raising. ``Curator.curate`` tests for this prefix and skips the
  step (``vendor/ace/core/curator.py``), and losing that would turn a transient
  empty reply into a crash mid-run.
* The return type stays ``(response_text, call_info)`` and ``call_info`` carries
  ``role`` and ``call_id``, which ``logger.log_llm_call`` formats into filenames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

EMPTY_RESPONSE_SENTINEL = "INCORRECT_DUE_TO_EMPTY_RESPONSE"


@dataclass
class ACEClient:
    """Model/credential holder passed to ACE's Reflector and Curator.

    ACE's constructors take ``(api_client, api_provider, model)``; this fills the
    ``api_client`` slot and carries everything litellm needs.
    """

    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0

    @classmethod
    def from_sdk_llm(cls, llm: Any) -> "ACEClient":
        """Build from the harness's ``LLM`` object (``load_llm_config`` output).

        The Reflector and Curator run at the same temperature as the agent so a
        replayed run reproduces; ``temperature=None`` in the SDK means "provider
        default" (~1.0 for Anthropic), so it is pinned to 0.0 here rather than
        forwarded as None.
        """
        api_key = getattr(llm, "api_key", None)
        # SDK stores secrets as pydantic SecretStr.
        if api_key is not None and hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()
        temperature = getattr(llm, "temperature", None)
        return cls(
            model=llm.model,
            api_key=api_key,
            base_url=getattr(llm, "base_url", None),
            temperature=0.0 if temperature is None else float(temperature),
        )


def timed_llm_call(
    client,
    api_provider,
    model,
    prompt,
    role,
    call_id,
    max_tokens=4096,
    log_dir=None,
    sleep_seconds=15,
    retries_on_timeout=3,
    attempt=1,
    use_json_mode=False,
):
    """Signature-compatible replacement for the reference ``timed_llm_call``.

    ``api_provider`` and ``attempt`` are accepted and ignored; they exist only so
    the vendored ACE call sites need no edits.
    """
    import litellm

    start_time = time.time()
    print(f"[{role.upper()}] Starting call {call_id}...")

    kwargs: dict[str, Any] = {
        "model": model or getattr(client, "model", None),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": getattr(client, "temperature", 0.0),
    }
    if getattr(client, "api_key", None):
        kwargs["api_key"] = client.api_key
    if getattr(client, "base_url", None):
        kwargs["base_url"] = client.base_url
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    text = ""
    usage: dict[str, Any] = {}
    last_error: Exception | None = None

    for retry in range(max(1, retries_on_timeout)):
        try:
            response = litellm.completion(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            raw_usage = getattr(response, "usage", None)
            if raw_usage is not None:
                usage = {
                    "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
                    "completion_tokens": getattr(raw_usage, "completion_tokens", None),
                    "total_tokens": getattr(raw_usage, "total_tokens", None),
                }
            if text:
                break
            # Empty but successful reply — retry once before giving up, matching
            # the reference behaviour of treating empties as transient.
            last_error = None
            print(f"[{role.upper()}] Empty response on attempt {retry + 1}")
        except Exception as e:  # noqa: BLE001 - litellm raises a wide range
            last_error = e
            print(f"[{role.upper()}] Call {call_id} failed ({e.__class__.__name__}): {e}")
        if retry < retries_on_timeout - 1:
            time.sleep(sleep_seconds * (retry + 1))

    duration = time.time() - start_time

    if not text:
        # ACE branches on this prefix rather than on an exception.
        text = EMPTY_RESPONSE_SENTINEL
        print(f"[{role.upper()}] Call {call_id} returned no content after retries")

    call_info = {
        "role": role,
        "call_id": call_id,
        "model": kwargs["model"],
        "duration_seconds": round(duration, 2),
        "prompt_chars": len(prompt),
        "response_chars": len(text),
        "usage": usage,
        "error": repr(last_error) if last_error is not None else None,
        "prompt": prompt,
        "response": text,
    }

    if log_dir:
        try:
            from .logger import log_llm_call

            log_llm_call(log_dir, dict(call_info))
        except Exception as e:  # noqa: BLE001 - logging must never break a run
            print(f"Warning: failed to log LLM call {call_id}: {e}")

    return text, call_info
