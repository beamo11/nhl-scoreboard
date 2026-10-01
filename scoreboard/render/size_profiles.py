"""Size profiles define layout dimensions and scaling for different matrix sizes.

This allows boards to adapt their rendering based on the configured matrix width/height,
enabling optimal layouts for 32x16, 64x32, 96x32, and larger matrices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..config.models import DisplayConfig


@dataclass(frozen=True)
class SizeProfile:
    """Rendering profile for a specific matrix size or aspect ratio."""
    name: str
    width: int
    height: int
    
    # Logo/team indicator sizing
    logo_width: int
    logo_height: int
    
    # Score/status text sizing
    score_font_size: int
    status_font_size: int
    
    # Team name/abbrev display
    team_name_width: int  # max characters to display
    
    # Additional spacing hints
    padding: int
    spacing: int


# Standard size profiles for common matrix dimensions
PROFILES: dict[str, SizeProfile] = {
    "32x16": SizeProfile(
        name="32x16",
        width=32,
        height=16,
        logo_width=8,
        logo_height=8,
        score_font_size=8,
        status_font_size=5,
        team_name_width=3,
        padding=1,
        spacing=1,
    ),
    "64x32": SizeProfile(
        name="64x32",
        width=64,
        height=32,
        logo_width=16,
        logo_height=16,
        score_font_size=14,
        status_font_size=7,
        team_name_width=6,
        padding=2,
        spacing=2,
    ),
    "96x32": SizeProfile(
        name="96x32",
        width=96,
        height=32,
        logo_width=20,
        logo_height=20,
        score_font_size=16,
        status_font_size=8,
        team_name_width=9,
        padding=3,
        spacing=2,
    ),
    "128x64": SizeProfile(
        name="128x64",
        width=128,
        height=64,
        logo_width=28,
        logo_height=28,
        score_font_size=20,
        status_font_size=10,
        team_name_width=12,
        padding=4,
        spacing=3,
    ),
    "96x64": SizeProfile(
        name="96x64",
        width=96,
        height=64,
        logo_width=24,
        logo_height=24,
        score_font_size=18,
        status_font_size=9,
        team_name_width=10,
        padding=3,
        spacing=2,
    ),
}


def get_profile(display: DisplayConfig) -> SizeProfile:
    """Get the best matching profile for a display config, or create one dynamically."""
    key = f"{display.width}x{display.height}"
    if key in PROFILES:
        return PROFILES[key]
    
    # Fallback: generate a profile based on dimensions
    # Scale linearly from the closest known profile
    known_sizes = sorted(PROFILES.keys())
    aspect_ratio = display.width / display.height if display.height > 0 else 1.0
    
    # Find closest known size by area
    area = display.width * display.height
    closest = min(known_sizes, key=lambda k: abs(_profile_area(k) - area))
    base = PROFILES[closest]
    
    # Scale factors
    base_area = base.width * base.height
    scale = (area / base_area) ** 0.5  # scale by square root of area ratio
    
    return SizeProfile(
        name=key,
        width=display.width,
        height=display.height,
        logo_width=max(4, int(base.logo_width * scale)),
        logo_height=max(4, int(base.logo_height * scale)),
        score_font_size=max(6, int(base.score_font_size * scale)),
        status_font_size=max(4, int(base.status_font_size * scale)),
        team_name_width=max(2, int(base.team_name_width * scale * 0.9)),
        padding=max(1, int(base.padding * scale * 0.7)),
        spacing=max(1, int(base.spacing * scale * 0.7)),
    )


def _profile_area(key: str) -> int:
    """Get the pixel area of a profile key."""
    w, h = map(int, key.split("x"))
    return w * h


def profile_for_aspect(aspect_ratio: float) -> Literal["narrow", "standard", "wide"]:
    """Classify a display by aspect ratio: narrow (< 1.5), standard (1.5-2.2), wide (> 2.2)."""
    if aspect_ratio < 1.5:
        return "narrow"
    elif aspect_ratio < 2.2:
        return "standard"
    else:
        return "wide"
