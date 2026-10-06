"""Matrix size presets for easy setup."""
from __future__ import annotations

from typing import NamedTuple

from .models import DisplayConfig


class MatrixPreset(NamedTuple):
    """A common LED matrix configuration preset."""
    name: str
    width: int
    height: int
    description: str
    chain: int = 1
    parallel: int = 1


# Common LED matrix sizes, ordered from smallest to largest
PRESETS = [
    MatrixPreset("32x16", 32, 16, "Small matrix (single 32x16 panel)"),
    MatrixPreset("64x32", 64, 32, "Standard matrix (two 32x16 panels chained)"),
    MatrixPreset("96x32", 96, 32, "Wide matrix (three 32x32 panels chained)", chain=3),
    MatrixPreset("128x64", 128, 64, "Large matrix (2x2 grid of 64x32 panels)", chain=2, parallel=2),
    MatrixPreset("96x64", 96, 64, "Wide large matrix (3 x 64x32 panels)", chain=3, parallel=2),
]

PRESET_MAP = {p.name: p for p in PRESETS}


def get_preset(name: str) -> DisplayConfig | None:
    """Return a DisplayConfig initialized from a preset name, or None if not found."""
    if name not in PRESET_MAP:
        return None
    p = PRESET_MAP[name]
    return DisplayConfig(
        width=p.width,
        height=p.height,
        chain=p.chain,
        parallel=p.parallel,
    )


def preset_for_config(cfg: DisplayConfig) -> str | None:
    """Return the preset name for a config, or None if it doesn't match any preset."""
    for preset in PRESETS:
        if (cfg.width == preset.width and cfg.height == preset.height
                and cfg.chain == preset.chain and cfg.parallel == preset.parallel):
            return preset.name
    return None
