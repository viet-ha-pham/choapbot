#!/usr/bin/env python3
"""
LangGraph PDF -> Gemini -> JSON slide plan -> Jinja PML -> PML/PSL renderer demo
================================================================================

Purpose
-------
This demo turns a PDF into presentation slides by separating the pipeline into
multiple LangGraph nodes:

    1. validate_input
    2. summarize_pdf_with_gemini
    3. normalize_slide_plan
    4. render_pml_with_jinja
    5. write_pml_psl_pcl_files
    6. render_with_existing_renderer

Key idea
--------
The LLM does NOT directly write final PML. It returns a structured JSON slide
plan. Jinja2 then inserts that JSON into a controlled PML template. This makes
PML stable, predictable, and easier to validate.

Install
-------
    pip install -U langgraph google-genai jinja2 python-pptx

Run with mock data
------------------
    python demo_langgraph_pdf_gemini_jinja_pml.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_demo \
      --mock

Run with Gemini
---------------
    export GEMINI_API_KEY="your-key"
    python demo_langgraph_pdf_gemini_jinja_pml.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_demo

Notes
-----
- The renderer must expose parse_pml, parse_psl, build_render_ir, render_html,
  render_pptx. If it also exposes parse_pcl/apply_constraints, this script will
  use them when a PCL file exists.
- This script writes generated.pml, corporate.psl, safe-layouts.pcl,
  output.html, output.pptx into --out-dir.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from jinja2 import Environment, StrictUndefined


# -----------------------------------------------------------------------------
# LangGraph imports are inside a helper so --help and static inspection remain
# friendly even before dependencies are installed.
# -----------------------------------------------------------------------------


def require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: langgraph. Install with: pip install -U langgraph"
        ) from exc
    return END, START, StateGraph


# -----------------------------------------------------------------------------
# State schema
# -----------------------------------------------------------------------------


class PipelineState(TypedDict, total=False):
    pdf_path: str
    renderer_path: str
    out_dir: str
    model: str
    mock: bool

    raw_llm_text: str
    slide_plan: Dict[str, Any]
    normalized_plan: Dict[str, Any]

    pml_text: str
    psl_text: str
    pcl_text: str

    pml_path: str
    psl_path: str
    pcl_path: str
    html_path: str
    pptx_path: str

    errors: List[str]


# -----------------------------------------------------------------------------
# Prompt + template
# -----------------------------------------------------------------------------


SLIDE_PLAN_SCHEMA = {
    "presentation_title": "string",
    "subtitle": "string",
    "author": "string",
    "sections": [
        {
            "title": "string",
            "subtitle": "string",
            "slides": [
                {
                    "title": "string",
                    "layout": "title-bullets | title-table | title-image | hero-image",
                    "subtitle": "string",
                    "bullets": ["string"],
                    "conclusion": "string",
                    "table": {
                        "headers": ["string"],
                        "rows": [["string"]]
                    },
                    "speaker_notes": "string"
                }
            ]
        }
    ]
}


GEMINI_PROMPT = f"""
Bạn là chuyên gia technical writing. Hãy đọc PDF và tóm tắt thành kế hoạch slide.

Yêu cầu:
- Chỉ trả JSON hợp lệ, không markdown fence.
- Không viết PML trực tiếp.
- Mỗi slide ngắn, practical, dễ đưa lên PowerPoint.
- Ưu tiên các layout: title-bullets, title-table.
- Mỗi section nên có subtitle ngắn.
- Mỗi slide title-bullets nên có 3-6 bullets.
- Nếu có bảng trong tài liệu, tạo 1 slide title-table.
- Thêm conclusion ngắn cho slide quan trọng.

