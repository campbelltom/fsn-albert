"""Backtest and snapshot evaluation: projected vs actual fantasy points (individual points only: no player-of-the-match, no team result).

walk_forward(): for each round of a competition, project using only earlier matches, then compare with what happened.
evaluate_snapshots(): compare projections frozen before a round (data/snapshots) with the actual results."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import BENCH_POS, SHIRT_POS
from .data import assign_positions
from .model import project
from .scoring import points_from_stats, scrums_for

SCORE_COLS = ["tries", "assists", "metres", "db", "tackles", "tw", "ls", "pc", "yc", "rc", "conv", "pen", "dg", "offloads", "cb", "missed", "lw", "err"]


def actual_points(hist: pd.DataFrame, pos: pd.Series, rules: dict) -> pd.Series:
    out = []
    for r in hist.itertuples(index=False):
        s = {c: getattr(r, c) for c in SCORE_COLS}
        p = pos.get(r.pid, "BACK3")
        scr = getattr(r, "scr_won", float("nan"))
        s["scrums"] = scrums_for(p, r.minutes, rules, team_scrums=None if scr != scr else scr)
        out.append(sum(points_from_stats(s, p, rules, floor_metres=True).values()))
    return pd.Series(out, index=hist.index)


def rounds_of(df: pd.DataFrame, gap_days: int = 2) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    dates = sorted(df["date"].dropna().unique())
    out, start, last = [], None, None
    for d in map(pd.Timestamp, dates):
        if start is None or (d - last).days > gap_days:
            if start is not None:
                out.append((start, last))
            start = d
        last = d
    if start is not None:
        out.append((start, last))
    return out


def _proj_to_rows(res: dict, rd_idx: int = 0) -> pd.DataFrame:
    rows = []
    for p in res["players"]:
        c = p["comp"][rd_idx]
        rows.append(dict(pid=p["id"], name=p["name"], team=p["team"], pos=p["pos"], exp=sum(v for k, v in c.items() if k not in ("potm", "team")),
                         ln=(p["fx"][rd_idx] or {}).get("ln"), emin=p["eMin"], caps=p["caps"]))
    return pd.DataFrame(rows)


def _baseline_recent(past: pd.DataFrame, pts_past: pd.Series, pids, teams, n: int = 3) -> dict:
    """Naive baseline: mean points over the team's last n matches, counting absences as 0."""
    out = {}
    pp = past.assign(pts=pts_past)
    tmatches = pp.drop_duplicates(["match_key", "team"])[["team", "match_key", "date"]].sort_values("date")
    by_team = {t: g.tail(n)["match_key"].tolist() for t, g in tmatches.groupby("team")}
    idx = pp.set_index(["pid", "match_key"])["pts"]
    for pid, t in zip(pids, teams):
        ks = by_team.get(t, [])
        out[pid] = float(np.mean([idx.get((pid, k), 0.0) for k in ks])) if ks else 0.0
    return out


def compare(df: pd.DataFrame) -> dict:
    """df columns: exp, act, base (optional), started (bool), round."""
    err = df["exp"] - df["act"]
    out = dict(n=int(len(df)), mae=float(err.abs().mean()), rmse=float(np.sqrt((err ** 2).mean())), bias=float(err.mean()),
               mean_act=float(df["act"].mean()))
    if "base" in df:
        out["mae_baseline"] = float((df["base"] - df["act"]).abs().mean())
        out["rmse_baseline"] = float(np.sqrt(((df["base"] - df["act"]) ** 2).mean()))
    sp, top, cap = [], [], []
    for _, g in df.groupby("round"):
        if len(g) > 10:
            sp.append(g["exp"].corr(g["act"], method="spearman"))
            k = min(15, len(g))
            top.append((g.nlargest(k, "exp")["act"].mean(), g.nlargest(k, "act")["act"].mean()))
            cap.append((g.nlargest(1, "exp")["act"].iloc[0], g["act"].max()))
    out["spearman"] = float(np.nanmean(sp)) if sp else float("nan")
    out["top15_actual"] = float(np.mean([t[0] for t in top])) if top else float("nan")
    out["top15_best_possible"] = float(np.mean([t[1] for t in top])) if top else float("nan")
    out["captain_actual"] = float(np.mean([c[0] for c in cap])) if cap else float("nan")
    out["captain_best_possible"] = float(np.mean([c[1] for c in cap])) if cap else float("nan")
    if "base" in df:
        bs = [g["base"].corr(g["act"], method="spearman") for _, g in df.groupby("round") if len(g) > 10]
        out["spearman_baseline"] = float(np.nanmean(bs)) if bs else float("nan")
    return out


