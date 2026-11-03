"""Shared constants and small helpers."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAMS = ["England", "France", "Ireland", "Italy", "Scotland", "Wales"]
SLUG = {t: t.lower() for t in TEAMS}
SIX_NATIONS = set(TEAMS)          # the site only shows players whose current team is in this set
ALIASES = {"Springboks": "South Africa", "All Blacks": "New Zealand", "Pumas": "Argentina", "Los Pumas": "Argentina",
           "Wallabies": "Australia", "Flying Fijians": "Fiji", "Brave Blossoms": "Japan", "Eagles": "USA", "United States": "USA",
           "Georgia Lelos": "Georgia", "Azzurri": "Italy", "England Men": "England", "Wales Men": "Wales", "Scotland Men": "Scotland",
           "Ireland Men": "Ireland", "France Men": "France"}


def norm_team(name):
    name = (name or "").strip()
    return ALIASES.get(name, name)
FORWARDS = {"PROP", "HOOK", "LOCK", "BR"}
POSITIONS = ["BACK3", "CEN", "FH", "SH", "BR", "LOCK", "PROP", "HOOK"]

# starting shirt -> fantasy position group
SHIRT_POS = {1: "PROP", 2: "HOOK", 3: "PROP", 4: "LOCK", 5: "LOCK", 6: "BR", 7: "BR", 8: "BR",
             9: "SH", 10: "FH", 11: "BACK3", 12: "CEN", 13: "CEN", 14: "BACK3", 15: "BACK3"}
# bench shirt -> most likely group (23 is a utility back: refined by the player's own starts)
BENCH_POS = {16: "HOOK", 17: "PROP", 18: "PROP", 19: "LOCK", 20: "BR", 21: "SH", 22: "FH", 23: "BACK3"}


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text())


def rules() -> dict:
    return load_json(ROOT / "config" / "rules.json")