Schema mong muốn:
{json.dumps(SLIDE_PLAN_SCHEMA, ensure_ascii=False, indent=2)}
""".strip()


PML_TEMPLATE = r'''presentation "{{ presentation_title }}":
  meta:
    author: "{{ author }}"
    language: vi
    format: pptx

  use style: "corporate.psl"
  use constraints: "safe-layouts.pcl"

  cover_layout: title-slide
  cover:
    subtitle: "{{ subtitle }}"
    author: "{{ author }}"

{% for section in sections %}
  section "{{ section.title }}":
    header_layout: section-header
    header:
      subtitle: "{{ section.subtitle }}"

{% for slide in section.slides %}
    slide "{{ slide.title }}":
      layout: {{ slide.layout }}
      intent: generated_from_pdf

      title:
        {{ slide.title }}
{% if slide.subtitle %}

      subtitle:
        {{ slide.subtitle }}
{% endif %}
{% if slide.layout == "title-table" and slide.table %}

      table:
        headers: [{{ slide.table.headers | join(', ') }}]
        rows:
{% for row in slide.table.rows %}
          - [{{ row | join(', ') }}]
{% endfor %}
{% else %}

      bullets:
        icon: check
        level_icons: [check, arrow, dot]
        overflow: paginate
        items:
{% for bullet in slide.bullets %}
          - text: {{ bullet }}
{% endfor %}
{% endif %}
{% if slide.conclusion %}

      conclusion:
        icon: star
        text: {{ slide.conclusion }}
{% endif %}
{% if slide.speaker_notes %}

      notes:
        {{ slide.speaker_notes }}
{% endif %}

{% endfor %}
{% endfor %}
'''


DEFAULT_PSL = r'''theme "corporate":
  page:
    size: widescreen
    background: "#FFFFFF"

  colors:
    primary: "#003A8C"
    secondary: "#E6F0FF"
    text: "#1F1F1F"
    muted: "#666666"
    white: "#FFFFFF"
    card: "#F8FAFC"
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
      position: [90, 210]
      width: 1100
      height: 90
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
      size: 20
      color: white
      position: [95, 430]
      width: 800
      height: 40

  section.section-header:
    background: secondary
    title:
      font: heading
      size: 44
      color: primary
      position: [90, 250]
      width: 1100
      height: 90
      bold: true
      align: center
    subtitle:
      font: body
      size: 24
      color: text
      position: [140, 350]
      width: 1000
      height: 60
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
    subtitle:
      font: body
      size: 18
      color: muted
      position: [65, 112]
      width: 1100
      height: 42
    bullets:
      font: body
      size: 23
      color: text
      position: [90, 170]
      width: 1050
      height: 310
      line_gap: 10
      overflow: paginate
      max_lines: 9
      min_size: 15
    conclusion:
      position: [80, 555]
      width: 1120
      height: 92
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
      height: 75
      bold: true
      align: center
    subtitle:
      font: body
      size: 18
      color: muted
      position: [80, 112]
      width: 1080
      height: 38
      align: center
    table:
      position: [70, 165]
      width: 1140
      height: 430
      font: body
      size: 17
      align: center
      header_fill: primary
      header_color: white
      border_color: "#CBD5E1"
      cell_fill: "#FFFFFF"
      overflow: paginate
      max_rows_per_slide: 10
'''


DEFAULT_PCL = r'''pcl "safe-layouts":
  default:
    text:
      overflow: shrink
      min_size: 13
    bullets:
      overflow: paginate
      max_lines: 9
      min_size: 14
    table:
      overflow: paginate
      max_rows_per_slide: 10
    image:
      mode: contain

  layout "title-bullets":
    bullets:
      overflow: paginate
      max_lines: 9
      min_size: 14
'''


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def json_from_llm_text(text: str) -> Dict[str, Any]:
    """Extract and parse JSON even if the model accidentally wraps it."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def quote_pml_text(value: Any) -> str:
    """Return a PML-safe scalar-ish string.

    We avoid quoting every text line because multiline PML text blocks are more
    readable. The colon-safe renderer parser should support colons in text
    blocks. We still normalize line breaks.
    """
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_cell(value: Any) -> str:
    # PML inline list item. Quote if comma/bracket/colon would confuse list parsing.
    s = "" if value is None else str(value).strip()
    s = s.replace('"', "'")
    if any(ch in s for ch in [",", "[", "]", ":"]):
        return f'"{s}"'
    return s


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# -----------------------------------------------------------------------------
# LangGraph nodes
# -----------------------------------------------------------------------------


