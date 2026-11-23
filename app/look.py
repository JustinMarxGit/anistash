"""Each person's look: colours, light/dark, background, fonts, shapes and motion.

A look is stored as JSON on the user (`users.look`); an empty value means "use the site's
default look", which admins can set (setting `default_look`). Everything that comes from the
browser goes through clean(), which keeps only known keys and allowed values, so a look can
never inject CSS or script.
"""

import json
import re

from . import db

HEX = re.compile(r"#[0-9a-f]{6}")

CHOICES = {
    ("mode",): ("system", "light", "dark"),
    ("font", "heading"): ("bricolage", "onest", "system", "serif", "rounded", "mono"),
    ("font", "body"): ("onest", "bricolage", "system", "serif", "rounded", "mono"),
    ("corners",): ("soft", "sharp", "round"),
    ("density",): ("comfortable", "compact"),
    ("posters",): ("m", "s", "l"),
    ("bg_image", "fit"): ("cover", "tile"),
    ("motion", "toggle"): ("fade", "instant"),
    ("motion", "page"): ("slide", "fade", "zoom", "side", "blur", "flip", "none"),
    ("motion", "hover"): ("lift", "tilt", "glow", "zoom", "none"),
    ("motion", "ambient"): ("grain", "drift", "petals", "snow", "stars", "none"),
    ("motion", "reduce"): ("system", "always", "never"),
    ("motion", "easing"): ("smooth", "springy", "snappy", "gentle"),
    ("motion", "enter"): ("rise", "fade", "pop", "blur", "slide", "none"),
    ("motion", "press"): ("squish", "deep", "none"),
    ("motion", "popups"): ("spring", "fade", "none"),
    ("motion", "burst"): ("petals", "confetti", "stars"),
    ("font", "weight"): ("heavy", "bold", "regular"),
    ("font", "case"): ("normal", "upper"),
    ("style", "shadows"): ("soft", "strong", "none"),
    ("style", "borders"): ("thin", "thick", "none"),
    ("style", "scrollbars"): ("normal", "thin", "hidden"),
    ("layout", "width"): ("normal", "narrow", "wide", "full"),
    ("layout", "nav"): ("top", "bottom", "side"),
    ("layout", "labels"): ("both", "icons", "text"),
    ("layout", "topbar"): ("sticky", "static"),
    ("layout", "shelves"): ("row", "grid"),
    ("layout", "titles"): ("show", "hide"),
    ("layout", "ranks"): ("show", "hide"),
    ("layout", "feature"): ("normal", "short", "tall"),
}
# The sections of Browse, in their default order; a layout can reorder and hide them
SECTIONS = ("feature", "search", "genres", "best", "foryou", "trending", "seasonal")
NUMBERS = {
    ("tint",): (0, 100),
    ("font", "size"): (90, 125),
    ("bg_image", "dim"): (0, 90),
    ("bg_image", "blur"): (0, 24),
    ("motion", "speed"): (50, 200),     # % of normal speed
    ("motion", "stagger"): (0, 120),    # ms between things appearing one after another
    ("motion", "amount"): (4, 40),      # how many petals, snowflakes or stars
}
FLAGS = {("glass",), ("bg_image", "on"), ("motion", "celebrate")}

DEFAULT = {
    "mode": "system",
    # colours: [] = AniStash's own vermilion; otherwise 1-4 colours (a combination from Sanzo Wada's
    # book, or your own), with which one is the accent and which tints the background (-1 = none)
    "palette": {"combo": None, "colors": [], "accent": 0, "tint": -1},
    "tint": 55,
    "bg_image": {"on": False, "dim": 45, "blur": 0, "fit": "cover"},
    "font": {"heading": "bricolage", "body": "onest", "size": 100, "weight": "heavy", "case": "normal"},
    "corners": "soft",
    "density": "comfortable",
    "posters": "m",
    "glass": True,
    "motion": {"toggle": "fade", "page": "slide", "hover": "lift", "ambient": "grain", "celebrate": True,
               "reduce": "system", "easing": "smooth", "enter": "rise", "press": "squish", "popups": "spring",
               "burst": "petals", "speed": 100, "stagger": 40, "amount": 12},
    "style": {"shadows": "soft", "borders": "thin", "scrollbars": "normal"},
    "layout": {"width": "normal", "nav": "top", "labels": "both", "topbar": "sticky", "shelves": "row",
               "titles": "show", "ranks": "show", "feature": "normal",
               "order": list(SECTIONS), "hidden": []},
}


