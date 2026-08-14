"""读图：EXIF 元数据 + 一张压缩过的 base64 JPEG。

EXIF 是这个项目的关键——"是在回家的路上吗"这句话，信息不在画面里，
在拍摄时间和 GPS 里。画面只告诉你"电梯"。

EXIF 解析思路参考 Romancha/photo-moments-telegram-bot 的 /info 实现
(https://github.com/Romancha/photo-moments-telegram-bot)，这里用 Pillow 重写。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image, ImageOps

try:  # iPhone 默认存 .HEIC，没装也不影响 jpg/png
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

# 发给模型的图分两档。
#
# 生活照（竖拍 3:4、横拍 4:3、方形）1024 长边足够看懂"电梯口/傍晚/一个人"
# 这类场景，图像 token 只有 1568 的约四成。
#
# 但**截图不够**：手机截图 1170×2532 压到 1024 长边只剩 473 宽，
# 评论区小字会糊成灰条，模型就会说"啥都看不见"。截图是这个产品的
# 主要输入之一，所以窄长（竖屏截图）和 16:9 及以上（桌面截图、
# 宽幅照片）都给到 1568——宁可多花一点 token，也不冒小字变糊的风险。
PHOTO_EDGE = 1024
SCREEN_EDGE = 1568
JPEG_QUALITY = 82


def _target_edge(w: int, h: int) -> int:
    """按宽高比挑档位。阈值故意保守：只把明确是普通照片的降档。

    ar < 0.53  竖屏截图（9:19.5 ≈ 0.46）
    ar > 1.5   桌面截图 / 16:9 宽幅（降档省的钱不值得冒小字糊掉的风险）
    其余       普通照片：竖拍 0.75、横拍 1.33、方形 1.0
    """
    if w <= 0 or h <= 0:
        return SCREEN_EDGE
    ar = w / h
    if ar < 0.53 or ar > 1.5:
        return SCREEN_EDGE
    return PHOTO_EDGE

_EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}
_GPS_TAGS = {v: k for k, v in ExifTags.GPSTAGS.items()}


@dataclass
class Photo:
    path: Path | None
    shot_at: datetime | None
    lat: float | None
    lon: float | None
    camera: str | None
    image_b64: str
    media_type: str = "image/jpeg"

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


def _to_degrees(value) -> float | None:
    """EXIF 的 GPS 是 ((度,分,秒)) 有理数三元组。"""
    try:
        d, m, s = (float(x) for x in value)
    except (TypeError, ValueError):
        return None
    return d + m / 60 + s / 3600


def _read_gps(exif) -> tuple[float | None, float | None]:
    try:
        gps = exif.get_ifd(_EXIF_TAGS["GPSInfo"])
    except (KeyError, AttributeError):
        return None, None
    if not gps:
        return None, None

    lat = _to_degrees(gps.get(_GPS_TAGS["GPSLatitude"]))
    lon = _to_degrees(gps.get(_GPS_TAGS["GPSLongitude"]))
    if lat is None or lon is None:
        return None, None

    if str(gps.get(_GPS_TAGS["GPSLatitudeRef"], "N")).upper().startswith("S"):
        lat = -lat
    if str(gps.get(_GPS_TAGS["GPSLongitudeRef"], "E")).upper().startswith("W"):
        lon = -lon
    return lat, lon


def _read_shot_at(exif) -> datetime | None:
    raw = None
    try:
        sub = exif.get_ifd(_EXIF_TAGS["ExifOffset"])
        raw = sub.get(_EXIF_TAGS["DateTimeOriginal"])
    except (KeyError, AttributeError):
        pass
    raw = raw or exif.get(_EXIF_TAGS["DateTime"])
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _encode(img: Image.Image) -> str:
    img = img.copy()
    if img.mode != "RGB":
        img = img.convert("RGB")
    edge = _target_edge(*img.size)
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    # 不带 exif 保存——元数据已经单独提取了，没必要再发一份给模型。
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _build(img: Image.Image, path: Path | None) -> Photo:
    # 先读元数据：exif_transpose 会重建图像，之后再取容易丢东西。
    exif = img.getexif()
    lat, lon = _read_gps(exif)
    make = exif.get(_EXIF_TAGS["Make"])
    model = exif.get(_EXIF_TAGS["Model"])
    camera = " ".join(str(x).strip() for x in (make, model) if x) or None
    shot_at = _read_shot_at(exif)

    # 再按 Orientation 摆正，否则竖拍的照片会横着发给模型。
    upright = ImageOps.exif_transpose(img) or img
    return Photo(
        path=path,
        shot_at=shot_at,
        lat=lat,
        lon=lon,
        camera=camera,
        image_b64=_encode(upright),
    )


def load(path: str | Path) -> Photo:
    path = Path(path).expanduser()
    with Image.open(path) as img:
        return _build(img, path)


def from_bytes(data: bytes) -> Photo:
    """Telegram 收到的图。压缩过的 photo 没有 EXIF，
    以 document 发的原图通常还留着。"""
    with Image.open(io.BytesIO(data)) as img:
        return _build(img, None)


def save_preview(root: Path, entry_id: int, photo: Photo) -> Path:
    path = Path(root) / "photos" / f"{entry_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.standard_b64decode(photo.image_b64))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
