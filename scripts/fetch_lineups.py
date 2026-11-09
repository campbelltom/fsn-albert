"""Named match-day squads from ESPN rosters (announced ~48h before kick-off). Also validates the parser on a finished match.

    python scripts/fetch_lineups.py [--competition nations-championship]"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import load_json, norm_team  # noqa: E402
from fsnmodel.espn import BASE, _get, events  # noqa: E402
from fsnmodel.data import international, load_matches  # noqa: E402
from fsnmodel.lineups import parse_rosters  # noqa: E402
from fsnmodel.schedule import all_fixtures, status  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", default=None)
    ap.add_argument("--out", default=str(ROOT / "data" / "lineups"))
    a = ap.parse_args()
    cfg = load_json(ROOT / "config" / "competitions.json")
    key = a.competition or cfg["active"]
    comp = cfg["competitions"][key]
    season = load_json(ROOT / comp["fixtures"])["season"]
    dirs = [d for d in (ROOT / "data" / "rfu", ROOT / "data" / "espn") if d.exists()]
    hist = load_matches(*dirs) if comp.get("finals_round") and dirs else None
    fixtures, _ = all_fixtures(comp, international(hist) if hist is not None else None, ROOT)   # includes the finals once they are set
    live = set(status(fixtures, datetime.now(timezone.utc))["live"])
    fixtures = [f for f in fixtures if f["round"] in live]                                      # finished rounds need no rosters
    fx = {"season": season, "fixtures": fixtures}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    league = comp["espn_league"]
    evs = events(fx["season"], league)
    diag = 0
    n = 0
    for ev in evs:
        c = ev["competitions"][0]
        sides = {x["homeAway"]: x["team"]["displayName"] for x in c["competitors"]}
        home, away = norm_team(sides.get("home")), norm_team(sides.get("away"))
        done = ev.get("status", {}).get("type", {}).get("completed", False)
        match = [f for f in fx["fixtures"] if {f["home"], f["away"]} == {home, away}]    # finals: ESPN may list the sides the other way round
        if not match and not (done and diag < 2):
            continue
        try:
            ros = parse_rosters(_get(f"{BASE.format(lg=league)}/summary?event={ev['id']}"))
        except Exception as e:  # noqa: BLE001
            print("lineup fetch failed", ev.get("id"), e)
            continue
        sizes = {t: (len(p), sum(1 for x in p if x["shirt"])) for t, p in ros.items()}
        if done and diag < 2:
            diag += 1
            print(f"parser check on finished match {home} v {away}: players/with shirt numbers {sizes}")
            continue
        if not match:
            continue
        for t, pl in ros.items():
            shirts = [x for x in pl if x["shirt"]]
            if len(shirts) >= 15:
                f = out / f"{key}_R{match[0]['round']}_{norm_team(t)}.json"
                f.write_text(json.dumps({"round": match[0]["round"], "team": norm_team(t), "source": "espn", "players": shirts}))
                n += 1
                print(f"lineup named: R{match[0]['round']} {norm_team(t)} ({len(shirts)} players)")
        if not ros:
            print(f"no roster yet: R{match[0]['round']} {home} v {away}")
    print(f"{n} team lineups written")


if __name__ == "__main__":
    main()
