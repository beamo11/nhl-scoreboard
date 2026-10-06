"""Clock board — port of the old one: cyan time, magenta date above-left and year below-right,
looping diagonal sheen on the time. In 12h mode a stacked A/M or P/M tag follows the time, as in V1.
Date and year share the top line (date left, year right) so the time gets the rest of the height."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from PIL import Image, ImageFont
from pydantic import BaseModel, Field

from ..render import Absolute, Sheen, Text, load_font, profile_for, render_tree
from ..render.text import text_size
from .base import BaseBoard, BoardContext

CLOCK_FONTS = ("clock", "score", "block", "ari", "gothic", "upheaval", "camels", "cute", "old", "pixelbold", "pl")
MIN_CLOCK = 8
DATE_RATIO = 0.4          # date/year height relative to the time, as the old client had it
WIDEST_TIME = "88:88"     # size for the widest time so the digits don't resize every minute
WIDEST_DATE, WIDEST_YEAR = "AUG 88", "8888"
MERIDIEM_LETTERS = "APM"  # every letter the stacked tag can show: A/M or P/M
MERIDIEM_GAP = 2          # px between the time and the stacked tag
STACK_GAP = 1             # px between the two stacked letters
DATE_YEAR_GAP = 2         # min px between the date and the year on the top line


class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    font: Literal[CLOCK_FONTS] = Field("block", description="Typeface for the time")
    show_date: bool = True
    show_meridiem: bool = Field(True, description="Show stacked A/M or P/M after the time (12h format only)")
    color: tuple[int, int, int] = Field((0, 150, 150), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((255, 0, 255), description="Date/year/AM-PM colour (RGB)")


def _date_font(clock_size: int, small: ImageFont.ImageFont) -> ImageFont.ImageFont:
    """Date/year face scaled to the time. Below 9 px only the profile's label face is legible."""
    size = max(6, round(clock_size * DATE_RATIO))
    if size >= 15:
        return load_font("pixelbold", size)
    return load_font("pl", 12) if size >= 9 else small


def _stack_size(font: ImageFont.ImageFont) -> tuple[int, int]:
    """Width of the stacked tag's column (widest of A/P/M) and its total height (two letters + gap)."""
    lw = max(text_size(c, font)[0] for c in MERIDIEM_LETTERS)
    lh = text_size("M", font)[1]
    return lw, 2 * lh + STACK_GAP


@lru_cache(maxsize=32)
def _fonts(width: int, height: int, pad: int, show_date: bool, family: str, meridiem: bool) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """Largest face of ``family`` whose whole block (date + year line / time + stacked tag) still fits the panel."""
    small = profile_for(width, height).label_font()
    for size in range(height, MIN_CLOCK - 1, -1):
        clock = load_font(family, size)
        date = _date_font(size, small)
        tw, th = text_size(WIDEST_TIME, clock)
        if meridiem:
            lw, stack_h = _stack_size(date)
            tw += MERIDIEM_GAP + lw             # the tag sits beside the time...
            th = max(th, stack_h)               # ...and the row is as tall as the taller of the two
        block_w, block_h = tw, th
        if show_date:
            dw, dh = text_size(WIDEST_DATE, date)
            yw, yh = text_size(WIDEST_YEAR, date)
            block_w, block_h = max(tw, dw + DATE_YEAR_GAP + yw), th + max(dh, yh) + 1
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
        twelve = cfg.format == "12h"
        meridiem = twelve and cfg.show_meridiem
        fmt = "%-I:%M" if twelve else "%H:%M"
        clock_font, date_font = _fonts(w, h, pad, cfg.show_date, cfg.font, meridiem)
        time_node = Text(ctx.now.strftime(fmt), clock_font, tuple(cfg.color))
        tw, th = time_node.measure()

        # Stacked A/M or P/M in the date face; the column is sized for the widest letter so the
        # time doesn't shift between AM and PM.
        letters: list[Text] = []
        lw = stack_h = extra = 0
        if meridiem:
            lw, stack_h = _stack_size(date_font)
            extra = MERIDIEM_GAP + lw
            letters = [Text(c, date_font, tuple(cfg.date_color)) for c in ctx.now.strftime("%p").upper()[:2]]
        row_h = max(th, stack_h)

        # Top line: date at the left, year at the right
        date = year = None
        dw = yw = hh = 0
        if cfg.show_date:
            date = Text(ctx.now.strftime("%b %d").upper(), date_font, tuple(cfg.date_color))
            year = Text(ctx.now.strftime("%Y"), date_font, tuple(cfg.date_color))
            (dw, dh), (yw, yh) = date.measure(), year.measure()
            hh = max(dh, yh)

        time_w = tw + extra
        block_w = max(time_w, dw + DATE_YEAR_GAP + yw) if cfg.show_date else time_w
        cx = (w - block_w) // 2
        tx = cx + (block_w - time_w) // 2                     # the time (and tag) centred under the top line
        top = max(0, (h - (row_h + hh + (1 if cfg.show_date else 0))) // 2)
        ry = top + (hh + 1 if cfg.show_date else 0)           # top of the time/tag row
        cy = ry + (row_h - th) // 2                           # the time and the tag are each centred in it
        items = [(Sheen(time_node, period=3.0, band=max(14, th), strength=0, delay=1.0), tx, cy, tw, th)]
        if letters:
            lx, sy = tx + tw + MERIDIEM_GAP, ry + (row_h - stack_h) // 2
            ly = sy
            for node in letters:
                cw, ch = node.measure()
                items.append((node, lx + (lw - cw) // 2, ly, cw, ch))
                ly += ch + STACK_GAP
        if date is not None and year is not None:
            items.append((date, cx, top, dw, hh))
            items.append((year, cx + block_w - yw, top, yw, hh))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)
