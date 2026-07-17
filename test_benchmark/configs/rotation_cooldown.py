"""LiteLLM custom callback: retire a Gemini key ONLY on a *daily-quota* (RPD) 429.

Gemini free tier throws 429 for three different limits, but litellm treats them all
as one RateLimitError, so its cooldown can't tell them apart:

  * RPD  -> requests-per-DAY exhausted     (hard wall until ~midnight PT; body says
            "GenerateRequestsPerDayPerProjectPerModel-FreeTier" / "...PerDay")
  * RPM  -> requests-per-MINUTE (15/min)    (body says "...PerMinute")
  * TPM  -> tokens-per-MINUTE (250k/min)    (body says "...PerMinute")

Desired behaviour (user): *switch keys only on RPD*. RPM/TPM are transient — the key
recovers within ~60s on its own, so we must NOT permanently abandon it; litellm's short
`cooldown_time` (configured ~65s in litellm_gemini.yaml) rides those out and the key
returns to the pool.

This callback adds the ONE thing litellm can't do natively: on an RPD 429 it marks that
specific key dead for RPD_COOLDOWN_SECONDS (default 4h) in a process-local registry, and
`async_pre_call_check` then skips every deployment using that key — so the router routes
around it for the rest of the day instead of re-probing it every 65s. Keying by the
resolved api_key means both the brain-group and the catch-all deployment for that key are
retired together (same key == same daily quota).

Wire-up (litellm_gemini.yaml):
    litellm_settings:
      callbacks: ["rotation_cooldown.proxy_handler_instance"]
    router_settings:
      enable_pre_call_checks: true      # required for async_pre_call_check to run
"""
from __future__ import annotations

import os
import time

from litellm.integrations.custom_logger import CustomLogger

# How long a DAILY-exhausted key stays retired. Gemini RPD resets at midnight PT; 4h is a
# safe "don't touch it again this session" default (a re-probe after it costs nothing but
# latency, so we don't need to hit the reset boundary exactly). Override via env.
RPD_COOLDOWN = int(os.environ.get("RPD_COOLDOWN_SECONDS", str(4 * 3600)))

# api_key -> epoch time when it may be tried again. Process-local (the proxy is one process).
_dead: dict[str, float] = {}


def _key_of(deployment: dict | None) -> str | None:
    """Resolved api_key for a router deployment dict (already substituted from env)."""
    lp = (deployment or {}).get("litellm_params", {}) or {}
    k = lp.get("api_key")
    return k if isinstance(k, str) and k else None


def _is_rpd(msg: str) -> bool:
    """True iff a 429 body is the DAILY (per-day) quota, not per-minute RPM/TPM."""
    m = (msg or "").lower().replace("_", "").replace("-", "").replace(" ", "")
    if "perminute" in m:                     # RPM or TPM -> transient, NOT rpd
        return False
    return ("perday" in m                    # "...PerDay..." / "requests per day"
            or "generaterequestsperday" in m
            or "requestsperday" in m)


def _short_key(k: str) -> str:
    return f"...{k[-6:]}" if k and len(k) > 6 else "?"


class RpdOnlyRetire(CustomLogger):
    """Only DAILY-quota (RPD) 429s retire a key; RPM/TPM are left to litellm's short cooldown."""

    # ---- pre-call: drop any deployment whose key is RPD-dead -----------------
    async def async_pre_call_check(self, deployment, parent_otel_span=None):  # noqa: ANN001
        try:
            k = _key_of(deployment)
            if k is not None:
                until = _dead.get(k, 0.0)
                if until > time.time():
                    # Raising removes THIS deployment from the candidate set for this request;
                    # the router falls over to a live key. (Router logs it as a skipped deploy.)
                    raise ValueError(
                        f"[rotation] key {_short_key(k)} retired (RPD) for "
                        f"{int(until - time.time())}s more")
        except ValueError:
            raise
        except Exception:  # noqa: BLE001 - never break deployment selection
            return deployment
        return deployment

    # ---- on failure: if it's an RPD 429, mark that key dead ------------------
    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        try:
            exc = kwargs.get("exception")
            msg = f"{exc} {response_obj}"
            status = getattr(exc, "status_code", None)
            if status != 429 and "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
                return
            if not _is_rpd(msg):
                return  # RPM/TPM: transient -> leave key alone, litellm's short cooldown recovers it
            lp = kwargs.get("litellm_params", {}) or {}
            k = lp.get("api_key")
            if isinstance(k, str) and k:
                _dead[k] = time.time() + RPD_COOLDOWN
                # visible in the proxy terminal so the operator sees the retire happen
                print(f">>> [rotation] RPD hit -> retiring key {_short_key(k)} for "
                      f"{RPD_COOLDOWN}s ({sum(1 for v in _dead.values() if v > time.time())} "
                      f"key(s) now retired)", flush=True)
        except Exception:  # noqa: BLE001 - a logging hook must never raise
            pass


proxy_handler_instance = RpdOnlyRetire()

# import-time banner: if this line shows in the proxy log, litellm loaded the callback and
# the "retire only on RPD" policy is active.
print(f">>> [rotation] RPD-only key-retire callback loaded "
      f"(RPD_COOLDOWN={RPD_COOLDOWN}s; RPM/TPM left to litellm short cooldown)", flush=True)
