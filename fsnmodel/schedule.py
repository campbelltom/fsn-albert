"""Where the competition has got to: which rounds are finished, which comes next, and (Nations Championship) the finals pairings.

Everything is decided from the clock and the fixture list, so the site moves on by itself:
  * a round is finished once its last match kicked off LOCK_HOURS ago (a match with no kick-off time counts as ending at midnight);
  * only unfinished rounds are projected;
  * after the last league round, finals weekend is built from the two hemisphere tables once all league results are in the data,
    or from config/finals.json if that exists (it wins, so pairings can always be set by hand)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

LOCK_HOURS = 3.0                       # a Test is about 100 minutes; the allowance also covers late data
UK = ZoneInfo("Europe/London")
WIN, DRAW, TRY_BONUS, LOSS_BONUS, LOSS_MARGIN, TRIES_FOR_BONUS = 4, 2, 1, 1, 7, 4
# place -> (round label, day, UK kick-off), as published for Finals Weekend at Twickenham; "north place k v south place k"
FINALS_SLOTS = {6: ("2026-11-27", "16:40"), 3: ("2026-11-27", "20:10"), 5: ("2026-11-28", "13:10"),
                2: ("2026-11-28", "16:40"), 4: ("2026-11-29", "13:10"), 1: ("2026-11-29", "16:40")}


def kickoff(f: dict) -> datetime:
    """Kick-off as an aware UTC datetime; without a time, the very end of the day."""
    d = datetime.fromisoformat(f["date"])
    h, m = (int(x) for x in f["ko"].split(":")) if f.get("ko") else (23, 59)
    return d.replace(hour=h, minute=m, tzinfo=UK).astimezone(timezone.utc)


def round_end(fixtures: list[dict], rnd: int) -> datetime:
    return max(kickoff(f) for f in fixtures if f["round"] == rnd) + timedelta(hours=LOCK_HOURS)


def status(fixtures: list[dict], now: datetime) -> dict:
    """{'finished': [rounds], 'live': [rounds still to project], 'current': round|None, 'next_kickoff': datetime|None}"""
    rounds = sorted({f["round"] for f in fixtures})
    finished = [r for r in rounds if round_end(fixtures, r) <= now]
    live = [r for r in rounds if r not in finished]
    nxt = None
    if live:
        up = [kickoff(f) for f in fixtures if f["round"] == live[0] and kickoff(f) > now]
        nxt = min(up) if up else None
    return {"finished": finished, "live": live, "current": live[0] if live else None, "next_kickoff": nxt}


def standings(hist: pd.DataFrame, comp_name: str, teams_by_hemi: dict[str, str]) -> pd.DataFrame:
    """Hemisphere tables from the played league matches in the history (needs columns match_key, team, opp, pf, pa, tries, competition)."""
    h = hist[hist["competition"] == comp_name]
    if h.empty:
        return pd.DataFrame()
    per = h.groupby(["match_key", "team"]).agg(opp=("opp", "first"), pf=("pf", "first"), pa=("pa", "first"), tries=("tries", "sum")).reset_index()
    rows = []
    for _, r in per.iterrows():
        win, draw = r["pf"] > r["pa"], r["pf"] == r["pa"]
        pts = WIN if win else DRAW if draw else 0
        pts += TRY_BONUS if r["tries"] >= TRIES_FOR_BONUS else 0
        pts += LOSS_BONUS if (not win and not draw and r["pa"] - r["pf"] <= LOSS_MARGIN) else 0
        rows.append(dict(team=r["team"], played=1, won=int(win), pts=pts, pf=r["pf"], pa=r["pa"], pd=r["pf"] - r["pa"]))
    t = pd.DataFrame(rows).groupby("team").sum(numeric_only=True).reset_index()
    t["hemi"] = t["team"].map(teams_by_hemi)
    t = t.sort_values(["hemi", "pts", "won", "pd", "pf"], ascending=[True, False, False, False, False]).reset_index(drop=True)
    t["place"] = t.groupby("hemi").cumcount() + 1
    return t


def finals_fixtures(hist: pd.DataFrame, comp: dict, league_fixtures: list[dict], round_no: int, root: Path) -> tuple[list[dict], str]:
    """-> (fixtures, note). Empty list while the league is not complete in the data."""
    manual = root / "config" / "finals.json"
    if manual.exists():
        fx = json.loads(manual.read_text())
        return [{**f, "round": round_no} for f in fx], "Finals pairings come from config/finals.json."
    name = comp.get("short", "Nations Championship")
    played = hist[hist["competition"] == name].drop_duplicates("match_key")
    need = 36                                              # 6 northern teams x 6 southern opponents
    if len(played) < need:
        return [], f"Finals pairings wait for the league: {len(played)} of {need} league results are in the data."
    tab = standings(hist, name, comp["hemisphere"])
    by = {(r["hemi"], r["place"]): r["team"] for _, r in tab.iterrows()}
    out = []
    for place, (day, ko) in sorted(FINALS_SLOTS.items(), key=lambda kv: kv[1]):
        n, s = by.get(("N", place)), by.get(("S", place))
        if n and s:
            out.append({"round": round_no, "date": day, "ko": ko, "home": n, "away": s, "final": place})
    return out, "Finals pairings are set from the league tables (4 for a win, 2 for a draw, 1 try bonus, 1 losing bonus; then wins, points difference)."


def all_fixtures(comp: dict, hist: pd.DataFrame | None, root: Path) -> tuple[list[dict], list[str]]:
    """League fixtures from config plus, once the league is over and the data is in, the finals round."""
    fx = json.loads((root / comp["fixtures"]).read_text())["fixtures"]
    notes: list[str] = []
    if comp.get("finals_round") and hist is not None:
        last = max(f["round"] for f in fx)
        if round_end(fx, last) <= datetime.now(timezone.utc):
            ff, note = finals_fixtures(hist, comp, fx, comp["finals_round"], root)
            notes.append(note)
            fx = fx + ff
    return fx, notes
