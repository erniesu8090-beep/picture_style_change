"""Gemini Web App 逆向封裝生圖引擎 (基於 gemini-webapi)"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, List

from gemini_webapi import GeminiClient, GeneratedImage
from .config import load_gemini_web_cookies, save_updated_cookies, SAFE_CA_BUNDLE


class GeminiWebApiEngine:
    """使用個人 Gemini App Web 端 Session 進行無頭生圖的引擎"""

    def __init__(self, cookie_file: str | Path | None = None) -> None:
        self.cookie_file = cookie_file
        self._client: Optional[GeminiClient] = None

    async def _get_client(self) -> GeminiClient:
        """初始化或取得已連線的 GeminiClient 實例"""
        if self._client is not None:
            return self._client

        psid, psidts, err = load_gemini_web_cookies(self.cookie_file)
        if err or not psid or not psidts:
            # 嘗試自動從本機瀏覽器 Profile 無頭提取
            try:
                from .cookie_fetcher import extract_cookies_from_browser
                psid, psidts = extract_cookies_from_browser(headless=True, wait_seconds=15)
                if psid and psidts:
                    save_updated_cookies(psid, psidts, self.cookie_file)
                    err = None
            except Exception:
                pass
        if err or not psid or not psidts:
            raise ValueError(err or "無法取得 Gemini 網頁版 Cookie 憑證，請雙擊執行「設定Web_Cookie.bat」。")

        # 建立非官方 Web 客戶端 (傳入 SAFE_CA_BUNDLE 避開中文路徑 curl 77 錯誤)
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=None,
            verify=SAFE_CA_BUNDLE or True,
        )

        try:
            print("[Gemini Web Engine] 正在與 Google Gemini 網頁端建立會話 (Handshake)...")
            await client.init(
                timeout=90,
                auto_close=False,
                close_delay=300,
                auto_refresh=True,
            )
            self._client = client

            # 若背景刷新了 Token，嘗試回寫保存
            if hasattr(client, "secure_1psidts") and client.secure_1psidts:
                save_updated_cookies(psid, client.secure_1psidts, self.cookie_file)

            return client
        except Exception as e:
            # 關閉失敗客戶端並嘗試自動從瀏覽器重新提取最新 Cookie 重試一次
            try:
                await client.close()
            except Exception:
                pass
            self._client = None

            try:
                from .cookie_fetcher import extract_cookies_from_browser
                print("[Gemini Web Engine] Cookie 可能已過期，正在嘗試從瀏覽器自動更新 Cookie...")
                new_psid, new_psidts = extract_cookies_from_browser(headless=True, wait_seconds=15)
                if new_psid and new_psidts:
                    save_updated_cookies(new_psid, new_psidts, self.cookie_file)
                    retry_client = GeminiClient(
                        secure_1psid=new_psid,
                        secure_1psidts=new_psidts,
                        proxy=None,
                        verify=SAFE_CA_BUNDLE or True,
                    )
                    await retry_client.init(timeout=90, auto_close=False, close_delay=300, auto_refresh=True)
                    self._client = retry_client
                    return retry_client
            except Exception:
                pass

            raise RuntimeError(
                f"Gemini 網頁端握手失敗：{e}。\n"
                "請雙擊專案目錄下的「設定Web_Cookie.bat」重新整理登入狀態。"
            )

    async def close(self) -> None:
        """關閉會話"""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    @staticmethod
    def _ensure_image_prompt(prompt: str, aspect_ratio: str = "16:9") -> str:
        """關鍵防呆：確保提示詞帶有生圖指令與長寬比，防止 Gemini 僅回覆文字或網頁圖"""
        keywords = ("generate", "create", "draw", "redraw", "transform", "生成", "畫", "繪製", "產生", "改圖")
        cleaned = prompt.strip()
        has_action = any(k in cleaned.lower() for k in keywords)
        
        base_prompt = cleaned if has_action else f"Generate an image: {cleaned}"
        
        # 若未指定比例，附加比例要求
        if "aspect ratio" not in base_prompt.lower() and "長寬比" not in base_prompt:
            base_prompt = f"{base_prompt}\n\nAspect ratio: {aspect_ratio}."
            
        return base_prompt

    async def generate_style_image(
        self,
        prompt_text: str,
        target_file: Path,
        ref_image: Optional[Path] = None,
        aspect_ratio: str = "16:9",
    ) -> tuple[bool, str]:
        """
        發送生圖請求並將成果存為 target_file
        
        參數:
            prompt_text: 風格提示詞
            target_file: 輸出目標路徑 (PNG)
            ref_image: 參考照片路徑 (供照片改圖 Image-to-Image 使用)
            aspect_ratio: 目標比例
        """
        try:
            client = await self._get_client()
            final_prompt = self._ensure_image_prompt(prompt_text, aspect_ratio)

            # 若有參考照片，以 files 列表傳入供上傳附件 (gemini-webapi v2+ 參數名稱為 files)
            input_files: List[str] = []
            if ref_image and Path(ref_image).is_file():
                input_files.append(str(Path(ref_image).resolve()))
                print(f"[Gemini Web Engine] 已掛載參考圖片進行風格轉移：{ref_image.name}")

            print(f"[Gemini Web Engine] 正在向 Gemini 網頁端發送生成請求 (Prompt: {final_prompt[:60]}...)...")
            
            if input_files:
                response = await client.generate_content(final_prompt, files=input_files)
            else:
                response = await client.generate_content(final_prompt)

            # 檢查並過濾生成圖片
            generated_imgs = [img for img in response.images if isinstance(img, GeneratedImage)]

            if not generated_imgs:
                # 若無專門 GeneratedImage，嘗試檢查所有回傳圖片
                if response.images:
                    generated_imgs = list(response.images)

            if not generated_imgs:
                reply_snippet = response.text[:200] if response.text else "無文字回應"
                return False, f"Gemini 網頁端未回傳任何圖片。模型回應摘要：{reply_snippet}"

            # 儲存第一張生成的圖片
            first_img = generated_imgs[0]
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 使用 gemini-webapi v2 內建的 save 方法（預設抓取 full_size 原圖並回傳實際存檔路徑）
            temp_dir = target_file.parent
            temp_name = f"temp_web_{target_file.stem}.png"
            temp_path = temp_dir / temp_name
            
            saved_path_str = await first_img.save(
                path=str(temp_dir),
                filename=temp_name,
            )

            actual_saved = Path(saved_path_str) if saved_path_str else temp_path
            if actual_saved.exists():
                actual_saved.replace(target_file)
                print(f"[Gemini Web Engine] 成功取得網頁端生成圖檔：{target_file.name}")
                return True, "成功從 Gemini 網頁端生成圖片"
            elif temp_path.exists():
                temp_path.replace(target_file)
                print(f"[Gemini Web Engine] 成功取得網頁端生成圖檔：{target_file.name}")
                return True, "成功從 Gemini 網頁端生成圖片"

            return False, "圖片下載存檔失敗，未在目標路徑找到暫存圖檔。"

        except Exception as e:
            err_msg = str(e)
            print(f"[Gemini Web Engine 錯誤]: {err_msg}")
            return False, err_msg


def run_gemini_web_sync(
    prompt_text: str,
    target_file: Path,
    ref_image: Optional[Path] = None,
    aspect_ratio: str = "16:9",
) -> tuple[bool, str]:
    """同步包裝函式，供標準 HTTP 執行緒直接呼叫"""
    engine = GeminiWebApiEngine()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                engine.generate_style_image(
                    prompt_text=prompt_text,
                    target_file=target_file,
                    ref_image=ref_image,
                    aspect_ratio=aspect_ratio,
                )
            )
        finally:
            loop.run_until_complete(engine.close())
            loop.close()
    except Exception as e:
        return False, str(e)
