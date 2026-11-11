"""Safe screenshot-reference inspection and comparison.

Image observations are *visual evidence*, not proof of interaction,
responsive behavior, pixel-perfect correctness or WCAG compliance.
Pillow is an optional CLI dependency: uv run --with pillow ...
"""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_AI_SIDE = 1600
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def _image_library():
    try:
        from PIL import Image, ImageChops, ImageOps, ImageStat
    except ImportError as exc:
        raise RuntimeError("Thiếu Pillow. Dùng: uv run --no-project --with playwright==1.55.0 --with pillow python run_audit.py ...") from exc
    return Image, ImageChops, ImageOps, ImageStat


def _open_local(path: str):
    Image, _, ImageOps, _ = _image_library()
    file = Path(path).expanduser().resolve()
    if not file.is_file() or file.suffix.lower() not in IMAGE_TYPES:
        raise ValueError("Ảnh mục tiêu phải là tệp PNG/JPEG/WebP tồn tại trên máy.")
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Ảnh quá lớn (giới hạn 16 MB).")
    from PIL import Image as PILImage
    PILImage.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(file) as opened:
            opened.verify()
        with Image.open(file) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("Ảnh vượt 24 triệu pixel.")
            result = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, PILImage.DecompressionBombError) as exc:
        raise ValueError("Không thể đọc ảnh hoặc ảnh có kích thước không an toàn.") from exc
    return file, result


def _payload_image(image):
    Image, _, _, _ = _image_library()
    copy = image.copy()
    copy.thumbnail((MAX_AI_SIDE, MAX_AI_SIDE), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    copy.save(stream, format="JPEG", quality=83, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def analyze_target_image(path: str) -> dict[str, Any]:
    """Get private-path-free metadata and a bounded screenshot for AI review."""
    file, image = _open_local(path)
    try:
        _, _, _, ImageStat = _image_library()
        stat = ImageStat.Stat(image.resize((min(image.width, 300), min(image.height, 300))))
        return {
            "sourceHash": hashlib.sha256(str(file).encode("utf-8")).hexdigest()[:32],
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "width": image.width, "height": image.height,
            "averageRGB": [round(v, 1) for v in stat.mean[:3]],
            "screenshot": _payload_image(image),
            "limits": [
                "Không có DOM, selector, computed CSS, hành vi hoặc dữ liệu người dùng từ ảnh.",
                "Các nhận xét bố cục và màu sắc là quan sát thị giác; không kiểm chứng thiết kế gốc.",
            ],
        }
    finally:
        image.close()


def compare_target_image(path: str, screenshot_data_uri: str) -> dict[str, Any]:
    """Pixel metrics only when target and actual dimensions are comparable.

    Do not resize different layouts into apparent matches or imply that pixel
    similarity proves usability. Differences in fonts/antialiasing are expected.
    """
    Image, ImageChops, _, ImageStat = _image_library()
    _, target = _open_local(path)
    try:
        if not screenshot_data_uri.startswith("data:image/") or "," not in screenshot_data_uri:
            return {"status": "UNVERIFIED", "reason": "Thiếu screenshot thực tế."}
        data = base64.b64decode(screenshot_data_uri.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(data)) as actual_open:
            actual = actual_open.convert("RGB")
        result = {
            "referenceWidth": target.width, "referenceHeight": target.height,
            "actualWidth": actual.width, "actualHeight": actual.height,
        }
        if target.size != actual.size:
            return {**result, "status": "UNVERIFIED",
                    "reason": "Ảnh mẫu và ảnh chụp không cùng kích thước; chưa căn chỉnh, không tính điểm sai lệch pixel."}
        small = target.size[0] * target.size[1]
        diff = ImageChops.difference(target, actual)
        stats = ImageStat.Stat(diff)
        mean = sum(stats.mean[:3]) / 3
        sample = diff.resize((max(1, target.width // 4), max(1, target.height // 4)))
        pixels = sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata()
        changed = sum(1 for px in pixels if max(px) >= 35)
        sample_count = sample.width * sample.height
        return {**result, "status": "MEASURED",
                "meanAbsoluteRGBDifference": round(mean, 2),
                "sampledChangedPixelsPercent": round(100 * changed / sample_count, 2),
                "comparability": "same-pixel-dimensions",
                "warning": "Sai lệch pixel không đồng nghĩa lỗi UI/UX; có thể do nội dung động, font, DPI hoặc trạng thái."}
    finally:
        target.close()
