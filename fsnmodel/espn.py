"""ESPN fallback: converts ESPN's public summary JSON into the same raw shape the RFU scraper writes.

Used (a) for matches the RFU scrape could not get and (b) never to overwrite RFU data. ESPN has no turnovers won
and no substitution minutes, so those are missing / estimated downstream (flagged on each player)."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta

LEAGUE = 180659
BASE = "https://site.api.espn.com/apis/site/v2/sports/rugby/{lg}"
MAP = {  # espn stat name -> RFU-style key
    "tries": "attack:T", "metres": "attack:M", "runs": "attack:C", "defendersBeaten": "attack:DB", "cleanBreaks": "attack:CB",
    "passes": "attack:P", "offload": "attack:O", "turnoversConceded": "attack:TC", "tryAssists": "attack:TA", "points": "attack:PTS",
    "tackles": "defence:Tackles Attempted", "missedTackles": "defence:MT",
    "kicks": "kicking:K", "conversionGoals": "kicking:C", "penaltyGoals": "kicking:PG", "dropGoalsConverted": "kicking:DG",
    "lineoutsWon": "setplay:LW", "lineoutWonSteal": "setplay:LS",
    "penaltiesConceded": "discipline:PC", "redCards": "discipline:RC", "yellowCards": "discipline:YC",
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "fsn-albert/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def events(year: int) -> list[dict]:
    """Finished and upcoming Six Nations events in a calendar year (Jan-Apr window)."""
    d = _get(f"{BASE.format(lg=LEAGUE)}/scoreboard?dates={year}0101-{year}0430")
    return d.get("events", [])


def parse_summary(summ: dict, ev: dict) -> dict | None:
    comp = ev["competitions"][0]
    sides = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = sides["home"]["team"]["displayName"], sides["away"]["team"]["displayName"]
    played = ev.get("status", {}).get("type", {}).get("completed", False)
    out = {"source": "espn", "season": int(ev["date"][:4]), "home": home, "away": away,
           "date": ev["date"][:10], "venue": (comp.get("venue") or {}).get("fullName"),
           "teams": [home, away], "players": [], "estimated_minutes": True, "tackles_made": True,
           "score": {"home": int(float(sides["home"]["score"])), "away": int(float(sides["away"]["score"])),
                     "homeName": home, "awayName": away} if played else None}
    for blk in summ.get("boxscore", {}).get("players", []):
        team = blk["team"]["displayName"]
        for ath in blk["statistics"][0]["athletes"]:
            a = ath["athlete"]
            stats = {}
            for s in ath["statistics"][0]["stats"]:
                k = MAP.get(s["name"])
                if k:
                    stats[k] = float(s.get("value") or 0)
            jersey = a.get("jersey")
            out["players"].append({"id": "e" + str(a["id"]), "name": a.get("shortName") or a.get("displayName"),
                                   "team": team, "shirt": int(jersey) if str(jersey).isdigit() else None,
                                   "espn_pos": (a.get("position") or {}).get("abbreviation"), "stats": stats, "events": None})
    return out if out["players"] else None


def fetch_season(year: int, out_dir) -> int:
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for ev in events(year):
        f = out_dir / f"{ev['id']}.json"
        if f.exists() and json.loads(f.read_text()).get("score"):
            continue
        try:
            summ = _get(f"{BASE.format(lg=LEAGUE)}/summary?event={ev['id']}")
            m = parse_summary(summ, ev)
        except Exception as e:  # noqa: BLE001
            print("espn fail", ev.get("id"), e)
            continue
        if m:
            f.write_text(json.dumps(m, separators=(",", ":")))
            n += 1
    return n
