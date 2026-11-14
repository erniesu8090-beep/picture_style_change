#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照片風格改圖器 - 本地輕量服務端 (Zero-dependency Local Bridge)
功能：
1. 提供靜態網頁服務 (風格型錄 HTML) 與 assets 資源
2. 接收照片上傳 (Base64 JSON / POST)
3. 三引擎生圖橋接：
   - 預設：Gemini (Antigravity 登入額度)
   - 備援 1：Codex ($imagegen / ChatGPT 登入額度)
   - 備援 2：Gemini Web (網頁版 Cookie 額度，零 API 費用)
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
import threading
import uuid
import urllib.parse
from PIL import Image, ImageOps, ImageFilter

# 設置 Windows 終端 UTF-8 輸出以防編碼異常
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 根目錄與資產路徑
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
UPLOADS_DIR = ASSETS_DIR / "uploads"
GENERATED_DIR = ASSETS_DIR / "generated"

# 確保目錄存在
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = 8088
GENERATION_LOCK = threading.Lock()
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB 上限

# 嘗試載入第三引擎 (Gemini Web) 模組
try:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from image_generator.webapi_engine import run_gemini_web_sync
    from image_generator.config import load_gemini_web_cookies
    HAS_GEMINI_WEB = True
except Exception as e:
    HAS_GEMINI_WEB = False
    print(f"[Notice] 第三引擎 (Gemini Web) 模組未啟用：{e}")

# 17 款風格提示詞字典 (包含英文核心 Prompt)
STYLE_PROMPTS = {
    "01": {
        "name": "層疊紙雕、手工剪紙",
        "slug": "papercut",
        "prompt": "Convert this image into a soft, handcrafted paper-cut layered illustration style, inspired by papercraft diorama aesthetics. Use smooth rounded shapes, simplified cute character proportions, and minimal facial details. Apply stacked paper layers with visible depth, subtle shadows between layers, and clean cut edges that resemble laser-cut cardstock. Add a distinct white outer outline layer surrounding main subjects. Use a pastel color palette with muted blues, greens, and warm neutrals. Lighting should feel soft, diffused, and even. Tactile paper textures, cozy and storybook-like."
    },
    "02": {
        "name": "搞笑醜萌塗鴉",
        "slug": "doodle",
        "prompt": "Turn this photo into a funny ugly doodle drawing. Make it look like: a quick sketch using a cheap marker or crayon, messy, rough, childlike style, bad perspective and awkward proportions, slightly exaggerated facial features. Add simple cartoon background, random sketchy lines, uneven coloring and visible strokes. Humorous, meme-like, casual internet style. Do NOT make it realistic."
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
    },
    "16": {
        "name": "包豪斯幾何曲線解構海報",
        "slug": "bauhaus_curvilinear",
        "prompt": "Transform the uploaded image into a single, premium full-frame modern abstract geometric art poster inspired by Bauhaus, Swiss experimental graphic design, vintage independent art magazines, and Risograph screenprint aesthetics. Thoroughly deconstruct the subject, contours, posture, and spatial flow into an abstract geometric composition. Strictly forbidden: rectangles, squares, block collages, checkerboards, rectangular grids, or orthogonal right-angle frames. Permitted forms only: circles, semicircles, rings, circular arcs, sectors, ellipses, triangles, wedges, cones, crescents, irregular curved planes, fluid curves, structural nodes, dots, orbital paths, and breathing negative space. The core geometric ensemble is scaled down by approximately 70% (occupying ~30% of normal scale) forming a refined focal center surrounded by vast warm-white paper negative space. Dual-tone Edensor-style slender high-contrast serif title with sparse micro-typography indexing. Rich tactile paper fibers and subtle Risograph print speckling. No split screens or photographic collage."
    },
    "17": {
        "name": "日系地形等高線敘事漫畫",
        "slug": "topographic_manga",
        "prompt": "Redraw the uploaded photograph into a single, full-frame 2D narrative comic illustration blending Japanese topographic contour-line art with modern linear illustration. Single standalone artwork only, no split-screen, no 50/50 photo comparison, no borders. Accurately preserve subject identity, facial features, proportions, posture, clothing folds, objects, and spatial relationships using crisp, complete pen-and-ink linework; never turn faces or bodies into abstract contour lines. Extract contours of architecture, ground, and natural environment into flowing topographic contour curves extending around the subject with cartographic order, varying line density, selective flat color planes, and fine hatching. Controlled color palette of deep ocean blue, sandstone beige, muted sage grey-green, and terracotta orange-red on clean fine-art paper with precise printed ink lines, avoiding rough grain or watercolor bleeding. Core visual content occupies about 2/3 of the canvas with balanced surrounding negative space, tiny cartographic markers, coordinate symbols, auxiliary hairlines, and a single small English title in a cute, rounded, refined independent art zine typeface."
    }
}


