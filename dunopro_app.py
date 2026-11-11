from __future__ import annotations

import base64
import datetime as dt
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    import winreg
except Exception:  # pragma: no cover
    winreg = None

try:
    import win32crypt  # type: ignore
except Exception:  # pragma: no cover
    win32crypt = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = None
    ImageDraw = None

try:
    import win32api
    import win32event
    import winerror
except Exception:  # pragma: no cover
    win32api = None
    win32event = None
    winerror = None

APP_NAME = "DuNoPro Thái Bình"
APP_VERSION = "2.2.0"
WORK_ROOT = Path(__file__).resolve().parent
WEB_ROOT = WORK_ROOT / "web"
RUNTIME_DIR = WORK_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
DOWNLOADS_DIR = RUNTIME_DIR / "downloads"
EXPORTS_DIR = RUNTIME_DIR / "exports"
SETTINGS_PATH = RUNTIME_DIR / "settings.json"
SECRETS_PATH = RUNTIME_DIR / "secrets.json"
SHOW_WINDOW_FLAG_PATH = RUNTIME_DIR / "show_window.flag"
SINGLE_INSTANCE_MUTEX_NAME = "DuNoPro_ThaiBinh_SingleInstance_Mutex"


def hide_console_window() -> None:
    """Hide console window if it was created exclusively for this process (e.g. Explorer/Startup)

    and the user did not specify a debug flag. Never hides an interactive user terminal.
    """
    if any(arg in os.sys.argv for arg in ("--debug", "-d", "/debug")):
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return

        process_list = (ctypes.c_uint * 8)()
        count = kernel32.GetConsoleProcessList(process_list, 8)
        if count == 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def check_single_instance() -> Optional[object]:
    """Ensure only one instance runs. If another exists, request it to show and exit."""
    if not win32event or not win32api or not winerror:
        return None
    try:
        mutex = win32event.CreateMutex(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            try:
                RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                SHOW_WINDOW_FLAG_PATH.write_text("show", encoding="ascii")
            except Exception:
                pass
            return False
        return mutex
    except Exception:
        return None
WEB_ASSET_FILES = ("users.json",)
GITHUB_REQUEST_TIMEOUT = 120
GITHUB_SOFT_PAYLOAD_LIMIT = 45 * 1024 * 1024
KHONO_SESSION_REFRESH_SECONDS = 10 * 60
KHONO_REPORT_API_MARKER = "/data/chamNo/thongKeDanhSachKhNoTheoThuNganGiao"

KHONO_LOGIN_URL = "https://khono.npc.com.vn/"
KHONO_REPORT_URL = "https://khono.npc.com.vn/bcao/xuat-ds-khang-no"


class KhonoSessionExpiredError(RuntimeError):
    """KHONO no longer accepts the current browser session."""

LEGACY_PATHS = {}


@dataclass(frozen=True)
class ProfileDef:
    profile_id: str
    display_name: str
    github_repo: str
    web_app_url: str
    profile_dir: Path
    khono_ma_dvi: str = ""


PROFILE_DEFS: Dict[str, ProfileDef] = {
    "thai_binh": ProfileDef(
        profile_id="thai_binh",
        display_name="Điện lực Thái Bình",
        github_repo="BaoCaoThaiBinh",
        web_app_url="https://bao-cao-thai-binh.vercel.app/",
        profile_dir=WORK_ROOT / "profiles" / "thai_binh",
        khono_ma_dvi="PA0901",
    ),
}

DEFAULT_SLOTS = [
    {"active": True, "sh": "08", "sm": "30", "eh": "16", "em": "30"},
    {"active": False, "sh": "00", "sm": "00", "eh": "00", "em": "00"},
    {"active": False, "sh": "00", "sm": "00", "eh": "00", "em": "00"},
]


def get_profile_web_dir(profile: ProfileDef) -> Path:
    return WEB_ROOT / profile.profile_id


@dataclass
class RunStats:
    ok: bool
    downloaded: int = 0
    no_data: int = 0
    failed: int = 0
    pushed: bool = False
    retry_needed: bool = False
    message: str = ""


@dataclass
class WorkerState:
    running: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None


def ensure_dirs() -> None:
    for path in [RUNTIME_DIR, LOG_DIR, DOWNLOADS_DIR, EXPORTS_DIR, WEB_ROOT]:
        path.mkdir(parents=True, exist_ok=True)
    for profile in PROFILE_DEFS.values():
        (DOWNLOADS_DIR / profile.profile_id).mkdir(parents=True, exist_ok=True)
        (EXPORTS_DIR / profile.profile_id).mkdir(parents=True, exist_ok=True)
        profile.profile_dir.mkdir(parents=True, exist_ok=True)
        get_profile_web_dir(profile).mkdir(parents=True, exist_ok=True)


def setup_logger() -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_DIR / "runner.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


LOGGER = setup_logger()


def normalize_text(value: str) -> str:
    value = str(value or "")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return value.strip().lower()


def default_profile_settings() -> dict:
    return {
        "interval": 30,
        "slots": json.loads(json.dumps(DEFAULT_SLOTS)),
        "show_browser": False,
        "remember": False,
        "auto_run": False,
    }


def default_settings() -> dict:
    return {
        "auto_start_windows": False,
        "start_hidden": False,
        "enable_shutdown": False,
        "shutdown_h": "17",
        "shutdown_m": "30",
        "enable_sleep": False,
        "sleep_h": "23",
        "sleep_m": "00",
        "github_user": "nguyentuan86pctb-cpu",
        "github_branch": "main",
        "profiles": {
            k: default_profile_settings()
            for k in PROFILE_DEFS
        },
    }


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = default_settings()
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                incoming = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = self._merge(default_settings(), incoming)
            except Exception as exc:
                LOGGER.exception("Cannot load settings: %s", exc)
                self.data = default_settings()
        else:
            self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _merge(self, base: dict, incoming: dict) -> dict:
        out = json.loads(json.dumps(base))
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = self._merge(out[key], value)
            else:
                out[key] = value
        return out


class SecretStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"profiles": {}, "github_token": ""}
        self.load()

    def _encrypt(self, text: str) -> str:
        if not text:
            return ""
        raw = text.encode("utf-8")
        if win32crypt:
            encrypted = self._extract_crypto_bytes(win32crypt.CryptProtectData(raw, "", None, None, None, 0))
            return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
        return "plain:" + base64.b64encode(raw).decode("ascii")

    def _decrypt(self, cipher: str) -> str:
        if not cipher:
            return ""
        try:
            if cipher.startswith("dpapi:") and win32crypt:
                payload = base64.b64decode(cipher.split(":", 1)[1])
                decrypted = self._extract_crypto_bytes(win32crypt.CryptUnprotectData(payload, None, None, None, 0))
                return decrypted.decode("utf-8")
            if cipher.startswith("plain:"):
                return base64.b64decode(cipher.split(":", 1)[1]).decode("utf-8")
            return base64.b64decode(cipher).decode("utf-8")
        except Exception:
            return ""

    @staticmethod
    def _extract_crypto_bytes(result) -> bytes:
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
        if isinstance(result, tuple):
            for item in result:
                if isinstance(item, (bytes, bytearray)):
                    return bytes(item)
        raise TypeError(f"Unsupported DPAPI return type: {type(result)}")

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {"profiles": {}, "github_token": ""}
        else:
            self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_profile_credential(self, profile_id: str) -> tuple[str, str]:
        raw = self.data.get("profiles", {}).get(profile_id, {})
        user = raw.get("user", "")
        pwd = self._decrypt(raw.get("pass", ""))
        return user, pwd

    def set_profile_credential(self, profile_id: str, user: str, password: str) -> None:
        self.data.setdefault("profiles", {})[profile_id] = {"user": user, "pass": self._encrypt(password)}
        self.save()

    def clear_profile_credential(self, profile_id: str) -> None:
        self.data.setdefault("profiles", {}).pop(profile_id, None)
        self.save()

    def get_github_token(self) -> str:
        env_token = os.getenv("DUNOPRO_GITHUB_TOKEN", "").strip()
        if env_token:
            return env_token
        return self._decrypt(self.data.get("github_token", ""))

    def set_github_token(self, token: str) -> None:
        self.data["github_token"] = self._encrypt(token)
        self.save()


