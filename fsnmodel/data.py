"""Raw scraped matches -> one tidy row per player per match."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CLUB_COMPETITIONS, BENCH_POS, SHIRT_POS, norm_team

MATCH_MIN = 80
ERR_FROM_TC = 0.5     # RFU has turnovers conceded, not knock-ons/forward passes: assume half are handling errors
STAT_COLS = ["lw", "err", "tries", "assists", "metres", "carries", "db", "cb", "offloads", "tackles", "missed", "tw", "ls",
             "pc", "yc", "rc", "conv", "pen", "dg", "kicks"]


def parse_date(s: str | None):
    if not s:
        return pd.NaT
    for fmt in ("%A %d %B %Y", "%A %d %b %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except ValueError:
            pass
    return pd.NaT


def minutes_on_pitch(shirt: int | None, events: list | None, match_min: int = MATCH_MIN) -> tuple[float, bool]:
    """(minutes played, started) from shirt number and Opta on/off events (blood bins handled)."""
    started = bool(shirt and shirt <= 15)
    on = 0 if started else None
    total = 0.0
    for e in (events or []):
        t, m = e.get("type"), e.get("min")
        if m is None:
            continue
        if t == "off" and on is not None:
            total += max(0, m - on); on = None
        elif t == "on" and on is None:
            on = m
    if on is not None:
        total += max(0, match_min - on)
    return float(min(total, match_min)), started


def _g(s: dict, k: str, default=0.0):
    return s.get(k, default)


def match_rows(m: dict, est_minutes_start: float = 68.0, est_minutes_sub: float = 20.0) -> list[dict]:
    if not m.get("score"):
        return []
    sc = m["score"]
    m = {**m, "home": norm_team(m["home"]), "away": norm_team(m["away"])}
    d = parse_date(m.get("date"))
    comp = m.get("competition") or "Six Nations"
    rows = []
    for p in m["players"]:
        team = norm_team(p.get("team"))
        if team not in (m["home"], m["away"]):
            continue
        is_home = team == m["home"]
        s = p.get("stats", {})
        shirt = p.get("shirt")
        if p.get("events") is None and m.get("estimated_minutes"):       # ESPN: no sub minutes
            active = any(s.get(k, 0) for k in ("attack:C", "defence:Tackles Attempted", "attack:P", "kicking:K", "setplay:LW"))
            started = bool(shirt and shirt <= 15)
            mins = est_minutes_start if started else (est_minutes_sub if active else 0.0)
            est = True
        else:
            mins, started = minutes_on_pitch(shirt, p.get("events"))
            est = False
        scr = (m.get("team_stats") or {}).get(p.get("team"), {}).get("scrumsWon")
        tk = _g(s, "defence:Tackles Attempted")
        mt = _g(s, "defence:MT")
        made = tk if m.get("tackles_made") else tk  # decided below by rules flag (raw kept as attempted)
        rows.append(dict(
            season=m["season"], competition=comp, date=d, team=team, opp=m["away"] if is_home else m["home"], home=is_home,
            pf=sc["home"] if is_home else sc["away"], pa=sc["away"] if is_home else sc["home"],
            pid=str(p["id"]), name=p.get("name"), shirt=shirt, started=started, minutes=mins, est_minutes=est,
            source=m.get("source", "rfu"), tackles_raw=tk, tackles_made_flag=bool(m.get("tackles_made")),
            tries=_g(s, "attack:T"), assists=_g(s, "attack:TA"), metres=_g(s, "attack:M"), carries=_g(s, "attack:C"),
            db=_g(s, "attack:DB"), cb=_g(s, "attack:CB"), offloads=_g(s, "attack:O"), missed=mt,
            tw=_g(s, "defence:TW", np.nan), ls=_g(s, "setplay:LS"), pc=_g(s, "discipline:PC"),
            yc=_g(s, "discipline:YC"), rc=_g(s, "discipline:RC"), conv=_g(s, "kicking:C"), pen=_g(s, "kicking:PG"),
            dg=_g(s, "kicking:DG"), kicks=_g(s, "kicking:K"), espn_pos=p.get("espn_pos"), lw=_g(s, "setplay:LW"), scr_won=scr if scr is not None else np.nan,
            err=(_g(s, "attack:TKO") if "attack:TKO" in s else ERR_FROM_TC * _g(s, "attack:TC")),
        ))
    return rows


def load_matches(*dirs, tackles_include_missed: bool = True) -> pd.DataFrame:
    """RFU data wins; ESPN fills in matches RFU does not have."""
    raw: dict[tuple, dict] = {}
    for dpath in dirs:                       # order = priority: first dir wins
        for f in sorted(Path(dpath).glob("*.json")):
            if f.name.startswith("_"):
                continue
            m = json.loads(f.read_text())
            if not m.get("score") or not m.get("players"):   # an empty page must not block another source's stats
                continue
            # parsed, because sources write dates differently (RFU "Saturday 4 February 2023", ESPN "2023-02-04")
            key = (m["season"], parse_date(m.get("date")), frozenset((norm_team(m["home"]), norm_team(m["away"]))))
            raw.setdefault(key, m)
    rows = []
    for m in raw.values():
        rows += match_rows(m)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # tackles made: RFU's column is "Tackles Attempted" (made + missed); ESPN's is tackles made
    if tackles_include_missed:
        df["tackles"] = np.where(df["tackles_made_flag"], df["tackles_raw"], np.maximum(df["tackles_raw"] - df["missed"], 0))
    else:
        df["tackles"] = df["tackles_raw"]
    df = df.copy()
    df["match_key"] = df["date"].dt.strftime("%Y%m%d").fillna("nodate") + "_" + df[["team", "opp"]].apply(lambda r: "_".join(sorted(r)), axis=1)
    from .identity import resolve_ids
    return resolve_ids(df.sort_values(["date", "match_key"]).reset_index(drop=True))


def international(df: pd.DataFrame) -> pd.DataFrame:
    """The national-team matches in a history (club matches only feed player form)."""
    return df[~df["competition"].isin(CLUB_COMPETITIONS)]


def assign_positions(df: pd.DataFrame, overrides: dict | None = None) -> pd.Series:
    """Player id -> fantasy position group: modal starting position (recency weighted), else bench shirt.
    National-team matches decide where they exist, since a player's club position may not be his Test position."""
    ov = (overrides or {}).get("players", {})
    out = {}
    last = df["date"].max()
    for pid, g in df.groupby("pid"):
        if pid in ov and ov[pid].get("pos"):
            out[pid] = ov[pid]["pos"]; continue
        nat = international(g)
        g = nat if len(nat) else g
        st = g[g["started"] & g["shirt"].notna()]
        if len(st):
            w = 0.5 ** ((last - st["date"]).dt.days / 500.0)
            votes = {}
            for sh, wi in zip(st["shirt"].astype(int), w):
                votes[SHIRT_POS.get(sh, "BACK3")] = votes.get(SHIRT_POS.get(sh, "BACK3"), 0) + wi
            out[pid] = max(votes, key=votes.get); continue
        bn = g[g["shirt"].notna()]
        votes = {}
        for sh in bn["shirt"].astype(int):
            votes[BENCH_POS.get(sh, "BACK3")] = votes.get(BENCH_POS.get(sh, "BACK3"), 0) + 1
        out[pid] = max(votes, key=votes.get) if votes else "BACK3"
    return pd.Series(out)


