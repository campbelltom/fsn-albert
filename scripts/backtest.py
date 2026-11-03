"""Walk-forward backtest and snapshot evaluation.

    python scripts/backtest.py --competition "Nations Championship" [--snapshots data/snapshots] [--rules config/rules_nc.json]
Writes markdown to stdout and to $GITHUB_STEP_SUMMARY when set."""
from __future__ import annotations

import argparse
import warnings
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import load_json  # noqa: E402
from fsnmodel.data import load_matches, with_measured_scrums  # noqa: E402
from fsnmodel.evaluate import evaluate_snapshots, report, walk_forward  # noqa: E402


warnings.filterwarnings("ignore")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", default="Nations Championship")
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--espn", default=str(ROOT / "data" / "espn"))
    ap.add_argument("--rules", default=str(ROOT / "config" / "rules_nc.json"))
    ap.add_argument("--snapshots", default=str(ROOT / "data" / "snapshots"))
    a = ap.parse_args()
    R = load_json(a.rules)
    ov = load_json(ROOT / "config" / "overrides.json", {})
    dirs = [d for d in (a.rfu, a.espn) if Path(d).exists()]
    hist = load_matches(*dirs, tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    R, _ = with_measured_scrums(R, hist) if not hist.empty else (R, None)
    out = []
    if hist.empty:
        out.append("No match data.")
    else:
        per = hist.drop_duplicates("match_key")["competition"].value_counts().to_dict()
        out.append(f"# Backtest\n\nHistory: {hist['match_key'].nunique()} matches with player stats, by competition: {per}.\n")
        comps = [c for c, n in per.items() if n >= 6] if a.competition == "all" else [a.competition]
        if not comps:
            out.append("No competition has enough matches with player stats to backtest yet.\n")
        for c in comps:
            df, info = walk_forward(hist, R, c, ov)
            out.append(report(df, info, f"Walk-forward: {c} (starters predicted)"))
            dl, il = walk_forward(hist, R, c, ov, use_lineups=True)
            out.append(report(dl, il, f"Walk-forward: {c} (with named lineups, as in real use)"))
        if Path(a.snapshots).exists() and any(Path(a.snapshots).glob("*.json")):
            sd, si = evaluate_snapshots(a.snapshots, hist, R, ov)
            out.append(report(sd, si, "Frozen pre-round snapshots vs actual"))
    text = "\n".join(out)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    main()
