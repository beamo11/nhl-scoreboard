"""Clock board - v2 port of the v1 clock: time with stacked AM/PM, date line above."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, model_validator

from ..isotime import parse_iso
from ..render import Absolute, Text, load_font, profile_for, render_tree
from ..render.text import text_size
from .base import BaseBoard, BoardContext

CLOCK_FONTS = ("clock", "score", "block", "ari", "gothic", "upheaval", "camels", "cute", "old", "pixelbold", "pl")
MIN_CLOCK = 8
DATE_RATIO = 0.4
WIDEST_TIME = "88:88"
WIDEST_DATE = "AUG 88 8888"
WIDEST_WEATHER = "100F H100%"
MERIDIEM_GAP = 2
STACK_GAP = 1
ROW_GAP = 1
RETIRED_KEYS = frozenset({"show_meridiem", "flash_separator", "show_weather", "show_weather_alerts"})
ALERT_SIZE = 7            # v1 used a 7px box in the bottom-right corner
# Same level colours as the alerts board, so the marker matches the card.
ALERT_COLORS = {"warning": (255, 40, 40), "watch": (255, 150, 0), "advisory": (255, 215, 0), "statement": (70, 150, 255)}
ALERT_OTHER = (170, 170, 170)


class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    font: Literal[CLOCK_FONTS] = Field("block", description="Typeface for the time")
    show_date: bool = True
    color: tuple[int, int, int] = Field((0, 150, 150), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((255, 0, 255), description="Date/year colour (RGB)")

    @model_validator(mode="before")
    @classmethod
    def _drop_retired(cls, data):
        """Settings from earlier drafts of this board are now always-on behaviour; ignore them
        if they are still saved, rather than failing on extra="forbid"."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in RETIRED_KEYS}
        return data


def _date_font(clock_size: int, small: ImageFont.ImageFont) -> ImageFont.ImageFont:
    size = max(6, round(clock_size * DATE_RATIO))
    if size >= 15:
        return load_font("pixelbold", size)
    return load_font("pl", 12) if size >= 9 else small


def _stack_size(font: ImageFont.ImageFont) -> tuple[int, int]:
    """Width and height of the two-letter vertical AM/PM stack."""
    width = max(text_size(c, font)[0] for c in "APM")
    return width, 2 * text_size("M", font)[1] + STACK_GAP


def _weather_line(current: object) -> str:
    """Compact temperature/humidity line, or "" when there is no weather data."""
    if not isinstance(current, dict):
        return ""
    unit = (current.get("units") or {}).get("temp", "C")
    parts = []
    if current.get("temp") is not None:
        parts.append(f"{current['temp']}{unit}")
    if current.get("humidity") is not None:
        parts.append(f"H{current['humidity']}%")
    return " ".join(parts)


def _alert_color(alerts: object, now: datetime) -> tuple[int, int, int] | None:
    """Colour of the most serious alert still in force, or None.

    The source publishes alerts most serious first and only re-checks expiry on its own
    schedule, so lapsed ones are skipped here by ``now`` (as the alerts board does).
    """
    if not isinstance(alerts, list):          # None means "unknown" / source off
        return None
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        end = parse_iso(alert.get("expires"))
        if end is not None and end <= now:
            continue
        return ALERT_COLORS.get(alert.get("level") or "other", ALERT_OTHER)
    return None


def _fit_text(text: str, font: ImageFont.ImageFont, width: int) -> str:
    while text and text_size(text, font)[0] > width:
        text = text[:-1]
    return text


@lru_cache(maxsize=32)
def _fonts(width: int, height: int, pad: int, show_date: bool, meridiem: bool, weather: bool, family: str) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """Largest face of ``family`` whose whole block (date / time + AM/PM / weather) still fits the panel."""
    small = profile_for(width, height).label_font()
    for size in range(height, MIN_CLOCK - 1, -1):
        clock = load_font(family, size)
        date = _date_font(size, small)
        tw, th = text_size(WIDEST_TIME, clock)
        if meridiem:
            lw, sh = _stack_size(date)
            tw += MERIDIEM_GAP + lw
            th = max(th, sh)
        block_w, block_h = tw, th
        for wanted, sample in ((show_date, WIDEST_DATE), (weather, WIDEST_WEATHER)):
            if wanted:
                sw, sh = text_size(sample, date)
                block_w, block_h = max(block_w, sw), block_h + sh + ROW_GAP
        if block_w <= width - 2 * pad and block_h <= height - 2 * pad:
            return clock, date
    return load_font(family, MIN_CLOCK), _date_font(MIN_CLOCK, small)


class ClockBoard(BaseBoard):
    key = "clock"
    title = "Clock"
    config_model = ClockConfig

    def render(self, ctx: BoardContext, cfg: ClockConfig) -> Image.Image:
        w, h = ctx.width, ctx.height
        pad = ctx.profile.pad
        now = ctx.now
        twelve = cfg.format == "12h"
        weather_text = _weather_line(ctx.snapshot.get("weather.current"))
        alert_color = _alert_color(ctx.snapshot.get("weather.alerts"), now)
        clock_font, date_font = _fonts(w, h, pad, cfg.show_date, twelve, bool(weather_text), cfg.font)

        hour = (now.hour % 12 or 12) if twelve else now.hour
        time_str = f"{hour}:{now.minute:02d}" if twelve else f"{hour:02d}:{now.minute:02d}"
        time_node = Text(time_str, clock_font, tuple(cfg.color))
        tw, th = time_node.measure()

        letters: list[Text] = []
        lw = stack_h = extra = 0
        if twelve:
            lw, stack_h = _stack_size(date_font)
            extra = MERIDIEM_GAP + lw
            letters = [Text(c, date_font, tuple(cfg.date_color)) for c in ("AM" if now.hour < 12 else "PM")]
        row_h = max(th, stack_h)

        date = None
        dw = dh = 0
        if cfg.show_date:
            date = Text(now.strftime("%b %d %Y").upper(), date_font, tuple(cfg.date_color))
            dw, dh = date.measure()

        weather = None
        ww = wh = 0
        if weather_text:
            fitted = _fit_text(weather_text, date_font, w - 2 * pad - (ALERT_SIZE + 1 if alert_color else 0))
            weather = Text(fitted, date_font, tuple(cfg.date_color))
            ww, wh = weather.measure()

        total_h = row_h + ((dh + ROW_GAP) if date else 0) + ((wh + ROW_GAP) if weather else 0)
        y = max(0, (h - total_h) // 2)
        time_x = (w - (tw + extra)) // 2

        items = []
        if date is not None:
            items.append((date, (w - dw) // 2, y, dw, dh))
            y += dh + ROW_GAP

        items.append((time_node, time_x, y + (row_h - th) // 2, tw, th))
        if letters:
            lx = time_x + tw + MERIDIEM_GAP
            ly = y + (row_h - stack_h) // 2
            for node in letters:
                cw, ch = node.measure()
                items.append((node, lx + (lw - cw) // 2, ly, cw, ch))
                ly += ch + STACK_GAP
        y += row_h + ROW_GAP

        if weather is not None:
            items.append((weather, (w - ww) // 2, y, ww, wh))

        frame = render_tree(Absolute(items), w, h, t=ctx.elapsed)

        if alert_color is not None:
            size = ALERT_SIZE if w >= 64 and h >= 32 else 3
            ImageDraw.Draw(frame).rectangle((w - size, h - size, w - 1, h - 1), fill=alert_color)
        return frame
