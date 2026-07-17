"""Fetch GDPval reference files and extract their text so the agent can actually
read attachments (xlsx / pdf / docx / csv / txt) instead of hallucinating.

GDPval tasks reference files by URL (``metadata['reference_file_urls']``) but the
harness never gave the agent their content -> the model "simulates" data and the
rubric scores it 0. This module downloads each file (cached under
``external/gdpval_files/``) and extracts a text rendering that gets injected into
the task observation. Binary media (images/audio/video/etc.) is noted, not decoded
(the ACE Generator is a text-only chat call; multimodal would be a separate path).

Gated by env ``GDPVAL_INJECT_FILES`` (default "1" = on). Per-file text is capped by
``GDPVAL_FILE_MAXCHARS`` (default 15000).
"""
from __future__ import annotations

import base64
import hashlib
import os
import urllib.parse
import urllib.request

_TEXT_EXT = {".txt", ".csv", ".md", ".json", ".tsv", ".overpassql", ".yaml", ".yml", ".py"}
_IMG_EXT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif"}
_AUDIO_EXT = {".wav": "wav", ".mp3": "mp3", ".m4a": "mp4", ".ogg": "ogg", ".flac": "flac"}
_VIDEO_EXT = {".mp4", ".mov", ".avi", ".webm", ".mkv"}


def _describe_image(path: str, fn: str, max_chars: int) -> str:
    """Vision→text: ask Gemini (multimodal) to transcribe/describe the image, so the
    text-only Generator can 'read' it. Cached next to the file. Gated by GDPVAL_VISION."""
    if os.environ.get("GDPVAL_VISION", "1") not in ("1", "true", "True"):
        return f"[{fn}: image, vision disabled (GDPVAL_VISION=0)]"
    mime = _IMG_EXT[os.path.splitext(fn)[1].lower()]
    desc_path = path + ".vision.txt"
    if os.path.exists(desc_path):
        with open(desc_path, encoding="utf-8") as f:
            return f.read()
    try:
        from openai import OpenAI  # noqa: PLC0415
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        base_url = os.environ.get("GEMINI_BASE_URL",
                                  "https://generativelanguage.googleapis.com/v1beta/openai/")
        api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                   or os.environ.get("GEMINI_KEY_1") or "sk-proxy-rotation")
        model = os.environ.get("GDPVAL_VISION_MODEL", "gemini-3.1-flash-lite")
        client = OpenAI(base_url=base_url, api_key=api_key)
        r = client.chat.completions.create(
            model=model, max_tokens=1500, temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Transcribe and describe this image in full detail for a downstream "
                    "agent that cannot see it. Include ALL visible text verbatim, any tables "
                    "as text, chart/graph values and trends, layout, and key visual facts. "
                    "Be thorough and factual."},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
            ]}])
        desc = ("[IMAGE auto-transcribed by vision model]\n"
                + (r.choices[0].message.content or "").strip())[:max_chars]
        try:
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(desc)
        except Exception:  # noqa: BLE001
            pass
        return desc
    except Exception as e:  # noqa: BLE001
        return f"[{fn}: image, vision failed: {type(e).__name__}: {e}]"
def _gemini_client():
    from openai import OpenAI  # noqa: PLC0415
    base_url = os.environ.get("GEMINI_BASE_URL",
                              "https://generativelanguage.googleapis.com/v1beta/openai/")
    api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
               or os.environ.get("GEMINI_KEY_1") or "sk-proxy-rotation")
    return OpenAI(base_url=base_url, api_key=api_key), os.environ.get("GDPVAL_VISION_MODEL",
                                                                      "gemini-3.1-flash-lite")


