"""The main game board — a faithful port of the old client's 128x64 XL scoreboards,
plus a compact 96x32 layout.

Layers (bottom -> top): logos, centre gradient, teams-info / centre, indicators.
All geometry lives in a ``Layout``; the board picks one from the matrix size.
Entrances are box-local wipes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Absolute, Anchor, Box, HBox, Img, Slide, Text, load_font, render_tree
from ...render.anim import cubic_out, elastic_out, exponential_out, quartic_out
from ...render.fx import Chip, fit_logo, reflected_gradient
from ..teams import logo, team
from .common import GREY, WHITE, fmt_date, fmt_time, local_time, outcome_chip

BLACK = (0, 0, 0)
RED = (200, 0, 0)
GREEN = (0, 255, 0)

HOME_STAGGER = 0.4          # seconds the home logo's slide trails the away logo
NOT_PLAYED_WORDS = {"PPD": "POSTPONED", "CANCELLED": "CANCELLED"}   # a suspended game keeps its score

Rect = tuple[int, int, int, int]    # x, y, w, h


@dataclass(frozen=True)
class Layout:
    """Every coordinate the board uses. Rects are (x, y, w, h)."""
    # logos / backdrop
    logo_w: int
    logo_h: int
    logo_y: dict[str, int]
    home_logo_x: int
    gradient: Rect
    # fonts
    score_font: int
    block_font: int
    # teams info (abbrev chips + records under the logos); None = not shown
    away_chip: Rect | None
    home_chip: Rect | None
    away_record: Rect | None
    home_record: Rect | None
    # centre column
    strip: Rect                 # live: period chip + clock; final: result chip
    not_played_word: Rect
    score_away: Rect
    score_home: Rect
    hyphen: Rect
    # pregame
    date: Rect
    time: Rect
    vs: Rect
    pre_chip: Rect
    # shots on goal: away number, "SOG" chip, home number
    sog: tuple[Rect, Rect, Rect]
    # indicators
    pp_y: int
    pp_w: int
    pp_h: int
    pp_home_x: int
    en_y: int
    en_w: int
    en_h: int
    en_home_x: int
    en_label: str
    inter_bar: Rect


LAYOUT_128x64 = Layout(
    logo_w=55, logo_h=45,
    logo_y={"pregame": 7, "live": 9, "intermission": 9, "postgame": 9},
    home_logo_x=73,
    gradient=(34, 0, 60, 64),
    score_font=15, block_font=8,
    away_chip=(2, 45, 25, 11), home_chip=(101, 45, 25, 11),
    away_record=(3, 57, 40, 5), home_record=(85, 57, 40, 5),
    strip=(34, 14, 60, 7),
    not_played_word=(34, 30, 60, 6),
    score_away=(43, 25, 18, 12), score_home=(67, 25, 18, 12),
    hyphen=(62, 30, 4, 2),
    date=(39, 14, 50, 7), time=(39, 22, 50, 5), vs=(39, 31, 50, 12),
    pre_chip=(54, 4, 20, 7),
    sog=((42, 43, 14, 5), (58, 42, 13, 7), (73, 43, 14, 5)),
    pp_y=0, pp_w=45, pp_h=7, pp_home_x=82,
    en_y=57, en_w=37, en_h=7, en_home_x=91,
    en_label="EMPTY NET",
    inter_bar=(46, 0, 36, 3),
)

# 96x32: logos shrink and tuck to the edges, centre column is ~44px wide.
# Team chips and records are dropped (no room; the logos identify the teams).
LAYOUT_96x32 = Layout(
    logo_w=34, logo_h=30,
    logo_y={"pregame": 1, "live": 1, "intermission": 1, "postgame": 1},
    home_logo_x=62,
    gradient=(22, 0, 52, 32),
    score_font=10, block_font=6,
    away_chip=None, home_chip=None, away_record=None, home_record=None,
    strip=(26, 3, 44, 7),
    not_played_word=(26, 14, 44, 6),
    score_away=(27, 11, 18, 10), score_home=(50, 11, 18, 10),
    hyphen=(46, 15, 4, 2),
    date=(28, 3, 40, 7), time=(28, 11, 40, 5), vs=(28, 17, 40, 9),
    pre_chip=(0, 0, 20, 7),
    sog=((26, 25, 14, 5), (41, 24, 13, 7), (55, 25, 14, 5)),
    pp_y=25, pp_w=30, pp_h=7, pp_home_x=66,
    en_y=0, en_w=16, en_h=7, en_home_x=80,
    en_label="EN",
    inter_bar=(30, 0, 36, 2),
)

LAYOUTS: dict[tuple[int, int], Layout] = {
    (128, 64): LAYOUT_128x64,
    (96, 32): LAYOUT_96x32,
}


def layout_for(width: int, height: int) -> Layout:
    """Exact match, else the compact layout for short panels, else the big one."""
    if (width, height) in LAYOUTS:
        return LAYOUTS[(width, height)]
    return LAYOUT_96x32 if height <= 32 else LAYOUT_128x64


class GameConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL game board")
    show_sog: bool = Field(True, description="Show shots on goal")
    show_records: bool = Field(True, description="Show team records before/after the game (128x64 only)")
    time_24h: bool = False


class GameBoard(BaseBoard):
    key = "nhl.game"
    title = "NHL game"
    config_model = GameConfig
    requires = frozenset({"main_event"})
    sport = "nhl"

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}      # indicator key -> board time it appeared (for entrance replays)
        self._lay: Layout = LAYOUT_128x64      # set from the matrix size at the top of render()

    def enter(self, ctx: BoardContext, cfg: GameConfig) -> None:
        self._seen = {}
        self._lay = layout_for(ctx.width, ctx.height)

    # -- helpers -------------------------------------------------------------

    def _since(self, key: str, present: bool, now: float) -> float | None:
        """Board-time at which ``key`` became present (None when absent)."""
        if not present:
            self._seen.pop(key, None)
            return None
        return self._seen.setdefault(key, now)

    # -- sport hooks (NFL etc. override these; layout stays identical) --

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def side_colors(self, g: dict[str, Any], side: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        t = team(g[side]["abbrev"])
        return t.primary, t.text_on_primary

    def _logo_node(self, abbrev: str, from_dir: str, delay: float = 0.0, g: dict[str, Any] | None = None) -> Slide:
        """Logo wipes in (1.5s); the delay staggers the two sides."""
        lay = self._lay
        img = fit_logo(self.logo_image(abbrev, g or {}), lay.logo_w, lay.logo_h)
        return Slide(Img(img), duration=1.5, direction=from_dir, delay=delay, easing=exponential_out)

    def _score(self, value: int, align: str = "center") -> Slide:
        return Slide(Text(str(value), load_font("score", self._lay.score_font), WHITE), duration=1.0, direction="up", easing=elastic_out, h_align=align)

    def _chip(self, text: str, g: dict[str, Any], side: str, font):
        primary, fg = self.side_colors(g, side)
        return Chip(text, font, fg, primary)

    def _sog_row(self, g: dict[str, Any], f6) -> list:
        away, chip, home = self._lay.sog
        return [
            (Anchor(Text(str(g["away"]["sog"]), f6, WHITE), h="end"), *away),
            (Chip("SOG", f6, BLACK, WHITE), *chip),
            (Anchor(Text(str(g["home"]["sog"]), f6, WHITE), h="start"), *home),
        ]

    def _score_items(self, g: dict[str, Any]) -> list:
        lay = self._lay
        return [
            (self._score(g["away"]["score"], "end"), *lay.score_away),
            (Box(fill=(255, 255, 255, 255)), *lay.hyphen),
            (self._score(g["home"]["score"], "start"), *lay.score_home),
        ]

    # -- render ---------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: GameConfig) -> Image.Image:
        g = ctx.snapshot.get("main_event")
        if not g:
            return Image.new("RGB", (ctx.width, ctx.height))
        self._lay = lay = layout_for(ctx.width, ctx.height)
        phase = g["phase"]
        ly = lay.logo_y.get(phase, lay.logo_y["live"])
        gx, gy, gw, gh = lay.gradient
        items: list = [
            (self._logo_node(g["away"]["abbrev"], "left", g=g), 0, ly, lay.logo_w, lay.logo_h),
            (self._logo_node(g["home"]["abbrev"], "right", delay=HOME_STAGGER, g=g), lay.home_logo_x, ly, lay.logo_w, lay.logo_h),
            (Img(reflected_gradient(gw, gh)), gx, gy, gw, gh),
        ]
        if phase == "pregame":
            items += self._pregame(g, ctx, cfg)
        elif phase == "postgame":
            items += self._final(g, ctx, cfg)
        else:
            items += self._live(g, ctx, cfg)
        tree = Absolute(items)
        return render_tree(tree, ctx.width, ctx.height, t=ctx.elapsed)

    def _teams_info(self, g: dict[str, Any], cfg: GameConfig, f6) -> list:
        lay = self._lay
        if lay.away_chip is None or lay.home_chip is None:      # compact panels: logos only
            return []
        f8 = load_font("block", lay.block_font)
        stroke = (0, 0, 0, 220)
        (ap, af), (hp, hf) = self.side_colors(g, "away"), self.side_colors(g, "home")
        items = [
            (Slide(Chip(g["away"]["abbrev"], f8, af, ap, stroke=stroke), 0.8, "left", easing=exponential_out, h_align="start"), *lay.away_chip),
            (Slide(Chip(g["home"]["abbrev"], f8, hf, hp, stroke=stroke), 0.8, "right", easing=exponential_out, h_align="end"), *lay.home_chip),
        ]
        if cfg.show_records and lay.away_record and lay.home_record:
            items += [
                (Slide(Text(g["away"]["record"], f6, WHITE), 0.5, "up", easing=cubic_out, h_align="start"), *lay.away_record),
                (Slide(Text(g["home"]["record"], f6, WHITE), 0.5, "up", easing=cubic_out, h_align="end"), *lay.home_record),
            ]
        return items

    def _pregame(self, g, ctx, cfg) -> list:
        lay = self._lay
        f6 = ctx.profile.label_font()
        start = local_time(g["start_time_utc"], ctx.now.tzinfo)
        date = fmt_date(g["date"]).replace(" ", "")
        return self._teams_info(g, cfg, f6) + self._pre_chip(g, f6) + [
            (Chip(date, f6, BLACK, WHITE), *lay.date),
            (Text("TBD" if g.get("time_tbd") else fmt_time(start, cfg.time_24h).upper() or "TBD", f6, WHITE), *lay.time),
            (Text("VS", load_font("score", lay.score_font), WHITE), *lay.vs),
        ]

    def _pre_chip(self, g: dict[str, Any], font) -> list:
        """Small yellow PRE tag for preseason games (gameType 1)."""
        if g.get("type") != 1:
            return []
        return [(Chip("PRE", font, (0, 0, 0), (255, 200, 0)), *self._lay.pre_chip)]

    def _final(self, g, ctx, cfg) -> list:
        lay = self._lay
        f6 = ctx.profile.label_font()
        label = outcome_chip(g["outcome"])
        if label in NOT_PLAYED_WORDS:                   # postponed / cancelled: there is no score to show
            return self._teams_info(g, cfg, f6) + [
                (Chip(label, f6, WHITE, RED), *lay.strip),
                (Text(NOT_PLAYED_WORDS[label], f6, GREY), *lay.not_played_word),
            ]
        items = self._teams_info(g, cfg, f6) + [(Chip(label, f6, WHITE, RED), *lay.strip)]
        items += self._score_items(g)
        items += self._final_stats_row(g, cfg, f6)
        return items

    def _live(self, g, ctx, cfg) -> list:
        f6 = ctx.profile.label_font()
        t = ctx.elapsed
        period = "INT" if g["in_intermission"] else g["period"].upper()
        strip = HBox([Chip(period, f6, BLACK, WHITE), Text(g["clock"], f6, WHITE)], spacing=1)
        items = self._pre_chip(g, f6) + [(strip, *self._lay.strip)]
        items += self._score_items(g)
        items += self._live_stats_row(g, cfg, f6)
        items += self._indicators(g, t, f6)
        return items

    def _live_stats_row(self, g: dict[str, Any], cfg: GameConfig, f6) -> list:
        return self._sog_row(g, f6) if cfg.show_sog else []

    def _indicators(self, g: dict[str, Any], t: float, f6) -> list:
        """Power play / empty net / intermission; each replays its entrance when it appears."""
        lay = self._lay
        items: list = []
        code = g["powerplay"]["code"]
        pp_side = None if code == "ev" else ("away" if code[0] == "a" else "home")
        for side, align in (("away", "start"), ("home", "end")):
            home = side == "home"
            since = self._since(f"pp:{side}", pp_side == side, t)
            if since is not None:
                label = f"PP {code[1]}-{code[2]}"
                node = HBox([self._chip(label, g, side, f6), Text(g["powerplay"]["clock"], f6, WHITE)], spacing=1)
                x = lay.pp_home_x if home else 0
                items.append((Slide(node, 0.6, "up", delay=since, easing=quartic_out, h_align=align), x, lay.pp_y, lay.pp_w, lay.pp_h))
            en = self._since(f"en:{side}", bool(g["pulled_goalie"] & (1 if side == "away" else 2)), t)
            if en is not None:
                node = self._chip(lay.en_label, g, side, f6)
                x = lay.en_home_x if home else 0
                items.append((Slide(node, 0.6, "down", delay=en, easing=quartic_out, h_align=align), x, lay.en_y, lay.en_w, lay.en_h))
        inter = self._since("int", g["in_intermission"], t)
        if inter is not None:
            bx, by, bw, bh = lay.inter_bar
            items.append((Slide(Box(bw, bh, (*GREEN, 255)), 0.3, "down", delay=inter, easing=elastic_out), bx, by, bw, bh))
        return items

    def _final_stats_row(self, g: dict[str, Any], cfg: GameConfig, f6) -> list:
        return self._sog_row(g, f6) if cfg.show_sog else []
