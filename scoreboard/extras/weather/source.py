
"""Weather from Environment and Climate Change Canada (MSC GeoMet city page weather).
 
Free and keyless. Publishes ``weather.current`` and ``weather.daily`` in the same shape the
boards already consume. EC publishes official forecaster-written 12-hour periods (Today,
Tonight, Friday, Friday night ...) with an icon code per period, so each day's icon comes from
the forecaster's own daytime summary rather than from the worst hour of the day.
"""
from __future__ import annotations
 
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo
 
import httpx
from pydantic import BaseModel, ConfigDict, Field
 
from ...config.models import ADVANCED
from ...data.source import SourceContext
 
log = logging.getLogger(__name__)
 
CITYPAGE = "https://api.weather.gc.ca/collections/citypageweather-realtime/items"
SEARCH_RADII = (0.15, 0.5, 1.5)   # degrees; widen until a city page is found near the location
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
 
# EC icon code -> (short label, icon key). Codes 00-29 are day, 30-39 are the night variants.
# Built from EC's published icon tables; check against
# https://collaboration.cmc.ec.gc.ca/cmc/cmos/public_doc/msc-data/citypage-weather/forecast_conditions_icon_code_descriptions_e.csv
ICONS: dict[int, tuple[str, str]] = {
    0: ("CLR", "clear"), 1: ("CLR", "clear"), 2: ("PCL", "partly"), 3: ("PCL", "partly"),
    4: ("PCL", "partly"), 5: ("PCL", "partly"), 6: ("SHR", "showers"), 7: ("SLT", "sleet"),
    8: ("SNW", "snow"), 9: ("STM", "storm"), 10: ("OVC", "cloudy"), 11: ("SHR", "showers"),
    12: ("RAN", "rain"), 13: ("RAN", "rain"), 14: ("SLT", "sleet"), 15: ("SLT", "sleet"),
    16: ("SNW", "snow"), 17: ("SNW", "snow"), 18: ("SNW", "snow"), 19: ("STM", "storm"),
    20: ("OVC", "cloudy"), 21: ("PCL", "partly"), 22: ("PCL", "partly"), 23: ("FOG", "fog"),
    24: ("FOG", "fog"), 25: ("SNW", "snow"), 26: ("SNW", "snow"), 27: ("SLT", "sleet"),
    28: ("DRZ", "showers"),
    30: ("CLR", "night"), 31: ("CLR", "night"), 32: ("PCL", "partly"), 33: ("PCL", "partly"),
    34: ("PCL", "partly"), 35: ("PCL", "partly"), 36: ("SHR", "showers"), 37: ("SLT", "sleet"),
    38: ("SNW", "snow"), 39: ("STM", "storm"),
    40: ("SNW", "snow"), 41: ("STM", "storm"), 42: ("STM", "storm"), 43: ("OVC", "cloudy"),
    44: ("FOG", "fog"), 45: ("FOG", "fog"), 46: ("STM", "storm"), 47: ("STM", "storm"),
    48: ("STM", "storm"),
}
 
 
class WeatherConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather")
    enabled: bool = True
    units: Literal["metric", "imperial"] = "metric"
    label: str = Field("", max_length=16, description="Name shown on the board (e.g. your town); blank = 'WEATHER'")
    refresh_seconds: int = Field(900, ge=300, le=3600, json_schema_extra=ADVANCED)
    forecast_days: int = Field(3, ge=1, le=5)
 
 
# ---------------------------------------------------------------- helpers
 
def _v(x: Any, lang: str = "en") -> Any:
    """Unwrap EC's bilingual {'en': .., 'fr': ..} and {'value': ..} wrappers to a plain value."""
    while isinstance(x, dict):
        if lang in x:
            x = x[lang]
        elif "value" in x:
            x = x["value"]
        else:
            return None
    return x
 
 
def _num(x: Any) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None                       # e.g. wind "calm"
 
 
def _temp(v: float | None, imperial: bool) -> int | None:
    return None if v is None else round(v * 9 / 5 + 32 if imperial else v)
 
 
def _speed(v: float | None, imperial: bool) -> int | None:
    return None if v is None else round(v * 0.6214 if imperial else v)
 
 
def _icon_info(code: Any) -> tuple[str, str]:
    try:
        return ICONS.get(int(code), ("---", "cloudy"))
    except (TypeError, ValueError):
        return ("---", "cloudy")
 
 
def _is_night(code: Any) -> bool:
    try:
        return 30 <= int(code) <= 39
    except (TypeError, ValueError):
        return False
 
 
def nearest_feature(features: list[dict], lat: float, lon: float) -> dict | None:
    def dist(f: dict) -> float:
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2:
            return math.inf
        return math.hypot(c[1] - lat, (c[0] - lon) * math.cos(math.radians(lat)))
    return min(features, key=dist, default=None)
 
 
def _period_date(name: str, today: date, prev: date | None, is_day: bool) -> date:
    """Map a period name ('Today', 'Tonight', 'Friday', 'Friday night') to a calendar date."""
    for i, wd in enumerate(WEEKDAYS):
        if name.startswith(wd):
            return today + timedelta(days=(i - today.weekday()) % 7)
    if prev is None or name in ("today", "tonight"):
        return today
    return prev + timedelta(days=1) if is_day else prev
 
 
# ---------------------------------------------------------------- normalisation
 
