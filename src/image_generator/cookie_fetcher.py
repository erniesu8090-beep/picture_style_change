#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Web Cookie 自動擷取與驗證工具 (免手動按 F12)
運作原理：
1. 優先檢查既有已登入的瀏覽器設定檔 (Profile)，以無頭模式開啟 gemini.google.com 讀取最新 __Secure-1PSID 與 __Secure-1PSIDTS。
2. 若尚未登入或 Cookie 已過期，自動彈出獨立純淨的 Edge/Chrome 視窗供使用者登入 Google，偵測到 Cookie 後自動寫入 cookies.json 並關閉視窗。
3. 寫入後自動呼叫 GeminiWebApiEngine 進行網頁端握手驗證。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from curl_cffi.requests import WebSocket

from .config import get_cookie_file_path, save_updated_cookies, load_gemini_web_cookies
from .webapi_engine import GeminiWebApiEngine

# 設定 Windows 終端 UTF-8 輸出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GEMINI_APP_URL = "https://gemini.google.com/app"


def find_browser_executable() -> Optional[Path]:
    """尋找系統安裝的 Microsoft Edge 或 Google Chrome 執行檔（不硬編碼磁碟代號）"""
    prog_x86 = Path(os.environ.get("PROGRAMFILES(X86)", ""))
    prog = Path(os.environ.get("PROGRAMFILES", ""))
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))

    candidates = [
        prog_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        prog / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        local_app / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        prog / "Google" / "Chrome" / "Application" / "chrome.exe",
        prog_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
        local_app / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p

    for cmd in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(cmd)
        if found:
            return Path(found)
    return None


def get_profile_dir() -> Path:
    """取得持久化瀏覽器 Profile 目錄（若同工作區已有登入過的 Profile 則優先共用）"""
    local_profile = PROJECT_ROOT / "data" / "browser_profile"
    if local_profile.exists():
        return local_profile

    # 檢查同工作區是否已有持久化的 Google 登入 Profile
    workspace_root = PROJECT_ROOT.parent.parent.parent
    shared_flow_profile = workspace_root / "GOOGLE FLOW" / "data" / "browser_profile"
    if shared_flow_profile.exists():
        return shared_flow_profile

    local_profile.mkdir(parents=True, exist_ok=True)
    return local_profile


def clean_profile_locks(profile_dir: Path) -> None:
    """清理殘留的 SingletonLock 避免啟動衝突"""
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_file = profile_dir / lock_name
        try:
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)
        except Exception:
            pass


def find_free_debug_port(start_port: int = 9235) -> int:
    """尋找可用的本機偵錯連接埠"""
    port = start_port
    while port < start_port + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return start_port