# 2K 標準目標解析度對照表 (長邊皆 >= 2048px / 2560px)
TARGET_2K_SIZES = {
    "16:9": (2560, 1440),
    "9:16": (1440, 2560),
    "3:4": (1920, 2560),
    "4:3": (2560, 1920),
    "1:1": (2048, 2048),
}


def ensure_2k_resolution(img_path: Path | str, aspect_ratio: str = "16:9") -> tuple[int, int]:
    """確保生成圖片達到 2K 以上高畫質（Lanczos 超解析放大 + 邊緣微銳化）"""
    img_path = Path(img_path)
    if not img_path.exists():
        return (0, 0)
    try:
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            target_w, target_h = TARGET_2K_SIZES.get(aspect_ratio, (2560, 1440))

            # 若原圖長邊未達 2048px，或與目標 2K 尺寸不一致，進行高品質 Lanczos 重採樣與微銳化
            if max(w, h) < 2048 or (w, h) != (target_w, target_h):
                src_ratio = w / max(h, 1)
                dst_ratio = target_w / max(target_h, 1)
                if abs(src_ratio - dst_ratio) / dst_ratio <= 0.15:
                    # 比例相符，直接居中擬合至精確 2K 解析度
                    upscaled = ImageOps.fit(im, (target_w, target_h), method=Image.Resampling.LANCZOS)
                else:
                    # 比例差異較大時，維持原圖比例將長邊放大至 2560px
                    scale = 2560.0 / max(w, h)
                    new_w = max(int(round(w * scale)), 2048 if w >= h else int(round(w * scale)))
                    new_h = max(int(round(h * scale)), 2048 if h >= w else int(round(h * scale)))
                    upscaled = im.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

                # 適度銳化補償放大後的微細節，呈現清晰筆觸與紙紋肌理
                upscaled = upscaled.filter(ImageFilter.UnsharpMask(radius=1.1, percent=115, threshold=2))
                upscaled.save(img_path, format="PNG", optimize=True)
                final_size = upscaled.size
                print(f"[2K Upscaler] 畫質已提升至 2K：{w}x{h} -> {final_size[0]}x{final_size[1]} ({img_path.name})")
                return final_size
            return (w, h)
    except Exception as e:
        print(f"[2K Upscaler] 處理異常：{e}")
        return (0, 0)


def run_codex_imagegen(prompt_text: str, target_file: Path, aspect_ratio: str = "3:4") -> tuple[bool, str]:
    """使用 Codex CLI ($imagegen) 生成圖片，免 API Key 走 ChatGPT 額度，並自動確保 2K 畫質"""
    try:
        codex_exe = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex_exe:
            return False, "找不到 codex CLI 執行檔，請確認已安裝並登入 Codex。"

        target_w, target_h = TARGET_2K_SIZES.get(aspect_ratio, (2560, 1440))
        codex_prompt = (
            f"$imagegen Generate in 2K high resolution ({target_w}x{target_h}, aspect ratio {aspect_ratio}): {prompt_text}. "
            f"Save or copy the final PNG to {target_file.resolve()}. "
            "Do not use an API key or API fallback."
        )

        cmd = [
            codex_exe,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(PROJECT_ROOT),
            codex_prompt,
        ]

        print(f"[Engine: Codex] 開始調用 Codex CLI ($imagegen)... 目標：{target_file.name}")
        res = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

        output = (res.stdout or "") + "\n" + (res.stderr or "")

        if target_file.exists() and target_file.stat().st_size > 1024:
            ensure_2k_resolution(target_file, aspect_ratio)
            print(f"[Engine: Codex] 生成成功且已完成 2K 補償：{target_file.name}")
            return True, output

        # 備援：搜尋專案目錄或 Codex 暫存目錄近期新建立之圖檔
        recent_cutoff = time.time() - 120.0
        candidates = []
        for search_dir in [PROJECT_ROOT, GENERATED_DIR]:
            for p in search_dir.glob("*.png"):
                if p.is_file() and p.stat().st_mtime >= recent_cutoff and p != target_file:
                    candidates.append(p)

        if candidates:
            latest = max(candidates, key=lambda x: x.stat().st_mtime)
            shutil.copy2(latest, target_file)
            ensure_2k_resolution(target_file, aspect_ratio)
            print(f"[Engine: Codex] 從候選圖檔成功還原並提升 2K：{target_file.name}")
            return True, output

        return False, f"Codex 執行完成但未找到產出圖檔。\nLog:\n{output[-600:]}"

    except subprocess.TimeoutExpired:
        return False, "Codex 生成超時 (180s)，請重試。"
    except Exception as e:
        return False, f"調用 Codex 發生異常：{str(e)}"


