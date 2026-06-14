from pathlib import Path
from typing import Optional

import renderer_v1
# -----------------------------------------------------------------------------
# Demo input
# -----------------------------------------------------------------------------

DEMO_PML = '''
presentation "AI Strategy 2026":
  meta:
    author: "Viettel AI Lab"
    language: vi
    format: pptx

  use style: "corporate.psl"
  use constraints: "safe-layouts.pcl"

  cover_layout: title-slide
  cover:
    subtitle: Chiến lược tự động hóa tri thức doanh nghiệp
    author: Viettel AI Lab
    date: 2026
    background_image:
      src: "demo_bg.png"
      mode: cover
      opacity: 0.18

  section "Bối cảnh":
    header_layout: section-header
    header:
      subtitle: Từ dữ liệu phân tán đến insight có thể hành động

    slide "Vấn đề hiện tại":
      layout: title-bullets
      intent: explain_problem

      background_image:
        src: "corporate_bg.png"
        opacity: 0.12
        mode: cover

      title:
        Quá tải thông tin trong doanh nghiệp

      bullets:
        - Dữ liệu phân tán ở nhiều hệ thống
        - Báo cáo thủ công mất nhiều thời gian
        - Lãnh đạo cần insight nhanh hơn, nhưng nội dung bullet đôi khi rất dài nên cần cơ chế tự xuống dòng, co chữ hoặc cắt gọn để không phá bố cục slide.

      link:
        text: "Xem tài liệu pipeline"
        url: "https://example.com/pipeline"

      footer_text:
        Internal Use Only | Viettel AI Lab

      footer_image:
        src: "viettel_logo.png"
        alt: "Viettel logo"

      notes:
        Nhấn mạnh vấn đề không nằm ở thiếu dữ liệu, mà là thiếu khả năng tổng hợp.

    slide "Kiến trúc đề xuất":
      layout: title-image
      intent: show_architecture

      title:
        Kiến trúc tổng quan hệ thống

      subtitle:
        Dữ liệu → xử lý → tổng hợp → trình bày

      image:
        src: "architecture.png"
        alt: "Sơ đồ kiến trúc pipeline"

      notes:
        Có thể thay architecture.png bằng đường dẫn ảnh thật.

    slide "Mô hình triển khai":
      layout: text-image
      intent: explain_architecture

      title:
        Một cột nội dung, một cột hình minh họa

      left:
        heading: Thành phần chính
        bullets:
          - Thu thập dữ liệu từ nhiều nguồn
          - Chuẩn hóa và tạo chỉ mục
          - Truy xuất ngữ cảnh liên quan
          - Sinh báo cáo hoặc slide

      right:
        image:
          src: "architecture.png"
          alt: "Minh họa kiến trúc hệ thống"

      notes:
        Layout text-image dùng left cho chữ và right cho ảnh.

  section "Giải pháp":
    header_layout: section-header
    header:
      subtitle: Thiết kế pipeline và giao diện đầu ra

    slide "Luồng xử lý":
      layout: two-column
      intent: describe_pipeline

      title:
        Pipeline xử lý dữ liệu theo từng giai đoạn

      left:
        heading: Đầu vào
        bullets:
          - Văn bản
          - Báo cáo
          - Dữ liệu mạng xã hội

      right:
        heading: Đầu ra
        bullets:
          - Tóm tắt
          - Dashboard
          - Slide trình bày


    slide "Quy trình 4 bước có bullet":
      layout: numbered-columns-4
      intent: explain_process

      title:
        Quy trình xử lý theo 4 bước

      columns:
        - heading: Thu thập
          bullet_icon: check
          bullets:
            - API nội bộ
            - File CSV
            - Log hệ thống

        - heading: Làm sạch
          bullets:
            icon: diamond
            items:
              - Chuẩn hóa schema
              - Khử trùng lặp
              - Kiểm tra lỗi

        - heading: Truy xuất
          bullets:
            - icon: gear
              text: Tạo chỉ mục
            - icon: arrow
              text: Mở rộng ngữ cảnh

        - heading: Sinh đầu ra
          text: Tạo báo cáo hoặc slide.
          bullets:
            - Tóm tắt
            - Dashboard
            - PPTX


    slide "Kiến trúc tổng quan":
      layout: hero-image
      intent: show_overview_diagram

      title:
        Kiến trúc xử lý dữ liệu tổng quan

      subtitle:
        Một ảnh lớn ở trung tâm, chú thích ngắn ở phía dưới

      image:
        src: "architecture_overview.png"
        alt: "Sơ đồ kiến trúc tổng quan"

      caption:
        Hệ thống đi từ thu thập dữ liệu, chuẩn hóa, truy xuất tri thức đến sinh báo cáo và slide trình bày.

    slide "Ảnh minh họa độc lập":
      layout: image-caption
      intent: show_visual_only

      image:
        src: "model_snapshot.png"
        alt: "Ảnh minh họa mô hình tổng hợp tri thức"

      caption:
        Minh họa trực quan mô hình tổng hợp tri thức tự động, không cần tiêu đề phụ phía trên.

      footer:
        text: Confidential | Draft version
        align: center


    slide "So sánh hai phương án":
      layout: two-images
      intent: compare_visual_options

      title:
        So sánh kiến trúc hiện tại và kiến trúc đề xuất

      subtitle:
        Mỗi ảnh chiếm một cột độc lập để dễ đối chiếu trực quan

      images:
        - src: "current_architecture.png"
          alt: "Kiến trúc hiện tại"
        - src: "target_architecture.png"
          alt: "Kiến trúc đề xuất"

      notes:
        Nếu file ảnh chưa tồn tại, renderer PPTX sẽ hiển thị placeholder missing image.

    slide "6 năng lực chính":
      layout: grid-6
      intent: summarize_capabilities

      title:
        Sáu năng lực chính của hệ thống

      cells:
        - heading: Thu thập
          text: Lấy dữ liệu từ nhiều nguồn nội bộ và bên ngoài.
        - heading: Làm sạch
          text: Chuẩn hóa định dạng, khử trùng lặp và lọc nhiễu.
        - heading: Truy xuất
          text: Tìm các đoạn liên quan theo ngữ nghĩa và cấu trúc.
        - heading: Tổng hợp
          text: Kết hợp thông tin thành bản tóm tắt có kiểm soát.
        - heading: Trực quan
          text: Sinh dashboard, biểu đồ hoặc slide trình bày.
        - heading: Giám sát
          text: Theo dõi lỗi, chất lượng đầu ra và độ tin cậy.

      notes:
        Layout grid-6 dùng 3 cột x 2 hàng.


    slide "Lộ trình triển khai":
      layout: stair-progress

      title:
        Lộ trình triển khai theo bậc thang

      subtitle:
        Mỗi bậc thể hiện một mức trưởng thành của hệ thống

      steps:
        - heading: Khởi tạo
          text: Xác định phạm vi, nguồn dữ liệu và mục tiêu đầu ra.
        - heading: Chuẩn hóa
          text: Làm sạch, hợp nhất và kiểm soát chất lượng dữ liệu.
        - heading: Truy xuất
          text: Xây dựng chỉ mục, embedding và graph context.
        - heading: Sinh nội dung
          text: Tổng hợp báo cáo, dashboard và slide trình bày.
        - heading: Giám sát
          text: Theo dõi lỗi, feedback và cải tiến liên tục.

    slide "Các tầng năng lực xếp chồng":
      layout: stacked-stairs
      intent: show_maturity_levels

      title:
        Các tầng năng lực xếp chồng

      subtitle:
        Các bậc nhỏ dần và căn trái để thể hiện mức độ thu hẹp / trưởng thành

      steps:
        - heading: Nền tảng dữ liệu
          text: Thu thập, chuẩn hóa và quản trị nguồn dữ liệu.
        - heading: Truy xuất tri thức
          text: Tạo chỉ mục, metadata và quan hệ ngữ cảnh.
        - heading: Suy luận bằng LLM
          text: Tổng hợp, kiểm chứng và sinh nội dung có kiểm soát.
        - heading: Tự động hóa đầu ra
          text: Sinh dashboard, báo cáo và slide trình bày.
        - heading: Giám sát vận hành
          text: Theo dõi chất lượng, lỗi và feedback để cải tiến.

      footer_text:
        Stacked stairs demo | Internal Use Only

    slide "Ma trận 4x2 năng lực":
      layout: grid-4x2
      intent: show_capability_matrix

      title:
        Ma trận 4x2 năng lực hệ thống

      cells:
        - heading: Thu thập
          text: Kết nối nhiều nguồn dữ liệu.
        - heading: Làm sạch
          text: Chuẩn hóa, lọc nhiễu, khử trùng lặp.
        - heading: Lập chỉ mục
          text: Tạo embedding và metadata truy xuất.
        - heading: Truy xuất
          text: Chọn ngữ cảnh liên quan theo truy vấn.
        - heading: Suy luận
          text: Kết hợp bằng LLM và luật nghiệp vụ.
        - heading: Kiểm chứng
          text: Soát nguồn, phát hiện thiếu nhất quán.
        - heading: Trình bày
          text: Sinh báo cáo, dashboard hoặc slide.
        - heading: Giám sát
          text: Theo dõi lỗi, độ trễ và chất lượng.

      footer:
        text: Grid 4x2 demo | Internal Use Only
        align: center



    slide "Timeline triển khai":
      layout: timeline
      intent: show_roadmap

      title:
        Timeline triển khai theo các mốc chính

      milestones:
        - date: Q1
          heading: Khởi tạo
          text: Xác định phạm vi, nguồn dữ liệu và tiêu chí thành công.
        - date: Q2
          heading: Chuẩn hóa
          text: Làm sạch dữ liệu, thống nhất schema và metadata.
        - date: Q3
          heading: Tích hợp
          text: Kết nối retrieval, reasoning và dashboard vận hành.
        - date: Q4
          heading: Mở rộng
          text: Tối ưu chi phí, giám sát chất lượng và nhân rộng.

    slide "Icon bullets":
      layout: title-bullets
      intent: show_icon_bullets

      title:
        Bullet đặc biệt bằng icon

      bullets:
        icon: check
        items:
          - Chuẩn hóa dữ liệu đầu vào
          - Tạo metadata và chỉ mục
          - Sinh báo cáo tự động



    slide "Danh sách dài tự ngắt":
      layout: title-bullets
      intent: show_auto_pagination

      title:
        Danh sách dài tự ngắt sang slide sau

      bullets:
        icon: check
        overflow: paginate
        max_items_per_slide: 5
        items:
          - Thu thập dữ liệu từ nhiều nguồn nội bộ
          - Chuẩn hóa định dạng đầu vào
          - Loại bỏ bản ghi trùng lặp
          - Gắn metadata cho từng tài liệu
          - Tạo embedding và chỉ mục truy xuất
          - Mở rộng ngữ cảnh bằng đồ thị quan hệ
          - Sinh tóm tắt theo chuẩn báo cáo
          - Kiểm tra chất lượng đầu ra
          - Xuất dashboard và slide trình bày
          - Lưu log phục vụ kiểm toán

    slide "Per-item icon bullets":
      layout: title-bullets
      intent: show_per_item_icons

      title:
        Mỗi bullet có icon riêng

      bullets:
        - icon: database
          text: Thu thập dữ liệu từ nhiều nguồn
        - icon: gear
          text: Xử lý và chuẩn hóa
        - icon: star
          text: Tạo insight cho lãnh đạo
'''


