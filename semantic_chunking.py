from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import re

def split_sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?。！？])\s+', text.strip()) if s.strip()]

def semantic_chunking(text, threshold=0.55):
    sentences = split_sentences(text)

    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    embeddings = model.encode(sentences)

    chunks = []
    current = [sentences[0]]

    for i in range(1, len(sentences)):
        sim = cosine_similarity(
            [embeddings[i - 1]],
            [embeddings[i]]
        )[0][0]

        print(f"SIM {i-1}->{i}: {sim:.3f}")

        if sim < threshold:
            chunks.append(" ".join(current))
            current = [sentences[i]]
        else:
            current.append(sentences[i])

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

chunks = semantic_chunking(text, threshold=0.55)

for i, chunk in enumerate(chunks, 1):
    print(f"\n[Chunk {i}]")
    print(chunk)