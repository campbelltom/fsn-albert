"""ESPN fallback: converts ESPN's public summary JSON into the same raw shape the RFU scraper writes.

Used (a) for matches the RFU scrape could not get and (b) never to overwrite RFU data. ESPN has no turnovers won
and no substitution minutes, so those are missing / estimated downstream (flagged on each player)."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta

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


def events(year: int, league: int) -> list[dict]:
    """Six Nations events for a year. Tries a few date-window formats; prints the exact response on errors."""
    import urllib.error
    last = None
    for q in (f"dates={year}",):
        url = f"{BASE.format(lg=league)}/scoreboard?{q}"
        try:
            ev = _get(url).get("events", [])
        except urllib.error.HTTPError as e:      # noqa: PERF203
            body = e.read()[:200].decode("utf8", "replace") if hasattr(e, "read") else ""
            print(f"ESPN {e.code} for {url}: {body}")
            last = e
            continue
        print(f"ESPN league {league} {q}: {len(ev)} events")
        if ev:
            return ev
    if last:
        raise last
    return []


def parse_summary(summ: dict, ev: dict, competition: str = "Six Nations") -> dict | None:
    comp = ev["competitions"][0]
    sides = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = sides["home"]["team"]["displayName"], sides["away"]["team"]["displayName"]
    played = ev.get("status", {}).get("type", {}).get("completed", False)
    out = {"source": "espn", "competition": competition, "season": int(ev["date"][:4]), "home": home, "away": away,
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


def fetch_season(year: int, out_dir, league: int, competition: str) -> int:
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for ev in events(year, league):
        f = out_dir / f"{league}_{ev['id']}.json"
        if f.exists() and json.loads(f.read_text()).get("score"):
            continue
        try:
            summ = _get(f"{BASE.format(lg=league)}/summary?event={ev['id']}")
            m = parse_summary(summ, ev, competition)
        except Exception as e:  # noqa: BLE001
            print("espn fail", ev.get("id"), e)
            continue
        if m:
            f.write_text(json.dumps(m, separators=(",", ":")))
            n += 1
    return n


def discover_leagues() -> list[tuple]:
    """Every rugby league ESPN knows (id, name): printed in the Actions log so more competitions can be added to config/espn_leagues.json."""
    for url in ("https://sports.core.api.espn.com/v2/sports/rugby/leagues?limit=300", "https://site.api.espn.com/apis/site/v2/sports/rugby"):
        try:
            d = _get(url)
        except Exception as e:  # noqa: BLE001
            print("league discovery failed:", url, e)
            continue
        if "items" in d:
            out = []
            for it in d["items"]:
                ref = it.get("$ref", "")
                try:
                    out.append((ref.rstrip("/").split("/")[-1].split("?")[0], _get(ref).get("name")))
                except Exception:  # noqa: BLE001
                    out.append((ref.rstrip("/").split("/")[-1].split("?")[0], "?"))
            return out
        lg = [(l.get("id"), l.get("name")) for s in d.get("sports", []) for l in s.get("leagues", [])]
        if lg:
            return lg
    return []