DEMO_PCL = '''
pcl "safe-slide-constraints":
  default:
    text:
      overflow: shrink
      min_size: 13
      max_lines: 6
    bullets:
      overflow: paginate
      min_size: 14
      max_lines: 7
    table:
      overflow: paginate
      max_rows_per_slide: 8
    image:
      mode: contain
    background_image:
      mode: cover

  layout "title-bullets":
    bullets:
      overflow: paginate
      max_items_per_slide: 5

  layout "numbered-columns-4":
    card:
      keep_inside: true
      overflow: shrink
      min_size: 11

  slide "Danh sách dài tự ngắt theo constraint":
    bullets:
      overflow: paginate
      max_items_per_slide: 4
'''


DEMO_PSL = '''
theme "corporate":
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
    background_image:
      src: "cover_bg.png"
      opacity: 0.20
      mode: cover

    title:
      font: heading
      size: 48
      color: white
      bold: true
      align: center
      position: [90, 220]
      width: 1100
      height: 90

    subtitle:
      font: body
      size: 26
      color: secondary
      italic: true
      align: center
      position: [95, 320]
      width: 1000
      height: 60

    author:
      font: body
      size: 20
      color: white
      position: [95, 430]
      width: 800
      height: 40

    date:
      font: body
      size: 18
      color: secondary
      position: [95, 470]
      width: 400
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

    subtitle:
      font: body
      size: 24
      color: text
      position: [95, 340]
      width: 1000
      height: 60

  slide.title-bullets:
    title:
      font: heading
      size: 36
      color: primary
      position: [60, 40]
      width: 1100
      height: 90

    bullets:
      font: body
      size: 24
      color: text
      position: [90, 170]
      width: 1000
      height: 420
      line_gap: 12
      overflow: shrink
      max_lines: 5
      min_size: 15

    link:
      font: body
      size: 18
      color: primary
      underline: true
      position: [90, 610]
      width: 700
      height: 36
      icon: arrow
      icon_color: primary
      icon_size: 24
      icon_gap: 12

    footer_text:
      font: body
      size: 14
      color: muted
      italic: true
      align: left
      position: [80, 685]
      width: 840
      height: 24

    footer_image:
      position: [1080, 665]
      width: 120
      height: 36

  slide.title-image:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 70

    subtitle:
      font: body
      size: 22
      color: muted
      position: [65, 105]
      width: 1080
      height: 50

    image:
      position: [170, 180]
      width: 940
      height: 450



  slide.text-image:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    left:
      position: [70, 155]
      width: 520
      height: 420
      heading_size: 25
      size: 21
      color: text

    right:
      position: [680, 155]
      width: 520
      height: 390
      image_width: 520
      image_height: 390

  slide.image-text:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    left:
      position: [70, 155]
      width: 520
      height: 390
      image_width: 520
      image_height: 390

    right:
      position: [680, 155]
      width: 520
      height: 420
      heading_size: 25
      size: 21
      color: text



  slide.hero-image:
    background: "#FFFFFF"

    title:
      font: heading
      size: 34
      color: primary
      position: [60, 36]
      width: 1120
      height: 70

    subtitle:
      font: body
      size: 19
      color: muted
      position: [62, 102]
      width: 1080
      height: 40

    image:
      position: [120, 155]
      width: 1040
      height: 410

    caption:
      font: body
      size: 17
      color: muted
      position: [140, 585]
      width: 1000
      height: 60

  slide.image-caption:
    background: "#FFFFFF"

    image:
      position: [120, 70]
      width: 1040
      height: 520

    caption:
      font: body
      size: 18
      color: muted
      position: [140, 610]
      width: 1000
      height: 60

    footer_text:
      font: body
      size: 14
      color: muted
      position: [140, 685]
      width: 1000
      height: 24


  slide.two-images:
    background: "#FFFFFF"

    title:
      font: heading
      size: 32
      color: primary
      position: [60, 36]
      width: 1120
      height: 70

    subtitle:
      font: body
      size: 19
      color: muted
      position: [62, 104]
      width: 1080
      height: 40

    left:
      position: [70, 165]
      width: 540
      height: 390
      image_width: 540
      image_height: 390
      caption_size: 16
      caption_color: muted

    right:
      position: [670, 165]
      width: 540
      height: 390
      image_width: 540
      image_height: 390
      caption_size: 16
      caption_color: muted

  slide.grid-3:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    grid:
      position: [70, 155]
      width: 1140
      height: 430
      columns: 3
      gap: 24
      padding: 18
      fill: white
      border: secondary
      heading_size: 21
      size: 16
      color: text

  slide.grid-4:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    grid:
      position: [70, 145]
      width: 1140
      height: 470
      columns: 2
      gap: 24
      padding: 18
      fill: white
      border: secondary
      heading_size: 21
      size: 16
      color: text

  slide.grid-5:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    grid:
      position: [70, 145]
      width: 1140
      height: 470
      columns: 3
      gap: 22
      padding: 16
      fill: white
      border: secondary
      heading_size: 20
      size: 15
      color: text

  slide.grid-6:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    grid:
      position: [70, 145]
      width: 1140
      height: 470
      columns: 3
      gap: 22
      padding: 16
      fill: white
      border: secondary
      heading_size: 20
      size: 15
      color: text


  slide.stair-progress:
    title:
      font: heading
      size: 34
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 60

    subtitle:
      font: body
      size: 18
      color: muted
      italic: true
      position: [65, 100]
      width: 1050
      height: 40

    stair:
      position: [80, 175]
      step_width: 255
      step_height: 118
      x_step: 195
      y_step: 58
      padding: 15
      fill: secondary
      border: primary
      number_color: primary
      heading_color: text
      color: muted
      heading_size: 18
      size: 14
      align: left

  slide.stacked-stairs:
    title:
      font: heading
      size: 34
      color: primary
      bold: true
      position: [60, 40]
      width: 1120
      height: 60
      align: left

    subtitle:
      font: body
      size: 18
      color: muted
      italic: true
      position: [65, 100]
      width: 1050
      height: 40

    stacked_stairs:
      position: [120, 175]
      base_width: 980
      step_height: 90
      shrink: 95
      overlap: 24
      align_side: left
      padding_x: 22
      padding_y: 13
      fill: secondary
      border: primary
      number_color: primary
      heading_color: text
      color: muted
      heading_size: 18
      size: 14
      align: left

  slide.grid-4x2:
    title:
      font: heading
      size: 32
      color: primary
      position: [50, 35]
      width: 1180
      height: 70
      bold: true
      align: center

    grid:
      position: [50, 135]
      width: 1180
      height: 500
      columns: 4
      rows: 2
      gap: 18
      padding: 14
      fill: white
      border: secondary
      heading_font: heading
      heading_size: 17
      heading_color: primary
      size: 13
      color: text
      align: left

  slide.numbered-columns-4:
    title:
      font: heading
      size: 32
      color: primary
      bold: true
      align: center
      position: [60, 40]
      width: 1120
      height: 70

    numbered_columns:
      position: [70, 185]
      width: 1140
      height: 360
      gap: 24
      columns: 4
      circle_size: 58
      circle_offset_y: -32
      padding: 16
      fill: "#FFFFFF"
      border: "#CBD5E1"
      circle_fill: primary
      circle_color: "#FFFFFF"
      heading_size: 19
      heading_height: 40
      heading_align: center
      bullet_size: 13
      bullet_align: left
      bullet_icon: dot
      icon_color: primary
      line_gap: 4
      overflow: shrink
      max_lines: 5
      min_size: 10


  slide.title-table:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 70
      bold: true

    table:
      font: body
      size: 17
      color: text
      position: [70, 140]
      width: 1140
      height: 470
      header_fill: primary
      header_color: white
      cell_fill: "#FFFFFF"
      alt_fill: "#F8FAFC"
      border_color: "#CBD5E1"
      align: left
      header_align: center

  slide.two-column:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 80

    left:
      position: [70, 160]
      width: 520

    right:
      position: [680, 160]
      width: 520


  slide.timeline:
    title:
      font: heading
      size: 34
      color: primary
      position: [60, 40]
      width: 1120
      height: 70
      bold: true
      align: center

    timeline:
      position: [90, 155]
      width: 1100
      height: 470
      axis_y: 365
      line_height: 5
      marker_size: 24
      connector_height: 58
      card_width: 230
      card_height: 118
      gap: 28
      alternate: true
      fill: white
      border: secondary
      line_color: primary
      marker_fill: primary
      marker_border: white
      date_color: primary
      heading_color: text
      color: muted
      date_size: 13
      heading_size: 17
      size: 13
'''
# def write_demo_external_files(base_dir: Path) -> None:
#     """Materialize files referenced by DEMO_PML so `use style` and `use constraints` work."""
#     (base_dir / "corporate.psl").write_text(DEMO_PSL, encoding="utf-8")
#     (base_dir / "safe-layouts.pcl").write_text(DEMO_PCL, encoding="utf-8")

def main() -> None:
    script_dir = Path(__file__).resolve().parent
    #renderer_v1.create_demo_assets(script_dir)
    #write_demo_external_files(script_dir)

    doc = renderer_v1.parse_pml(DEMO_PML)
    theme = renderer_v1.load_theme_for_doc(doc, script_dir, DEMO_PSL)
    constraints = renderer_v1.load_constraints_for_doc(doc,script_dir, DEMO_PCL)
    render_ir = renderer_v1.build_render_ir(doc, theme, constraints)

    renderer_v1.render_html(render_ir, "demo_output.html", asset_base_dir=script_dir)
    renderer_v1.render_pptx(render_ir, "demo_output.pptx", asset_base_dir=script_dir)

    print(f"Style file: {doc.style_file}")
    print(f"Constraint file: {doc.constraint_file}")
    print("Generated demo_output.html")
    print("Generated demo_output.pptx")


if __name__ == "__main__":
    main()
