"""Smoke tests for the GDPval OUTPUT bridge (gdpval_codegen.run_codegen).

These guard the two fixes that keep a broken script from being scored as a
delivered file (see the GDPval runbook, Part A, Problem 3 / Step 7):

  * crash-after-write — a stub file is written, then the script raises; it must
    still classify as ``model`` (the exit-code guard), while the non-empty stub
    stays listed in ``files``;
  * empty file — a workbook saved with no cells classifies as ``model`` AND
    disappears from ``files`` (the empty-artifact guard).

Requires openpyxl (the INPUT/OUTPUT bridge dependency); skipped without it.
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("openpyxl") is None,
    reason="openpyxl not installed (pip install -e '.[gdpval]')",
)

from harness.benchmarks.gdpval_codegen import extract_code, run_codegen

_CLEAN = (
    "```python\nimport openpyxl\nwb=openpyxl.Workbook(); ws=wb.active\n"
    "ws['A1']='Region'; ws['B1']='Sales'\nwb.save('out.xlsx')\n```"
)
_CRASH = (
    "```python\nimport openpyxl\nwb=openpyxl.Workbook(); ws=wb.active\n"
    "ws['A1']='partial'\nwb.save('partial.xlsx')\nraise KeyError('x')\n```"
)
_EMPTY = "```python\nimport openpyxl\nopenpyxl.Workbook().save('blank.xlsx')\n```"


def test_clean_run_produces_file():
    r = run_codegen(_CLEAN)
    assert r["fail_type"] == "none"
    assert r["ok"] is True
    assert r["files"] == ["out.xlsx"]
    assert "Region" in r["extracted"]


def test_crash_after_write_is_model_failure():
    # Stub file is non-empty so it stays in `files`, but the crash forces `model`.
    r = run_codegen(_CRASH)
    assert r["fail_type"] == "model"
    assert r["ok"] is False
    assert r["files"] == ["partial.xlsx"]


def test_empty_file_is_model_failure_and_dropped():
    r = run_codegen(_EMPTY)
    assert r["fail_type"] == "model"
    assert r["ok"] is False
    assert r["files"] == []


def test_prose_deliverable_is_no_code():
    r = run_codegen("Here is my analysis, in prose, with no code block at all.")
    assert r["fail_type"] == "no_code"
    assert r["ok"] is False
    assert r["files"] == []


def test_missing_library_is_env_failure():
    # A guaranteed-missing import -> ModuleNotFoundError -> classified `env`
    # (a tooling limitation of this machine, not a fault in the agent's code).
    r = run_codegen("```python\nimport totally_missing_pkg_xyz123\n```")
    assert r["fail_type"] == "env"
    assert r["ok"] is False


def test_codegen_disabled_env(monkeypatch):
    monkeypatch.setenv("GDPVAL_CODEGEN", "0")
    r = run_codegen(_CLEAN)
    assert r["fail_type"] == "no_code"  # execution disabled -> grade text as-is
    assert r["ran"] is False


def test_extract_code_handles_unclosed_fence():
    code = extract_code("```python\nx = 1\nprint(x)")  # no closing ```
    assert code == "x = 1\nprint(x)"
    assert extract_code("just prose") is None
