#!/usr/bin/env python3
"""
Legal/article-first slide pipeline, final robust version
========================================================

Purpose
-------
Generate PML/PSL for Vietnamese legal/normative documents without falling back
onto the administrative header or only the first article.

Design choice
-------------
This pipeline does NOT use a task pop loop. The earlier bug came from loop/state
aggregation in LangGraph/Streamlit combinations. This version builds all article
units first, converts every unit into a slide spec deterministically, then
aggregates once. It still exposes build_graph().invoke(state) for the Streamlit
app.

Main guarantees
---------------
- For legal documents, substantive content starts at `Điều 1`.
- Lines before `Điều 1` such as `Căn cứ...`, `Xét...`, quốc hiệu/tiêu ngữ are
  never used as summary bullets.
- `Nơi nhận` and signature tail are removed, but a later `PHỤ LỤC` is preserved.
- Every `Điều N` becomes a separate slide; `PHỤ LỤC` becomes a separate slide.
- Fallback never uses the administrative header.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# PSL default. The Streamlit app may recolor this or replace it with inferred PSL.
# ---------------------------------------------------------------------------

DEFAULT_PSL = '''
theme "corporate":
  page:
    size: widescreen
    background: "#FFFFFF"

  colors:
    primary: "#003A8C"
    secondary: "#E6F0FF"
    text: "#1F1F1F"
    muted: "#666666"
    white: "#FFFFFF"

  fonts:
    heading: "Aptos Display"
    body: "Aptos"

  presentation.title-slide:
    background: primary
    title:
      font: heading
      size: 42
      color: white
      bold: true
      align: center
      position: [90, 230]
      width: 1100
      height: 110
    author:
      font: body
      size: 20
      color: white
      align: center
      position: [180, 390]
      width: 920
      height: 50

  slide.title-bullets:
    title:
      font: heading
      size: 34
      color: primary
      bold: true
      align: left
      position: [60, 40]
      width: 1120
      height: 80
    bullets:
      font: body
      size: 22
      color: text
      position: [90, 150]
      width: 1060
      height: 500
      line_gap: 9
      overflow: shrink
      max_lines: 8
      min_size: 15
'''


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------




def setup_logger(out_dir: str | Path, level: str = "INFO") -> logging.Logger:
    """Create a per-run logger that writes to both console and pipeline.log."""
    out = ensure_dir(out_dir)
    logger = logging.getLogger("legal_article_pipeline")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(out / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def save_json(path: str | Path, data: Any) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pml_quote(text: Any) -> str:
    s = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    s = s.replace('"', "'")
    # Quote only when needed; renderer parser accepts quoted scalars.
    if not s:
        return '""'
    if any(ch in s for ch in [":", "#", "[", "]", "- "]) or len(s) > 70:
        return json.dumps(s, ensure_ascii=False)
    return s


def truncate_sentence_safe(text: str, max_chars: int = 190) -> str:
    """Shorten without ellipsis and prefer sentence/word boundary."""
    s = normalize_space(text)
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    # prefer the last strong punctuation or semicolon/comma if reasonably far
    candidates = [cut.rfind(x) for x in [". ", "; ", ": ", ", ", " "]]
    pos = max(candidates)
    if pos >= max(60, max_chars // 2):
        return cut[:pos].strip(" ;,:.-–—")
    return cut.strip(" ;,:.-–—")


# ---------------------------------------------------------------------------
# Input extraction
# ---------------------------------------------------------------------------


def extract_docx_text_in_order(path: Path) -> str:
    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise RuntimeError("Missing dependency: python-docx") from exc

    doc = Document(str(path))
    parts: List[str] = []

    def iter_blocks(document):
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, document)
            elif isinstance(child, CT_Tbl):
                yield Table(child, document)

    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            t = normalize_space(block.text)
            if t:
                parts.append(t)
        else:
            for row in block.rows:
                cells = [normalize_space(cell.text) for cell in row.cells]
                # De-duplicate repeated merged cells while preserving order.
                uniq: List[str] = []
                for c in cells:
                    if c and (not uniq or c != uniq[-1]):
                        uniq.append(c)
                if uniq:
                    parts.append(" | ".join(uniq))
    return "\n".join(parts)


def extract_input_text(path: str = "", input_text: str = "", use_stdin: bool = False) -> str:
    if input_text and input_text.strip():
        return input_text
    if use_stdin:
        return sys.stdin.read()
    if not path:
        raise ValueError("Provide input file path, --text, or --stdin")
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return p.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".docx":
        return extract_docx_text_in_order(p)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Missing dependency: pypdf") from exc
        reader = PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"Unsupported input file type: {suffix}")


# ---------------------------------------------------------------------------
# Legal cleaning and splitting
# ---------------------------------------------------------------------------


ARTICLE_RE = re.compile(r"(?im)^\s*Điều\s+(\d+)\s*[\.．:]\s*(.+?)\s*$")
APPENDIX_RE = re.compile(r"(?im)^\s*PHỤ\s+LỤC\b.*$")
RECIPIENT_RE = re.compile(r"(?im)^\s*Nơi\s+nhận\s*:?")


def is_legal_doc(text: str) -> bool:
    return bool(ARTICLE_RE.search(text) or APPENDIX_RE.search(text))


def is_admin_or_preamble_line(line: str) -> bool:
    s = normalize_space(line)
    if not s:
        return False
    patterns = [
        r"^HỘI\s+ĐỒNG\s+NHÂN\s+DÂN\b",
        r"^ỦY\s+BAN\s+NHÂN\s+DÂN\b",
        r"^THÀNH\s+PHỐ\b",
        r"^TỈNH\b",
        r"^Số\s*[:：]",
        r"^CỘNG\s+H[OÒÓỌÕỎÔỒỐỘỖỔƠỜỚỢỠỞ]A\s+X[AÃ]\s+H[ỘOÒÓỌÕỎ]I\s+CH[ỦU]\s+NGH[IĨ]A\s+VI[ỆE]T\s+NAM$",
        r"^Độc\s+lập\s*[-–—]\s*Tự\s+do\s*[-–—]\s*Hạnh\s+phúc$",
        r"^Hải\s+Phòng,\s+ngày\b",
        r"^DỰ\s+THẢO$",
        r"^NGHỊ\s+QUYẾT$",
        r"^QUYẾT\s+ĐỊNH$",
        r"^Căn\s+cứ\b",
        r"^Xét\b",
        r"^Hội\s+đồng\s+nhân\s+dân\s+.*ban\s+hành\b",
        r"^Nghị\s+quyết\s+này\s+đã\s+được\b",
        r"^CHỦ\s+TỊCH$",
        r"^TM\.\b",
        r"^KT\.\b",
    ]
    return any(re.search(p, s, re.I) for p in patterns)


def remove_recipient_tail_keep_appendix(text: str) -> str:
    """Remove `Nơi nhận`/signature tail while preserving a later `PHỤ LỤC`."""
    lines = text.splitlines()
    out: List[str] = []
    in_tail = False
    for raw in lines:
        line = normalize_space(raw)
        if in_tail and APPENDIX_RE.match(line):
            in_tail = False
        if not in_tail and RECIPIENT_RE.match(line):
            in_tail = True
            continue
        if in_tail:
            continue
        out.append(raw)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def cut_legal_body(text: str) -> str:
    """Hard-start at Điều 1; preserve appendix even if it appears after signature."""
    if not text:
        return ""
    # Remove recipient/signature tail but keep appendix first.
    t = remove_recipient_tail_keep_appendix(text)
    article = ARTICLE_RE.search(t)
    if article:
        return t[article.start():].strip()
    appendix = APPENDIX_RE.search(t)
    if appendix:
        return t[appendix.start():].strip()
    return t.strip()


def clean_text_for_pipeline(text: str) -> str:
    """Clean but never let legal fallback use the document header."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if is_legal_doc(text):
        text = cut_legal_body(text)
    lines: List[str] = []
    for raw in text.splitlines():
        line = normalize_space(raw)
        if not line:
            continue
        # After Điều 1, still drop legal basis/preamble lines if any were OCR-moved.
        if is_admin_or_preamble_line(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def split_legal_sections(text: str) -> List[Dict[str, Any]]:
    """Split by Điều N and PHỤ LỤC. Returns every article, not just first one."""
    t = clean_text_for_pipeline(text)
    if not t:
        return []

    matches: List[tuple[int, str, str, str]] = []
    for m in ARTICLE_RE.finditer(t):
        num = m.group(1)
        heading = normalize_space(m.group(2))
        matches.append((m.start(), f"Điều {num}", heading, "article"))
    app = APPENDIX_RE.search(t)
    if app:
        # Keep whole appendix heading line as title if possible.
        title = normalize_space(app.group(0)) or "PHỤ LỤC"
        matches.append((app.start(), "PHỤ LỤC", title.title(), "appendix"))

    matches.sort(key=lambda x: x[0])
    sections: List[Dict[str, Any]] = []
    for idx, (start, numbering, heading, kind) in enumerate(matches):
        end = matches[idx + 1][0] if idx + 1 < len(matches) else len(t)
        block = t[start:end].strip()
        lines = block.splitlines()
        # Remove the heading line itself from content.
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        # Remove any accidental admin/preamble lines in the content.
        content_lines = [ln for ln in content.splitlines() if not is_admin_or_preamble_line(ln)]
        content = "\n".join(content_lines).strip()
        if not content and kind != "appendix":
            continue
        sections.append({
            "id": f"H{idx+1:04d}",
            "numbering": numbering,
            "title": heading if kind == "appendix" else heading,
            "kind": kind,
            "content": content,
            "char_count": len(content),
        })
    return sections


# ---------------------------------------------------------------------------
# Non-legal fallback outline
# ---------------------------------------------------------------------------


GENERIC_HEADING_RE = re.compile(r"(?m)^\s*((?:[IVX]+|\d+(?:\.\d+)*|[A-Z])\s*[\.\)]\s+.+|[A-ZÀ-ỸĐ][A-ZÀ-ỸĐ\s]{8,})\s*$")


def split_generic_sections(text: str) -> List[Dict[str, Any]]:
    lines = [normalize_space(x) for x in text.splitlines() if normalize_space(x)]
    if not lines:
        return []
    matches = []
    for i, line in enumerate(lines):
        if GENERIC_HEADING_RE.match(line) and len(line) <= 140:
            matches.append((i, line))
    if len(matches) < 2:
        return [{"id": "H0001", "numbering": "", "title": "Nội dung chính", "kind": "generic", "content": "\n".join(lines), "char_count": len("\n".join(lines))}]
    sections = []
    for idx, (line_idx, heading) in enumerate(matches):
        next_idx = matches[idx + 1][0] if idx + 1 < len(matches) else len(lines)
        content = "\n".join(lines[line_idx + 1:next_idx]).strip()
        if content:
            sections.append({"id": f"H{idx+1:04d}", "numbering": "", "title": heading, "kind": "generic", "content": content, "char_count": len(content)})
    return sections


def split_document_units(text: str) -> List[Dict[str, Any]]:
    cleaned = clean_text_for_pipeline(text)
    if is_legal_doc(text) or is_legal_doc(cleaned):
        sections = split_legal_sections(text)
        if sections:
            return sections
    return split_generic_sections(cleaned)


# ---------------------------------------------------------------------------
# Slide shaping
# ---------------------------------------------------------------------------


def split_content_to_bullets(content: str, kind: str = "article", max_items: int = 8) -> List[str]:
    lines = [normalize_space(x) for x in content.splitlines() if normalize_space(x)]
    bullets: List[str] = []

    if kind == "appendix":
        # Prefer rows that look like service/category rows, but keep compact.
        for line in lines:
            if line.lower().startswith("stt") or "danh mục" in line.lower() and "mức thu" in line.lower():
                continue
            if re.match(r"^\d+(?:\.\d+)*\s*\|?\s*", line) or "dịch vụ" in line.lower():
                bullets.append(truncate_sentence_safe(line.replace(" | ", ": "), 190))
            if len(bullets) >= max_items:
                break
        if bullets:
            return bullets

    # Keep subheadings and legal list items as separate bullets.
    for line in lines:
        if not line or is_admin_or_preamble_line(line):
            continue
        # Split very long semicolon-heavy legal clauses into sentence-like units.
        pieces = [line]
        if len(line) > 260 and ";" in line:
            pieces = [p.strip() for p in line.split(";") if p.strip()]
        for p in pieces:
            if p:
                bullets.append(truncate_sentence_safe(p, 190))
            if len(bullets) >= max_items:
                break
        if len(bullets) >= max_items:
            break
    return bullets


def slide_spec_from_unit(unit: Dict[str, Any]) -> Dict[str, Any]:
    numbering = unit.get("numbering", "")
    title = unit.get("title") or numbering or "Nội dung"
    if numbering.startswith("Điều") and title and not title.startswith(numbering):
        slide_title = f"{numbering}. {title}"
    elif numbering == "PHỤ LỤC":
        slide_title = "Phụ lục: Danh mục các khoản thu và mức thu"
    else:
        slide_title = title
    bullets = split_content_to_bullets(unit.get("content", ""), unit.get("kind", "article"), max_items=8)
    if not bullets:
        bullets = ["Nội dung mục này không có thông tin đủ rõ để tóm tắt."]
    return {
        "task_id": unit.get("id"),
        "status": "completed",
        "title": slide_title,
        "intent": "summarize_legal_article" if unit.get("kind") == "article" else "summarize_appendix_table",
        "layout": "title-bullets",
        "blocks": {"bullets": bullets},
        "source_kind": unit.get("kind"),
        "source_numbering": numbering,
    }


def validate_slide_spec(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not normalize_space(spec.get("title", "")):
        errors.append("missing_title")
    bullets = spec.get("blocks", {}).get("bullets", [])
    if not isinstance(bullets, list) or not bullets:
        errors.append("missing_bullets")
    serialized = json.dumps(spec, ensure_ascii=False)
    # Only flag true administrative boilerplate. Do NOT flag ordinary legal
    # wording like "căn cứ vào điều kiện thực tế" inside substantive articles.
    if re.search(r"^\s*HỘI\s+ĐỒNG\s+NHÂN\s+DÂN\s*$|CỘNG\s+HOÀ|CỘNG\s+HÒA|^\s*Căn\s+cứ\b|^\s*Xét\b|Nơi\s+nhận", serialized, re.I | re.M):
        errors.append("administrative_or_preamble_leak")
    return errors


# ---------------------------------------------------------------------------
# LLM task summarization
# ---------------------------------------------------------------------------


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I).strip()
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(s[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("LLM response does not contain a valid JSON object")


def build_task_prompt(unit: Dict[str, Any], max_bullets: int = 7) -> str:
    numbering = unit.get("numbering", "")
    title = unit.get("title", "")
    kind = unit.get("kind", "article")
    content = unit.get("content", "")
    return f"""Bạn là chuyên gia tóm tắt văn bản pháp quy Việt Nam để tạo slide.

Nhiệm vụ: Tóm tắt RIÊNG đơn vị sau thành nội dung slide.

Yêu cầu bắt buộc:
- Chỉ dùng nội dung trong đơn vị được cung cấp.
- Không tóm tắt phần căn cứ pháp lý, quốc hiệu, tiêu ngữ, số văn bản, nơi nhận, chữ ký.
- Không dùng dấu ba chấm "..." hoặc "…".
- Không cắt câu giữa chừng.
- Trả về JSON object hợp lệ, không markdown, không giải thích.
- bullets phải là các ý hoàn chỉnh, ngắn gọn nhưng đủ nghĩa.
- Nếu nội dung là bảng/phụ lục, hãy tóm tắt các nhóm khoản thu/mức thu chính.
- Số bullet: tối đa {max_bullets}.

Schema JSON:
{{
  "title": "tiêu đề slide ngắn gọn",
  "bullets": ["ý 1", "ý 2"],
  "conclusion": "kết luận ngắn nếu thật sự cần, nếu không thì để rỗng"
}}

Đơn vị:
- kind: {kind}
- numbering: {numbering}
- heading: {title}

Nội dung:
<CONTENT>
{content}
</CONTENT>
"""


def make_gemini_client(api_key: str | None = None):
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Missing dependency: google-genai. Install: pip install google-genai") from exc
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is required when --llm-mode gemini is used. The pipeline will not silently fallback.")
    return genai.Client(api_key=key)


def gemini_text_response(client, model: str, prompt: str) -> str:
    resp = client.models.generate_content(model=model, contents=prompt)
    text = getattr(resp, "text", None)
    if text:
        return text
    try:
        return resp.candidates[0].content.parts[0].text
    except Exception as exc:
        raise RuntimeError("Gemini response did not contain text") from exc


def clean_llm_bullet(text: Any, max_chars: int = 220) -> str:
    s = normalize_space(str(text or ""))
    s = s.replace("...", "").replace("…", "").strip()
    return truncate_sentence_safe(s, max_chars=max_chars)


def slide_spec_from_llm_result(unit: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    numbering = unit.get("numbering", "")
    default_title = unit.get("title") or numbering or "Nội dung"
    if numbering.startswith("Điều") and default_title and not default_title.startswith(numbering):
        default_title = f"{numbering}. {default_title}"
    elif numbering == "PHỤ LỤC":
        default_title = "Phụ lục: Danh mục các khoản thu và mức thu"

    title = normalize_space(result.get("title") or default_title)
    if numbering.startswith("Điều") and numbering not in title:
        title = default_title

    bullets_raw = result.get("bullets", [])
    if not isinstance(bullets_raw, list):
        bullets_raw = []
    bullets = []
    for b in bullets_raw:
        bb = clean_llm_bullet(b)
        if bb and not is_admin_or_preamble_line(bb) and not re.match(r"^(Căn cứ|Xét)\b", bb, re.I):
            bullets.append(bb)
    bullets = bullets[:8]
    if not bullets:
        bullets = split_content_to_bullets(unit.get("content", ""), unit.get("kind", "article"), max_items=6)

    spec = {
        "task_id": unit.get("id"),
        "status": "completed",
        "title": title,
        "intent": "summarize_legal_article" if unit.get("kind") == "article" else "summarize_appendix_table",
        "layout": "title-bullets",
        "blocks": {"bullets": bullets},
        "source_kind": unit.get("kind"),
        "source_numbering": numbering,
        "llm_used": True,
    }
    conclusion = clean_llm_bullet(result.get("conclusion", ""), max_chars=180)
    if conclusion:
        spec["blocks"]["conclusion"] = conclusion
    return spec


def summarize_unit_with_gemini(unit: Dict[str, Any], client, model: str, logger: logging.Logger, max_bullets: int = 7) -> Dict[str, Any]:
    task_id = unit.get("id")
    logger.info("[LLM START] task=%s title=%s chars=%s", task_id, unit.get("title"), len(unit.get("content", "")))
    t0 = time.time()
    prompt = build_task_prompt(unit, max_bullets=max_bullets)
    raw = gemini_text_response(client, model, prompt)
    elapsed = time.time() - t0
    logger.info("[LLM END] task=%s elapsed=%.2fs response_chars=%s", task_id, elapsed, len(raw or ""))
    obj = extract_json_object(raw)
    return slide_spec_from_llm_result(unit, obj)


def summarize_unit_by_mode(unit: Dict[str, Any], mode: str, client: Any, model: str, logger: logging.Logger) -> Dict[str, Any]:
    mode = (mode or "gemini").lower()
    if mode == "gemini":
        return summarize_unit_with_gemini(unit, client, model, logger)
    if mode == "mock":
        logger.warning("[LLM MOCK] task=%s using deterministic mock summary", unit.get("id"))
        spec = slide_spec_from_unit(unit)
        spec["llm_used"] = False
        spec["mock_used"] = True
        return spec
    if mode == "heuristic":
        logger.warning("[LLM HEURISTIC] task=%s using heuristic summary by explicit request", unit.get("id"))
        spec = slide_spec_from_unit(unit)
        spec["llm_used"] = False
        spec["heuristic_used"] = True
        return spec
    raise ValueError(f"Unsupported llm_mode: {mode}")


# ---------------------------------------------------------------------------
# PML generation
# ---------------------------------------------------------------------------


def render_pml(slides: List[Dict[str, Any]], style_file: str = "corporate.psl") -> str:
    out: List[str] = []
    out.append('presentation "Tóm tắt báo cáo":')
    out.append('  meta:')
    out.append('    author: "Legal Article Final Pipeline"')
    out.append('    language: vi')
    out.append('    format: pptx')
    out.append('')
    out.append(f'  use style: "{style_file}"')
    out.append('')
    out.append('  cover_layout: title-slide')
    out.append('  cover:')
    out.append('    author: "Tạo tự động từ bố cục thật của văn bản"')
    out.append('')
    out.append('  section "Nội dung chính":')
    out.append('')
    for spec in slides:
        title = json.dumps(str(spec["title"]), ensure_ascii=False)
        out.append(f'    slide {title}:')
        out.append(f'      layout: {spec.get("layout", "title-bullets")}')
        out.append(f'      intent: {spec.get("intent", "summarize_core_message")}')
        out.append('')
        out.append('      title:')
        out.append(f'        {pml_quote(spec["title"])}')
        out.append('')
        out.append('      bullets:')
        out.append('        icon: check')
        out.append('        items:')
        for b in spec.get("blocks", {}).get("bullets", []):
            out.append(f'          - {pml_quote(b)}')
        out.append('')
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Optional renderer call for CLI
# ---------------------------------------------------------------------------


def import_module(path: str | Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def render_outputs_if_possible(state: Dict[str, Any]) -> Dict[str, Any]:
    renderer_path = state.get("renderer_path", "")
    if not renderer_path or not Path(renderer_path).exists():
        return state
    renderer = import_module(renderer_path, "pml_renderer_final")
    required = ["parse_pml", "parse_psl", "build_render_ir", "render_html", "render_pptx"]
    if any(not hasattr(renderer, x) for x in required):
        return state
    out_dir = ensure_dir(state["out_dir"])
    doc = renderer.parse_pml(state["pml_text"])
    theme = renderer.parse_psl(state["psl_text"])
    ir = renderer.build_render_ir(doc, theme)
    html_path = out_dir / "output.html"
    pptx_path = out_dir / "output.pptx"
    md_path = out_dir / "output.md"
    renderer.render_html(ir, str(html_path))
    renderer.render_pptx(ir, str(pptx_path))
    if hasattr(renderer, "render_markdown"):
        renderer.render_markdown(ir, str(md_path))
        state["md_path"] = str(md_path)
    state["html_path"] = str(html_path)
    state["pptx_path"] = str(pptx_path)
    return state


# ---------------------------------------------------------------------------
# Graph-like pipeline
# ---------------------------------------------------------------------------


def run_pipeline(state: Dict[str, Any]) -> Dict[str, Any]:
    out_dir = ensure_dir(state.get("out_dir", "out_legal_article_final"))
    logger = setup_logger(out_dir, state.get("log_level", "INFO"))
    validation: List[Dict[str, Any]] = []
    repairs: List[str] = []

    logger.info("=== PIPELINE START ===")
    llm_mode = str(state.get("llm_mode") or ("mock" if state.get("mock") else "gemini")).lower()
    if llm_mode == "mock":
        state["mock"] = True
    logger.info("mode=legal_article_pipeline llm_mode=%s", llm_mode)
    client = None
    if llm_mode == "gemini":
        client = make_gemini_client(state.get("gemini_api_key"))
        logger.info("[LLM READY] provider=gemini model=%s", state.get("model", "gemini-2.0-flash"))
    else:
        logger.warning("[LLM DISABLED] llm_mode=%s; no Gemini calls will be made", llm_mode)
    logger.info("input_path=%s out_dir=%s renderer=%s", state.get("input_path") or state.get("pdf_path") or "<text/stdin>", out_dir, state.get("renderer_path", ""))

    source_text = extract_input_text(
        path=state.get("input_path", "") or state.get("pdf_path", ""),
        input_text=state.get("input_text", ""),
        use_stdin=bool(state.get("use_stdin", False)),
    )
    cleaned_text = clean_text_for_pipeline(source_text)
    units = split_document_units(source_text)

    logger.info("=== DOCUMENT ===")
    logger.info("source_chars=%s cleaned_chars=%s", len(source_text), len(cleaned_text))
    logger.info("is_legal_doc=%s", is_legal_doc(source_text) or is_legal_doc(cleaned_text))

    logger.info("=== UNITS / ARTICLES ===")
    logger.info("unit_count=%s", len(units))
    for idx, u in enumerate(units, start=1):
        logger.info("[UNIT %02d] kind=%s numbering=%s title=%s chars=%s", idx, u.get("kind"), u.get("numbering"), u.get("title"), len(u.get("content", "")))

    validation.append({
        "stage": "extract_and_split",
        "source_chars": len(source_text),
        "cleaned_chars": len(cleaned_text),
        "unit_count": len(units),
        "unit_titles": [u.get("numbering", "") + " " + u.get("title", "") for u in units],
    })

    slides: List[Dict[str, Any]] = []
    logger.info("=== TASK BUILD / SLIDE SPEC ===")
    logger.info("task_count=%s", len(units))
    for unit in units:
        logger.info("[TASK START] unit=%s title=%s chars=%s", unit.get("numbering"), unit.get("title"), len(unit.get("content", "")))
        spec = summarize_unit_by_mode(unit, llm_mode, client, state.get("model", "gemini-2.0-flash"), logger)
        logger.info("[TASK GENERATED] task=%s slide_title=%s bullet_count=%s llm_used=%s", unit.get("id"), spec.get("title"), len(spec.get("blocks", {}).get("bullets", [])), spec.get("llm_used", False))
        errors = validate_slide_spec(spec)
        if errors:
            logger.warning("[VALIDATE FAIL] task=%s errors=%s", unit.get("id"), errors)
            repairs.append(f"{unit.get('id')}: {errors}")
            # If preamble leaked, clean bullets again by dropping bad bullets.
            bullets = []
            for b in spec.get("blocks", {}).get("bullets", []):
                if not is_admin_or_preamble_line(b) and not re.search(r"^\s*Căn\s+cứ\b|^\s*Xét\b|Nơi\s+nhận", b, re.I):
                    bullets.append(b)
            spec["blocks"]["bullets"] = bullets or ["Nội dung chính của mục này cần được rà soát thêm."]
            errors = validate_slide_spec(spec)
            if errors:
                logger.error("[REPAIR FAILED] task=%s errors=%s", unit.get("id"), errors)
            else:
                logger.info("[REPAIR OK] task=%s", unit.get("id"))
        if not errors:
            slides.append(spec)
            logger.info("[TASK DONE] task=%s accepted", unit.get("id"))
        else:
            validation.append({"stage": "skip_slide", "unit": unit.get("title"), "errors": errors})
            logger.warning("[TASK SKIPPED] task=%s title=%s", unit.get("id"), unit.get("title"))

    if not slides:
        logger.warning("[FALLBACK] no valid slides; generating fallback from cleaned body")
        # Last resort: never use header; use cleaned body only.
        bullets = split_content_to_bullets(cleaned_text, "generic", max_items=8)
        slides = [{
            "task_id": "FALLBACK",
            "status": "completed",
            "title": "Nội dung chính",
            "intent": "summarize_core_message",
            "layout": "title-bullets",
            "blocks": {"bullets": bullets or ["Không có đủ nội dung sau khi loại phần hành chính."]},
        }]
        validation.append({"stage": "fallback", "reason": "no_valid_units"})

    logger.info("=== AGGREGATE ===")
    logger.info("slide_count=%s", len(slides))
    for idx, spec in enumerate(slides, start=1):
        logger.info("[SLIDE %02d] %s", idx, spec.get("title"))

    pml_text = render_pml(slides)
    psl_text = DEFAULT_PSL

    source_path = out_dir / "source_text.txt"
    cleaned_path = out_dir / "cleaned_text.txt"
    units_path = out_dir / "source_units.json"
    slides_path = out_dir / "slide_specs.json"
    pml_path = out_dir / "generated.pml"
    psl_path = out_dir / "corporate.psl"
    report_path = out_dir / "validation_report.json"
    repair_path = out_dir / "repair_log.txt"
    debug_path = out_dir / "debug_state.json"

    source_path.write_text(source_text, encoding="utf-8")
    cleaned_path.write_text(cleaned_text, encoding="utf-8")
    save_json(units_path, units)
    save_json(slides_path, slides)
    pml_path.write_text(pml_text, encoding="utf-8")
    psl_path.write_text(psl_text, encoding="utf-8")
    save_json(report_path, validation)
    repair_path.write_text("\n".join(repairs), encoding="utf-8")
    debug_payload = {
        "document": {
            "source_chars": len(source_text),
            "cleaned_chars": len(cleaned_text),
            "is_legal_doc": is_legal_doc(source_text) or is_legal_doc(cleaned_text),
        },
        "units": units,
        "tasks": units,
        "slides": slides,
        "pml_preview": pml_text[:4000],
        "llm": {
            "mode": llm_mode,
            "called": llm_mode == "gemini",
            "task_llm_call_count": len(units) if llm_mode == "gemini" else 0,
            "model": state.get("model", "gemini-2.0-flash"),
        },
        "validation": validation,
        "repairs": repairs,
    }
    save_json(debug_path, debug_payload)
    logger.info("artifacts: source=%s cleaned=%s units=%s slides=%s pml=%s", source_path, cleaned_path, units_path, slides_path, pml_path)
    logger.info("debug_state=%s validation_report=%s repair_log=%s", debug_path, report_path, repair_path)

    state.update({
        "source_text": source_text,
        "cleaned_text": cleaned_text,
        "source_outline": units,
        "task_plan": {"tasks": units},
        "completed_tasks": slides,
        "pml_text": pml_text,
        "psl_text": psl_text,
        "source_text_path": str(source_path),
        "cleaned_text_path": str(cleaned_path),
        "outline_path": str(units_path),
        "task_plan_path": str(units_path),
        "pml_path": str(pml_path),
        "psl_path": str(psl_path),
        "validation_report_path": str(report_path),
        "repair_log_path": str(repair_path),
        "debug_state_path": str(debug_path),
        "pipeline_log_path": str(out_dir / "pipeline.log"),
        "validation_reports": validation,
        "repair_log": repairs,
    })
    state = render_outputs_if_possible(state)
    logger.info("=== PIPELINE END ===")
    if state.get("html_path"):
        logger.info("HTML=%s", state.get("html_path"))
    if state.get("pptx_path"):
        logger.info("PPTX=%s", state.get("pptx_path"))
    if state.get("md_path"):
        logger.info("MD=%s", state.get("md_path"))
    return state


class SimpleGraph:
    def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return run_pipeline(dict(state))


def build_graph() -> SimpleGraph:
    """Streamlit-compatible graph object.

    This intentionally exposes the same API as LangGraph's compiled graph:
    `build_graph().invoke(initial_state)`. A single deterministic graph node is
    used because article tasks must all be processed, not consumed through a
    state-mutation loop.
    """
    return SimpleGraph()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--renderer", default="")
    parser.add_argument("--out-dir", default="out_legal_article_final")
    parser.add_argument("--mock", action="store_true", help="Alias for --llm-mode mock")
    parser.add_argument("--llm-mode", choices=["gemini", "heuristic", "mock"], default="gemini", help="gemini calls LLM for every article; heuristic/mock are explicit non-LLM modes")
    # Accepted for compatibility with Streamlit/past pipelines.
    parser.add_argument("--max-chunk-chars", type=int, default=12000)
    parser.add_argument("--max-task-chars", type=int, default=7000)
    parser.add_argument("--model", default="gemini-2.0-flash")
    parser.add_argument("--report-ontology", default="")
    parser.add_argument("--presentation-ontology", default="")
    parser.add_argument("--layout-registry", default="")
    parser.add_argument("--debug", action="store_true", help="Write detailed pipeline.log/debug_state.json. Logs are written by default; this flag is kept for compatibility.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    state = run_pipeline({
        "input_path": args.input,
        "input_text": args.text,
        "use_stdin": args.stdin,
        "renderer_path": args.renderer,
        "out_dir": args.out_dir,
        "mock": args.mock,
        "llm_mode": "mock" if args.mock else args.llm_mode,
        "max_chunk_chars": args.max_chunk_chars,
        "max_task_chars": args.max_task_chars,
        "model": args.model,
        "report_ontology_path": args.report_ontology,
        "presentation_ontology_path": args.presentation_ontology,
        "layout_registry_path": args.layout_registry,
        "debug": args.debug,
        "log_level": args.log_level,
    })
    print(f"PML: {state.get('pml_path')}")
    print(f"PSL: {state.get('psl_path')}")
    print(f"LOG: {state.get('pipeline_log_path')}")
    print(f"DEBUG: {state.get('debug_state_path')}")
    if state.get("pptx_path"):
        print(f"PPTX: {state.get('pptx_path')}")
    if state.get("html_path"):
        print(f"HTML: {state.get('html_path')}")


if __name__ == "__main__":
    main()
