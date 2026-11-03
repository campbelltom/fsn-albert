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
from fsnmodel.data import assign_positions, assign_rounds, load_matches  # noqa: E402
from fsnmodel.export import encrypt_json  # noqa: E402
from fsnmodel.evaluate import save_snapshots  # noqa: E402
from fsnmodel.model import project  # noqa: E402


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
        print("  %-22s %-9s %-5s %3d* start %.2f sub %.2f  exp/rd %.1f  %s" % (p["name"][:22], p["team"], p["pos"], p["price"], p["pStart"], p["pSub"], float(np.mean(p["exp"])), p["flags"]))
    print("flagged few caps:", sum("few caps" in p["flags"] for p in P), "| nan:", sum(1 for p in P if any(x != x for x in p["exp"])))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
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
    dirs = [d for d in (a.rfu, a.espn) if Path(d).exists()]
    hist = load_matches(*dirs, tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    if hist.empty:
        raise SystemExit("no match data found - run scripts/scrape_rfu.py first")
    hist = assign_rounds(hist)
    ov = load_json(ROOT / "config" / "overrides.json", {})
    pos = assign_positions(hist, ov)
    fx = load_json(ROOT / comp["fixtures"])
    asof = pd.Timestamp(datetime.now(timezone.utc).date())
    if (hist["source"] == "espn").any():
        n = hist[hist["source"] == "espn"]["match_key"].nunique()
        notes.append(f"{n} historical match(es) came from the ESPN fallback: sub minutes estimated, no turnovers-won data.")
    res = project(hist, pos, fx["fixtures"], R, asof, ov, scope_teams=set(comp["teams"]))
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
    seasons = sorted(hist["season"].unique().tolist())
    summarise(hist, res)
    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "season": fx["season"], "competition": comp["label"], "competitionKey": key, "history": {"seasons": seasons, "matches": int(hist["match_key"].nunique())},
        "rules": R, "notes": notes, **res,
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