def _describe_audio(path: str, fn: str, max_chars: int) -> str:
    """Audio -> text: Gemini transcribes speech + describes music/sound. Cached."""
    if os.environ.get("GDPVAL_VISION", "1") not in ("1", "true", "True"):
        return f"[{fn}: audio, transcription disabled]"
    desc_path = path + ".audio.txt"
    if os.path.exists(desc_path):
        with open(desc_path, encoding="utf-8") as f:
            return f.read()
    try:
        import base64  # noqa: PLC0415
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        fmt = _AUDIO_EXT.get(os.path.splitext(fn)[1].lower(), "wav")
        client, model = _gemini_client()
        r = client.chat.completions.create(
            model=model, max_tokens=2500, temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Transcribe and describe this audio in full for a downstream agent that cannot "
                    "hear it. Include ALL spoken words verbatim with speaker turns, and describe any "
                    "music, tempo, instruments, or sounds. Be thorough and factual."},
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}]}])
        desc = ("[AUDIO auto-transcribed]\n" + (r.choices[0].message.content or "").strip())[:max_chars]
        try:
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(desc)
        except Exception:  # noqa: BLE001
            pass
        return desc
    except Exception as e:  # noqa: BLE001
        return f"[{fn}: audio, transcription failed: {type(e).__name__}: {e}]"


def _describe_video(path: str, fn: str, max_chars: int) -> str:
    """Video -> text: sample frames (imageio) + vision-describe each, plus transcribe
    the audio track if present. Cached."""
    if os.environ.get("GDPVAL_VISION", "1") not in ("1", "true", "True"):
        return f"[{fn}: video]"
    desc_path = path + ".video.txt"
    if os.path.exists(desc_path):
        with open(desc_path, encoding="utf-8") as f:
            return f.read()
    parts = []
    try:
        import base64, tempfile  # noqa: PLC0415
        import imageio.v2 as imageio  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        rd = imageio.get_reader(path)
        try:
            nframes = rd.count_frames()
        except Exception:  # noqa: BLE001
            nframes = 0
        idxs = [int(nframes * f) for f in (0.1, 0.5, 0.9)] if nframes > 3 else [0]
        client, model = _gemini_client()
        for k, idx in enumerate(idxs):
            try:
                frame = rd.get_data(idx)
                tmp = os.path.join(tempfile.gettempdir(), f"vf_{k}.png")
                Image.fromarray(frame).save(tmp)
                with open(tmp, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                r = client.chat.completions.create(
                    model=model, max_tokens=800, temperature=0,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": f"Describe this video frame ({k+1}/{len(idxs)}) "
                         "in detail: on-screen text verbatim, subjects, action, layout."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}])
                parts.append(f"[frame {k+1}] " + (r.choices[0].message.content or "").strip())
            except Exception:  # noqa: BLE001
                continue
        rd.close()
    except Exception as e:  # noqa: BLE001
        parts.append(f"[video frame extraction failed: {type(e).__name__}]")
    desc = ("[VIDEO auto-described from sampled frames]\n" + "\n".join(parts))[:max_chars]
    try:
        with open(desc_path, "w", encoding="utf-8") as f:
            f.write(desc)
    except Exception:  # noqa: BLE001
        pass
    return desc


_DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "external", "gdpval_files")


def _cache_dir() -> str:
    return os.environ.get("GDPVAL_FILES_CACHE", _DEFAULT_CACHE)


def _fetch(url: str) -> tuple[str, str]:
    """Download url (cached). Returns (local_path, filename)."""
    fn = urllib.parse.unquote(os.path.basename(url.split("?")[0])) or "file"
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    cache = _cache_dir()
    path = os.path.join(cache, f"{h}_{fn}")
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "gdpval-harness"})
        data = urllib.request.urlopen(req, timeout=90).read()
        with open(path, "wb") as f:
            f.write(data)
    return path, fn


