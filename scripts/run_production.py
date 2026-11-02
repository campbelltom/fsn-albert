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
from fsnmodel.common import load_json, rules  # noqa: E402
from fsnmodel.data import assign_positions, assign_rounds, load_matches  # noqa: E402
from fsnmodel.export import encrypt_json  # noqa: E402
from fsnmodel.model import project  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--espn", default=str(ROOT / "data" / "espn"))
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    ap.add_argument("--fixtures", default=str(ROOT / "config" / "fixtures_2027.json"))
    ap.add_argument("--prices", default=str(ROOT / "config" / "prices.csv"))
    ap.add_argument("--no-encrypt", action="store_true")
    a = ap.parse_args()
    R = rules()
    notes: list[str] = []
    dirs = [d for d in (a.rfu, a.espn) if Path(d).exists()]
    hist = load_matches(*dirs, tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    if hist.empty:
        raise SystemExit("no match data found - run scripts/scrape_rfu.py first")
    hist = assign_rounds(hist)
    ov = load_json(ROOT / "config" / "overrides.json", {})
    pos = assign_positions(hist, ov)
    fx = load_json(a.fixtures)
    asof = pd.Timestamp(datetime.now(timezone.utc).date())
    if (hist["source"] == "espn").any():
        n = hist[hist["source"] == "espn"]["match_key"].nunique()
        notes.append(f"{n} historical match(es) came from the ESPN fallback: sub minutes estimated, no turnovers-won data.")
    res = project(hist, pos, fx["fixtures"], R, asof, ov)
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
    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "season": fx["season"], "history": {"seasons": seasons, "matches": int(hist["match_key"].nunique())},
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
