"""
PDF -> Gemini summary -> PML/PSL/PCL renderer demo
====================================================

This script demonstrates a practical pipeline:

    1. Take one PDF file.
    2. Ask Gemini to summarize it into a structured slide plan.
    3. Convert that slide plan into PML.
    4. Use the existing PML/PSL/PCL renderer to generate HTML and PPTX.

Requirements:

    pip install google-genai python-pptx

Run with Gemini:

    export GEMINI_API_KEY="your-key"
    python demo_pdf_gemini_renderer_pipeline.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_slides

Run without Gemini, using mock data:

    python demo_pdf_gemini_renderer_pipeline.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_slides \
      --mock

Notes:
- This script uses Gemini File API upload for the PDF.
- The renderer file is loaded dynamically, so you can replace it with a newer renderer version.
- Generated files:
    - generated_summary.pml
    - corporate_summary.psl
    - safe_summary.pcl
    - summary_deck.html
    - summary_deck.pptx
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Data model for the Gemini output
# -----------------------------------------------------------------------------


@dataclass
class SummarySlide:
    title: str
    layout: str = "title-bullets"
    subtitle: Optional[str] = None
    bullets: List[str] = field(default_factory=list)
    conclusion: Optional[str] = None
    table: Optional[Dict[str, Any]] = None


@dataclass
class SummaryDeck:
    title: str
    subtitle: str = "Tóm tắt tự động từ PDF bằng Gemini"
    author: str = "Gemini + PML/PSL Renderer"
    sections: Dict[str, List[SummarySlide]] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Renderer loading
# -----------------------------------------------------------------------------


def load_renderer(renderer_path: Path):
    if not renderer_path.exists():
        raise FileNotFoundError(f"Renderer file not found: {renderer_path}")

    spec = importlib.util.spec_from_file_location("pml_psl_renderer", renderer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load renderer module from: {renderer_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["pml_psl_renderer"] = module
    spec.loader.exec_module(module)

    required = ["parse_pml", "parse_psl", "build_render_ir", "render_html", "render_pptx"]
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise RuntimeError(f"Renderer is missing required functions: {missing}")

    return module


# -----------------------------------------------------------------------------
# Gemini call
# -----------------------------------------------------------------------------


def wait_for_gemini_file(client: Any, file_obj: Any, timeout_sec: int = 120) -> Any:
    """Wait until a Gemini uploaded file is active."""
    start = time.time()
    name = file_obj.name

    while True:
        current = client.files.get(name=name)
        state = getattr(current, "state", None)
        state_name = getattr(state, "name", str(state))

        if state_name in {"ACTIVE", "State.ACTIVE"}:
            return current
        if state_name in {"FAILED", "State.FAILED"}:
            raise RuntimeError(f"Gemini file processing failed: {name}")
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"Timed out waiting for Gemini file processing: {name}")

        time.sleep(2)


def build_gemini_prompt(max_slides: int) -> str:
    return f"""
Bạn là chuyên gia tóm tắt tài liệu và thiết kế slide kỹ thuật.

Hãy đọc PDF được cung cấp và tạo nội dung slide tóm tắt bằng tiếng Việt.

Yêu cầu:
- Tối đa {max_slides} slide nội dung, chưa tính title slide.
- Ưu tiên các ý có giá trị kỹ thuật/thực tiễn.
- Mỗi slide nên có 3-6 bullet ngắn.
- Nếu tài liệu có bảng so sánh/kế hoạch/kết quả, hãy tạo đúng 1 slide dạng table nếu phù hợp.
- Mỗi slide có thể có conclusion ngắn ở cuối để nhấn mạnh ý chính.
- Không bịa nguồn, số liệu, thuật ngữ nếu PDF không nói.

