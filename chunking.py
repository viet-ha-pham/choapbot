import re

STRONG_MARKERS = [
    "tuy nhiên", "nhưng", "song", "mặc dù", "dù vậy",
    "vì vậy", "do đó", "bởi vậy", "cho nên",
    "trong khi đó", "ngược lại",
    "họ đề xuất", "theo đó"
]

def split_sentences(text: str):
    # Tách câu đơn giản theo . ! ?
    parts = re.split(r'(?<=[.!?。！？])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]

def starts_with_marker(sentence: str) -> bool:
    s = sentence.lower().strip()
    return any(s.startswith(m) for m in STRONG_MARKERS)

def discourse_chunk(text: str, max_sent_per_chunk: int = 2):
    sentences = split_sentences(text)
    chunks = []
    current = []

    for sent in sentences:
        if not current:
            current.append(sent)
            continue

        # Nếu câu hiện tại bắt đầu bằng marker mạnh
        # thì gộp với câu trước
        if starts_with_marker(sent):
            current.append(sent)
            chunks.append(" ".join(current))
            current = []
        else:
            # Nếu chunk đã đủ dài thì đóng chunk
            if len(current) >= max_sent_per_chunk:
                chunks.append(" ".join(current))
                current = [sent]
            else:
                current.append(sent)

    if current:
        chunks.append(" ".join(current))

    return chunks


text = """
Sáng nay, mưa lớn kéo dài tại Hà Nội đã khiến nhiều tuyến phố trung tâm bị ngập sâu.
Tuy nhiên, hệ thống metro Cát Linh – Hà Đông vẫn hoạt động bình thường và lượng hành khách tăng mạnh trong giờ cao điểm.

Theo Trung tâm Dự báo Khí tượng Thủy văn Quốc gia, lượng mưa đo được tại một số khu vực vượt 120 mm chỉ trong ba giờ.
Vì vậy, nhiều phương tiện chết máy và giao thông ùn tắc nghiêm trọng tại các quận nội thành.

Trong khi đó, một số chuyên gia cho rằng hệ thống thoát nước hiện tại chưa đáp ứng được tốc độ đô thị hóa.
Họ đề xuất thành phố cần mở rộng hồ điều hòa và nâng cấp hạ tầng thoát nước trong các năm tới.
"""

chunks = discourse_chunk(text)

for i, chunk in enumerate(chunks, 1):
    print(f"\n[Chunk {i}]")
    print(chunk)