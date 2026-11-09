#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Web Cookie 自動擷取與驗證工具 (免手動按 F12)
運作原理：
1. 優先檢查既有已登入的獨立瀏覽器 Profile 目錄，以無頭模式開啟 gemini.google.com 讀取最新 __Secure-1PSID 與 __Secure-1PSIDTS。
2. 若尚未登入或 Cookie 已過期，自動彈出獨立純淨的 Chrome 視窗供使用者登入 Google，
   透過 Chrome DevTools Protocol (CDP，本機直連繞過代理) 即時偵測 Cookie，偵測齊全後自動寫入 cookies.json 並關閉視窗。
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
    """
    尋找系統安裝的 Google Chrome 或 Microsoft Edge 執行檔。
    優先使用 Google Chrome，因在許多公司/企業 Windows 原則下 Edge 會被強制停用 DevTools 遠端偵錯。
    """
    prog_x86 = Path(os.environ.get("PROGRAMFILES(X86)", ""))
    prog = Path(os.environ.get("PROGRAMFILES", ""))
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))

    candidates = [
        prog / "Google" / "Chrome" / "Application" / "chrome.exe",
        prog_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
        local_app / "Google" / "Chrome" / "Application" / "chrome.exe",
        prog_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        prog / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        local_app / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p

    for cmd in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(cmd)
        if found:
            return Path(found)
    return None


def get_profile_dir() -> Path:
    """取得持久化瀏覽器 Profile 目錄"""
    local_profile = PROJECT_ROOT / "data" / "browser_profile"
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


def fetch_cookies_via_cdp(port: int) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    透過 Chrome DevTools Protocol 讀取瀏覽器內已解密的 Google Cookies。
    重要：本機 CDP 連線必須完全繞過代理 (ProxyHandler({}))，避免被本機 8899 等代理工具攔截回傳 405。
    回傳 (psid, psidts, page_ws_url)
    """
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)

    try:
        with opener.open(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None, None

    page_ws = None
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            if "gemini.google.com" in t.get("url", ""):
                page_ws = t["webSocketDebuggerUrl"]
                break
            elif not page_ws:
                page_ws = t["webSocketDebuggerUrl"]

    if not page_ws:
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=3) as resp:
                ver_info = json.loads(resp.read().decode("utf-8"))
                page_ws = ver_info.get("webSocketDebuggerUrl")
        except Exception:
            return None, None, None

    if not page_ws:
        return None, None, None

    # 暫時清除環境變數代理，確保 curl_cffi WebSocket 直連本地 127.0.0.1
    old_env = {}
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        if k in os.environ:
            old_env[k] = os.environ.pop(k)

    ws = WebSocket()
    try:
        ws.connect(page_ws)
        cookies = []
        # 優先嘗試 Storage.getCookies
        try:
            ws.send_json({"id": 1, "method": "Storage.getCookies"})
            data = ws.recv_json()
            cookies = data.get("result", {}).get("cookies", [])
        except Exception:
            pass

        # 備援嘗試 Network.getAllCookies
        if not cookies:
            try:
                ws.send_json({"id": 2, "method": "Network.getAllCookies"})
                data = ws.recv_json()
                cookies = data.get("result", {}).get("cookies", [])
            except Exception:
                pass

        psid, psidts = None, None
        for c in cookies:
            domain = c.get("domain", "")
            name = c.get("name", "")
            val = c.get("value", "")
            if "google" in domain and val:
                if name == "__Secure-1PSID":
                    psid = val
                elif name == "__Secure-1PSIDTS":
                    psidts = val

        return psid, psidts, page_ws
    except Exception:
        return None, None, None
    finally:
        try:
            ws.close()
        except Exception:
            pass
        for k, v in old_env.items():
            os.environ[k] = v


def trigger_page_refresh(ws_url: str) -> None:
    """透過 CDP 促使頁面重新整理或導向，誘發 Google 伺服器頒發 __Secure-1PSIDTS 安全權杖"""
    old_env = {}
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        if k in os.environ:
            old_env[k] = os.environ.pop(k)
    ws = WebSocket()
    try:
        ws.connect(ws_url)
        ws.send_json({"id": 99, "method": "Page.navigate", "params": {"url": GEMINI_APP_URL}})
        time.sleep(1.0)
    except Exception:
        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass
        for k, v in old_env.items():
            os.environ[k] = v


def extract_cookies_from_browser(headless: bool = True, wait_seconds: int = 12) -> Tuple[Optional[str], Optional[str]]:
    """啟動瀏覽器前往 gemini.google.com 並透過直連 CDP 即時提取最新 Cookie"""
    browser_exe = find_browser_executable()
    if not browser_exe:
        print("  ✗ 未偵測到相容之 Google Chrome 或 Edge 瀏覽器。")
        return None, None

    profile_dir = get_profile_dir()
    clean_profile_locks(profile_dir)
    port = find_free_debug_port()

    cmd = [
        str(browser_exe),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
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
        last_log_time = 0.0
        refreshed_once = False

        while time.time() < deadline:
            time.sleep(1.5)
            curr_psid, curr_psidts, page_ws = fetch_cookies_via_cdp(port)

            if curr_psid and curr_psidts:
                # 雙權杖齊全，稍微再等 1 秒確保寫入穩定
                time.sleep(1.0)
                final_psid, final_psidts, _ = fetch_cookies_via_cdp(port)
                psid = final_psid or curr_psid
                psidts = final_psidts or curr_psidts
                print("\n  ✓ 成功偵測到完整 Google 登入 Cookie 與安全憑據！", flush=True)
                break

            # 若偵測到已有初步登入 Cookie 但尚未拿到 PSIDTS，主動顯示進度並觸發頁面刷新
            if curr_psid and not curr_psidts:
                now = time.time()
                if now - last_log_time > 3.0:
                    print("  ⏳ 已偵測到 Google 登入帳號，正在等待安全驗證權杖 (__Secure-1PSIDTS) 頒發中...", flush=True)
                    last_log_time = now
                    if not refreshed_once and page_ws:
                        refreshed_once = True
                        trigger_page_refresh(page_ws)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
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
        return True, "成功與 Google Gemini 網頁端建立連線並通過身分驗證！"
    except Exception as e:
        return False, str(e)
    finally:
        await engine.close()


def interactive_setup(auto_only: bool = False) -> int:
    """執行 Cookie 自動擷取、儲存與握手驗證流程"""
    print("=" * 65)
    print("🟣 Gemini Web (第三引擎) Cookie 自動擷取與設定工具")
    print("=" * 65)

    print("\n[步驟 1/3] 正在嘗試從本機已儲存的 Profile 自動提取 Cookie...")
    psid, psidts = extract_cookies_from_browser(headless=True, wait_seconds=8)

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

    print("\n" + "-" * 65)
    print("正在開啟獨立純淨瀏覽器視窗前往 https://gemini.google.com/app ...")
    print("👉 請在彈出的瀏覽器視窗中點擊「登入」，登入您的 Google 帳號。")
    print("（系統在背景即時直連偵測，一旦登入完成將自動儲存並關閉視窗）")
    print("-" * 65)

    psid, psidts = extract_cookies_from_browser(headless=False, wait_seconds=120)
    if psid and psidts:
        save_updated_cookies(psid, psidts)
        print(f"\n[步驟 2/3] 已成功捕獲最新 Cookie 並寫入：{get_cookie_file_path()}")
        print("\n[步驟 3/3] 正在與 Google Gemini 網頁端進行連線握手驗證...")
        ok, msg = asyncio.run(verify_cookies_handshake())
        if ok:
            print(f"  ✓ {msg}")
            print("\n🎉 設定全部完成！現在您可以直接在工作台選用「🟣 WEB 端」生圖！")
            return 0
        else:
            print(f"  ⚠ 驗證訊息：{msg}")
            return 1

    print("\n" + "=" * 65)
    print("【備用手動設定方式】")
    print("若彈出視窗因系統政策或網路限制未能自動捕獲，您也可手動複製貼上：")
    print("1. 開啟您平日已登入的日常瀏覽器，前往 https://gemini.google.com/")
    print("2. 按鍵盤 F12 -> 點「應用程式」(Application) -> 左側「Cookies」-> gemini.google.com")
    print("3. 複製 __Secure-1PSID 與 __Secure-1PSIDTS 兩個項目的值貼在下方：")
    print("=" * 65)
    try:
        manual_psid = input("\n請貼上 __Secure-1PSID (直接按 Enter 略過): ").strip()
        manual_psidts = input("請貼上 __Secure-1PSIDTS (直接按 Enter 略過): ").strip()
        if manual_psid and manual_psidts:
            save_updated_cookies(manual_psid, manual_psidts)
            print("\n正在進行連線握手驗證...")
            ok, msg = asyncio.run(verify_cookies_handshake())
            print(f"驗證結果：{msg}")
            return 0 if ok else 1
    except (EOFError, KeyboardInterrupt):
        pass

    return 1


if __name__ == "__main__":
    is_auto = "--auto" in sys.argv
    sys.exit(interactive_setup(auto_only=is_auto))