def validate_input(state: PipelineState) -> Dict[str, Any]:
    errors: List[str] = []
    pdf_path = Path(state["pdf_path"]).expanduser().resolve()
    renderer_path = Path(state["renderer_path"]).expanduser().resolve()
    out_dir = Path(state["out_dir"]).expanduser().resolve()

    if not pdf_path.exists() and not state.get("mock"):
        errors.append(f"PDF not found: {pdf_path}")
    if not renderer_path.exists():
        errors.append(f"Renderer not found: {renderer_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    if errors:
        raise FileNotFoundError("; ".join(errors))

    return {
        "pdf_path": str(pdf_path),
        "renderer_path": str(renderer_path),
        "out_dir": str(out_dir),
        "errors": [],
    }


def summarize_pdf_with_gemini(state: PipelineState) -> Dict[str, Any]:
    if state.get("mock"):
        mock_json = {
            "presentation_title": "Tóm tắt tài liệu PDF",
            "subtitle": "Demo sinh slide bằng LangGraph + Gemini + Jinja + PML",
            "author": "Auto Summary Pipeline",
            "sections": [
                {
                    "title": "Tổng quan",
                    "subtitle": "Các ý chính được trích xuất từ tài liệu",
                    "slides": [
                        {
                            "title": "Vấn đề chính",
                            "layout": "title-bullets",
                            "subtitle": "Tóm tắt theo hướng thực dụng",
                            "bullets": [
                                "Tài liệu có nhiều nội dung cần cô đọng thành slide.",
                                "LLM chỉ sinh JSON slide plan, không sinh PML trực tiếp.",
                                "Jinja kiểm soát khung PML để giảm lỗi cú pháp.",
                                "Renderer nhận PML/PSL/PCL và sinh HTML/PPTX.",
                            ],
                            "conclusion": "Tách JSON plan khỏi PML giúp pipeline ổn định và dễ kiểm soát hơn.",
                            "speaker_notes": "Nhấn mạnh vai trò của template trong việc chuẩn hóa đầu ra.",
                        },
                        {
                            "title": "Bảng pipeline",
                            "layout": "title-table",
                            "subtitle": "Các bước chính trong LangGraph",
                            "table": {
                                "headers": ["Bước", "Node", "Kết quả"],
                                "rows": [
                                    ["1", "validate_input", "Kiểm tra file và thư mục"],
                                    ["2", "summarize_pdf_with_gemini", "JSON slide plan"],
                                    ["3", "render_pml_with_jinja", "generated.pml"],
                                    ["4", "render_with_existing_renderer", "HTML/PPTX"],
                                ],
                            },
                            "bullets": [],
                            "conclusion": "Pipeline nhiều node giúp dễ debug từng bước.",
                            "speaker_notes": "Có thể thêm node QA, validation hoặc human review sau này.",
                        },
                    ],
                }
            ],
        }
        return {"raw_llm_text": json.dumps(mock_json, ensure_ascii=False, indent=2)}

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY. Set it or run with --mock.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: google-genai. Install with: pip install -U google-genai"
        ) from exc

    client = genai.Client(api_key=api_key)
    pdf_path = state["pdf_path"]

    # File API path is robust for real PDFs. Some SDK versions also support
    # inline bytes; File API keeps the graph state smaller.
    uploaded = client.files.upload(file=pdf_path)

    response = client.models.generate_content(
        model=state.get("model", "gemini-2.5-flash"),
        contents=[uploaded, GEMINI_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return {"raw_llm_text": response.text or "{}"}


def normalize_slide_plan(state: PipelineState) -> Dict[str, Any]:
    plan = json_from_llm_text(state["raw_llm_text"])

    normalized: Dict[str, Any] = {
        "presentation_title": quote_pml_text(plan.get("presentation_title") or "Tóm tắt tài liệu"),
        "subtitle": quote_pml_text(plan.get("subtitle") or "Sinh tự động từ PDF"),
        "author": quote_pml_text(plan.get("author") or "Gemini + LangGraph"),
        "sections": [],
    }

    for sec in ensure_list(plan.get("sections")):
        if not isinstance(sec, dict):
            continue
        nsec = {
            "title": quote_pml_text(sec.get("title") or "Nội dung chính"),
            "subtitle": quote_pml_text(sec.get("subtitle") or ""),
            "slides": [],
        }
        for sl in ensure_list(sec.get("slides")):
            if not isinstance(sl, dict):
                continue
            layout = sl.get("layout") or "title-bullets"
            if layout not in {"title-bullets", "title-table", "title-image", "hero-image"}:
                layout = "title-bullets"

            bullets = [quote_pml_text(x.get("text", x) if isinstance(x, dict) else x) for x in ensure_list(sl.get("bullets"))]
            table = sl.get("table") if isinstance(sl.get("table"), dict) else None
            if layout == "title-table" and not table:
                layout = "title-bullets"
            if layout == "title-table" and table:
                table = {
                    "headers": [normalize_cell(x) for x in ensure_list(table.get("headers"))],
                    "rows": [[normalize_cell(c) for c in ensure_list(row)] for row in ensure_list(table.get("rows"))],
                }

            nsec["slides"].append(
                {
                    "title": quote_pml_text(sl.get("title") or "Slide"),
                    "layout": layout,
                    "subtitle": quote_pml_text(sl.get("subtitle") or ""),
                    "bullets": bullets[:12],
                    "conclusion": quote_pml_text(sl.get("conclusion") or ""),
                    "table": table,
                    "speaker_notes": quote_pml_text(sl.get("speaker_notes") or sl.get("notes") or ""),
                }
            )
        if nsec["slides"]:
            normalized["sections"].append(nsec)

    if not normalized["sections"]:
        normalized["sections"].append(
            {
                "title": "Tổng quan",
                "subtitle": "Fallback",
                "slides": [
                    {
                        "title": "Không có nội dung hợp lệ",
                        "layout": "title-bullets",
                        "subtitle": "Fallback slide",
                        "bullets": ["LLM không trả về slide plan hợp lệ."],
                        "conclusion": "Cần kiểm tra lại prompt hoặc nội dung PDF.",
                        "table": None,
                        "speaker_notes": "",
                    }
                ],
            }
        )

    return {"slide_plan": plan, "normalized_plan": normalized}


def render_pml_with_jinja(state: PipelineState) -> Dict[str, Any]:
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=False)
    template = env.from_string(PML_TEMPLATE)
    pml = template.render(**state["normalized_plan"])
    return {"pml_text": pml, "psl_text": DEFAULT_PSL, "pcl_text": DEFAULT_PCL}


def write_pml_psl_pcl_files(state: PipelineState) -> Dict[str, Any]:
    out_dir = Path(state["out_dir"])
    pml_path = out_dir / "generated.pml"
    psl_path = out_dir / "corporate.psl"
    pcl_path = out_dir / "safe-layouts.pcl"

    pml_path.write_text(state["pml_text"], encoding="utf-8")
    psl_path.write_text(state["psl_text"], encoding="utf-8")
    pcl_path.write_text(state["pcl_text"], encoding="utf-8")

    return {"pml_path": str(pml_path), "psl_path": str(psl_path), "pcl_path": str(pcl_path)}


def load_renderer(renderer_path: str):
    path = Path(renderer_path).resolve()
    spec = importlib.util.spec_from_file_location("pml_renderer_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load renderer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pml_renderer_runtime"] = module
    spec.loader.exec_module(module)
    return module


def render_with_existing_renderer(state: PipelineState) -> Dict[str, Any]:
    renderer = load_renderer(state["renderer_path"])

    pml_text = Path(state["pml_path"]).read_text(encoding="utf-8")
    psl_text = Path(state["psl_path"]).read_text(encoding="utf-8")
    pcl_text = Path(state["pcl_path"]).read_text(encoding="utf-8")

    doc = renderer.parse_pml(pml_text)
    theme = renderer.parse_psl(psl_text)
    render_ir = renderer.build_render_ir(doc, theme)

    # Optional PCL support, depending on which renderer version is passed.
    if hasattr(renderer, "parse_pcl") and hasattr(renderer, "apply_constraints"):
        constraints = renderer.parse_pcl(pcl_text)
        render_ir = renderer.apply_constraints(render_ir, constraints)

    out_dir = Path(state["out_dir"])
    html_path = out_dir / "output.html"
    pptx_path = out_dir / "output.pptx"

    renderer.render_html(render_ir, str(html_path))
    renderer.render_pptx(render_ir, str(pptx_path))

    return {"html_path": str(html_path), "pptx_path": str(pptx_path)}


# -----------------------------------------------------------------------------
# Graph assembly
# -----------------------------------------------------------------------------


def build_graph():
    END, START, StateGraph = require_langgraph()
    graph = StateGraph(PipelineState)

    graph.add_node("validate_input", validate_input)
    graph.add_node("summarize_pdf_with_gemini", summarize_pdf_with_gemini)
    graph.add_node("normalize_slide_plan", normalize_slide_plan)
    graph.add_node("render_pml_with_jinja", render_pml_with_jinja)
    graph.add_node("write_pml_psl_pcl_files", write_pml_psl_pcl_files)
    graph.add_node("render_with_existing_renderer", render_with_existing_renderer)

    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "summarize_pdf_with_gemini")
    graph.add_edge("summarize_pdf_with_gemini", "normalize_slide_plan")
    graph.add_edge("normalize_slide_plan", "render_pml_with_jinja")
    graph.add_edge("render_pml_with_jinja", "write_pml_psl_pcl_files")
    graph.add_edge("write_pml_psl_pcl_files", "render_with_existing_renderer")
    graph.add_edge("render_with_existing_renderer", END)

    return graph.compile()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PDF -> Gemini JSON slide plan -> Jinja PML -> PML renderer via LangGraph"
    )
    parser.add_argument("pdf", help="Input PDF path. In --mock mode, it can be a dummy path.")
    parser.add_argument("--renderer", default="demo_dsl_conclusion_box.py", help="Path to renderer .py")
    parser.add_argument("--out-dir", default="out_langgraph_pml", help="Output directory")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--mock", action="store_true", help="Skip Gemini and use mock slide plan")
    parser.add_argument("--print-state", action="store_true", help="Print selected final state fields")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = build_graph()
    final_state = app.invoke(
        {
            "pdf_path": args.pdf,
            "renderer_path": args.renderer,
            "out_dir": args.out_dir,
            "model": args.model,
            "mock": args.mock,
        }
    )

    print("Generated:")
    print("- PML :", final_state.get("pml_path"))
    print("- PSL :", final_state.get("psl_path"))
    print("- PCL :", final_state.get("pcl_path"))
    print("- HTML:", final_state.get("html_path"))
    print("- PPTX:", final_state.get("pptx_path"))

    if args.print_state:
        keep = {
            k: final_state.get(k)
            for k in ["pml_path", "psl_path", "pcl_path", "html_path", "pptx_path", "normalized_plan"]
        }
        print(json.dumps(keep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
