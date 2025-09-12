# Entrypoint Script Documentation

## entrypoint.sh

Script khởi động chính cho Docker container TECHXPO.

### Chức năng:

1. **Khởi tạo Environment**
   - Tạo các thư mục cần thiết (/data/out, Booking_data, KMS)
   - Copy dữ liệu khởi tạo từ image nếu volume trống
   - Thiết lập quyền truy cập file

2. **Khởi tạo Database**
   - Gọi `storage.init_db()` để tạo bảng SQLite
   - Xử lý lỗi gracefully nếu module không tồn tại

3. **Quản lý Services**
   - **RUN_AGENT=1**: Khởi động LiveKit Agent + Web Server
   - **RUN_DASHBOARD=1**: Thêm Dashboard server (port 8090)
   - **Mặc định**: Chỉ chạy health check server

4. **Signal Handling**
   - Xử lý SIGTERM/SIGINT để tắt services gracefully
   - Cleanup các process con khi container stop

### Environment Variables:

- `RUN_AGENT`: "1" để chạy AI agent
- `RUN_DASHBOARD`: "1" để chạy dashboard
- `PORT`: Cổng web server (mặc định 8080)
- `PYTHONPATH`: Python module path

### Services được khởi động:

#### Khi RUN_AGENT=1:
1. **LiveKit Agent**: `python gemini_kiosk.py`
2. **Web Server**: `uvicorn web.server:app --port 8080`
3. **Dashboard** (nếu RUN_DASHBOARD=1): `uvicorn Dashboard.server:app --port 8090`

#### Khi RUN_AGENT=0:
- **Health Check Server**: FastAPI server đơn giản với `/healthz` endpoint

### Ports:
- **8080**: Web interface + API
- **8090**: Dashboard (nếu enabled)

### Health Check:
- Endpoint: `GET /healthz`
- Response: `{"status": "ok", "service": "techxpo"}`

### Docker Integration:
- Dockerfile sẽ copy và chmod +x cho script này
- docker-compose.yml dùng script này làm CMD mặc định
- Hoạt động với non-root user (appuser)

### Logs:
Script sẽ in ra:
- Environment variables
- Service startup status  
- PID của các process
- Cleanup messages khi shutdown