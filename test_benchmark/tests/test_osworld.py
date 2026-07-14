"""Tests for the OSWorld-Verified adapter.

Offline (always run): provider prechecks (attach/vmware/aws/docker), endpoint
parsing, registry wiring, and — when the repo is present — task loading. A real
scored run needs a provisioned provider VM (docker/vmware/aws) plus OSWorld's
heavy deps, so it is exercised manually.
"""

from __future__ import annotations

import pytest

from harness import registry
from harness.benchmarks import osworld as ow
from harness.benchmarks._stub import StubBenchmark
from harness.schema import Task


def _task() -> Task:
    return Task(id="x", benchmark="osworld", prompt="p", metadata={"task_config": {}})


def test_registered_as_real_benchmark():
    registry.load_builtins()
    b = registry.get_benchmark("osworld")
    assert isinstance(b, ow.OSWorld)
    assert not isinstance(b, StubBenchmark)


def test_attach_requires_endpoint():
    b = ow.OSWorld(provider="attach", env_endpoint=None)
    with pytest.raises(RuntimeError, match="env-endpoint"):
        b.setup(_task())


def test_vmware_precheck_missing_vmrun(monkeypatch):
    monkeypatch.setattr(ow.shutil, "which", lambda exe: None)
    with pytest.raises(RuntimeError, match="vmrun"):
        ow.OSWorld(provider="vmware").setup(_task())


def test_aws_precheck_missing_env(monkeypatch):
    for v in ow.OSWorld._AWS_ENV:
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError, match="aws"):
        ow.OSWorld(provider="aws").setup(_task())


def test_docker_precheck_daemon_down(monkeypatch):
    monkeypatch.setattr(ow, "_docker_ok", lambda: False)
    with pytest.raises(RuntimeError, match="Docker daemon"):
        ow.OSWorld(provider="docker").setup(_task())


def test_docker_ok_then_deps_missing(monkeypatch):
    # docker daemon fine, but OSWorld's heavy deps aren't installed here ->
    # the next gate gives the precise install guidance instead of a bogus score.
    monkeypatch.setattr(ow, "_docker_ok", lambda: True)
    with pytest.raises(RuntimeError, match="desktop_env"):
        ow.OSWorld(provider="docker").setup(_task())


@pytest.mark.parametrize("ep,host,port", [
    ("192.168.1.5", "192.168.1.5", 5000),
    ("192.168.1.5:5000", "192.168.1.5", 5000),
    ("10.0.0.2:9999", "10.0.0.2", 9999),
    ("http://myhost:5000/", "myhost", 5000),
])
def test_parse_endpoint(ep, host, port):
    assert ow.OSWorld(provider="attach", env_endpoint=ep)._parse_endpoint() == (host, port)


def test_probe_unreachable_is_false():
    assert ow._probe("127.0.0.1", 1, timeout=1.0) is False


def test_load_tasks_reads_verified_set():
    try:
        tasks = ow.OSWorld().load_tasks(limit=5)
    except RuntimeError as exc:  # repo not cloned in this environment
        pytest.skip(f"OSWorld repo not available: {exc}")
    assert 1 <= len(tasks) <= 5
    for t in tasks:
        assert t.benchmark == "osworld"
        assert t.prompt.strip()
        assert t.metadata["task_config"]["evaluator"]  # verified evaluator present
        assert t.metadata["domain"]


def test_nogdrive_meta_is_smaller():
    try:
        allt = ow.OSWorld(meta="test_all.json").load_tasks()
        nog = ow.OSWorld(meta="test_nogdrive.json").load_tasks()
    except RuntimeError as exc:
        pytest.skip(f"OSWorld repo not available: {exc}")
    # nogdrive drops the 8 Google-Drive tasks (369 -> 361)
    assert len(nog) == len(allt) - 8
