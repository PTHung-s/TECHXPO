# 📊 Hướng dẫn xem Database

## 🚀 Cách sử dụng

### 1. Xem database qua Terminal (Khuyến nghị)

```bash
# Xem nhanh thông tin database
python test_db.py

# Xem chi tiết với menu interactive
python view_database.py

# Test functions (không interactive)
python test_view.py
```

### 2. Xem database qua Web Browser

```bash
# Khởi động web server
python serve_viewer.py

# Sau đó mở browser tại: http://localhost:8080/db_viewer.html
```

## 📋 Tính năng

### Terminal Viewer (`view_database.py`)
- ✅ Xem danh sách khách hàng với facts/summary
- ✅ Xem tất cả lượt khám
- ✅ Xem chi tiết từng khách hàng
- ✅ Hiển thị đầy đủ thông tin triệu chứng, chẩn đoán
- ✅ Menu interactive dễ sử dụng

### Web Viewer (`db_viewer.html`)
- ✅ Interface đẹp mắt
- ✅ Lọc khách hàng theo tên/SĐT
- ✅ Hiển thị facts và summary columns
- ✅ Click vào khách hàng để xem lượt khám
- ✅ Tooltip để xem đầy đủ nội dung

## 🛠️ Cấu trúc Database

### Bảng `customers`
- `id`: ID khách hàng (CUS-xxxxxxxxxx)
- `name`: Tên khách hàng  
- `phone`: Số điện thoại
- `facts`: Thông tin cá nhân tích lũy (mới)
- `last_summary`: Tóm tắt cuối cùng (mới)

### Bảng `visits`
- `visit_id`: ID lượt khám (VIS-xxxxxxxxxxxxx)
- `customer_id`: ID khách hàng
- `created_at`: Thời gian tạo
- `payload_json`: Dữ liệu chi tiết (JSON)
- `summary`: Tóm tắt lượt khám (mới)
- `facts_extracted`: Facts được trích xuất (mới)

## 🔧 Troubleshooting

### Lỗi "File kiosk.db không tồn tại"
```bash
# Chạy agent ít nhất 1 lần để tạo database
python gemini_kiosk.py dev
```

### Lỗi web viewer không load được database
```bash
# Đảm bảo chạy qua HTTP server, không mở file:// trực tiếp
python serve_viewer.py
```

### Không thấy facts/summary
- Facts và summary chỉ được tạo sau khi tích hợp `facts_extractor.py`
- Dữ liệu cũ sẽ chưa có facts/summary
- Test với lượt khám mới để thấy dữ liệu đầy đủ

## 📊 Sample Output

```
============================================================
📋 DANH SÁCH KHÁCH HÀNG  
============================================================
ID           Tên                  SĐT          Số lượt  Có Facts   Có Summary
--------------------------------------------------------------------------------
CUS-3bc6fb07db Phạm Tuấn Hưng     0336018126   1        ✅         ✅

--- LƯỢT KHÁM 1 ---
🆔 Visit ID: VIS-1756273348204
👤 Customer: CUS-3bc6fb07db
📅 Thời gian: 2025-08-27 05:42:28
📝 Tóm tắt: Lý do khám chính: Đặt lịch khám bệnh...
🔍 Facts: Thông tin cá nhân cơ bản: Tên: Phạm Tuấn Hưng...
🩺 Triệu chứng:
   - Ho (mức độ: nhẹ, thời gian: 2 ngày)
   - Sổ mũi (mức độ: nhẹ, thời gian: 2 ngày)
```
