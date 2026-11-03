"""Walk-forward backtest and snapshot evaluation.

    python scripts/backtest.py --competition "Nations Championship" [--snapshots data/snapshots] [--rules config/rules_nc.json]
Writes markdown to stdout and to $GITHUB_STEP_SUMMARY when set."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import load_json  # noqa: E402
from fsnmodel.data import load_matches  # noqa: E402
from fsnmodel.evaluate import evaluate_snapshots, report, walk_forward  # noqa: E402


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
    out = []
    if hist.empty:
        out.append("No match data.")
    else:
        n_comp = hist[hist["competition"] == a.competition]["match_key"].nunique()
        out.append(f"# Backtest\n\nHistory: {hist['match_key'].nunique()} matches ({hist['competition'].value_counts().to_dict()} rows by competition); {n_comp} {a.competition} matches evaluated round by round.\n")
        df, info = walk_forward(hist, R, a.competition, ov)
        out.append(report(df, info, f"Walk-forward: {a.competition}"))
        if Path(a.snapshots).exists() and any(Path(a.snapshots).glob("*.json")):
            sd, si = evaluate_snapshots(a.snapshots, hist, R, ov)
            out.append(report(sd, si, "Frozen pre-round snapshots vs actual"))
    text = "\n".join(out)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(text)


if __name__ == "__main__":
    main()
