"""Gemini Web 引擎設定與 Cookie 載入器"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Tuple, Optional
from dotenv import load_dotenv

# 優先載入根目錄 .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_safe_ca_bundle() -> str:
    """
    取得不受中文路徑影響的純 ASCII CA 憑證路徑。
    解決 Windows 下 curl_cffi / libcurl 在包含中文字元路徑（如「實驗區」、「未完成專案」）時觸發 curl (77) 的問題。
    """
    try:
        import certifi

        raw_ca = certifi.where()
        try:
            raw_ca.encode("ascii")
            return raw_ca
        except UnicodeEncodeError:
            pass

        safe_ca = Path(tempfile.gettempdir()) / "curl_cacert.pem"
        if not safe_ca.exists() or safe_ca.stat().st_size != Path(raw_ca).stat().st_size:
            shutil.copyfile(raw_ca, str(safe_ca))

        os.environ["SSL_CERT_FILE"] = str(safe_ca)
        os.environ["CURL_CA_BUNDLE"] = str(safe_ca)
        return str(safe_ca)
    except Exception:
        return ""


# 自動初始化安全 CA 憑證路徑
SAFE_CA_BUNDLE = get_safe_ca_bundle()


def get_cookie_file_path() -> Path:
    """取得預設 cookies.json 存放路徑"""
    return PROJECT_ROOT / "cookies.json"


def load_gemini_web_cookies(
    cookie_file: str | Path | None = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    載入 Gemini 網頁版 __Secure-1PSID 與 __Secure-1PSIDTS。
    
    回傳值:
        (psid, psidts, error_message)
        若成功載入，error_message 為 None；若未設定或無效，回傳清晰繁中指引。
    """
    cookie_path = Path(cookie_file) if cookie_file else get_cookie_file_path()

    psid = os.getenv("GEMINI_1PSID")
    psidts = os.getenv("GEMINI_1PSIDTS")

    # 若環境變數未齊全，嘗試由 cookies.json 讀取
    if (not psid or not psidts) and cookie_path.exists():
        try:
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
            psid = psid or data.get("__Secure-1PSID")
            psidts = psidts or data.get("__Secure-1PSIDTS")
        except Exception as e:
            return None, None, f"讀取 {cookie_path.name} 失敗：{e}。請檢查 JSON 語法是否正確。"

    # 檢查是否為尚未填寫的預設範本文字
    placeholder_keywords = ("請在此貼上", "your_", "YOUR_", "placeholder", "<your-")
    def is_placeholder(val: Optional[str]) -> bool:
        if not val or not val.strip():
            return True
        return any(k in val for k in placeholder_keywords)

    if is_placeholder(psid) or is_placeholder(psidts):
        guide_msg = (
            "尚未設定有效的 Gemini 網頁版 Cookie 憑證！\n\n"
            "【快速設定步驟】：\n"
            "1. 開啟瀏覽器登入 https://gemini.google.com/ （確認帳號能正常對話與生圖）。\n"
            "2. 按鍵盤 F12 開啟開發者工具 -> 點選「Application」(應用程式) 頁籤。\n"
            "3. 展開左側「Cookies」 -> 點擊「https://gemini.google.com」。\n"
            "4. 複製 __Secure-1PSID 與 __Secure-1PSIDTS 兩個項目的 Value 值。\n"
            f"5. 將專案根目錄的 cookies.json.example 複製為 cookies.json 並貼上真實數值存檔即可。"
        )
        return None, None, guide_msg

    return psid.strip(), psidts.strip(), None


def save_updated_cookies(psid: str, psidts: str, cookie_file: str | Path | None = None) -> bool:
    """自動將背景刷新後的 Cookie 回寫持久化至 cookies.json"""
    cookie_path = Path(cookie_file) if cookie_file else get_cookie_file_path()
    try:
        data = {
            "_updated_at": "auto_refreshed_by_gemini_webapi",
            "__Secure-1PSID": psid,
            "__Secure-1PSIDTS": psidts,
        }
        cookie_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[Gemini Web Config] Cookie 回寫失敗：{e}")
        return False
