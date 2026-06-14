#!/usr/bin/env python3
"""
Ontology-guided PDF -> Gemini -> Slide plan -> Jinja PML -> Renderer pipeline
==============================================================================

This demo extends the previous LangGraph pipeline with three planning assets:

1. Report domain ontology
   - Defines business/reporting headings such as executive summary, context,
     findings, risks, recommendations, roadmap.
   - The LLM is asked to summarize the PDF according to these ontology concepts.

2. Slide creation ontology
   - Defines content signals, slide intents, planning heuristics, and constraints.
   - The planner uses it to decide what each slide is trying to do.

3. Layout registry JSON
   - Describes layouts supported by the renderer.
   - Each layout declares supported blocks, intended use, capacity, and selection
     signals, so layout choice is data-driven instead of hardcoded only.

Pipeline nodes
--------------
    validate_input
      -> load_planning_assets
      -> summarize_pdf_with_gemini_or_mock
      -> normalize_report_summary
      -> validate_summary -> repair_summary
      -> plan_slides_from_ontology_and_registry
      -> validate_slide_plan -> repair_slide_plan
      -> render_pml_with_jinja
      -> validate_pml -> repair_pml
      -> write_outputs
      -> render_with_existing_renderer

Install
-------
    pip install -U langgraph google-genai jinja2 python-pptx

Run mock
--------
    python demo_ontology_langgraph_pdf_to_pml.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_ontology_demo \
      --mock

Run with Gemini
---------------
    export GEMINI_API_KEY="your-key"
    python demo_ontology_langgraph_pdf_to_pml.py input.pdf \
      --renderer demo_dsl_conclusion_box.py \
      --out-dir out_ontology_demo

Outputs
-------
    generated.pml
    corporate.psl
    safe-layouts.pcl
    report_domain_ontology.json
    slide_creation_ontology.json
    layout_registry.json
    slide_plan.json
    validation_report.json
    repair_log.txt
    output.html
    output.pptx
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from jinja2 import Environment, StrictUndefined


# -----------------------------------------------------------------------------
# Optional LangGraph import helper
# -----------------------------------------------------------------------------


def require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Missing dependency: langgraph. Install with: pip install -U langgraph") from exc
    return END, START, StateGraph


# -----------------------------------------------------------------------------
# Pipeline state
# -----------------------------------------------------------------------------


class PipelineState(TypedDict, total=False):
    pdf_path: str
    renderer_path: str
    out_dir: str
    model: str
    mock: bool

    report_ontology_path: str
    slide_ontology_path: str
    layout_registry_path: str

    report_ontology: Dict[str, Any]
    slide_ontology: Dict[str, Any]
    layout_registry: Dict[str, Any]

    raw_llm_text: str
    report_summary: Dict[str, Any]
    normalized_summary: Dict[str, Any]
    slide_plan: Dict[str, Any]

    pml_text: str
    psl_text: str
    pcl_text: str

    pml_path: str
    psl_path: str
    pcl_path: str
    slide_plan_path: str
    html_path: str
    pptx_path: str

    validation_reports: List[Dict[str, Any]]
    repair_log: List[str]
    report_summary_issues: List[Dict[str, Any]]
    slide_plan_issues: List[Dict[str, Any]]
    pml_issues: List[Dict[str, Any]]
    pml_validation_ok: bool


# -----------------------------------------------------------------------------
# Default planning assets
# -----------------------------------------------------------------------------


DEFAULT_REPORT_ONTOLOGY: Dict[str, Any] = {
    "ontology_id": "report-domain-ontology-v0.1",
    "description": "Ontology nghiệp vụ báo cáo dùng để buộc LLM tóm tắt theo các đề mục nghiệp vụ ổn định.",
    "concepts": [
        {
            "id": "executive_summary",
            "preferred_heading": "Tóm tắt điều hành",
            "intent": "summarize_core_message",
            "extraction_goal": "Nêu thông điệp chính, kết luận lớn, và ý nghĩa quản trị.",
            "max_bullets": 5,
        },
        {
            "id": "context",
            "preferred_heading": "Bối cảnh và vấn đề",
            "intent": "explain_context",
            "extraction_goal": "Nêu bối cảnh, vấn đề hiện tại, nguyên nhân hoặc động lực phát sinh báo cáo.",
            "max_bullets": 6,
        },
        {
            "id": "findings",
            "preferred_heading": "Phát hiện chính",
            "intent": "present_findings",
            "extraction_goal": "Liệt kê các phát hiện, số liệu, xu hướng, điểm bất thường, bằng chứng quan trọng.",
            "max_bullets": 7,
        },
        {
            "id": "comparison",
            "preferred_heading": "So sánh / đối chiếu",
            "intent": "compare_options",
            "extraction_goal": "Nếu tài liệu có các phương án, nhóm đối tượng hoặc giai đoạn, trích xuất so sánh thành bảng.",
            "max_rows": 6,
        },
        {
            "id": "risks",
            "preferred_heading": "Rủi ro và điểm cần chú ý",
            "intent": "highlight_risks",
            "extraction_goal": "Nêu rủi ro, hạn chế, giả định yếu, điểm cần kiểm tra thêm.",
            "max_bullets": 6,
        },
        {
            "id": "recommendations",
            "preferred_heading": "Khuyến nghị",
            "intent": "recommend_actions",
            "extraction_goal": "Đề xuất hành động, ưu tiên triển khai, quyết định cần đưa ra.",
            "max_bullets": 6,
        },
        {
            "id": "roadmap",
            "preferred_heading": "Lộ trình / bước tiếp theo",
            "intent": "show_progression",
            "extraction_goal": "Tách các bước theo thời gian hoặc theo trình tự triển khai nếu có.",
            "max_milestones": 6,
        },
    ],
    "output_contract": {
        "report_title": "string",
        "subtitle": "string",
        "author": "string",
        "sections": [
            {
                "concept_id": "one concept id above",
                "heading": "preferred heading or custom heading",
                "summary": "one paragraph",
                "bullets": ["short bullet"],
                "table": {"headers": ["col"], "rows": [["cell"]]},
                "milestones": [{"date": "Q1", "heading": "Step", "text": "Detail"}],
                "conclusion": "short takeaway",
                "evidence": ["short source clue, no long quote"],
            }
        ],
    },
}


DEFAULT_SLIDE_ONTOLOGY: Dict[str, Any] = {
    "ontology_id": "slide-creation-ontology-v0.1",
    "description": "Ontology nghiệp vụ tạo slide: mô tả intent, tín hiệu nội dung và heuristic chọn layout.",
    "concepts": {
        "SlideIntent": [
            "cover",
            "section_divider",
            "summarize_core_message",
            "explain_context",
            "present_findings",
            "compare_options",
            "highlight_risks",
            "recommend_actions",
            "show_progression",
        ],
        "ContentSignal": [
            "has_bullets",
            "has_table",
            "has_milestones",
            "has_many_items",
            "has_takeaway",
            "needs_emphasis",
        ],
        "SlideObject": ["title", "subtitle", "bullets", "table", "timeline", "conclusion", "notes"],
    },
    "planning_rules": [
        {
            "name": "table_first",
            "when": {"has_table": True},
            "prefer_intent": "compare_options",
            "layout_candidates": ["title-table"],
        },
        {
            "name": "milestones_to_timeline",
            "when": {"has_milestones": True},
            "prefer_intent": "show_progression",
            "layout_candidates": ["timeline", "stair-progress", "title-bullets"],
        },
        {
            "name": "bullets_default",
            "when": {"has_bullets": True},
            "layout_candidates": ["title-bullets"],
        },
        {
            "name": "many_items_grid",
            "when": {"has_many_items": True},
            "layout_candidates": ["grid-4", "grid-6", "title-bullets"],
        },
    ],
    "quality_constraints": {
        "max_bullets_per_slide": 6,
        "max_table_rows_per_slide": 8,
        "prefer_conclusion_box_for": ["executive_summary", "recommendations", "risks"],
        "split_long_sections": True,
    },
}


DEFAULT_LAYOUT_REGISTRY: Dict[str, Any] = {
    "registry_id": "pml-renderer-layout-registry-v0.1",
    "description": "Registry mô tả layout renderer hỗ trợ, intent phù hợp, block input, capacity, và tín hiệu lựa chọn.",
    "layouts": [
        {
            "id": "title-bullets",
            "family": "content",
            "intent": ["summarize_core_message", "explain_context", "present_findings", "highlight_risks", "recommend_actions"],
            "supported_blocks": ["title", "subtitle", "bullets", "conclusion", "notes", "footer"],
            "content_capacity": {"bullets_min": 2, "bullets_max": 7, "supports_nested_bullets": True, "supports_icon_bullets": True},
            "selection_signals": ["has_bullets", "has_takeaway", "no_table"],
            "score": 0.75,
        },
        {
            "id": "title-table",
            "family": "data",
            "intent": ["compare_options", "present_findings", "summarize_metrics"],
            "supported_blocks": ["title", "subtitle", "table", "conclusion", "notes"],
            "content_capacity": {"rows_max": 10, "columns_max": 5},
            "selection_signals": ["has_table", "comparison", "matrix"],
            "score": 0.95,
        },
        {
            "id": "timeline",
            "family": "process",
            "intent": ["show_progression", "show_roadmap"],
            "supported_blocks": ["title", "subtitle", "milestones", "notes"],
            "content_capacity": {"milestones_min": 3, "milestones_max": 6},
            "selection_signals": ["has_milestones", "time_sequence", "roadmap"],
            "fallback": "title-bullets",
            "score": 0.9,
        },
        {
            "id": "grid-4",
            "family": "cards",
            "intent": ["present_findings", "group_items"],
            "supported_blocks": ["title", "subtitle", "cells", "notes"],
            "content_capacity": {"cells": 4},
            "selection_signals": ["has_many_items", "grouped_items"],
            "fallback": "title-bullets",
            "score": 0.65,
        },
        {
            "id": "grid-6",
            "family": "cards",
            "intent": ["present_findings", "group_items"],
            "supported_blocks": ["title", "subtitle", "cells", "notes"],
            "content_capacity": {"cells": 6},
            "selection_signals": ["has_many_items", "grouped_items"],
            "fallback": "title-bullets",
            "score": 0.6,
        },
    ],
}


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

  slide.timeline:
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
    timeline:
      position: [90, 170]
      width: 1100
      height: 420
      line_color: primary
      marker_fill: primary
      card_fill: "#FFFFFF"
      border_color: "#CBD5E1"
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
# Prompt + Jinja template
# -----------------------------------------------------------------------------


def build_gemini_prompt(report_ontology: Dict[str, Any]) -> str:
    return f"""