def walk_forward(hist: pd.DataFrame, rules: dict, competition: str, overrides: dict | None = None, min_rounds_before: int = 0) -> tuple[pd.DataFrame, dict]:
    """Project each round of `competition` from earlier matches only, with each team's real named 23 (as announced before the match).
    Scored on the named 23: players left out are known before kick-off to score nothing. Returns (rows, coverage info)."""
    comp = hist[hist["competition"] == competition]
    rds = rounds_of(comp)
    pos_all = assign_positions(hist, overrides)
    rows, cov = [], []
    for i, (start, end) in enumerate(rds):
        if i < min_rounds_before:
            continue
        past = hist[hist["date"] < start]
        cur = hist[(hist["date"] >= start) & (hist["date"] <= end) & (hist["competition"] == competition)]
        if past.empty or cur.empty:
            continue
        fixtures = []
        for _, g in cur[cur["home"]].drop_duplicates("match_key").iterrows():
            fixtures.append({"round": 1, "date": str(g["date"].date()), "home": g["team"], "away": g["opp"]})
        if not fixtures:
            continue
        pos_past = assign_positions(past, overrides)
        lm = {}
        for t, g in cur.groupby("team"):
            g = g[g["shirt"].notna()]
            if len(g) >= 15:
                lm[(1, t)] = {r.pid: (int(r.shirt), r.name) for r in g.itertuples()}
        res = project(past, pos_past, fixtures, rules, start - pd.Timedelta(days=1), overrides, lineup_map=lm)
        pr = _proj_to_rows(res)
        pts_past = actual_points(past, pos_past, rules)
        pr["base"] = pr["pid"].map(_baseline_recent(past, pts_past, pr["pid"], pr["team"]))
        pts_cur = actual_points(cur, pos_all, rules)
        ac = cur.assign(act=pts_cur.values)[["pid", "act", "started", "minutes", "team"]]
        teams_playing = set(cur["team"])
        pr = pr[pr["team"].isin(teams_playing) & (pr["ln"] != "X")]
        m = pr.merge(ac[["pid", "act", "started", "minutes"]], on="pid", how="left")
        m["played"] = m["minutes"].fillna(0) > 0
        m["act"] = m["act"].fillna(0.0)
        m["started"] = m["started"].fillna(False).astype(bool)
        m["round"] = i + 1
        m["date"] = start
        rows.append(m)
        covered = ac["pid"].isin(pr["pid"]).mean()
        cov.append(dict(round=i + 1, start=str(start.date()), players_in_matches=int(len(ac)), with_projection=float(covered)))
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return df, {"coverage": cov}


