"""Team summary — port of the old board: dark gradient column, cascading text rows
(RECORD / LAST / NEXT sections), big logo sliding in from the right with a looping sheen,
scroll if needed, hold, exit upward.

Geometry comes from a ``Layout`` picked by matrix size (128x64 original, 96x32 compact).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Img, render_node
from ...render.anim import quintic_out
from ...render.fx import chip, fit_logo, reflected_gradient
from ..teams import logo, team
from .common import fmt_date, fmt_time, local_time

WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
CASCADE_FRAMES = 4
ROW_SLIDE_FRAMES = 4
SCROLL_DELAY = 5.0
EXIT_PX_PER_FRAME = 3


@dataclass(frozen=True)
class Layout:
    fade_start: int         # header bars are solid to here...
    fade_end: int           # ...and transparent by here (must stay left of the logo's left edge)
    grad_w: int             # dark gradient column width
    grad_x: int             # and its x offset
    logo_w_frac: float      # logo max width as a fraction of panel width
    logo_h_frac: float      # logo max height as a fraction of panel height
    logo_cx_frac: float     # logo centre x as a fraction of panel width
    compact: bool           # trim rows so a short panel doesn't scroll forever


LAYOUT_128x64 = Layout(fade_start=46, fade_end=72, grad_w=60, grad_x=-10,
                       logo_w_frac=0.55, logo_h_frac=0.86, logo_cx_frac=0.83, compact=False)

# 96x32: smaller logo tucked right (left edge ~x=61), text column ~58px wide, fewer rows.
LAYOUT_96x32 = Layout(fade_start=38, fade_end=56, grad_w=46, grad_x=-6,
                      logo_w_frac=0.36, logo_h_frac=0.90, logo_cx_frac=0.82, compact=True)


def layout_for(width: int, height: int) -> Layout:
    if (width, height) == (96, 32):
        return LAYOUT_96x32
    return LAYOUT_96x32 if height <= 32 else LAYOUT_128x64


def _fade_mask(width: int, height: int, solid_until: int, gone_at: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    for x in range(width):
        if x <= solid_until:
            a = 255
        elif x >= gone_at:
            a = 0
        else:
            a = int(255 * (1 - (x - solid_until) / (gone_at - solid_until)))
        for y in range(height):
            px[x, y] = a
    return mask


class TeamSummaryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL team summary")
    scroll_speed: float = Field(5.0, ge=1, le=40)
    hold_seconds: float = Field(5.0, ge=0, le=20)
    sheen_seconds: float = Field(2.5, ge=0.5, le=10, description="Seconds per shimmer sweep across the logo")
    time_24h: bool = False


class TeamSummaryBoard(BaseBoard):
    key = "nhl.team_summary"
    title = "NHL team summary"
    config_model = TeamSummaryConfig
    requires = frozenset({"nhl.team_summary"})
    summary_key = "nhl.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        t = team(abbrev)
        return t.primary, t.text_on_primary

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        """Text lines under the RECORD header (sport-specific). The first line is the headline record."""
        return [f"{rec['wins']}-{rec['losses']}-{rec['otl']}  {rec['points']} PTS",
                f"GP {rec['gp']}  L10 {'-'.join(map(str, rec['l10']))}"]

    def __init__(self) -> None:
        self._teams: list[dict[str, Any]] = []
        self._built: dict[str, tuple[Image.Image, list[tuple[int, bool, int]], Image.Image]] = {}
        self._timeline: list[float] = []
        self._size = (0, 0)

    def enter(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> None:
        self._teams = list((ctx.snapshot.get(self.summary_key) or {}).values())
        self._built = {}
        self._size = (ctx.width, ctx.height)
        self._timeline = [self._seconds(s, ctx, cfg) for s in self._teams]

    # -- content --------------------------------------------------------------------

    def _rows(self, s: dict[str, Any], ctx: BoardContext, cfg: TeamSummaryConfig) -> list[tuple[Image.Image, bool]]:
        lay = layout_for(ctx.width, ctx.height)
        f6 = ctx.profile.label_font()
        primary, fg = self.team_colors(s["abbrev"])
        w = ctx.width
        rec = s["record"]

        def header(text: str) -> tuple[Image.Image, bool]:
            """Section bar in team colour, fading out before the logo so it never cuts through it."""
            bar = chip(text, f6, fg, primary, pad=(1, 1, w, 1)).crop((0, 0, w, 7))
            bar.putalpha(_fade_mask(w, 7, solid_until=lay.fade_start, gone_at=lay.fade_end))
            return bar, False

        def line(parts: list[tuple[str, tuple[int, int, int]]]) -> tuple[Image.Image, bool]:
            img = Image.new("RGBA", (w, 6), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = 0
            for text, color in parts:
                d.text((x, 0), text, font=f6, fill=color)
                x += d.textlength(text, font=f6) + 4
            return img, True

        streak = rec.get("streak", "")
        streak_color = GREEN if streak.startswith("W") else RED if streak.startswith("L") else WHITE
        record_lines = self._record_lines(rec)
        if lay.compact:
            record_lines = record_lines[:1]             # headline record only; GP / L10 line dropped
        rows = [header("RECORD")]
        rows += [line([(txt, WHITE)]) for txt in record_lines]
        rows += [line([("STREAK", WHITE), (streak, streak_color)]), header("LAST")]
        prev, nxt = s.get("prev_game"), s.get("next_game")
        if prev:
            result_color = GREEN if prev["result"] == "W" else RED
            score = f"{prev['score']}-{prev['opponent_score']}"
            if lay.compact:                             # one row: result, score, VS/AT opponent (no date)
                rows.append(line([(prev["result"], result_color), (score, WHITE),
                                  (f"{'VS' if prev['home'] else 'AT'} {prev['opponent']}", WHITE)]))
            else:
                rows.append(line([(f"{fmt_date(prev['date'])} {'VS' if prev['home'] else 'AT'} {prev['opponent']}", WHITE)]))
                rows.append(line([(prev["result"], result_color), (score, WHITE)]))
        else:
            rows.append(line([("---------", WHITE)]))
        rows.append(header("NEXT"))
        if nxt:
            start = local_time(nxt["start_time_utc"], ctx.now.tzinfo)
            rows.append(line([(f"{fmt_date(nxt['date'])} {'VS' if nxt['home'] else 'AT'} {nxt['opponent']}", WHITE)]))
            rows.append(line([(fmt_time(start, cfg.time_24h).upper(), WHITE)]))
        else:
            rows.append(line([("---------", WHITE)]))
        return rows

    def _build(self, s: dict[str, Any], ctx: BoardContext, cfg: TeamSummaryConfig):
        lay = layout_for(ctx.width, ctx.height)
        rows = self._rows(s, ctx, cfg)
        total = sum(r.height for r, _ in rows) + max(len(rows) - 1, 0)
        comp = Image.new("RGBA", (ctx.width, max(total, ctx.height)), (0, 0, 0, 0))
        y, meta = 0, []
        for img, animated in rows:
            comp.alpha_composite(img, (0, y))
            meta.append((y, animated, img.height))
            y += img.height + 1
        lg = fit_logo(self.logo_image(s["abbrev"]), int(ctx.width * lay.logo_w_frac), int(ctx.height * lay.logo_h_frac))
        return comp, meta, lg

    def _seconds(self, s, ctx, cfg) -> float:
        comp, meta, _ = self._get(s, ctx, cfg)
        fps = ctx.fps
        travel = max(comp.height - ctx.height, 0)
        return 0.3 + (len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES) / fps + SCROLL_DELAY + travel / cfg.scroll_speed + cfg.hold_seconds + (ctx.height + 4) / EXIT_PX_PER_FRAME / fps

    def _get(self, s, ctx, cfg):
        key = s["abbrev"]
        if key not in self._built:
            self._built[key] = self._build(s, ctx, cfg)
        return self._built[key]

    # -- playback ---------------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> Image.Image:
        if not self._teams or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        lay = layout_for(w, h)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        if not self._teams:
            return out.convert("RGB")
        t = ctx.elapsed
        idx = 0
        while idx < len(self._timeline) - 1 and t >= self._timeline[idx]:
            t -= self._timeline[idx]
            idx += 1
        s = self._teams[idx]
        comp, meta, lg = self._get(s, ctx, cfg)
        fps = ctx.fps
        logo_in = 0.3
        cascade_end = logo_in + (len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES) / fps
        travel = max(comp.height - h, 0)
        scroll_start = cascade_end + SCROLL_DELAY
        scroll_end = scroll_start + travel / cfg.scroll_speed
        exit_start = scroll_end + cfg.hold_seconds
        exit_px = int((t - exit_start) * fps) * EXIT_PX_PER_FRAME if t >= exit_start else 0
        # gradient column (left), text, logo (top)
        grad = reflected_gradient(lay.grad_w, h)
        if exit_px <= h:
            out.alpha_composite(grad, (lay.grad_x, -exit_px))
        offset = int(min(max(t - scroll_start, 0) * cfg.scroll_speed, travel))
        if t < cascade_end:
            frame_no = int((t - logo_in) * fps)
            for i, (y, animated, hh) in enumerate(meta):
                start = i * CASCADE_FRAMES
                if frame_no < start:
                    continue
                strip = comp.crop((0, y, w, y + hh))
                if animated and frame_no < start + ROW_SLIDE_FRAMES:
                    k = quintic_out((frame_no - start + 1) / ROW_SLIDE_FRAMES)
                    dy = int(hh * (1 - k))
                    strip = strip.crop((0, 0, w, hh - dy))
                    out.alpha_composite(strip, (0, y + dy))
                else:
                    out.alpha_composite(strip, (0, y))
        else:
            out.alpha_composite(comp.crop((0, offset, w, offset + h)), (0, -exit_px))
        # logo: slides in from the right over 0.3s (quintic)
        lx, ly = int(w * lay.logo_cx_frac) - lg.width // 2, (h - lg.height) // 2
        k = quintic_out(min(t / logo_in, 1.0))
        limg = render_node(Img(lg), t)
        out.paste(limg, (lx + int(lg.width * (1 - k)), ly - exit_px), limg)
        return out.convert("RGB")

    def done(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> bool:
        return bool(self._timeline) and ctx.elapsed >= sum(self._timeline)

    def auto_seconds(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> float | None:
        return sum(self._timeline) if self._timeline else None      # known once the board has been built

    def auto_items(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> tuple[int, str]:
        return len(ctx.snapshot.get(self.summary_key) or {}), "team"
