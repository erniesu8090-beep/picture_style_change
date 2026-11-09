"""圖片高畫質升頻工具 (Image Upscale Tool)

將生成圖片自動調整至至少 1920x1080（2K/FHD）標準規格。
- 16:9 比例：寬度至少 1920，高度至少 1080
- 9:16 比例：寬度至少 1080，高度至少 1920
採用 Pillow Lanczos 高品質插值重採樣，確保線條清晰平滑。
"""

import argparse
import sys
from pathlib import Path
from PIL import Image


TARGET_16_9 = (1920, 1080)
TARGET_9_16 = (1080, 1920)


def upscale_image(
    image_path: Path,
    min_width: int = 1920,
    min_height: int = 1080,
    output_path: Path | None = None,
) -> tuple[int, int, bool]:
    """將單張圖片升頻至指定門檻規格（若小於門檻）。

    回傳：(新寬度, 新高度, 是否有進行放大)
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"找不到檔案：{image_path}")

    if output_path is None:
        output_path = image_path

    with Image.open(image_path) as img:
        orig_w, orig_h = img.size
        ratio = orig_w / orig_h

        # 判斷是橫向 (接近 16:9) 還是直向 (接近 9:16)
        if ratio >= 1.0:
            target_min_w, target_min_h = min_width, min_height
        else:
            target_min_w, target_min_h = min_height, min_width

        # 若兩邊都已達到或超過標準，且未強制放大，則保持原樣
        if orig_w >= target_min_w and orig_h >= target_min_h:
            return orig_w, orig_h, False

        # 計算保持長寬比下的縮放倍率
        scale_w = target_min_w / orig_w
        scale_h = target_min_h / orig_h
        scale = max(scale_w, scale_h)

        new_w = int(round(orig_w * scale))
        new_h = int(round(orig_h * scale))

        # 使用 Lanczos 演算法進行高品質重採樣
        resized_img = img.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

        # 保持原本色彩模式與儲存品質
        save_kwargs = {}
        if image_path.suffix.lower() in [".jpg", ".jpeg"]:
            save_kwargs["quality"] = 95
            save_kwargs["subsampling"] = 0
        elif image_path.suffix.lower() == ".png":
            save_kwargs["optimize"] = True

        resized_img.save(output_path, **save_kwargs)
        return new_w, new_h, True


def main() -> None:
    parser = argparse.ArgumentParser(description="圖片高品質升頻至至少 1920x1080 規格")
    parser.add_argument("path", nargs="?", help="要處理的圖片路徑或資料夾")
    parser.add_argument("--all", action="store_true", help="批次處理 assets/ 內的所有圖片")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    assets_dir = project_root / "assets"

    targets: list[Path] = []
    if args.all:
        if assets_dir.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                targets.extend(assets_dir.glob(ext))
    elif args.path:
        p = Path(args.path)
        if not p.is_absolute():
            p = project_root / p
        if p.is_dir():
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                targets.extend(p.glob(ext))
        else:
            targets.append(p)
    else:
        # 預設處理 assets/ 內所有圖片
        if assets_dir.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                targets.extend(assets_dir.glob(ext))

    if not targets:
        print("未指定圖片且 assets/ 中無支援格式圖片。")
        return

    print(f"開始處理，共 {len(targets)} 張圖片...")
    for t in targets:
        try:
            new_w, new_h, modified = upscale_image(t)
            status = "已放大" if modified else "已達標免放大"
            print(f"[{status}] {t.name}: {new_w} x {new_h}")
        except Exception as e:
            print(f"[失敗] {t.name}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