Chỉ trả về JSON hợp lệ, không markdown, không giải thích ngoài JSON.
Schema:
{{
  "title": "Tên bộ slide",
  "subtitle": "Một câu mô tả ngắn",
  "author": "Nguồn/tác giả nếu xác định được, nếu không ghi Tóm tắt từ PDF",
  "sections": {{
    "Tên section": [
      {{
        "title": "Tên slide",
        "layout": "title-bullets | title-table",
        "subtitle": "phụ đề nếu cần",
        "bullets": ["ý 1", "ý 2"],
        "conclusion": "kết luận ngắn nếu có",
        "table": {{
          "headers": ["Cột 1", "Cột 2"],
          "rows": [["A", "B"], ["C", "D"]]
        }}
      }}
    ]
  }}
}}
""".strip()


def summarize_pdf_with_gemini(pdf_path: Path, model: str, max_slides: int) -> Dict[str, Any]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Missing dependency. Install with: pip install google-genai") from exc

    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY, or run with --mock")

    client = genai.Client()
    uploaded = client.files.upload(file=pdf_path, config={"mime_type": "application/pdf"})
    uploaded = wait_for_gemini_file(client, uploaded)

    response = client.models.generate_content(
        model=model,
        contents=[uploaded, build_gemini_prompt(max_slides)],
        config={
            "response_mime_type": "application/json",
            "temperature": 0.2,
        },
    )

    return parse_json_response(response.text)


# -----------------------------------------------------------------------------
# JSON parsing and normalization
# -----------------------------------------------------------------------------


def parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = text.strip()

    # Defensive cleanup if model accidentally returns fenced JSON.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini did not return valid JSON. First 500 chars:\n{cleaned[:500]}") from exc

    if not isinstance(data, dict):
        raise ValueError("Expected top-level JSON object")
    return data


def normalize_deck(data: Dict[str, Any]) -> SummaryDeck:
    title = str(data.get("title") or "Tóm tắt PDF")
    subtitle = str(data.get("subtitle") or "Tóm tắt tự động từ PDF bằng Gemini")
    author = str(data.get("author") or "Tóm tắt từ PDF")

    sections_raw = data.get("sections") or {"Tóm tắt": data.get("slides", [])}
    if not isinstance(sections_raw, dict):
        sections_raw = {"Tóm tắt": []}

    sections: Dict[str, List[SummarySlide]] = {}
    for section_name, slides_raw in sections_raw.items():
        section_slides: List[SummarySlide] = []
        if not isinstance(slides_raw, list):
            continue

        for item in slides_raw:
            if not isinstance(item, dict):
                continue

            table = item.get("table") if isinstance(item.get("table"), dict) else None
            layout = str(item.get("layout") or ("title-table" if table else "title-bullets"))
            bullets = item.get("bullets") or []
            bullets = [str(x) for x in bullets if str(x).strip()]

            section_slides.append(
                SummarySlide(
                    title=str(item.get("title") or "Ý chính"),
                    layout=layout,
                    subtitle=str(item["subtitle"]) if item.get("subtitle") else None,
                    bullets=bullets,
                    conclusion=str(item["conclusion"]) if item.get("conclusion") else None,
                    table=table,
                )
            )

        if section_slides:
            sections[str(section_name)] = section_slides

    if not sections:
        sections = {
            "Tóm tắt": [
                SummarySlide(
                    title="Không tạo được nội dung",
                    bullets=["Gemini không trả về slide hợp lệ."],
                    conclusion="Cần kiểm tra lại PDF hoặc prompt.",
                )
            ]
        }

    return SummaryDeck(title=title, subtitle=subtitle, author=author, sections=sections)


def mock_summary() -> Dict[str, Any]:
    return {
        "title": "Demo tóm tắt PDF",
        "subtitle": "Pipeline PDF → Gemini → PML/PSL → PPTX/HTML",
        "author": "Mock data",
        "sections": {
            "Tổng quan": [
                {
                    "title": "Mục tiêu của tài liệu",
                    "layout": "title-bullets",
                    "subtitle": "Các ý chính được rút ra từ PDF",
                    "bullets": [
                        "Tài liệu trình bày một vấn đề nghiệp vụ/kỹ thuật cần được tổng hợp.",
                        "Các khái niệm chính được nhóm lại thành các ý dễ đưa lên slide.",
                        "Nội dung dài được chuyển thành bullet ngắn để phục vụ thuyết trình.",
                    ],
                    "conclusion": "Điểm quan trọng là biến PDF dài thành deck có cấu trúc rõ ràng.",
                },
                {
                    "title": "Bảng tóm tắt đầu ra",
                    "layout": "title-table",
                    "bullets": [],
                    "table": {
                        "headers": ["Thành phần", "Vai trò", "Đầu ra"],
                        "rows": [
                            ["PDF", "Nguồn nội dung", "Tài liệu gốc"],
                            ["Gemini", "Tóm tắt và cấu trúc hóa", "JSON slide plan"],
                            ["Renderer", "Render PML/PSL", "HTML và PPTX"],
                        ],
                    },
                    "conclusion": "Pipeline này có thể dùng làm bản demo nhanh cho hệ thống sinh slide.",
                },
            ],
            "Quy trình": [
                {
                    "title": "Luồng xử lý đề xuất",
                    "layout": "numbered-columns-4",
                    "bullets": [
                        "Upload PDF vào Gemini File API.",
                        "Yêu cầu Gemini trả về JSON theo schema.",
                        "Chuyển JSON thành PML.",
                        "Render HTML/PPTX bằng renderer hiện có.",
                    ],
                    "conclusion": "Tách nội dung, style và constraint giúp pipeline dễ mở rộng.",
                }
            ],
        },
    }


# -----------------------------------------------------------------------------
# PML/PSL/PCL generation
# -----------------------------------------------------------------------------


def pml_quote(value: Any) -> str:
    s = str(value).replace('"', '\\"')
    return f'"{s}"'


def indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def emit_text_block(key: str, value: str, level: int) -> List[str]:
    lines = [" " * level + f"{key}:"]
    for line in str(value).splitlines() or [""]:
        lines.append(" " * (level + 2) + line)
    return lines


def deck_to_pml(deck: SummaryDeck) -> str:
    lines: List[str] = []
    lines.append(f"presentation {pml_quote(deck.title)}:")
    lines.append("  meta:")
    lines.append(f"    author: {pml_quote(deck.author)}")
    lines.append("    language: vi")
    lines.append("    format: pptx")
    lines.append("")
    lines.append('  use style: "corporate_summary.psl"')
    lines.append('  use constraints: "safe_summary.pcl"')
    lines.append("")
    lines.append("  cover_layout: title-slide")
    lines.append("  cover:")
    lines.append(f"    subtitle: {pml_quote(deck.subtitle)}")
    lines.append(f"    author: {pml_quote(deck.author)}")
    lines.append("")

    for section_name, slides in deck.sections.items():
        lines.append(f"  section {pml_quote(section_name)}:")
        lines.append("    header_layout: section-header")
        lines.append("    header:")
        lines.append(f"      subtitle: {pml_quote('Tóm tắt các ý chính')}")
        lines.append("")

        for slide in slides:
            layout = slide.layout
            if slide.table:
                layout = "title-table"
            elif layout not in {"title-bullets", "title-table", "numbered-columns-3", "numbered-columns-4", "numbered-columns-5", "numbered-columns-6"}:
                layout = "title-bullets"

            lines.append(f"    slide {pml_quote(slide.title)}:")
            lines.append(f"      layout: {layout}")
            lines.append("      intent: summarize_pdf")
            lines.append("")
            lines.extend(emit_text_block("title", slide.title, 6))
            if slide.subtitle:
                lines.append("")
                lines.extend(emit_text_block("subtitle", slide.subtitle, 6))

            if slide.table:
                headers = slide.table.get("headers") or []
                rows = slide.table.get("rows") or []
                lines.append("")
                lines.append("      table:")
                lines.append("        headers: [" + ", ".join(str(h) for h in headers) + "]")
                lines.append("        rows:")
                for row in rows:
                    if isinstance(row, list):
                        lines.append("          - [" + ", ".join(str(c) for c in row) + "]")
            else:
                if layout.startswith("numbered-columns"):
                    lines.append("")
                    lines.append("      columns:")
                    for bullet in slide.bullets[:6]:
                        lines.append("        - heading: " + str(bullet)[:50])
                        lines.append("          text: " + str(bullet))
                else:
                    lines.append("")
                    lines.append("      bullets:")
                    lines.append("        icon: check")
                    lines.append("        overflow: paginate")
                    lines.append("        items:")
                    for bullet in slide.bullets:
                        lines.append("          - " + str(bullet))

            if slide.conclusion:
                lines.append("")
                lines.append("      conclusion:")
                lines.append("        icon: check")
                lines.append("        text: " + str(slide.conclusion))

            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def default_psl() -> str:
    return r'''
theme "corporate-summary":
  page:
    size: widescreen
    background: "#FFFFFF"

  colors:
    primary: "#003A8C"
    secondary: "#E6F0FF"
    accent: "#0F766E"
    text: "#1F1F1F"
    muted: "#666666"
    white: "#FFFFFF"
    border: "#CBD5E1"

  fonts:
    heading: "Aptos Display"
    body: "Aptos"

  presentation.title-slide:
    background: primary
    title:
      font: heading
      size: 46
      color: white
      position: [90, 205]
      width: 1100
      height: 95
      bold: true
      align: left
    subtitle:
      font: body
      size: 25
      color: secondary
      position: [95, 315]
      width: 1050
      height: 60
      align: left
    author:
      font: body
      size: 19
      color: white
      position: [95, 430]
      width: 700
      height: 40

  section.section-header:
    background: secondary
    title:
      font: heading
      size: 42
      color: primary
      position: [90, 260]
      width: 1100
      height: 80
      bold: true
      align: center
    subtitle:
      font: body
      size: 22
      color: text
      position: [140, 350]
      width: 1000
      height: 50
      align: center

  slide.title-bullets:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 70
      bold: true
      align: left
    subtitle:
      font: body
      size: 18
      color: muted
      position: [65, 105]
      width: 1080
      height: 42
      italic: true
      align: left
    bullets:
      font: body
      size: 23
      color: text
      icon_color: accent
      position: [90, 165]
      width: 1030
      height: 320
      line_gap: 10
      overflow: paginate
      max_lines: 10
    conclusion:
      position: [80, 555]
      width: 1120
      height: 90
      font: body
      size: 19
      color: text
      fill: "#E0F2FE"
      border: "#0284C7"
      bold: true
      align: center

  slide.title-table:
    title:
      font: heading
      size: 32
      color: primary
      position: [60, 40]
      width: 1120
      height: 70
      bold: true
      align: center
    table:
      position: [70, 145]
      width: 1140
      height: 455
      font: body
      size: 17
      align: center
      header_fill: primary
      header_color: white
      border_color: border
      cell_fill: "#FFFFFF"
    conclusion:
      position: [80, 625]
      width: 1120
      height: 55
      font: body
      size: 16
      color: text
      fill: "#F8FAFC"
      border: border
      align: center

  slide.numbered-columns-4:
    title:
      font: heading
      size: 32
      color: primary
      position: [60, 40]
      width: 1120
      height: 70
      bold: true
      align: center
    numbered_columns:
      position: [70, 155]
      width: 1140
      height: 360
      gap: 22
      circle_fill: primary
      circle_color: white
      fill: "#F8FAFC"
      border: border
      heading_color: primary
      text_color: text
    conclusion:
      position: [80, 560]
      width: 1120
      height: 85
      font: body
      size: 18
      color: text
      fill: "#E0F2FE"
      border: "#0284C7"
      bold: true
      align: center
'''.strip() + "\n"


def default_pcl() -> str:
    return r'''
pcl "summary-safe-layouts":
  default:
    text:
      overflow: shrink
      min_size: 14
      max_lines: 8

    bullets:
      overflow: paginate
      max_lines: 10
      max_items_per_slide: 6
      keep_children_with_parent: true

    table:
      overflow: paginate
      max_rows_per_slide: 9

    image:
      mode: contain

  layout "title-bullets":
    bullets:
      overflow: paginate
      max_items_per_slide: 6
'''.strip() + "\n"


# -----------------------------------------------------------------------------
# Render pipeline
# -----------------------------------------------------------------------------


def render_deck_with_renderer(
    renderer: Any,
    pml_text: str,
    psl_text: str,
    pcl_text: Optional[str],
    output_html: Path,
    output_pptx: Path,
    asset_base_dir: Path,
) -> None:
    doc = renderer.parse_pml(pml_text)
    theme = renderer.parse_psl(psl_text)

    constraints = None
    if pcl_text and hasattr(renderer, "parse_pcl"):
        constraints = renderer.parse_pcl(pcl_text)

    # Newer renderers support constraints in build_render_ir; older ones do not.
    try:
        render_ir = renderer.build_render_ir(doc, theme, constraints)
    except TypeError:
        render_ir = renderer.build_render_ir(doc, theme)
        if constraints is not None and hasattr(renderer, "apply_constraints"):
            render_ir = renderer.apply_constraints(render_ir, constraints)

    try:
        renderer.render_html(render_ir, str(output_html), asset_base_dir=asset_base_dir)
    except TypeError:
        renderer.render_html(render_ir, str(output_html))

    try:
        renderer.render_pptx(render_ir, str(output_pptx), asset_base_dir=asset_base_dir)
    except TypeError:
        renderer.render_pptx(render_ir, str(output_pptx))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize one PDF with Gemini and render slides with PML/PSL renderer.")
    parser.add_argument("pdf", type=Path, help="Input PDF path")
    parser.add_argument("--renderer", type=Path, default=Path("demo_dsl_conclusion_box.py"), help="Renderer .py file")
    parser.add_argument("--out-dir", type=Path, default=Path("out_pdf_summary"), help="Output directory")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--max-slides", type=int, default=6, help="Maximum content slides from Gemini")
    parser.add_argument("--mock", action="store_true", help="Use mock summary instead of Gemini")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    renderer_path = args.renderer
    if not renderer_path.exists():
        # Convenience: look beside this script too.
        candidate = Path(__file__).resolve().parent / renderer_path.name
        if candidate.exists():
            renderer_path = candidate

    renderer = load_renderer(renderer_path)

    if args.mock:
        raw = mock_summary()
    else:
        if not args.pdf.exists():
            raise FileNotFoundError(args.pdf)
        raw = summarize_pdf_with_gemini(args.pdf, model=args.model, max_slides=args.max_slides)

    deck = normalize_deck(raw)
    pml_text = deck_to_pml(deck)
    psl_text = default_psl()
    pcl_text = default_pcl()

    pml_path = args.out_dir / "generated_summary.pml"
    psl_path = args.out_dir / "corporate_summary.psl"
    pcl_path = args.out_dir / "safe_summary.pcl"
    html_path = args.out_dir / "summary_deck.html"
    pptx_path = args.out_dir / "summary_deck.pptx"
    json_path = args.out_dir / "gemini_summary.json"

    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    pml_path.write_text(pml_text, encoding="utf-8")
    psl_path.write_text(psl_text, encoding="utf-8")
    pcl_path.write_text(pcl_text, encoding="utf-8")

    render_deck_with_renderer(
        renderer=renderer,
        pml_text=pml_text,
        psl_text=psl_text,
        pcl_text=pcl_text,
        output_html=html_path,
        output_pptx=pptx_path,
        asset_base_dir=args.out_dir,
    )

    print("Generated:")
    print(f"- {json_path}")
    print(f"- {pml_path}")
    print(f"- {psl_path}")
    print(f"- {pcl_path}")
    print(f"- {html_path}")
    print(f"- {pptx_path}")


if __name__ == "__main__":
    main()
