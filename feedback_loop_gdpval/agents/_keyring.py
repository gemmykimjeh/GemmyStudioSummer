"""_keyring — round-robin + cooldown rotation over several Gemini free-tier keys.

Free-tier Gemini enforces both RPM (requests/min) and RPD (requests/day) caps.
With 3-4 keys we can keep a run going: when a key returns 429 (rate limit) or
RESOURCE_EXHAUSTED, we park it on a cooldown and move to the next live key. When
ALL keys are cooling down we sleep until the soonest one is ready. The harness's
own JSONL --resume handles the day-boundary case (all keys hit RPD): the run
stops cleanly and picks up next day with the playbook intact.

Keys are read (in priority order) from:
  1. GEMINI_API_KEYS   = "key1,key2,key3"   (comma/space/newline separated)
  2. GEMINI_API_KEY_1 .. GEMINI_API_KEY_9
  3. GEMINI_API_KEY / GOOGLE_API_KEY        (single-key fallback)

This wraps the OpenAI-compatible Gemini endpoint (same base_url ACE's utils.py
uses), so it is a drop-in for the generator/reflector/grader clients.
"""

from __future__ import annotations

import os
import re
import threading
import time

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# How long to park a key after it reports rate-limit exhaustion, in seconds.
# RPM windows are 60s; we wait a little longer to be safe. RPD exhaustion will
# just keep re-parking the key until every key is down, then the run pauses.
_COOLDOWN_S = float(os.environ.get("GEMINI_KEY_COOLDOWN", "65"))


def load_keys() -> list[str]:
    """Collect all configured Gemini keys, de-duplicated, in priority order."""
    keys: list[str] = []

    blob = os.environ.get("GEMINI_API_KEYS", "")
    if blob:
        keys += [k.strip() for k in re.split(r"[,\s]+", blob) if k.strip()]

    for i in range(1, 10):
        k = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)

    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        k = os.environ.get(name, "").strip()
        if k:
            keys.append(k)

    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    if not ordered:
        raise RuntimeError(
            "No Gemini keys found. Set GEMINI_API_KEYS='k1,k2,k3' (recommended) "
            "or GEMINI_API_KEY_1.. or GEMINI_API_KEY.")
    return ordered


def _is_rate_limit(exc: Exception) -> bool:
    s = f"{type(exc).__name__} {exc}".lower()
    return (
        "429" in s
        or "rate limit" in s
        or "resource_exhausted" in s
        or "resource exhausted" in s
        or "quota" in s
        or "too many requests" in s
    )


class RotatingGeminiClient:
    """OpenAI-SDK-shaped client that rotates keys on rate-limit errors.

    Exposes `.chat.completions.create(**kwargs)` so it is a drop-in for the
    `openai.OpenAI` clients that ACE's `initialize_clients` returns, and a
    minimal Anthropic-shaped `.messages.create(...)` path is provided by the
    RGR agent's grader instead (the grader stays on the LiteLLM proxy).
    """

    def __init__(self, model_hint: str | None = None, max_retries_cycles: int = 3):
        import openai  # lazy

        self._openai = openai
        self._keys = load_keys()
        self._clients = [openai.OpenAI(api_key=k, base_url=_BASE_URL) for k in self._keys]
        self._ready_at = [0.0] * len(self._keys)   # epoch seconds a key becomes usable
        self._idx = 0
        self._lock = threading.Lock()
        self._max_cycles = max_retries_cycles
        self.chat = _ChatNamespace(self)
        print(f"[keyring] loaded {len(self._keys)} Gemini key(s)")

    # -- key scheduling ----------------------------------------------------
    def _next_live_client(self):
        """Return (index, client), sleeping if every key is cooling down."""
        with self._lock:
            n = len(self._clients)
            now = time.time()
            # try each key once starting from round-robin cursor
            for off in range(n):
                i = (self._idx + off) % n
                if self._ready_at[i] <= now:
                    self._idx = (i + 1) % n
                    return i, self._clients[i]
            # all parked -> wait for the soonest
            wake = min(self._ready_at)
            delay = max(0.0, wake - now)
        if delay > 0:
            print(f"[keyring] all {len(self._clients)} keys cooling down; "
                  f"sleeping {delay:.0f}s")
            time.sleep(delay + 0.5)
        return self._next_live_client()

    def _park(self, i: int) -> None:
        with self._lock:
            self._ready_at[i] = time.time() + _COOLDOWN_S
        print(f"[keyring] key #{i + 1} rate-limited; parked for {_COOLDOWN_S:.0f}s")

    # -- the actual call ---------------------------------------------------
    def _create(self, **kwargs):
        last_exc: Exception | None = None
        attempts = self._max_cycles * len(self._clients)
        for _ in range(attempts):
            i, client = self._next_live_client()
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_rate_limit(exc):
                    self._park(i)
                    continue
                raise  # non-rate-limit error: surface it
        raise RuntimeError(
            f"[keyring] exhausted all keys after {attempts} attempts; "
            f"last error: {last_exc}")

    # -- embeddings (semantic dedup) --------------------------------------
    def embed(self, text: str, model: str | None = None) -> list[float] | None:
        """Return an embedding vector for `text`, rotating keys on rate limits.

        Uses Gemini's OpenAI-compatible embeddings endpoint. Returns None on any
        non-rate-limit failure so callers can fall back to a cheaper heuristic.
        A non-rate-limit failure (e.g. the model is unsupported here) latches
        embeddings OFF for the rest of the run, so we degrade to the caller's
        fallback once instead of spamming the same 404 on every bullet.
        """
        if getattr(self, "_embed_disabled", False):
            return None
        model = model or os.environ.get("RGR_EMBED_MODEL", "gemini-embedding-001")
        last_exc: Exception | None = None
        attempts = self._max_cycles * len(self._clients)
        for _ in range(attempts):
            i, client = self._next_live_client()
            try:
                r = client.embeddings.create(model=model, input=text)
                return list(r.data[0].embedding)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_rate_limit(exc):
                    self._park(i)
                    continue
                self._embed_disabled = True
                print(f"[keyring] embed disabled (non-rate-limit error on "
                      f"{model!r}): {exc}; falling back to Jaccard dedup")
                return None
        print(f"[keyring] embed exhausted all keys; last error: {last_exc}")
        return None


class _ChatNamespace:
    def __init__(self, parent: RotatingGeminiClient):
        self.completions = _CompletionsNamespace(parent)


class _CompletionsNamespace:
    def __init__(self, parent: RotatingGeminiClient):
        self._parent = parent

    def create(self, **kwargs):
        return self._parent._create(**kwargs)