def _get(d: dict, path: tuple):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


def _set(d: dict, path: tuple, value) -> None:
    for key in path[:-1]:
        d = d[key]
    d[path[-1]] = value


def clean(raw) -> dict:
    """A safe, complete look from whatever the browser sent (unknown or bad values fall back)."""
    out = json.loads(json.dumps(DEFAULT))
    if not isinstance(raw, dict):
        return out
    for path, allowed in CHOICES.items():
        value = _get(raw, path)
        if value in allowed:
            _set(out, path, value)
    for path, (low, high) in NUMBERS.items():
        value = _get(raw, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _set(out, path, int(max(low, min(high, value))))
    for path in FLAGS:
        value = _get(raw, path)
        if isinstance(value, bool):
            _set(out, path, value)
    layout = raw.get("layout")
    if isinstance(layout, dict):
        order = [x for x in layout.get("order") or [] if x in SECTIONS]
        order = list(dict.fromkeys(order))  # no repeats
        out["layout"]["order"] = order + [x for x in SECTIONS if x not in order]
        out["layout"]["hidden"] = [x for x in SECTIONS if x in (layout.get("hidden") or [])]
    palette = raw.get("palette")
    if isinstance(palette, dict):
        colors = [c.lower() for c in palette.get("colors") or [] if isinstance(c, str) and HEX.fullmatch(c.lower())][:4]
        combo = palette.get("combo")
        accent, tint = palette.get("accent", 0), palette.get("tint", -1)
        out["palette"] = {
            "combo": combo if isinstance(combo, int) and 1 <= combo <= 348 and colors else None,
            "colors": colors,
            "accent": accent if isinstance(accent, int) and 0 <= accent < max(1, len(colors)) else 0,
            "tint": tint if isinstance(tint, int) and -1 <= tint < len(colors) else -1,
        }
    return out


def from_legacy(theme_mode: str, accent: str) -> dict:
    """A look for people who chose light/dark or an accent before looks existed."""
    look = clean({"mode": theme_mode})
    if accent and HEX.fullmatch(accent):
        look["palette"] = {"combo": None, "colors": [accent], "accent": 0, "tint": -1}
    return look


def default_look() -> dict:
    stored = db.get_setting("default_look")
    return clean(stored) if stored else clean({})


def for_user(row) -> tuple[dict, bool]:
    """(look, is_their_own): their own look, or the site default when they have none."""
    if row is not None and row["look"]:
        try:
            return clean(json.loads(row["look"])), True
        except ValueError:
            pass
    return default_look(), False


def save(user_id: int, raw) -> dict:
    look = clean(raw)
    db.execute("UPDATE users SET look = ?, theme_mode = ?, accent = ? WHERE id = ?",
               (json.dumps(look), look["mode"], _accent(look), user_id))
    return look


def reset(user_id: int) -> None:
    db.execute("UPDATE users SET look = '', theme_mode = 'system', accent = '' WHERE id = ?", (user_id,))


def set_default(raw) -> dict:
    look = clean(raw)
    db.set_settings(default_look=look)
    return look


def _accent(look: dict) -> str:
    colors = look["palette"]["colors"]
    return colors[look["palette"]["accent"]] if colors else ""


def migrate() -> int:
    """Turns the light/dark and accent choices of v1.1–v1.3 into looks (once)."""
    rows = db.query("SELECT id, theme_mode, accent FROM users WHERE look = '' AND (theme_mode != 'system' OR accent != '')")
    for row in rows:
        db.execute("UPDATE users SET look = ? WHERE id = ?", (json.dumps(from_legacy(row["theme_mode"], row["accent"])), row["id"]))
    return len(rows)
