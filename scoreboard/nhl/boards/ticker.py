"""Score ticker — port of the old ScoretickerXL card: logos stacked left (away over home),
names/records/scores right, date chip + time (scheduled) or period/clock (live/final).
Each card's elements slide in; the board reports done after every game has shown."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, per_item
from ...render import Absolute, HBox, Img, Sheen, Slide, Text, load_font, render_tree
from ...render.anim import exponential_in_out, linear
from ...render.fx import Chip, chip, fit_logo
from ..teams import logo
from .common import fmt_date, fmt_time, local_time, outcome_chip
from ...render.text import text_size

WHITE = (255, 255, 255)
LIGHT = (200, 200, 200)
RED = (200, 0, 0)
BLACK = (0, 0, 0)


def _wrap(text: str, font, width: int) -> list[str]:
    """Word-wrap onto up to two lines that fit ``width`` (the old board's 80px name wrap)."""
    from ...render.text import text_size
    if text_size(text, font)[0] <= width:
        return [text]
    words = text.split()
    for i in range(len(words) - 1, 0, -1):
        first, rest = " ".join(words[:i]), " ".join(words[i:])
        if text_size(first, font)[0] <= width and text_size(rest, font)[0] <= width:
            return [first, rest]
    return [text]


class TickerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL ticker")
    seconds_per_game: float = Field(8.0, ge=2, le=30, description="How long each game shows when the playlist row leaves the seconds blank")
    time_24h: bool = False
    skip_finished: bool = Field(False, description="Only show upcoming and live games")


class TickerBoard(BaseBoard):
    key = "nhl.ticker"
    title = "NHL ticker"
    config_model = TickerConfig
    pace_unit = "game"
    requires = frozenset({"nhl.scores"})
    scores_key = "nhl.scores"
    empty_record = "0-0-0"          # what a pregame card prints when the feed has no record for a side

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def __init__(self) -> None:
        self._games: list[dict[str, Any]] = []

    def _game_list(self, ctx: BoardContext, cfg: TickerConfig) -> list[dict[str, Any]]:
        games = ctx.snapshot.get(self.scores_key) or []
        if cfg.skip_finished:
            games = [g for g in games if g["phase"] != "postgame"] or games
        return list(games)

    def enter(self, ctx: BoardContext, cfg: TickerConfig) -> None:
        self._games = self._game_list(ctx, cfg)

    def done(self, ctx: BoardContext, cfg: TickerConfig) -> bool:
        return ctx.elapsed >= per_item(ctx, cfg.seconds_per_game) * max(len(self._games), 1)

    def auto_seconds(self, ctx: BoardContext, cfg: TickerConfig) -> float:
        return per_item(ctx, cfg.seconds_per_game) * max(len(self._game_list(ctx, cfg)), 1)

    def auto_items(self, ctx: BoardContext, cfg: TickerConfig) -> tuple[int, str]:
        return len(self._game_list(ctx, cfg)), self.pace_unit

    def render(self, ctx: BoardContext, cfg: TickerConfig) -> Image.Image:
        if not self._games:
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        if not self._games:
            return render_tree(Text("NO GAMES TODAY", ctx.profile.label_font(), LIGHT), w, h)
        per = per_item(ctx, cfg.seconds_per_game)
        idx = min(int(ctx.elapsed // per), len(self._games) - 1)
        local = ctx.elapsed - idx * per
        # The list (order, count, which games) is fixed at enter so the run is stable; the
        # card itself is drawn from the freshest copy of that game, or on a long slate the
        # last cards showed scores a couple of minutes old while games were live.
        game = self._games[idx]
        fresh = next((g for g in ctx.snapshot.get(self.scores_key) or [] if g.get("id") == game.get("id")), game)
        return render_tree(Absolute(self._card(fresh, ctx, cfg)), w, h, t=local)

    def _date_label(self, g: dict[str, Any], ctx: BoardContext) -> str:
        return fmt_date(g["date"]).replace(" ", "")

    def _card(self, g: dict[str, Any], ctx: BoardContext, cfg: TickerConfig) -> list:
        if ctx.height < 48 and ctx.width >= 96:
            return self._compact_card(g, ctx, cfg)
        f7, f6 = load_font("camels", 7), ctx.profile.label_font()
        half = ctx.height // 2
        logo_w, win_h = 45, half - 1
        items = []
        for side, y, direction, delay, sheen_delay in (("away", 0, "down", 0.0, 0.0), ("home", half + 1, "up", 0.3, 1.4)):
            img = fit_logo(self.logo_image(g[side]["abbrev"], g), logo_w, win_h + 4)
            node = Sheen(Img(img), period=2.0, band=25, strength=0.6, once=True, delay=1.0 + sheen_delay)
            items.append((Slide(node, 1.0, direction, delay=delay, easing=exponential_in_out), 0, y, logo_w, win_h))
        pregame = g["phase"] == "pregame"
        name_w = ctx.width - 48 - (0 if pregame else 20)
        for side, top in (("away", 0), ("home", half + 1)):
            name = (g[side]["name"] if pregame else g[side]["abbrev"]).upper()
            lines = _wrap(name, f7, name_w)
            y0 = top + 9 if len(lines) == 1 else top + 3
            for i, line in enumerate(lines[:2]):
                items.append((Slide(Text(line, f7, WHITE), 0.4, "up", easing=linear, h_align="start"), 48, y0 + 8 * i, name_w, 8))
            if pregame:
                items.append((Slide(Text(g[side]["record"] or self.empty_record, f6, LIGHT), 0.4, "up", easing=linear, h_align="start"), 48, top + 21, 40, 5))
            else:
                items.append((Slide(Text(str(g[side]["score"]), f7, WHITE), 0.4, "up", easing=linear, h_align="end"), 111, top + 7, 16, 12))
        if pregame:
            date = chip(self._date_label(g, ctx), f6, BLACK, WHITE)
            # date chip + start time sit in the seam between the halves, clear of both name blocks
            items.append((Slide(Img(date), 0.4, "right", easing=linear, h_align="end"), 100, half - 8, 27, 7))
            start = local_time(g["start_time_utc"], ctx.now.tzinfo)
            when = "TBD" if g.get("time_tbd") else fmt_time(start, cfg.time_24h).upper()
            items.append((Slide(Text(when, f6, WHITE), 0.4, "right", easing=linear, h_align="end"), 90, half + 2, 37, 5))
        if g.get("type") == 1:
            items.append((Chip("PRE", f6, BLACK, (255, 200, 0)), 108, 1, 19, 7))
        if pregame:
            pass
        elif g["phase"] == "postgame":
            label = outcome_chip(g["outcome"], compact=True)
            items.append((Slide(Chip(label, f6, WHITE, RED), 0.4, "right", easing=linear, h_align="end"), 67, half - 4, 60, 8))
        else:
            period = "INT" if g["in_intermission"] else g["period"].upper()
            strip = HBox([Chip(period, f6, BLACK, WHITE), Text(g["clock"], f6, WHITE)], spacing=1)
            items.append((Slide(strip, 0.4, "right", easing=linear, h_align="end"), 67, half - 4, 60, 8))
        return items


    def _compact_card(self, g: dict[str, Any], ctx: BoardContext, cfg: TickerConfig) -> list:
        """96x32 card. Two 16px team rows on the left, one status column on the right.
 
        x 0-17    logo (16px square, one per row)
        x 19-..   team abbreviation
        right edge of x<=53   score (live/final) or record (pregame, only if it fits)
        x 56-95   status, centred vertically across both rows:
                    live     period chip over clock
                    pregame  date chip over start time
                    final    outcome chip
        """
        f7, f6 = load_font("camels", 7), ctx.profile.label_font()
        w, h = ctx.width, ctx.height
        row_h = h // 2
        logo_w = 18
        score_right = 53
        pregame = g["phase"] == "pregame"
        items = []
 
        for side, top, direction, delay, sheen_delay in (
            ("away", 0, "down", 0.0, 0.0),
            ("home", row_h, "up", 0.3, 1.4),
        ):
            img = fit_logo(self.logo_image(g[side]["abbrev"], g), logo_w, row_h)
            items.append((Slide(node, 1.0, direction, delay=delay, easing=exponential_in_out), 0, top, logo_w, row_h))
 
            abbrev = g[side]["abbrev"].upper()
            aw = text_size(abbrev, f7)[0]
            items.append((Slide(Text(abbrev, f7, WHITE), 0.4, "up", easing=linear, h_align="start"),
                          logo_w + 1, top + (row_h - 7) // 2, aw, 8))
 
            if pregame:
                # Records are a bonus: only drawn when they fit beside the abbreviation.
                rec = g[side]["record"] or self.empty_record
                rw = text_size(rec, f6)[0]
                if logo_w + 1 + aw + 3 + rw <= score_right:
                    items.append((Slide(Text(rec, f6, LIGHT), 0.4, "up", easing=linear, h_align="end"),
                                  score_right - rw, top + (row_h - 6) // 2, rw, 6))
            else:
                sc = str(g[side]["score"])
                sw = text_size(sc, f7)[0]
                items.append((Slide(Text(sc, f7, WHITE), 0.4, "up", easing=linear, h_align="end"),
                              score_right - sw, top + (row_h - 7) // 2, sw, 8))
 
        # -- status column, stacked and centred --
        rows = []   # (node, width, height)
        if pregame:
            date_img = chip(self._date_label(g, ctx), f6, BLACK, WHITE)
            rows.append((Img(date_img), date_img.width, date_img.height))
            start = local_time(g["start_time_utc"], ctx.now.tzinfo)
            when = "TBD" if g.get("time_tbd") else fmt_time(start, cfg.time_24h).upper()
            rows.append((Text(when, f6, WHITE), text_size(when, f6)[0], 6))
        elif g["phase"] == "postgame":
            lab = chip(outcome_chip(g["outcome"], compact=True), f6, WHITE, RED)
            rows.append((Img(lab), lab.width, lab.height))
        else:
            period = "INT" if g["in_intermission"] else g["period"].upper()
            pimg = chip(period, f6, BLACK, WHITE)
            rows.append((Img(pimg), pimg.width, pimg.height))
            rows.append((Text(g["clock"], f6, WHITE), text_size(g["clock"], f6)[0], 6))
 
        cx = (score_right + 3 + w) // 2                  # centre of the free column on the right
        gap = 2
        y = (h - (sum(r[2] for r in rows) + gap * (len(rows) - 1))) // 2
        for node, nw, nh in rows:
            items.append((Slide(node, 0.4, "right", easing=linear, h_align="start"), cx - nw // 2, y, nw, nh))
            y += nh + gap
 
        if g.get("type") == 1:                           # preseason marker, top of the status column
            pre = chip("PRE", f6, BLACK, (255, 200, 0))
            items.append((Img(pre), cx - pre.width // 2, 0, pre.width, pre.height))
        return items