def report(df: pd.DataFrame, info: dict, title: str) -> str:
    if df.empty:
        return f"## {title}\n\nNo rounds could be evaluated (not enough earlier matches).\n"
    lines = [f"## {title}", ""]
    allm = compare(df)
    st = compare(df[df["ln"] == "S"])
    pl = compare(df[df["played"]])
    fmt = lambda d, k: f"{d[k]:.2f}" if k in d and d[k] == d[k] else "-"  # noqa: E731
    lines += ["| subset | n | MAE | MAE baseline | RMSE | bias (proj-actual) | rank corr | rank corr baseline | actual mean |", "|---|---|---|---|---|---|---|---|---|"]
    for name, d in (("named 23", allm), ("named starters", st), ("players who played", pl)):
        lines.append(f"| {name} | {d['n']} | {fmt(d,'mae')} | {fmt(d,'mae_baseline')} | {fmt(d,'rmse')} | {fmt(d,'bias')} | {fmt(d,'spearman')} | {fmt(d,'spearman_baseline')} | {fmt(d,'mean_act')} |")
    lines += ["", f"Best 15 by projection averaged **{allm['top15_actual']:.1f}** actual points per round against {allm['top15_best_possible']:.1f} for the best possible 15; "
              f"the top-projected player scored {allm['captain_actual']:.1f} on average against {allm['captain_best_possible']:.1f} for the round's best.", "",
              "Coverage (share of players in the match squads that had a projection):", "", "| round | starts | players in squads | with projection |", "|---|---|---|---|"]
    for c in info["coverage"]:
        lines.append(f"| {c['round']} | {c['start']} | {c['players_in_matches']} | {c['with_projection']*100:.0f}% |")
    by = df.assign(err=df["exp"] - df["act"]).groupby("pos").agg(n=("exp", "size"), exp=("exp", "mean"), act=("act", "mean"), bias=("err", "mean"))
    lines += ["", "By position (mean projected vs actual):", "", "| position | n | projected | actual | bias |", "|---|---|---|---|---|"]
    for p, r in by.iterrows():
        lines.append(f"| {p} | {int(r['n'])} | {r['exp']:.1f} | {r['act']:.1f} | {r['bias']:+.1f} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- snapshots
def snapshot_rows(res: dict, rd_idx: int) -> list[dict]:
    d = _proj_to_rows(res, rd_idx)
    return d.to_dict("records")


def save_snapshots(res: dict, fixtures: list[dict], comp_key: str, today: pd.Timestamp, out_dir) -> list[str]:
    """Freeze projections per round: overwrite until the round's first fixture date, then leave the file alone."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, r in enumerate(res["rounds"]):
        first = min(pd.Timestamp(f["date"]) for f in fixtures if f["round"] == r["round"])
        f = out_dir / f"{comp_key}_R{r['round']}.json"
        if today >= first and f.exists():
            continue                                     # round started: frozen
        if today >= first + pd.Timedelta(days=1):
            continue                                     # too late to be a genuine pre-round snapshot
        f.write_text(json.dumps({"competition": comp_key, "round": r["round"], "first_date": str(first.date()), "taken": str(today.date()),
                                 "matches": r["matches"], "players": snapshot_rows(res, i)}))
        written.append(f.name)
    return written


def evaluate_snapshots(snap_dir, hist: pd.DataFrame, rules: dict, overrides: dict | None = None) -> tuple[pd.DataFrame, dict]:
    pos = assign_positions(hist, overrides)
    acts = actual_points(hist, pos, rules)
    h = hist.assign(act=acts.values)
    rows, cov = [], []
    for f in sorted(Path(snap_dir).glob("*.json")):
        s = json.loads(f.read_text())
        first = pd.Timestamp(s["first_date"])
        teams = {m["home"] for m in s["matches"]} | {m["away"] for m in s["matches"]}
        last = max(pd.Timestamp(m["date"]) for m in s["matches"])
        cur = h[(h["date"] >= first - pd.Timedelta(days=1)) & (h["date"] <= last + pd.Timedelta(days=1)) & h["team"].isin(teams)]
        if cur.empty:
            continue                                    # round not played / not in the data yet
        pr = pd.DataFrame(s["players"])
        m = pr.merge(cur[["pid", "act", "started", "minutes"]], on="pid", how="left")
        m["played"] = m["minutes"].fillna(0) > 0
        m["act"] = m["act"].fillna(0.0)
        m["started"] = m["started"].fillna(False).astype(bool)
        m["round"] = s["round"]
        rows.append(m)
        cov.append(dict(round=s["round"], start=s["first_date"], players_in_matches=int(len(cur)), with_projection=float(cur["pid"].isin(pr["pid"]).mean())))
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), {"coverage": cov}
