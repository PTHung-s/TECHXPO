## TECHXPO Dashboard (Lịch Khám Bác Sĩ)

Giao diện mới chuyên nghiệp để quan sát và quản lý lịch khám bác sĩ.

### Chức năng chính
- Xem lịch theo bệnh viện, bác sĩ, khung giờ
- Tự động cập nhật (poll 5s) nếu có thay đổi phiên bản booking
- Phân biệt trạng thái: Đã đặt (đỏ), Trống (xanh), Held (vàng)
- Xem chi tiết phiên khám (click ô đã đặt)
- Đặt lịch nhanh (click ô trống)
- Xuất CSV toàn bộ bảng
- Dark / Light mode (nút 🌓)
- Thống kê realtime (tổng slot, đã đặt, trống, held, version)

### Chạy Dashboard
Trong thư mục gốc dự án:
```powershell
uvicorn Dashboard.server:app --reload --port 8090
```
Sau đó mở trình duyệt tới:
```
http://localhost:8090/index.html
```
(Bạn cũng có thể phục vụ tĩnh thông qua mount / nhưng `/index.html` bảo đảm load đúng.)

### Cấu trúc thư mục
```
Dashboard/
  server.py        # FastAPI endpoints + static mount
  schedule_logic.py
  schedule.db
  static/
    index.html     # Giao diện chính (sidebar + table + metrics)
    styles.css     # Giao diện mới (dark/light, responsive)
    app.js         # Logic client: polling, bookings, modal, metrics, export
```

### API chính (tham khảo nhanh)
- `GET /api/hospitals`
- `GET /api/meta?hospital_code=...`
- `GET /api/bookings_by_code?hospital_code=...&department_codes=...&date=...`
- `POST /api/book` hoặc `POST /api/book_by_code`
- `GET /api/visit_detail?hospital_code=...&date=...&doctor_name=...&slot_time=...`

### Mở rộng
- Có thể thêm filter khoa / bác sĩ ở thanh công cụ.
- Thêm realtime (WebSocket) để thay polling.
- Thêm biểu đồ occupancy (Biểu đồ donut hoặc line theo giờ).

### Lưu ý UI
- Bảng dùng first-column sticky + header sticky.
- Responsive: dưới 980px sidebar chuyển thành hàng ngang.
- Sử dụng biến CSS (`:root`) để dễ chỉnh palette.

### License
Nội dung thuộc dự án TECHXPO nội bộ (Internal Use Only).
