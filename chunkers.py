import re
from dataclasses import dataclass, field


@dataclass
class ParagraphChunk:
    index: int
    text: str
    discourse_markers: list[str] = field(default_factory=list)
    role: str = "body"


class DiscourseParagraphChunker:
    def __init__(self, min_chars: int = 200, max_chars: int = 1200):
        self.min_chars = min_chars
        self.max_chars = max_chars

        self.marker_patterns = {
            "contrast": [
                r"\btuy nhiên\b",
                r"\bmặc dù\b",
                r"\btrái lại\b",
                r"\bngược lại\b",
                r"\bnhưng\b",
            ],
            "cause": [
                r"\bvì\b",
                r"\bdo\b",
                r"\bbởi vì\b",
                r"\bnguyên nhân\b",
            ],
            "result": [
                r"\bdo đó\b",
                r"\bvì vậy\b",
                r"\bcho nên\b",
                r"\bkết quả là\b",
                r"\bdẫn đến\b",
            ],
            "addition": [
                r"\bngoài ra\b",
                r"\bbên cạnh đó\b",
                r"\bhơn nữa\b",
                r"\bđồng thời\b",
            ],
            "example": [
                r"\bví dụ\b",
                r"\bchẳng hạn\b",
                r"\bcụ thể\b",
            ],
            "conclusion": [
                r"\btóm lại\b",
                r"\bnhìn chung\b",
                r"\bcó thể thấy\b",
                r"\bkết luận\b",
            ],
            "elaboration": [
                r"\bđây là\b"
            ],
            "background": [
                r"\btrước đó\b"
            ],
        }

    def split_paragraphs(self, text: str) -> list[str]:
        text = text.strip()
        paragraphs = re.split(r"\n\s*\n+", text)
        return [p.strip() for p in paragraphs if p.strip()]

    def detect_markers(self, paragraph: str) -> list[str]:
        found = []
        lowered = paragraph.lower()

        for marker_type, patterns in self.marker_patterns.items():
            for pattern in patterns:
                if re.search(pattern, lowered):
                    found.append(marker_type)
                    break

        return found

    def infer_role(self, paragraph: str, index: int, total: int, markers: list[str]) -> str:
        if index == 0:
            return "lead"

        # if index == total - 1:
        #     return "ending"

        if "conclusion" in markers:
            return "conclusion"

        if "result" in markers:
            return "result"

        if "cause" in markers:
            return "cause"

        if "contrast" in markers:
            return "contrast"

        if "example" in markers:
            return "example"

        return "body"

    def merge_short_chunks(self, chunks: list[ParagraphChunk]) -> list[ParagraphChunk]:
        merged = []
        buffer_text = []
        buffer_markers = []
        buffer_role = "body"

        for chunk in chunks:
            if len(chunk.text) < self.min_chars:
                buffer_text.append(chunk.text)
                buffer_markers.extend(chunk.discourse_markers)
                buffer_role = chunk.role
            else:
                if buffer_text:
                    merged.append(
                        ParagraphChunk(
                            index=len(merged),
                            text="\n\n".join(buffer_text),
                            discourse_markers=sorted(set(buffer_markers)),
                            role=buffer_role,
                        )
                    )
                    buffer_text = []
                    buffer_markers = []

                merged.append(chunk)

        if buffer_text:
            merged.append(
                ParagraphChunk(
                    index=len(merged),
                    text="\n\n".join(buffer_text),
                    discourse_markers=sorted(set(buffer_markers)),
                    role=buffer_role,
                )
            )

        for i, chunk in enumerate(merged):
            chunk.index = i

        return merged

    def chunk(self, text: str) -> list[ParagraphChunk]:
        paragraphs = self.split_paragraphs(text)
        chunks = []

        for i, paragraph in enumerate(paragraphs):
            markers = self.detect_markers(paragraph)
            role = self.infer_role(paragraph, i, len(paragraphs), markers)

            chunks.append(
                ParagraphChunk(
                    index=i,
                    text=paragraph,
                    discourse_markers=markers,
                    role=role,
                )
            )

        return self.merge_short_chunks(chunks)


