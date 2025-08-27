# Multi-user (Nhiều khách) hướng dẫn nhanh

## Kiến trúc đề xuất
Mỗi client (trình duyệt / cuộc gọi) = 1 LiveKit room riêng. Agent (kiosk) join vào room đó dưới dạng participant `agent`. Frontend tạo token động.

## Luồng
1. Client gọi API backend (`POST /create-room`).
2. Backend tạo tên room (ví dụ: `visit-<uuid4>`), phát token cho user + token cho agent (hoặc agent dùng API key/worker).
3. Frontend kết nối LiveKit bằng token user.
4. Worker agent được LiveKit jobs dispatch vào room (hoặc agent join thủ công dựa trên webhook / REST trigger).

## Ghi chú DB
- SQLite dùng chung được (file lock) cho số lượng nhỏ. Scale lên Postgres nếu >50 đồng thời.
- Dữ liệu phân tách theo customer_id qua số điện thoại nên không lẫn giữa rooms.

## Bảo mật
- Mỗi token có TTL ngắn (5-10 phút) và `roomCreate=true` chỉ ở backend.
- Không để lộ LIVEKIT_API_SECRET ở frontend.

## Lưu ý
- Nếu cần agent phục vụ nhiều room song song, chạy nhiều worker processes hoặc bật autoscaling container.
- Có thể cấu hình LiveKit server với `--room-inactive-timeout` để dọn phòng khi idle.
