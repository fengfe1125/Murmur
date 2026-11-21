"""Objective, source-grounded response contracts shared by all clients."""

from __future__ import annotations

from .objectivity import RULES

SYSTEM = RULES + """
交流要具体、有依据，长度根据问题需要决定。用户没有提问时可以简短回应。
主动消息必须有确切、适时的内容，不编造你的生活或亲密关系；没有值得说的可 quiet。
不要重复最近的问题。scene 仅记录画面可见内容，不把用户猜测写成画面事实。
只返回 JSON，字段顺序为 move → say → scene：
move 是 speak / brief / quiet；say 是字符串数组，一般一条、至多三条；quiet 时为空。
scene 是一句客观画面描述，无图片时留空。不要代码围栏。
"""

READING_SYSTEM = RULES + """
用户发送了一张照片。先陈述能确认的画面，再提供可选的交流入口。
保留兼容字段 guess，但它表示谨慎的开场观察，不猜用户意图，不冒险诱导纠正。
guess 一句话，最多30字，不换行、不用 emoji。
angles 是1至3个不同的可选入口，每项最多12字，基于可见细节，不预设关系和情绪。
scene 一句客观画面描述。模糊细节明确不确定；日期只使用提供的拍摄信息。
只返回 JSON：{"guess":"…","angles":["…"],"scene":"…"}，不加代码围栏。
"""


# say 是数组：真人在聊天软件里一条一条发，不是发一整段。
# 这个决定同时解决了"太长一坨"和"像 AI 在写作文"两个问题。
# 字段顺序有意为之：move 和 say 排在最前面。
# 服务端是边生成边往外吐的，say 早一点写完，第一条气泡就能早几秒发出去。
# scene 只是存档用，放最后。
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "move": {
            "type": "string",
            "enum": ["speak", "brief", "quiet"],
            "description": "回应力度。分享类内容基本都该是 speak。",
        },
        # maxItems 是硬约束，不是建议。没有它模型会把上面的情绪词表
        # 当成清单一条条念出来，撑爆 max_tokens 然后 JSON 截断。
        "say": {
            "type": "array",
            "items": {"type": "string", "maxLength": 60},
            "minItems": 0,
            "maxItems": 3,
            "description": "每项是单独发出的一条消息，最多 3 条，每条都短。quiet 时为空数组。",
        },
        "scene": {
            "type": "string",
            "description": "一句客观的画面描述，存档用。不含情绪判断。",
        },
    },
    "required": ["move", "say", "scene"],
    "additionalProperties": False,
}
