"""Fantasy points under the (editable) rules in config/rules*.json. Unknown rule keys count as 0, so older rule sets still work."""
from __future__ import annotations

import math

from .common import FORWARDS

COMPONENTS = ["tries", "assists", "kicking", "carrying", "tackles", "steals", "discipline", "potm", "team"]
FRONT_ROW = {"PROP", "HOOK"}


def try_points(pos: str, R: dict) -> float:
    return R["points"]["try_forward"] if pos in FORWARDS else R["points"]["try_back"]


def scrums_for(pos: str, minutes: float, R: dict, team_scrums: float | None = None) -> float:
    """Scrums won while a front-row player is on the pitch (approximation: team scrums won per match x share of the match played)."""
    if pos not in FRONT_ROW:
        return 0.0
    a = R.get("assumptions", {})
    n = team_scrums if team_scrums is not None else a.get("scrums_won_per_match", 0.0)
    return n * minutes / a.get("match_minutes", 80)


def _n(x) -> float:
    return 0.0 if x is None or (isinstance(x, float) and math.isnan(x)) else x


def points_from_stats(s: dict, pos: str, R: dict, floor_metres: bool = True) -> dict:
    """Component breakdown of one stat line (counts) -> points. POTM and team result are added separately.

    Metres score 1 point per full 10m (19m = 1pt), so actual lines use the floor; expectations use floor_metres=False and apply their own correction.
    Missed kicks are not in the data: they are approximated as kick_miss_ratio misses per successful kick."""
    P = R["points"]
    miss = R.get("assumptions", {}).get("kick_miss_ratio", 0.0)
    g = lambda k: _n(s.get(k, 0))  # noqa: E731
    m = g("metres")
    metre_pts = math.floor(m / P["metres_per_point"]) if floor_metres else m / P["metres_per_point"]
    return {
        "tries": g("tries") * try_points(pos, R),
        "assists": g("assists") * P["assist"],
        "kicking": g("conv") * (P["conversion"] + miss * P.get("missed_conversion", 0)) + g("pen") * (P["penalty"] + miss * P.get("missed_penalty", 0))
                   + g("dg") * P["drop_goal"],
        "carrying": g("db") * P["defender_beaten"] + metre_pts + g("f2250") * P["fifty22"] + g("offloads") * P.get("offload", 0) + g("cb") * P.get("line_break", 0),
        "tackles": g("tackles") * P["tackle"] + g("missed") * P.get("tackle_missed", 0),
        "steals": g("tw") * P["breakdown_steal"] + g("ls") * P["lineout_steal"] + g("lw") * P.get("lineout_won", 0)
                  + g("interceptions") * P.get("interception", 0) + g("scrums") * P.get("scrum_won", 0),
        "discipline": g("pc") * P["penalty_conceded"] + g("yc") * P["yellow"] + g("rc") * P["red"] + g("err") * P.get("error", 0) + g("lineout_errors") * P.get("lineout_error", 0),
    }