def assign_rounds(df: pd.DataFrame) -> pd.DataFrame:
    """Round number within a season: matches on dates within 2 days of the round's first date."""
    df = df.copy()
    df["round"] = 0
    for s, g in df.groupby("season"):
        dates = sorted(g["date"].dropna().unique())
        rid, start, r = {}, None, 0
        for d in dates:
            if start is None or (pd.Timestamp(d) - start).days > 2:
                r += 1; start = pd.Timestamp(d)
            rid[d] = r
        df.loc[g.index, "round"] = g["date"].map(rid).fillna(0).astype(int)
    return df


def with_measured_scrums(R: dict, hist: pd.DataFrame, min_team_matches: int = 20) -> tuple[dict, str | None]:
    """Replace the scrums_won_per_match placeholder by the measured average (team scrums won per match) when there is enough data."""
    if "scr_won" not in hist or hist["scr_won"].notna().sum() == 0:
        return R, None
    t = hist.drop_duplicates(["match_key", "team"])
    t = t[t["scr_won"].notna()]
    if len(t) < min_team_matches:
        return R, None
    import copy
    R2 = copy.deepcopy(R)
    R2.setdefault("assumptions", {})["scrums_won_per_match"] = float(t["scr_won"].mean())
    return R2, f"Scrums won per team per match measured from {len(t)} team-matches: {t['scr_won'].mean():.1f} (front-row scrum points use this)."
