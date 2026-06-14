#!/usr/bin/env python3
"""
Streamlit UI for ontology/task-graph slide generation
=====================================================

This app wraps the LangGraph pipeline and the PML/PSL renderer.
It supports:
- Upload PDF/DOCX/TXT/MD or paste plain text.
- Select visual style from a selectbox.
- Select ontology/registry JSON files.
- Run mock mode or Gemini mode.
- Download generated PML/PSL/HTML/PPTX/Markdown and validation artifacts.
- Preview generated HTML directly inside the app after rendering.

Run:
    pip install -U streamlit langgraph google-genai jinja2 python-pptx python-docx pypdf
    streamlit run streamlit_slide_generator_app_long_report.py

Recommended renderer:
    demo_dsl_inline_markdown_styles.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components


APP_DIR = Path(__file__).resolve().parent
DEFAULT_PIPELINE_PATH = APP_DIR / "demo_task_graph_legal_article_pipeline_long_llm_ontology.py"
DEFAULT_RENDERER_PATH = APP_DIR / "demo_dsl_inline_markdown_styles.py"
DEFAULT_REPORT_ONTOLOGY_RICH = APP_DIR / "report_document_ontology_clean_roles.json"
DEFAULT_PRESENTATION_ONTOLOGY = APP_DIR / "presentation_intent_ontology.json"
DEFAULT_LAYOUT_REGISTRY = APP_DIR / "layout_registry_clean_roles.json"
DEFAULT_PPTX_STYLE_INFERER = APP_DIR / "pptx_profile_to_psl.py"


STYLE_PRESETS: Dict[str, Dict[str, str]] = {
    "Corporate Blue": {
        "primary": "#003A8C",
        "secondary": "#E6F0FF",
        "text": "#1F1F1F",
        "muted": "#666666",
        "background": "#FFFFFF",
        "white": "#FFFFFF",
    },
    "Emerald Tech": {
        "primary": "#0F766E",
        "secondary": "#CCFBF1",
        "text": "#111827",
        "muted": "#64748B",
        "background": "#F8FAFC",
        "white": "#FFFFFF",
    },
    "Military Olive": {
        "primary": "#3F4A2F",
        "secondary": "#E7E9DC",
        "text": "#1F2933",
        "muted": "#6B7280",
        "background": "#FBFAF4",
        "white": "#FFFFFF",
    },
    "Minimal Slate": {
        "primary": "#334155",
        "secondary": "#E2E8F0",
        "text": "#0F172A",
        "muted": "#64748B",
        "background": "#FFFFFF",
        "white": "#FFFFFF",
    },
    "Warm Report": {
        "primary": "#9A3412",
        "secondary": "#FFEDD5",
        "text": "#1C1917",
        "muted": "#78716C",
        "background": "#FFFBF5",
        "white": "#FFFFFF",
    },
}


def import_module_from_path(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


@st.cache_resource(show_spinner=False)
def load_pipeline_module(path_str: str):
    return import_module_from_path(Path(path_str), "task_graph_pipeline_streamlit")


@st.cache_resource(show_spinner=False)
def load_renderer_module(path_str: str):
    return import_module_from_path(Path(path_str), "pml_renderer_streamlit")


@st.cache_resource(show_spinner=False)
def load_pptx_style_inferer(path_str: str):
    return import_module_from_path(Path(path_str), "pptx_profile_to_psl_streamlit")


def save_uploaded_file(uploaded_file, out_dir: Path) -> Optional[Path]:
    if uploaded_file is None:
        return None
    safe_name = re.sub(r"[^\w.\-() ]+", "_", uploaded_file.name)
    path = out_dir / "input" / safe_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(uploaded_file.getbuffer())
    return path


def save_uploaded_json(uploaded_file, out_dir: Path, fallback: Path, filename: str) -> str:
    if uploaded_file is None:
        return str(fallback) if fallback.exists() else ""
    path = out_dir / "assets" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    data = uploaded_file.getvalue()
    # Validate JSON early so user sees a clear error.
    json.loads(data.decode("utf-8"))
    path.write_bytes(data)
    return str(path)


def style_psl_from_preset(base_psl: str, preset_name: str) -> str:
    colors = STYLE_PRESETS[preset_name]
    psl = base_psl

    replacements = {
        r'primary:\s*"#[0-9A-Fa-f]{6}"': f'primary: "{colors["primary"]}"',
        r'secondary:\s*"#[0-9A-Fa-f]{6}"': f'secondary: "{colors["secondary"]}"',
        r'text:\s*"#[0-9A-Fa-f]{6}"': f'text: "{colors["text"]}"',
        r'muted:\s*"#[0-9A-Fa-f]{6}"': f'muted: "{colors["muted"]}"',
        r'white:\s*"#[0-9A-Fa-f]{6}"': f'white: "{colors["white"]}"',
        r'background:\s*"#[0-9A-Fa-f]{6}"': f'background: "{colors["background"]}"',
    }
    for pattern, repl in replacements.items():
        psl = re.sub(pattern, repl, psl, count=1)
    return psl


def rerender_outputs(renderer_path: Path, pml_text: str, psl_text: str, out_dir: Path) -> Dict[str, Path]:
    renderer = load_renderer_module(str(renderer_path))
    required = ["parse_pml", "parse_psl", "build_render_ir", "render_html", "render_pptx"]
    missing = [name for name in required if not hasattr(renderer, name)]
    if missing:
        raise RuntimeError(f"Renderer thiếu hàm: {missing}")

    doc = renderer.parse_pml(pml_text)
    theme = renderer.parse_psl(psl_text)
    render_ir = renderer.build_render_ir(doc, theme)

    html_path = out_dir / "output.html"
    pptx_path = out_dir / "output.pptx"
    md_path = out_dir / "output.md"
    pml_path = out_dir / "generated.pml"
    psl_path = out_dir / "corporate.psl"

    pml_path.write_text(pml_text, encoding="utf-8")
    psl_path.write_text(psl_text, encoding="utf-8")
    renderer.render_html(render_ir, str(html_path))
    renderer.render_pptx(render_ir, str(pptx_path))
    if hasattr(renderer, "render_markdown"):
        renderer.render_markdown(render_ir, str(md_path))

    outputs = {"pml": pml_path, "psl": psl_path, "html": html_path, "pptx": pptx_path}
    if md_path.exists():
        outputs["md"] = md_path
    return outputs


def file_download_button(label: str, path: Path, mime: str) -> None:
    if path.exists():
        st.download_button(label, data=path.read_bytes(), file_name=path.name, mime=mime)


def preview_html_file(path: Path, height: int = 760) -> None:
    """Render generated HTML inside Streamlit for immediate inspection."""
    if not path.exists():
        st.info("Chưa có file HTML để preview.")
        return

    html_text = path.read_text(encoding="utf-8")
    st.subheader("Preview HTML")
    st.caption("Bản preview này dùng chính file HTML đã sinh; nếu slide rộng 1280px, có thể kéo ngang trong khung preview.")
    components.html(
        f"""
        <div style="width: 100%; overflow: auto; background: #f3f4f6; padding: 12px; box-sizing: border-box;">
            {html_text}
        </div>
        """,
        height=height,
        scrolling=True,
    )


def main() -> None:
    st.set_page_config(page_title="Ontology Slide Generator", page_icon="📊", layout="wide")
    st.title("📊 Slide Generator (Legal Article + LLM Restored)")
    st.caption("Input báo cáo dài → chunk/task graph → PML/PSL → HTML/PPTX/Markdown. Có thể chọn preset hoặc upload PPTX mẫu để infer PSL.")

    with st.sidebar:
        st.header("Cấu hình")
        style_name = st.selectbox("Style / Theme preset", list(STYLE_PRESETS.keys()), index=0)
        style_source = st.radio(
            "Nguồn style",
            ["Preset", "Infer từ PPTX mẫu"],
            index=0,
            help="Nếu chọn infer, app sẽ dùng pptx_profile_inferer để suy ra font/màu/kích thước và sinh PSL mới.",
        )
        template_pptx_upload = st.file_uploader("PPTX mẫu để infer style", type=["pptx"], key="template_pptx")
        extract_template_background = st.checkbox(
            "Thử lấy ảnh nền lớn từ PPTX mẫu",
            value=True,
            help="Nếu mẫu có ảnh lớn phủ slide, app sẽ lưu asset và tham chiếu trong PSL background_image.",
        )
        llm_mode = st.selectbox(
            "LLM mode",
            ["gemini", "heuristic", "mock"],
            index=0,
            help="gemini = gọi Gemini cho từng Điều/Phụ lục; heuristic/mock = không gọi LLM và sẽ được log rõ.",
        )
        mock = llm_mode == "mock"
        model = st.text_input("Gemini model", value="gemini-2.0-flash")
        st.caption("Nếu chọn gemini, cần đặt GEMINI_API_KEY trong môi trường. Pipeline sẽ báo lỗi nếu thiếu key, không fallback âm thầm.")

        st.divider()
        st.subheader("Long report")
        max_chunk_chars = st.number_input(
            "Max chunk chars",
            min_value=2_000,
            max_value=50_000,
            value=12_000,
            step=1_000,
            help="Giới hạn độ dài mỗi chunk khi một Điều/Phụ lục quá dài. Pipeline sẽ gọi LLM cho từng chunk rồi gọi LLM để gộp.",
        )
        max_task_chars = st.number_input(
            "Max task chars",
            min_value=1_000,
            max_value=30_000,
            value=7_000,
            step=500,
            help="Nếu một Điều/Phụ lục dài hơn ngưỡng này, pipeline chuyển sang map-reduce: tóm tắt từng chunk rồi gộp bằng LLM.",
        )

        st.divider()
        st.subheader("Module paths")
        renderer_path_str = st.text_input("Renderer path", value=str(DEFAULT_RENDERER_PATH))
        pipeline_path_str = st.text_input("Pipeline path", value=str(DEFAULT_PIPELINE_PATH))
        pptx_style_inferer_path_str = st.text_input("PPTX style inferer path", value=str(DEFAULT_PPTX_STYLE_INFERER))

        st.divider()
        st.subheader("Preview")
        show_html_preview = st.checkbox("Hiện preview HTML sau khi sinh", value=True)
        preview_height = st.slider("Chiều cao preview", min_value=420, max_value=1200, value=760, step=40)

        st.divider()
        st.subheader("Ontology / Registry")
        report_ontology_upload = st.file_uploader("Report ontology JSON", type=["json"], key="report_ontology")
        presentation_ontology_upload = st.file_uploader("Presentation ontology JSON", type=["json"], key="presentation_ontology")
        layout_registry_upload = st.file_uploader("Layout registry JSON", type=["json"], key="layout_registry")

    tab_text, tab_file = st.tabs(["Nhập plain text", "Upload file"])
    with tab_text:
        input_text = st.text_area(
            "Nội dung báo cáo/văn bản hành chính",
            height=260,
            placeholder="Ví dụ: Đặc điểm tình hình: ... Kết quả nổi bật: ... Biện pháp: ... Tổ chức thực hiện: ...",
        )
    with tab_file:
        uploaded_input = st.file_uploader("Upload PDF/DOCX/TXT/MD", type=["pdf", "docx", "txt", "md", "markdown"])

    col_a, col_b = st.columns([1, 3])
    with col_a:
        run = st.button("🚀 Sinh slide", type="primary", use_container_width=True)
    with col_b:
        st.info("Gợi ý: dùng `demo_dsl_inline_markdown_styles.py` để có Markdown phục vụ G-Eval và hỗ trợ inline **bold**/*italic*.")

    if not run:
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = APP_DIR / "streamlit_outputs" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        pipeline_path = Path(pipeline_path_str)
        renderer_path = Path(renderer_path_str)
        if not pipeline_path.exists():
            raise FileNotFoundError(f"Không tìm thấy pipeline: {pipeline_path}")
        if not renderer_path.exists():
            raise FileNotFoundError(f"Không tìm thấy renderer: {renderer_path}")
        pptx_style_inferer_path = Path(pptx_style_inferer_path_str)
        if style_source == "Infer từ PPTX mẫu" and not pptx_style_inferer_path.exists():
            raise FileNotFoundError(f"Không tìm thấy PPTX style inferer: {pptx_style_inferer_path}")

        input_path = save_uploaded_file(uploaded_input, out_dir)
        template_pptx_path = save_uploaded_file(template_pptx_upload, out_dir)
        if not input_text.strip() and input_path is None and not mock:
            st.warning("Hãy nhập plain text hoặc upload file, hoặc bật mock mode.")
            return

        report_ontology_path = save_uploaded_json(
            report_ontology_upload,
            out_dir,
            DEFAULT_REPORT_ONTOLOGY_RICH,
            "report_domain_ontology.json",
        )
        presentation_ontology_path = save_uploaded_json(
            presentation_ontology_upload,
            out_dir,
            DEFAULT_PRESENTATION_ONTOLOGY,
            "presentation_intent_ontology.json",
        )
        layout_registry_path = save_uploaded_json(
            layout_registry_upload,
            out_dir,
            DEFAULT_LAYOUT_REGISTRY,
            "layout_registry.json",
        )

        with st.spinner("Đang chạy LangGraph pipeline..."):
            pipeline = load_pipeline_module(str(pipeline_path))
            graph = pipeline.build_graph()
            initial_state = {
                # Send both old/new input keys so older and newer pipelines can run.
                "input_path": str(input_path) if input_path else "direct_text_input.txt",
                "pdf_path": str(input_path) if input_path else "direct_text_input.txt",
                "input_text": input_text,
                "use_stdin": False,
                "renderer_path": str(renderer_path),
                "out_dir": str(out_dir),
                "model": model,
                "mock": mock,
                "llm_mode": llm_mode,
                "report_ontology_path": report_ontology_path,
                # Send both old/new ontology keys for compatibility.
                "presentation_ontology_path": presentation_ontology_path,
                "slide_ontology_path": presentation_ontology_path,
                "layout_registry_path": layout_registry_path,
                "max_chunk_chars": int(max_chunk_chars),
                "max_task_chars": int(max_task_chars),
                "template_pptx_path": str(template_pptx_path) if template_pptx_path else "",
                "infer_template_style": bool(style_source == "Infer từ PPTX mẫu" and template_pptx_path),
                "extract_template_background": bool(extract_template_background),
            }
            final_state = graph.invoke(initial_state)

        # Apply visual style after task planning, then rerender all outputs.
        # Priority:
        #   1. PPTX template inference if user selected it and uploaded a sample.
        #   2. Pipeline-provided PSL if it already inferred one.
        #   3. Preset recolor over DEFAULT_PSL.
        pml_text = final_state.get("pml_text") or Path(final_state["pml_path"]).read_text(encoding="utf-8")
        profile = None
        inferred_bg = None

        if style_source == "Infer từ PPTX mẫu" and template_pptx_path:
            with st.spinner("Đang infer style từ PPTX mẫu và sinh PSL..."):
                style_inferer = load_pptx_style_inferer(str(pptx_style_inferer_path))
                selected_psl, profile, inferred_bg = style_inferer.infer_template_psl(
                    template_pptx_path,
                    out_dir,
                    theme_name="uploaded-pptx-template",
                    extract_background=bool(extract_template_background),
                )
        elif final_state.get("psl_text") and final_state.get("template_profile_path"):
            selected_psl = final_state["psl_text"]
        else:
            base_psl = getattr(pipeline, "DEFAULT_PSL", final_state.get("psl_text", ""))
            selected_psl = style_psl_from_preset(base_psl, style_name)

        with st.spinner("Đang áp dụng style và render HTML/PPTX/Markdown..."):
            outputs = rerender_outputs(renderer_path, pml_text, selected_psl, out_dir)

        st.success("Đã sinh slide xong.")
        st.caption(f"LLM mode: {llm_mode} | max_chunk_chars={int(max_chunk_chars):,} | max_task_chars={int(max_task_chars):,}")
        if style_source == "Infer từ PPTX mẫu" and template_pptx_path:
            st.caption("Style source: inferred from uploaded PPTX sample")
            if inferred_bg:
                st.caption(f"Background asset inferred: {inferred_bg}")

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            file_download_button("⬇️ PPTX", outputs["pptx"], "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        with c2:
            file_download_button("⬇️ HTML", outputs["html"], "text/html")
        with c3:
            if "md" in outputs:
                file_download_button("⬇️ Markdown", outputs["md"], "text/markdown")
        with c4:
            file_download_button("⬇️ PML", outputs["pml"], "text/plain")
        with c5:
            file_download_button("⬇️ PSL", outputs["psl"], "text/plain")

        if show_html_preview:
            preview_html_file(outputs["html"], height=preview_height)

        with st.expander("Xem PML sinh ra", expanded=False):
            st.code(outputs["pml"].read_text(encoding="utf-8"), language="yaml")
        with st.expander("Xem Markdown cho G-Eval", expanded=False):
            md_path = outputs.get("md")
            if md_path and md_path.exists():
                st.markdown(md_path.read_text(encoding="utf-8"))
            else:
                st.info("Renderer hiện tại chưa có `render_markdown`.")

        profile_path = out_dir / "template_profile.json"
        if profile_path.exists():
            with st.expander("PPTX template profile đã infer", expanded=False):
                st.json(json.loads(profile_path.read_text(encoding="utf-8")))

        col1, col2 = st.columns(2)
        with col1:
            validation_path = out_dir / "validation_report.json"
            if validation_path.exists():
                st.subheader("Validation report")
                st.json(json.loads(validation_path.read_text(encoding="utf-8")))
        with col2:
            repair_path = out_dir / "repair_log.txt"
            if repair_path.exists():
                st.subheader("Repair log")
                st.text(repair_path.read_text(encoding="utf-8"))

        st.caption(f"Output dir: {out_dir}")

    except Exception as exc:
        st.error(f"Lỗi: {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