def run_gemini_imagegen(prompt_text: str, target_file: Path, aspect_ratio: str = "3:4") -> tuple[bool, str]:
    """使用 Antigravity CLI (agy -p) 呼叫內建 image-generator 子代理，走 Gemini 額度，並自動確保 2K 畫質"""
    try:
        agy_exe = (
            shutil.which("agy")
            or shutil.which("agy.cmd")
            or shutil.which("antigravity")
            or shutil.which("antigravity.cmd")
        )
        if not agy_exe:
            # 嘗試 Windows 預設安裝路徑
            app_data = os.environ.get("LOCALAPPDATA", "")
            user_prof = os.environ.get("USERPROFILE", "")
            possible_paths = [
                Path(user_prof) / ".gemini" / "antigravity" / "bin" / "agy.cmd",
                Path(user_prof) / "AppData" / "Roaming" / "npm" / "agy.cmd",
                Path(app_data) / "Programs" / "Antigravity" / "agy.cmd",
            ]
            for p in possible_paths:
                if p.exists():
                    agy_exe = str(p)
                    break

        if not agy_exe:
            return False, "找不到 Antigravity CLI (agy) 執行檔，請確認已安裝並加入 PATH。"

        start_ts = time.time() - 1.0
        print("[Engine: Gemini] 開始調用 Antigravity CLI (agy -p)...")

        target_w, target_h = TARGET_2K_SIZES.get(aspect_ratio, (2560, 1440))
        agy_prompt = (
            f"Invoke the built-in image-generator subagent to generate in ultra-sharp 2K resolution ({target_w}x{target_h}): {prompt_text}. "
            f"Aspect ratio: {aspect_ratio}. "
            "Do not run any shell commands; just wait for the subagent to finish and reply with the saved file path."
        )

        cmd = [agy_exe, "-p", agy_prompt]
        res = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )

        output = (res.stdout or "") + "\n" + (res.stderr or "")

        # 1. 嘗試從輸出中解析圖片路徑
        path_matches = re.findall(r"([A-Za-z]:\\[^ \r\n\t]+\.(?:png|jpg|jpeg))", output, re.IGNORECASE)
        for match_str in reversed(path_matches):
            candidate = Path(match_str)
            if candidate.is_file() and candidate.stat().st_size > 1024:
                shutil.copy2(candidate, target_file)
                ensure_2k_resolution(target_file, aspect_ratio)
                print(f"[Engine: Gemini] 從回覆路徑成功提取產生 2K 圖檔：{target_file.name}")
                return True, output

        # 2. 備援：尋找由 image-generator 新產生的圖檔
        brain_dir = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity" / "brain"
        latest_file = None
        latest_mtime = start_ts

        if brain_dir.exists():
            for f in brain_dir.glob("*/*.*"):
                if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]:
                    try:
                        mtime = f.stat().st_mtime
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_file = f
                    except Exception:
                        pass

        if latest_file and latest_file.exists():
            shutil.copy2(latest_file, target_file)
            ensure_2k_resolution(target_file, aspect_ratio)
            print(f"[Engine: Gemini] 成功提取產生 2K 圖檔：{target_file.name}")
            return True, output

        print("[Engine: Gemini] 未找到新產生的圖檔。")
        return False, f"Gemini 執行完成但未找到產出圖檔。\nLog:\n{output[-600:]}"

    except subprocess.TimeoutExpired:
        return False, "Gemini 生成超時 (240s)，請重試。"
    except Exception as e:
        return False, f"調用 Gemini 發生異常：{str(e)}"


