"""Tests for the BrowseComp adapter.

Offline tests (always run): XOR decrypt round-trip, grader-verdict parsing,
registry wiring, and the no-key precheck (the 'infra missing -> clear guidance'
path). A live end-to-end run needs a key + network and is done manually.
"""

from __future__ import annotations

import base64

import pytest

from harness import registry
from harness.benchmarks import browsecomp as bc


def _encrypt(text: str, password: str) -> str:
    key = bc._derive_key(password, len(text.encode()))
    return base64.b64encode(
        bytes(a ^ b for a, b in zip(text.encode(), key))).decode()


def test_decrypt_roundtrip():
    for text, pw in [("Paris", "canary1"), ("42.0 kg", "xyz"),
                     ("a longer answer with spaces", "pw")]:
        assert bc.decrypt(_encrypt(text, pw), pw) == text


def test_parse_verdict():
    assert bc._parse_verdict("reasoning: ...\ncorrect: yes\nconfidence: 90") == "yes"
    assert bc._parse_verdict("correct: no") == "no"
    assert bc._parse_verdict("no verdict here") == "no"   # default


def test_registered_as_real_benchmark():
    registry.load_builtins()
    b = registry.get_benchmark("browsecomp")
    assert isinstance(b, bc.BrowseComp)
    # tools the Env would expose (constructed without network/key)
    from harness.schema import Task
    env = bc.BrowseCompEnv(Task(id="x", benchmark="browsecomp", prompt="q"),
                           "claude-sonnet-4-6", 3000)
    assert {t.name for t in env.tools()} == {"web_search", "web_fetch"}


def test_setup_precheck_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from harness.schema import Task
    task = Task(id="x", benchmark="browsecomp", prompt="q",
                metadata={"problem": "p", "answer": "a"})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        bc.BrowseComp().setup(task)
