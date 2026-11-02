"""Synthetic RFU-shaped match files so the pipeline and site can be tested offline.

    python tests/make_fixture.py data_test/rfu"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

TEAMS = ["England", "France", "Ireland", "Italy", "Scotland", "Wales"]
STRENGTH = {"England": 1.0, "France": 1.2, "Ireland": 1.15, "Italy": 0.8, "Scotland": 1.0, "Wales": 0.75}
FIX = {  # per season: 15 (round, home, away) fixtures
    "rounds": [(1, "Ireland", "England"), (1, "Scotland", "Italy"), (1, "France", "Wales"), (2, "Italy", "Ireland"), (2, "Scotland", "Wales"),
               (2, "England", "France"), (3, "Wales", "Ireland"), (3, "England", "Italy"), (3, "France", "Scotland"), (4, "Scotland", "Ireland"),
               (4, "Italy", "France"), (4, "Wales", "England"), (5, "Italy", "Wales"), (5, "England", "Scotland"), (5, "Ireland", "France")]}
DATES = {1: ("-02-05", "-02-06"), 2: ("-02-13", "-02-14"), 3: ("-02-20", "-02-21"), 4: ("-03-05", "-03-06"), 5: ("-03-13", "-03-13")}
RNG = random.Random(7)


def squad(team: str):
    """23 persistent players per team (ids stable across seasons)."""
    base = TEAMS.index(team) * 1000
    return [{"id": str(base + i), "name": f"{team[:3]}{i}", "shirt": i} for i in range(1, 24)]


def stat_line(shirt: int, mins: float, att: float):
    f = mins / 80.0
    fwd = shirt <= 8 or shirt in (16, 17, 18, 19, 20)
    r = RNG.random
    tries = 1 if r() < (0.10 if fwd else 0.20) * att * f else 0
    return {
        "attack:T": tries, "attack:M": int(RNG.gauss(25 if fwd else 45, 15) * f) if mins else 0, "attack:C": int(RNG.gauss(8 if fwd else 6, 3) * f) if mins else 0,
        "attack:DB": int(r() * 3 * f) if not fwd else int(r() * 1.5 * f), "attack:CB": int(r() * 1.5 * f), "attack:P": int(RNG.gauss(20 if shirt in (9, 10, 22, 21) else 4, 4) * f),
        "attack:O": int(r() * 2 * f), "attack:TC": int(r() * 2 * f), "attack:TA": 1 if r() < 0.10 * att * f else 0, "attack:PTS": tries * 5,
        "defence:Tackles Attempted": int(RNG.gauss(10 if fwd else 6, 3) * f) + 0, "defence:MT": int(r() * 2 * f), "defence:TW": 1 if r() < 0.12 * f else 0,
        "kicking:K": int(r() * 8) if shirt in (9, 10, 15, 22, 21) else 0, "kicking:C": 0, "kicking:PG": 0, "kicking:DG": 0,
        "setplay:TW": 0, "setplay:LW": int(r() * 4) if shirt in (4, 5, 6, 7, 8, 19, 20) else 0, "setplay:LS": 1 if r() < 0.03 and shirt in (4, 5, 19) else 0,
        "discipline:PC": 1 if r() < 0.25 * f else 0, "discipline:RC": 0, "discipline:YC": 1 if r() < 0.02 * f else 0,
    }


def make(out: Path, seasons=(2024, 2025, 2026)):
    out.mkdir(parents=True, exist_ok=True)
    for y in seasons:
        for rd, h, a in FIX["rounds"]:
            date = f"{y}{DATES[rd][0 if (h in ('Ireland', 'Scotland', 'England') and rd in (1, 4)) else 1]}"
            sc_h = max(3, int(RNG.gauss(24 * STRENGTH[h] / STRENGTH[a], 8))); sc_a = max(3, int(RNG.gauss(22 * STRENGTH[a] / STRENGTH[h], 8)))
            players = []
            for team, sc in ((h, sc_h), (a, sc_a)):
                att = STRENGTH[team]
                kicker = None
                for p in squad(team):
                    sh = p["shirt"]
                    # starters 1-15 mostly; bench players come on with some probability
                    if sh <= 15:
                        mins = 80 if RNG.random() > 0.55 else RNG.choice([52, 60, 65, 70])
                        ev = [] if mins == 80 else [{"type": "off", "min": mins}]
                    else:
                        on = RNG.choice([None, 45, 52, 60, 65, 70]) if sh <= 22 else None
                        mins = 0 if on is None else 80 - on
                        ev = [] if on is None else [{"type": "on", "min": on}]
                    s = stat_line(sh, mins, att)
                    if sh == 10:
                        s["kicking:C"] = max(0, sc // 7 - 1) if mins else 0; s["kicking:PG"] = (sc % 7) // 3 if mins else 0
                    players.append({"id": p["id"], "name": p["name"], "team": team, "shirt": sh, "events": ev, "stats": s})
            m = {"season": y, "home": h, "away": a, "date": None, "teams": [h, a], "score": {"home": sc_h, "away": sc_a, "homeName": h, "awayName": a},
                 "players": players, "source": "rfu"}
            # RFU date format
            import datetime as dt
            m["date"] = dt.datetime.strptime(date, "%Y-%m-%d").strftime("%A %d %B %Y")
            (out / f"{y}_{h}_{a}.json").write_text(json.dumps(m))


if __name__ == "__main__":
    make(Path(sys.argv[1] if len(sys.argv) > 1 else "data_test/rfu"))
    print("fixture written")
