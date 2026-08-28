"""把一张照片折成"此刻"——时间、时段、星期。

地点不在这里。地点由模型从画面里读（写字楼电梯间 / 地铁 / 自家沙发），
比任何逆地理编码都准，而且不用用户填一行配置。

这里只保留一个匿名的"地点指纹"：GPS 四舍五入到约 100 米的格子，
用来回答"他在同一个地方来过几次"。永远不解析成地名，也永远不发给模型坐标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .photo import Photo

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 边界值是起点，按小时升序
_BUCKETS = [
    (5, "清晨"),
    (8, "上午"),
    (11, "午间"),
    (14, "下午"),
    (17, "傍晚"),
    (20, "夜里"),
    (23, "深夜"),
]


def time_bucket(hour: int) -> str:
    label = "深夜"  # 0:00–5:00 落在这里
    for start, name in _BUCKETS:
        if hour >= start:
            label = name
    return label


def spot_key(lat: float | None, lon: float | None) -> str | None:
    """匿名地点指纹。小数点后 3 位 ≈ 110 米，够区分"家/公司/常去的店"，
    又粗到没法还原成一个具体门牌。这个值只进本地数据库，不出网。"""
    if lat is None or lon is None:
        return None
    return f"{lat:.3f},{lon:.3f}"


@dataclass
class Moment:
    at: datetime
    bucket: str
    weekday: str
    is_workday: bool
    spot: str | None
    exact_time: bool  # False = 没有拍摄时间，用的是收到的时间

    @classmethod
    def of(cls, photo: Photo, tz: ZoneInfo, received_at: datetime | None = None) -> Moment:
        at = photo.shot_at
        exact = at is not None
        if at is None:
            # Telegram 压缩过的图会丢 EXIF，用消息时间兜底——
            # 随手拍随手发的场景里，这两个时间差不了几分钟。
            at = received_at or datetime.now(tz)
        if at.tzinfo is None:
            at = at.replace(tzinfo=tz)
        return cls(
            at=at.astimezone(tz),
            bucket=time_bucket(at.astimezone(tz).hour),
            weekday=_WEEKDAYS[at.astimezone(tz).weekday()],
            is_workday=at.astimezone(tz).weekday() < 5,
            spot=spot_key(photo.lat, photo.lon),
            exact_time=exact,
        )

    @classmethod
    def text_only(cls, tz: ZoneInfo, received_at: datetime | None = None) -> Moment:
        """没有图的时候的此刻。时间照样有用——半夜发消息和中午发消息不一样。"""
        at = (received_at or datetime.now(tz)).astimezone(tz)
        return cls(
            at=at,
            bucket=time_bucket(at.hour),
            weekday=_WEEKDAYS[at.weekday()],
            is_workday=at.weekday() < 5,
            spot=None,
            exact_time=True,
        )

    def describe(self) -> str:
        """给模型看的一行摘要。只说时间——地点让它自己从画面里看。"""
        stamp = self.at.strftime("%H:%M")
        note = "" if self.exact_time else "（照片没带拍摄时间，这是收到的时间）"
        return (
            f"{self.at.strftime('%Y-%m-%d')} {self.weekday} {stamp}{note}"
            f" · {self.bucket} · {'工作日' if self.is_workday else '周末'}"
        )