Bạn là chuyên gia phân tích báo cáo và technical writing.
Hãy đọc PDF và tóm tắt theo ontology nghiệp vụ báo cáo được cung cấp.

Nguyên tắc:
- Chỉ trả JSON hợp lệ, không markdown fence.
- Không viết PML.
- Bám vào các concept trong ontology; nếu concept không có trong tài liệu thì có thể bỏ qua.
- Mỗi bullet ngắn, có ý nghĩa nghiệp vụ.
- Nếu có so sánh/số liệu/bảng, đưa vào field table.
- Nếu có trình tự thời gian/lộ trình, đưa vào field milestones.
- Không bịa số liệu. Nếu không chắc, viết thận trọng.

REPORT_DOMAIN_ONTOLOGY:
{json.dumps(report_ontology, ensure_ascii=False, indent=2)}

Hãy trả đúng output_contract trong ontology.
""".strip()


PML_TEMPLATE = r'''presentation "{{ plan.presentation_title | pml_inline }}":
  meta:
    author: "{{ plan.author | pml_inline }}"
    language: vi
    format: pptx

  use style: "corporate.psl"
  use constraints: "safe-layouts.pcl"

  cover_layout: title-slide
  cover:
    subtitle: "{{ plan.subtitle | pml_inline }}"
    author: "{{ plan.author | pml_inline }}"

{% for section in plan.sections %}
  section "{{ section.title | pml_inline }}":
    header_layout: section-header
    header:
      subtitle: "{{ section.subtitle | pml_inline }}"

{% for slide in section.slides %}
    slide "{{ slide.title | pml_inline }}":
      layout: {{ slide.layout }}
      intent: {{ slide.intent }}

      title:
{{ slide.title | pml_block(8) }}
{% if slide.subtitle %}

      subtitle:
{{ slide.subtitle | pml_block(8) }}
{% endif %}
{% if slide.layout == "title-table" and slide.table %}

      table:
        headers: [{{ slide.table.headers | pml_list }}]
        rows:
{% for row in slide.table.rows %}
          - [{{ row | pml_list }}]
{% endfor %}
{% elif slide.layout == "timeline" and slide.milestones %}

      milestones:
{% for ms in slide.milestones %}
        - date: "{{ ms.date | pml_inline }}"
          heading: "{{ ms.heading | pml_inline }}"
          text: "{{ ms.text | pml_inline }}"
{% endfor %}
{% else %}

      bullets:
        icon: check
        level_icons: [check, arrow, dot]
        overflow: paginate
        items:
{% for bullet in slide.bullets %}
          - text: "{{ bullet | pml_inline }}"
{% endfor %}
{% endif %}
{% if slide.conclusion %}

      conclusion:
        icon: star
        text: "{{ slide.conclusion | pml_inline }}"
{% endif %}
{% if slide.notes %}

      notes:
{{ slide.notes | pml_block(8) }}
{% endif %}

{% endfor %}
{% endfor %}
'''


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


def json_from_llm_text(text: str) -> Dict[str, Any]:
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


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def clean_text(value: Any, max_len: Optional[int] = None) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"[ \t]+", " ", s)
    if max_len and len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def pml_inline(value: Any) -> str:
    s = clean_text(value)
    # PML quoted scalar: keep it simple and safe.
    return s.replace('"', "'").replace("\n", " ")


def pml_block(value: Any, indent: int = 8) -> str:
    s = clean_text(value)
    pad = " " * indent
    if not s:
        return pad
    return "\n".join(pad + line for line in s.split("\n"))


def pml_list(values: Any) -> str:
    items = []
    for v in ensure_list(values):
        s = pml_inline(v)
        # Quote all cells to avoid comma/colon parser ambiguity.
        items.append(f'"{s}"')
    return ", ".join(items)


def write_json_if_missing(path: Path, data: Dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# Validation + repair helpers
# -----------------------------------------------------------------------------

Issue = Dict[str, Any]


def issue(stage: str, severity: str, code: str, message: str, path: str = "", fix_hint: str = "") -> Issue:
    return {
        "stage": stage,
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
        "fix_hint": fix_hint,
    }


def append_report(state: PipelineState, stage: str, issues: List[Issue], repaired: bool = False) -> Dict[str, Any]:
    reports = list(state.get("validation_reports") or [])
    reports.append(
        {
            "stage": stage,
            "ok": not any(i.get("severity") == "error" for i in issues),
            "repaired": repaired,
            "issues": issues,
        }
    )
    return {"validation_reports": reports}


def has_errors(issues: List[Issue]) -> bool:
    return any(i.get("severity") == "error" for i in issues)


def validate_summary_object(summary: Dict[str, Any], report_ontology: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    concept_ids = {c.get("id") for c in report_ontology.get("concepts", [])}

    for key in ["report_title", "sections"]:
        if key not in summary:
            issues.append(issue("summary", "error", "missing_key", f"Missing required key: {key}", key))

    if not isinstance(summary.get("sections"), list) or not summary.get("sections"):
        issues.append(issue("summary", "error", "empty_sections", "Summary must contain at least one section", "sections"))
        return issues

    for i, sec in enumerate(summary.get("sections", [])):
        path = f"sections[{i}]"
        if not isinstance(sec, dict):
            issues.append(issue("summary", "error", "section_not_object", "Section must be an object", path))
            continue
        concept_id = sec.get("concept_id")
        if concept_id not in concept_ids:
            issues.append(issue("summary", "warning", "unknown_concept", f"Unknown concept_id: {concept_id}", f"{path}.concept_id", "Map to findings"))
        has_content = bool(sec.get("summary") or sec.get("bullets") or sec.get("table") or sec.get("milestones"))
        if not has_content:
            issues.append(issue("summary", "error", "empty_section_content", "Section has no usable content", path))
        if sec.get("table") is not None:
            tbl = sec.get("table")
            if not isinstance(tbl, dict) or not tbl.get("headers") or not tbl.get("rows"):
                issues.append(issue("summary", "warning", "bad_table", "Table must have headers and rows", f"{path}.table", "Drop malformed table"))
    return issues


def repair_summary_object(summary: Dict[str, Any], report_ontology: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    repaired = dict(summary or {})
    logs: List[str] = []
    concept_ids = {c.get("id") for c in report_ontology.get("concepts", [])}

    if not repaired.get("report_title"):
        repaired["report_title"] = "Tóm tắt báo cáo"
        logs.append("summary: filled missing report_title")
    repaired["subtitle"] = clean_text(repaired.get("subtitle") or "Tạo slide tự động có kiểm tra chất lượng", 140)
    repaired["author"] = clean_text(repaired.get("author") or "Gemini + LangGraph", 80)

    sections = []
    for sec in ensure_list(repaired.get("sections")):
        if not isinstance(sec, dict):
            logs.append("summary: dropped non-object section")
            continue
        concept_id = clean_text(sec.get("concept_id") or "findings")
        if concept_id not in concept_ids:
            sec["concept_id"] = "findings"
            logs.append(f"summary: mapped unknown concept {concept_id!r} to findings")
        if not sec.get("heading"):
            sec["heading"] = sec.get("concept_id", "findings").replace("_", " ").title()
            logs.append("summary: filled missing section heading")
        if not (sec.get("summary") or sec.get("bullets") or sec.get("table") or sec.get("milestones")):
            sec["bullets"] = ["Cần kiểm tra lại nội dung trích xuất cho phần này."]
            logs.append(f"summary: inserted fallback bullet for section {sec.get('heading')}")
        if sec.get("table") is not None and normalize_table(sec.get("table")) is None:
            sec.pop("table", None)
            logs.append(f"summary: dropped malformed table in section {sec.get('heading')}")
        sections.append(sec)

    if not sections:
        sections = [
            {
                "concept_id": "executive_summary",
                "heading": "Tóm tắt điều hành",
                "summary": "Không có nội dung hợp lệ từ LLM.",
                "bullets": ["Cần kiểm tra lại PDF hoặc prompt."],
                "conclusion": "Pipeline đã tạo fallback slide.",
                "evidence": [],
            }
        ]
        logs.append("summary: created fallback section")

    repaired["sections"] = sections
    return repaired, logs


def validate_slide_plan_object(plan: Dict[str, Any], registry: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    layouts = get_layouts_by_id(registry)

    if not plan.get("presentation_title"):
        issues.append(issue("slide_plan", "error", "missing_title", "Missing presentation_title", "presentation_title"))
    if not isinstance(plan.get("sections"), list) or not plan.get("sections"):
        issues.append(issue("slide_plan", "error", "no_sections", "Slide plan has no sections", "sections"))
        return issues

    for si, sec in enumerate(plan.get("sections", [])):
        spath = f"sections[{si}]"
        if not sec.get("title"):
            issues.append(issue("slide_plan", "warning", "missing_section_title", "Section title is empty", f"{spath}.title"))
        slides = sec.get("slides")
        if not isinstance(slides, list) or not slides:
            issues.append(issue("slide_plan", "error", "section_without_slides", "Section must have at least one slide", f"{spath}.slides"))
            continue
        for li, sl in enumerate(slides):
            path = f"{spath}.slides[{li}]"
            layout = sl.get("layout")
            if layout not in layouts:
                issues.append(issue("slide_plan", "error", "unsupported_layout", f"Unsupported layout: {layout}", f"{path}.layout", "Fallback to title-bullets"))
            else:
                supported = set(layouts[layout].get("supported_blocks", []))
                block_map = {
                    "bullets": bool(sl.get("bullets")),
                    "table": bool(sl.get("table")),
                    "milestones": bool(sl.get("milestones")),
                    "conclusion": bool(sl.get("conclusion")),
                    "subtitle": bool(sl.get("subtitle")),
                }
                for block, present in block_map.items():
                    if present and block not in supported and not (block == "milestones" and "timeline" in supported):
                        issues.append(issue("slide_plan", "warning", "unsupported_block", f"Layout {layout} may not support block {block}", f"{path}.{block}"))
            if not sl.get("title"):
                issues.append(issue("slide_plan", "warning", "missing_slide_title", "Slide title is empty", f"{path}.title"))
            if layout == "title-table" and not sl.get("table"):
                issues.append(issue("slide_plan", "error", "table_layout_without_table", "title-table needs table", path))
            if layout == "timeline" and not sl.get("milestones"):
                issues.append(issue("slide_plan", "error", "timeline_without_milestones", "timeline needs milestones", path))
    return issues


def repair_slide_plan_object(plan: Dict[str, Any], registry: Dict[str, Any], slide_ontology: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    repaired = json.loads(json.dumps(plan, ensure_ascii=False))
    logs: List[str] = []
    layouts = get_layouts_by_id(registry)
    fallback = "title-bullets" if "title-bullets" in layouts else next(iter(layouts.keys()), "title-bullets")
    max_bullets = int(slide_ontology.get("quality_constraints", {}).get("max_bullets_per_slide", 6))

    repaired["presentation_title"] = clean_text(repaired.get("presentation_title") or "Tóm tắt báo cáo", 100)
    repaired["subtitle"] = clean_text(repaired.get("subtitle") or "Đã qua validate/repair", 140)
    repaired["author"] = clean_text(repaired.get("author") or "Gemini + LangGraph", 80)

    new_sections = []
    for sec in ensure_list(repaired.get("sections")):
        if not isinstance(sec, dict):
            logs.append("plan: dropped non-object section")
            continue
        sec["title"] = clean_text(sec.get("title") or "Nội dung", 90)
        sec["subtitle"] = clean_text(sec.get("subtitle") or "", 160)
        new_slides = []
        for sl in ensure_list(sec.get("slides")):
            if not isinstance(sl, dict):
                logs.append(f"plan: dropped non-object slide in section {sec['title']}")
                continue
            sl["title"] = clean_text(sl.get("title") or sec["title"], 90)
            layout = sl.get("layout") or fallback
            if layout not in layouts:
                sl["layout"] = fallback
                logs.append(f"plan: changed unsupported layout {layout!r} to {fallback!r}")
            if sl.get("layout") == "title-table" and not sl.get("table"):
                sl["layout"] = fallback
                sl["bullets"] = sl.get("bullets") or [sl.get("subtitle") or sl["title"]]
                logs.append("plan: converted empty title-table to title-bullets")
            if sl.get("layout") == "timeline" and not sl.get("milestones"):
                sl["layout"] = fallback
                sl["bullets"] = sl.get("bullets") or [sl.get("subtitle") or sl["title"]]
                logs.append("plan: converted empty timeline to title-bullets")

            # Deterministic splitting for very large bullet lists before PML rendering.
            if sl.get("layout") == fallback and len(ensure_list(sl.get("bullets"))) > max_bullets:
                bullets = [clean_text(b.get("text", b) if isinstance(b, dict) else b, 130) for b in ensure_list(sl.get("bullets"))]
                for idx, chunk in enumerate(split_bullets(bullets, max_bullets)):
                    clone = dict(sl)
                    clone["bullets"] = chunk
                    if idx > 0:
                        clone["title"] = f"{sl['title']} (tiếp {idx + 1})"
                        clone["conclusion"] = ""
                    new_slides.append(clone)
                logs.append(f"plan: split long bullet slide {sl['title']!r}")
            else:
                new_slides.append(sl)
        if not new_slides:
            new_slides.append({"title": sec["title"], "layout": fallback, "intent": "present_findings", "bullets": ["Không có slide hợp lệ; đã tạo fallback."]})
            logs.append(f"plan: created fallback slide for section {sec['title']}")
        sec["slides"] = new_slides
        new_sections.append(sec)

    if not new_sections:
        new_sections.append({"title": "Tóm tắt", "subtitle": "", "slides": [{"title": "Tóm tắt", "layout": fallback, "intent": "present_findings", "bullets": ["Không có kế hoạch slide hợp lệ."]}]})
        logs.append("plan: created fallback section")

    repaired["sections"] = new_sections
    return repaired, logs


def validate_pml_with_renderer(pml_text: str, psl_text: str, pcl_text: str, renderer_path: str) -> List[Issue]:
    issues: List[Issue] = []
    try:
        renderer = load_renderer(renderer_path)
        doc = renderer.parse_pml(pml_text)
        theme = renderer.parse_psl(psl_text)
        render_ir = renderer.build_render_ir(doc, theme)
        if hasattr(renderer, "parse_pcl") and hasattr(renderer, "apply_constraints"):
            constraints = renderer.parse_pcl(pcl_text)
            render_ir = renderer.apply_constraints(render_ir, constraints)
        if not render_ir.get("slides"):
            issues.append(issue("pml", "error", "no_render_slides", "Renderer produced no slides", "render_ir.slides"))
    except Exception as exc:
        issues.append(issue("pml", "error", "renderer_parse_failed", f"Renderer failed to parse/build IR: {exc}", "pml_text"))
    return issues


# -----------------------------------------------------------------------------
# Planning logic
# -----------------------------------------------------------------------------


def get_layouts_by_id(registry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {layout["id"]: layout for layout in registry.get("layouts", []) if isinstance(layout, dict) and layout.get("id")}


def registry_supports(registry: Dict[str, Any], layout_id: str) -> bool:
    return layout_id in get_layouts_by_id(registry)


def detect_signals(section: Dict[str, Any]) -> Dict[str, bool]:
    bullets = ensure_list(section.get("bullets"))
    table = section.get("table") if isinstance(section.get("table"), dict) else None
    milestones = ensure_list(section.get("milestones"))
    concept_id = section.get("concept_id", "")
    return {
        "has_bullets": bool(bullets),
        "has_table": bool(table and table.get("headers") and table.get("rows")),
        "has_milestones": bool(milestones),
        "has_many_items": len(bullets) >= 7,
        "has_takeaway": bool(section.get("conclusion")),
        "comparison": concept_id == "comparison",
        "roadmap": concept_id == "roadmap",
        "no_table": not bool(table),
    }


def choose_layout(section: Dict[str, Any], slide_ontology: Dict[str, Any], registry: Dict[str, Any]) -> str:
    signals = detect_signals(section)
    layouts = get_layouts_by_id(registry)

    # Rule-based selection using ontology rules + registry availability.
    for rule in slide_ontology.get("planning_rules", []):
        when = rule.get("when", {})
        if all(signals.get(k) == v for k, v in when.items()):
            for candidate in rule.get("layout_candidates", []):
                if candidate in layouts:
                    # Avoid choosing unsupported specialized layouts if renderer passed later is older;
                    # fallback remains available via registry.
                    return candidate

    if signals["has_table"] and "title-table" in layouts:
        return "title-table"
    if signals["has_milestones"] and "timeline" in layouts:
        return "timeline"
    return "title-bullets"


def split_bullets(bullets: List[str], max_bullets: int) -> List[List[str]]:
    if not bullets:
        return [[]]
    return [bullets[i : i + max_bullets] for i in range(0, len(bullets), max_bullets)]


def normalize_table(table: Any, max_rows: int = 8) -> Optional[Dict[str, Any]]:
    if not isinstance(table, dict):
        return None
    headers = [clean_text(x, 40) for x in ensure_list(table.get("headers"))]
    rows = []
    for row in ensure_list(table.get("rows"))[:max_rows]:
        rows.append([clean_text(c, 60) for c in ensure_list(row)])
    if not headers or not rows:
        return None
    return {"headers": headers[:5], "rows": [r[:5] for r in rows]}


def make_slide_from_section(
    section: Dict[str, Any],
    layout: str,
    intent: str,
    bullets: Optional[List[str]] = None,
    suffix: str = "",
) -> Dict[str, Any]:
    title = clean_text(section.get("heading") or section.get("title") or "Nội dung", 80)
    if suffix:
        title = f"{title} {suffix}"
    return {
        "title": title,
        "subtitle": clean_text(section.get("summary") or "", 140),
        "layout": layout,
        "intent": intent or "present_findings",
        "bullets": bullets if bullets is not None else [clean_text(x, 120) for x in ensure_list(section.get("bullets"))],
        "table": normalize_table(section.get("table")),
        "milestones": [
            {
                "date": clean_text(ms.get("date") or ms.get("time") or "", 20),
                "heading": clean_text(ms.get("heading") or ms.get("title") or "Mốc", 50),
                "text": clean_text(ms.get("text") or ms.get("description") or "", 90),
            }
            for ms in ensure_list(section.get("milestones"))
            if isinstance(ms, dict)
        ][:6],
        "conclusion": clean_text(section.get("conclusion") or "", 180),
        "notes": "; ".join(clean_text(x, 120) for x in ensure_list(section.get("evidence"))[:3]),
    }


# -----------------------------------------------------------------------------
# LangGraph nodes
# -----------------------------------------------------------------------------


def validate_input(state: PipelineState) -> Dict[str, Any]:
    pdf_path = Path(state["pdf_path"]).expanduser().resolve()
    renderer_path = Path(state["renderer_path"]).expanduser().resolve()
    out_dir = Path(state["out_dir"]).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not state.get("mock") and not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not renderer_path.exists():
        raise FileNotFoundError(f"Renderer not found: {renderer_path}")

    return {"pdf_path": str(pdf_path), "renderer_path": str(renderer_path), "out_dir": str(out_dir)}


def load_planning_assets(state: PipelineState) -> Dict[str, Any]:
    out_dir = Path(state["out_dir"])

    report_path = Path(state.get("report_ontology_path") or out_dir / "report_domain_ontology.json").resolve()
    slide_path = Path(state.get("slide_ontology_path") or out_dir / "slide_creation_ontology.json").resolve()
    registry_path = Path(state.get("layout_registry_path") or out_dir / "layout_registry.json").resolve()

    report_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    write_json_if_missing(report_path, DEFAULT_REPORT_ONTOLOGY)
    write_json_if_missing(slide_path, DEFAULT_SLIDE_ONTOLOGY)
    write_json_if_missing(registry_path, DEFAULT_LAYOUT_REGISTRY)

    return {
        "report_ontology_path": str(report_path),
        "slide_ontology_path": str(slide_path),
        "layout_registry_path": str(registry_path),
        "report_ontology": load_json(report_path),
        "slide_ontology": load_json(slide_path),
        "layout_registry": load_json(registry_path),
    }


def summarize_pdf_with_gemini_or_mock(state: PipelineState) -> Dict[str, Any]:
    if state.get("mock"):
        mock_summary = {
            "report_title": "Báo cáo tổng hợp tri thức doanh nghiệp",
            "subtitle": "Tóm tắt theo ontology báo cáo và lập kế hoạch slide bằng registry layout",
            "author": "Ontology Slide Planner",
            "sections": [
                {
                    "concept_id": "executive_summary",
                    "heading": "Tóm tắt điều hành",
                    "summary": "Báo cáo nhấn mạnh nhu cầu chuyển từ tổng hợp thủ công sang pipeline tri thức tự động.",
                    "bullets": [
                        "Dữ liệu nằm rải rác ở nhiều nguồn và khó khai thác đồng bộ.",
                        "Quy trình báo cáo thủ công làm tăng độ trễ trong ra quyết định.",
                        "Cần một pipeline tóm tắt, truy xuất và sinh slide có kiểm soát.",
                        "Ontology giúp chuẩn hóa tiêu đề nghiệp vụ và giảm tùy tiện trong tóm tắt.",
                    ],
                    "conclusion": "Giá trị chính là biến tài liệu dài thành slide có cấu trúc và có thể kiểm soát.",
                    "evidence": ["Các phần mô tả vấn đề phân tán dữ liệu và nhu cầu tổng hợp nhanh."],
                },
                {
                    "concept_id": "comparison",
                    "heading": "So sánh phương án triển khai",
                    "summary": "Có thể so sánh cách làm thủ công và cách dùng pipeline tự động.",
                    "table": {
                        "headers": ["Tiêu chí", "Thủ công", "Pipeline tự động"],
                        "rows": [
                            ["Tốc độ", "Chậm, phụ thuộc người tổng hợp", "Nhanh hơn nhờ tự động hóa"],
                            ["Tính nhất quán", "Dễ lệch cấu trúc", "Theo ontology và template"],
                            ["Khả năng mở rộng", "Khó mở rộng", "Có thể mở rộng theo layout registry"],
                        ],
                    },
                    "conclusion": "Pipeline tự động phù hợp khi cần lặp lại nhiều báo cáo với cấu trúc ổn định.",
                    "evidence": ["Các yêu cầu về renderer, registry và ontology trong đề bài."],
                },
                {
                    "concept_id": "roadmap",
                    "heading": "Lộ trình triển khai",
                    "summary": "Lộ trình nên đi từ chuẩn hóa ontology đến tự động hóa renderer.",
                    "milestones": [
                        {"date": "B1", "heading": "Ontology báo cáo", "text": "Xác định đề mục nghiệp vụ và mục tiêu trích xuất."},
                        {"date": "B2", "heading": "Registry layout", "text": "Mô tả layout, intent, capacity và block hỗ trợ."},
                        {"date": "B3", "heading": "Planner", "text": "Chọn layout theo tín hiệu nội dung và ontology tạo slide."},
                        {"date": "B4", "heading": "Renderer", "text": "Sinh PML bằng Jinja và render HTML/PPTX."},
                    ],
                    "conclusion": "Tách ontology, registry và renderer giúp hệ thống dễ nâng cấp.",
                    "evidence": ["Luồng LangGraph nhiều node trong pipeline."],
                },
                {
                    "concept_id": "risks",
                    "heading": "Rủi ro và điểm cần chú ý",
                    "summary": "Các rủi ro chính nằm ở chất lượng trích xuất, chọn layout và overflow văn bản.",
                    "bullets": [
                        "LLM có thể bỏ sót nội dung nếu prompt không bám ontology.",
                        "Layout registry sai hoặc thiếu sẽ làm planner chọn layout không phù hợp.",
                        "Text dài có thể gây overflow nếu constraint không đủ chặt.",
                        "Bảng lớn cần pagination hoặc rút gọn trước khi render.",
                    ],
                    "conclusion": "Cần validation sau mỗi node thay vì chỉ kiểm tra ở cuối pipeline.",
                    "evidence": ["Các lỗi từng gặp: colon text, subtitle overlap, pagination chưa đủ."],
                },
            ],
        }
        return {"raw_llm_text": json.dumps(mock_summary, ensure_ascii=False, indent=2)}

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY. Set it or run with --mock.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Missing dependency: google-genai. Install with: pip install -U google-genai") from exc

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(file=state["pdf_path"])
    prompt = build_gemini_prompt(state["report_ontology"])

    response = client.models.generate_content(
        model=state.get("model", "gemini-2.5-flash"),
        contents=[uploaded, prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.15),
    )
    return {"raw_llm_text": response.text or "{}"}


def normalize_report_summary(state: PipelineState) -> Dict[str, Any]:
    raw = json_from_llm_text(state["raw_llm_text"])
    concept_ids = {c.get("id") for c in state["report_ontology"].get("concepts", [])}

    normalized = {
        "report_title": clean_text(raw.get("report_title") or raw.get("presentation_title") or "Tóm tắt báo cáo", 100),
        "subtitle": clean_text(raw.get("subtitle") or "Sinh slide theo ontology và layout registry", 140),
        "author": clean_text(raw.get("author") or "Gemini + LangGraph", 80),
        "sections": [],
    }

    for sec in ensure_list(raw.get("sections")):
        if not isinstance(sec, dict):
            continue
        concept_id = clean_text(sec.get("concept_id") or "findings")
        if concept_id not in concept_ids:
            concept_id = "findings"
        normalized["sections"].append(
            {
                "concept_id": concept_id,
                "heading": clean_text(sec.get("heading") or sec.get("title") or concept_id.replace("_", " ").title(), 90),
                "summary": clean_text(sec.get("summary") or "", 220),
                "bullets": [clean_text(x.get("text", x) if isinstance(x, dict) else x, 130) for x in ensure_list(sec.get("bullets"))],
                "table": normalize_table(sec.get("table"), max_rows=10),
                "milestones": ensure_list(sec.get("milestones")),
                "conclusion": clean_text(sec.get("conclusion") or "", 180),
                "evidence": [clean_text(x, 140) for x in ensure_list(sec.get("evidence"))],
            }
        )

    if not normalized["sections"]:
        normalized["sections"].append(
            {
                "concept_id": "executive_summary",
                "heading": "Tóm tắt điều hành",
                "summary": "Không có nội dung hợp lệ từ LLM.",
                "bullets": ["Cần kiểm tra lại PDF hoặc prompt."],
                "table": None,
                "milestones": [],
                "conclusion": "Pipeline đã tạo fallback slide.",
                "evidence": [],
            }
        )

    return {"report_summary": raw, "normalized_summary": normalized}


def plan_slides_from_ontology_and_registry(state: PipelineState) -> Dict[str, Any]:
    summary = state["normalized_summary"]
    slide_ontology = state["slide_ontology"]
    registry = state["layout_registry"]
    constraints = slide_ontology.get("quality_constraints", {})
    max_bullets = int(constraints.get("max_bullets_per_slide", 6))

    plan = {
        "presentation_title": summary["report_title"],
        "subtitle": summary["subtitle"],
        "author": summary["author"],
        "sections": [],
    }

    for sec in summary["sections"]:
        intent = sec.get("concept_id", "present_findings")
        layout = choose_layout(sec, slide_ontology, registry)
        # Use fallback if layout exists in registry but renderer may not support it. The pipeline is
        # registry-driven; users can edit registry to match their renderer exactly.

        section_node = {
            "title": sec["heading"],
            "subtitle": sec.get("summary") or "",
            "slides": [],
        }

        if layout == "title-table" and sec.get("table"):
            section_node["slides"].append(make_slide_from_section(sec, "title-table", intent))
        elif layout == "timeline" and sec.get("milestones"):
            section_node["slides"].append(make_slide_from_section(sec, "timeline", intent))
        else:
            bullet_chunks = split_bullets(sec.get("bullets") or [sec.get("summary") or sec["heading"]], max_bullets)
            for idx, chunk in enumerate(bullet_chunks):
                suffix = "" if idx == 0 else f"(tiếp {idx + 1})"
                slide = make_slide_from_section(sec, "title-bullets", intent, bullets=chunk, suffix=suffix)
                if idx > 0:
                    slide["conclusion"] = ""
                section_node["slides"].append(slide)

        plan["sections"].append(section_node)

    return {"slide_plan": plan}



def validate_summary_node(state: PipelineState) -> Dict[str, Any]:
    issues = validate_summary_object(state["normalized_summary"], state["report_ontology"])
    update = append_report(state, "summary", issues, repaired=False)
    update["report_summary_issues"] = issues
    return update


def repair_summary_node(state: PipelineState) -> Dict[str, Any]:
    issues = state.get("report_summary_issues") or []
    if not issues:
        return {"repair_log": list(state.get("repair_log") or [])}
    repaired, logs = repair_summary_object(state["normalized_summary"], state["report_ontology"])
    update = append_report(state, "summary_repair", validate_summary_object(repaired, state["report_ontology"]), repaired=True)
    update["normalized_summary"] = repaired
    update["repair_log"] = list(state.get("repair_log") or []) + logs
    return update


def validate_slide_plan_node(state: PipelineState) -> Dict[str, Any]:
    issues = validate_slide_plan_object(state["slide_plan"], state["layout_registry"])
    update = append_report(state, "slide_plan", issues, repaired=False)
    update["slide_plan_issues"] = issues
    return update


def repair_slide_plan_node(state: PipelineState) -> Dict[str, Any]:
    issues = state.get("slide_plan_issues") or []
    if not issues:
        return {"repair_log": list(state.get("repair_log") or [])}
    repaired, logs = repair_slide_plan_object(state["slide_plan"], state["layout_registry"], state["slide_ontology"])
    update = append_report(state, "slide_plan_repair", validate_slide_plan_object(repaired, state["layout_registry"]), repaired=True)
    update["slide_plan"] = repaired
    update["repair_log"] = list(state.get("repair_log") or []) + logs
    return update


def validate_pml_node(state: PipelineState) -> Dict[str, Any]:
    issues = validate_pml_with_renderer(state["pml_text"], state["psl_text"], state["pcl_text"], state["renderer_path"])
    update = append_report(state, "pml", issues, repaired=False)
    update["pml_issues"] = issues
    update["pml_validation_ok"] = not has_errors(issues)
    return update


def repair_pml_node(state: PipelineState) -> Dict[str, Any]:
    # Most PML syntax errors come from unsafe/generated strings. Regenerate PML from the already repaired slide_plan.
    issues = state.get("pml_issues") or []
    if not issues:
        return {"repair_log": list(state.get("repair_log") or [])}
    regenerated = render_pml_with_jinja(state)
    repaired_issues = validate_pml_with_renderer(regenerated["pml_text"], regenerated["psl_text"], regenerated["pcl_text"], state["renderer_path"])
    update = append_report(state, "pml_repair", repaired_issues, repaired=True)
    update.update(regenerated)
    update["pml_issues"] = repaired_issues
    update["pml_validation_ok"] = not has_errors(repaired_issues)
    update["repair_log"] = list(state.get("repair_log") or []) + ["pml: regenerated from sanitized slide_plan"]
    return update

def render_pml_with_jinja(state: PipelineState) -> Dict[str, Any]:
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=False)
    env.filters["pml_inline"] = pml_inline
    env.filters["pml_block"] = pml_block
    env.filters["pml_list"] = pml_list
    template = env.from_string(PML_TEMPLATE)
    pml_text = template.render(plan=state["slide_plan"])
    return {"pml_text": pml_text, "psl_text": DEFAULT_PSL, "pcl_text": DEFAULT_PCL}


def write_outputs(state: PipelineState) -> Dict[str, Any]:
    out_dir = Path(state["out_dir"])
    pml_path = out_dir / "generated.pml"
    psl_path = out_dir / "corporate.psl"
    pcl_path = out_dir / "safe-layouts.pcl"
    slide_plan_path = out_dir / "slide_plan.json"
    validation_report_path = out_dir / "validation_report.json"
    repair_log_path = out_dir / "repair_log.txt"

    pml_path.write_text(state["pml_text"], encoding="utf-8")
    psl_path.write_text(state["psl_text"], encoding="utf-8")
    pcl_path.write_text(state["pcl_text"], encoding="utf-8")
    slide_plan_path.write_text(json.dumps(state["slide_plan"], ensure_ascii=False, indent=2), encoding="utf-8")
    validation_report_path.write_text(json.dumps(state.get("validation_reports") or [], ensure_ascii=False, indent=2), encoding="utf-8")
    repair_log_path.write_text("\n".join(state.get("repair_log") or []), encoding="utf-8")

    return {
        "pml_path": str(pml_path),
        "psl_path": str(psl_path),
        "pcl_path": str(pcl_path),
        "slide_plan_path": str(slide_plan_path),
        "validation_report_path": str(validation_report_path),
        "repair_log_path": str(repair_log_path),
    }


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
    doc = renderer.parse_pml(Path(state["pml_path"]).read_text(encoding="utf-8"))
    theme = renderer.parse_psl(Path(state["psl_path"]).read_text(encoding="utf-8"))
    render_ir = renderer.build_render_ir(doc, theme)

    if hasattr(renderer, "parse_pcl") and hasattr(renderer, "apply_constraints"):
        constraints = renderer.parse_pcl(Path(state["pcl_path"]).read_text(encoding="utf-8"))
        render_ir = renderer.apply_constraints(render_ir, constraints)

    out_dir = Path(state["out_dir"])
    html_path = out_dir / "output.html"
    pptx_path = out_dir / "output.pptx"
    renderer.render_html(render_ir, str(html_path))
    renderer.render_pptx(render_ir, str(pptx_path))
    return {"html_path": str(html_path), "pptx_path": str(pptx_path)}


# -----------------------------------------------------------------------------
# Graph
# -----------------------------------------------------------------------------


def build_graph():
    END, START, StateGraph = require_langgraph()
    graph = StateGraph(PipelineState)
    graph.add_node("validate_input", validate_input)
    graph.add_node("load_planning_assets", load_planning_assets)
    graph.add_node("summarize_pdf_with_gemini_or_mock", summarize_pdf_with_gemini_or_mock)
    graph.add_node("normalize_report_summary", normalize_report_summary)
    graph.add_node("validate_summary", validate_summary_node)
    graph.add_node("repair_summary", repair_summary_node)
    graph.add_node("plan_slides_from_ontology_and_registry", plan_slides_from_ontology_and_registry)
    graph.add_node("validate_slide_plan", validate_slide_plan_node)
    graph.add_node("repair_slide_plan", repair_slide_plan_node)
    graph.add_node("render_pml_with_jinja", render_pml_with_jinja)
    graph.add_node("validate_pml", validate_pml_node)
    graph.add_node("repair_pml", repair_pml_node)
    graph.add_node("write_outputs", write_outputs)
    graph.add_node("render_with_existing_renderer", render_with_existing_renderer)

    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "load_planning_assets")
    graph.add_edge("load_planning_assets", "summarize_pdf_with_gemini_or_mock")
    graph.add_edge("summarize_pdf_with_gemini_or_mock", "normalize_report_summary")
    graph.add_edge("normalize_report_summary", "validate_summary")
    graph.add_edge("validate_summary", "repair_summary")
    graph.add_edge("repair_summary", "plan_slides_from_ontology_and_registry")
    graph.add_edge("plan_slides_from_ontology_and_registry", "validate_slide_plan")
    graph.add_edge("validate_slide_plan", "repair_slide_plan")
    graph.add_edge("repair_slide_plan", "render_pml_with_jinja")
    graph.add_edge("render_pml_with_jinja", "validate_pml")
    graph.add_edge("validate_pml", "repair_pml")
    graph.add_edge("repair_pml", "write_outputs")
    graph.add_edge("write_outputs", "render_with_existing_renderer")
    graph.add_edge("render_with_existing_renderer", END)
    return graph.compile()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ontology-guided PDF -> Gemini -> Jinja PML -> Renderer pipeline")
    parser.add_argument("pdf", help="Input PDF path. In --mock mode, it can be a dummy path.")
    parser.add_argument("--renderer", default="demo_dsl_conclusion_box.py", help="Path to renderer .py")
    parser.add_argument("--out-dir", default="out_ontology_demo", help="Output directory")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--report-ontology", default=None, help="Optional report domain ontology JSON path")
    parser.add_argument("--slide-ontology", default=None, help="Optional slide creation ontology JSON path")
    parser.add_argument("--layout-registry", default=None, help="Optional layout registry JSON path")
    parser.add_argument("--mock", action="store_true", help="Skip Gemini and use built-in mock summary")
    parser.add_argument("--print-plan", action="store_true", help="Print final slide plan JSON")
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
            "report_ontology_path": args.report_ontology,
            "slide_ontology_path": args.slide_ontology,
            "layout_registry_path": args.layout_registry,
        }
    )

    print("Generated:")
    for key in [
        "report_ontology_path",
        "slide_ontology_path",
        "layout_registry_path",
        "slide_plan_path",
        "validation_report_path",
        "repair_log_path",
        "pml_path",
        "psl_path",
        "pcl_path",
        "html_path",
        "pptx_path",
    ]:
        print(f"- {key}: {final_state.get(key)}")

    if args.print_plan:
        print(json.dumps(final_state.get("slide_plan"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
