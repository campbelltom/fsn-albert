"""Data -> projections for every round of the next Six Nations -> (encrypted) JSON for the site.

    python scripts/run_production.py --rfu data/rfu --espn data/espn --out site/data [--no-encrypt]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import load_json  # noqa: E402
from fsnmodel.data import assign_positions, assign_rounds, international, load_matches, with_measured_scrums  # noqa: E402
from fsnmodel.export import encrypt_json  # noqa: E402
from fsnmodel.lineups import build_lineup_map, load_all  # noqa: E402
from fsnmodel.evaluate import save_snapshots  # noqa: E402
from fsnmodel.model import project  # noqa: E402
from fsnmodel.pool import load_pool, match_history, match_one  # noqa: E402


def summarise(hist, res) -> None:
    """Public-stat sanity summary for the Actions log."""
    import numpy as np
    P = res["players"]
    print("history rows", len(hist), "matches", hist["match_key"].nunique(), "by source", hist.drop_duplicates("match_key")["source"].value_counts().to_dict())
    tm = hist.groupby("match_key").agg(tk=("tackles", "sum"), mt=("missed", "sum"), tr=("tries", "sum"), mt_m=("metres", "sum"), pc=("pc", "sum"))
    print("per match totals (both teams) mean:", tm.mean().round(1).to_dict())
    print("minutes: starters mean %.1f, subs-with-minutes mean %.1f" % (hist[hist.started]["minutes"].mean(), hist[(~hist.started) & (hist.minutes > 0)]["minutes"].mean()))
    print("shirt missing:", int(hist["shirt"].isna().sum()), "| tw available:", int(hist["tw"].notna().sum()))
    by = {}
    for p in P:
        by.setdefault(p["pos"], []).append(np.mean(p["exp"]))
    print("players", len(P), {k: (len(v), round(float(np.mean(v)), 1)) for k, v in sorted(by.items())})
    for p in P[:12]:
        print("  %-22s %-9s %-5s %3d*  exp/rd %.1f  %s" % (p["name"][:22], p["team"], p["pos"], p["price"], float(np.mean(p["exp"])), p["flags"]))
    print("flagged few caps:", sum("few caps" in p["flags"] for p in P), "| nan:", sum(1 for p in P if any(x != x for x in p["exp"])))


def ingest_summary(hist, rfu_dir, lm, fx, pool, used, players) -> dict:
    """Plain-language picture of what went into the model, shown on the site's Data tab."""
    m = hist.drop_duplicates("match_key")
    rows = hist.drop_duplicates(["match_key", "pid"])
    def cnt(df, col):
        return {str(k): int(v) for k, v in df[col].value_counts().items()}
    comp = []
    for c, g in m.groupby("competition"):
        comp.append({"name": str(c), "matches": int(len(g)), "rfu": int((g["source"] == "rfu").sum()), "espn": int((g["source"] == "espn").sum()),
                     "from": str(g["date"].min().date()), "to": str(g["date"].max().date())})
    comp.sort(key=lambda x: -x["matches"])
    seasons = [{"season": str(k), "matches": int(v)} for k, v in sorted(m["season"].value_counts().items())]
    rfu_rows = rows[rows["source"] == "rfu"]
    n_rfu = max(len(rfu_rows), 1)
    cov = {
        "rows": int(len(rows)), "rfuRows": int(len(rfu_rows)),
        "realMinutesPct": round(100 * float((~rows["est_minutes"]).mean()), 1),
        "turnoversPct": round(100 * float(rows["tw"].notna().mean()), 1),
        "lineoutsPct": round(100 * float((rfu_rows["lw"] > 0).mean()), 1) if len(rfu_rows) else 0.0,
        "shirtPct": round(100 * float(rows["shirt"].notna().mean()), 1),
    }
    rd = {}
    for f in fx["fixtures"]:
        r = rd.setdefault(f["round"], {"round": f["round"], "teams": 0, "named": 0})
        for t in (f["home"], f["away"]):
            r["teams"] += 1
            r["named"] += int((f["round"], t) in lm)
    rfu_files = [f for f in Path(rfu_dir).glob("*.json") if not f.name.startswith("_")] if Path(rfu_dir).exists() else []
    miss = load_json(Path(rfu_dir) / "_missing.json", []) if Path(rfu_dir).exists() else []
    latest = m.sort_values("date").tail(5)
    return {
        "matches": int(len(m)), "firstMatch": str(m["date"].min().date()), "lastMatch": str(m["date"].max().date()),
        "bySource": cnt(m, "source"), "competitions": comp[:12], "seasons": seasons, "coverage": cov,
        "lineups": sorted(rd.values(), key=lambda x: x["round"]),
        "rfuFiles": len(rfu_files), "rfuMissing": len(miss) if isinstance(miss, list) else 0,
        "pool": None if pool is None else {"size": int(len(pool)), "matched": int(len(used)), "unmatched": int(len(pool) - len(used)),
                                           "inSite": int(len(players))},
        "latest": [{"date": str(r["date"].date()), "home": r["team"] if r["home"] else r["opp"], "away": r["opp"] if r["home"] else r["team"],
                    "score": f'{int(r["pf"])}-{int(r["pa"])}' if r["home"] else f'{int(r["pa"])}-{int(r["pf"])}', "source": r["source"]} for _, r in latest.iterrows()],
    }


