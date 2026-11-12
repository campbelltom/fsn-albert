"""Per-shirt profile of starters (per 80 minutes): what do 6, 7 and 8 (and every other shirt) actually do, and what is it worth in fantasy points?

    python scripts/role_table.py [--rules config/rules_nc.json]    -> markdown (also appended to $GITHUB_STEP_SUMMARY)"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import ROLE_START, load_json  # noqa: E402
from fsnmodel.data import assign_positions, load_matches, with_measured_scrums  # noqa: E402
from fsnmodel.evaluate import actual_points  # noqa: E402

COLS = ["tries", "assists", "metres", "db", "cb", "offloads", "tackles", "missed", "tw", "ls", "lw", "pc", "err"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(ROOT / "config" / "rules_nc.json"))
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--super", default=str(ROOT / "data" / "super"), help="Super Rugby match files (scripts/scrape_super.py)")
    ap.add_argument("--espn", default=str(ROOT / "data" / "espn"))
    a = ap.parse_args()
    R = load_json(a.rules)
    hist = load_matches(*[d for d in (a.rfu, a.super, a.espn) if Path(d).exists()], tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    R, _ = with_measured_scrums(R, hist)
    pos = assign_positions(hist)
    hist = hist.assign(pts=actual_points(hist, pos, R).values)
    s = hist[(hist["shirt"] >= 1) & (hist["shirt"] <= 15) & (hist["minutes"] >= 60)].copy()
    rows = []
    for sh, g in s.groupby("shirt"):
        m = g["minutes"].sum()
        r = {"shirt": int(sh), "role": ROLE_START.get(int(sh)), "n": len(g), "min": g["minutes"].mean()}
        for c in COLS:
            r[c] = g[c].fillna(0).sum() / m * 80 if c != "tw" or g["tw"].notna().any() else float("nan")
        r["fantasy pts/80"] = g["pts"].sum() / m * 80
        rows.append(r)
    t = pd.DataFrame(rows).round(2)
    lines = [f"# Starters by shirt (per 80 minutes; {len(s)} starts of 60+ minutes, {hist['match_key'].nunique()} matches)", "",
             "| " + " | ".join(t.columns) + " |", "|" + "---|" * len(t.columns)]
    for r in t.itertuples(index=False):
        lines.append("| " + " | ".join("" if (isinstance(x, float) and x != x) else str(x) for x in r) + " |")
    # 6 vs 7 vs 8 head to head
    sub = t[t["shirt"].isin([6, 7, 8])].set_index("shirt")
    if len(sub) == 3:
        lines += ["", "Spread between the three back-row shirts (max - min, per 80): " + ", ".join(f"{c} {sub[c].max() - sub[c].min():.2f}" for c in COLS + ["fantasy pts/80"])]
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    main()
