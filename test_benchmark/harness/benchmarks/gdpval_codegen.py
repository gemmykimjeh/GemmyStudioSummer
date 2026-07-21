"""gdpval_codegen — OUTPUT bridge for GDPval (execute the model's script, verify
the real deliverable file it produces, hand a verified manifest to the grader).

GDPval deliverables are FILES (.xlsx/.docx/.pdf/.pptx/...). The agent is a
text-only LLM: it cannot save a workbook, so it emits a ```python block instead.
This module runs that block in a sandbox, collects the real files it wrote, reads
them back to text, prepends a *verified* structure summary, and classifies the
outcome so the grader knows whether a file actually exists.

The classification is the point (see the GDPval runbook, Part A / Step 6):

    fail_type  meaning                                    grading policy
    ---------  -----------------------------------------  --------------------------
    none       ran clean AND produced a non-empty file    grade the verified manifest
    model      crashed / timed out / wrote nothing / an   tell the judge NO file
               empty stub file                            exists; file/format/
                                                          structure criteria NOT met
    env        blocked by THIS machine's tooling          not the agent's fault; grade
               (ModuleNotFoundError, DLL load failure,    intended content, do not
               Application Control, permission)           penalise the missing binary
    no_code    no ```python block emitted (prose only)    grade the text as-is

Two guards keep a broken script from being scored as a delivered file:
  * exit-code guard — a non-empty ``error`` (non-zero exit or timeout) forces
    ``model`` even when a stub file was written before the crash;
  * empty-artifact guard — a file with no real content (0 bytes, workbook with no
    non-empty cell, document with no text, deck with no slides) is not counted as
    produced, so the repair loop keeps working instead of accepting a stub.

Set ``GDPVAL_CODEGEN=0`` to disable execution entirely (the "no tools" mode): the
deliverable is then graded as plain text (fail_type ``no_code``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

# Extensions we recognise as *produced deliverables* (the script itself and any
# copied-in source attachments are excluded before this list is consulted).
_OUTPUT_EXTS = {
    ".xlsx", ".xlsm", ".docx", ".pdf", ".pptx", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".step", ".stp", ".stl", ".iges", ".igs",
    ".zip", ".mp4", ".ipynb",
}

# Substrings in stderr that mean THIS machine could not run the code (a tooling
# limitation), not that the agent's code is wrong. -> classified ``env``.
_ENV_MARKERS = (
    "modulenotfounderror",
    "no module named",
    "importerror",
    "dll load failed",
    "failed to load",
    "libopencascade",
    "ocp.dll",
    "smart app control",
    "application control",
    "permissionerror",
    "access is denied",
    "was blocked",
    "not marked for execution",
)

_TIMEOUT_S = 120
_READBACK_MAXCHARS = 15000


# --------------------------------------------------------------------------- #
# code extraction
# --------------------------------------------------------------------------- #
def extract_code(text: str) -> str | None:
    """Pull the python source out of a deliverable, tolerating an unclosed fence.

    Prefers a ```python fenced block; falls back to a bare ``` block; finally, if
    the whole text looks like a script, returns it verbatim. Returns None when no
    code is present (a prose-only deliverable).
    """
    if not text:
        return None
    # ```python ... ```  (also ```py / ```python3)
    m = re.search(r"```(?:python3?|py)\s*\n(.*?)(?:```|\Z)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        # a bare ``` ... ``` fence
        m = re.search(r"```\s*\n(.*?)(?:```|\Z)", text, re.DOTALL)
    if m:
        code = m.group(1).strip("\n")
        return code or None
    # No fence at all: only treat as code if it clearly is a script.
    stripped = text.strip()
    if re.search(r"^\s*(import |from \w+ import |def |wb\s*=|import openpyxl)", stripped, re.MULTILINE):
        return stripped
    return None


# --------------------------------------------------------------------------- #
# emptiness checks — a file with no real content is not a produced deliverable
# --------------------------------------------------------------------------- #
def _is_empty_artifact(path: str) -> bool:
    """True if the file has no real content (0 bytes / no cells / no text / no slides)."""
    try:
        if os.path.getsize(path) == 0:
            return True
    except OSError:
        return True
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None and str(c).strip() != "" for c in row):
                        wb.close()
                        return False
            wb.close()
            return True
        if ext == ".docx":
            import docx  # noqa: PLC0415
            d = docx.Document(path)
            if any(p.text.strip() for p in d.paragraphs):
                return False
            for t in d.tables:
                for r in t.rows:
                    if any(c.text.strip() for c in r.cells):
                        return False
            return True
        if ext == ".pptx":
            from pptx import Presentation  # noqa: PLC0415
            prs = Presentation(path)
            return len(prs.slides) == 0
        if ext == ".csv":
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return not f.read().strip()
        if ext == ".pdf":
            # 0-byte already caught above; a non-empty PDF binary counts as produced.
            return False
    except Exception:  # noqa: BLE001 - if we cannot open it, fall back to size check
        return False
    return False


# --------------------------------------------------------------------------- #
# structure summary — verified facts about the produced file
# --------------------------------------------------------------------------- #
def _collapse_formulas(cells: list[tuple[str, str]]) -> list[str]:
    """Report formulas by shape: collapse a filled-down column to one line.

    ``cells`` is a list of (coord, formula). A run of the same relative formula
    down a column becomes e.g. ``[936x] T2..T937  =Gn/In``.
    """
    if not cells:
        return []
    # Normalise each formula to a shape by replacing row numbers with 'n'.
    def shape(f: str) -> str:
        return re.sub(r"(\d+)", "n", f)
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for coord, formula in cells:
        s = shape(formula)
        if s not in groups:
            groups[s] = []
            order.append(s)
        groups[s].append(coord)
    lines = []
    for s in order:
        coords = groups[s]
        if len(coords) == 1:
            lines.append(f"[1x] {coords[0]}  ={s}")
        else:
            lines.append(f"[{len(coords)}x] {coords[0]}..{coords[-1]}  ={s}")
    return lines


def _structure_summary(path: str) -> str:
    """A short, verified structure description prepended to the readback content."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(path, data_only=False)
            parts = [f"sheets={wb.sheetnames}"]
            for ws in wb.worksheets:
                merged = len(ws.merged_cells.ranges)
                formulas: list[tuple[str, str]] = []
                bold = 0
                fmts: set[str] = set()
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and c.value.startswith("="):
                            formulas.append((c.coordinate, c.value))
                        if c.font and c.font.bold:
                            bold += 1
                        if c.number_format and c.number_format != "General":
                            fmts.add(c.number_format)
                s = [f"  [{ws.title}] {ws.max_row}x{ws.max_column} cells",
                     f"merged={merged}", f"bold_cells={bold}"]
                if fmts:
                    s.append("number_formats=" + ",".join(sorted(fmts)[:8]))
                parts.append(" ".join(s))
                if formulas:
                    parts.append("  formulas (by shape):")
                    parts.extend("    " + ln for ln in _collapse_formulas(formulas)[:60])
            wb.close()
            return "\n".join(parts)
        if ext == ".docx":
            import docx  # noqa: PLC0415
            d = docx.Document(path)
            paras = sum(1 for p in d.paragraphs if p.text.strip())
            return f"paragraphs={paras} tables={len(d.tables)}"
        if ext == ".pptx":
            from pptx import Presentation  # noqa: PLC0415
            prs = Presentation(path)
            w, h = prs.slide_width, prs.slide_height
            orient = "landscape" if (w or 0) >= (h or 0) else "portrait"
            return f"slides={len(prs.slides)} size={w}x{h} ({orient})"
        if ext == ".pdf":
            try:
                import pdfplumber  # noqa: PLC0415
                with pdfplumber.open(path) as pdf:
                    n = len(pdf.pages)
                    p0 = pdf.pages[0] if n else None
                    dims = f"{int(p0.width)}x{int(p0.height)}" if p0 else "?"
                    orient = ("landscape" if p0 and p0.width >= p0.height
                              else "portrait") if p0 else "?"
                    return f"pages={n} page_size={dims} ({orient})"
            except Exception:  # noqa: BLE001
                return f"pdf bytes={os.path.getsize(path)}"
    except Exception as exc:  # noqa: BLE001
        return f"(structure summary unavailable: {exc})"
    return f"bytes={os.path.getsize(path)}"


