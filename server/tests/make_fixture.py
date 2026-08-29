"""造一张带 EXIF（拍摄时间 + GPS）的假照片，用来跑通链路。

    python tests/make_fixture.py out.jpg --at "2026-08-12 18:47:03" --gps 31.22 121.46
"""

from __future__ import annotations

import argparse
from fractions import Fraction

from PIL import Image
from PIL.TiffImagePlugin import IFDRational


def _dms(value: float) -> tuple:
    value = abs(value)
    d = int(value)
    m_float = (value - d) * 60
    m = int(m_float)
    s = round((m_float - m) * 60, 4)
    return (
        IFDRational(d, 1),
        IFDRational(m, 1),
        IFDRational(Fraction(s).limit_denominator(10000)),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--at", default="2026-08-12 18:47:03")
    ap.add_argument("--gps", nargs=2, type=float, metavar=("LAT", "LON"))
    args = ap.parse_args()

    img = Image.new("RGB", (1200, 1600), (88, 94, 108))
    exif = Image.Exif()
    exif[271] = "Apple"  # Make
    exif[272] = "iPhone 15 Pro"  # Model
    stamp = args.at.replace("-", ":")
    exif[306] = stamp  # DateTime
    exif.get_ifd(0x8769)[36867] = stamp  # ExifIFD / DateTimeOriginal

    if args.gps:
        lat, lon = args.gps
        gps = exif.get_ifd(0x8825)
        gps[1] = "N" if lat >= 0 else "S"
        gps[2] = _dms(lat)
        gps[3] = "E" if lon >= 0 else "W"
        gps[4] = _dms(lon)

    img.save(args.out, exif=exif)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
