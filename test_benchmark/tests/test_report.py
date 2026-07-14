"""Tests for the run-directory report + provenance (all offline)."""

from __future__ import annotations

import json
from pathlib import Path

from harness import registry
from harness import report as rp
from harness.repro import collect_provenance


def _rec(agent: str, bench: str, **kw) -> dict:
    return {"task_id": kw.get("task_id", "t1"), "benchmark": bench, "agent": agent,
            "success": kw.get("success", True), "trial": 0,
            "score": kw.get("score", 1.0), "metrics": {}, "error": kw.get("error"),
            "cost": kw.get("cost", 0.0), "wall_time": 0.0,
            "mock": kw.get("mock", False)}


def _write_run(base: Path, cell: str, records: list[dict], manifest: dict | None = None) -> Path:
    sub = base / cell
    sub.mkdir(parents=True)
    (sub / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8")
    if manifest is not None:
        (base / "manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return base


def test_cell_status(monkeypatch):
    registry.load_builtins()
    assert rp._cell_status("tau_bench", []) == "—"
    assert rp._cell_status("tau_bench", [_rec("scripted", "tau_bench", mock=True)]) == "WIRED-MOCK"
    assert rp._cell_status("tau_bench", [_rec("claude_sdk", "tau_bench", success=True)]) == "WIRED-REAL"
    infra = [_rec("scripted", "osworld", success=False,
                  error="cannot reach the OSWorld guest server")]
    assert rp._cell_status("osworld", infra) == "INFRA-MISSING"
    monkeypatch.setattr(rp, "_is_stub", lambda b: True)
    assert rp._cell_status("tau_bench", []) == "STUB"


def test_md_table_escapes_pipes():
    assert "x\\|y" in rp._md_table(["a"], [["x|y"]])


def test_classify_error():
    assert rp._classify_error("") == "ok"
    assert rp._classify_error("timeout after 30s") == "timeout"
    assert rp._classify_error("Docker daemon not reachable") == "infra"


def test_load_run_and_render_single(tmp_path):
    manifest = {"run_id": "r1", "config": {"model": None},
                "provenance": {"git_note": "not a git repository", "python": "3.12",
                               "platform": "win", "packages": {"anthropic": "0.1"}}}
    d = _write_run(tmp_path / "r1", "tau_bench__scripted",
                   [_rec("scripted", "tau_bench", mock=True)], manifest)
    md = rp.render([d])
    assert "Connection / verification status" in md
    assert "WIRED-MOCK" in md and "[MOCK]" in md
    assert "7/7 benchmark adapters implemented" in md
    assert "Licenses, data sources" in md
    assert "데이터 부족" in md  # single run -> diff insufficient
    run = rp.load_run(d)
    assert run["by_cell"][("scripted", "tau_bench")]


def test_render_diff_two_runs(tmp_path):
    d1 = _write_run(tmp_path / "a", "tau_bench__scripted",
                    [_rec("scripted", "tau_bench", score=0.4)])
    d2 = _write_run(tmp_path / "b", "tau_bench__scripted",
                    [_rec("scripted", "tau_bench", score=0.9)])
    md = rp.render([d1, d2])
    assert "Δ (B−A)" in md
    assert "+0.500" in md


def test_collect_provenance_structure():
    p = collect_provenance(root=".")
    assert {"git_commit", "hermes_commit", "python", "platform", "packages"} <= p.keys()
    assert isinstance(p["packages"], dict)
    # when there's no commit, a note explains why (this checkout is not a git repo)
    if p["git_commit"] is None:
        assert p["git_note"] == "not a git repository"
