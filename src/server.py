#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照片風格改圖器 - 本地輕量服務端 (Zero-dependency Local Bridge)
功能：
1. 提供靜態網頁服務 (風格型錄 HTML) 與 assets 資源
2. 接收照片上傳 (Base64 JSON / POST)
3. 雙引擎生圖橋接：
   - 預設：Gemini (Antigravity 登入額度)
   - 可選：Codex ($imagegen / ChatGPT 登入額度)
"""

import sys
import os
import re
import json
import time
import base64
import shutil
import socket
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.parse

# 設置 Windows 終端 UTF-8 輸出以防編碼異常
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
UPLOADS_DIR = ASSETS_DIR / "uploads"
GENERATED_DIR = ASSETS_DIR / "generated"

# 確保目錄存在
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = 8088

# 15 款風格提示詞字典 (包含英文核心 Prompt)
STYLE_PROMPTS = {
    "01": {
        "name": "層疊紙雕、手工剪紙",
        "slug": "papercut",
        "prompt": "Convert this image into a soft, handcrafted paper-cut layered illustration style, inspired by papercraft diorama aesthetics. Use smooth rounded shapes, simplified cute character proportions, and minimal facial details. Apply stacked paper layers with visible depth, subtle shadows between layers, and clean cut edges that resemble laser-cut cardstock. Add a distinct white outer outline layer surrounding main subjects. Use a pastel color palette with muted blues, greens, and warm neutrals. Lighting should feel soft, diffused, and even. Tactile paper textures, cozy and storybook-like."
    },
    "02": {
        "name": "搞笑醜萌塗鴉",
        "slug": "doodle",
        "prompt": "Turn this photo into a funny ugly doodle drawing. Make it look like a quick sketch using a cheap marker or crayon, messy, rough, childlike style, bad perspective and awkward proportions, slightly exaggerated facial features. Add simple cartoon background, random sketchy lines, uneven coloring and visible strokes. Humorous, meme-like, casual internet style. Do NOT make it realistic."
    },
    "03": {
        "name": "立體語意文字雕塑",
        "slug": "typography",
        "prompt": "Transform the uploaded image into a semantic typography sculpture artwork. Faithfully preserve the original subject, identity, composition, proportions, perspective, colors, and lighting. Analyze the subject geometry and negative space, physically remove approximately 25-60% of selected object structures and reconstruct the missing shapes using meaningful English words related to the image. Letters must REPLACE the original material, sculpted along original contours with Swiss experimental editorial aesthetics. No extra titles or watermarks."
    },
    "04": {
        "name": "復古裝飾插畫海報",
        "slug": "retro_poster",
        "prompt": "Transform the uploaded photo into a full-bleed vintage decorative illustration poster while keeping photographic texture. Extract the most recognizable subject, selectively reduce background clutter, rearrange with clear hierarchy and breathing space. Use a color palette of 65% haze blue, cream yellow, sage green, and off-white in matte flat fills. Objects simplified into natural organic planes with fine hand-drawn outlines. Subtle vintage printed aesthetic, avoid gradients and heavy grunge."
    },
    "05": {
        "name": "垂直折疊城市海報",
        "slug": "folded_city",
        "prompt": "Reconstruct the scene in the photo into a 'vertically folded world': bottom maintains normal eye-level street perspective, transitioning upwards into a seamless 90-degree vertical bird's-eye aerial view, like the ground is folded skyward. Preserve authentic architectural landmarks and city grid. Mixed hyper-perspective with strong leading lines. Top 20% features a bold sans-serif city name partially masked by buildings. Hyper-realistic architectural photography quality, cinematic natural lighting."
    },
    "06": {
        "name": "彩繪玻璃窗",
        "slug": "stained_glass",
        "prompt": "Transform the uploaded photo into an intricate stained glass window artwork. Preserve the identity, pose, contours, and main composition of the subject. Translate the scene into hand-cut colored glass pieces with dark lead cames along outlines. Glass shows luminous translucency, subtle color variations, and gentle backlighting. Vivid colors, clear facial and architectural features. No frames, watermarks, or plastic neon look."
    },
    "07": {
        "name": "微縮模型移軸效果",
        "slug": "tilt_shift",
        "prompt": "Transform the original photo into a tilt-shift miniature model scene. Preserve recognizable buildings, roads, and people positions, making the scene appear as a meticulously handcrafted scale diorama. Slightly elevated high-angle perspective, sharp center focus with progressive foreground and background blur (narrow depth-of-field). Fine scale-model painted textures, vibrant saturation, authentic lighting. No toy stands or display cases."
    },
    "08": {
        "name": "藍曬植物／山水標本",
        "slug": "cyanotype",
        "prompt": "Transform the photo into a classic cyanotype print artwork. Deep Prussian blue base paper with white and pale blue silhouettes and fine details, as if contact-printed by sunlight. Natural paper fiber texture, slight uneven exposure, and authentic photographic wash. Monochrome blue and white, no frames, titles, or artificial captions."
    },
    "09": {
        "name": "木刻凸版海報",
        "slug": "woodcut",
        "prompt": "Redraw the photo as a handcrafted woodcut or linocut relief print poster. Preserve subject shape, pose, and composition with flat color blocks, bold carved lines, and directional gouge marks for shading. Handcrafted carved irregularities, natural ink traces, and slight print misalignments. Maximum 4 harmonious print colors. Avoid gradients, photorealism, and digital 3D effects."
    },
    "10": {
        "name": "分解軸測圖",
        "slug": "exploded_view",
        "prompt": "Transform the main object in the photo into a clean, precise isometric exploded diagram. Preserve original shape, proportions, materials, and colors, with recognizable components pulled apart along assembly axes while remaining aligned. Crisp axonometric perspective, clean background, soft cast shadows. Technical illustration aesthetics without fake internal parts or commercial UI."
    },
    "11": {
        "name": "原創溫暖手繪動畫風",
        "slug": "warm_anime",
        "prompt": "Transform the uploaded photo into an original warm hand-drawn animated illustration. Preserve subject identities, facial features, poses, clothing, architecture, and camera angle. Organic delicate linework, soft watercolor or gouache backgrounds, gentle animation cel shading, natural warm lighting, and a peaceful, nostalgic, whimsical atmosphere. Purely original style, not copying any specific commercial studio or character."
    },
    "12": {
        "name": "厚塗油畫微景觀",
        "slug": "impasto_oil",
        "prompt": "Transform the photo into a sculptural impasto oil painting miniature landscape poster. Extract core contours, postures, and spatial relations with thick oil paint layers, palette knife textures, and sculpted edges. Center-aligned subject with generous warm-white negative space on canvas; a single color band extracted from the photo supports the subject slice. Heavy paint impasto and subtle specular highlights."
    },
    "13": {
        "name": "歐洲復古鋼筆雕刻繪本",
        "slug": "vintage_comic",
        "prompt": "Redraw the uploaded photograph as a single, full-frame richly detailed 1950s-1960s European vintage comic and storybook illustration. Traditional pen-and-ink engraving, woodcut-inspired linework, and nostalgic hand-printed aesthetics. Bold black ink outlines with exceptionally fine parallel and cross-hatching following contours of buildings, clothing, and sky. Saturated teal-cyan skies, cool shadows, warm vermilion, golden ochre, and risograph paper texture. No split screens or borders."
    },
    "14": {
        "name": "解構瑞士符號海報",
        "slug": "swiss_symbol",
        "prompt": "Transform the uploaded photograph into a single premium full-frame graphic design poster based on post-digital graphic design and deconstructed Swiss typography. Extract an independent abstract visual symbol distilling 2-3 core features using minimal geometric modules, fine structural cuts, and negative space. The symbol is the sole hero surrounded by generous warm-white negative space. Subtle construction grids, hairline guide paths, flat spot-color printing with risograph texture. Razor-sharp micro-typography."
    },
    "15": {
        "name": "靜謐棉紙水彩雜誌海報",
        "slug": "editorial_watercolor",
        "prompt": "Transform the uploaded image into a single full-frame minimalist editorial art poster with a quiet, poetic magazine aesthetic. Set on tactile off-white heavy cotton paper with visible fibers and deckle edges. Render the subject as an exquisite, delicate watercolor silhouette in faint warm grey and muted earthy tones with translucent washes. Refined editorial typography with an elegant serif title, wide-tracked uppercase subtitle, and poetic italic text. Minimal, quiet, airy, wabi-sabi magazine aesthetic."
    }
}


def run_codex_imagegen(prompt_text: str, target_file: Path) -> tuple[bool, str]:
    """使用 Codex CLI ($imagegen) 生成圖片，免 API Key 走 ChatGPT 額度"""
    try:
        codex_exe = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex_exe:
            return False, "找不到 codex CLI 執行檔，請確認已安裝並登入 Codex。"

        codex_prompt = (
            f"$imagegen Generate: {prompt_text}. "
            f"Save or copy the final PNG to {target_file.resolve()}. "
            "Do not use an API key or API fallback."
        )
        print("[Engine: Codex] 開始調用 Codex CLI...")
        cmd = [
            codex_exe, "exec",
            "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "--cd", str(PROJECT_ROOT),
            codex_prompt
        ]
        res = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False
        )
        stdout = res.stdout or ""
        stderr = res.stderr or ""
        output = stdout + "\n" + stderr

        if target_file.exists() and target_file.stat().st_size > 1024:
            print(f"[Engine: Codex] 成功產生圖檔：{target_file.name}")
            return True, output

        print(f"[Engine: Codex] 產生失敗 (ExitCode: {res.returncode})")
        return False, output
    except Exception as e:
        print(f"[Engine: Codex] 執行異常：{e}")
        return False, str(e)


def run_gemini_imagegen(prompt_text: str, target_file: Path, aspect_ratio: str = "3:4") -> tuple[bool, str]:
    """使用 Antigravity CLI (agy -p) 呼叫內建 image-generator 子代理，走 Gemini 額度"""
    try:
        agy_exe = (
            shutil.which("agy")
            or shutil.which("agy.cmd")
            or os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")
        )
        if not agy_exe or not Path(agy_exe).exists():
            return False, f"找不到 Antigravity CLI (agy)：{agy_exe}"

        start_ts = time.time() - 1.0
        print("[Engine: Gemini] 開始調用 Antigravity CLI (agy -p)...")

        agy_prompt = (
            f"Invoke the built-in image-generator subagent to generate: {prompt_text}. "
            f"Aspect ratio: {aspect_ratio}. "
            "Do not run any shell commands; just wait for the subagent to finish and reply with the saved file path."
        )
        cmd = [agy_exe, "-p", agy_prompt]
        res = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=150,
            shell=False
        )
        stdout = res.stdout or ""
        stderr = res.stderr or ""
        output = stdout + "\n" + stderr

        # 1. 優先從 CLI 輸出文字解析回報的實際圖檔路徑
        path_matches = re.findall(r'([A-Za-z]:[\\/][^\s"\'<>|]+?\.(?:png|jpg|jpeg))', output, flags=re.IGNORECASE)
        for match_str in reversed(path_matches):
            candidate = Path(match_str)
            if candidate.is_file() and candidate.stat().st_size > 1024:
                shutil.copy2(candidate, target_file)
                print(f"[Engine: Gemini] 從回覆路徑成功提取產生圖檔：{target_file.name}")
                return True, output

        # 2. 備援：尋找由 image-generator 新產生的圖檔
        brain_dirs = [
            Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity-cli\brain")),
            Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\brain"))
        ]
        latest_file = None
        for bdir in brain_dirs:
            if not bdir.exists():
                continue
            for f in bdir.glob("**/*"):
                if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]:
                    if f.stat().st_mtime >= start_ts:
                        if latest_file is None or f.stat().st_mtime > latest_file.stat().st_mtime:
                            latest_file = f

        if latest_file and latest_file.exists():
            shutil.copy2(latest_file, target_file)
            print(f"[Engine: Gemini] 成功提取產生圖檔：{target_file.name}")
            return True, output

        print("[Engine: Gemini] 未找到新產生的圖檔。")
        return False, output
    except Exception as e:
        print(f"[Engine: Gemini] 執行異常：{e}")
        return False, str(e)


class StyleStudioHandler(SimpleHTTPRequestHandler):
    """處理靜態檔案與 API 請求"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def do_GET(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # 根路徑自動提供 風格型錄.html
        if path == "/" or path == "/index.html":
            catalog_file = PROJECT_ROOT / "風格型錄.html"
            if catalog_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(catalog_file.read_bytes())
                return

        # API: 獲取歷史生成列表
        if path == "/api/history":
            files = []
            if GENERATED_DIR.exists():
                for f in sorted(GENERATED_DIR.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
                    if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]:
                        files.append({
                            "name": f.name,
                            "url": f"/assets/generated/{f.name}",
                            "size": f.stat().st_size,
                            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        })
            self.send_json(200, {"success": True, "files": files})
            return

        # 其餘靜態檔案交由 SimpleHTTPRequestHandler
        super().do_GET()

    def do_POST(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # API: 上傳照片 (Base64)
        if path == "/api/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode("utf-8"))
                filename = data.get("filename", f"upload_{int(time.time())}.jpg")
                img_data = data.get("data", "")
                
                if "," in img_data:
                    img_data = img_data.split(",", 1)[1]
                
                raw_bytes = base64.b64decode(img_data)
                orig_path = Path(filename)
                ext = orig_path.suffix.lower() if orig_path.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"] else ".jpg"
                stem_clean = re.sub(r"[^A-Za-z0-9_]", "_", orig_path.stem).strip("_")[:32] or "photo"
                safe_name = f"user_{int(time.time())}_{stem_clean}{ext}"
                dest_path = UPLOADS_DIR / safe_name
                dest_path.write_bytes(raw_bytes)
                
                rel_url = f"/assets/uploads/{safe_name}"
                self.send_json(200, {
                    "success": True,
                    "filename": safe_name,
                    "url": rel_url,
                    "abs_path": str(dest_path.resolve())
                })
            except Exception as e:
                self.send_json(400, {"success": False, "error": str(e)})
            return

        # API: 執行改圖生成
        if path == "/api/generate":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode("utf-8"))
                style_id = data.get("style_id", "13")
                aspect_ratio = data.get("aspect_ratio", "3:4")
                image_ref = data.get("image_ref", "assets/reference_taipei101.jpg")
                engine_pref = data.get("engine", "gemini")  # 預設 "gemini"，可選 "codex"
                custom_prompt = data.get("custom_prompt", "").strip()

                # 防止意外收到整串 Base64 當作路徑
                if image_ref.startswith("data:image/"):
                    self.send_json(400, {"success": False, "error": "照片尚未完成上傳，請重新選擇照片後再試。"})
                    return

                ref_abs_path = (PROJECT_ROOT / image_ref.lstrip("/\\")).resolve()
                ref_display = str(ref_abs_path) if ref_abs_path.exists() else image_ref

                style_info = STYLE_PROMPTS.get(style_id, STYLE_PROMPTS["13"])
                base_prompt = custom_prompt if custom_prompt else style_info["prompt"]
                full_prompt = f"{base_prompt} Aspect ratio: {aspect_ratio}. Use reference image: {ref_display}."

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_filename = f"{style_info['slug']}_{aspect_ratio.replace(':', 'x')}_{timestamp}.png"
                target_path = GENERATED_DIR / target_filename

                engine_used = None
                success = False
                log_info = ""

                # 依使用者所選生圖模型執行（預設 Gemini，可選 Codex）
                if engine_pref == "codex":
                    success, log_info = run_codex_imagegen(full_prompt, target_path)
                    engine_used = "Codex (ChatGPT)"
                else:
                    success, log_info = run_gemini_imagegen(full_prompt, target_path, aspect_ratio)
                    engine_used = "Gemini (Antigravity)"

                if success and target_path.exists():
                    self.send_json(200, {
                        "success": True,
                        "image_url": f"/assets/generated/{target_filename}",
                        "filename": target_filename,
                        "engine_used": engine_used,
                        "style_name": style_info["name"]
                    })
                else:
                    self.send_json(500, {
                        "success": False,
                        "error": f"{engine_used} 生成失敗，請確認 CLI 登入狀態或切換另一個模型重試。",
                        "engine_attempted": engine_used,
                        "log": log_info[-800:] if log_info else ""
                    })

            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        self.send_json(404, {"error": "Not found"})

    def send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def find_free_port(start_port=DEFAULT_PORT):
    """尋找可用通訊埠"""
    port = start_port
    while port < start_port + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return start_port


def main():
    port = find_free_port(DEFAULT_PORT)
    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, StyleStudioHandler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print(" [SERVER] Photo Style Studio - Local Server Started")
    print(f" [URL] {url}")
    print(f" [ROOT] {PROJECT_ROOT}")
    print(" [MODELS] Gemini (Default) / Codex (ChatGPT)")
    print(" [INFO] Press Ctrl+C in terminal to stop server")
    print("=" * 60)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped.")


if __name__ == "__main__":
    main()