class StyleStudioHandler(SimpleHTTPRequestHandler):
    """自訂 HTTP 請求處理器"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def do_GET(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # 根目錄自動映射至風格型錄 HTML
        if path in ["/", "/index.html"]:
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
                        res_str = ""
                        try:
                            with Image.open(f) as im:
                                res_str = f"{im.width}×{im.height}"
                        except Exception:
                            res_str = "2K"
                        files.append({
                            "name": f.name,
                            "url": f"/assets/generated/{f.name}",
                            "size": f.stat().st_size,
                            "resolution": res_str,
                            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        })
            self.send_json(200, {"success": True, "files": files})
            return

        # API: 獲取第三引擎 (Gemini Web) Cookie 狀態
        if path == "/api/cookie_status":
            if not HAS_GEMINI_WEB:
                self.send_json(200, {
                    "available": False,
                    "ready": False,
                    "error": "gemini-webapi 模組未安裝，請執行 pip install -r requirements.txt"
                })
                return
            psid, psidts, err = load_gemini_web_cookies()
            is_ready = bool(psid and psidts and not err)
            self.send_json(200, {
                "available": True,
                "ready": is_ready,
                "error": err
            })
            return

        # 其餘靜態檔案：嚴格白名單限制只允許存取 /assets/ 下的圖片資源與風格型錄.html，阻絕讀取 cookies.json / .env / .git / src
        clean_rel = urllib.parse.unquote(path).lstrip("/")
        if clean_rel == "風格型錄.html":
            catalog_file = PROJECT_ROOT / "風格型錄.html"
            if catalog_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(catalog_file.read_bytes())
                return

        if not clean_rel.startswith("assets/"):
            self.send_json(403, {"error": "Forbidden: Static file access is restricted to /assets/"})
            return

        target_static = (PROJECT_ROOT / clean_rel).resolve()
        if not target_static.is_relative_to(ASSETS_DIR.resolve()) or not target_static.is_file():
            self.send_json(404, {"error": "File not found"})
            return

        super().do_GET()

    def do_POST(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        # CSRF 同源檢查：限制只允許來自本機 127.0.0.1 / localhost 的請求
        origin = self.headers.get("Origin", "")
        if origin and not (origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")):
            self.send_json(403, {"success": False, "error": "Forbidden cross-origin request"})
            return

        # API: 上傳照片 (Base64)
        if path == "/api/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
                self.send_json(413, {"success": False, "error": "上傳檔案超過大小限制 (上限 25MB)"})
                return
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode("utf-8"))
                filename = data.get("filename", f"upload_{int(time.time())}.jpg")
                filename = re.sub(r"[^\w\.\-]", "_", filename)
                ext = Path(filename).suffix.lower()
                if ext not in ALLOWED_IMAGE_EXTS:
                    self.send_json(400, {"success": False, "error": "僅允許上傳 PNG、JPG、JPEG、WEBP 圖片檔"})
                    return
                base64_data = data.get("data", "")

                if "," in base64_data:
                    base64_data = base64_data.split(",", 1)[1]

                img_bytes = base64.b64decode(base64_data)
                save_path = UPLOADS_DIR / f"user_{int(time.time())}_{uuid.uuid4().hex[:6]}_{filename}"
                save_path.write_bytes(img_bytes)

                # 使用 PIL 驗證是否為合法圖片檔，若損毀或偽造則立即刪除
                try:
                    with Image.open(save_path) as im:
                        im.verify()
                except Exception:
                    save_path.unlink(missing_ok=True)
                    self.send_json(400, {"success": False, "error": "上傳的檔案不是有效的圖片格式"})
                    return

                rel_url = f"/assets/uploads/{save_path.name}"
                self.send_json(200, {
                    "success": True,
                    "url": rel_url,
                    "image_url": rel_url,
                    "filename": save_path.name,
                    "rel_path": f"assets/uploads/{save_path.name}"
                })
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        # API: 觸發生圖
        if path == "/api/generate":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 1024 * 1024:
                self.send_json(413, {"success": False, "error": "請求資料過大"})
                return
            post_data = self.rfile.read(content_length)
            if not GENERATION_LOCK.acquire(blocking=False):
                self.send_json(429, {"success": False, "error": "目前已有生圖任務正在執行中，請稍候完成再試。"})
                return
            try:
                data = json.loads(post_data.decode("utf-8"))
                style_id = str(data.get("style_id", "13"))
                if style_id not in STYLE_PROMPTS:
                    style_id = "13"
                aspect_ratio = str(data.get("aspect_ratio", "3:4"))
                if aspect_ratio not in TARGET_2K_SIZES:
                    aspect_ratio = "3:4"
                image_ref = str(data.get("image_ref", "assets/reference_taipei101.jpg"))
                engine_pref = str(data.get("engine", "gemini"))
                if engine_pref not in ("gemini", "codex", "gemini_web"):
                    engine_pref = "gemini"

                # 防止意外收到整串 Base64 當作路徑
                if image_ref.startswith("data:image/"):
                    self.send_json(400, {"success": False, "error": "照片尚未完成上傳，請重新選擇照片後再試。"})
                    return

                ref_abs_path = (PROJECT_ROOT / image_ref.lstrip("/\\")).resolve()
                if not ref_abs_path.is_relative_to(ASSETS_DIR.resolve()):
                    self.send_json(400, {"success": False, "error": "參考圖片路徑必須位於 assets/ 目錄內。"})
                    return
                ref_display = str(ref_abs_path) if ref_abs_path.exists() else image_ref

                style_info = STYLE_PROMPTS.get(style_id, STYLE_PROMPTS["13"])
                base_prompt = style_info["prompt"]
                full_prompt = f"{base_prompt} Aspect ratio: {aspect_ratio}. Use reference image: {ref_display}."

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_filename = f"{style_info['slug']}_{aspect_ratio.replace(':', 'x')}_{timestamp}_{uuid.uuid4().hex[:4]}.png"
                target_path = GENERATED_DIR / target_filename

                engine_used = None
                success = False
                log_info = ""

                # 依使用者所選生圖模型執行（Gemini / Codex / Gemini Web）
                if engine_pref == "gemini_web":
                    engine_used = "Gemini Web (網頁版 Cookie)"
                    if not HAS_GEMINI_WEB:
                        self.send_json(400, {
                            "success": False,
                            "error": "Gemini Web 引擎模組未正確安裝，請先執行 pip install -r requirements.txt",
                            "engine_attempted": engine_used
                        })
                        return

                    psid, psidts, cookie_err = load_gemini_web_cookies()
                    if cookie_err:
                        self.send_json(400, {
                            "success": False,
                            "error": cookie_err,
                            "engine_attempted": engine_used,
                            "is_cookie_error": True
                        })
                        return

                    ref_file = ref_abs_path if ref_abs_path.exists() else None
                    success, log_info = run_gemini_web_sync(
                        prompt_text=base_prompt,
                        target_file=target_path,
                        ref_image=ref_file,
                        aspect_ratio=aspect_ratio,
                    )
                elif engine_pref == "codex":
                    success, log_info = run_codex_imagegen(full_prompt, target_path, aspect_ratio)
                    engine_used = "Codex (ChatGPT)"
                else:
                    success, log_info = run_gemini_imagegen(full_prompt, target_path, aspect_ratio)
                    engine_used = "Gemini (Antigravity)"

                if success and target_path.exists():
                    final_w, final_h = ensure_2k_resolution(target_path, aspect_ratio)
                    self.send_json(200, {
                        "success": True,
                        "image_url": f"/assets/generated/{target_filename}",
                        "filename": target_filename,
                        "engine_used": engine_used,
                        "style_name": style_info["name"],
                        "resolution": f"{final_w}×{final_h}"
                    })
                else:
                    self.send_json(500, {
                        "success": False,
                        "error": log_info if log_info and len(log_info) < 300 else f"{engine_used} 生成失敗，請確認登入狀態或切換另一個模型重試。",
                        "engine_attempted": engine_used,
                        "log": log_info[-800:] if log_info else ""
                    })

            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            finally:
                GENERATION_LOCK.release()
            return

        self.send_json(404, {"error": "Not found"})

    def send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    return DEFAULT_PORT


def main():
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), StyleStudioHandler)
    url = f"http://127.0.0.1:{port}"

    print("=" * 60)
    print("🎨 照片風格改圖器 (Triple Engine AI Studio) 已在本地啟動！")
    print(f"👉 網址：{url}")
    print(f"👉 目標目錄：{PROJECT_ROOT}")
    print(f"👉 生圖引擎：")
    print(f"   - 🔵 GEMINI (預設，走 Antigravity 官方額度)")
    print(f"   - 🟢 CODEX (ChatGPT $imagegen 額度)")
    print(f"   - 🟣 GEMINI WEB ({'已就緒' if HAS_GEMINI_WEB else '模組待配置'}，網頁版 Cookie 額度)")
    print("=" * 60)
    print("提示：在瀏覽器中操作即可上傳照片、選擇 15 款風格並一鍵 2K 直出。")
    print("按 Ctrl+C 可停止伺服器。")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服務器已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
