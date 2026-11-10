# DuNoPro Thái Bình — Điện Lực Thái Bình

Hệ thống theo dõi và báo cáo dư nợ tiền điện dành riêng cho **Điện lực Thái Bình** (`thai_binh`).

## Thông tin hệ thống
- **Đơn vị:** Điện lực Thái Bình (Mã đơn vị KHONO: `PA0901`)
- **Khoảng thời gian lấy nợ:** Từ ngày 01 tháng hiện tại đến ngày hiện tại.
- **GitHub Repository:** `https://github.com/hanhtuanhunghan-lang/BaoCaoThaiBinh`
- **Địa chỉ Web Dashboard:** `https://BaoCaoThaiBinh.vercel.app/`
- **Cơ chế:** Lấy dữ liệu API KHONO trực tiếp thông qua phiên đăng nhập Playwright chạy nền, tự động chuẩn hóa và đẩy `data_duno.json` lên GitHub.

## Cấu trúc thư mục
- `dunopro_app.py`: Ứng dụng chính (GUI + Scheduler + Playwright + Push GitHub + Khay hệ thống).
- `run_du_nopro.bat`: Script khởi động tiêu chuẩn.
- `run_du_nopro.vbs`: Khởi động chạy êm 100%, không nháy đen terminal console.
- `mo_lai_du_nopro.bat` / `.ps1`: Hiện lại cửa sổ khi ứng dụng đang ẩn ở khay hệ thống.
- `QuanLyUser.py` / `quan_ly_user_thai_binh.bat`: Tool quản lý tài khoản thu ngân đăng nhập Dashboard.
- `mo_web_thai_binh.bat`: Mở nhanh web dashboard trên trình duyệt.
- `profiles/thai_binh/TNV.xlsx`: Danh sách thu ngân viên Điện lực Thái Bình.
- `web/thai_binh/index.html`: Giao diện Dashboard web Điện lực Thái Bình.
- `web/thai_binh/users.json`: Danh sách tài khoản đăng nhập web (mật khẩu băm SHA-256).
- `runtime/`: Thư mục lưu cấu hình, token mã hóa DPAPI, file export Excel và logs.

## Hướng dẫn sử dụng
1. Nhấp đúp `run_du_nopro.vbs` (hoặc `run_du_nopro.bat`) để mở giao diện DuNoPro.
2. Nhập tài khoản và mật khẩu KHONO của Điện lực Thái Bình, tích chọn **Lưu tài khoản profile này**.
3. Bấm **Test login** để kiểm tra kết nối với hệ thống KHONO.
4. Bấm **Run now** để chạy cập nhật nợ ngay lập tức, hoặc bật chế độ tự động theo lịch.
5. Khi cần đổi mật khẩu hoặc thêm thu ngân, mở `quan_ly_user_thai_binh.bat`, sau đó bấm **Đồng bộ users.json (thủ công)** trên app.
