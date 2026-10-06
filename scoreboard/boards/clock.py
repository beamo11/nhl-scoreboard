"""Clock board — V1 layout, drop-in replacement for the V2 clock (same key, class and config names).
 
Time with the stacked A/M or P/M tag beside it, a one-line date ("AUG 08 2026") under it, and
(optionally) the current temperature and humidity on the bottom line, as the V1 weather clock
did. A small coloured square in the bottom-right corner flags an active weather alert.
The colon can blink once a second, as V1's ``clock_flash_seconds`` did.
 
Weather and alerts are optional and read from the data snapshot (``weather.current`` and
``weather.alerts``); if either is missing the board just draws without it.
 
Existing saved clock settings keep working: every field the old ClockConfig had is still here with
the same name, and the new fields all have defaults.
"""
from __future__ import annotations
 
from functools import lru_cache
from typing import Literal
 
from PIL import Image, ImageFont
from pydantic import BaseModel, Field
 
from ..render import Absolute, Img, Text, load_font, profile_for, render_tree
from ..render.text import text_size
from .base import BaseBoard, BoardContext
 
CLOCK_FONTS = ("clock", "score", "block", "ari", "gothic", "upheaval", "camels", "cute", "old", "pixelbold", "pl")
MIN_CLOCK = 8
WIDEST_TIME = "88:88"     # size for the widest time so the digits don't resize every minute
MERIDIEM_LETTERS = "APM"  # every letter the stacked tag can show: A/M or P/M
MERIDIEM_GAP = 2          # px between the time and the stacked tag
STACK_GAP = 1             # px between the two stacked letters
WIDEST_DATE = "AUG 88 8888"
WIDEST_WX = "-88F 100%"
LINE_GAP = 2                 # px between the time row, the date line and the weather line
ALERT_SIZE = 7               # px side of the corner alert square, as in V1
# V1 colours: warning red, watch yellow, advisory grey. Highest priority wins.
ALERT_COLORS = {"warning": (255, 0, 0), "watch": (255, 255, 0), "advisory": (169, 169, 169)}
ALERT_ORDER = ("warning", "watch", "advisory")
 
 
class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid", "title": "Clock"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    leading_zero: bool = Field(True, description="12h only: 08:05 rather than 8:05, as V1 showed it")
    font: Literal[CLOCK_FONTS] = Field("block", description="Typeface for the time")
    flash_colon: bool = Field(False, description="Blink the colon once a second")
    show_date: bool = Field(True, description="One-line date under the time")
    show_meridiem: bool = Field(True, description="Stacked A/M or P/M after the time (12h format only)")
    show_weather: bool = Field(True, description="Current temperature and humidity on the bottom line")
    show_alerts: bool = Field(True, description="Coloured corner square while a weather alert is active")
    color: tuple[int, int, int] = Field((0, 150, 150), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((255, 0, 255), description="Date, weather and AM/PM colour (RGB)")
 
 
def _stack_size(font: ImageFont.ImageFont) -> tuple[int, int]:
    """Width of the stacked tag's column (widest of A/P/M) and its total height (two letters + gap)."""
    lw = max(text_size(c, font)[0] for c in MERIDIEM_LETTERS)
    lh = text_size("M", font)[1]
    return lw, 2 * lh + STACK_GAP
 
 
@lru_cache(maxsize=32)
def _fonts(width: int, height: int, pad: int, family: str, meridiem: bool,
           show_date: bool, show_wx: bool) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """Largest time face of ``family`` whose whole block still fits; small text uses the profile's label face."""
    small = profile_for(width, height).label_font()
    for size in range(height, MIN_CLOCK - 1, -1):
        clock = load_font(family, size)
        tw, th = text_size(WIDEST_TIME, clock)
        if meridiem:
            lw, stack_h = _stack_size(small)
            tw += MERIDIEM_GAP + lw
            th = max(th, stack_h)
        block_w, block_h = tw, th
        for shown, widest in ((show_date, WIDEST_DATE), (show_wx, WIDEST_WX)):
            if shown:
                lw_, lh_ = text_size(widest, small)
                block_w, block_h = max(block_w, lw_), block_h + LINE_GAP + lh_
        if block_w <= width - 2 * pad and block_h <= height - 2 * pad:
            return clock, small
    return load_font(family, MIN_CLOCK), small
 
 
def _alert_color(alerts: list) -> tuple[int, int, int] | None:
    """Colour for the most severe active alert, or None. An alert may carry its own ``color`` (e.g. NWS)."""
    best, best_rank = None, len(ALERT_ORDER)
    for a in alerts or []:
        kind = str(a.get("type", "")).lower()
        if kind in ALERT_ORDER and ALERT_ORDER.index(kind) < best_rank:
            best, best_rank = a, ALERT_ORDER.index(kind)
    if best is None:
        return None
    c = best.get("color")
    return tuple(c)[:3] if c else ALERT_COLORS[str(best["type"]).lower()]
 
 
class ClockBoard(BaseBoard):
    key = "clock"
    title = "Clock"
    config_model = ClockConfig
 
    def render(self, ctx: BoardContext, cfg: ClockConfig) -> Image.Image:
        w, h = ctx.width, ctx.height
        pad = ctx.profile.pad
        now = ctx.now
        twelve = cfg.format == "12h"
        meridiem = twelve and cfg.show_meridiem
        color, dcolor = tuple(cfg.color), tuple(cfg.date_color)
 
        clock_font, small = _fonts(w, h, pad, cfg.font, meridiem, cfg.show_date, cfg.show_weather)
 
        # -- time, drawn as left / colon / right so the digits never shift when the colon blinks --
        fmt = ("%I:%M" if cfg.leading_zero else "%-I:%M") if twelve else "%H:%M"
        text = now.strftime(fmt)
        i = text.index(":")
        left, right = text[:i], text[i + 1:]
        tw, th = text_size(text, clock_font)
        lw = text_size(left, clock_font)[0]
        cw = text_size(":", clock_font)[0]
        rx = text_size(left + ":", clock_font)[0]
        rw = text_size(right, clock_font)[0]
        colon_on = not cfg.flash_colon or now.second % 2 == 0
 
        # -- stacked A/M or P/M, column sized for the widest letter so the time doesn't move --
        letters: list[Text] = []
        lcol = stack_h = extra = 0
        if meridiem:
            lcol, stack_h = _stack_size(small)
            extra = MERIDIEM_GAP + lcol
            letters = [Text(c, small, dcolor) for c in now.strftime("%p").upper()[:2]]
        row_h = max(th, stack_h)
 
        # -- optional lines under the time --
        date = wx = None
        if cfg.show_date:
            date = Text(now.strftime("%b %d %Y").upper(), small, dcolor)
        cur = (ctx.snapshot.get("weather.current") or {}) if cfg.show_weather else {}
        if cur.get("temp") is not None:
            unit = (cur.get("units") or {}).get("temp", "")
            hum = cur.get("humidity")
            wx = Text(f"{cur['temp']}{unit}" + (f" {hum}%" if hum is not None else ""), small, dcolor)
 
        lines = [n for n in (date, wx) if n is not None]
        total = row_h + sum(LINE_GAP + n.measure()[1] for n in lines)
        y = max(0, (h - total) // 2)
 
        block_w = tw + extra
        cx = (w - block_w) // 2
        ty = y + (row_h - th) // 2
        items = [(Text(left, clock_font, color), cx, ty, lw, th)]
        if colon_on:
            items.append((Text(":", clock_font, color), cx + lw, ty, cw, th))
        items.append((Text(right, clock_font, color), cx + rx, ty, rw, th))
 
        if letters:
            lx, ly = cx + tw + MERIDIEM_GAP, y + (row_h - stack_h) // 2
            for node in letters:
                nw, nh = node.measure()
                items.append((node, lx + (lcol - nw) // 2, ly, nw, nh))
                ly += nh + STACK_GAP
 
        y += row_h
        for node in lines:
            nw, nh = node.measure()
            y += LINE_GAP
            items.append((node, (w - nw) // 2, y, nw, nh))
            y += nh
 
        if cfg.show_alerts:
            ac = _alert_color(ctx.snapshot.get("weather.alerts") or [])
            if ac is not None:
                sq = Image.new("RGBA", (ALERT_SIZE, ALERT_SIZE), (*ac, 255))
                items.append((Img(sq), w - ALERT_SIZE, h - ALERT_SIZE, ALERT_SIZE, ALERT_SIZE))
 
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)
 