def pool_report(res, pool, used) -> None:
    """Log how well the pool matched, and how projections line up with the game's own points so far."""
    import numpy as np
    P = res["players"]
    un = pool[~pool.index.isin(used)]
    print(f"pool: {len(pool)} players, {len(used)} matched to our data, {len(un)} unmatched")
    top = un[(un["pts"].fillna(0) >= 30) | (un["price"] >= 6)].sort_values("price", ascending=False)
    print("  unmatched pool players (pts>=30 or price>=6M):", "; ".join(f"{r['name']} ({r['grp']}/{r['hemi']}, {r['price']}M, {int(r['pts'] or 0)}pts)" for _, r in top.head(40).iterrows()))
    d = pd.DataFrame([{"exp": float(np.mean(p["exp"])), "pts": p["poolPts"], "price": p["price"]} for p in P if p.get("poolPts") is not None and p["poolPts"] > 0])
    if len(d) > 10:
        print(f"  rank correlation, projected points/round vs the game's own points so far (n={len(d)}): {d['exp'].corr(d['pts'], method='spearman'):.2f}; vs price: {d['exp'].corr(d['price'], method='spearman'):.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--super", default=str(ROOT / "data" / "super"), help="Super Rugby match files (scripts/scrape_super.py)")
    ap.add_argument("--espn", default=str(ROOT / "data" / "espn"))
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    ap.add_argument("--competition", default=None, help="key in config/competitions.json (default: the active one)")
    ap.add_argument("--snapshots", default=str(ROOT / "data" / "snapshots"))
    ap.add_argument("--prices", default=str(ROOT / "config" / "prices.csv"))
    ap.add_argument("--no-encrypt", action="store_true")
    a = ap.parse_args()
    cfg = load_json(ROOT / "config" / "competitions.json")
    key = a.competition or cfg["active"]
    comp = cfg["competitions"][key]
    R = load_json(ROOT / comp["rules"])
    notes: list[str] = []
    dirs = [d for d in (a.rfu, a.super, a.espn) if Path(d).exists()]
    hist = load_matches(*dirs, tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    if hist.empty:
        raise SystemExit("no match data found - run scripts/scrape_rfu.py first")
    hist = assign_rounds(hist)
    R, scr_note = with_measured_scrums(R, hist)
    if scr_note:
        notes.append(scr_note)
    ov = load_json(ROOT / "config" / "overrides.json", {})
    pos = assign_positions(hist, ov)
    fx = load_json(ROOT / comp["fixtures"])
    asof = pd.Timestamp(datetime.now(timezone.utc).date())
    if (hist["source"] == "espn").any():
        n = hist[hist["source"] == "espn"]["match_key"].nunique()
        notes.append(f"{n} historical match(es) came from the ESPN fallback: sub minutes estimated, no turnovers-won data.")
    lm, ls = build_lineup_map(load_all([ROOT / "data" / "lineups"], ROOT / "config" / "lineups.json"), international(hist), fx["fixtures"])
    if lm:
        notes.append("Named lineups used for: " + ", ".join(f"R{k[0]} {k[1]}" for k in sorted(lm)) + (f" ({ls['without_history']} players without history use position averages)" if ls["without_history"] else "") + ".")
    else:
        notes.append("No lineups named yet: starters are predicted from recent selection.")
    pool, mp, only = None, {}, None
    if comp.get("pool") and (ROOT / comp["pool"]).exists():
        pool = load_pool(ROOT / comp["pool"])
        mp, pinfo = match_history(international(hist), pool, comp["hemisphere"], pos)
        for pid, ix in mp.items():
            pos[pid] = pool.loc[ix, "grp"]                 # the game's own position slot decides where a player can be picked
        only = set(mp)
        notes.append(f"Official player pool: {len(mp)} history players matched to {len(pool)} game players (others are not in the game and are excluded).")
    res = project(hist, pos, fx["fixtures"], R, asof, ov, scope_teams=set(comp["teams"]), lineup_map=lm, only_pids=only)
    if pool is not None:
        used = set(mp.values())
        keep = []
        for p in res["players"]:
            ix = mp.get(p["id"])
            if ix is None and p.get("priceEst"):           # lineup debutants (no history): try the pool by name
                ix = match_one(pool, used, p["name"], p["team"], comp["hemisphere"], None)
                if ix is not None:
                    used.add(ix)
            if ix is None:
                continue
            row = pool.loc[ix]
            p["price"], p["priceEst"], p["own"], p["poolPts"], p["poolName"] = float(row["price"]), False, float(row["own_pct"]), (None if pd.isna(row["pts"]) else float(row["pts"])), row["name"]
            p["pos"] = row["grp"]
            keep.append(p)
        res["players"] = keep
        pool_report(res, pool, used)
    for w in save_snapshots(res, fx["fixtures"], key, asof, a.snapshots):
        notes.append(f"Snapshot taken: {w}")
    # official prices, once published: config/prices.csv with columns id,price (Opta player id)
    pcsv = Path(a.prices)
    if pcsv.exists():
        pr = pd.read_csv(pcsv, dtype={"id": str}).set_index("id")["price"]
        n = 0
        for p in res["players"]:
            if p["id"] in pr.index:
                p["price"], p["priceEst"] = float(pr[p["id"]]), False; n += 1
        notes.append(f"{n} official prices loaded from config/prices.csv.")
    else:
        notes.append("Prices are estimates (official prices are published when the game opens): add config/prices.csv (id,price) to use them.")
    seasons = sorted(international(hist)["season"].unique().tolist())
    summarise(hist, res)
    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "season": fx["season"], "competition": comp["label"], "competitionKey": key, "history": {"seasons": seasons, "matches": int(hist["match_key"].nunique())},
        "ingest": ingest_summary(hist, a.rfu, lm, fx, pool, used if pool is not None else set(), res["players"]), "rules": R, "hemisphere": comp.get("hemisphere"), "notes": notes, **res,
    }
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.no_encrypt:
        (out / "predictions.json").write_text(json.dumps(data, separators=(",", ":")))
    else:
        pw = os.environ.get("FSN_SITE_PASSWORD")
        if not pw:
            raise SystemExit("FSN_SITE_PASSWORD is not set (use --no-encrypt for local testing)")
        (out / "predictions.enc.json").write_text(json.dumps(encrypt_json(data, pw)))
    print(f"wrote {len(res['players'])} players, {hist['match_key'].nunique()} historical matches, seasons {seasons}")


if __name__ == "__main__":
    main()
