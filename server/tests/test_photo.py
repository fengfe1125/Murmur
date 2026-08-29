"""photo 解码：像素上限挡压缩炸弹，JPEG draft 缩小解码栅格。"""

from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from murmur.photo import (  # noqa: E402
    MAX_IMAGE_PIXELS,
    PHOTO_EDGE,
    PhotoTooLarge,
    from_bytes,
    load,
)


def jpeg_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "coral").save(buffer, "JPEG")
    return buffer.getvalue()


def decode_size(photo) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(photo.image_b64))).size


class PhotoDecodeTests(unittest.TestCase):
    def test_from_bytes_rejects_a_pixel_bomb_before_decoding(self):
        with self.assertRaises(PhotoTooLarge):
            from_bytes(jpeg_bytes(6000, 5000), max_image_pixels=1_000_000)

    def test_from_bytes_downscales_a_large_photo_to_the_target_edge(self):
        photo = from_bytes(jpeg_bytes(4000, 3000))
        self.assertLessEqual(max(decode_size(photo)), PHOTO_EDGE)
        self.assertEqual(photo.media_type, "image/jpeg")

    def test_load_uses_the_shared_pixel_ceiling(self):
        self.assertEqual(MAX_IMAGE_PIXELS, 100_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.jpg"
            path.write_bytes(jpeg_bytes(800, 600))
            photo = load(path)
            self.assertEqual(photo.path, path)
            self.assertLessEqual(max(decode_size(photo)), PHOTO_EDGE)

    def test_exif_orientation_is_applied_after_downscaling(self):
        buffer = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6  # Orientation: 右转 90°
        Image.new("RGB", (4000, 3000), "coral").save(buffer, "JPEG", exif=exif)
        photo = from_bytes(buffer.getvalue())
        width, height = decode_size(photo)
        self.assertGreater(height, width)


if __name__ == "__main__":
    unittest.main()
