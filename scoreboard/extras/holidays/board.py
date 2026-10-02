"""Holiday countdown board: image on the left (when we have one), big day count and name on the right.

Short panels (height <= 32, e.g. 96x32) get a compact text column: the day count and its
label share one row, and the TODAY name uses a smaller font, so two-line names still fit.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, per_item
from ...imagecache import load as load_image
from ...render import Absolute, HBox, Img, Sheen, Slide, Text, VBox, fit_font, load_font, render_tree
from ...render.anim import quintic_out
from ...render.text import text_size, wrap_text

NUMBER = (80, 200, 255)
LABEL = (160, 170, 180)
NAME = (255, 255, 255)
TODAY_LABEL = (255, 160, 40)
TODAY_NAME = (255, 220, 100)
SEPARATOR = (40, 50, 60, 255)

COMPACT_MAX_H = 32          # panels this short or shorter use the compact column
COMPACT_TODAY_FONT = 8      # name font size for the TODAY case on compact panels
COMPACT_LABEL_GAP = 2       # between the day count and its label on the shared row


class CountdownConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Holiday countdown")
    seconds_per_holiday: float = Field(5.0, ge=2, le=30, description="How long each holiday shows when the playlist row leaves the seconds blank")
    max_holidays: int = Field(3, ge=1, le=10, description="How many upcoming holidays to cycle through")


def _image(path: str, size: int) -> Image.Image | None:
    """The picture the source resolved for this holiday, or None if it has gone away.

    Goes through imagecache so the decode is keyed on the file's mtime: replacing a
    picture has to take effect without a restart.
    """
    return load_image(Path(path), size)


class CountdownBoard(BaseBoard):
    key = "holidays.countdown"
    title = "Holiday countdown"
    config_model = CountdownConfig
    pace_unit = "holiday"
    requires = frozenset({"holidays.upcoming"})

    def __init__(self) -> None:
        self._items: list[dict] = []

    def _item_list(self, ctx: BoardContext, cfg: CountdownConfig) -> list[dict]:
        return list(ctx.snapshot.get("holidays.upcoming") or [])[: cfg.max_holidays]

    def enter(self, ctx: BoardContext, cfg: CountdownConfig) -> None:
        self._items = self._item_list(ctx, cfg)

    def done(self, ctx: BoardContext, cfg: CountdownConfig) -> bool:
        return ctx.elapsed >= per_item(ctx, cfg.seconds_per_holiday) * max(len(self._items), 1)

    def auto_seconds(self, ctx: BoardContext, cfg: CountdownConfig) -> float:
        return per_item(ctx, cfg.seconds_per_holiday) * max(len(self._item_list(ctx, cfg)), 1)

    def auto_items(self, ctx: BoardContext, cfg: CountdownConfig) -> tuple[int, str]:
        return len(self._item_list(ctx, cfg)), self.pace_unit

    def render(self, ctx: BoardContext, cfg: CountdownConfig) -> Image.Image:
        if not self._items:
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        compact = h <= COMPACT_MAX_H
        if not self._items:
            msg = "NO HOLIDAYS" if compact else "NO UPCOMING HOLIDAYS"      # the long form is ~100px wide
            return render_tree(Text(msg, ctx.profile.label_font(), LABEL), w, h)
        per = per_item(ctx, cfg.seconds_per_holiday)
        idx = min(int(ctx.elapsed // per), len(self._items) - 1)
        local = ctx.elapsed - idx * per
        item = self._items[idx]
        items = []
        text_x, text_w = 0, w
        img = _image(item["image"], h) if item.get("image") and w >= 96 else None
        if img is not None:
            items.append((Slide(Img(img), 0.5, "left", easing=quintic_out), 0, 0, img.width, h))
            sep_x = img.width + 2
            items.append((Img(Image.new("RGBA", (1, h - 8), SEPARATOR)), sep_x, 4, 1, h - 8))
            text_x, text_w = sep_x + 3, w - sep_x - 3
        big, small = load_font("pl", 12), ctx.profile.label_font()
        today = item["days"] == 0
        label = str(item.get("display") or item["name"]).upper()
        if today:
            rows = [Text("TODAY IS", small, TODAY_LABEL)]
            if compact:
                mid = load_font("pl", COMPACT_TODAY_FONT)
                rows += [Text(line, fit_font(line, "pl", text_w, COMPACT_TODAY_FONT), TODAY_NAME)
                         for line in wrap_text(label, mid, text_w, max_lines=2)]
            else:
                rows += [Text(line, fit_font(line, "pl", text_w, 12), TODAY_NAME) for line in wrap_text(label, big, text_w, max_lines=2)]
        else:
            number = Sheen(Text(str(item["days"]), big, NUMBER), period=3.0, band=10, strength=0.6, once=True, delay=0.6)
            word = "DAY TIL" if item["days"] == 1 else "DAYS TIL"
            if compact:
                # number and label share one row; fall back to the short label if "TIL" doesn't fit
                used = text_size(str(item["days"]), big)[0] + COMPACT_LABEL_GAP
                if used + text_size(word, small)[0] > text_w:
                    word = word.removesuffix(" TIL")
                rows = [HBox([number, Text(word, small, LABEL)], spacing=COMPACT_LABEL_GAP)]
            else:
                rows = [number, Text(word, small, LABEL)]
            rows += [Text(line, small, NAME) for line in wrap_text(label, small, text_w, max_lines=2)]
        col = VBox(rows, spacing=1)
        items.append((Slide(col, 0.4, "up", delay=0.1, easing=quintic_out), text_x, 0, text_w, h))
        return render_tree(Absolute(items), w, h, t=local)