if __name__ == '__main__':
    txt = """Tổng Bí thư, Chủ tịch nước Tô Lâm nhấn mạnh bên cạnh nhà ở để bán, thời gian tới cần ưu tiên phát triển nhà ở cho thuê, nhất là mô hình chung cư cho thuê tại đô thị.

Ngày 22/5, thông báo kết luận buổi làm việc với một số cơ quan về tình hình triển khai chỉ thị 34 của Ban Bí thư về nhà ở xã hội và định hướng phát triển nhà ở trong thời gian tới, Tổng Bí thư, Chủ tịch nước Tô Lâm đánh giá việc phát triển nhà ở xã hội nói riêng và nhà ở nói chung còn một số hạn chế.

Phân khúc nhà ở phù hợp với khả năng chi trả của người có thu nhập trung bình, thu nhập thấp chưa được quan tâm đúng mức. Nguồn cung nhà ở xã hội còn thiếu, phân bổ chưa hợp lý; nhiều dự án thiếu kết nối với các hạ tầng xã hội thiết yếu; quy hoạch quỹ đất cho nhà ở xã hội thiếu đồng bộ, thiếu gắn kết với khu vực tập trung nhiều người lao động.

Các doanh nghiệp thiếu động lực tham gia vào phân khúc nhà ở xã hội, nhà ở có giá phù hợp; cơ cấu sản phẩm nhà ở còn thiên về sở hữu, trong khi nhu cầu thuê nhà ở với giá hợp lý của người lao động là rất lớn.

Vì vậy, thời gian tới, Tổng Bí thư, Chủ tịch nước yêu cầu các cơ quan quán triệt quan điểm "quyền có nơi ở hợp pháp là quyền cơ bản của công dân". Tiếp cận nhà ở an toàn, phù hợp với khả năng chi trả là thước đo của tiến bộ xã hội, tạo nền tảng phát triển xã hội ổn định, bền vững.

Nhà nước có chính sách hướng tới mục tiêu mọi người đều có chỗ ở. Việc phát triển nhà ở trong giai đoạn tới theo cơ chế thị trường, có sự định hướng, quản lý hiệu quả của Nhà nước. Nhà nước không bao cấp về nhà ở, nhưng cũng không hoàn toàn để thị trường tự điều tiết.

Nhà nước giữ vai trò kiến tạo thông qua xây dựng thể chế, chính sách, quy hoạch để thị trường phát triển lành mạnh, minh bạch, doanh nghiệp tham gia xây dựng, vận hành với lợi nhuận hợp lý và người dân được tiếp cận chỗ ở ổn định, an toàn, phù hợp với khả năng chi trả.

"Bên cạnh nhà ở để bán, cần ưu tiên phát triển nhà ở cho thuê, nhất là mô hình chung cư cho thuê tại các đô thị lớn, khu công nghiệp, khu kinh tế, vùng động lực và các hành lang kinh tế quan trọng", kết luận nêu.

Tổng Bí thư, Chủ tịch nước yêu cầu phân loại nhà ở theo bốn nhóm gồm nhà thương mại, nhà cho thuê, nhà ở công vụ và nhà ở chính sách. Trên cơ sở này, Nhà nước sẽ có chính sách tương ứng, trong đó có thể hỗ trợ giá, cung cấp nhà miễn phí cho một số nhóm nhất định.

Đảng ủy Chính phủ được giao nghiên cứu xây dựng chính sách đất đai, tín dụng... phù hợp để phát triển nhanh thị trường nhà ở cho thuê với giá hợp lý, khuyến khích thu hút khu vực tư nhân tham gia. Thủ tục đầu tư, quy hoạch, giao đất, cấp phép xây dựng, tiếp cận tín dụng ưu đãi cần được rút ngắn, thuận lợi.

Địa phương rà soát quỹ đất, trong đó quy hoạch nhà ở trong mọi phân khúc phải gắn liền với quy hoạch hạ tầng kỹ thuật, dịch vụ xã hội thiết yếu, thiết chế văn hóa, y tế, giáo dục... trước hết tại các khu đô thị, khu kinh tế, khu công nghiệp, khu công nghệ cao và khu vực đô thị hóa nhanh.

Địa phương cũng cần chủ động giải phóng mặt bằng, chuẩn bị quỹ đất sạch để phát triển nhà ở cho thuê và kiểm soát chặt chẽ, minh bạch các trường hợp được ưu tiên về nhà ở, không để xảy ra trục lợi chính sách.

Trước đó tại buổi làm việc ngày 19/5 với Đảng ủy Chính phủ liên quan công tác phát triển nhà ở xã hội trong tình hình mới, Tổng Bí thư, Chủ tịch nước Tô Lâm nhấn mạnh quan điểm: "Nhà là để ở chứ không phải để kinh doanh, tích sản". "Từ nay đến 2030, nhà ở để bán vẫn cần thiết nhưng nhà ở cho thuê phải được xác định là trụ cột chiến lược".
    """

    chunker = DiscourseParagraphChunker()
    chunks = chunker.chunk(txt)
    print("Number of chunks: {}".format(len(chunks)))
    for chunk in chunks:
        if chunk.role != "background":
            print(chunk.text)
            print("-----")