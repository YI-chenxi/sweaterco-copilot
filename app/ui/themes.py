"""Session-local palettes. Recolour the approved geometry without altering green CSS."""

import re
from colorsys import rgb_to_hls

MODES = ("day", "green", "night")
MODE_LABELS = {"day": "Daily mode", "green": "Eye comfort mode", "night": "Night mode"}
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# Achromatic ramps keep silver neutral and the night palette genuinely black.
_DAY_RAMP = (
    "#ffffff",
    "#f8f8f8",
    "#f0f0f0",
    "#e8e8e8",
    "#d6d6d6",
    "#bfbfbf",
    "#9d9d9d",
    "#797979",
    "#636363",
    "#4e4e4e",
    "#333333",
)
_NIGHT_RAMP = (
    "#1c1c1c",
    "#181818",
    "#262626",
    "#303030",
    "#505050",
    "#777777",
    "#b0b0b0",
    "#c8c8c8",
    "#dedede",
    "#e8e8e8",
    "#f5f5f5",
)
_LEVELS = (0.97, 0.93, 0.86, 0.80, 0.71, 0.61, 0.51, 0.43, 0.33, 0.24)
_SPECIAL = {
    "day": {
        "edf5f0": "#eeeeee",
        "eaf5f1": "#e4e4e4",
        "fff": "#ffffff",
        "fcfefd": "#fcfcfc",
        "f5fbf8": "#f5f5f5",
        "087f78": "#595959",
        "05615b": "#3f3f3f",
        "04635e": "#494949",
        "163c43": "#292929",
        "214844": "#414141",
        "547079": "#707070",
        "526f65": "#747474",
        "dcefe5": "#e3e3e3",
        "eaf4ed": "#efefef",
        "d7eee6": "#d9d9d9",
        "b6d6d0": "#c5c5c5",
        "9ac5b1": "#b4b4b4",
    },
    "night": {
        "edf5f0": "#080808",
        "eaf5f1": "#101010",
        "fff": "#1c1c1c",
        "fcfefd": "#161616",
        "f5fbf8": "#111111",
        "087f78": "#d0d0d0",
        "05615b": "#e0e0e0",
        "04635e": "#dedede",
        "163c43": "#f5f5f5",
        "214844": "#ebebeb",
        "547079": "#c8c8c8",
        "526f65": "#bebebe",
        "dcefe5": "#303030",
        "eaf4ed": "#252525",
        "d7eee6": "#292929",
        "b6d6d0": "#585858",
        "9ac5b1": "#747474",
        "e3f1e9": "#252525",
        "226c55": "#9acdb5",
        "128264": "#8bc5a9",
        "82baa3": "#8a8a8a",
        "f0f7f3": "#181818",
        "d5e5dc": "#4b4b4b",
        "d0e3d7": "#4b4b4b",
    },
}


def mode_name(value):
    return value if value in MODES else "green"


def _colour(match, mode):
    value = match.group()[1:].lower()
    if len(value) == 3:
        expanded = "".join(c * 2 for c in value)
    else:
        expanded = value[:6]
    if len(expanded) != 6:
        return match.group()
    if len(value) == 8:
        alpha = int(value[6:], 16)
        if expanded == "ffffff":
            return "#ffffff" + (f"{min(alpha, 25):02x}" if mode == "night" else value[6:])
        if expanded in {"347a5c", "123d54", "163c43", "087f78"}:
            # Preserve inset highlights and sculpted shadows in both palettes.
            base = "000000" if mode == "night" else "454545"
            return "#" + base + f"{min(200, round(alpha * (2.5 if mode == 'night' else 1))):02x}"
        return _colour(re.match(_COLOR, "#" + expanded), mode) + value[6:]
    special = _SPECIAL[mode]
    if value in special:
        return special[value]
    if expanded in special:
        return special[expanded]
    # Warm warning colours retain their meaning instead of becoming neutral grey.
    if expanded in {"905b20", "9e5b13", "dd9e49", "e8d9bf", "fffdf8"}:
        return (
            {
                "905b20": "#edbd78",
                "9e5b13": "#edbd78",
                "dd9e49": "#edbd78",
                "e8d9bf": "#67513a",
                "fffdf8": "#302a24",
            }.get(expanded)
            if mode == "night"
            else "#" + expanded
        )
    rgb = [int(expanded[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lightness = rgb_to_hls(*rgb)[1]
    index = next((i for i, level in enumerate(_LEVELS) if lightness >= level), 10)
    return (_DAY_RAMP if mode == "day" else _NIGHT_RAMP)[index]


def theme_styles(css, mode):
    """Only colour literals change; every radius, gradient stop and dimension stays intact."""
    mode = mode_name(mode)
    if mode == "green":
        return css
    return _COLOR.sub(lambda match: _colour(match, mode), css)


def native_styles(mode):
    """Theme native controls/portals that do not inherit the app's custom surfaces."""
    mode = mode_name(mode)
    if mode == "green":
        return ""
    from pathlib import Path

    css = Path(__file__).with_name("theme_native.css").read_text(encoding="utf-8")
    palette = {
        "day": {
            "SURFACE": "#ffffff",
            "SOFT": "#f4f4f4",
            "INK": "#292929",
            "MUTED": "#707070",
            "LINE": "#c5c5c5",
            "ACCENT": "#595959",
            "ON_ACCENT": "#ffffff",
            "GLOW": "#ffffffed",
            "SHADOW": "#45454514",
            "ACTIVE": "#d9d9d9",
            "SCHEME": "light",
            "SCROLL": "#bfbfbf",
            "BACKDROP": "#30303040",
        },
        "night": {
            "SURFACE": "#1c1c1c",
            "SOFT": "#141414",
            "INK": "#f5f5f5",
            "MUTED": "#c8c8c8",
            "LINE": "#585858",
            "ACCENT": "#d0d0d0",
            "ON_ACCENT": "#111111",
            "GLOW": "#ffffff14",
            "SHADOW": "#00000055",
            "ACTIVE": "#303030",
            "SCHEME": "dark",
            "SCROLL": "#777777",
            "BACKDROP": "#00000085",
        },
    }[mode]
    for name, value in palette.items():
        css = css.replace("__" + name + "__", value)
    if mode == "night":
        # The native data grid paints into canvas, so CSS text/background cannot reach it.
        css += '\n[data-testid="stDataFrame"] canvas{filter:invert(.95) grayscale(1)}'
        css += Path(__file__).with_name("theme_night.css").read_text(encoding="utf-8")
    else:
        css += '\n[data-testid="stDataFrame"] canvas{filter:grayscale(1)}'
        css += Path(__file__).with_name("theme_day.css").read_text(encoding="utf-8")
    return css


def table_for_mode(data, mode):
    """Keep source values intact while making night-grid body text match headings."""
    if mode_name(mode) != "night":
        return data
    import pandas as pd

    # Streamlit paints the native grid into canvas. These pre-filter colours
    # become near-white text on graphite after the night canvas filter.
    return pd.DataFrame(data).style.set_properties(
        **{"color": "#050505", "background-color": "#ededed"}
    )
