"""Compare model settings by walk-forward backtest (earlier matches only).

    python scripts/tune.py [--rules config/rules_nc.json]   -> markdown table (also appended to $GITHUB_STEP_SUMMARY)"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel import model  # noqa: E402
from fsnmodel.common import load_json  # noqa: E402
from fsnmodel.data import load_matches, with_measured_scrums  # noqa: E402
from fsnmodel.evaluate import compare, walk_forward  # noqa: E402

ROLE_VARIANTS = [
    ("roles by shirt + team baselines (new)", {}),
    ("fantasy positions only, no team baselines (old)", {"role_detail": "group", "k_team": 1e9}),
    ("fantasy positions + team baselines", {"role_detail": "group"}),
    ("roles by shirt, no team baselines", {"k_team": 1e9}),
    ("roles by shirt, team baselines x2 weight (k_team 3)", {"k_team": 3.0}),
    ("roles by shirt, team baselines /2 weight (k_team 20)", {"k_team": 20.0}),
]

VARIANTS = [
    ("current (tuned defaults)", {}),
    ("previous defaults", {"half_life": 420, "k_scale": 1.0, "ctx": 1.0}),
    ("form half-life 200d", {"half_life": 200}),
    ("form half-life 900d", {"half_life": 900}),
    ("shrink x0.4 (trust own data)", {"k_scale": 0.4}),
    ("shrink x2.5 (trust position avg)", {"k_scale": 2.5}),
    ("no opposition context", {"ctx": 0.0}),
    ("half context", {"ctx": 0.5}),
    ("hl 900 + shrink x0.4", {"half_life": 900, "k_scale": 0.4}),
    ("hl 900 + shrink x0.4 + half ctx", {"half_life": 900, "k_scale": 0.4, "ctx": 0.5}),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(ROOT / "config" / "rules_nc.json"))
    ap.add_argument("--rfu", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--espn", default=str(ROOT / "data" / "espn"))
    ap.add_argument("--skip", type=int, default=4, help="skip the first N rounds of each competition (little history)")
    ap.add_argument("--set", default="settings", choices=["settings", "roles"], help="which family of variants to compare")
    ap.add_argument("--no-lineups", action="store_true", help="predict starters instead of using the named 23")
    a = ap.parse_args()
    R = load_json(a.rules)
    ov = load_json(ROOT / "config" / "overrides.json", {})
    hist = load_matches(*[d for d in (a.rfu, a.espn) if Path(d).exists()], tackles_include_missed=R["assumptions"]["tackles_include_missed"])
    R, _ = with_measured_scrums(R, hist) if not hist.empty else (R, None)
    per = hist.drop_duplicates("match_key")["competition"].value_counts()
    comps = [c for c, n in per.items() if n >= 12]
    rows = []
    base = dict(model.PARAMS)
    for name, ch in (ROLE_VARIANTS if a.set == "roles" else VARIANTS):
        model.PARAMS.update(base); model.PARAMS.update(ch)
        parts = []
        for c in comps:
            df, _ = walk_forward(hist, R, c, ov, min_rounds_before=a.skip, use_lineups=not a.no_lineups)
            if not df.empty:
                df["comp"] = c; df["round"] = df["comp"] + df["round"].astype(str)
                parts.append(df)
        d = pd.concat(parts, ignore_index=True)
        al, st = compare(d), compare(d[d["p_start"] >= 0.5])
        rows.append(dict(variant=name, n=al["n"], mae_all=al["mae"], mae_base=al.get("mae_baseline"), rho_all=al["spearman"], mae_start=st["mae"], mae_start_base=st.get("mae_baseline"),
                         rho_start=st["spearman"], bias=al["bias"], top15=al["top15_actual"], best15=al["top15_best_possible"]))
        print(name, {k: round(v, 3) for k, v in rows[-1].items() if isinstance(v, float)}, flush=True)
    model.PARAMS.update(base)
    t = pd.DataFrame(rows)
    lines = [f"# Tuning (walk-forward, competitions: {', '.join(comps)}; first {a.skip} rounds of each skipped)", "",
             "| variant | n | MAE all | (naive) | rank corr all | MAE starters | (naive) | rank corr starters | bias | best-15 actual | (ideal) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t.itertuples():
        lines.append(f"| {r.variant} | {r.n} | {r.mae_all:.2f} | {r.mae_base:.2f} | {r.rho_all:.3f} | {r.mae_start:.2f} | {r.mae_start_base:.2f} | {r.rho_start:.3f} | {r.bias:+.2f} | {r.top15:.1f} | {r.best15:.1f} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    main()