def build_daily(props: dict[str, Any], today: date, imp: bool) -> list[dict[str, Any]]:
    forecasts = (props.get("forecastGroup") or {}).get("forecasts") or []
    days: dict[str, dict[str, Any]] = {}
    prev: date | None = None
    for f in forecasts:
        temps = (f.get("temperatures") or {}).get("temperature") or []
        t = (temps[0] if isinstance(temps, list) and temps else temps) or {}
        if not isinstance(t, dict):
            t = {}
        name = str(_v((f.get("period") or {}).get("textForecastName")) or "").strip().lower()
        cls = str(_v(t.get("class")) or "").lower()
        is_night = cls == "low" or "night" in name or name == "tonight"
        d = _period_date(name, today, prev, not is_night)
        prev = d
        ab = f.get("abbreviatedForecast") or {}
        code = _v(ab.get("iconCode"))
        short, icon = _icon_info(code)
        pop = _num(_v(ab.get("pop")))
        temp = _temp(_num(_v(t.get("value") if "value" in t else t)), imp)
 
        row = days.setdefault(d.isoformat(), {
            "date": d.isoformat(), "hi": "--", "lo": "--", "pop": None,
            "sunrise": "", "sunset": "", "code": None, "short": "---", "desc": "", "icon": "cloudy",
            "_has_day": False,
        })
        if temp is not None:
            row["lo" if is_night else "hi"] = temp
        if pop is not None:
            row["pop"] = max(int(pop), row["pop"] or 0)
        # The day period supplies the icon; a night period only fills in a day with no day period (Tonight).
        if not is_night or not row["_has_day"]:
            if code is not None:
                row.update(code=code, short=short, icon=icon, desc=str(_v(ab.get("textSummary")) or ""))
        if not is_night:
            row["_has_day"] = True
    out = [days[k] for k in sorted(days)]
    for r in out:
        r.pop("_has_day", None)
    return out
 
 
def normalize(payload: dict[str, Any], cfg: WeatherConfig, today: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    imp = cfg.units == "imperial"
    props = payload.get("properties") or {}
    daily = build_daily(props, today, imp)
    cc = props.get("currentConditions") or {}
 
    code = _v(cc.get("iconCode"))
    today_row = next((r for r in daily if r["date"] == today.isoformat()), daily[0] if daily else None)
    if code is None and today_row:                 # some stations report no condition
        code = today_row["code"]
    short, icon = _icon_info(code)
    desc = str(_v(cc.get("condition")) or (today_row or {}).get("desc") or "Unknown")
 
    temp = _num(_v(cc.get("temperature")))
    feels = next((v for v in (_num(_v(cc.get("windChill"))), _num(_v(cc.get("humidex")))) if v is not None), temp)
    wind = cc.get("wind") or {}
    current = {
        "label": cfg.label or "WEATHER",
        "temp": _temp(temp, imp),
        "feels": _temp(feels, imp),
        "humidity": (None if _num(_v(cc.get("relativeHumidity"))) is None else round(_num(_v(cc.get("relativeHumidity"))))),
        "wind": _speed(_num(_v(wind.get("speed"))) or 0.0, imp),
        "gusts": _speed(_num(_v(wind.get("gust"))), imp),
        "wind_dir": _v(wind.get("bearing")),
        "precip": None,
        "is_day": not _is_night(code),
        "units": {"temp": "F" if imp else "C", "speed": "mph" if imp else "kmh"},
        "code": code, "short": short, "desc": desc, "icon": icon,
    }
    return current, daily[: cfg.forecast_days + 1]
 
 
# ---------------------------------------------------------------- source
 
class WeatherSource:
    key: ClassVar[str] = "weather"
    config_model: ClassVar[type[BaseModel]] = WeatherConfig
 
    async def _fetch(self, ctx: SourceContext, lat: float, lon: float) -> dict | None:
        for r in SEARCH_RADII:
            params = {"f": "json", "lang": "en", "limit": 20,
                      "bbox": f"{lon - r},{lat - r},{lon + r},{lat + r}"}
            resp = await ctx.http.get(CITYPAGE, params=params, follow_redirects=True)
            resp.raise_for_status()
            feat = nearest_feature(resp.json().get("features") or [], lat, lon)
            if feat:
                return feat
        return None
 
    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: WeatherConfig = ctx.config  # type: ignore[assignment]
            loc = ctx.location
            if not cfg.enabled or loc is None:
                if loc is None:
                    ctx.log.info("weather: no location configured; set latitude/longitude in Settings > Location")
                await ctx.sleep(60)
                continue
            try:
                try:
                    today = datetime.now(ZoneInfo(ctx.timezone)).date() if ctx.timezone else date.today()
                except Exception:                               # unknown tz name
                    today = date.today()
                feat = await self._fetch(ctx, loc[0], loc[1])
                if feat is None:
                    ctx.log.warning("weather: no Environment Canada city page near %s,%s (Canada only)", loc[0], loc[1])
                else:
                    current, daily = normalize(feat, cfg, today)
                    log.debug("weather: %s", [(d["date"], d["code"], d["icon"], d["hi"], d["lo"], d["pop"]) for d in daily])
                    ctx.publish(current, subkey="current")
                    ctx.publish(daily, subkey="daily")
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                ctx.log.warning("weather poll failed: %s", exc)
            await ctx.sleep(cfg.refresh_seconds)