def fetch_cookies_via_cdp(port: int) -> Tuple[Optional[str], Optional[str]]:
    """透過 Chrome DevTools Protocol 讀取瀏覽器內已解密的 Google Cookies"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None

    ws_url = None
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            ws_url = t["webSocketDebuggerUrl"]
            if "gemini.google.com" in t.get("url", ""):
                break

    if not ws_url:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as resp:
                ver_info = json.loads(resp.read().decode("utf-8"))
                ws_url = ver_info.get("webSocketDebuggerUrl")
        except Exception:
            return None, None

    if not ws_url:
        return None, None

    ws = WebSocket()
    try:
        ws.connect(ws_url)
        ws.send_json({"id": 1, "method": "Network.getAllCookies"})
        data = ws.recv_json()
        cookies = data.get("result", {}).get("cookies", [])

        psid = None
        psidts = None
        for c in cookies:
            domain = c.get("domain", "")
            if "google.com" in domain:
                if c.get("name") == "__Secure-1PSID" and c.get("value"):
                    psid = c["value"]
                elif c.get("name") == "__Secure-1PSIDTS" and c.get("value"):
                    psidts = c["value"]
        return psid, psidts
    except Exception:
        return None, None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def extract_cookies_from_browser(headless: bool = True, wait_seconds: int = 12) -> Tuple[Optional[str], Optional[str]]:
    """啟動瀏覽器前往 gemini.google.com 並透過 CDP 提取 __Secure-1PSID 與 __Secure-1PSIDTS"""
    browser_exe = find_browser_executable()
    if not browser_exe:
        return None, None

    profile_dir = get_profile_dir()
    clean_profile_locks(profile_dir)
    port = find_free_debug_port()

    cmd = [
        str(browser_exe),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=http://127.0.0.1:{port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        cmd.append("--headless=new")
    cmd.append(GEMINI_APP_URL)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    psid, psidts = None, None
    try:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(2.0)
            psid, psidts = fetch_cookies_via_cdp(port)
            if psid and psidts:
                # 稍微多等 2 秒讓頁面完成最新 Token 輪換
                time.sleep(2.0)
                new_psid, new_psidts = fetch_cookies_via_cdp(port)
                if new_psid and new_psidts:
                    psid, psidts = new_psid, new_psidts
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return psid, psidts


async def verify_cookies_handshake() -> Tuple[bool, str]:
    """實際與 Google Gemini 網頁端進行握手連線驗證"""
    engine = GeminiWebApiEngine()
    try:
        await engine._get_client()
        return True, "成功與 Google Gemini 網頁端建立連線並通過驗證！"
    except Exception as e:
        return False, str(e)
    finally:
        await engine.close()


def interactive_setup(auto_only: bool = False) -> int:
    """執行 Cookie 自動擷取、儲存與握手驗證流程"""
    print("=" * 65)
    print("🟣 Gemini Web (第三引擎) Cookie 自動擷取與設定工具")
    print("=" * 65)

    print("\n[步驟 1/3] 正在嘗試從本機已登入的瀏覽器環境自動提取 Cookie...")
    psid, psidts = extract_cookies_from_browser(headless=True, wait_seconds=15)

    if psid and psidts:
        print(f"  ✓ 成功自動提取 Cookie！")
        print(f"    __Secure-1PSID   : {psid[:14]}...{psid[-6:]}")
        print(f"    __Secure-1PSIDTS : {psidts[:14]}...{psidts[-6:]}")
        save_updated_cookies(psid, psidts)
        print(f"\n[步驟 2/3] 已自動寫入設定檔：{get_cookie_file_path()}")

        print("\n[步驟 3/3] 正在與 Google Gemini 網頁端進行連線握手驗證...")
        ok, msg = asyncio.run(verify_cookies_handshake())
        if ok:
            print(f"  ✓ {msg}")
            print("\n🎉 設定全部完成！現在您可以在工作台選用「🟣 WEB 端」直接生圖了！")
            return 0
        else:
            print(f"  ⚠ 自動提取的 Cookie 握手未通過：{msg}")

    if auto_only:
        print("  ✗ 無頭自動提取未能完成握手驗證，需互動式登入。")
        return 1

    print("\n-----------------------------------------------------------------")
    print("正在為您彈出瀏覽器視窗前往 https://gemini.google.com/app ...")
    print("請在開啟的瀏覽器中確認已登入您的 Google 帳號（若已登入會自動偵測關閉）！")
    print("-----------------------------------------------------------------")

    psid, psidts = extract_cookies_from_browser(headless=False, wait_seconds=120)
    if psid and psidts:
        save_updated_cookies(psid, psidts)
        print(f"\n  ✓ 成功捕獲最新 Cookie 並寫入：{get_cookie_file_path()}")
        ok, msg = asyncio.run(verify_cookies_handshake())
        if ok:
            print(f"  ✓ {msg}")
            print("\n🎉 設定全部完成！")
            return 0
        else:
            print(f"  ⚠ 驗證訊息：{msg}")
            return 1

    print("\n[備用手動輸入模式]")
    print("若瀏覽器未能自動捕獲，您也可以直接貼上數值：")
    manual_psid = input("請貼上 __Secure-1PSID (直接按 Enter 略過): ").strip()
    manual_psidts = input("請貼上 __Secure-1PSIDTS (直接按 Enter 略過): ").strip()
    if manual_psid and manual_psidts:
        save_updated_cookies(manual_psid, manual_psidts)
        ok, msg = asyncio.run(verify_cookies_handshake())
        print(f"驗證結果：{msg}")
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    is_auto = "--auto" in sys.argv
    sys.exit(interactive_setup(auto_only=is_auto))
