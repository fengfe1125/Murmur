"""把一张照片折成「此刻」——时间、时段、星期，外加它是在哪儿拍的。

地点有两层，用途完全不同：

- ``place``：地名，由 App 在机上（CLGeocoder）反解好之后随上传一起送来。
  它进模型上下文。「是不是又去那家店了」这句话的信息不在画面里，画面只告诉你
  「靠窗的位子」。服务端自己不做逆地理编码，坐标也不会被送去任何地图服务。
- ``spot``：GPS 四舍五入到约 100 米的格子，匿名指纹，只用来回答「他在同一个
  地方来过几次」。只有它进本地数据库。

画面仍然是地点的第一来源：地名说「星巴克」，画面说「一个人坐了很久」，
后者才是他翻出这张照片的原因。地名是补充，不是替代。

原始经纬度两层都不留：折完就丢，不落库。
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

# 地名再长也不该把读图的上下文顶掉。App 那边已经截过一次，这里是兜底。
MAX_PLACE_CHARS = 60


def time_bucket(hour: int) -> str:
    label = "深夜"  # 0:00–5:00 落在这里
    for start, name in _BUCKETS:
        if hour >= start:
            label = name
    return label


def spot_key(lat: float | None, lon: float | None) -> str | None:
    """匿名地点指纹。小数点后 3 位 ≈ 110 米，够区分「家/公司/常去的店」，
    又粗到没法还原成一个具体门牌。这个值只进本地数据库，不出网。"""
    if lat is None or lon is None:
        return None
    return f"{lat:.3f},{lon:.3f}"


def _clean_place(place: str | None) -> str | None:
    if not place:
        return None
    text = " ".join(str(place).split())
    return text[:MAX_PLACE_CHARS] or None


def _gap(at: datetime, now: datetime) -> str:
    """这张照片离现在多远，说成一句能直接读出口的话。

    「3 年前的今天」是当年今日整个功能的名字，所以同月同日单独成一档；
    其余的按天/月/年取一个粗档，宁可粗也不要精确到让人出戏。
    """
    days = (now.date() - at.date()).days
    if days <= 0:
        return "就在今天"
    years = now.year - at.year
    if years >= 1 and (now.month, now.day) == (at.month, at.day):
        return f"{years} 年前的今天"
    if days < 30:
        return f"{days} 天前"
    if days < 365:
        return f"{days // 30} 个月前"
    return f"{days // 365} 年前"


@dataclass
class Moment:
    at: datetime
    bucket: str
    weekday: str
    is_workday: bool
    spot: str | None
    exact_time: bool  # False = 没有拍摄时间，用的是收到的时间
    # 反解出来的地名。没有就是没有——拿不到、关了定位、离线，都很正常。
    place: str | None = None
    # 真正的现在。随手拍随手发时它和 at 是同一刻；当年今日翻出来的旧照片
    # 差着好几年，两个时间必须分开说。
    now: datetime | None = None

    @classmethod
    def of(
        cls,
        photo: Photo,
        tz: ZoneInfo,
        received_at: datetime | None = None,
        place: str | None = None,
    ) -> Moment:
        now = (received_at or datetime.now(tz)).astimezone(tz)
        at = photo.shot_at
        exact = at is not None
        if at is None:
            # Telegram 压缩过的图会丢 EXIF，用消息时间兜底——
            # 随手拍随手发的场景里，这两个时间差不了几分钟。
            at = now
        if at.tzinfo is None:
            at = at.replace(tzinfo=tz)
        return cls(
            at=at.astimezone(tz),
            bucket=time_bucket(at.astimezone(tz).hour),
            weekday=_WEEKDAYS[at.astimezone(tz).weekday()],
            is_workday=at.astimezone(tz).weekday() < 5,
            spot=spot_key(photo.lat, photo.lon),
            exact_time=exact,
            place=_clean_place(place),
            now=now,
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
            place=None,
            now=at,
        )

    @property
    def is_recalled(self) -> bool:
        """拍的那天不是今天——「此刻」这个词对它就是错的。

        当年今日读图失败会退回普通回复，那条路一样不能把三年前说成此刻；
        判断放在这里而不是各个调用点，是因为漏掉一处就等于没修。
        """
        now = self.now or self.at
        return self.exact_time and self.at.date() != now.date()

    def describe(self) -> str:
        """给模型看的一行摘要，随手拍随手发用。地点另起一行，见 describe_place。"""
        stamp = self.at.strftime("%H:%M")
        note = "" if self.exact_time else "（照片没带拍摄时间，这是收到的时间）"
        return (
            f"{self.at.strftime('%Y-%m-%d')} {self.weekday} {stamp}{note}"
            f" · {self.bucket} · {'工作日' if self.is_workday else '周末'}"
        )

    def describe_place(self) -> str | None:
        """地点那一行。没反解出地名就什么都不说——不要写「地点：未知」，
        那只会让模型把「不知道」当成一条信息去用。"""
        return f"拍摄地点：{self.place}" if self.place else None

    def describe_recalled(self) -> str:
        """当年今日：他翻出来的是一张旧照片，拍摄时间不是「此刻」。

        两个时间必须分开说。合成一个，模型就会以为今天是三年前的那天，
        问出来的话全是错的——而这一屏的全部价值就是问对那一天。
        """
        now = self.now or self.at
        lines: list[str] = []
        if self.exact_time:
            lines.append(
                f"这张照片拍摄于 {self.at.strftime('%Y-%m-%d')} {self.weekday}"
                f" {self.at.strftime('%H:%M')} · {self.bucket} ·"
                f" {'工作日' if self.is_workday else '周末'}"
                f"（{_gap(self.at, now)}）"
            )
        else:
            lines.append("这张照片没带拍摄时间，不知道是哪天拍的——别猜具体日期。")
        if line := self.describe_place():
            lines.append(line)
        lines.append(
            f"现在是 {now.strftime('%Y-%m-%d')} {_WEEKDAYS[now.weekday()]}"
            f" {now.strftime('%H:%M')}"
        )
        return "\n".join(lines)
