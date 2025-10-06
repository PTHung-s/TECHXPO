# Logo Setup Instructions

## Thêm Logo cho Web Interface

Để thêm logo cho web interface, bạn chỉ cần:

1. **Đặt file logo** vào thư mục này với tên `logo.png`
2. **Định dạng hỗ trợ**: PNG (khuyến nghị), JPG, JPEG, GIF, SVG
3. **Kích thước đề xuất**: 
   - Tối thiểu: 48x48 pixels
   - Tối ưu: 96x96 pixels (cho màn hình độ phân giải cao)
   - Tỷ lệ: Vuông (1:1) hoặc gần vuông
4. **Nền trong suốt**: Khuyến nghị dùng PNG với nền trong suốt

## Favicon (Biểu tượng Tab Trình duyệt)

Khi bạn thêm `logo.png`, hệ thống sẽ **tự động**:
- ✅ Tạo favicon 32x32 từ logo của bạn
- ✅ Hiển thị trên tab trình duyệt
- ✅ Có nền màu xanh (#2563eb) làm background
- ✅ Logo được canh giữa với padding 2px

Nếu không có logo, sẽ hiển thị favicon mặc định với chữ "AI".

## Cách hoạt động

### Logo trong Header:
- Nếu có file `logo.png`: Logo sẽ hiển thị trong header
- Nếu không có file: Sẽ hiển thị fallback text "AI" với nền gradient

### Favicon (Tab Browser):
- **Có logo**: Tự động tạo favicon từ logo
- **Không có logo**: Hiển thị favicon "AI" với gradient xanh

## Vị trí hiển thị

Logo sẽ xuất hiện ở:
- **Header**: Góc trái, bên cạnh status dot và title "Bác sĩ Ảo"
- **Tab trình duyệt**: Favicon 32x32 pixels
- **Kích thước header**: 48x48 pixels
- **Style**: Bo góc, có hiệu ứng hover nhẹ

## Ví dụ tên file hợp lệ

✅ `logo.png` (tên chính xác cần thiết)
❌ `Logo.PNG` (case-sensitive)
❌ `company-logo.png` (tên khác)
❌ `logo.jpg` (extension khác - cần đổi code nếu muốn dùng)

## Thay đổi tên file khác

Nếu muốn dùng tên file khác, sửa trong `app.js`:
```javascript
const logoPath = '/images/your-logo-name.png'
```

## Kết quả

Sau khi thêm logo:
1. 🖼️ Logo hiển thị trong header web
2. 🌐 Favicon hiển thị trên tab trình duyệt
3. 📱 Icon cho PWA (Progressive Web App)
4. 🔄 Tự động fallback nếu logo không tải được