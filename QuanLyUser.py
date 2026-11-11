# -*- coding: utf-8 -*-
"""
Tên tool: Tool Quản lý User/Pass cho Web Báo Cáo Dư Nợ - Điện Lực Thái Bình
Mô tả: GUI giúp sếp Tuấn thêm/sửa/xóa danh sách thu ngân Điện Lực Thái Bình.
       Tự động hash mật khẩu (SHA-256) và xuất ra users.json để gắn vào web.
       Có tính năng nạp danh sách thu ngân từ file Excel (TNV.xlsx).
       Có nút [Đồng bộ lên Web (GitHub)] để đẩy thẳng tài khoản lên Web ngay lập tức.
Tác giả: Nguyen Tuan - Điện Lực Thái Bình
SĐT: 0919089886
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import hashlib
import json
import os
import sys
import logging
import unicodedata
import shutil
import base64
import urllib.request
import urllib.error
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    pd = None

PHIEN_BAN = "1.3"

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
PROFILE_OUTPUTS = {
    "thai_binh": os.path.join(SCRIPT_DIR, "web", "thai_binh", "users.json"),
}

GITHUB_USER = "hanhtuanhunghan-lang"
GITHUB_REPO = "BaoCaoThaiBinh"
GITHUB_BRANCH = "main"

def lay_profile_dang_chon():
    if len(sys.argv) > 1 and sys.argv[1] in PROFILE_OUTPUTS:
        return sys.argv[1]
    env_profile = os.environ.get("DUNOPRO_USER_PROFILE", "").strip()
    if env_profile in PROFILE_OUTPUTS:
        return env_profile
    return "thai_binh"

USER_PROFILE = lay_profile_dang_chon()
FILE_USERS_JSON = PROFILE_OUTPUTS[USER_PROFILE]
os.makedirs(os.path.dirname(FILE_USERS_JSON), exist_ok=True)

logging.basicConfig(
    filename=os.path.join(SCRIPT_DIR, 'log_tool_quan_ly_user.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

def lay_github_token() -> str:
    sec_path = os.path.join(SCRIPT_DIR, "runtime", "secrets.json")
    if os.path.exists(sec_path):
        try:
            with open(sec_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            t = d.get("github_token", "")
            if t.startswith("dpapi:"):
                import win32crypt
                raw = base64.b64decode(t.split(":", 1)[1])
                res = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
                return (res[1] if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], (bytes, bytearray)) else res[0]).decode("utf-8")
            if t.startswith("plain:"):
                return base64.b64decode(t.split(":", 1)[1]).decode("utf-8")
            if t:
                return t
        except Exception as e:
            logging.error(f"Lỗi giải mã token secrets.json: {e}")

    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.windll.advapi32
        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR),
            ]
        pcred = ctypes.POINTER(CREDENTIAL)()
        target = f'git:https://{GITHUB_USER}@github.com'
        if advapi32.CredReadW(target, 1, 0, ctypes.byref(pcred)):
            raw = bytes((ctypes.c_byte * pcred.contents.CredentialBlobSize).from_address(ctypes.addressof(pcred.contents.CredentialBlob.contents)))
            token = raw.decode('utf-16le').strip()
            advapi32.CredFree(pcred)
            return token
    except Exception:
        pass
    return ""

def hash_mat_khau(mat_khau_goc: str) -> str:
    bytes_mat_khau = mat_khau_goc.encode("utf-8")
    return hashlib.sha256(bytes_mat_khau).hexdigest()

def _bo_dau(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace('đ', 'd').replace('Đ', 'd')
    s = s.lower().strip()
    s = s.replace(' ', '_').replace('-', '_')
    return s

def doc_danh_sach_user():
    if not os.path.exists(FILE_USERS_JSON):
        return []
    try:
        with open(FILE_USERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except Exception as e:
        logging.error(f"Lỗi đọc users.json: {e}")
        return []

def ghi_danh_sach_user(danh_sach_user):
    try:
        data = {
            "cap_nhat_luc": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "tong_so": len(danh_sach_user),
            "users": danh_sach_user
        }
        with open(FILE_USERS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        root_users = os.path.join(SCRIPT_DIR, "users.json")
        try:
            shutil.copyfile(FILE_USERS_JSON, root_users)
        except Exception:
            pass
        logging.info(f"Đã ghi {len(danh_sach_user)} user vào {FILE_USERS_JSON}")
        return True
    except Exception as e:
        logging.error(f"Lỗi ghi users.json: {e}")
        return False

class ToolQuanLyUser(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Quản Lý Tài Khoản Web Dư Nợ — Điện Lực Thái Bình v{PHIEN_BAN}")
        self.geometry("860x640")
        self.minsize(740, 520)
        self.configure(bg="#f1f5f9")
        self.users = doc_danh_sach_user()
        self.dong_dang_chon = None
        self.tao_giao_dien()
        self.nap_du_lieu_len_bang()

    def tao_giao_dien(self):
        khung_tren = tk.Frame(self, bg="#0054A6", height=65)
        khung_tren.pack(fill=tk.X, side=tk.TOP)
        khung_tren.pack_propagate(False)

        lbl_tieu_de = tk.Label(
            khung_tren,
            text=f"QUẢN LÝ USER / PASS WEB DƯ NỢ — ĐIỆN LỰC THÁI BÌNH (v{PHIEN_BAN})",
            font=("Segoe UI", 12, "bold"),
            fg="white",
            bg="#0054A6"
        )
        lbl_tieu_de.pack(pady=(6, 2))

        lbl_mo_ta = tk.Label(
            khung_tren,
            text=f"GitHub: {GITHUB_USER}/{GITHUB_REPO}  |  Web: https://bao-cao-thai-binh.vercel.app/",
            font=("Segoe UI", 9),
            fg="#cbd5e1",
            bg="#0054A6"
        )
        lbl_mo_ta.pack()

        khung_nhap = tk.LabelFrame(
            self,
            text="  Thông tin tài khoản  ",
            font=("Segoe UI", 10, "bold"),
            bg="#f1f5f9",
            fg="#1e293b",
            padx=15,
            pady=10
        )
        khung_nhap.pack(fill=tk.X, padx=15, pady=10)

        tk.Label(khung_nhap, text="Tên đăng nhập:", font=("Segoe UI", 9, "bold"), bg="#f1f5f9").grid(row=0, column=0, sticky="w", pady=4)
        self.ent_username = ttk.Entry(khung_nhap, width=22, font=("Segoe UI", 10))
        self.ent_username.grid(row=0, column=1, sticky="w", padx=5, pady=4)

        tk.Label(khung_nhap, text="Họ và tên:", font=("Segoe UI", 9, "bold"), bg="#f1f5f9").grid(row=0, column=2, sticky="w", padx=(15, 0), pady=4)
        self.ent_ho_ten = ttk.Entry(khung_nhap, width=28, font=("Segoe UI", 10))
        self.ent_ho_ten.grid(row=0, column=3, sticky="w", padx=5, pady=4)

        tk.Label(khung_nhap, text="Mật khẩu mới:", font=("Segoe UI", 9, "bold"), bg="#f1f5f9").grid(row=1, column=0, sticky="w", pady=4)
        self.ent_password = ttk.Entry(khung_nhap, width=22, font=("Segoe UI", 10), show="*")
        self.ent_password.grid(row=1, column=1, sticky="w", padx=5, pady=4)

        self.lbl_luu_y_pass = tk.Label(
            khung_nhap,
            text="(Khi SỬA: để trống nếu không muốn đổi mật khẩu)",
            font=("Segoe UI", 8, "italic"),
            fg="#64748b",
            bg="#f1f5f9"
        )
        self.lbl_luu_y_pass.grid(row=1, column=2, columnspan=2, sticky="w", padx=15, pady=4)

        khung_nut = tk.Frame(khung_nhap, bg="#f1f5f9")
        khung_nut.grid(row=2, column=0, columnspan=4, pady=(10, 2), sticky="w")

        btn_them = tk.Button(
            khung_nut, text="➕ Thêm mới", bg="#16a34a", fg="white",
            font=("Segoe UI", 9, "bold"), padx=10, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xu_ly_them
        )
        btn_them.pack(side=tk.LEFT, padx=3)

        btn_sua = tk.Button(
            khung_nut, text="✏️ Cập nhật", bg="#2563eb", fg="white",
            font=("Segoe UI", 9, "bold"), padx=10, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xu_ly_sua
        )
        btn_sua.pack(side=tk.LEFT, padx=3)

        btn_xoa = tk.Button(
            khung_nut, text="🗑️ Xóa", bg="#dc2626", fg="white",
            font=("Segoe UI", 9, "bold"), padx=10, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xu_ly_xoa
        )
        btn_xoa.pack(side=tk.LEFT, padx=3)

        btn_lam_moi = tk.Button(
            khung_nut, text="🔄 Xóa trắng ô", bg="#64748b", fg="white",
            font=("Segoe UI", 9), padx=8, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xoa_trang_o_nhap
        )
        btn_lam_moi.pack(side=tk.LEFT, padx=3)

        btn_nap_excel = tk.Button(
            khung_nut, text="📂 Nạp từ Excel", bg="#7c3aed", fg="white",
            font=("Segoe UI", 9, "bold"), padx=10, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xu_ly_nap_excel
        )
        btn_nap_excel.pack(side=tk.LEFT, padx=(10, 3))

        btn_dong_bo = tk.Button(
            khung_nut, text="🚀 Đồng bộ lên Web (GitHub)", bg="#ea580c", fg="white",
            font=("Segoe UI", 9, "bold"), padx=12, pady=5, relief=tk.FLAT, cursor="hand2",
            command=self.xu_ly_dong_bo_github
        )
        btn_dong_bo.pack(side=tk.LEFT, padx=(12, 3))

        khung_bang = tk.Frame(self, bg="#f1f5f9")
        khung_bang.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        cot = ("stt", "username", "ho_ten", "pass_hash")
        self.bang = ttk.Treeview(khung_bang, columns=cot, show="headings", selectmode="browse")

        self.bang.heading("stt", text="STT")
        self.bang.heading("username", text="Tên đăng nhập")
        self.bang.heading("ho_ten", text="Họ và tên")
        self.bang.heading("pass_hash", text="Mật khẩu đã băm (SHA-256)")

        self.bang.column("stt", width=45, anchor="center")
        self.bang.column("username", width=140, anchor="w")
        self.bang.column("ho_ten", width=180, anchor="w")
        self.bang.column("pass_hash", width=380, anchor="w")

        scrollbar_y = ttk.Scrollbar(khung_bang, orient=tk.VERTICAL, command=self.bang.yview)
        self.bang.configure(yscrollcommand=scrollbar_y.set)

        self.bang.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.bang.bind("<<TreeviewSelect>>", self.khi_chon_dong)

        khung_day = tk.Frame(self, bg="#e2e8f0", height=40)
        khung_day.pack(fill=tk.X, side=tk.BOTTOM)

        self.lbl_tong_so = tk.Label(
            khung_day, text=f"Tổng số: {len(self.users)} user",
            font=("Segoe UI", 9, "bold"), bg="#e2e8f0", fg="#334155"
        )
        self.lbl_tong_so.pack(side=tk.LEFT, padx=15, pady=8)

        self.lbl_vi_tri_file = tk.Label(
            khung_day, text=f"Lưu tại: {FILE_USERS_JSON}",
            font=("Segoe UI", 8), bg="#e2e8f0", fg="#64748b"
        )
        self.lbl_vi_tri_file.pack(side=tk.RIGHT, padx=15, pady=8)

    def nap_du_lieu_len_bang(self):
        for item in self.bang.get_children():
            self.bang.delete(item)
        for i, u in enumerate(self.users, start=1):
            hash_hien_thi = u.get("pass_hash", "")
            if len(hash_hien_thi) > 28:
                hash_hien_thi = hash_hien_thi[:28] + "..."
            self.bang.insert("", tk.END, iid=str(i-1), values=(i, u.get("username", ""), u.get("ho_ten", ""), hash_hien_thi))
        self.lbl_tong_so.config(text=f"Tổng số: {len(self.users)} user")

    def khi_chon_dong(self, event):
        chon = self.bang.selection()
        if not chon:
            return
        idx = int(chon[0])
        self.dong_dang_chon = idx
        u = self.users[idx]
        self.ent_username.delete(0, tk.END)
        self.ent_username.insert(0, u.get("username", ""))
        self.ent_ho_ten.delete(0, tk.END)
        self.ent_ho_ten.insert(0, u.get("ho_ten", ""))
        self.ent_password.delete(0, tk.END)

    def xoa_trang_o_nhap(self):
        self.dong_dang_chon = None
        self.bang.selection_remove(self.bang.selection())
        self.ent_username.delete(0, tk.END)
        self.ent_ho_ten.delete(0, tk.END)
        self.ent_password.delete(0, tk.END)
        self.ent_username.focus()

    def xu_ly_them(self):
        username = self.ent_username.get().strip()
        ho_ten = self.ent_ho_ten.get().strip()
        password = self.ent_password.get()

        if not username:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập Tên đăng nhập!")
            self.ent_username.focus()
            return
        if not password:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập Mật khẩu!")
            self.ent_password.focus()
            return
        for u in self.users:
            if u["username"].lower() == username.lower():
                messagebox.showerror("Trùng tên", f"Tên đăng nhập '{username}' đã tồn tại!")
                return

        pass_hash = hash_mat_khau(password)
        self.users.append({"username": username, "ho_ten": ho_ten, "pass_hash": pass_hash})
        if ghi_danh_sach_user(self.users):
            self.nap_du_lieu_len_bang()
            self.xoa_trang_o_nhap()
            messagebox.showinfo("Thành công", f"Đã thêm user '{username}' thành công!\n(Hãy bấm [Đồng bộ lên Web (GitHub)] để áp dụng lên Web)")

    def xu_ly_sua(self):
        if self.dong_dang_chon is None:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn 1 user trong bảng để sửa!")
            return
        username = self.ent_username.get().strip()
        ho_ten = self.ent_ho_ten.get().strip()
        password = self.ent_password.get()

        if not username:
            messagebox.showwarning("Thiếu thông tin", "Tên đăng nhập không được để trống!")
            return
        for idx, u in enumerate(self.users):
            if idx != self.dong_dang_chon and u["username"].lower() == username.lower():
                messagebox.showerror("Trùng tên", f"Tên đăng nhập '{username}' đã trùng với user khác!")
                return

        self.users[self.dong_dang_chon]["username"] = username
        self.users[self.dong_dang_chon]["ho_ten"] = ho_ten
        if password:
            self.users[self.dong_dang_chon]["pass_hash"] = hash_mat_khau(password)

        if ghi_danh_sach_user(self.users):
            self.nap_du_lieu_len_bang()
            self.xoa_trang_o_nhap()
            messagebox.showinfo("Thành công", f"Đã cập nhật user '{username}' thành công!\n(Hãy bấm [Đồng bộ lên Web (GitHub)] để áp dụng lên Web)")

    def xu_ly_xoa(self):
        if self.dong_dang_chon is None:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn 1 user trong bảng để xóa!")
            return
        username = self.users[self.dong_dang_chon]["username"]
        xac_nhan = messagebox.askyesno("Xác nhận xóa", f"Sếp có chắc chắn muốn xóa user '{username}' không?")
        if not xac_nhan:
            return
        del self.users[self.dong_dang_chon]
        if ghi_danh_sach_user(self.users):
            self.nap_du_lieu_len_bang()
            self.xoa_trang_o_nhap()
            messagebox.showinfo("Thành công", f"Đã xóa user '{username}' thành công!\n(Hãy bấm [Đồng bộ lên Web (GitHub)] để áp dụng lên Web)")

    def xu_ly_nap_excel(self):
        if pd is None:
            messagebox.showerror("Thiếu thư viện", "Chưa cài đặt pandas. Vui lòng chạy:\npip install pandas openpyxl")
            return
        file_path = filedialog.askopenfilename(
            title="Chọn file Excel danh sách thu ngân",
            initialdir=SCRIPT_DIR,
            filetypes=[("File Excel", "*.xlsx *.xls")]
        )
        if not file_path:
            return
        try:
            df = pd.read_excel(file_path, header=None)
            ds_thu_ngan = []
            for val in df.iloc[:, 0].dropna():
                val_str = str(val).strip()
                if val_str and "thu ngan" not in _bo_dau(val_str) and "ten" not in _bo_dau(val_str):
                    ds_thu_ngan.append(val_str)

            if not ds_thu_ngan:
                messagebox.showwarning("Không có dữ liệu", "Không tìm thấy danh sách thu ngân trong cột đầu tiên của file Excel!")
                return

            mat_khau_mac_dinh = "123"
            hash_mac_dinh = hash_mat_khau(mat_khau_mac_dinh)
            so_them_moi = 0
            so_bo_qua = 0

            map_users_hien_tai = {u["username"].lower(): u for u in self.users}
            for dong in ds_thu_ngan:
                parts = dong.split(maxsplit=1)
                username = parts[0].strip()
                ho_ten = parts[1].strip() if len(parts) > 1 else username
                import re
                ho_ten_clean = re.sub(r'^\d+\.\s*', '', ho_ten)
                if username.lower() in map_users_hien_tai:
                    so_bo_qua += 1
                else:
                    self.users.append({
                        "username": username,
                        "ho_ten": ho_ten_clean,
                        "pass_hash": hash_mac_dinh
                    })
                    map_users_hien_tai[username.lower()] = self.users[-1]
                    so_them_moi += 1

            if so_them_moi > 0:
                ghi_danh_sach_user(self.users)
                self.nap_du_lieu_len_bang()
                messagebox.showinfo(
                    "Hoàn tất nạp Excel",
                    f"Đã nạp thành công từ Excel:\n- Thêm mới: {so_them_moi} user (Mật khẩu mặc định: {mat_khau_mac_dinh})\n- Bỏ qua do đã tồn tại: {so_bo_qua} user.\n(Hãy bấm [Đồng bộ lên Web (GitHub)] để áp dụng lên Web)"
                )
            else:
                messagebox.showinfo("Thông báo", f"Tất cả {so_bo_qua} user trong file Excel đã tồn tại trong danh sách.")
        except Exception as e:
            logging.error(f"Lỗi nạp Excel: {e}")
            messagebox.showerror("Lỗi đọc file", f"Không đọc được file Excel:\n{e}")

    def xu_ly_dong_bo_github(self):
        token = lay_github_token()
        if not token:
            messagebox.showerror(
                "Chưa có Token",
                "Không tìm thấy GitHub Token trong runtime/secrets.json!\nVui lòng mở DuNoPro hoặc kiểm tra cài đặt."
            )
            return

        try:
            with open(FILE_USERS_JSON, "rb") as f:
                content_bytes = f.read()

            api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/users.json"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "DuNoPro-UserManager"
            }

            sha = None
            req_get = urllib.request.Request(api_url, headers=headers)
            try:
                with urllib.request.urlopen(req_get) as resp:
                    data_get = json.loads(resp.read().decode())
                    sha = data_get.get("sha")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise

            put_body = {
                "message": f"Dong bo users.json thai_binh tu tool QuanLyUser ({len(self.users)} user)",
                "content": base64.b64encode(content_bytes).decode("ascii"),
                "branch": GITHUB_BRANCH
            }
            if sha:
                put_body["sha"] = sha

            req_put = urllib.request.Request(
                api_url,
                data=json.dumps(put_body).encode("utf-8"),
                headers={**headers, "Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(req_put) as resp:
                if resp.status in (200, 201):
                    messagebox.showinfo(
                        "Đồng bộ thành công",
                        f"✅ Đã đồng bộ {len(self.users)} tài khoản lên GitHub ({GITHUB_REPO}) thành công!\nCác thu ngân có thể đăng nhập trên Web ngay lập tức."
                    )
                    return
            messagebox.showwarning("Cảnh báo", "Lệnh gửi đi nhưng phản hồi không xác định.")
        except Exception as e:
            logging.error(f"Lỗi đồng bộ GitHub: {e}")
            messagebox.showerror("Lỗi đồng bộ", f"Không thể đẩy users.json lên GitHub:\n{e}")

if __name__ == "__main__":
    app = ToolQuanLyUser()
    app.mainloop()