def _extract(path: str, fn: str, max_chars: int) -> str:
    ext = os.path.splitext(fn)[1].lower()
    try:
        if ext == ".xlsx":
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            out: list[str] = []
            size = 0
            for ws in wb.worksheets:
                out.append(f"# Sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    line = "\t".join("" if c is None else str(c) for c in row)
                    out.append(line)
                    size += len(line)
                    if size > max_chars:
                        out.append("... [truncated]")
                        wb.close()
                        return "\n".join(out)
            wb.close()
            return "\n".join(out)
        if ext == ".docx":
            import docx  # noqa: PLC0415
            d = docx.Document(path)
            paras = [p.text for p in d.paragraphs if p.text]
            for tbl in d.tables:
                for r in tbl.rows:
                    paras.append("\t".join(c.text for c in r.cells))
            return "\n".join(paras)
        if ext == ".pdf":
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(path) as pdf:
                txt = []
                for pg in pdf.pages:
                    txt.append(pg.extract_text() or "")
                    if sum(len(t) for t in txt) > max_chars:
                        break
                return "\n".join(txt)
        if ext == ".pptx":
            from pptx import Presentation  # noqa: PLC0415
            out = []
            for i, slide in enumerate(Presentation(path).slides, 1):
                out.append(f"# Slide {i}")
                for sh in slide.shapes:
                    if sh.has_text_frame:
                        for p in sh.text_frame.paragraphs:
                            t = "".join(run.text for run in p.runs)
                            if t:
                                out.append(t)
                    if getattr(sh, "has_table", False):
                        for row in sh.table.rows:
                            out.append("\t".join(c.text for c in row.cells))
            return "\n".join(out)[:max_chars]
        if ext == ".zip":
            import zipfile, tempfile  # noqa: PLC0415
            parts = []
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
                for name in names[:25]:
                    data = z.read(name)
                    sub = tempfile.NamedTemporaryFile(delete=False,
                                                      suffix="_" + os.path.basename(name))
                    sub.write(data); sub.close()
                    parts.append(f"--- {name} ---\n" + _extract(sub.name, os.path.basename(name),
                                                                 max(1000, max_chars // max(1, min(len(names), 25)))))
                    try:
                        os.remove(sub.name)
                    except Exception:  # noqa: BLE001
                        pass
            return (f"[ZIP with {len(names)} files]\n" + "\n".join(parts))[:max_chars]
        if ext in _AUDIO_EXT:
            return _describe_audio(path, fn, max_chars)
        if ext in _VIDEO_EXT:
            return _describe_video(path, fn, max_chars)
        if ext == ".ipynb":
            import json as _json  # noqa: PLC0415
            with open(path, encoding="utf-8") as f:
                nb = _json.load(f)
            cells = ["[%s]\n%s" % (c.get("cell_type"), "".join(c.get("source", [])))
                     for c in nb.get("cells", [])]
            return "\n\n".join(cells)[:max_chars]
        if ext == ".psd":
            try:
                from PIL import Image  # noqa: PLC0415
                import tempfile  # noqa: PLC0415
                tmp = os.path.join(tempfile.gettempdir(), os.path.basename(fn) + ".png")
                Image.open(path).convert("RGB").save(tmp)
                return _describe_image(tmp, os.path.basename(tmp), max_chars)
            except Exception:  # noqa: BLE001
                return f"[{fn}: Photoshop PSD, {os.path.getsize(path)} bytes -- could not rasterize]"
        if ext in (".step", ".stp", ".iges", ".igs"):
            return f"[{fn}: {ext[1:].upper()} CAD geometry file, {os.path.getsize(path)} bytes -- 3D geometry, not text-readable]"
        if ext in _IMG_EXT:
            return _describe_image(path, fn, max_chars)
        if ext in _TEXT_EXT:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        return f"[{ext or 'binary'} file, {os.path.getsize(path)} bytes -- not text-extractable]"
    except Exception as e:  # noqa: BLE001 - never let extraction crash a run
        return f"[could not extract {fn}: {type(e).__name__}: {e}]"


def reference_files_text(metadata: dict | None) -> str:
    """Return an injectable block with the extracted content of a task's reference
    files, or '' if none / disabled. Safe: never raises."""
    if os.environ.get("GDPVAL_INJECT_FILES", "1") not in ("1", "true", "True"):
        return ""
    urls = (metadata or {}).get("reference_file_urls") or []
    if not urls:
        return ""
    max_chars = int(os.environ.get("GDPVAL_FILE_MAXCHARS", "15000"))
    parts: list[str] = []
    for url in urls:
        try:
            path, fn = _fetch(url)
            text = _extract(path, fn, max_chars)[:max_chars]
            parts.append(f"\n===== ATTACHED FILE: {fn} =====\n{text}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"\n===== ATTACHED FILE: {os.path.basename(url)} (fetch failed: {e}) =====")
    return ("\n\n--- ATTACHED REFERENCE FILES (extracted; use this real data, "
            "do NOT simulate) ---" + "".join(parts))