# --------------------------------------------------------------------------- #
# readback — turn the produced file into text for the grader
# --------------------------------------------------------------------------- #
def _read_back(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(path, data_only=True)
            out = []
            for ws in wb.worksheets:
                out.append(f"### sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None for c in row):
                        out.append("\t".join("" if c is None else str(c) for c in row))
            wb.close()
            return "\n".join(out)
        if ext == ".docx":
            import docx  # noqa: PLC0415
            d = docx.Document(path)
            out = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:
                for r in t.rows:
                    out.append("\t".join(c.text for c in r.cells))
            return "\n".join(out)
        if ext == ".pptx":
            from pptx import Presentation  # noqa: PLC0415
            prs = Presentation(path)
            out = []
            for i, slide in enumerate(prs.slides, 1):
                out.append(f"### slide {i}")
                for shp in slide.shapes:
                    if shp.has_text_frame and shp.text_frame.text.strip():
                        out.append(shp.text_frame.text)
            return "\n".join(out)
        if ext == ".pdf":
            import pdfplumber  # noqa: PLC0415
            out = []
            with pdfplumber.open(path) as pdf:
                for pg in pdf.pages:
                    out.append(pg.extract_text() or "")
            return "\n".join(out)
        if ext in (".csv", ".svg", ".ipynb"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001
        return f"(could not read back {os.path.basename(path)}: {exc})"
    return f"(binary file {os.path.basename(path)}, {os.path.getsize(path)} bytes — not text-extracted)"


def _extracted_text(files: list[str], workdir: str) -> str:
    blocks = []
    for name in files:
        path = os.path.join(workdir, name)
        summary = _structure_summary(path)
        content = _read_back(path)
        if len(content) > _READBACK_MAXCHARS:
            content = content[:_READBACK_MAXCHARS] + "\n...[truncated]"
        blocks.append(f"===== FILE: {name} =====\n[STRUCTURE] {summary}\n[CONTENT]\n{content}")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# the sandbox
# --------------------------------------------------------------------------- #
def _classify_error(error: str) -> str:
    e = (error or "").lower()
    if any(m in e for m in _ENV_MARKERS):
        return "env"
    return "model"


def run_codegen(deliverable: str, attachments: list[str] | None = None,
                workdir: str | None = None, timeout: int = _TIMEOUT_S) -> dict:
    """Execute the python block in ``deliverable`` and verify the file it produces.

    Returns a dict:
        ran        bool  — code was extracted and executed
        ok         bool  — clean exit AND a non-empty produced file exists
        fail_type  str   — none | model | env | no_code
        files      list  — non-empty produced deliverable filenames
        extracted  str   — readback content + verified structure summary (fail_type none)
        error      str   — stderr / timeout marker ("" on clean exit)
        stdout     str   — captured stdout
    """
    result = {"ran": False, "ok": False, "fail_type": "no_code",
              "files": [], "extracted": "", "error": "", "stdout": ""}

    if os.environ.get("GDPVAL_CODEGEN", "1") == "0":
        return result  # execution disabled -> grade the text as-is

    code = extract_code(deliverable)
    if not code:
        return result  # no_code

    owns_workdir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="gdpval_codegen_")
    try:
        # Copy the task's attachments in under their original filenames so the
        # script can `pd.read_excel('Key Indicators.xlsx')` directly.
        source_names = set()
        for src in attachments or []:
            if src and os.path.isfile(src):
                base = os.path.basename(src)
                shutil.copy2(src, os.path.join(workdir, base))
                source_names.add(base)

        script_name = "deliverable_gen.py"
        script_path = os.path.join(workdir, script_name)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        before = set(os.listdir(workdir))
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        try:
            proc = subprocess.run(
                [sys.executable, script_name],
                cwd=workdir, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            result["ran"] = True
            result["stdout"] = (proc.stdout or "")[-4000:]
            if proc.returncode != 0:
                result["error"] = (proc.stderr or f"exit code {proc.returncode}")[-4000:]
        except subprocess.TimeoutExpired as exc:
            result["ran"] = True
            result["error"] = f"timeout after {timeout}s"
            result["stdout"] = (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else ""

        # Collect produced files: new files, recognised output extension, not the
        # script and not a copied-in source. Then drop empty artifacts.
        produced = []
        for name in sorted(os.listdir(workdir)):
            if name == script_name or name in source_names:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in _OUTPUT_EXTS:
                continue
            path = os.path.join(workdir, name)
            if not os.path.isfile(path):
                continue
            if _is_empty_artifact(path):
                continue  # empty-artifact guard: a stub is not a produced file
            produced.append(name)
        result["files"] = produced

        # Classify.
        if result["error"]:
            result["fail_type"] = _classify_error(result["error"])
            result["ok"] = False
        elif produced:
            result["fail_type"] = "none"
            result["ok"] = True
            result["extracted"] = _extracted_text(produced, workdir)
        else:
            # Ran clean but wrote no real file -> the agent's code is incomplete.
            result["fail_type"] = "model"
            result["ok"] = False
        return result
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
