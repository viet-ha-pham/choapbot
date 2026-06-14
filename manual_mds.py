# app.py
import json
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

OUT_PATH = Path("manual_mds.jsonl")

st.set_page_config(page_title="Manual MDS Collector", layout="wide")

st.title("Manual MDS Collector")

query = st.text_input("Query / chủ đề / ghi chú ngắn", "")

col1, col2 = st.columns(2)

with col1:
    doc1 = st.text_area("Bài 1", height=500)

with col2:
    doc2 = st.text_area("Bài 2", height=500)

if st.button("OK - Lưu vào JSONL"):
    if not doc1.strip() or not doc2.strip():
        st.error("Cần nhập đủ cả 2 bài.")
    else:
        sample = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "query": query.strip() or None,
            "documents": [
                {
                    "doc_id": "doc1",
                    "content": doc1.strip()
                },
                {
                    "doc_id": "doc2",
                    "content": doc2.strip()
                }
            ],
            "summary": None,
            "source": "manual"
        }

        with OUT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        st.success(f"Đã lưu vào {OUT_PATH}")
        st.json(sample)