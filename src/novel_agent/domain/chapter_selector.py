"""Parse chapter ordinals used in natural-language Project requests."""

from __future__ import annotations

from novel_agent.domain.errors import InvalidChange


def ordinal(token: str) -> int:
    if token.isdecimal():
        return int(token)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if token == "十":
        return 10
    if "百" in token:
        left, right = token.split("百", 1)
        if left not in digits or digits[left] == 0:
            raise InvalidChange("Chapter number is unsupported or ambiguous")
        return digits[left] * 100 + (ordinal(right.lstrip("零〇")) if right else 0)
    if "十" in token:
        left, right = token.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + (digits.get(right, 0) if right else 0)
    if token in digits:
        return digits[token]
    raise InvalidChange("Chapter number is unsupported or ambiguous")
