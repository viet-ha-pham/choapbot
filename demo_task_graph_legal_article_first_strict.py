#!/usr/bin/env python3
"""
Long-report scalable LangGraph slide pipeline with legal-preamble fix
===========================================

This version separates the three knowledge sources clearly:

1. Report/document ontology
   - Used ONLY for administrative boilerplate filtering and heading/content classification.
   - It never creates missing sections and never dictates deck structure.

2. Presentation ontology
   - Used to infer rhetorical/presentation intent for an existing document unit.
   - It says what kind of communication act a slide should perform: summarize, compare,
     show timeline, warn, recommend, assign tasks, etc.

3. Layout registry
   - Used to select a renderer-supported layout based on intent + data shape/capacity.
   - It is a capability registry, not a domain ontology.

Pipeline shape
--------------
    input text / PDF / DOCX
      -> clean administrative boilerplate
      -> split long document into safe chunks by heading/token budget
      -> extract local outline per chunk
      -> merge local outlines into a global source outline
      -> classify outline headings with report ontology
      -> analyze hierarchy / section policy
      -> create independent slide tasks from real document units
      -> summarize/shape each task independently
      -> validate / repair / skip empty tasks
      -> aggregate slide specs into PML
      -> validate / repair PML/deck
      -> renderer outputs HTML / PPTX / Markdown if supported

Long-document guarantees
------------------------
    - Never relies on one giant prompt for the whole report.
    - Keeps per-chunk artifacts on disk for audit/cache.
    - Preserves source heading order.
    - Ontology remains advisory/classificatory, not structure-generating.
    - Empty/boilerplate sections are skipped before slide generation.

Install
-------
    pip install -U langgraph google-genai jinja2 python-pptx python-docx pypdf

Mock run
--------
    python demo_task_graph_clean_ontology_roles.py \
      --text "I. Đặc điểm tình hình\nNội dung...\nII. Kết quả nổi bật\nNội dung..." \
      --renderer demo_dsl_inline_markdown_styles.py \
      --out-dir out_clean_roles \
      --mock

Gemini run
----------
    export GEMINI_API_KEY="your-key"
    python demo_task_graph_clean_ontology_roles.py input.docx \
      --renderer demo_dsl_inline_markdown_styles.py \
      --out-dir out_clean_roles
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from jinja2 import Environment, StrictUndefined


def require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Missing dependency: langgraph. Install with: pip install -U langgraph") from exc
    return END, START, StateGraph


class PipelineState(TypedDict, total=False):
    input_path: str
    input_text: str
    use_stdin: bool
    renderer_path: str
    out_dir: str
    model: str
    mock: bool

    report_ontology_path: str
    presentation_ontology_path: str
    layout_registry_path: str
    template_pptx_path: str
    infer_template_style: bool
    extract_template_background: bool

    report_ontology: Dict[str, Any]
    presentation_ontology: Dict[str, Any]
    layout_registry: Dict[str, Any]

    source_text: str
    cleaned_text: str
    source_outline: List[Dict[str, Any]]
    classified_outline: List[Dict[str, Any]]
    hierarchy: Dict[str, Any]
    section_groups: List[Dict[str, Any]]

    task_plan: Dict[str, Any]
    pending_tasks: List[Dict[str, Any]]
    current_task: Dict[str, Any]
    current_task_output: Dict[str, Any]
    completed_tasks: List[Dict[str, Any]]

    pml_text: str
    psl_text: str

    source_text_path: str
    cleaned_text_path: str
    outline_path: str
    classified_outline_path: str
    hierarchy_path: str
    task_plan_path: str
    pml_path: str
    psl_path: str
    template_profile_path: str
    template_background_path: str
    html_path: str
    pptx_path: str
    md_path: str
    validation_report_path: str
    repair_log_path: str

    validation_reports: List[Dict[str, Any]]
    repair_log: List[str]


# ---------------------------------------------------------------------------
# Defaults: report ontology = classification/filtering only
# ---------------------------------------------------------------------------

DEFAULT_REPORT_ONTOLOGY: Dict[str, Any] = {
    "ontology_id": "report-document-ontology-clean-role-v1",
    "role": "classify_existing_document_units_only",
    "principles": [
        "Do not create sections from ontology concepts.",
        "Do not override the source document outline.",
        "Use ontology only to classify real headings/content and remove boilerplate.",
    ],
    "exclusion_rules": {
        "ignore_opening_motto": True,
        "ignore_recipients_tail": True,
        "ignored_line_patterns": [
            r"^\s*số\s*[:：]?\s*.*$",
            r"^\s*cộng\s+h[oòóọõỏôồốộỗổơờớợỡở][aàáạãả]\s+x[aã]\s+h[oộòóọõỏ]i\s+ch[uủ]\s+ngh[iĩ]a\s+vi[eệ]t\s+nam\s*$",
            r"^\s*độc\s+lập\s*[-–—]\s*tự\s+do\s*[-–—]\s*hạnh\s+phúc\s*$",
            r"^\s*nơi\s+nhận\s*:?\s*$",
            r"^\s*kính\s+gửi\s*:?\s*$",
            r"^\s*tm\.?\s+.*$",
            r"^\s*kt\.?\s+.*$",
            r"^\s*chủ\s+tịch\s*$",
            r"^\s*người\s+ký\s*$",
        ],
        "tail_heading_patterns": [r"^\s*nơi\s+nhận\s*:?\s*$"],
    },
    "concepts": [
        {
            "id": "current_situation",
            "labels": ["đặc điểm tình hình", "tình hình", "bối cảnh", "thực trạng"],
            "description": "Mô tả bối cảnh, điều kiện, hiện trạng trước khi báo cáo kết quả hoặc nhiệm vụ.",
        },
        {
            "id": "objectives_requirements",
            "labels": ["mục tiêu", "yêu cầu", "mục đích", "quan điểm chỉ đạo"],
            "description": "Mục tiêu, yêu cầu, định hướng chỉ đạo của văn bản.",
        },
        {
            "id": "implementation_results",
            "labels": ["kết quả thực hiện", "kết quả", "kết quả đạt được"],
            "description": "Kết quả thực hiện chung, thành tựu và sản phẩm đầu ra.",
        },
        {
            "id": "highlights",
            "labels": ["kết quả nổi bật", "điểm nổi bật", "nổi bật", "thành tựu nổi bật"],
            "description": "Các kết quả đáng nhấn mạnh, thành tích hoặc điểm sáng.",
        },
        {
            "id": "limitations_causes",
            "labels": ["tồn tại", "hạn chế", "nguyên nhân", "khó khăn", "vướng mắc"],
            "description": "Tồn tại, hạn chế, nguyên nhân, khó khăn và vướng mắc.",
        },
        {
            "id": "warnings_risks",
            "labels": ["nhắc nhở", "lưu ý", "cảnh báo", "rủi ro", "điểm cần chú ý"],
            "description": "Các nhắc nhở, cảnh báo, rủi ro hoặc yêu cầu chú ý.",
        },
        {
            "id": "measures_solutions",
            "labels": ["biện pháp", "giải pháp", "phương hướng", "nhiệm vụ trọng tâm"],
            "description": "Biện pháp, giải pháp, phương hướng hoặc nhóm hành động cần triển khai.",
        },
        {
            "id": "tasks_assignment",
            "labels": ["phân công", "nhiệm vụ", "trách nhiệm", "đơn vị chủ trì", "đơn vị phối hợp"],
            "description": "Nhiệm vụ, phân công trách nhiệm, đơn vị chủ trì/phối hợp.",
        },
        {
            "id": "implementation_organization",
            "labels": ["tổ chức thực hiện", "triển khai thực hiện", "thực hiện"],
            "description": "Cách thức tổ chức, chỉ đạo, kiểm tra, đôn đốc thực hiện.",
        },
        {
            "id": "timeline_progress",
            "labels": ["tiến độ", "lộ trình", "thời gian", "giai đoạn", "mốc"],
            "description": "Tiến độ, lộ trình, mốc thời gian và các giai đoạn thực hiện.",
        },
        {
            "id": "data_metrics",
            "labels": ["số liệu", "chỉ tiêu", "tỷ lệ", "kinh phí", "dự toán", "nguồn lực"],
            "description": "Số liệu, chỉ tiêu định lượng, nguồn lực, kinh phí.",
        },
        {
            "id": "proposals_recommendations",
            "labels": ["kiến nghị", "đề xuất", "đề nghị"],
            "description": "Kiến nghị, đề xuất hoặc yêu cầu cấp có thẩm quyền xem xét.",
        },
    ],
}


# ---------------------------------------------------------------------------
# Defaults: presentation ontology = intent inference only
# ---------------------------------------------------------------------------

DEFAULT_PRESENTATION_ONTOLOGY: Dict[str, Any] = {
    "ontology_id": "presentation-intent-ontology-v1",
    "role": "infer_slide_intent_from_existing_task",
    "principles": [
        "Do not decide document section structure.",
        "Only infer rhetorical intent for a real source unit.",
        "Return intent candidates that layout registry can satisfy.",
    ],
    "intents": [
        {
            "id": "explain_context",
            "labels": ["bối cảnh", "đặc điểm tình hình", "thực trạng", "tình hình"],
            "description": "Giải thích bối cảnh hoặc hiện trạng.",
            "preferred_content_shape": "bullets",
        },
        {
            "id": "summarize_core_message",
            "labels": ["tóm tắt", "khái quát", "tổng quan", "nội dung chính"],
            "description": "Tóm tắt thông điệp cốt lõi.",
            "preferred_content_shape": "bullets",
        },
        {
            "id": "present_findings",
            "labels": ["kết quả", "kết quả nổi bật", "phát hiện", "điểm nổi bật"],
            "description": "Trình bày kết quả hoặc phát hiện chính.",
            "preferred_content_shape": "cards",
        },
        {
            "id": "highlight_risks",
            "labels": ["hạn chế", "rủi ro", "cảnh báo", "nhắc nhở", "khó khăn", "vướng mắc"],
            "description": "Làm nổi bật rủi ro, hạn chế, nhắc nhở.",
            "preferred_content_shape": "icon_bullets",
        },
        {
            "id": "recommend_actions",
            "labels": ["biện pháp", "giải pháp", "phương hướng", "kiến nghị", "đề xuất"],
            "description": "Đề xuất giải pháp hoặc hành động.",
            "preferred_content_shape": "numbered_columns",
        },
        {
            "id": "assign_responsibilities",
            "labels": ["phân công", "trách nhiệm", "đơn vị chủ trì", "đơn vị phối hợp"],
            "description": "Trình bày nhiệm vụ và đơn vị chịu trách nhiệm.",
            "preferred_content_shape": "table",
        },
        {
            "id": "show_progression",
            "labels": ["tiến độ", "lộ trình", "giai đoạn", "mốc thời gian"],
            "description": "Trình bày tiến trình, timeline hoặc roadmap.",
            "preferred_content_shape": "timeline_or_steps",
        },
        {
            "id": "organize_implementation",
            "labels": ["tổ chức thực hiện", "triển khai", "kiểm tra", "đôn đốc"],
            "description": "Trình bày cách tổ chức triển khai.",
            "preferred_content_shape": "steps",
        },
        {
            "id": "show_metrics",
            "labels": ["số liệu", "chỉ tiêu", "tỷ lệ", "kinh phí", "nguồn lực"],
            "description": "Trình bày số liệu/chỉ tiêu dưới dạng bảng hoặc bullet ngắn.",
            "preferred_content_shape": "table_or_cards",
        },
    ],
    "fallback_intent": "summarize_core_message",
}


# ---------------------------------------------------------------------------
# Defaults: layout registry = renderer capabilities only
# ---------------------------------------------------------------------------

DEFAULT_LAYOUT_REGISTRY: Dict[str, Any] = {
    "registry_id": "rich-renderer-layout-registry-v1",
    "role": "renderer_capability_registry",
    "layouts": [
        {"id": "title-bullets", "intents": ["summarize_core_message", "explain_context", "highlight_risks"], "supported_blocks": ["title", "subtitle", "bullets", "conclusion"], "capacity": {"min_items": 1, "max_items": 8}, "data_shapes": ["bullets", "icon_bullets"], "description": "Safe general title + bullets layout."},
        {"id": "two-column", "intents": ["compare", "explain_context"], "supported_blocks": ["title", "left", "right"], "capacity": {"max_columns": 2}, "data_shapes": ["two_column_text"], "description": "Two text columns."},
        {"id": "title-table", "intents": ["assign_responsibilities", "show_metrics"], "supported_blocks": ["title", "table"], "capacity": {"max_rows": 12, "max_cols": 5}, "data_shapes": ["table"], "description": "Title plus table."},
        {"id": "grid-3", "intents": ["present_findings", "highlight_risks"], "supported_blocks": ["title", "cells"], "capacity": {"max_cells": 3}, "data_shapes": ["cards"], "description": "Three card grid."},
        {"id": "grid-4", "intents": ["present_findings", "highlight_risks", "show_metrics"], "supported_blocks": ["title", "cells"], "capacity": {"max_cells": 4}, "data_shapes": ["cards"], "description": "Four card grid."},
        {"id": "grid-5", "intents": ["present_findings"], "supported_blocks": ["title", "cells"], "capacity": {"max_cells": 5}, "data_shapes": ["cards"], "description": "Five card grid."},
        {"id": "grid-6", "intents": ["present_findings"], "supported_blocks": ["title", "cells"], "capacity": {"max_cells": 6}, "data_shapes": ["cards"], "description": "Six card grid."},
        {"id": "grid-4x2", "intents": ["present_findings", "show_metrics"], "supported_blocks": ["title", "cells"], "capacity": {"max_cells": 8}, "data_shapes": ["cards"], "description": "4x2 card grid."},
        {"id": "numbered-columns-3", "intents": ["recommend_actions", "organize_implementation"], "supported_blocks": ["title", "columns"], "capacity": {"max_columns": 3}, "data_shapes": ["numbered_columns"], "description": "Three numbered columns."},
        {"id": "numbered-columns-4", "intents": ["recommend_actions", "organize_implementation"], "supported_blocks": ["title", "columns"], "capacity": {"max_columns": 4}, "data_shapes": ["numbered_columns"], "description": "Four numbered columns."},
        {"id": "numbered-columns-5", "intents": ["recommend_actions"], "supported_blocks": ["title", "columns"], "capacity": {"max_columns": 5}, "data_shapes": ["numbered_columns"], "description": "Five numbered columns."},
        {"id": "numbered-columns-6", "intents": ["recommend_actions"], "supported_blocks": ["title", "columns"], "capacity": {"max_columns": 6}, "data_shapes": ["numbered_columns"], "description": "Six numbered columns."},
        {"id": "timeline", "intents": ["show_progression"], "supported_blocks": ["title", "milestones"], "capacity": {"max_milestones": 7}, "data_shapes": ["timeline"], "description": "Horizontal timeline with milestones."},
        {"id": "stair-progress", "intents": ["show_progression", "organize_implementation"], "supported_blocks": ["title", "steps"], "capacity": {"max_steps": 6}, "data_shapes": ["steps"], "description": "Stair progress layout."},
        {"id": "stacked-stairs", "intents": ["organize_implementation", "recommend_actions"], "supported_blocks": ["title", "steps"], "capacity": {"max_steps": 6}, "data_shapes": ["steps"], "description": "Stacked shrinking stair cards."},
        {"id": "conclusion", "intents": ["summarize_core_message", "recommend_actions"], "supported_blocks": ["title", "bullets", "conclusion"], "capacity": {"max_items": 6}, "data_shapes": ["bullets"], "description": "Bullets plus highlighted conclusion box."},
    ],
    "selection_policy": {
        "prefer_exact_intent_match": True,
        "respect_capacity": True,
        "fallback_layout": "title-bullets",
    },
}


DEFAULT_PSL = r'''
theme "corporate":
  page:
    size: widescreen
    background: "#FFFFFF"

  colors:
    primary: "#003A8C"
    secondary: "#E6F0FF"
    text: "#1F1F1F"
    muted: "#666666"

  fonts:
    heading: "Aptos Display"
    body: "Aptos"

  presentation.title-slide:
    background: primary
    title:
      font: heading
      size: 46
      color: "#FFFFFF"
      bold: true
      align: left
      position: [90, 230]
      width: 1080
      height: 100
    author:
      font: body
      size: 20
      color: "#E6F0FF"
      position: [95, 360]
      width: 920
      height: 40

  section.section-header:
    background: secondary
    title:
      font: heading
      size: 42
      color: primary
      bold: true
      align: center
      position: [120, 285]
      width: 1040
      height: 90

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
    subtitle:
      font: body
      size: 18
      color: muted
      italic: true
      position: [64, 105]
      width: 1050
      height: 42
    bullets:
      font: body
      size: 22
      color: text
      position: [85, 165]
      width: 1040
      height: 410
      icon: check
      overflow: shrink
      min_size: 15
    conclusion:
      position: [80, 585]
      width: 1120
      height: 72
      fill: secondary
      border: primary
      font: body
      size: 18
      bold: true
      align: center

  slide.conclusion:
    title:
      font: heading
      size: 34
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    bullets:
      font: body
      size: 21
      color: text
      position: [85, 150]
      width: 1040
      height: 330
      icon: check
    conclusion:
      position: [80, 535]
      width: 1120
      height: 95
      fill: "#E0F2FE"
      border: primary
      font: body
      size: 19
      bold: true
      align: center

  slide.grid-3:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    grid:
      position: [70, 160]
      width: 1140
      height: 430
      columns: 3
      gap: 24
    card:
      fill: "#F8FAFC"
      border: "#CBD5E1"

  slide.grid-4:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    grid:
      position: [70, 145]
      width: 1140
      height: 475
      columns: 2
      gap: 22
    card:
      fill: "#F8FAFC"
      border: "#CBD5E1"

  slide.grid-5:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    grid:
      position: [70, 145]
      width: 1140
      height: 475
      columns: 3
      gap: 20
    card:
      fill: "#F8FAFC"
      border: "#CBD5E1"

  slide.grid-6:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    grid:
      position: [70, 145]
      width: 1140
      height: 475
      columns: 3
      gap: 20
    card:
      fill: "#F8FAFC"
      border: "#CBD5E1"

  slide.grid-4x2:
    title:
      font: heading
      size: 30
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 75
    grid:
      position: [55, 135]
      width: 1170
      height: 500
      columns: 4
      gap: 16
    card:
      fill: "#F8FAFC"
      border: "#CBD5E1"

  slide.numbered-columns-3:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    columns:
      position: [70, 175]
      width: 1140
      height: 420
      count: 3
      gap: 24

  slide.numbered-columns-4:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    columns:
      position: [55, 175]
      width: 1170
      height: 420
      count: 4
      gap: 18

  slide.numbered-columns-5:
    title:
      font: heading
      size: 30
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 75
    columns:
      position: [45, 170]
      width: 1190
      height: 430
      count: 5
      gap: 14

  slide.numbered-columns-6:
    title:
      font: heading
      size: 29
      color: primary
      bold: true
      position: [55, 35]
      width: 1160
      height: 70
    columns:
      position: [35, 165]
      width: 1210
      height: 435
      count: 6
      gap: 10

  slide.timeline:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    timeline:
      position: [90, 360]
      width: 1100
      height: 12
      card_width: 210
      card_height: 110

  slide.stair-progress:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    stairs:
      position: [100, 175]
      step_width: 210
      step_height: 115
      dx: 190
      dy: 58

  slide.stacked-stairs:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 80
    stacked_stairs:
      position: [120, 165]
      base_width: 990
      step_height: 76
      shrink: 80
      overlap: 18
      align_side: left

  slide.title-table:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      align: center
      position: [60, 35]
      width: 1120
      height: 80
    table:
      position: [70, 140]
      width: 1140
      height: 465
      font: body
      size: 17
      align: center
      header_fill: primary
      header_color: "#FFFFFF"
      border_color: "#CBD5E1"
'''


DECK_TEMPLATE = r'''
presentation "{{ title }}":
  meta:
    author: "Legal Article First Pipeline"
    language: vi
    format: pptx

  use style: "corporate.psl"

  cover_layout: title-slide
  cover:
    author: "Tạo tự động từ bố cục thật của văn bản"

{% for group in section_groups %}
  section "{{ group.section_title | pml_escape }}":

{% for slide in group.slides %}
    slide "{{ slide.title | pml_escape }}":
      layout: {{ slide.layout }}
      intent: {{ slide.intent }}

      title:
        {{ slide.title | pml_escape }}
{% if slide.subtitle %}

      subtitle:
        {{ slide.subtitle | pml_escape }}
{% endif %}
{% if slide.blocks.get("bullets") %}

      bullets:
{% set bullets = slide.blocks.get("bullets") %}
{% if bullets is mapping %}
{% if bullets.get("icon") %}
        icon: {{ bullets.get("icon") }}
{% endif %}
        items:
{% for item in bullets.get("items", []) %}
          - {{ item | pml_escape }}
{% endfor %}
{% else %}
{% for item in bullets %}
        - {{ item | pml_escape }}
{% endfor %}
{% endif %}
{% endif %}
{% if slide.blocks.get("cells") %}

      cells:
{% for cell in slide.blocks.get("cells") %}
        - heading: {{ cell.heading | pml_escape }}
          text: {{ cell.text | pml_escape }}
{% endfor %}
{% endif %}
{% if slide.blocks.get("columns") %}

      columns:
{% for col in slide.blocks.get("columns") %}
        - heading: {{ col.heading | pml_escape }}
          text: {{ col.text | pml_escape }}
{% if col.bullets %}
          bullets:
{% for b in col.bullets %}
            - {{ b | pml_escape }}
{% endfor %}
{% endif %}
{% endfor %}
{% endif %}
{% if slide.blocks.get("milestones") %}

      milestones:
{% for m in slide.blocks.get("milestones") %}
        - date: {{ m.date | pml_escape }}
          heading: {{ m.heading | pml_escape }}
          text: {{ m.text | pml_escape }}
{% endfor %}
{% endif %}
{% if slide.blocks.get("steps") %}

      steps:
{% for st in slide.blocks.get("steps") %}
        - heading: {{ st.heading | pml_escape }}
          text: {{ st.text | pml_escape }}
{% endfor %}
{% endif %}
{% if slide.blocks.get("table") %}

      table:
        headers: [{{ slide.blocks.get("table").headers | map('pml_escape') | join(', ') }}]
        rows:
{% for row in slide.blocks.get("table").rows %}
          - [{{ row | map('pml_escape') | join(', ') }}]
{% endfor %}
{% endif %}
{% if slide.blocks.get("conclusion") %}

      conclusion:
        icon: check
        text: {{ slide.blocks.get("conclusion") | pml_escape }}
{% endif %}
{% endfor %}
{% endfor %}
'''


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json_or_default(path: str, default: Dict[str, Any], out_dir: Path, default_name: str) -> Dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    out_path = out_dir / default_name
    save_json(out_path, default)
    return default


def normalize_space(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def is_blank_or_placeholder(text: Any) -> bool:
    if text is None:
        return True
    s = normalize_space(str(text))
    if not s:
        return True
    bad = {"n/a", "none", "null", "không có", "không rõ", "chưa có", "...", "…"}
    return s.lower() in bad


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", text)
    if len(parts) == 1:
        # Vietnamese admin docs often use semicolons/newlines rather than periods.
        parts = re.split(r";\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if p.strip(" -•\t")]


def truncate_without_ellipsis(text: str, max_chars: int = 180) -> str:
    text = normalize_space(text)
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    sentence_cut = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(".\n"))
    if sentence_cut > max_chars * 0.45:
        return cut[: sentence_cut + 1].strip()
    word_cut = cut.rfind(" ")
    if word_cut > max_chars * 0.55:
        return cut[:word_cut].strip()
    return text[:max_chars].strip()


def pml_escape(value: Any) -> str:
    """Escape values for the simple indentation parser.

    We quote scalars that contain colon/brackets/leading special chars so that
    text like "Lưu ý: ..." is preserved as text, not parsed as key:value.
    """
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    s = normalize_space(s)
    if not s:
        return '""'
    need_quote = any(ch in s for ch in [":", "#", "[", "]", "{", "}", ","]) or s.startswith(("-", "@", "!", "*"))
    if need_quote:
        return json.dumps(s, ensure_ascii=False)
    return s


def pml_escape_filter(value: Any) -> str:
    return pml_escape(value)


def load_renderer(renderer_path: str):
    spec = importlib.util.spec_from_file_location("dsl_renderer", renderer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import renderer from {renderer_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dsl_renderer"] = mod
    spec.loader.exec_module(mod)
    required = ["parse_pml", "parse_psl", "build_render_ir", "render_html", "render_pptx"]
    missing = [name for name in required if not hasattr(mod, name)]
    if missing:
        raise RuntimeError(f"Renderer thiếu hàm: {missing}")
    return mod


def render_outputs(renderer_path: str, pml_text: str, psl_text: str, out_dir: Path) -> Dict[str, str]:
    renderer = load_renderer(renderer_path)
    doc = renderer.parse_pml(pml_text)
    theme = renderer.parse_psl(psl_text)
    render_ir = renderer.build_render_ir(doc, theme)

    html_path = out_dir / "output.html"
    pptx_path = out_dir / "output.pptx"
    md_path = out_dir / "output.md"

    renderer.render_html(render_ir, str(html_path))
    renderer.render_pptx(render_ir, str(pptx_path))
    if hasattr(renderer, "render_markdown"):
        renderer.render_markdown(render_ir, str(md_path))
    else:
        md_path.write_text(simple_markdown_from_ir(render_ir), encoding="utf-8")
    return {"html": str(html_path), "pptx": str(pptx_path), "md": str(md_path)}




def validate_pml_deck(pml_text: str, psl_text: str, renderer_path: str, layout_registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate generated PML before rendering.

    This validator is intentionally practical rather than formal:
    - Can renderer parse PML/PSL and build IR?
    - No empty slides.
    - No metadata subtitle / footer objects.
    - No ellipsis truncation.
    - No unsupported layouts when registry is supplied.
    - No section with one slide if there are multiple sections.
    """
    issues: List[Dict[str, Any]] = []

    if "..." in pml_text or "…" in pml_text:
        issues.append({"code": "ellipsis_found", "severity": "error", "message": "PML contains ellipsis-style truncation."})

    if re.search(r"^\s*footer(_text|_image|_images)?\s*:", pml_text, re.M):
        issues.append({"code": "footer_found", "severity": "error", "message": "Footer block found although current policy forbids footer."})

    if re.search(r"theo đề mục gốc|task|layout|ontology|renderer", pml_text, re.I):
        # This is broad, but catches common subtitle/metadata leakage.
        issues.append({"code": "metadata_leak", "severity": "warning", "message": "Possible metadata leaked into slide text/subtitle."})

    supported_layouts = set()
    for item in layout_registry.get("layouts", []):
        if isinstance(item, dict) and item.get("id"):
            supported_layouts.add(item["id"])
    if not supported_layouts and isinstance(layout_registry.get("layouts"), dict):
        supported_layouts.update(layout_registry["layouts"].keys())

    for lineno, line in enumerate(pml_text.splitlines(), 1):
        m = re.match(r"\s*layout:\s*([A-Za-z0-9_.-]+)\s*$", line)
        if m and supported_layouts and m.group(1) not in supported_layouts:
            issues.append({"code": "unsupported_layout", "severity": "error", "line": lineno, "layout": m.group(1)})

    try:
        renderer = load_renderer(renderer_path)
        doc = renderer.parse_pml(pml_text)
        theme = renderer.parse_psl(psl_text)
        ir = renderer.build_render_ir(doc, theme)
        slides = ir.get("slides", [])
        if not slides:
            issues.append({"code": "no_render_slides", "severity": "error", "message": "Render IR has no slides."})
        section_counts: Dict[str, int] = {}
        for idx, slide in enumerate(slides, 1):
            objs = slide.get("objects", [])
            visible_content = []
            for obj in objs:
                if obj.get("type") in {"TextBox", "BulletList", "IconBulletList", "TableBox", "CardBox", "ImageBox", "Hyperlink"}:
                    visible_content.append(obj)
            if not visible_content:
                issues.append({"code": "empty_render_slide", "severity": "error", "slide_index": idx, "slide_title": slide.get("slide_title")})
            sec = slide.get("section") or ""
            if sec and slide.get("layout") not in {"section-header", "title-slide"}:
                section_counts[sec] = section_counts.get(sec, 0) + 1
        multi_sections = {k: v for k, v in section_counts.items() if k}
        if len(multi_sections) > 1:
            for sec, count in multi_sections.items():
                if count == 1:
                    issues.append({"code": "singleton_section", "severity": "warning", "section": sec, "message": "Section has only one content slide."})
    except Exception as exc:
        issues.append({"code": "renderer_parse_error", "severity": "error", "message": str(exc)})
    return issues


