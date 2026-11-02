"""Fantasy points under the (editable) rules in config/rules.json."""
from __future__ import annotations

import math

from .common import FORWARDS

COMPONENTS = ["tries", "assists", "kicking", "carrying", "tackles", "steals", "discipline", "potm", "team"]


def try_points(pos: str, R: dict) -> float:
    return R["points"]["try_forward"] if pos in FORWARDS else R["points"]["try_back"]


def points_from_stats(s: dict, pos: str, R: dict, floor_metres: bool = True) -> dict:
    """Component breakdown of one stat line (counts) -> points. POTM and team result are added separately.

    Metres score 1 point per full 10m in the real game (19m = 1pt), so actual lines use the floor; expectations use
    floor_metres=False and apply their own continuity correction."""
    P = R["points"]
    m = s.get("metres", 0) or 0
    metre_pts = math.floor(m / P["metres_per_point"]) if floor_metres else m / P["metres_per_point"]
    tw = s.get("tw", 0)
    tw = 0 if tw is None or (isinstance(tw, float) and math.isnan(tw)) else tw
    return {
        "tries": s.get("tries", 0) * try_points(pos, R),
        "assists": s.get("assists", 0) * P["assist"],
        "kicking": s.get("conv", 0) * P["conversion"] + s.get("pen", 0) * P["penalty"] + s.get("dg", 0) * P["drop_goal"],
        "carrying": s.get("db", 0) * P["defender_beaten"] + metre_pts + s.get("f2250", 0) * P["fifty22"],
        "tackles": s.get("tackles", 0) * P["tackle"],
        "steals": tw * P["breakdown_steal"] + s.get("ls", 0) * P["lineout_steal"],
        "discipline": s.get("pc", 0) * P["penalty_conceded"] + s.get("yc", 0) * P["yellow"] + s.get("rc", 0) * P["red"],
    }
