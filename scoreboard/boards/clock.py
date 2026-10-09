"""Clock board - v2 port of the v1 clock.

Layout follows v1's layout.json: the time is centred a little below the middle, the date sits
2px above it aligned to its left edge, AM/PM stacks 2px to its right aligned to its top, and the
weather line hangs at the bottom. With the ``old`` font (04B_24, v1's face) the time snaps to
24 / 16 / 8 px, and ``pl`` is enlarged in whole-number steps. The date, AM/PM and weather use a 5-row bitmap face so the time can be large.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, model_validator

from ..isotime import parse_iso
from ..render import Absolute, Img, Text, load_font, render_tree
from ..render.text import text_box, text_size
from .base import BaseBoard, BoardContext

CLOCK_FONTS = ("clock", "score", "block", "ari", "gothic", "upheaval", "camels", "cute", "old", "pixel", "pixelbold", "pl")
MIN_CLOCK = 8
OLD = "old"                         # 04B_24: a pixel font that is only sharp at multiples of 8
OLD_SIZES = (24, 16, 8)             # v1 drew the time at 24 and everything else at 8
PL = "pl"                          # a bitmap face with only 6 and 12 px sizes: the time is scaled up instead
PL_NATIVE = 12
PL_SCALES = (4, 3, 2, 1)           # whole-number scales only, so the pixels stay sharp
SMALL = 6                         # small text: 5 rows tall, so a big time still fits around it
WIDEST_TIME = "88:88"
WIDEST_DATE = "AUG 88 8888"
WIDEST_WEATHER = "100F H100%"
MERIDIEM_GAP = 2
STACK_GAP = 1
DATE_GAP = 2                        # date bottom to time top, as in v1
ROW_GAP = 1
TIME_CENTER = 0.60                  # v1: time centred at 60% of the height (55% with the weather line)
TIME_CENTER_WEATHER = 0.55
WEATHER_BOTTOM = 0.95               # v1: weather line's bottom edge at 95%
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


def _load(name: str, size: int):
    """load_font, or None when that face cannot be loaded (a missing bitmap file raises)."""
    try:
        return load_font(name, size)
    except (OSError, ValueError):
        return None


def _date_font() -> ImageFont.ImageFont:
    """Face for the date, AM/PM and weather: a 5-row bitmap face, whatever font the time uses.

    Short enough that date + time + weather stack on a 32px panel with a large time. If the
    face is missing, try the next one rather than crash the board."""
    for name, size in (("pl", SMALL), ("pixel", SMALL), ("pixel", 7)):
        font = _load(name, size)
        if font is not None:
            return font
    return ImageFont.load_default()


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


def _sizes(family: str, height: int) -> list[tuple[int, int]]:
    """Candidate (font size, scale) pairs for the time, largest first.

    04B_24 only looks right at multiples of 8. ``pl`` cannot grow, so it is drawn at its
    native 12 px and enlarged by a whole number."""
    if family == OLD:
        return [(s, 1) for s in OLD_SIZES if s <= height] or [(OLD_SIZES[-1], 1)]
    if family == PL:
        return [(PL_NATIVE, k) for k in PL_SCALES]
    return [(s, 1) for s in range(height, MIN_CLOCK - 1, -1)]


def _scaled_text(text: str, font: ImageFont.ImageFont, color: tuple[int, int, int], scale: int) -> Image.Image:
    """``text`` drawn at the face's own size, cropped to its ink and enlarged by ``scale``
    with nearest-neighbour so the pixels stay square."""
    left, top, right, bottom = text_box(text, font)
    img = Image.new("RGBA", (max(int(right), 1), max(int(bottom), 1)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), text, font=font, fill=(*color, 255))
    img = img.crop((int(left), int(top), max(int(right), int(left) + 1), max(int(bottom), int(top) + 1)))
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


@lru_cache(maxsize=32)
def _fonts(width: int, height: int, pad: int, show_date: bool, meridiem: bool, weather: bool, family: str) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, int]:
    """Largest face of ``family`` (and scale) whose whole block (date / time + AM/PM / weather) fits the panel."""
    sizes = _sizes(family, height)
    date = _date_font()
    for size, scale in sizes:
        clock = _load(family, size)
        if clock is None:                       # this face is not installed: try the next size
            continue
        tw, th = text_size(WIDEST_TIME, clock)
        tw, th = tw * scale, th * scale
        if meridiem:
            lw, sh = _stack_size(date)
            tw += MERIDIEM_GAP + lw
            th = max(th, sh)
        if family == OLD:
            # v1 never shrank the time: it drew 24 whenever the time row itself fits the panel,
            # and the date and weather lines just tucked in around it.
            if tw <= width and th <= height:
                return clock, date, scale
            continue
        block_w, block_h = tw, th
        for wanted, sample, gap in ((show_date, WIDEST_DATE, DATE_GAP), (weather, WIDEST_WEATHER, ROW_GAP)):
            if wanted:
                sw, sh = text_size(sample, date)
                block_w, block_h = max(block_w, sw), block_h + sh + gap
        if block_w <= width - 2 * pad and block_h <= height:      # no vertical padding, as in v1
            return clock, date, scale
    last, scale = sizes[-1] if family in (OLD, PL) else (MIN_CLOCK, 1)
    return _load(family, last) or _load("pixel", 8) or date, date, scale


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
        clock_font, date_font, scale = _fonts(w, h, pad, cfg.show_date, twelve, bool(weather_text), cfg.font)

        hour = (now.hour % 12 or 12) if twelve else now.hour
        time_str = f"{hour}:{now.minute:02d}" if twelve else f"{hour:02d}:{now.minute:02d}"
        if scale > 1:
            big = _scaled_text(time_str, clock_font, tuple(cfg.color), scale)
            time_node, (tw, th) = Img(big), big.size
        else:
            time_node = Text(time_str, clock_font, tuple(cfg.color))
            tw, th = time_node.measure()

        letters: list[Text] = []
        lw = stack_h = extra = 0
        if twelve:
            lw, stack_h = _stack_size(date_font)
            extra = MERIDIEM_GAP + lw
            letters = [Text(c, date_font, tuple(cfg.date_color)) for c in ("AM" if now.hour < 12 else "PM")]

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

        # Time: centred across, v1's percentage down, nudged only if the date or weather would be pushed off.
        time_x = max(pad, min((w - tw) // 2, w - pad - tw - extra))
        centre = round(h * (TIME_CENTER_WEATHER if weather else TIME_CENTER))
        top_limit = (dh + DATE_GAP) if date else 0
        bottom_limit = h - th - ((wh + ROW_GAP) if weather else 0)
        time_y = max(top_limit, min(centre - th // 2, bottom_limit))

        items = [(time_node, time_x, time_y, tw, th)]

        # Date: left edge on the time's left edge, DATE_GAP above it.
        if date is not None:
            date_x = max(pad, min(time_x, w - pad - dw))
            items.append((date, date_x, max(0, time_y - DATE_GAP - dh), dw, dh))

        # AM/PM: right of the time, top aligned with it (centred on it when taller).
        if letters:
            lx = time_x + tw + MERIDIEM_GAP
            ly = time_y + (th - stack_h) // 2 if stack_h > th else time_y
            for node in letters:
                cw, ch = node.measure()
                items.append((node, lx + (lw - cw) // 2, ly, cw, ch))
                ly += ch + STACK_GAP

        # Weather: centred, bottom edge at 95%, never touching the time.
        if weather is not None:
            wy = max(round(h * WEATHER_BOTTOM) - wh, time_y + th + ROW_GAP)
            items.append((weather, (w - ww) // 2, min(wy, h - wh), ww, wh))

        frame = render_tree(Absolute(items), w, h, t=ctx.elapsed)

        if alert_color is not None:
            size = ALERT_SIZE if w >= 64 and h >= 32 else 3
            ImageDraw.Draw(frame).rectangle((w - size, h - size, w - 1, h - 1), fill=alert_color)
        return frame