def repair_pml_deck(pml_text: str, issues: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Heuristic PML repair. Keeps repairs conservative and deterministic."""
    repaired = pml_text
    log: List[str] = []
    codes = {i.get("code") for i in issues}

    if "ellipsis_found" in codes:
        repaired = repaired.replace("...", "").replace("…", "")
        log.append("Removed ellipsis markers from PML.")

    if "footer_found" in codes:
        lines = repaired.splitlines()
        out: List[str] = []
        skip_indent = None
        for line in lines:
            m = re.match(r"^(\s*)footer(?:_text|_image|_images)?\s*:", line)
            if m:
                skip_indent = len(m.group(1))
                log.append("Removed footer block from PML.")
                continue
            if skip_indent is not None:
                indent = len(line) - len(line.lstrip(" "))
                if line.strip() and indent > skip_indent:
                    continue
                skip_indent = None
            out.append(line)
        repaired = "\n".join(out) + "\n"

    if "metadata_leak" in codes:
        # Remove only common subtitle blocks that clearly contain metadata. Do not touch normal content.
        lines = repaired.splitlines()
        out: List[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^\s*subtitle:\s*$", line):
                block = [line]
                base_indent = len(line) - len(line.lstrip(" "))
                j = i + 1
                while j < len(lines):
                    ind = len(lines[j]) - len(lines[j].lstrip(" "))
                    if lines[j].strip() and ind <= base_indent:
                        break
                    block.append(lines[j])
                    j += 1
                block_text = "\n".join(block)
                if re.search(r"theo đề mục gốc|task|layout|ontology|renderer", block_text, re.I):
                    log.append("Removed metadata-like subtitle block.")
                    i = j
                    continue
            out.append(line)
            i += 1
        repaired = "\n".join(out) + "\n"

    if "unsupported_layout" in codes:
        bad_layouts = {i.get("layout") for i in issues if i.get("code") == "unsupported_layout"}
        for bad in bad_layouts:
            if bad:
                repaired = re.sub(rf"(^\s*layout:\s*){re.escape(str(bad))}\s*$", rf"\1title-bullets", repaired, flags=re.M)
                log.append(f"Replaced unsupported layout {bad!r} with title-bullets.")

    return repaired, log


def node_validate_repair_pml(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    pml_text = state.get("pml_text", "")
    psl_text = state.get("psl_text", "")
    issues1 = validate_pml_deck(pml_text, psl_text, state["renderer_path"], state.get("layout_registry", {}))
    state.setdefault("validation_reports", []).append({"stage": "pml_validation_before_repair", "issues": issues1})

    hard_issues = [i for i in issues1 if i.get("severity") == "error" or i.get("code") in {"metadata_leak", "singleton_section"}]
    if hard_issues:
        repaired, logs = repair_pml_deck(pml_text, issues1)
        state.setdefault("repair_log", []).extend(["PML: " + x for x in logs])
        state["pml_text"] = repaired
        pml_path = out_dir / "generated.pml"
        pml_path.write_text(repaired, encoding="utf-8")
        state["pml_path"] = str(pml_path)
        issues2 = validate_pml_deck(repaired, psl_text, state["renderer_path"], state.get("layout_registry", {}))
        state.setdefault("validation_reports", []).append({"stage": "pml_validation_after_repair", "issues": issues2})
    return state


def node_render_deck(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    outputs = render_outputs(state["renderer_path"], state["pml_text"], state["psl_text"], out_dir)
    state["html_path"] = outputs["html"]
    state["pptx_path"] = outputs["pptx"]
    state["md_path"] = outputs["md"]

    validation = state.get("validation_reports", [])
    validation.append({
        "stage": "ontology_role_summary",
        "report_ontology": "classification/filtering only",
        "presentation_ontology": "intent inference only",
        "layout_registry": "layout capability selection only",
        "completed_slides": len(state.get("completed_tasks", [])),
    })
    validation_path = out_dir / "validation_report.json"
    repair_log_path = out_dir / "repair_log.txt"
    save_json(validation_path, validation)
    repair_log_path.write_text("\n".join(state.get("repair_log", [])), encoding="utf-8")
    state["validation_report_path"] = str(validation_path)
    state["repair_log_path"] = str(repair_log_path)
    return state

def simple_markdown_from_ir(render_ir: Dict[str, Any]) -> str:
    lines = [f"# {render_ir.get('title', 'Deck')}", ""]
    for idx, slide in enumerate(render_ir.get("slides", []), 1):
        lines.append(f"## Slide {idx}: {slide.get('slide_title', '')}")
        if slide.get("section"):
            lines.append(f"Section: {slide.get('section')}")
        lines.append(f"Layout: {slide.get('layout', '')}")
        lines.append("")
        for obj in slide.get("objects", []):
            typ = obj.get("type")
            role = obj.get("role", typ)
            if typ == "TextBox":
                lines.append(f"**{role}:** {obj.get('text','')}")
            elif typ in {"BulletList", "IconBulletList"}:
                lines.append(f"**{role}:**")
                for item in obj.get("items", []):
                    if isinstance(item, dict):
                        lines.append(f"- {item.get('text', '')}")
                    else:
                        lines.append(f"- {item}")
            elif typ == "TableBox":
                headers = obj.get("headers", [])
                rows = obj.get("rows", [])
                if headers:
                    lines.append("| " + " | ".join(map(str, headers)) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for row in rows:
                        lines.append("| " + " | ".join(map(str, row)) + " |")
            elif typ in {"CardBox", "ImageBox"}:
                lines.append(f"**{role}:** {obj.get('heading', obj.get('alt', ''))} {obj.get('text', '')}")
        lines.append("")
    return "\n".join(lines)




# ---------------------------------------------------------------------------
# STRICT legal-content body policy patch
# ---------------------------------------------------------------------------

LEGAL_BODY_START_RE = re.compile(r"(?im)^\s*Điều\s+1\s*[\.\s:–—-]+")
LEGAL_APPENDIX_RE = re.compile(r"(?im)^\s*PHỤ\s+LỤC\b")


def cut_to_legal_body(text: str) -> str:
    """Return only the substantive legal body for Vietnamese normative docs.

    For draft resolutions and decisions, everything before `Điều 1` is normally
    header/title/legal basis/preamble (`Căn cứ...`, `Xét...`) and should not be
    summarized as content slides. If no `Điều 1` exists but a `PHỤ LỤC` exists,
    start from `PHỤ LỤC`. This is intentionally applied BEFORE chunking and also
    in fallbacks so the deck never falls back to administrative header lines.
    """
    if not text:
        return ""
    m = LEGAL_BODY_START_RE.search(text)
    if m:
        return text[m.start():].strip()
    app = LEGAL_APPENDIX_RE.search(text)
    if app:
        return text[app.start():].strip()
    return text.strip()


def is_legal_heading_or_body(text: str) -> bool:
    return bool(LEGAL_BODY_START_RE.search(text) or LEGAL_APPENDIX_RE.search(text))


def remove_legal_signature_tail_but_keep_appendix(text: str) -> str:
    """Remove recipient/signature tail before appendix without swallowing appendix.

    Some legal DOCX files have `Nơi nhận` and signature before a later appendix.
    We remove the recipient/signature block up to the next `PHỤ LỤC`, preserving
    appendix content.
    """
    lines = text.splitlines()
    out = []
    in_tail = False
    for raw in lines:
        line = normalize_space(raw)
        if in_tail and re.match(r"^\s*PHỤ\s+LỤC\b", line, re.I):
            in_tail = False
        if not in_tail and re.match(r"^\s*Nơi\s+nhận\s*:?\s*$", line, re.I):
            in_tail = True
            continue
        if in_tail:
            continue
        out.append(raw)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# ---------------------------------------------------------------------------
# Input extraction and cleaning
# ---------------------------------------------------------------------------


def extract_input_text(path: str, input_text: str = "", use_stdin: bool = False) -> str:
    if input_text:
        return input_text
    if use_stdin:
        return sys.stdin.read()
    if not path:
        raise ValueError("Provide input file path, --text, or --stdin")

    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".txt", ".md"}:
        return p.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".docx":
        try:
            from docx import Document
            from docx.oxml.table import CT_Tbl
            from docx.oxml.text.paragraph import CT_P
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError as exc:
            raise RuntimeError("Missing dependency: python-docx") from exc

        def iter_docx_blocks(document):
            """Yield paragraphs and tables in the original DOCX body order.

            The older implementation appended all tables after all paragraphs.
            For legal documents this is harmful because appendices are usually
            tables after a `PHỤ LỤC` heading; if `Nơi nhận` appears before the
            appended tables, the boilerplate tail filter can accidentally remove
            the entire appendix.
            """
            body = document.element.body
            for child in body.iterchildren():
                if isinstance(child, CT_P):
                    yield Paragraph(child, document)
                elif isinstance(child, CT_Tbl):
                    yield Table(child, document)

        doc = Document(str(p))
        parts: List[str] = []
        for block in iter_docx_blocks(doc):
            if hasattr(block, "text"):
                txt = block.text.strip()
                if txt:
                    parts.append(txt)
            else:
                for row in block.rows:
                    cells = [normalize_space(cell.text) for cell in row.cells if normalize_space(cell.text)]
                    if cells:
                        # Preserve table rows as pipe-separated text so the
                        # outline/task extractor can summarize appendix tables.
                        parts.append(" | ".join(cells))
        return "\n".join(parts)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Missing dependency: pypdf") from exc
        reader = PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"Unsupported input file type: {suffix}")


def compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    compiled = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat, flags=re.IGNORECASE))
        except re.error:
            continue
    return compiled


def strip_admin_tokens_from_mixed_line(line: str) -> str:
    """Remove administrative boilerplate fragments inside a mixed OCR/PDF line.

    Older versions dropped a whole line when it contained both `Số:` and
    `CỘNG HÒA...`. In PDF extraction those fragments may be concatenated with a
    real title such as `NGHỊ QUYẾT...`; dropping the whole line can erase the
    only meaningful heading. This function removes only the boilerplate pieces
    and keeps any remaining meaningful text.
    """
    s = normalize_space(line)
    # Remove document number fragment at the beginning or after whitespace.
    s = re.sub(r"(^|\s)số\s*[:：]?\s*[^\n]{0,80}?(?=(cộng\s+h[oòóọõỏôồốộỗổơờớợỡở]a|độc\s+lập|nghị\s+quyết|quyết\s+định|kế\s+hoạch|báo\s+cáo|tờ\s+trình|$))", " ", s, flags=re.I)
    # Remove state title and motto wherever they appear.
    s = re.sub(r"cộng\s+h[oòóọõỏôồốộỗổơờớợỡở]a\s+x[aã]\s+h[oộòóọõỏ]i\s+ch[uủ]\s+ngh[iĩ]a\s+vi[eệ]t\s+nam", " ", s, flags=re.I)
    s = re.sub(r"độc\s+lập\s*[-–—]\s*tự\s+do\s*[-–—]\s*hạnh\s+phúc", " ", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–—\t")
    return s


def meaningful_after_boilerplate_strip(raw_line: str) -> str:
    """Return residual meaningful text after removing inline boilerplate."""
    residual = strip_admin_tokens_from_mixed_line(raw_line)
    if not residual:
        return ""
    low = residual.lower()
    pure_boilerplate = [
        r"^số\s*[:：]?\s*/?\s*\d*",
        r"^ngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}$",
        r"^nơi\s+nhận\s*:?$",
        r"^kính\s+gửi\s*:?$",
    ]
    if any(re.search(pat, low, re.I) for pat in pure_boilerplate):
        return ""
    # Keep only if there is enough alphabetic content or it looks like a real title/heading.
    alpha_count = len(re.findall(r"[A-Za-zÀ-ỹĐđ]", residual))
    if alpha_count < 12:
        return ""
    return residual


def _base_strip_admin_boilerplate(text: str, report_ontology: Dict[str, Any]) -> str:
    rules = report_ontology.get("exclusion_rules", {})
    ignored_patterns = compile_patterns(rules.get("ignored_line_patterns", []))
    tail_patterns = compile_patterns(rules.get("tail_heading_patterns", []))

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: List[str] = []
    removed_lines: List[str] = []
    rescued_lines: List[str] = []
    in_tail = False

    for raw in lines:
        line = normalize_space(raw)
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        # Tail blocks such as `Nơi nhận:` should be dropped, but do not let
        # this swallow a later `PHỤ LỤC` in legal documents. Some DOCX/PDF
        # extractions place appendix tables after signature/recipient blocks.
        if in_tail and re.match(r"^\s*(PHỤ\s+LỤC|Điều\s+\d+)", line, re.I):
            in_tail = False
        if not in_tail and any(p.search(line) for p in tail_patterns):
            in_tail = True
            removed_lines.append(raw.rstrip())
            continue
        if in_tail:
            removed_lines.append(raw.rstrip())
            continue

        if is_legal_preamble_line(line):
            removed_lines.append(raw.rstrip())
            continue

        residual = meaningful_after_boilerplate_strip(line)

        # If a line mixes boilerplate and real content, keep the residual instead
        # of deleting the whole line.
        mixed_admin = (
            re.search(r"\bsố\s*[:：]?", line, re.I)
            or re.search(r"cộng\s+h[oòóọõỏôồốộỗổơờớợỡở]a", line, re.I)
            or re.search(r"độc\s+lập\s*[-–—]\s*tự\s+do", line, re.I)
        )
        if mixed_admin and residual and residual != line:
            cleaned.append(residual)
            rescued_lines.append(residual)
            continue

        # Drop pure boilerplate only. If an ontology regex is over-broad but the
        # residual looks meaningful, preserve it.
        if any(p.search(line) for p in ignored_patterns):
            if residual and len(residual) >= 20 and not re.search(r"^(tm\.?|kt\.?|chủ\s+tịch|phó\s+chủ\s+tịch|người\s+ký|nơi\s+nhận)", residual, re.I):
                cleaned.append(residual)
                rescued_lines.append(residual)
            else:
                removed_lines.append(raw.rstrip())
            continue

        cleaned.append(raw.rstrip())

    out = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()
    if is_legal_document_text(out):
        out = clean_legal_preamble_lines(out)

    # Safety fallback: if filtering removed almost everything, return a lightly
    # cleaned version rather than forcing the deck to contain a fake error slide.
    original_nonempty = [normalize_space(x) for x in lines if normalize_space(x)]
    cleaned_nonempty = [normalize_space(x) for x in out.splitlines() if normalize_space(x)]
    if len(cleaned_nonempty) < 2 and len(original_nonempty) >= 3:
        light: List[str] = []
        in_tail = False
        for raw in lines:
            line = normalize_space(raw)
            if not line:
                continue
            if any(p.search(line) for p in tail_patterns):
                in_tail = True
                continue
            if in_tail:
                continue
            residual = meaningful_after_boilerplate_strip(line)
            if residual:
                light.append(residual)
            elif not any(p.search(line) for p in ignored_patterns):
                # Keep non-boilerplate lines even if short; they may be OCR fragments.
                light.append(raw.rstrip())
        out = re.sub(r"\n{3,}", "\n\n", "\n".join(light)).strip()

    return out




def strip_admin_boilerplate(text: str, report_ontology: Dict[str, Any]) -> str:
    """Strict cleaner used by the pipeline.

    Difference from older versions:
    - If this is a legal/normative document, hard-start content at `Điều 1`.
    - Drop `Căn cứ...`/`Xét...` preamble no matter where fallback would otherwise start.
    - Keep `PHỤ LỤC` even if it appears after `Nơi nhận` in extracted DOCX order.
    """
    base = _base_strip_admin_boilerplate(text, report_ontology)
    # If the original contains a legal body, use the body from the original after
    # removing inline administrative tokens. This prevents a too-aggressive early
    # cleaner from leaving organization/header lines before `Điều 1`.
    source_for_body = text if is_legal_heading_or_body(text) else base
    if is_legal_heading_or_body(source_for_body):
        body = cut_to_legal_body(source_for_body)
        body = remove_legal_signature_tail_but_keep_appendix(body)
        body = clean_legal_preamble_lines(body)
        # Also strip pure admin fragments line-by-line, but do not remove content lines.
        kept = []
        for raw in body.splitlines():
            line = normalize_space(raw)
            if not line:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            residual = meaningful_after_boilerplate_strip(line)
            if residual and residual != line:
                kept.append(residual)
            elif re.match(r"^\s*(HỘI\s+ĐỒNG\s+NHÂN\s+DÂN|THÀNH\s+PHỐ|DỰ\s+THẢO|NGHỊ\s+QUYẾT)\s*$", line, re.I):
                continue
            elif is_legal_preamble_line(line):
                continue
            else:
                kept.append(raw.rstrip())
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        return cleaned or base
    return base


# ---------------------------------------------------------------------------
# Outline extraction and role separation
# ---------------------------------------------------------------------------


HEADING_PATTERNS = [
    re.compile(r"^\s*(ĐIỀU)\s+(\d+(?:\.\d+)*)[\.:\-–—]?\s*(.+)$", re.I),
    re.compile(r"^\s*(PHẦN|CHƯƠNG|MỤC)\s+([IVXLCDM]+|\d+)[\.:\-–—]?\s*(.+)$", re.I),
    re.compile(r"^\s*([IVXLCDM]+)\s*[\.)]\s+(.+)$", re.I),
    re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[\.)]\s+(.+)$"),
    re.compile(r"^\s*([a-zđ])\)\s+(.+)$", re.I),
]


def looks_like_heading(line: str) -> Tuple[bool, int, str, str]:
    s = normalize_space(line)
    if not s or len(s) > 180:
        return False, 0, "", ""
    for pat in HEADING_PATTERNS:
        m = pat.match(s)
        if m:
            if pat.pattern.startswith("^\\s*(ĐI"):
                title = m.group(3).strip() or f"Điều {m.group(2)}"
                num = f"{m.group(1)} {m.group(2)}"
                return True, 1, num, title
            if pat.pattern.startswith("^\\s*(PH"):
                title = m.group(3).strip()
                num = f"{m.group(1)} {m.group(2)}"
                return True, 1, num, title
            marker = m.group(1)
            title = m.group(2).strip()
            if re.match(r"^[IVXLCDM]+$", marker, re.I):
                return True, 1, marker, title
            if re.match(r"^\d+(?:\.\d+)*$", marker):
                level = marker.count(".") + 1
                return True, min(level, 4), marker, title
            return True, 3, marker, title
    # Uppercase/colon administrative headings like "Đặc điểm tình hình:".
    if s.endswith(":") and 4 <= len(s) <= 90:
        return True, 2, "", s[:-1].strip()
    if s.isupper() and 4 <= len(s) <= 90:
        return True, 1, "", s.title()
    return False, 0, "", ""



LEGAL_PREAMBLE_LINE_PATTERNS = [
    re.compile(r"^\s*căn\s+cứ\b", re.I),
    re.compile(r"^\s*xét\b", re.I),
    re.compile(r"^\s*theo\s+đề\s+nghị\b", re.I),
    re.compile(r"^\s*hội\s+đồng\s+nhân\s+dân\s+.*\s+ban\s+hành\s+nghị\s+quyết\b", re.I),
]


def is_legal_document_text(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*Điều\s+1[\.\s]", text)) or (
        bool(re.search(r"(?im)^\s*NGHỊ\s+QUYẾT\s*$", text))
        and bool(re.search(r"(?im)^\s*Căn\s+cứ\b", text))
    )


def is_legal_preamble_line(line: str) -> bool:
    s = normalize_space(line)
    if not s:
        return False
    return any(p.search(s) for p in LEGAL_PREAMBLE_LINE_PATTERNS)


def clean_legal_preamble_lines(text: str) -> str:
    """Remove normative-document preamble lines such as `Căn cứ...`.

    These lines are legal basis metadata, not summary content. The function is
    deliberately conservative: it drops lines only when they start with legal
    preamble markers, so phrases like `các cơ sở giáo dục căn cứ vào...` inside
    an article are preserved.
    """
    out: List[str] = []
    for raw in text.splitlines():
        line = normalize_space(raw)
        if is_legal_preamble_line(line):
            continue
        out.append(raw.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def split_legal_document_sections(text: str) -> List[Dict[str, Any]]:
    """Split Vietnamese legal/normative documents by `Điều` and `Phụ lục`.

    For draft resolutions, the real slide-worthy content usually starts at
    `Điều 1`. The opening title/legal basis (`Căn cứ...`, `Xét...`) should not
    become a slide. Appendix tables are preserved as a dedicated content unit.
    """
    lines = text.splitlines()
    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    seen_article = False

    article_re = re.compile(r"^\s*Điều\s+(\d+(?:\.\d+)*)[\.\s:–—-]+(.+)$", re.I)
    appendix_re = re.compile(r"^\s*PHỤ\s+LỤC\b[:\s–—-]*(.*)$", re.I)
    appendix_title_pending = False

    def flush() -> None:
        nonlocal current
        if current:
            current["content"] = "\n".join(current.pop("_buf")).strip()
            if not is_blank_or_placeholder(current.get("content", "")) or current.get("source_heading", "").lower().startswith("phụ lục"):
                entries.append(current)
            current = None

    for idx, raw in enumerate(lines, 1):
        line = normalize_space(raw)
        if not line:
            if current:
                current["_buf"].append("")
            continue

        # Stop dropping recipient tail if a real appendix starts after the signature.
        app_m = appendix_re.match(line)
        if app_m:
            flush()
            seen_article = True
            trailing_title = normalize_space(app_m.group(1) or "")
            heading_title = trailing_title if trailing_title else "Phụ lục"
            if trailing_title and not trailing_title.lower().startswith("phụ lục"):
                heading_title = f"Phụ lục: {trailing_title}"
            current = {
                "id": f"H{len(entries)+1:03d}",
                "source_heading": heading_title,
                "level": 1,
                "numbering": "PHỤ LỤC",
                "line_no": idx,
                "_buf": [],
            }
            appendix_title_pending = not bool(trailing_title)
            continue

        m = article_re.match(line)
        if m:
            flush()
            seen_article = True
            current = {
                "id": f"H{len(entries)+1:03d}",
                "source_heading": m.group(2).strip(),
                "level": 1,
                "numbering": f"Điều {m.group(1)}",
                "line_no": idx,
                "_buf": [],
            }
            appendix_title_pending = False
            continue

        # Everything before Điều 1 is title/legal basis/preamble. Do not create
        # slide tasks from it. This fixes the bug where only `Căn cứ...` was
        # summarized.
        if not seen_article:
            continue

        if current is None:
            current = {
                "id": f"H{len(entries)+1:03d}",
                "source_heading": "Nội dung chính",
                "level": 1,
                "numbering": "",
                "line_no": idx,
                "_buf": [],
            }

        # For appendix, use the next uppercase descriptive line as heading.
        if appendix_title_pending and line and len(line) <= 140 and not line.startswith("("):
            current["source_heading"] = f"Phụ lục: {line.title()}" if line.isupper() else f"Phụ lục: {line}"
            appendix_title_pending = False
            continue

        if is_legal_preamble_line(line):
            continue
        current["_buf"].append(raw.rstrip())

    flush()
    return [e for e in entries if not is_blank_or_placeholder(e.get("content", "")) and not should_skip_outline_item(e)]


def should_skip_outline_item(item: Dict[str, Any]) -> bool:
    heading = normalize_space(str(item.get("source_heading", ""))).lower()
    content = normalize_space(str(item.get("content", ""))).lower()
    if heading in {"nghị quyết", "dự thảo", "mở đầu"}:
        return True
    if content.startswith("căn cứ ") or "\ncăn cứ " in content:
        return True
    if content.startswith("xét ") or "\nxét " in content:
        return True
    return False


def split_by_source_outline(text: str) -> List[Dict[str, Any]]:
    if is_legal_document_text(text):
        legal_entries = split_legal_document_sections(text)
        if legal_entries:
            return legal_entries

    lines = text.splitlines()
    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    preface: List[str] = []

    for idx, line in enumerate(lines, 1):
        is_heading, level, marker, title = looks_like_heading(line)
        if is_heading:
            if current:
                current["content"] = "\n".join(current.pop("_buf")).strip()
                entries.append(current)
            elif preface:
                txt = "\n".join(preface).strip()
                if len(txt) > 80:
                    entries.append({
                        "id": "H000",
                        "source_heading": "Mở đầu",
                        "level": 1,
                        "numbering": "",
                        "line_no": 1,
                        "content": txt,
                    })
                preface = []
            current = {
                "id": f"H{len(entries)+1:03d}",
                "source_heading": title,
                "level": level,
                "numbering": marker,
                "line_no": idx,
                "_buf": [],
            }
        else:
            if current:
                current["_buf"].append(line)
            else:
                preface.append(line)
    if current:
        current["content"] = "\n".join(current.pop("_buf")).strip()
        entries.append(current)
    elif preface:
        txt = "\n".join(preface).strip()
        if txt:
            entries.append({"id": "H001", "source_heading": "Nội dung chính", "level": 1, "numbering": "", "line_no": 1, "content": txt})

    # If the document had no usable headings, chunk by paragraph blocks.
    meaningful = [e for e in entries if len(e.get("content", "").strip()) > 30]
    if not meaningful:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if len(p.strip()) > 30]
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else []
        entries = [
            {"id": f"H{i+1:03d}", "source_heading": f"Nội dung {i+1}", "level": 1, "numbering": "", "line_no": 0, "content": p}
            for i, p in enumerate(paragraphs[:12])
        ]
    return [e for e in entries if not is_blank_or_placeholder(e.get("content", "")) and not should_skip_outline_item(e)]


def match_report_concept(heading: str, content: str, report_ontology: Dict[str, Any]) -> Dict[str, Any]:
    hay = f"{heading} {content[:400]}".lower()
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for concept in report_ontology.get("concepts", []):
        labels = concept.get("labels", [])
        score = 0
        for label in labels:
            l = str(label).lower()
            if l and l in hay:
                score += 2 if l in heading.lower() else 1
        if score > best_score:
            best_score = score
            best = concept
    if best:
        return {"concept_id": best.get("id"), "concept_label": best.get("labels", [best.get("id")])[0], "score": best_score}
    return {"concept_id": "source_section", "concept_label": heading, "score": 0}


def classify_outline_with_report_ontology(outline: List[Dict[str, Any]], report_ontology: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for item in outline:
        concept = match_report_concept(item.get("source_heading", ""), item.get("content", ""), report_ontology)
        enriched = dict(item)
        enriched["report_concept"] = concept
        # Important: this classification never changes heading/order/structure.
        out.append(enriched)
    return out


def build_hierarchy_and_section_policy(classified_outline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Estimate hierarchy and sections from source levels, not ontology concepts.

    Policy:
    - Create multiple sections only if there are at least two level-1 groups and
      each resulting section has >= 2 slide candidates.
    - Otherwise use one section: "Nội dung chính".
    """
    if not classified_outline:
        return {"mode": "empty", "groups": []}

    top_level_count = sum(1 for x in classified_outline if int(x.get("level", 1)) <= 1)
    has_deep_structure = any(int(x.get("level", 1)) >= 2 for x in classified_outline)

    # First group items by nearest previous level-1 heading.
    raw_groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for item in classified_outline:
        level = int(item.get("level", 1))
        if level <= 1:
            if current:
                raw_groups.append(current)
            current = {"section_title": item.get("source_heading", "Nội dung"), "items": [item]}
        else:
            if current is None:
                current = {"section_title": "Nội dung chính", "items": []}
            current["items"].append(item)
    if current:
        raw_groups.append(current)

    # Each section needs at least two actual slide items.
    useful_groups = [g for g in raw_groups if len([x for x in g["items"] if not is_blank_or_placeholder(x.get("content", ""))]) >= 2]
    if not has_deep_structure or top_level_count < 2 or len(useful_groups) < 2:
        return {
            "mode": "single_section",
            "reason": "outline is shallow or section groups would be too small",
            "groups": [{"section_title": "Nội dung chính", "items": classified_outline}],
        }

    # Merge single-slide groups into nearest useful neighbor.
    merged: List[Dict[str, Any]] = []
    for g in raw_groups:
        count = len([x for x in g["items"] if not is_blank_or_placeholder(x.get("content", ""))])
        if count >= 2:
            merged.append(g)
        elif merged:
            merged[-1]["items"].extend(g["items"])
        elif raw_groups:
            # prepend to next useful group later
            if len(raw_groups) > 1:
                raw_groups[1]["items"] = g["items"] + raw_groups[1]["items"]
    if len(merged) < 2:
        return {
            "mode": "single_section",
            "reason": "after merging singleton groups, fewer than 2 valid sections remain",
            "groups": [{"section_title": "Nội dung chính", "items": classified_outline}],
        }
    return {"mode": "multi_section", "reason": "deep source outline with valid section sizes", "groups": merged}


# ---------------------------------------------------------------------------
# Presentation intent and layout selection
# ---------------------------------------------------------------------------


def infer_presentation_intent(item: Dict[str, Any], presentation_ontology: Dict[str, Any]) -> Dict[str, Any]:
    text = f"{item.get('source_heading','')} {item.get('content','')[:500]}".lower()
    concept_id = item.get("report_concept", {}).get("concept_id", "")
    best_intent: Optional[Dict[str, Any]] = None
    best_score = 0
    for intent in presentation_ontology.get("intents", []):
        score = 0
        for label in intent.get("labels", []):
            if str(label).lower() in text:
                score += 2
        # Soft mapping from report concepts to presentation intents.
        cid_map = {
            "current_situation": "explain_context",
            "implementation_results": "present_findings",
            "highlights": "present_findings",
            "limitations_causes": "highlight_risks",
            "warnings_risks": "highlight_risks",
            "measures_solutions": "recommend_actions",
            "proposals_recommendations": "recommend_actions",
            "tasks_assignment": "assign_responsibilities",
            "timeline_progress": "show_progression",
            "implementation_organization": "organize_implementation",
            "data_metrics": "show_metrics",
        }
        if cid_map.get(concept_id) == intent.get("id"):
            score += 3
        if score > best_score:
            best_score = score
            best_intent = intent
    if best_intent:
        return {"intent": best_intent["id"], "score": best_score, "shape": best_intent.get("preferred_content_shape", "bullets")}
    return {"intent": presentation_ontology.get("fallback_intent", "summarize_core_message"), "score": 0, "shape": "bullets"}


def content_shape_for_item(item: Dict[str, Any], intent_info: Dict[str, Any]) -> str:
    content = item.get("content", "")
    heading = item.get("source_heading", "")
    lines = [normalize_space(x) for x in content.splitlines() if normalize_space(x)]
    has_dates = bool(re.search(r"\b(20\d{2}|quý\s*[ivx\d]+|q[1-4]|tháng\s+\d+)\b", content, re.I))
    has_table_cues = "|" in content or re.search(r"\b(đơn vị chủ trì|đơn vị phối hợp|trách nhiệm|kinh phí|chỉ tiêu)\b", content, re.I)
    if intent_info["intent"] in {"assign_responsibilities", "show_metrics"} and has_table_cues:
        return "table"
    if intent_info["intent"] == "show_progression" and has_dates:
        return "timeline"
    if intent_info["intent"] in {"show_progression", "organize_implementation"}:
        return "steps"
    if intent_info["intent"] == "recommend_actions":
        return "numbered_columns"
    if intent_info["intent"] == "present_findings" and len(lines) >= 3:
        return "cards"
    return intent_info.get("shape", "bullets")


def registry_layouts(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    return registry.get("layouts", [])


def choose_layout(intent: str, shape: str, item_count: int, registry: Dict[str, Any]) -> str:
    layouts = registry_layouts(registry)
    candidates = []
    for layout in layouts:
        score = 0
        if intent in layout.get("intents", []):
            score += 5
        if shape in layout.get("data_shapes", []):
            score += 4
        cap = layout.get("capacity", {})
        max_items = cap.get("max_items") or cap.get("max_cells") or cap.get("max_columns") or cap.get("max_steps") or cap.get("max_milestones") or cap.get("max_rows")
        if max_items is None or item_count <= int(max_items):
            score += 2
        else:
            score -= (item_count - int(max_items))
        # Prefer specific grid/column sizes.
        lid = layout.get("id", "")
        if shape == "cards" and lid.startswith("grid-"):
            score += 1
            if str(min(max(item_count, 3), 6)) in lid:
                score += 2
        if shape == "numbered_columns" and lid.startswith("numbered-columns"):
            score += 1
        candidates.append((score, layout.get("id")))
    candidates.sort(reverse=True)
    if candidates and candidates[0][0] > 0:
        return candidates[0][1]
    return registry.get("selection_policy", {}).get("fallback_layout", "title-bullets")


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------


def safe_bulletize(text: str, max_items: int = 6, max_chars: int = 150) -> List[str]:
    lines = [normalize_space(re.sub(r"^[\-•*]\s*", "", x)) for x in text.splitlines() if normalize_space(x)]
    candidates: List[str] = []
    for line in lines:
        if len(line) < 8:
            continue
        if len(line) > max_chars * 1.7:
            candidates.extend(split_sentences(line))
        else:
            candidates.append(line)
    if not candidates:
        candidates = split_sentences(text)
    clean = []
    seen = set()
    for c in candidates:
        c = truncate_without_ellipsis(c, max_chars=max_chars)
        if len(c) < 8:
            continue
        key = c.lower()
        if key in seen:
            continue
        clean.append(c)
        seen.add(key)
        if len(clean) >= max_items:
            break
    return clean


def make_cards_from_bullets(bullets: List[str], max_cards: int = 6) -> List[Dict[str, str]]:
    cells = []
    for i, b in enumerate(bullets[:max_cards], 1):
        # Split heading/text if colon exists; otherwise first words become heading.
        if ":" in b and len(b.split(":", 1)[0]) <= 45:
            h, t = b.split(":", 1)
            heading = normalize_space(h)
            text = normalize_space(t)
        else:
            words = b.split()
            heading = " ".join(words[: min(4, len(words))])
            text = b
        cells.append({"heading": heading or f"Ý {i}", "text": truncate_without_ellipsis(text or b, 130)})
    return cells


def make_columns_from_bullets(bullets: List[str], max_cols: int = 4) -> List[Dict[str, Any]]:
    cols = []
    for i, b in enumerate(bullets[:max_cols], 1):
        if ":" in b and len(b.split(":", 1)[0]) <= 40:
            h, t = b.split(":", 1)
            cols.append({"heading": normalize_space(h), "text": truncate_without_ellipsis(t, 120), "bullets": []})
        else:
            cols.append({"heading": f"Bước {i}", "text": truncate_without_ellipsis(b, 120), "bullets": []})
    return cols


def make_steps_from_bullets(bullets: List[str], max_steps: int = 5) -> List[Dict[str, str]]:
    steps = []
    for i, b in enumerate(bullets[:max_steps], 1):
        steps.append({"heading": f"Bước {i}", "text": truncate_without_ellipsis(b, 125)})
    return steps


def make_milestones_from_text(text: str, bullets: List[str], max_items: int = 5) -> List[Dict[str, str]]:
    milestones = []
    for i, b in enumerate(bullets[:max_items], 1):
        m = re.search(r"\b(20\d{2}|Q[1-4]|quý\s*[IVX\d]+|tháng\s+\d+)\b", b, re.I)
        date = m.group(1) if m else f"M{i}"
        heading = truncate_without_ellipsis(b.replace(date, "").strip(" :-–—"), 35) or f"Mốc {i}"
        milestones.append({"date": date, "heading": heading, "text": truncate_without_ellipsis(b, 100)})
    return milestones


def maybe_make_table(item: Dict[str, Any], bullets: List[str]) -> Dict[str, Any]:
    content = item.get("content", "")
    rows: List[List[str]] = []
    # Parse pipe-separated rows if present.
    for line in content.splitlines():
        if "|" in line:
            cells = [normalize_space(c) for c in line.split("|") if normalize_space(c)]
            if len(cells) >= 2:
                rows.append(cells[:5])
    if rows:
        max_cols = max(len(r) for r in rows)
        headers = rows[0] if len(rows) > 1 else [f"Cột {i+1}" for i in range(max_cols)]
        data_rows = rows[1:] if len(rows) > 1 else rows
        return {"headers": headers, "rows": data_rows[:12]}
    # Fallback table for responsibility-like content.
    if re.search(r"đơn vị|trách nhiệm|chủ trì|phối hợp", content, re.I):
        return {"headers": ["Nội dung", "Ghi chú"], "rows": [[truncate_without_ellipsis(b, 80), ""] for b in bullets[:10]]}
    return {"headers": ["Nội dung"], "rows": [[b] for b in bullets[:10]]}


def create_slide_spec_from_task(task: Dict[str, Any]) -> Dict[str, Any]:
    item = task["source_item"]
    heading = item.get("source_heading", "Nội dung")
    content = item.get("content", "")
    intent = task["intent"]
    shape = task["shape"]
    layout = task["layout"]

    bullets = safe_bulletize(content, max_items=8, max_chars=145)
    if not bullets:
        return {"task_id": task["task_id"], "status": "skipped", "reason": "empty content after cleaning"}

    blocks: Dict[str, Any] = {}
    subtitle = ""  # no metadata subtitles; only meaningful content subtitles if source has a real short lead sentence.
    first_sentence = split_sentences(content)[0] if split_sentences(content) else ""
    if first_sentence and len(first_sentence) <= 90 and first_sentence.lower() not in heading.lower():
        # Still conservative. Avoid subtitles that are just metadata, numbering,
        # legal references, or administrative fragments.
        bad_subtitle = (
            re.match(r"^\d+(?:\.\d+)*[\.)]?$", first_sentence.strip())
            or re.match(r"^[a-zđ]\)$", first_sentence.strip(), re.I)
            or re.search(r"\b(task|layout|ontology|level|parent|theo đề mục)\b", first_sentence, re.I)
            or re.search(r"^(căn cứ|xét|số[:：]|hội đồng nhân dân|cộng hoà|cộng hòa|độc lập)", first_sentence, re.I)
        )
        if not bad_subtitle:
            subtitle = first_sentence

    if layout == "title-table" or shape == "table":
        blocks["table"] = maybe_make_table(item, bullets)
    elif layout.startswith("grid-") or shape == "cards":
        cap = 8 if layout == "grid-4x2" else int(re.search(r"grid-(\d+)", layout).group(1)) if re.search(r"grid-(\d+)", layout) else 4
        blocks["cells"] = make_cards_from_bullets(bullets, max_cards=cap)
    elif layout.startswith("numbered-columns") or shape == "numbered_columns":
        m = re.search(r"numbered-columns-(\d+)", layout)
        cap = int(m.group(1)) if m else 4
        blocks["columns"] = make_columns_from_bullets(bullets, max_cols=cap)
    elif layout == "timeline":
        blocks["milestones"] = make_milestones_from_text(content, bullets, max_items=6)
    elif layout in {"stair-progress", "stacked-stairs"} or shape == "steps":
        blocks["steps"] = make_steps_from_bullets(bullets, max_steps=6)
    elif layout == "conclusion":
        blocks["bullets"] = {"icon": "check", "items": bullets[:5]}
        blocks["conclusion"] = truncate_without_ellipsis("; ".join(bullets[:2]), 170)
    else:
        icon = "warning" if intent == "highlight_risks" else "check"
        blocks["bullets"] = {"icon": icon, "items": bullets[:7]}

    return {
        "task_id": task["task_id"],
        "status": "completed",
        "title": truncate_without_ellipsis(heading, 90),
        "subtitle": subtitle,
        "intent": intent,
        "layout": layout,
        "source_heading": heading,
        "report_concept": item.get("report_concept", {}),
        "blocks": blocks,
    }



# ---------------------------------------------------------------------------
# Long-document chunking and outline merge
# ---------------------------------------------------------------------------


def estimate_token_count(text: str) -> int:
    """Cheap Vietnamese-friendly token estimate.

    This is intentionally conservative. It is not model-token exact, but good
    enough to stop the pipeline from stuffing very long reports into one task.
    """
    if not text:
        return 0
    # Vietnamese whitespace tokenization is already usable for budgeting.
    words = re.findall(r"\S+", text)
    return int(len(words) * 1.25)


def split_long_block_by_paragraphs(block: str, max_chars: int) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", block) if p.strip()]
    if not paragraphs:
        return [block.strip()] if block.strip() else []
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for p in paragraphs:
        # Extremely long paragraph: cut by sentences.
        if len(p) > max_chars:
            if buf:
                chunks.append("\n\n".join(buf).strip())
                buf, size = [], 0
            sentences = split_sentences(p) or [p]
            sbuf: List[str] = []
            ssize = 0
            for s in sentences:
                if sbuf and ssize + len(s) + 2 > max_chars:
                    chunks.append(" ".join(sbuf).strip())
                    sbuf, ssize = [], 0
                sbuf.append(s)
                ssize += len(s) + 1
            if sbuf:
                chunks.append(" ".join(sbuf).strip())
            continue
        if buf and size + len(p) + 2 > max_chars:
            chunks.append("\n\n".join(buf).strip())
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 2
    if buf:
        chunks.append("\n\n".join(buf).strip())
    return chunks


def chunk_document_for_long_report(text: str, max_chars: int = 12000) -> List[Dict[str, Any]]:
    """Split a long report into chunks while respecting headings when possible.

    The unit of splitting is the real source outline. If a section is itself too
    large, it is split by paragraphs/sentences but keeps the source heading.
    """
    base_outline = split_by_source_outline(text)
    chunks: List[Dict[str, Any]] = []
    chunk_index = 1

    if not base_outline:
        base_outline = [{"id": "H001", "source_heading": "Nội dung chính", "level": 1, "numbering": "", "line_no": 1, "content": text}]

    current_parts: List[str] = []
    current_meta: List[Dict[str, Any]] = []
    current_size = 0

    def flush() -> None:
        nonlocal chunk_index, current_parts, current_meta, current_size
        if not current_parts:
            return
        chunk_text = "\n\n".join(current_parts).strip()
        chunks.append({
            "chunk_id": f"C{chunk_index:04d}",
            "text": chunk_text,
            "source_units": current_meta,
            "char_count": len(chunk_text),
            "estimated_tokens": estimate_token_count(chunk_text),
        })
        chunk_index += 1
        current_parts, current_meta, current_size = [], [], 0

    for unit in base_outline:
        heading = unit.get("source_heading", "Nội dung")
        level = int(unit.get("level", 1) or 1)
        numbering = unit.get("numbering", "")
        content = unit.get("content", "")
        if is_blank_or_placeholder(content):
            continue
        unit_text = f"{numbering + ' ' if numbering else ''}{heading}\n{content}".strip()
        if len(unit_text) > max_chars:
            flush()
            parts = split_long_block_by_paragraphs(content, max_chars=max_chars - len(heading) - 20)
            for j, part in enumerate(parts, 1):
                subheading = heading if len(parts) == 1 else f"{heading} (phần {j})"
                subtext = f"{numbering + ' ' if numbering else ''}{subheading}\n{part}".strip()
                chunks.append({
                    "chunk_id": f"C{chunk_index:04d}",
                    "text": subtext,
                    "source_units": [{"source_id": unit.get("id"), "source_heading": heading, "part": j, "level": level}],
                    "char_count": len(subtext),
                    "estimated_tokens": estimate_token_count(subtext),
                })
                chunk_index += 1
            continue
        if current_parts and current_size + len(unit_text) + 2 > max_chars:
            flush()
        current_parts.append(unit_text)
        current_meta.append({"source_id": unit.get("id"), "source_heading": heading, "level": level})
        current_size += len(unit_text) + 2
    flush()

    if not chunks and text.strip():
        for part in split_long_block_by_paragraphs(text, max_chars=max_chars):
            chunks.append({
                "chunk_id": f"C{len(chunks)+1:04d}",
                "text": part,
                "source_units": [],
                "char_count": len(part),
                "estimated_tokens": estimate_token_count(part),
            })
    return chunks


def extract_local_outline_from_chunk(chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
    local = split_by_source_outline(chunk.get("text", ""))
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(local, 1):
        content = item.get("content", "")
        if is_blank_or_placeholder(content):
            continue
        enriched = dict(item)
        enriched["id"] = f"{chunk['chunk_id']}_{i:03d}"
        enriched["chunk_id"] = chunk["chunk_id"]
        enriched["chunk_char_count"] = chunk.get("char_count", 0)
        enriched["chunk_estimated_tokens"] = chunk.get("estimated_tokens", 0)
        out.append(enriched)
    # If splitting by headings consumed the heading as title and left little content,
    # keep a fallback chunk unit.
    if not out and not is_blank_or_placeholder(chunk.get("text", "")):
        out.append({
            "id": f"{chunk['chunk_id']}_001",
            "source_heading": f"Nội dung {chunk['chunk_id']}",
            "level": 1,
            "numbering": "",
            "line_no": 0,
            "content": chunk.get("text", ""),
            "chunk_id": chunk["chunk_id"],
            "chunk_char_count": chunk.get("char_count", 0),
            "chunk_estimated_tokens": chunk.get("estimated_tokens", 0),
        })
    return out


def merge_local_outlines(local_outlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge chunk-local outline entries while preserving order.

    Adjacent entries with the same source heading are merged. This handles the
    case where one long source section was split into multiple chunks.
    """
    merged: List[Dict[str, Any]] = []
    for chunk_record in local_outlines:
        for item in chunk_record.get("outline", []):
            heading = normalize_space(item.get("source_heading", "Nội dung"))
            content = item.get("content", "").strip()
            if is_blank_or_placeholder(content):
                continue
            if merged and normalize_space(merged[-1].get("source_heading", "")) == heading:
                merged[-1]["content"] = (merged[-1].get("content", "") + "\n\n" + content).strip()
                merged[-1].setdefault("chunk_ids", []).append(item.get("chunk_id"))
                merged[-1]["char_count"] = len(merged[-1]["content"])
                merged[-1]["estimated_tokens"] = estimate_token_count(merged[-1]["content"])
            else:
                new = dict(item)
                new["id"] = f"H{len(merged)+1:04d}"
                new["source_heading"] = heading
                new["chunk_ids"] = [item.get("chunk_id")]
                new["char_count"] = len(content)
                new["estimated_tokens"] = estimate_token_count(content)
                merged.append(new)
    return merged


def split_outline_item_for_task_budget(item: Dict[str, Any], max_task_chars: int = 7000) -> List[Dict[str, Any]]:
    """Ensure one slide task never receives an unbounded text body."""
    content = item.get("content", "")
    if len(content) <= max_task_chars:
        return [item]
    parts = split_long_block_by_paragraphs(content, max_chars=max_task_chars)
    out: List[Dict[str, Any]] = []
    for i, part in enumerate(parts, 1):
        new = dict(item)
        new["id"] = f"{item.get('id')}_P{i:02d}"
        new["source_heading"] = f"{item.get('source_heading', 'Nội dung')} (phần {i})"
        new["content"] = part
        new["part_index"] = i
        new["part_count"] = len(parts)
        new["char_count"] = len(part)
        new["estimated_tokens"] = estimate_token_count(part)
        out.append(new)
    return out

# ---------------------------------------------------------------------------
# LangGraph nodes
# ---------------------------------------------------------------------------


def node_load_assets(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    state["report_ontology"] = load_json_or_default(state.get("report_ontology_path", ""), DEFAULT_REPORT_ONTOLOGY, out_dir, "report_document_ontology_clean_roles.json")
    state["presentation_ontology"] = load_json_or_default(state.get("presentation_ontology_path", ""), DEFAULT_PRESENTATION_ONTOLOGY, out_dir, "presentation_intent_ontology.json")
    state["layout_registry"] = load_json_or_default(state.get("layout_registry_path", ""), DEFAULT_LAYOUT_REGISTRY, out_dir, "layout_registry_clean_roles.json")
    state.setdefault("validation_reports", [])
    state.setdefault("repair_log", [])
    return state


def node_extract_text(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    source_text = extract_input_text(state.get("input_path", ""), state.get("input_text", ""), state.get("use_stdin", False))
    state["source_text"] = source_text
    source_path = out_dir / "source_text.txt"
    source_path.write_text(source_text, encoding="utf-8")
    state["source_text_path"] = str(source_path)
    return state


def node_clean_text(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    cleaned = strip_admin_boilerplate(state.get("source_text", ""), state["report_ontology"])
    state["cleaned_text"] = cleaned
    path = out_dir / "cleaned_text.txt"
    path.write_text(cleaned, encoding="utf-8")
    state["cleaned_text_path"] = str(path)
    state.setdefault("validation_reports", []).append({"stage": "clean_text", "removed_boilerplate": len(state.get("source_text", "")) - len(cleaned)})
    return state


def node_chunk_document(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    max_chars = int(state.get("max_chunk_chars", 12000) or 12000)
    chunks = chunk_document_for_long_report(state.get("cleaned_text", ""), max_chars=max_chars)
    state["document_chunks"] = chunks
    chunks_dir = ensure_dir(out_dir / "chunks")
    for chunk in chunks:
        (chunks_dir / f"{chunk['chunk_id']}.txt").write_text(chunk.get("text", ""), encoding="utf-8")
    path = out_dir / "document_chunks.json"
    save_json(path, [{k: v for k, v in c.items() if k != "text"} for c in chunks])
    state["chunks_path"] = str(path)
    state.setdefault("validation_reports", []).append({
        "stage": "chunk_document",
        "chunk_count": len(chunks),
        "max_chunk_chars": max_chars,
        "max_chunk_tokens_est": max([c.get("estimated_tokens", 0) for c in chunks] or [0]),
    })
    return state


def node_extract_local_outlines(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    local_records: List[Dict[str, Any]] = []
    local_dir = ensure_dir(out_dir / "local_outlines")
    for chunk in state.get("document_chunks", []):
        outline = extract_local_outline_from_chunk(chunk)
        record = {"chunk_id": chunk["chunk_id"], "outline": outline}
        local_records.append(record)
        save_json(local_dir / f"{chunk['chunk_id']}_outline.json", record)
    state["local_outlines"] = local_records
    path = out_dir / "local_outlines.json"
    save_json(path, local_records)
    state["local_outlines_path"] = str(path)
    return state


def node_merge_outline(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    outline = merge_local_outlines(state.get("local_outlines", []))
    state["source_outline"] = outline
    path = out_dir / "source_outline_merged.json"
    save_json(path, outline)
    state["outline_path"] = str(path)
    state.setdefault("validation_reports", []).append({
        "stage": "merge_outline",
        "merged_units": len(outline),
        "note": "Global outline is merged from local chunk outlines; long sections may be split again at task budget stage.",
    })
    return state


def node_extract_outline(state: PipelineState) -> PipelineState:
    # Backward-compatible alias. New graph uses chunk -> local outline -> merge.
    return node_merge_outline(node_extract_local_outlines(node_chunk_document(state)))


def node_classify_with_report_ontology(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    classified = classify_outline_with_report_ontology(state.get("source_outline", []), state["report_ontology"])
    state["classified_outline"] = classified
    path = out_dir / "classified_outline.json"
    save_json(path, classified)
    state["classified_outline_path"] = str(path)
    state.setdefault("validation_reports", []).append({
        "stage": "report_ontology_classification",
        "note": "Report ontology used only for labels/classification, not for creating deck structure.",
        "items": len(classified),
    })
    return state


def node_build_hierarchy_policy(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    hierarchy = build_hierarchy_and_section_policy(state.get("classified_outline", []))
    state["hierarchy"] = hierarchy
    state["section_groups"] = hierarchy.get("groups", [])
    path = out_dir / "outline_hierarchy_and_section_policy.json"
    save_json(path, hierarchy)
    state["hierarchy_path"] = str(path)
    state.setdefault("validation_reports", []).append({"stage": "section_policy", "mode": hierarchy.get("mode"), "reason": hierarchy.get("reason")})
    return state


def node_create_task_plan(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    max_task_chars = int(state.get("max_task_chars", 7000) or 7000)
    tasks: List[Dict[str, Any]] = []
    for g_idx, group in enumerate(state.get("section_groups", []), 1):
        expanded_items: List[Dict[str, Any]] = []
        for raw_item in group.get("items", []):
            if should_skip_outline_item(raw_item):
                state.setdefault("validation_reports", []).append({
                    "stage": "skip_source_unit",
                    "reason": "legal_preamble_or_boilerplate",
                    "heading": raw_item.get("source_heading"),
                    "source_item_id": raw_item.get("id"),
                })
                continue
            expanded_items.extend(split_outline_item_for_task_budget(raw_item, max_task_chars=max_task_chars))
        for item in expanded_items:
            if is_blank_or_placeholder(item.get("content", "")):
                continue
            intent_info = infer_presentation_intent(item, state["presentation_ontology"])
            shape = content_shape_for_item(item, intent_info)
            numbering = str(item.get("numbering", ""))
            # Legal/normative documents should be summarized article-by-article.
            # Do not let generic words like "thực hiện" force process layouts
            # such as stair-progress for every legal article.
            if re.match(r"^Điều\s+\d+", numbering, re.I):
                intent_info = {"intent": "summarize_core_message", "score": intent_info.get("score", 0), "shape": "bullets"}
                shape = "bullets"
            elif re.match(r"^PHỤ\s+LỤC", numbering, re.I):
                intent_info = {"intent": "show_metrics", "score": intent_info.get("score", 0), "shape": "table"}
                shape = "table" if "|" in item.get("content", "") else "bullets"
            approx_items = len(safe_bulletize(item.get("content", ""), max_items=10))
            layout = choose_layout(intent_info["intent"], shape, approx_items, state["layout_registry"])
            if re.match(r"^Điều\s+\d+", numbering, re.I):
                layout = "title-bullets"
            elif re.match(r"^PHỤ\s+LỤC", numbering, re.I) and shape == "table":
                layout = "title-table"
            tasks.append({
                "task_id": f"T{len(tasks)+1:03d}",
                "section_title": group.get("section_title", "Nội dung chính"),
                "source_item_id": item.get("id"),
                "source_heading": item.get("source_heading"),
                "source_item": item,
                "report_concept": item.get("report_concept"),
                "intent": intent_info["intent"],
                "intent_score": intent_info.get("score", 0),
                "shape": shape,
                "layout": layout,
                "char_count": len(item.get("content", "")),
                "estimated_tokens": estimate_token_count(item.get("content", "")),
                "chunk_ids": item.get("chunk_ids", [item.get("chunk_id")]),
                "part_index": item.get("part_index"),
                "part_count": item.get("part_count"),
                "ontology_roles": {
                    "report_ontology": "classification_only",
                    "presentation_ontology": "intent_inference",
                    "layout_registry": "layout_capability_selection",
                },
            })
    task_plan = {"tasks": tasks, "policy": {"skip_empty": True, "one_task_per_source_unit_or_budget_part": True, "max_task_chars": max_task_chars}}
    state["task_plan"] = task_plan
    state["pending_tasks"] = tasks.copy()
    state["completed_tasks"] = []
    path = out_dir / "task_plan.json"
    save_json(path, task_plan)
    state["task_plan_path"] = str(path)
    return state


def node_pop_task(state: PipelineState) -> PipelineState:
    pending = state.get("pending_tasks", [])
    if pending:
        state["current_task"] = pending.pop(0)
        state["pending_tasks"] = pending
    else:
        state["current_task"] = {}
    return state


def route_has_task(state: PipelineState) -> str:
    return "execute" if state.get("current_task") else "aggregate"


def node_execute_task(state: PipelineState) -> PipelineState:
    task = state.get("current_task", {})
    if not task:
        return state
    spec = create_slide_spec_from_task(task)
    state["current_task_output"] = spec
    return state


def validate_slide_spec(spec: Dict[str, Any]) -> List[str]:
    errors = []
    if spec.get("status") != "completed":
        return errors
    if is_blank_or_placeholder(spec.get("title")):
        errors.append("missing_title")
    blocks = spec.get("blocks", {})
    has_content = False
    for value in blocks.values():
        if isinstance(value, list) and value:
            has_content = True
        elif isinstance(value, dict) and value:
            has_content = True
        elif isinstance(value, str) and not is_blank_or_placeholder(value):
            has_content = True
    if not has_content:
        errors.append("empty_blocks")
    if spec.get("subtitle") and re.search(r"\b(task|layout|ontology|level|parent|theo đề mục)\b", spec.get("subtitle", ""), re.I):
        errors.append("metadata_subtitle")
    # No ellipsis truncation.
    serialized = json.dumps(spec, ensure_ascii=False)
    if "..." in serialized or "…" in serialized:
        errors.append("ellipsis_found")
    return errors


def node_validate_repair_task(state: PipelineState) -> PipelineState:
    spec = state.get("current_task_output", {})
    task = state.get("current_task", {})
    errors = validate_slide_spec(spec)
    if errors:
        state.setdefault("repair_log", []).append(f"{task.get('task_id')}: {errors}")
        if "metadata_subtitle" in errors:
            spec["subtitle"] = ""
        if "ellipsis_found" in errors:
            # Remove ellipsis conservatively.
            def clean_obj(x):
                if isinstance(x, str):
                    return x.replace("...", "").replace("…", "").strip()
                if isinstance(x, list):
                    return [clean_obj(v) for v in x]
                if isinstance(x, dict):
                    return {k: clean_obj(v) for k, v in x.items()}
                return x
            spec = clean_obj(spec)
        if "empty_blocks" in errors or "missing_title" in errors:
            spec = {"task_id": task.get("task_id"), "status": "skipped", "reason": ",".join(errors)}
    if spec.get("status") == "completed":
        state.setdefault("completed_tasks", []).append(spec)
    else:
        state.setdefault("validation_reports", []).append({"stage": "task_skipped", "task_id": task.get("task_id"), "reason": spec.get("reason")})

    out_dir = ensure_dir(state["out_dir"])
    task_dir = ensure_dir(out_dir / "task_outputs")
    save_json(task_dir / f"{task.get('task_id','unknown')}.json", spec)
    return state


def build_fallback_slide_from_source(state: PipelineState) -> Dict[str, Any]:
    """Create a real content slide when all planned tasks were skipped.

    This avoids showing the internal error sentence `Không tìm thấy mục...` to the
    user. It uses cleaned text first, then raw source text, after applying the
    same boilerplate-aware bulletization.
    """
    source = state.get("cleaned_text") or state.get("source_text") or ""
    source = strip_admin_boilerplate(source, state.get("report_ontology", DEFAULT_REPORT_ONTOLOGY))
    if is_legal_heading_or_body(source):
        source = cut_to_legal_body(source)
    bullets = safe_bulletize(source, max_items=7, max_chars=180)
    if not bullets:
        residual_lines = []
        for line in source.splitlines():
            residual = meaningful_after_boilerplate_strip(line)
            if residual and len(residual) >= 8:
                residual_lines.append(truncate_without_ellipsis(residual, 180))
            if len(residual_lines) >= 7:
                break
        bullets = residual_lines
    if not bullets:
        bullets = ["Văn bản đầu vào chưa có đoạn nội dung đủ dài để tạo slide sau khi loại phần hành chính."]
    return {
        "title": "Nội dung chính",
        "subtitle": "",
        "intent": "summarize_core_message",
        "layout": "title-bullets",
        "blocks": {"bullets": bullets},
        "task_id": "FALLBACK",
        "status": "completed",
    }


def group_completed_slides_by_section(tasks: List[Dict[str, Any]], task_plan: Dict[str, Any], hierarchy: Dict[str, Any]) -> List[Dict[str, Any]]:
    task_to_section = {t["task_id"]: t.get("section_title", "Nội dung chính") for t in task_plan.get("tasks", [])}
    groups: List[Dict[str, Any]] = []
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    for spec in tasks:
        sec = task_to_section.get(spec.get("task_id"), "Nội dung chính")
        by_title.setdefault(sec, []).append(spec)
    # Enforce min two-slide section after task skipping.
    valid_multi = [(k, v) for k, v in by_title.items() if len(v) >= 2]
    if len(valid_multi) < 2:
        return [{"section_title": "Nội dung chính", "slides": tasks}]
    for sec, slides in valid_multi:
        groups.append({"section_title": sec, "slides": slides})
    # Merge singleton leftover.
    for sec, slides in by_title.items():
        if len(slides) < 2 and groups:
            groups[-1]["slides"].extend(slides)
    return groups


def maybe_infer_psl_from_template(state: PipelineState, default_psl: str, out_dir: Path) -> Tuple[str, Dict[str, Any]]:
    """Return PSL, optionally inferred from uploaded sample PPTX.

    This keeps style inference inside the pipeline for CLI/automation use.
    Streamlit may also rerender with the same helper, but the state keys here make
    the pipeline self-contained when `template_pptx_path` is provided.
    """
    template_path = state.get("template_pptx_path") or ""
    if not state.get("infer_template_style") or not template_path:
        return default_psl, {}

    try:
        import importlib.util
        helper_path = Path(__file__).resolve().parent / "pptx_profile_to_psl.py"
        spec = importlib.util.spec_from_file_location("pptx_profile_to_psl_pipeline", str(helper_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot import PPTX style helper from {helper_path}")
        helper = importlib.util.module_from_spec(spec)
        sys.modules["pptx_profile_to_psl_pipeline"] = helper
        spec.loader.exec_module(helper)  # type: ignore[attr-defined]
        psl_text, profile, bg_path = helper.infer_template_psl(
            template_path,
            out_dir,
            theme_name="uploaded-pptx-template",
            extract_background=bool(state.get("extract_template_background", True)),
        )
        state["template_profile_path"] = str(out_dir / "template_profile.json")
        if bg_path:
            state["template_background_path"] = str(bg_path)
        state.setdefault("validation_reports", []).append({
            "stage": "infer_template_style",
            "source_pptx": str(template_path),
            "profile_path": state.get("template_profile_path"),
            "background_asset": state.get("template_background_path", ""),
            "likely_font_family": (profile.get("inference") or {}).get("likely_font_family"),
            "likely_palette": (profile.get("inference") or {}).get("likely_palette"),
        })
        return psl_text, profile
    except Exception as exc:
        state.setdefault("repair_log", []).append(f"Template style inference failed; using default PSL. Error: {exc}")
        return default_psl, {}


def node_aggregate_deck(state: PipelineState) -> PipelineState:
    out_dir = ensure_dir(state["out_dir"])
    completed = state.get("completed_tasks", [])
    section_groups = group_completed_slides_by_section(completed, state.get("task_plan", {}), state.get("hierarchy", {}))

    if not completed:
        fallback_slide = build_fallback_slide_from_source(state)
        section_groups = [{
            "section_title": "Nội dung chính",
            "slides": [fallback_slide],
        }]
        state.setdefault("validation_reports", []).append({
            "stage": "fallback_content_slide",
            "reason": "all_tasks_skipped_after_validation_or_cleaning",
            "bullet_count": len(fallback_slide.get("blocks", {}).get("bullets", [])),
            "note": "Generated fallback from cleaned/source text instead of showing an internal no-content error."
        })

    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["pml_escape"] = pml_escape_filter
    template = env.from_string(DECK_TEMPLATE)
    pml_text = template.render(title="Tóm tắt báo cáo", section_groups=section_groups)
    psl_text, _template_profile = maybe_infer_psl_from_template(state, DEFAULT_PSL, out_dir)

    pml_path = out_dir / "generated.pml"
    psl_path = out_dir / "corporate.psl"
    pml_path.write_text(pml_text, encoding="utf-8")
    psl_path.write_text(psl_text, encoding="utf-8")
    state["pml_text"] = pml_text
    state["psl_text"] = psl_text
    state["pml_path"] = str(pml_path)
    state["psl_path"] = str(psl_path)

    state.setdefault("validation_reports", []).append({
        "stage": "aggregate_deck",
        "completed_slides": len(completed),
        "sections": [{"title": g["section_title"], "slide_count": len(g.get("slides", []))} for g in section_groups],
    })
    return state


# ---------------------------------------------------------------------------
# Graph + CLI
# ---------------------------------------------------------------------------


def build_graph():
    END, START, StateGraph = require_langgraph()
    graph = StateGraph(PipelineState)
    graph.add_node("load_assets", node_load_assets)
    graph.add_node("extract_text", node_extract_text)
    graph.add_node("clean_text", node_clean_text)
    graph.add_node("chunk_document", node_chunk_document)
    graph.add_node("extract_local_outlines", node_extract_local_outlines)
    graph.add_node("merge_outline", node_merge_outline)
    graph.add_node("classify_with_report_ontology", node_classify_with_report_ontology)
    graph.add_node("build_hierarchy_policy", node_build_hierarchy_policy)
    graph.add_node("create_task_plan", node_create_task_plan)
    graph.add_node("pop_task", node_pop_task)
    graph.add_node("execute_task", node_execute_task)
    graph.add_node("validate_repair_task", node_validate_repair_task)
    graph.add_node("aggregate_deck", node_aggregate_deck)
    graph.add_node("validate_repair_pml", node_validate_repair_pml)
    graph.add_node("render_deck", node_render_deck)

    graph.add_edge(START, "load_assets")
    graph.add_edge("load_assets", "extract_text")
    graph.add_edge("extract_text", "clean_text")
    graph.add_edge("clean_text", "chunk_document")
    graph.add_edge("chunk_document", "extract_local_outlines")
    graph.add_edge("extract_local_outlines", "merge_outline")
    graph.add_edge("merge_outline", "classify_with_report_ontology")
    graph.add_edge("classify_with_report_ontology", "build_hierarchy_policy")
    graph.add_edge("build_hierarchy_policy", "create_task_plan")
    graph.add_edge("create_task_plan", "pop_task")
    graph.add_conditional_edges("pop_task", route_has_task, {"execute": "execute_task", "aggregate": "aggregate_deck"})
    graph.add_edge("execute_task", "validate_repair_task")
    graph.add_edge("validate_repair_task", "pop_task")
    graph.add_edge("aggregate_deck", "validate_repair_pml")
    graph.add_edge("validate_repair_pml", "render_deck")
    graph.add_edge("render_deck", END)
    return graph.compile()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-report task graph slide generator with validate/repair")
    parser.add_argument("input", nargs="?", default="", help="Input .pdf/.docx/.txt/.md file")
    parser.add_argument("--text", default="", help="Plain text input")
    parser.add_argument("--stdin", action="store_true", help="Read plain text from stdin")
    parser.add_argument("--renderer", required=True, help="Path to DSL renderer .py")
    parser.add_argument("--out-dir", default="out_long_report_validate_repair", help="Output directory")
    parser.add_argument("--report-ontology", default="", help="Optional report/document ontology JSON")
    parser.add_argument("--presentation-ontology", default="", help="Optional presentation intent ontology JSON")
    parser.add_argument("--layout-registry", default="", help="Optional layout registry JSON")
    parser.add_argument("--model", default="gemini-2.0-flash", help="Reserved for future LLM nodes")
    parser.add_argument("--mock", action="store_true", help="Compatibility flag; this pipeline is deterministic by default")
    parser.add_argument("--max-chunk-chars", type=int, default=12000, help="Maximum characters per document chunk before local outline extraction")
    parser.add_argument("--max-task-chars", type=int, default=7000, help="Maximum characters per independent slide task")
    parser.add_argument("--template-pptx", default="", help="Optional PPTX sample used to infer PSL style")
    parser.add_argument("--infer-template-style", action="store_true", help="Infer PSL from --template-pptx")
    parser.add_argument("--no-template-background", action="store_true", help="Do not extract large background images from template PPTX")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = build_graph()
    final = app.invoke({
        "input_path": args.input,
        "input_text": args.text,
        "use_stdin": args.stdin,
        "renderer_path": args.renderer,
        "out_dir": args.out_dir,
        "report_ontology_path": args.report_ontology,
        "presentation_ontology_path": args.presentation_ontology,
        "layout_registry_path": args.layout_registry,
        "model": args.model,
        "mock": args.mock,
        "max_chunk_chars": args.max_chunk_chars,
        "max_task_chars": args.max_task_chars,
        "template_pptx_path": args.template_pptx,
        "infer_template_style": bool(args.infer_template_style and args.template_pptx),
        "extract_template_background": not args.no_template_background,
        "validation_reports": [],
        "repair_log": [],
    })
    print("Generated:")
    for key in ["pml_path", "html_path", "pptx_path", "md_path", "validation_report_path"]:
        if final.get(key):
            print(f"- {key}: {final[key]}")


if __name__ == "__main__":
    main()