class DebtPipeline:
    def __init__(self, config: ConfigStore, secrets: SecretStore):
        self.config = config
        self.secrets = secrets

    @staticmethod
    def _launch_browser(playwright, show_browser: bool, log: Callable[[str], None]):
        launch_options = {"headless": not show_browser, "timeout": 30_000}

        try:
            # Uu tien Chrome co san tren may de khong bat nguoi dung tai browser Playwright nang.
            return playwright.chromium.launch(channel="chrome", **launch_options)
        except Exception as chrome_exc:
            try:
                log(f"Khong mo duoc Google Chrome, thu browser Playwright: {chrome_exc}")
            except Exception:
                pass
            try:
                return playwright.chromium.launch(**launch_options)
            except Exception as playwright_exc:
                msg = str(playwright_exc)
                if "Executable doesn't exist" in msg or "playwright install" in msg:
                    raise RuntimeError(
                        "May chua co browser cho Playwright. Hay cai Google Chrome hoac chay: python -m playwright install chromium"
                    ) from playwright_exc
                raise

    @staticmethod
    def read_cashiers(excel_path: Path) -> List[str]:
        if not excel_path.exists():
            return []
        df = pd.read_excel(excel_path, header=None)
        values = df.iloc[:, 0].dropna().astype(str).tolist()
        if values and ("thu ngan" in normalize_text(values[0]) or "ten" in normalize_text(values[0])):
            values = values[1:]
        return [item.strip() for item in values if item.strip()]

    @staticmethod
    def copy_cashier_download(
        profile: ProfileDef, source: Path, cashier: str, selected_label: str,
    ) -> Optional[Path]:
        """Keep the latest Vu Thu download per cashier without touching the original."""
        if profile.profile_id != "vu_thu":
            return None
        parts = str(cashier).strip().split(maxsplit=1)
        code = parts[0] if parts else ""
        if not code or not all(char.isalnum() or char in "_-" for char in code):
            raise ValueError("Mã thu ngân không hợp lệ để đặt tên file sao chép")
        name = parts[1] if len(parts) > 1 else str(selected_label).strip()
        name = unicodedata.normalize("NFC", name)
        name = "".join(" " if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in name)
        name = " ".join(name.split()).strip(" .")[:120].rstrip(" .") or "Thu ngân"
        suffix = f" [{code}].xlsx"
        copy_dir = DOWNLOADS_DIR / "thu ngan vu thu"
        copy_dir.mkdir(parents=True, exist_ok=True)
        target = copy_dir / f"{name}{suffix}"
        # Stage in the same directory: a failed copy/locked destination keeps the old file.
        with tempfile.NamedTemporaryFile(dir=copy_dir, prefix=".cashier-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        # A renamed cashier still owns the same code. Never clear the whole directory.
        for previous in copy_dir.iterdir():
            if previous != target and previous.name.casefold().endswith(suffix.casefold()) and previous.is_file():
                previous.unlink()
        return target

    @staticmethod
    def _get_report_date_range(
        profile_id: str,
        today: Optional[dt.date] = None,
    ) -> tuple[dt.date, dt.date]:
        """Return the KHONO report range while keeping the normal monthly default."""
        report_to = today or dt.date.today()
        current_month_start = report_to.replace(day=1)
        report_from = current_month_start

        # Thái Bình lấy từ ngày 01 tháng hiện tại đến ngày hiện tại

        return report_from, report_to

    @staticmethod
    def _parse_report_input_date(value: str) -> Optional[dt.date]:
        try:
            return dt.datetime.strptime(value.strip(), "%d-%m-%Y").date()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _set_report_date_range(page, report_from: dt.date, report_to: dt.date) -> None:
        """Set readonly NgbDatepicker inputs and emit events so Angular updates ngModel."""
        set_input_value = """
            (element, value) => {
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype,
                    'value'
                ).set;
                setter.call(element, value);
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new Event('blur', { bubbles: true }));
            }
        """
        expected_dates = {
            "#from": report_from,
            "#to": report_to,
        }
        for selector, expected_date in expected_dates.items():
            value_to_set = expected_date.strftime("%d-%m-%Y")
            locator = page.locator(selector)
            locator.wait_for(state="visible", timeout=20_000)
            locator.evaluate(set_input_value, value_to_set)
            actual = locator.input_value()
            if DebtPipeline._parse_report_input_date(actual) != expected_date:
                raise RuntimeError(f"Khong dat duoc ngay bao cao {selector}: {actual!r}")

    @staticmethod
    def _normalize_cashier_code(value: object) -> str:
        """Normalize a cashier code without depending on KHONO's label formatting."""
        return "".join(char for char in normalize_text(str(value)) if char.isalnum())

    @classmethod
    def _cashier_option_matches(cls, code: str, label: str, value: str) -> bool:
        """Match the Excel code against either a KHONO option value or its display text."""
        expected = cls._normalize_cashier_code(code)
        if not expected:
            return False

        option_value = cls._normalize_cashier_code(value)
        if option_value == expected:
            return True

        option_label = cls._normalize_cashier_code(label)
        if option_label == expected:
            return True

        # KHONO may display a numeric code with separators or a prefix, for example
        # "Ma TNV: 9709999.020 - Nguyen Van A".  Names normally have no digits,
        # so the digits-only comparison keeps that formatting from hiding a TNV.
        if expected.isdigit():
            for candidate in (label, value):
                option_digits = "".join(char for char in candidate if char.isdigit())
                if option_digits == expected:
                    return True

        return False

    @classmethod
    def _find_cashier_option(cls, page, code: str, timeout_ms: int = 30_000):
        """Find the active KHONO cashier select and its matching option after it loads."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            # KHONO currently exposes the cashier selector as #thungan-select.
            # Keep the generic fallback for older deployments, but never rely on
            # surrounding Vietnamese label text because Angular can rerender it.
            dropdowns = page.locator("#thungan-select").all()
            if not dropdowns:
                dropdowns = page.locator("select").all()

            for dropdown in dropdowns:
                try:
                    if not dropdown.is_visible():
                        continue
                    for option in dropdown.locator("option").all():
                        label = (option.text_content() or "").strip()
                        value = (option.get_attribute("value") or "").strip()
                        if cls._cashier_option_matches(code, label, value):
                            return dropdown, label, value
                except Exception:
                    # KHONO replaces its select while the page is loading; retry the
                    # fresh DOM instead of treating a transient select as missing.
                    continue

            if time.monotonic() >= deadline:
                return None
            page.wait_for_timeout(500)

    @staticmethod
    def _is_no_data_message(*messages: str) -> bool:
        no_data_markers = (
            "khong co du lieu",
            "chua co du lieu",
            "khong tim thay du lieu",
            "khong co ban ghi",
            "khong tim thay ban ghi",
            "khong co ket qua",
            "khong tim thay ket qua",
            "no data available",
            "no records found",
            "no matching records",
        )
        text = " ".join(normalize_text(message) for message in messages if message)
        return any(marker in text for marker in no_data_markers)

    @classmethod
    def _page_shows_no_data(cls, page, dialog_messages: List[str]) -> bool:
        try:
            body_text = page.locator("body").inner_text(timeout=2_000)
        except Exception:
            body_text = ""
        return cls._is_no_data_message(body_text, *dialog_messages)

    @staticmethod
    def _is_download_event_timeout(exc: Exception) -> bool:
        """Return whether Playwright waited for a download that never started."""
        return isinstance(exc, PlaywrightTimeoutError) and 'waiting for event "download"' in str(exc).lower()

    @staticmethod
    def _login_and_open_report(page, user: str, pwd: str) -> None:
        # A still-valid KHONO cookie makes the root URL skip the login form.
        # Clear only this isolated Playwright context so a refresh always creates
        # a genuinely new authenticated session.
        page.context.clear_cookies()
        page.goto(KHONO_LOGIN_URL)
        username = page.locator("input[type='text']").first
        password = page.locator("input[type='password']").first
        username.wait_for(state="visible", timeout=20_000)
        username.fill(user)
        password.fill(pwd)
        page.locator("button:has-text('Đăng nhập'), input[type='submit']").first.click()
        page.wait_for_timeout(3000)

        page.goto(KHONO_REPORT_URL)
        dropdown = page.locator("#thungan-select")
        dropdown.wait_for(state="visible", timeout=30_000)
        page.wait_for_function(
            "selector => document.querySelector(selector)?.options.length > 1",
            arg="#thungan-select",
            timeout=30_000,
        )

    @staticmethod
    def _is_cashier_report_response(response, code: str) -> bool:
        return KHONO_REPORT_API_MARKER in response.url and f"maThuNgan={code}" in response.url

    @staticmethod
    def _read_cashier_report_rows(response) -> List[dict]:
        if response.status in (401, 403):
            raise KhonoSessionExpiredError(f"KHONO tra ve HTTP {response.status}")
        if response.status != 200:
            raise RuntimeError(f"KHONO tra ve HTTP {response.status} khi xem du lieu")

        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("KHONO tra ve du lieu khong dung dinh dang danh sach")
        return rows

    @staticmethod
    def _find_header_row(temp_df: pd.DataFrame) -> int:
        for idx, row in temp_df.iterrows():
            row_text = " ".join(str(v) for v in row.values)
            normalized = normalize_text(row_text)
            if any(k in normalized for k in ["tong tien", "stt", "ten khach hang", "so tien"]):
                return int(idx)
        return 0

    @staticmethod
    def _format_money_column(series: pd.Series) -> pd.Series:
        cleaned = (
            series.astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.replace(" ", "", regex=False)
        )
        numeric = pd.to_numeric(cleaned, errors="coerce").fillna(0)
        return numeric.apply(lambda x: f"{x:,.0f}".replace(",", "."))

    @staticmethod
    def _purge_jsdelivr(github_user: str, repo: str, branch: str, file_name: str, log: Callable[[str], None]) -> None:
        try:
            purge_url = f"https://purge.jsdelivr.net/gh/{github_user}/{repo}@{branch}/{file_name}"
            res = requests.get(purge_url, timeout=5)
            if res.status_code == 200:
                log(f"⚡ Da lam moi cache CDN jsDelivr: {file_name}")
        except Exception:
            pass

    def clean_and_merge(self, profile: ProfileDef, log: Callable[[str], None]) -> Optional[Path]:
        source_dir = DOWNLOADS_DIR / profile.profile_id
        frames: List[pd.DataFrame] = []

        for file in source_dir.iterdir():
            if file.suffix.lower() != ".xlsx" or file.name.startswith("~$"):
                continue
            try:
                temp_df = pd.read_excel(file, header=None)
                header_row = self._find_header_row(temp_df)
                df = pd.read_excel(file, header=header_row)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                log(f"❌ Loi doc file {file.name}: {exc}")

        if not frames:
            return None

        merged = pd.concat(frames, ignore_index=True)
        merged.dropna(how="all", inplace=True)

        drop_norm = {"stt", "tien no", "thue no"}
        to_drop = [col for col in merged.columns if normalize_text(col) in drop_norm]
        if to_drop:
            merged.drop(columns=to_drop, inplace=True, errors="ignore")

        money_cols = [col for col in merged.columns if any(k in normalize_text(col) for k in ["tong tien", "so tien"])]
        for col in money_cols:
            merged[col] = self._format_money_column(merged[col])

        merged = merged.fillna("")
        out_path = EXPORTS_DIR / profile.profile_id / f"TongHop_DuNo_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        merged.to_excel(out_path, index=False)
        return out_path

    def push_to_github(self, profile: ProfileDef, excel_path: Path, log: Callable[[str], None]) -> bool:
        token = self.secrets.get_github_token().strip()
        if not token:
            log("❌ Chua co GitHub Token. Vao Cai dat de nhap token.")
            return False

        github_user = self.config.data.get("github_user", "").strip()
        github_branch = self.config.data.get("github_branch", "main").strip() or "main"

        expected_web = "https://bao-cao-thai-binh.vercel.app/"

        if profile.web_app_url != expected_web:
            log("❌ Chan bao mat: profile mapping web app khong hop le.")
            return False

        df = pd.read_excel(excel_path).fillna("")
        records = df.to_dict(orient="records")
        updated_at = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        payload = {
            "profile": profile.profile_id,
            "web_app": profile.web_app_url,
            "cap_nhat_luc": updated_at,
            "tong_so_kh": len(records),
            "du_lieu": records,
        }

        data_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        size_mb = len(data_bytes) / (1024 * 1024)
        log(f"-> Chuan bi push {len(records)} dong len GitHub ({size_mb:.2f} MB JSON)")
        if len(data_bytes) > GITHUB_SOFT_PAYLOAD_LIMIT:
            log("⚠ Du lieu dang rat lon. Nen giam cot khong can thiet hoac chia nguon neu GitHub tiep tuc tu choi.")
        encoded = base64.b64encode(data_bytes).decode("ascii")

        api_url = f"https://api.github.com/repos/{github_user}/{profile.github_repo}/contents/data_duno.json"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        sha = None
        target = f"{github_user}/{profile.github_repo}@{github_branch}:data_duno.json"
        get_res = self._github_request(
            "GET", api_url, headers=headers, log=log, target=target,
            params={"ref": github_branch},
        )
        if get_res is None:
            return False
        if get_res.status_code == 200:
            try:
                metadata = get_res.json()
                sha = metadata.get("sha")
                if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                    raise ValueError("Invalid file SHA")
                # Contents API omits inline content for files larger than 1 MB.
                # Read this exact blob, never a different branch or a cached local copy.
                if metadata.get("encoding") != "base64":
                    blob_url = f"https://api.github.com/repos/{github_user}/{profile.github_repo}/git/blobs/{sha}"
                    blob_res = self._github_request("GET", blob_url, headers=headers, log=log, target=target)
                    if blob_res is None:
                        return False
                    if blob_res.status_code != 200:
                        self._log_github_failure(
                            log, action="Doc du lieu de so sanh", response=blob_res,
                            repo=profile.github_repo, branch=github_branch,
                            path="data_duno.json", payload_bytes=0,
                        )
                        return False
                    metadata = blob_res.json()
                if metadata.get("encoding") != "base64":
                    raise ValueError("Missing Base64 content")
                old_bytes = base64.b64decode("".join(metadata["content"].split()), validate=True)
                previous = json.loads(old_bytes.decode("utf-8"))
                if not isinstance(previous, dict):
                    raise ValueError("Expected JSON object")
                # Only the outer update time is volatile. Preserve list order,
                # identifiers, blanks, value types, nested timestamps and all data.
                def comparable(value):
                    return json.dumps(
                        {key: item for key, item in value.items() if key != "cap_nhat_luc"},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    )
                if comparable(previous) == comparable(payload):
                    log(f"✅ Du lieu khong thay doi -> bo qua push {profile.github_repo}/data_duno.json; giu nguyen thoi diem cap nhat tren web.")
                    return True
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
                log(f"❌ Khong doc/so sanh duoc du lieu GitHub ({type(exc).__name__}); khong push de tranh ghi de khi chua xac minh.")
                return False
        elif get_res.status_code not in (404,):
            self._log_github_failure(
                log,
                action="Doc SHA truoc khi push",
                response=get_res,
                repo=profile.github_repo,
                branch=github_branch,
                path="data_duno.json",
                payload_bytes=0,
            )
            return False

        body = {
            "message": f"Cap nhat DuNo {profile.profile_id} luc {updated_at}",
            "content": encoded,
            "branch": github_branch,
        }
        if sha:
            body["sha"] = sha

        put_res = self._github_request("PUT", api_url, headers=headers, json=body, log=log, target=target)
        if put_res and put_res.status_code in (200, 201):
            log(f"✅ Push GitHub thanh cong -> {profile.github_repo}/data_duno.json")
            self._purge_jsdelivr(github_user, profile.github_repo, github_branch, "data_duno.json", log)
            # users.json chi duoc dong bo khi bam nut thu cong tren giao dien.
            return True

        if put_res is not None:
            self._log_github_failure(
                log,
                action="Push data_duno.json",
                response=put_res,
                repo=profile.github_repo,
                branch=github_branch,
                path="data_duno.json",
                payload_bytes=len(data_bytes),
            )
        return False

    def _github_request(
        self,
        method: str,
        url: str,
        headers: dict,
        log: Callable[[str], None],
        target: str = "",
        **kwargs,
    ) -> Optional[requests.Response]:
        for attempt in range(1, 4):
            try:
                return requests.request(method, url, headers=headers, timeout=GITHUB_REQUEST_TIMEOUT, **kwargs)
            except requests.RequestException as exc:
                if attempt >= 3:
                    label = f" ({target})" if target else ""
                    log(f"❌ GitHub request loi sau 3 lan thu{label}: {exc.__class__.__name__}: {exc}")
                    log(f"   Method={method}, timeout={GITHUB_REQUEST_TIMEOUT}s")
                    return None
                label = f" ({target})" if target else ""
                log(f"⚠ GitHub tam loi lan {attempt}{label}, thu lai sau 5 giay: {exc.__class__.__name__}: {exc}")
                time.sleep(5)
        return None

    def _format_bytes(self, size: int) -> str:
        if size <= 0:
            return "0 B"
        mb = size / (1024 * 1024)
        return f"{mb:.2f} MB ({size:,} bytes)"

    def _github_error_text(self, response: requests.Response) -> str:
        try:
            data = response.json()
        except Exception:
            return response.text[:1000].replace("\n", " ")

        parts = []
        message = data.get("message")
        if message:
            parts.append(str(message))
        errors = data.get("errors")
        if isinstance(errors, list):
            for item in errors[:5]:
                if isinstance(item, dict):
                    detail = item.get("message") or item.get("code") or item.get("resource")
                    if detail:
                        parts.append(str(detail))
                else:
                    parts.append(str(item))
        documentation_url = data.get("documentation_url")
        if documentation_url:
            parts.append(f"docs: {documentation_url}")
        return " | ".join(parts) if parts else response.text[:1000].replace("\n", " ")

    def _github_failure_hint(self, response: requests.Response, payload_bytes: int) -> str:
        status = response.status_code
        text = self._github_error_text(response).lower()
        if status in (401, 403) and "rate limit" in text:
            return "Co the bi gioi han API GitHub. Cho mot luc roi thu lai."
        if status == 401:
            return "Token sai/het han/chua duoc luu dung."
        if status == 403:
            return "Token co the thieu quyen repo contents:write, repo private bi chan, hoac bi GitHub rate limit."
        if status == 404:
            return "Khong thay repo/branch/file. Kiem tra GitHub user, ten repo, branch main va quyen token."
        if status == 409:
            return "Xung dot SHA/branch dang thay doi. Thu Run now lai sau vai giay."
        if status == 413:
            return "Payload gui len qua lon voi API/lop mang trung gian."
        if status == 422:
            if payload_bytes >= 90 * 1024 * 1024:
                return "File gan/vuot nguong 100 MB cua GitHub. Can giam dung luong data_duno.json."
            return "GitHub tu choi noi dung request. Thuong do branch khong hop le, SHA cu, file qua lon, hoac quyen token."
        if status >= 500:
            return "GitHub/server tam loi. Tool da retry, co the Run now lai."
        return "Kiem tra token, quyen repo, branch, ket noi mang va dung luong file."

    def _log_github_failure(
        self,
        log: Callable[[str], None],
        action: str,
        response: requests.Response,
        repo: str,
        branch: str,
        path: str,
        payload_bytes: int,
    ) -> None:
        log(f"❌ {action} that bai")
        log(f"   Repo={repo}, branch={branch}, file={path}")
        if payload_bytes:
            log(f"   Dung luong gui len: {self._format_bytes(payload_bytes)}")
            if payload_bytes >= 50 * 1024 * 1024:
                log("   Luu y: tren 50 MB GitHub co the canh bao/phan hoi cham; tren 100 MB se bi chan.")
        log(f"   HTTP {response.status_code} {response.reason}")
        log(f"   GitHub message: {self._github_error_text(response)}")
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            reset_text = ""
            if reset:
                try:
                    reset_text = dt.datetime.fromtimestamp(int(reset)).strftime("%H:%M:%S %d/%m/%Y")
                except Exception:
                    reset_text = reset
            log(f"   Rate limit con lai: {remaining}" + (f", reset luc {reset_text}" if reset_text else ""))
        log(f"   Goi y: {self._github_failure_hint(response, payload_bytes)}")

    def sync_web_assets(self, profile: ProfileDef, log: Callable[[str], None]) -> bool:
        token = self.secrets.get_github_token().strip()
        if not token:
            log("Loi: Chua co GitHub Token. Vao Cai dat de nhap token.")
            return False

        github_user = self.config.data.get("github_user", "").strip()
        if not github_user:
            log("Loi: Chua co GitHub User trong Cai dat.")
            return False

        github_branch = self.config.data.get("github_branch", "main").strip() or "main"
        web_dir = get_profile_web_dir(profile)
        if not web_dir.exists():
            log(f"Loi: chua co thu muc web cuc bo {web_dir}")
            return False

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        ok = True
        for file_name in WEB_ASSET_FILES:
            local_file = web_dir / file_name
            if not local_file.exists():
                log(f"Loi: thieu file web {local_file}")
                ok = False
                continue

            api_url = f"https://api.github.com/repos/{github_user}/{profile.github_repo}/contents/{file_name}"
            sha = None
            target = f"{github_user}/{profile.github_repo}@{github_branch}:{file_name}"
            get_res = self._github_request("GET", api_url, headers=headers, log=log, target=target)
            if get_res is None:
                ok = False
                continue
            if get_res.status_code == 200:
                sha = get_res.json().get("sha")
            elif get_res.status_code not in (404,):
                self._log_github_failure(
                    log,
                    action=f"Doc SHA truoc khi dong bo {file_name}",
                    response=get_res,
                    repo=profile.github_repo,
                    branch=github_branch,
                    path=file_name,
                    payload_bytes=0,
                )
                ok = False
                continue

            file_bytes = local_file.read_bytes()
            body = {
                "message": f"Dong bo users.json {profile.profile_id}: {file_name}",
                "content": base64.b64encode(file_bytes).decode("ascii"),
                "branch": github_branch,
            }
            if sha:
                body["sha"] = sha

            put_res = self._github_request("PUT", api_url, headers=headers, json=body, log=log, target=target)
            if put_res and put_res.status_code in (200, 201):
                log(f"Push GitHub thanh cong -> {profile.github_repo}/{file_name}")
                self._purge_jsdelivr(github_user, profile.github_repo, github_branch, file_name, log)
            else:
                if put_res is not None:
                    self._log_github_failure(
                        log,
                        action=f"Push {file_name}",
                        response=put_res,
                        repo=profile.github_repo,
                        branch=github_branch,
                        path=file_name,
                        payload_bytes=len(file_bytes),
                    )
                ok = False

        return ok

    def run_playwright(
        self,
        profile: ProfileDef,
        user: str,
        pwd: str,
        show_browser: bool,
        cashiers: List[str],
        log: Callable[[str], None],
    ) -> RunStats:
        download_dir = DOWNLOADS_DIR / profile.profile_id
        for old_file in download_dir.glob("*.xlsx"):
            try:
                old_file.unlink()
            except Exception:
                pass

        downloaded = 0
        no_data = 0
        failed = 0

        with sync_playwright() as p:
            log("Dang mo trinh duyet de rut du lieu KHONO")
            browser = self._launch_browser(p, show_browser, log)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(60_000)
            page.set_default_navigation_timeout(60_000)
            try:
                self._login_and_open_report(page, user, pwd)
                session_started = time.monotonic()
                dialog_messages: List[str] = []

                def handle_dialog(dialog) -> None:
                    dialog_messages.append(dialog.message)
                    dialog.dismiss()

                page.on("dialog", handle_dialog)
                report_from, report_to = self._get_report_date_range(profile.profile_id)
                if profile.profile_id == "vu_thu":
                    log(
                        "Vu Thu - tat ca TNV: "
                        f"{report_from.strftime('%d-%m-%Y')} -> {report_to.strftime('%d-%m-%Y')}"
                    )

                for cashier in cashiers:
                    code = str(cashier).split()[0].strip()
                    if not code:
                        continue
                    dialog_messages.clear()
                    log(f"▶ Dang tai ma TNV: {code}")
                    cashier_done = False
                    last_error: Optional[Exception] = None
                    copy_pending = False
                    vu_thu_file_name = (
                        f"DuNo_{code}_{dt.datetime.now().strftime('%d%m%Y_%H%M%S')}.xlsx"
                        if profile.profile_id == "vu_thu" else None
                    )

                    for attempt in range(2):
                        try:
                            if time.monotonic() - session_started >= KHONO_SESSION_REFRESH_SECONDS:
                                log("↻ Lam moi phien KHONO de tranh het han khi dang rut")
                                self._login_and_open_report(page, user, pwd)
                                session_started = time.monotonic()

                            cashier_option = self._find_cashier_option(page, code, timeout_ms=8_000)
                            if not cashier_option:
                                raise KhonoSessionExpiredError(
                                    f"Danh sach thu ngan KHONO khong con ma {code}"
                                )

                            dropdown, selected_label, selected_value = cashier_option
                            if selected_value:
                                dropdown.select_option(value=selected_value)
                            else:
                                dropdown.select_option(label=selected_label)
                            selected_code = self._normalize_cashier_code(dropdown.input_value())
                            if selected_code != self._normalize_cashier_code(code):
                                raise RuntimeError(
                                    f"KHONO khong chon dung ma TNV {code}: {dropdown.input_value()!r}"
                                )

                            self._set_report_date_range(page, report_from, report_to)
                            with page.expect_response(
                                lambda response: self._is_cashier_report_response(response, code),
                                timeout=60_000,
                            ) as response_info:
                                page.locator("button:has-text('Xem'), input[value='Xem']").first.click()

                            rows = self._read_cashier_report_rows(response_info.value)
                            try:
                                page.locator(".app-waiting").wait_for(state="hidden", timeout=20_000)
                            except Exception:
                                pass

                            if not rows:
                                if copy_pending:
                                    raise RuntimeError("Bản tải trước chưa sao chép thành công; vẫn cần thử lại")
                                no_data += 1
                                log(f"ℹ Ma TNV {code} khong co du lieu (API tra 0 dong), bo qua")
                                cashier_done = True
                                break

                            page.wait_for_function(
                                "() => document.querySelectorAll('.ag-center-cols-container .ag-row').length > 0",
                                timeout=20_000,
                            )

                            with page.expect_download(timeout=45_000) as dl_info:
                                page.locator("button:has-text('Xuất file'), input[value='Xuất file']").first.click()

                            # Reuse the source path on Vu Thu retries to avoid duplicate merge rows
                            # if a download succeeded but updating its named copy failed.
                            file_name = vu_thu_file_name or f"DuNo_{code}_{dt.datetime.now().strftime('%d%m%Y_%H%M%S')}.xlsx"
                            save_path = download_dir / file_name
                            dl_info.value.save_as(str(save_path))
                            copy_pending = profile.profile_id == "vu_thu"
                            copied_path = self.copy_cashier_download(profile, save_path, cashier, selected_label)
                            copy_pending = False
                            if copied_path:
                                log(f"📁 Đã cập nhật bản sao thu ngân: {copied_path.name}")
                            downloaded += 1
                            log(f"✅ Da tai: {file_name} (API co {len(rows)} dong)")
                            cashier_done = True
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt == 0:
                                log(f"⚠ Loi tam thoi ma {code}, dang nhap lai KHONO va thu lai: {exc}")
                                try:
                                    self._login_and_open_report(page, user, pwd)
                                    session_started = time.monotonic()
                                except Exception as login_exc:
                                    last_error = login_exc
                                    break

                    if not cashier_done:
                        failed += 1
                        log(f"❌ Loi tai ma {code} sau khi da dang nhap lai: {last_error}")

            except Exception as exc:
                return RunStats(
                    ok=False,
                    downloaded=downloaded,
                    no_data=no_data,
                    failed=max(len(cashiers) - downloaded - no_data, 1),
                    retry_needed=True,
                    message=f"Loi web/he thong: {exc}",
                )
            finally:
                try:
                    page.wait_for_timeout(500)
                    context.close()
                except Exception:
                    pass
                browser.close()

        ok = failed == 0 and (downloaded > 0 or no_data > 0)
        msg = f"Tai xong {downloaded}, khong co du lieu {no_data}, loi {failed}."
        return RunStats(
            ok=ok,
            downloaded=downloaded,
            no_data=no_data,
            failed=failed,
            retry_needed=failed > 0,
            message=msg,
        )

    def run_api(
        self,
        profile: ProfileDef,
        user: str,
        pwd: str,
        show_browser: bool,
        cashiers: List[str],
        log: Callable[[str], None],
    ) -> tuple[RunStats, List[dict]]:
        downloaded = 0
        no_data = 0
        failed = 0
        all_records: List[dict] = []

        with sync_playwright() as p:
            log("Dang mo trinh duyet de rut du lieu KHONO qua API")
            browser = self._launch_browser(p, show_browser, log)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(60_000)
            page.set_default_navigation_timeout(60_000)
            try:
                self._login_and_open_report(page, user, pwd)
                session_started = time.monotonic()
                report_from, report_to = self._get_report_date_range(profile.profile_id)
                from_date = f"{report_from.day}-{report_from.month}-{report_from.year}"
                to_date = f"{report_to.day}-{report_to.month}-{report_to.year}"
                ma_dvi = profile.khono_ma_dvi
                if not ma_dvi:
                    raise RuntimeError(f"Profile {profile.profile_id} chua co ma don vi KHONO (khono_ma_dvi)")
                log(
                    "Thai Binh - tat ca TNV: "
                    f"{from_date} -> {to_date}"
                )

                for cashier in cashiers:
                    code = str(cashier).split()[0].strip()
                    if not code:
                        continue
                    cashier_done = False
                    last_error: Optional[Exception] = None

                    for attempt in range(2):
                        try:
                            if time.monotonic() - session_started >= KHONO_SESSION_REFRESH_SECONDS:
                                log("↻ Lam moi phien KHONO de tranh het han khi dang rut")
                                self._login_and_open_report(page, user, pwd)
                                session_started = time.monotonic()

                            url = (
                                f"https://khono.npc.com.vn{KHONO_REPORT_API_MARKER}"
                                f"?maDviQly={ma_dvi}&maThuNgan={code}"
                                f"&fromDate={from_date}&toDate={to_date}"
                                f"&page=1&size=999999"
                            )
                            log(f"▶ Dang lay du lieu ma TNV qua API: {code}")
                            res = context.request.get(url, timeout=60_000)
                            if res.status in (401, 403):
                                raise KhonoSessionExpiredError(f"KHONO tra ve HTTP {res.status}")
                            if res.status != 200:
                                body_preview = ""
                                try:
                                    body_preview = res.text()[:500]
                                except Exception:
                                    pass
                                raise RuntimeError(
                                    f"KHONO API tra ve HTTP {res.status} khi lay du lieu."
                                    f" Response: {body_preview}"
                                )

                            rows = res.json()
                            if not isinstance(rows, list):
                                raise RuntimeError("KHONO tra ve du lieu khong dung dinh dang danh sach")

                            if any(not isinstance(r, dict) for r in rows):
                                raise RuntimeError("KHONO tra ve phan tu khong dung dinh dang dict")

                            if not rows:
                                no_data += 1
                                log(f"ℹ Ma TNV {code} khong co du lieu (API tra 0 dong), bo qua")
                                cashier_done = True
                                break

                            downloaded += 1
                            all_records.extend(rows)
                            log(f"✅ Ma TNV {code}: API tra ve {len(rows)} dong")
                            cashier_done = True
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt == 0:
                                log(f"⚠ Loi tam thoi ma {code}, dang nhap lai KHONO va thu lai: {exc}")
                                try:
                                    self._login_and_open_report(page, user, pwd)
                                    session_started = time.monotonic()
                                except Exception as login_exc:
                                    last_error = login_exc
                                    break

                    if not cashier_done:
                        failed += 1
                        log(f"❌ Loi lay du lieu ma {code} sau khi da dang nhap lai: {last_error}")

            except Exception as exc:
                return (
                    RunStats(
                        ok=False,
                        downloaded=downloaded,
                        no_data=no_data,
                        failed=max(len(cashiers) - downloaded - no_data, 1),
                        retry_needed=True,
                        message=f"Loi web/he thong: {exc}",
                    ),
                    [],
                )
            finally:
                try:
                    page.wait_for_timeout(500)
                    context.close()
                except Exception:
                    pass
                browser.close()

        ok = failed == 0 and (downloaded > 0 or no_data > 0)
        msg = f"Rut xong {downloaded}, khong co du lieu {no_data}, loi {failed}."
        return (
            RunStats(
                ok=ok,
                downloaded=downloaded,
                no_data=no_data,
                failed=failed,
                retry_needed=failed > 0,
                message=msg,
            ),
            all_records,
        )

    @classmethod
    def _normalize_api_records(cls, all_rows: List[dict], log: Callable[[str], None]) -> List[dict]:
        if not all_rows:
            return []

        drop_norm = {"stt", "tien no", "thue no"}

        def is_money_col(col_name: str) -> bool:
            norm = normalize_text(col_name)
            return any(k in norm for k in ["tong tien", "so tien"])

        def format_money(val: object) -> str:
            if val is None or val == "":
                return ""
            if isinstance(val, (int, float)):
                return f"{val:,.0f}".replace(",", ".")
            cleaned = (
                str(val)
                .replace(".", "")
                .replace(",", "")
                .replace(" ", "")
            )
            try:
                numeric = float(cleaned)
                return f"{numeric:,.0f}".replace(",", ".")
            except (ValueError, TypeError):
                return str(val)

        normalized_rows: List[dict] = []
        for row in all_rows:
            if any(k in row for k in ("maKh", "tenKh", "idHoaDon", "tongTien")):
                # Chuyển đổi các trường API camelCase sang 14 cột tiếng Việt chuẩn của web dashboard
                ky = row.get("ky")
                thang = row.get("thang")
                nam = row.get("nam")
                if ky is not None and thang is not None and nam is not None and str(ky) != "" and str(thang) != "" and str(nam) != "":
                    ky_str = f"{ky}-{thang}/{nam}"
                else:
                    ky_str = str(row.get("Kỳ-tháng/năm", row.get("kyThangNam", "")) or "").strip()

                dia_chi = row.get("diaChi") or row.get("diaChiSuDungDien") or ""
                dia_chi_str = str(dia_chi).strip()

                ma_tn = str(row.get("maThuNgan", "") or "").strip()
                ten_tn = str(row.get("tenThuNgan", "") or "").strip()
                if ma_tn and ten_tn:
                    tnv = f"{ma_tn} {ten_tn}"
                else:
                    tnv = ten_tn or ma_tn or str(row.get("Thu ngân viên", "") or "").strip()

                new_row = {
                    "Mã Sổ GCS": "" if row.get("maSoGcs") is None else str(row.get("maSoGcs")).strip(),
                    "ID hóa đơn": "" if row.get("idHoaDon") is None else str(row.get("idHoaDon")).strip(),
                    "Mã khách hàng": "" if row.get("maKh") is None else str(row.get("maKh")).strip(),
                    "Tên khách hàng": "" if row.get("tenKh") is None else str(row.get("tenKh")).strip(),
                    "Địa chỉ khách hàng": dia_chi_str,
                    "Kỳ-tháng/năm": ky_str,
                    "Loại hóa đơn": "" if row.get("loaiHoaDon") is None else str(row.get("loaiHoaDon")).strip(),
                    "Ngày phát hành": "" if row.get("ngayPhatHanh") is None else str(row.get("ngayPhatHanh")).strip(),
                    "Tổng tiền": format_money(row.get("tongTien")),
                    "Số điện thoại": "" if row.get("soDienThoai") is None else str(row.get("soDienThoai")).strip(),
                    "Thu ngân viên": tnv,
                    "Số CTơ": "" if row.get("soCongTo") is None else str(row.get("soCongTo")).strip(),
                    "Số Cột": "" if row.get("soCot") is None else str(row.get("soCot")).strip(),
                    "Tên Trạm": "" if row.get("tenTram") is None else str(row.get("tenTram")).strip(),
                }
            else:
                new_row = {}
                for col, val in row.items():
                    if normalize_text(col) in drop_norm:
                        continue
                    if is_money_col(col):
                        new_row[col] = format_money(val)
                    else:
                        new_row[col] = "" if val is None else val
            normalized_rows.append(new_row)

        log(f"-> Da chuan hoa {len(normalized_rows)} ban ghi tu API")
        return normalized_rows

    def push_to_github_direct(self, profile: ProfileDef, records: List[dict], log: Callable[[str], None]) -> bool:
        token = self.secrets.get_github_token().strip()
        if not token:
            log("❌ Chua co GitHub Token. Vao Cai dat de nhap token.")
            return False

        github_user = self.config.data.get("github_user", "").strip()
        github_branch = self.config.data.get("github_branch", "main").strip() or "main"

        expected_web = "https://bao-cao-thai-binh.vercel.app/"

        if profile.web_app_url != expected_web:
            log("❌ Chan bao mat: profile mapping web app khong hop le.")
            return False

        updated_at = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        payload = {
            "profile": profile.profile_id,
            "web_app": profile.web_app_url,
            "cap_nhat_luc": updated_at,
            "tong_so_kh": len(records),
            "du_lieu": records,
        }

        data_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        size_mb = len(data_bytes) / (1024 * 1024)
        log(f"-> Chuan bi push {len(records)} dong len GitHub ({size_mb:.2f} MB JSON)")
        if len(data_bytes) > GITHUB_SOFT_PAYLOAD_LIMIT:
            log("⚠ Du lieu dang rat lon. Nen giam cot khong can thiet hoac chia nguon neu GitHub tiep tuc tu choi.")
        encoded = base64.b64encode(data_bytes).decode("ascii")

        api_url = f"https://api.github.com/repos/{github_user}/{profile.github_repo}/contents/data_duno.json"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        sha = None
        target = f"{github_user}/{profile.github_repo}@{github_branch}:data_duno.json"
        get_res = self._github_request(
            "GET", api_url, headers=headers, log=log, target=target,
            params={"ref": github_branch},
        )
        if get_res is None:
            return False
        if get_res.status_code == 200:
            try:
                metadata = get_res.json()
                sha = metadata.get("sha")
                if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                    raise ValueError("Invalid file SHA")
                # Contents API omits inline content for files larger than 1 MB.
                # Read this exact blob, never a different branch or a cached local copy.
                if metadata.get("encoding") != "base64":
                    blob_url = f"https://api.github.com/repos/{github_user}/{profile.github_repo}/git/blobs/{sha}"
                    blob_res = self._github_request("GET", blob_url, headers=headers, log=log, target=target)
                    if blob_res is None:
                        return False
                    if blob_res.status_code != 200:
                        self._log_github_failure(
                            log, action="Doc du lieu de so sanh", response=blob_res,
                            repo=profile.github_repo, branch=github_branch,
                            path="data_duno.json", payload_bytes=0,
                        )
                        return False
                    metadata = blob_res.json()
                if metadata.get("encoding") != "base64":
                    raise ValueError("Missing Base64 content")
                old_bytes = base64.b64decode("".join(metadata["content"].split()), validate=True)
                previous = json.loads(old_bytes.decode("utf-8"))
                if not isinstance(previous, dict):
                    raise ValueError("Expected JSON object")
                # Only the outer update time is volatile. Preserve list order,
                # identifiers, blanks, value types, nested timestamps and all data.
                def comparable(value):
                    return json.dumps(
                        {key: item for key, item in value.items() if key != "cap_nhat_luc"},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    )
                if comparable(previous) == comparable(payload):
                    log(f"✅ Du lieu khong thay doi -> bo qua push {profile.github_repo}/data_duno.json; giu nguyen thoi diem cap nhat tren web.")
                    return True
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
                log(f"❌ Khong doc/so sanh duoc du lieu GitHub ({type(exc).__name__}); khong push de tranh ghi de khi chua xac minh.")
                return False
        elif get_res.status_code not in (404,):
            self._log_github_failure(
                log,
                action="Doc SHA truoc khi push",
                response=get_res,
                repo=profile.github_repo,
                branch=github_branch,
                path="data_duno.json",
                payload_bytes=0,
            )
            return False

        body = {
            "message": f"Cap nhat DuNo {profile.profile_id} luc {updated_at}",
            "content": encoded,
            "branch": github_branch,
        }
        if sha:
            body["sha"] = sha

        put_res = self._github_request("PUT", api_url, headers=headers, json=body, log=log, target=target)
        if put_res and put_res.status_code in (200, 201):
            log(f"✅ Push GitHub thanh cong -> {profile.github_repo}/data_duno.json")
            self._purge_jsdelivr(github_user, profile.github_repo, github_branch, "data_duno.json", log)
            # users.json chi duoc dong bo khi bam nut thu cong tren giao dien.
            return True

        if put_res is not None:
            self._log_github_failure(
                log,
                action="Push data_duno.json",
                response=put_res,
                repo=profile.github_repo,
                branch=github_branch,
                path="data_duno.json",
                payload_bytes=len(data_bytes),
            )
        return False


class ProfileWorker:
    _power_lock = threading.Lock()
    _shutdown_done_minute = ""
    _sleep_done_minute = ""

    def __init__(
        self,
        profile: ProfileDef,
        config: ConfigStore,
        secrets: SecretStore,
        pipeline: DebtPipeline,
        log: Callable[[str, str], None],
    ):
        self.profile = profile
        self.config = config
        self.secrets = secrets
        self.pipeline = pipeline
        self.log = log
        self.state = WorkerState()
        self.force_run = threading.Event()
        self.last_run_stamp = ""
        self.retry_time: Optional[str] = None

    @staticmethod
    def build_targets(slots: List[dict], interval: int) -> List[str]:
        targets = set()
        for slot in slots:
            if not slot.get("active"):
                continue
            try:
                start_dt = dt.datetime.strptime(f"{slot['sh']}:{slot['sm']}", "%H:%M")
                end_dt = dt.datetime.strptime(f"{slot['eh']}:{slot['em']}", "%H:%M")
                # Ho tro khung gio qua ngay: vd 23:00 -> 16:30 (ngay hom sau)
                if end_dt < start_dt:
                    end_dt += dt.timedelta(days=1)
                cursor = start_dt
                while cursor <= end_dt:
                    targets.add(cursor.strftime("%H:%M"))
                    cursor += dt.timedelta(minutes=interval)
            except Exception:
                continue
        return sorted(targets)

    def start(self) -> None:
        if self.state.running:
            return
        self.state.running = True
        self.state.stop_event.clear()
        self.state.thread = threading.Thread(target=self._loop, daemon=True)
        self.state.thread.start()
        self.log(self.profile.profile_id, "✅ Da bat worker")

    def stop(self) -> None:
        if not self.state.running:
            return
        self.state.stop_event.set()
        self.state.running = False
        self.log(self.profile.profile_id, "⏹ Da gui lenh dung")

    def trigger_now(self) -> None:
        self.force_run.set()

    def _loop(self) -> None:
        while not self.state.stop_event.is_set():
            p_cfg = self.config.data["profiles"][self.profile.profile_id]
            interval = int(p_cfg.get("interval", 30))
            targets = self.build_targets(p_cfg.get("slots", DEFAULT_SLOTS), interval)

            now_hm = dt.datetime.now().strftime("%H:%M")
            now_full = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

            self._handle_power_events(now_hm)

            should_run = False
            if self.force_run.is_set():
                should_run = True
                self.force_run.clear()
            elif now_hm in targets or (self.retry_time and now_hm == self.retry_time):
                should_run = now_full != self.last_run_stamp

            if should_run:
                is_retry = self.retry_time and now_hm == self.retry_time
                if is_retry:
                    self.log(self.profile.profile_id, f"[!] Den moc rut bu {now_hm}")
                    self.retry_time = None
                else:
                    self.log(self.profile.profile_id, f"[!] Den moc lich {now_hm}")

                stats = self._run_once()
                self.last_run_stamp = now_full
                if stats.retry_needed:
                    retry_dt = dt.datetime.now() + dt.timedelta(minutes=15)
                    self.retry_time = retry_dt.strftime("%H:%M")
                    self.log(self.profile.profile_id, f"⚠ Hen rut bu vao {self.retry_time}")

            for _ in range(10):
                if self.state.stop_event.is_set():
                    break
                time.sleep(1)

        self.state.running = False

    def _handle_power_events(self, now_hm: str) -> None:
        s = self.config.data
        shutdown_time = f"{s.get('shutdown_h', '17')}:{s.get('shutdown_m', '30')}"
        sleep_time = f"{s.get('sleep_h', '23')}:{s.get('sleep_m', '00')}"
        minute_key = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._power_lock:
            if s.get("enable_shutdown") and now_hm == shutdown_time and self._shutdown_done_minute != minute_key:
                self.log(self.profile.profile_id, "⏰ Den gio hen, may tinh se tat sau 60 giay")
                try:
                    import subprocess
                    subprocess.run(
                        ["shutdown", "/s", "/t", "60"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                    )
                except Exception as exc:
                    self.log(self.profile.profile_id, f"❌ Loi tat may: {exc}")
                self.__class__._shutdown_done_minute = minute_key

            if s.get("enable_sleep") and now_hm == sleep_time and self._sleep_done_minute != minute_key:
                self.log(self.profile.profile_id, "🌙 Den gio hen, may tinh se vao Sleep")
                time.sleep(2)
                try:
                    import subprocess
                    subprocess.run(
                        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                    )
                except Exception as exc:
                    self.log(self.profile.profile_id, f"❌ Loi sleep: {exc}")
                self.__class__._sleep_done_minute = minute_key

    def _run_once(self) -> RunStats:
        user, pwd = self.secrets.get_profile_credential(self.profile.profile_id)
        if not user or not pwd:
            msg = "❌ Chua co tai khoan/mat khau profile."
            self.log(self.profile.profile_id, msg)
            return RunStats(ok=False, message=msg)

        tnv_path = self.profile.profile_dir / "TNV.xlsx"
        cashiers = self.pipeline.read_cashiers(tnv_path)
        if not cashiers:
            msg = f"❌ Khong tim thay TNV.xlsx hoac file rong: {tnv_path}"
            self.log(self.profile.profile_id, msg)
            return RunStats(ok=False, message=msg)

        show_browser = bool(self.config.data["profiles"][self.profile.profile_id].get("show_browser", False))

        # Điện lực Thái Bình: gọi API KHONO trực tiếp
        self.log(self.profile.profile_id, f"-> Dang lay du lieu {len(cashiers)} TNV qua API KHONO")
        self.log(self.profile.profile_id, f"-> Dang rut {len(cashiers)} TNV qua API")

        stats, all_records = self.pipeline.run_api(
            self.profile, user, pwd, show_browser, cashiers,
            lambda m: self.log(self.profile.profile_id, m),
        )
        self.log(self.profile.profile_id, stats.message)

        if stats.failed > 0:
            self.log(
                self.profile.profile_id,
                "❌ Luot rut chua day du; giu nguyen du lieu GitHub hien tai va hen rut lai",
            )
            stats.ok = False
            stats.retry_needed = True
            return stats

        if not all_records:
            if stats.no_data == len(cashiers) and stats.failed == 0:
                self.log(self.profile.profile_id, "ℹ Tat ca TNV khong co du lieu; khong hen rut bu")
                stats.ok = True
                stats.retry_needed = False
                return stats

            self.log(self.profile.profile_id, "❌ Khong co du lieu de xu ly do qua trinh rut bi loi")
            stats.ok = False
            stats.retry_needed = True
            return stats

        records = self.pipeline._normalize_api_records(
            all_records, lambda m: self.log(self.profile.profile_id, m),
        )

        pushed = self.pipeline.push_to_github_direct(
            self.profile, records,
            lambda m: self.log(self.profile.profile_id, m),
        )
        stats.pushed = pushed
        stats.ok = stats.ok and pushed
        if not pushed:
            stats.retry_needed = True
        return stats


class AppGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION} - Điện Lực Thái Bình")
        self.root.geometry("1120x820")

        ensure_dirs()
        self.config_store = ConfigStore(SETTINGS_PATH)
        self.secret_store = SecretStore(SECRETS_PATH)
        self.pipeline = DebtPipeline(self.config_store, self.secret_store)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.workers: Dict[str, ProfileWorker] = {
            pid: ProfileWorker(profile, self.config_store, self.secret_store, self.pipeline, self._log)
            for pid, profile in PROFILE_DEFS.items()
        }

        self.tray_icon = None
        self.tray_image = None
        self.tray_ready = threading.Event()
        self.tray_mode_enabled = False
        self._build_ui()
        self._migrate_legacy_once()
        self._flush_logs()

        if self.config_store.data.get("auto_start_windows"):
            self._set_run_on_startup(True)

        self._init_tray_icon()

        if self.config_store.data.get("start_hidden"):
            self.root.after(200, lambda: self.hide_to_tray(notify=False))

        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        for pid, p_cfg in self.config_store.data.get("profiles", {}).items():
            if p_cfg.get("auto_run"):
                self.root.after(1000, lambda x=pid: self.start_worker(x))

    def _configure_theme(self) -> None:
        self.root.configure(bg="#f3f5f7")
        self.root.minsize(1080, 760)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background="#f3f5f7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 8), font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", padding=3)

    def _section(self, parent: tk.Widget, title: str) -> tk.LabelFrame:
        frame = tk.LabelFrame(
            parent,
            text=title,
            bg="#ffffff",
            fg="#1f2937",
            padx=14,
            pady=10,
            bd=1,
            relief="solid",
            font=("Segoe UI", 10, "bold"),
            labelanchor="nw",
        )
        return frame

    def _label(self, parent: tk.Widget, text: str, **kwargs) -> tk.Label:
        opts = {"bg": "#ffffff", "fg": "#374151", "font": ("Segoe UI", 9)}
        opts.update(kwargs)
        return tk.Label(parent, text=text, **opts)

    def _check(self, parent: tk.Widget, text: str, variable: tk.BooleanVar, **kwargs) -> tk.Checkbutton:
        opts = {
            "bg": "#ffffff",
            "fg": "#374151",
            "activebackground": "#ffffff",
            "font": ("Segoe UI", 9),
            "variable": variable,
            "text": text,
        }
        opts.update(kwargs)
        return tk.Checkbutton(parent, **opts)

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None], tone: str = "default") -> tk.Button:
        colors = {
            "default": ("#ffffff", "#1f2937", "#d1d5db"),
            "primary": ("#2563eb", "#ffffff", "#1d4ed8"),
            "success": ("#15803d", "#ffffff", "#166534"),
            "danger": ("#b91c1c", "#ffffff", "#991b1b"),
            "warning": ("#92400e", "#ffffff", "#78350f"),
            "muted": ("#4b5563", "#ffffff", "#374151"),
        }
        bg, fg, active_bg = colors.get(tone, colors["default"])
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=fg,
            bd=1,
            relief="solid",
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )

    def _build_ui(self) -> None:
        self._configure_theme()

        shell = tk.Frame(self.root, bg="#f3f5f7")
        shell.pack(fill="both", expand=True, padx=14, pady=12)

        header = tk.Frame(shell, bg="#f3f5f7")
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header,
            text="DuNoPro Control Center",
            bg="#f3f5f7",
            fg="#111827",
            font=("Segoe UI", 18, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text=f"v{APP_VERSION}",
            bg="#e5e7eb",
            fg="#374151",
            padx=10,
            pady=3,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=10)

        github = self._section(shell, "Cau hinh GitHub chung")
        github.pack(fill="x", pady=(0, 10))
        github.columnconfigure(1, weight=1)
        github.columnconfigure(3, weight=1)

        self._label(github, "GitHub user", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.ent_gh_user = tk.Entry(github, width=30, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_gh_user.insert(0, self.config_store.data.get("github_user", ""))
        self.ent_gh_user.grid(row=0, column=1, sticky="ew", padx=(0, 18), pady=4)

        self._label(github, "Branch", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
        self.ent_gh_branch = tk.Entry(github, width=16, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_gh_branch.insert(0, self.config_store.data.get("github_branch", "main"))
        self.ent_gh_branch.grid(row=0, column=3, sticky="ew", pady=4)

        self._label(github, "Token", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.ent_gh_token = tk.Entry(github, show="*", font=("Segoe UI", 10), relief="solid", bd=1)
        token_exists = bool(self.secret_store.get_github_token())
        self.ent_gh_token.insert(0, "********" if token_exists else "")
        self.ent_gh_token.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        self.var_show_gh_token = tk.BooleanVar(value=False)
        self._check(
            github,
            "Hien token",
            self.var_show_gh_token,
            command=lambda: self.ent_gh_token.config(show="" if self.var_show_gh_token.get() else "*"),
        ).grid(row=1, column=4, sticky="w", padx=(12, 0), pady=4)

        self.var_auto_start = tk.BooleanVar(value=self.config_store.data.get("auto_start_windows", False))
        self.var_start_hidden = tk.BooleanVar(value=self.config_store.data.get("start_hidden", False))
        self._check(github, "Khoi dong cung Windows", self.var_auto_start).grid(row=2, column=1, sticky="w", pady=(6, 0))
        self._check(github, "Mo an vao khay he thong", self.var_start_hidden).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        power = self._section(shell, "He thong")
        power.pack(fill="x", pady=(0, 10))
        self.var_shutdown = tk.BooleanVar(value=self.config_store.data.get("enable_shutdown", False))
        self.var_sleep = tk.BooleanVar(value=self.config_store.data.get("enable_sleep", False))
        self.var_sd_h = tk.StringVar(value=self.config_store.data.get("shutdown_h", "17"))
        self.var_sd_m = tk.StringVar(value=self.config_store.data.get("shutdown_m", "30"))
        self.var_sl_h = tk.StringVar(value=self.config_store.data.get("sleep_h", "23"))
        self.var_sl_m = tk.StringVar(value=self.config_store.data.get("sleep_m", "00"))
        hh = [f"{i:02d}" for i in range(24)]
        mm = [f"{i:02d}" for i in range(60)]

        self._check(power, "Hen gio tat may", self.var_shutdown).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(power, textvariable=self.var_sd_h, values=hh, width=4, state="readonly").grid(row=0, column=1)
        self._label(power, ":", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, padx=3)
        ttk.Combobox(power, textvariable=self.var_sd_m, values=mm, width=4, state="readonly").grid(row=0, column=3)

        self._check(power, "Hen gio Sleep", self.var_sleep).grid(row=0, column=4, sticky="w", padx=(28, 8))
        ttk.Combobox(power, textvariable=self.var_sl_h, values=hh, width=4, state="readonly").grid(row=0, column=5)
        self._label(power, ":", font=("Segoe UI", 10, "bold")).grid(row=0, column=6, padx=3)
        ttk.Combobox(power, textvariable=self.var_sl_m, values=mm, width=4, state="readonly").grid(row=0, column=7)

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True, pady=(0, 10))
        self.profile_widgets: Dict[str, dict] = {}

        for pid, profile in PROFILE_DEFS.items():
            tab = tk.Frame(self.tabs, bg="#f3f5f7")
            self.tabs.add(tab, text=f"{profile.display_name}  |  {profile.github_repo}")
            self.profile_widgets[pid] = self._build_profile_tab(tab, profile)

        control = tk.Frame(shell, bg="#f3f5f7")
        control.pack(fill="x", pady=(0, 10))
        self._button(control, "Luu tat ca cai dat", self.save_all, "primary").pack(side="left")
        self._button(control, "Bat tat ca", self.start_all, "success").pack(side="left", padx=6)
        self._button(control, "Dung tat ca", self.stop_all, "danger").pack(side="left")
        self._button(control, "Run now tat ca", self.run_now_all, "warning").pack(side="left", padx=6)
        self.btn_tray_toggle = self._button(control, "An xuong khay", self.hide_to_tray, "muted")
        self.btn_tray_toggle.pack(side="right")
        self.btn_quit = self._button(control, "Thoat app", self.confirm_quit, "danger")
        self.btn_quit.pack(side="right", padx=(0, 6))

        log_box = self._section(shell, "Nhat ky he thong")
        log_box.pack(fill="x")
        self.txt_log = scrolledtext.ScrolledText(
            log_box,
            height=10,
            font=("Consolas", 9),
            bg="#0f172a",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief="flat",
        )
        self.txt_log.pack(fill="both", expand=False)

    def _build_profile_tab(self, tab: tk.Frame, profile: ProfileDef) -> dict:
        cfg = self.config_store.data["profiles"][profile.profile_id]
        user, pwd = self.secret_store.get_profile_credential(profile.profile_id)

        content = tk.Frame(tab, bg="#f3f5f7")
        content.pack(fill="both", expand=True, padx=4, pady=10)
        content.columnconfigure(0, weight=1, uniform="profile")
        content.columnconfigure(1, weight=1, uniform="profile")

        identity = self._section(content, "Don vi dang quan ly")
        identity.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        identity.columnconfigure(1, weight=1)
        tk.Label(
            identity,
            text=profile.display_name,
            bg="#ffffff",
            fg="#111827",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w", columnspan=2)
        self._label(identity, f"Repo: {profile.github_repo}", fg="#1d4ed8", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(8, 2))
        self._label(identity, f"Web: {profile.web_app_url}", fg="#1d4ed8").grid(row=2, column=0, sticky="w")
        self._label(identity, f"Thu muc web: {get_profile_web_dir(profile)}", fg="#6b7280").grid(row=3, column=0, sticky="w", pady=(2, 0))

        status_var = tk.StringVar(value="Idle")
        status_badge = tk.Label(
            identity,
            textvariable=status_var,
            bg="#eef2ff",
            fg="#3730a3",
            padx=12,
            pady=5,
            font=("Segoe UI", 10, "bold"),
        )
        status_badge.grid(row=0, column=2, sticky="ne")

        auth = self._section(content, "Tai khoan KHONO rieng cua don vi")
        auth.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        auth.columnconfigure(1, weight=1)

        self._label(auth, "Tai khoan", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ent_user = tk.Entry(auth, font=("Segoe UI", 10), relief="solid", bd=1)
        ent_user.insert(0, user)
        ent_user.grid(row=0, column=1, sticky="ew", pady=5)

        self._label(auth, "Mat khau", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ent_pwd = tk.Entry(auth, show="*", font=("Segoe UI", 10), relief="solid", bd=1)
        ent_pwd.insert(0, pwd)
        ent_pwd.grid(row=1, column=1, sticky="ew", pady=5)
        var_show_pwd = tk.BooleanVar(value=False)
        self._check(
            auth,
            "Hien mat khau",
            var_show_pwd,
            command=lambda e=ent_pwd, v=var_show_pwd: e.config(show="" if v.get() else "*"),
        ).grid(row=1, column=2, sticky="w", padx=(10, 0))

        var_remember = tk.BooleanVar(value=cfg.get("remember", False))
        var_show_browser = tk.BooleanVar(value=cfg.get("show_browser", False))
        var_auto_run = tk.BooleanVar(value=cfg.get("auto_run", False))
        self._check(auth, "Luu tai khoan profile nay", var_remember).grid(row=2, column=1, sticky="w", pady=(10, 0))
        self._check(auth, "Hien trinh duyet khi rut du lieu", var_show_browser).grid(row=3, column=1, sticky="w")
        self._check(auth, "Tu chay khi mo app", var_auto_run).grid(row=4, column=1, sticky="w")

        sched = self._section(content, "Lich chay va tan suat")
        sched.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        sched.columnconfigure(8, weight=1)

        self._label(sched, "Lap moi", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ent_interval = tk.Entry(sched, width=8, font=("Segoe UI", 10), relief="solid", bd=1)
        ent_interval.insert(0, str(cfg.get("interval", 30)))
        ent_interval.grid(row=0, column=1, sticky="w", pady=5)
        self._label(sched, "phut trong cac khung gio dang bat").grid(row=0, column=2, sticky="w", padx=8)

        hour_values = [f"{i:02d}" for i in range(24)]
        minute_values = [f"{i:02d}" for i in range(60)]
        slot_vars = []
        slots = cfg.get("slots", json.loads(json.dumps(DEFAULT_SLOTS)))
        for idx in range(3):
            row = idx + 1
            slot = slots[idx] if idx < len(slots) else DEFAULT_SLOTS[idx]
            var_active = tk.BooleanVar(value=slot.get("active", False))
            var_sh = tk.StringVar(value=slot.get("sh", "00"))
            var_sm = tk.StringVar(value=slot.get("sm", "00"))
            var_eh = tk.StringVar(value=slot.get("eh", "00"))
            var_em = tk.StringVar(value=slot.get("em", "00"))

            self._check(sched, f"Khung {idx + 1}", var_active).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Combobox(sched, textvariable=var_sh, values=hour_values, width=4, state="readonly").grid(row=row, column=1, sticky="w")
            self._label(sched, ":", font=("Segoe UI", 10, "bold")).grid(row=row, column=2, padx=3)
            ttk.Combobox(sched, textvariable=var_sm, values=minute_values, width=4, state="readonly").grid(row=row, column=3, sticky="w")
            self._label(sched, "den").grid(row=row, column=4, sticky="w", padx=8)
            ttk.Combobox(sched, textvariable=var_eh, values=hour_values, width=4, state="readonly").grid(row=row, column=5, sticky="w")
            self._label(sched, ":", font=("Segoe UI", 10, "bold")).grid(row=row, column=6, padx=3)
            ttk.Combobox(sched, textvariable=var_em, values=minute_values, width=4, state="readonly").grid(row=row, column=7, sticky="w")
            slot_vars.append((var_active, var_sh, var_sm, var_eh, var_em))

        actions = self._section(content, "Thao tac cho don vi hien tai")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        left_actions = tk.Frame(actions, bg="#ffffff")
        left_actions.pack(side="left")
        self._button(left_actions, "Luu don vi", lambda x=profile.profile_id: self.save_profile(x), "primary").pack(side="left")
        self._button(left_actions, "Bat chay", lambda x=profile.profile_id: self.start_worker(x), "success").pack(side="left", padx=6)
        self._button(left_actions, "Dung", lambda x=profile.profile_id: self.stop_worker(x), "danger").pack(side="left")
        self._button(left_actions, "Run now", lambda x=profile.profile_id: self.run_now(x), "warning").pack(side="left", padx=6)

        right_actions = tk.Frame(actions, bg="#ffffff")
        right_actions.pack(side="right")
        self._button(right_actions, "Test login", lambda x=profile.profile_id: self.test_login(x), "default").pack(side="left")
        self._button(right_actions, "Dong bo users.json (thu cong)", lambda x=profile.profile_id: self.sync_web_profile(x), "muted").pack(side="left", padx=(6, 0))

        return {
            "ent_user": ent_user,
            "ent_pwd": ent_pwd,
            "var_remember": var_remember,
            "var_show_browser": var_show_browser,
            "var_auto_run": var_auto_run,
            "ent_interval": ent_interval,
            "slot_vars": slot_vars,
            "status_var": status_var,
        }

    def _migrate_legacy_once(self) -> None:
        pass

    def _collect_profile_settings(self, profile_id: str) -> dict:
        w = self.profile_widgets[profile_id]
        try:
            interval = int(float(w["ent_interval"].get().strip()))
            if interval <= 0:
                raise ValueError
        except Exception:
            raise ValueError("Tan suat phai la so phut > 0")

        slots = []
        for var_active, var_sh, var_sm, var_eh, var_em in w["slot_vars"]:
            slots.append(
                {
                    "active": bool(var_active.get()),
                    "sh": var_sh.get(),
                    "sm": var_sm.get(),
                    "eh": var_eh.get(),
                    "em": var_em.get(),
                }
            )

        settings = {
            "interval": interval,
            "slots": slots,
            "show_browser": bool(w["var_show_browser"].get()),
            "remember": bool(w["var_remember"].get()),
            "auto_run": bool(w["var_auto_run"].get()),
        }
        return settings

    def save_profile(self, profile_id: str) -> None:
        try:
            p_settings = self._collect_profile_settings(profile_id)
        except ValueError as exc:
            messagebox.showerror("Loi", str(exc))
            return

        self.config_store.data["profiles"][profile_id] = p_settings

        w = self.profile_widgets[profile_id]
        user = w["ent_user"].get().strip()
        pwd = w["ent_pwd"].get().strip()
        remember = p_settings["remember"]

        if remember:
            if not user or not pwd:
                messagebox.showerror("Loi", "Da chon luu tai khoan nhung thieu user/password")
                return
            self.secret_store.set_profile_credential(profile_id, user, pwd)
        else:
            self.secret_store.clear_profile_credential(profile_id)

        self.config_store.save()
        self._log(profile_id, "✅ Da luu profile")

    def save_all(self) -> None:
        for pid in PROFILE_DEFS:
            self.save_profile(pid)
        self._save_global_settings_only()

        self._set_run_on_startup(self.var_auto_start.get())
        self._log("system", "✅ Da luu tat ca cai dat")

    def _save_global_settings_only(self) -> None:
        token = self.ent_gh_token.get().strip()
        if token and token != "********":
            self.secret_store.set_github_token(token)
            self.ent_gh_token.delete(0, tk.END)
            self.ent_gh_token.insert(0, "********")

        self.config_store.data["github_user"] = self.ent_gh_user.get().strip()
        self.config_store.data["github_branch"] = self.ent_gh_branch.get().strip() or "main"
        self.config_store.data["auto_start_windows"] = bool(self.var_auto_start.get())
        self.config_store.data["start_hidden"] = bool(self.var_start_hidden.get())
        self.config_store.data["enable_shutdown"] = bool(self.var_shutdown.get())
        self.config_store.data["shutdown_h"] = self.var_sd_h.get()
        self.config_store.data["shutdown_m"] = self.var_sd_m.get()
        self.config_store.data["enable_sleep"] = bool(self.var_sleep.get())
        self.config_store.data["sleep_h"] = self.var_sl_h.get()
        self.config_store.data["sleep_m"] = self.var_sl_m.get()
        self.config_store.save()

    def _set_run_on_startup(self, enable: bool) -> None:
        if not winreg:
            return
        app_key = f"{APP_NAME}_AutoStart"
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_ALL_ACCESS,
            )
            if enable:
                script = str(Path(__file__).resolve())
                pyw = Path(os.sys.executable).with_name("pythonw.exe")
                exe = str(pyw) if pyw.exists() else os.sys.executable
                cmd = f'"{exe}" "{script}"'
                winreg.SetValueEx(key, app_key, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, app_key)
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception as exc:
            self._log("system", f"❌ Loi startup Windows: {exc}")

    def start_worker(self, profile_id: str) -> None:
        self._save_global_settings_only()
        self.save_profile(profile_id)
        self.workers[profile_id].start()
        self.profile_widgets[profile_id]["status_var"].set("Running")

    def stop_worker(self, profile_id: str) -> None:
        self.workers[profile_id].stop()
        self.profile_widgets[profile_id]["status_var"].set("Stopped")

    def run_now(self, profile_id: str) -> None:
        self._save_global_settings_only()
        self.save_profile(profile_id)
        self.workers[profile_id].trigger_now()
        self.workers[profile_id].start()
        self.profile_widgets[profile_id]["status_var"].set("Running (Run now)")
        self._log(profile_id, "⚡ Da gui lenh Run now")

    def run_now_all(self) -> None:
        self._save_global_settings_only()
        for pid in PROFILE_DEFS:
            self.save_profile(pid)
            self.workers[pid].trigger_now()
            self.workers[pid].start()
            self.profile_widgets[pid]["status_var"].set("Running (Run now)")
        self._log("system", "⚡ Da gui lenh Run now tat ca")

    def test_login(self, profile_id: str) -> None:
        self._save_global_settings_only()
        self.save_profile(profile_id)

        def do_test() -> None:
            profile = PROFILE_DEFS[profile_id]
            user, pwd = self.secret_store.get_profile_credential(profile_id)
            if not user or not pwd:
                self._log(profile_id, "❌ Chua co credential de test")
                return
            cashiers = self.pipeline.read_cashiers(profile.profile_dir / "TNV.xlsx")
            if not cashiers:
                self._log(profile_id, "❌ Khong co TNV.xlsx de test")
                return
            one = cashiers[:1]
            self._log(profile_id, "🔍 Bat dau Test Login + pull 1 TNV")
            stats = self.pipeline.run_playwright(profile, user, pwd, True, one, lambda m: self._log(profile_id, m))
            self._log(profile_id, f"Test xong: {stats.message}")

        threading.Thread(target=do_test, daemon=True).start()

    def sync_web_profile(self, profile_id: str) -> None:
        self._save_global_settings_only()

        def do_sync() -> None:
            profile = PROFILE_DEFS[profile_id]
            self._log(profile_id, "Bat dau dong bo users.json theo thao tac thu cong")
            ok = self.pipeline.sync_web_assets(profile, lambda m: self._log(profile_id, m))
            if ok:
                self._log(profile_id, "Dong bo users.json thanh cong")
            else:
                self._log(profile_id, "Dong bo users.json co loi, xem dong log phia tren")

        threading.Thread(target=do_sync, daemon=True).start()

    def start_all(self) -> None:
        self._save_global_settings_only()
        for pid in PROFILE_DEFS:
            self.start_worker(pid)

    def stop_all(self) -> None:
        for pid in PROFILE_DEFS:
            self.stop_worker(pid)

    def _log(self, profile_id: str, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] [{profile_id}] {message}"
        LOGGER.info(line)
        self.log_queue.put(line)

    def _flush_logs(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.txt_log.insert(tk.END, line + "\n")
                self.txt_log.see(tk.END)
        except queue.Empty:
            pass
        self._poll_external_show_request()
        self.root.after(250, self._flush_logs)

    def _poll_external_show_request(self) -> None:
        if SHOW_WINDOW_FLAG_PATH.exists():
            try:
                SHOW_WINDOW_FLAG_PATH.unlink()
            except Exception:
                pass
            self._restore_window()
            self._log("system", "🟢 Nhan lenh mo lai tu file bat hoac tien trinh khac")

    def _update_tray_toggle_button(self) -> None:
        if not hasattr(self, "btn_tray_toggle"):
            return
        if self.tray_mode_enabled:
            self.btn_tray_toggle.config(text="Mo lai cua so")
        else:
            self.btn_tray_toggle.config(text="An xuong khay")

    def toggle_tray_mode(self) -> None:
        if self.tray_mode_enabled:
            self._restore_window()
        else:
            self.hide_to_tray()

    def disable_tray_mode(self) -> None:
        self._restore_window()

    @staticmethod
    def _create_tray_image() -> Optional["Image.Image"]:
        if not Image or not ImageDraw:
            return None
        try:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(21, 101, 192), outline=(255, 255, 255), width=3)
            draw.text((16, 20), "DN", fill=(255, 255, 255))
            return img
        except Exception:
            return None

    def _init_tray_icon(self) -> None:
        if not pystray or not Image or not ImageDraw:
            self._log("system", "⚠ Chua cai pystray hoac PIL. Tinh nang khay he thong tam tat.")
            return

        try:
            self.tray_image = self._create_tray_image()
            if self.tray_image is None:
                return

            separator = getattr(pystray.Menu, "SEPARATOR", None)
            menu_items = [
                pystray.MenuItem("Mo cua so DuNoPro", self._tray_show, default=True),
                pystray.MenuItem("An xuong khay", self._tray_hide),
            ]
            if separator is not None:
                menu_items.append(separator)
            menu_items.extend([
                pystray.MenuItem("Bat tat ca", self._tray_start_all),
                pystray.MenuItem("Dung tat ca", self._tray_stop_all),
            ])
            if separator is not None:
                menu_items.append(separator)
            menu_items.append(pystray.MenuItem("Thoat ung dung", self._tray_quit))

            menu = pystray.Menu(*menu_items)
            self.tray_icon = pystray.Icon(
                APP_NAME,
                self.tray_image,
                f"{APP_NAME} v{APP_VERSION} - Dang chay ngam",
                menu,
            )
            self.tray_ready.clear()

            def setup(icon: pystray.Icon) -> None:
                icon.visible = True
                self.tray_ready.set()

            if hasattr(self.tray_icon, "run_detached"):
                self.tray_icon.run_detached(setup=setup)
            else:
                threading.Thread(target=lambda: self.tray_icon.run(setup=setup), daemon=True).start()
        except Exception as exc:
            self._log("system", f"❌ Loi khoi tao tray icon: {exc}")
            self.tray_ready.clear()
            self.tray_icon = None
            self.tray_image = None

    def hide_to_tray(self, notify: bool = True) -> None:
        if not pystray or not Image or not ImageDraw:
            self._log("system", "⚠ Chua cai pystray. Khong the an vao khay he thong.")
            messagebox.showerror(
                "Thieu pystray",
                "May dang thieu pystray. Cai pystray truoc khi dung tinh nang an vao khay he thong.",
            )
            return

        if self.tray_icon is None:
            self._init_tray_icon()

        if self.tray_icon is None:
            self._log("system", "❌ Khong the an cua so vi khong khoi tao duoc icon khay he thong.")
            messagebox.showerror(
                "Loi khay he thong",
                "Khong khoi tao duoc icon khay he thong. Cua so se duoc giu nguyen de tranh mat giao dien.",
            )
            return

        try:
            self.tray_icon.visible = True
        except Exception as exc:
            self._log("system", f"❌ Khong the hien thi icon khay: {exc}")
            messagebox.showerror(
                "Loi khay he thong",
                f"Khong the hien thi icon khay: {exc}\nCua so se duoc giu nguyen.",
            )
            return

        self.root.withdraw()
        self.tray_mode_enabled = True
        self._update_tray_toggle_button()
        self._log("system", "🔽 Da an vao khay he thong (dang chay ngam)")

        if notify and self.tray_icon and hasattr(self.tray_icon, "notify"):
            try:
                self.tray_icon.notify(
                    "DuNoPro dang chay ngam trong khay he thong.\nClick dup vao bieu tuong de mo lai.",
                    f"{APP_NAME} v{APP_VERSION}",
                )
            except Exception:
                pass

    def _restore_window(self) -> None:
        self.tray_mode_enabled = False
        self._update_tray_toggle_button()
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(150, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        self.root.focus_force()
        self._log("system", "🟢 Da mo lai cua so ung dung")

    def _tray_show(self, icon=None, item=None) -> None:
        self.root.after(0, self._restore_window)

    def _tray_hide(self, icon=None, item=None) -> None:
        self.root.after(0, lambda: self.hide_to_tray(notify=False))

    def _tray_start_all(self, icon=None, item=None) -> None:
        self.root.after(0, self.start_all)

    def _tray_stop_all(self, icon=None, item=None) -> None:
        self.root.after(0, self.stop_all)

    def _tray_quit(self, icon=None, item=None) -> None:
        self.root.after(0, self.quit_app)

    def on_window_close(self) -> None:
        # Neu tray san sang hoac co the dung pystray, an xuong khay de khong ngat tien trinh tu dong
        if pystray and Image:
            self.hide_to_tray(notify=True)
        else:
            self.confirm_quit()

    def confirm_quit(self) -> None:
        if messagebox.askyesno(
            "Thoat DuNoPro",
            "Sep co chac chan muon thoat DuNoPro khong?\n(Tat ca cac tien trinh chay tu dong se dung)",
        ):
            self.quit_app()

    def on_close(self) -> None:
        self.quit_app()

    def quit_app(self) -> None:
        if getattr(self, "_is_quitting", False):
            return
        self._is_quitting = True

        self.stop_all()
        self.save_all()
        self._wait_for_workers_and_destroy(waited_ms=0)

    def _wait_for_workers_and_destroy(self, waited_ms: int = 0) -> None:
        any_alive = any(
            w.state.thread is not None and w.state.thread.is_alive()
            for w in self.workers.values()
        )
        if not any_alive:
            self._cleanup_and_destroy()
            return

        if waited_ms >= 3000:
            force = messagebox.askyesno(
                "Tien trinh dang chay",
                "Tien trinh rut/gom du lieu van dang hoat dong.\nSep co muon buoc thoat ngay khong?\n(Chon 'No' de tiep tuc doi tien trinh hoan tat an toan)",
            )
            if force:
                self._cleanup_and_destroy()
            else:
                self.root.after(500, lambda: self._wait_for_workers_and_destroy(0))
            return

        self.root.after(200, lambda: self._wait_for_workers_and_destroy(waited_ms + 200))

    def _cleanup_and_destroy(self) -> None:
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.tray_image = None
        self.tray_mode_enabled = False
        try:
            self.root.destroy()
        except Exception:
            pass


def main() -> None:
    hide_console_window()
    ensure_dirs()
    mutex_or_flag = check_single_instance()
    if mutex_or_flag is False:
        # Da co mot phien khac dang chay, da gui tin hieu hien cua so
        return

    try:
        root = tk.Tk()
        app_gui = AppGUI(root)
        app_gui._instance_mutex = mutex_or_flag
        root.mainloop()
    except Exception as exc:
        LOGGER.exception("Loi khoi dong hoac thuc thi ung dung: %s", exc)
        try:
            err_root = tk.Tk()
            err_root.withdraw()
            messagebox.showerror(
                "Loi DuNoPro",
                f"Da xay ra loi khi chay ung dung:\n\n{exc}\n\nXem chi tiet tai:\nruntime/logs/runner.log",
            )
            err_root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
