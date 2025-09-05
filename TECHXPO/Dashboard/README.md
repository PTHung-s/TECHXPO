# Dashboard (Doctor Schedule)

Realtime (polling) dashboard hiển thị lịch 20 phút của bác sĩ theo từng khoa.

Window thời gian: 07:40 -> 16:40 (inclusive) với bước 20 phút. DB riêng `schedule.db` (không đụng tới `kiosk.db`).

## Cấu trúc

```
Dashboard/
  schedule_logic.py   # Core hàm đọc catalog, tính free intervals, book slot
  server.py           # FastAPI app (uvicorn Dashboard.server:app --reload --port 8090)
  static/
    index.html        # UI bảng
    styles.css
    app.js
  schedule.db         # (tự tạo sau khi chạy)
```

## Chạy server

Kích hoạt venv rồi:

```
uvicorn Dashboard.server:app --reload --port 8090
```

Mở file: `Dashboard/static/index.html` (hoặc phục vụ qua bất kỳ http server tĩnh nào). UI sẽ fetch:

```
GET http://localhost:8090/api/overview?hospital_code=BV_BINHDAN&date=YYYY-MM-DD&departments=Ngoại%20tổng%20quát,Nam%20khoa
POST http://localhost:8090/api/book {hospital_code,department,doctor_name,date,slot_time}
```

## API

1. GET /api/overview
   - params: hospital_code, departments (comma separated), date (ISO, optional -> today)
   - trả về: slots, departments[], mỗi doctor có booked[], free_slots[], free_intervals[] (gộp chuỗi free).

2. POST /api/book
   - body: {hospital_code, department, doctor_name, date, slot_time}
   - slot_time phải trong danh sách hợp lệ (07:40..16:40 step 20). Trả về already_booked nếu trùng.

## Ghi chú

Nếu chưa chạy `catalog_builder.py` và thiếu `catalog/<code>.grouped.json` module sẽ fallback group theo trường `specialty` trong `Data/<code>.json`.

Có thể mở rộng sau:
- SSE/WebSocket thay polling
- Thêm endpoint hủy lịch
- Cache doctor list theo ngày để giảm I/O
