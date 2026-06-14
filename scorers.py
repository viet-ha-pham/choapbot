from dataclasses import dataclass
from typing import Dict, List
import re


@dataclass
class MatchResult:
    score: float
    matched_keywords: List[str]


class KeywordScorer:
    def __init__(self, config: Dict):
        self.config = config

    def score(self, text: str) -> Dict:
        result = {}
        total_score = 0

        text_lower = text.lower()

        for category, groups in self.config.items():

            category_score = 0
            category_matches = []

            for group_name, group_cfg in groups.items():

                weight = group_cfg["weight"]
                keywords = group_cfg["keywords"]

                matched = []

                for kw in keywords:
                    if kw.lower() in text_lower:
                        matched.append(kw)

                if matched:
                    group_score = weight * len(matched)

                    category_score += group_score
                    category_matches.extend(matched)

            result[category] = MatchResult(
                score=category_score,
                matched_keywords=category_matches
            )

            total_score += category_score

        result["total_score"] = total_score

        return result

if __name__ == '__main__':
    import yaml

    with open("keywords.yaml") as f:
        cfg = yaml.safe_load(f)

    scorer = KeywordScorer(cfg)
    article_text = """Tổng Bí thư, Chủ tịch nước: Ưu tiên xây chung cư cho thuê ở đô thị lớn
    
Tổng Bí thư, Chủ tịch nước Tô Lâm nhấn mạnh bên cạnh nhà ở để bán, thời gian tới cần ưu tiên phát triển nhà ở cho thuê, nhất là mô hình chung cư cho thuê tại đô thị.

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
    result = scorer.score(article_text)

    print(result["total_score"])