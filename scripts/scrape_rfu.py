"""Scrape Six Nations match centres from englandrugby.com (Opta widgets, rendered client-side).

    python scripts/scrape_rfu.py --from 2023 --to 2027 --out data/rfu

One JSON per match: data/rfu/<season>_<home>_<away>.json (the output of fsnmodel/rfu_extract.js plus season/home/away/url).
Finished matches are cached and never fetched again; pages that do not exist are remembered in _missing.json for past
seasons only (current and future seasons are retried every run, so new fixtures and results appear automatically)."""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import SLUG, TEAMS  # noqa: E402

URL = "https://www.englandrugby.com/fixtures-and-results/match-centre/six-nations-{y}-{h}-v-{a}-match-centre"
JS = (ROOT / "fsnmodel" / "rfu_extract.js").read_text()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="y0", type=int, default=2023)
    ap.add_argument("--to", dest="y1", type=int, default=date.today().year + 1)
    ap.add_argument("--out", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--only-new", action="store_true", help="skip pages already cached as finished (default behaviour; kept for clarity)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    miss_f = out / "_missing.json"
    missing = set(json.loads(miss_f.read_text())) if miss_f.exists() else set()
    this_year = date.today().year

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    n_new = n_fail = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(user_agent="Mozilla/5.0 (fsn-albert personal fantasy model)")
        page = ctx.new_page()
        for y in range(a.y0, a.y1 + 1):
            for h, w in itertools.permutations(TEAMS, 2):
                key = f"{y}_{h}_{w}"
                f = out / f"{key}.json"
                if f.exists():
                    old = json.loads(f.read_text())
                    if old.get("score"):          # finished: cached for good
                        continue
                if key in missing and y < this_year:
                    continue
                url = URL.format(y=y, h=SLUG[h], a=SLUG[w])
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if resp is None or resp.status >= 400:
                        raise PWTimeout("http " + str(getattr(resp, "status", "?")))
                    page.wait_for_selector("table.Opta-Squad", timeout=25000)
                    page.wait_for_selector("[class*=c111] table.Opta-Striped", state="attached", timeout=25000)
                    time.sleep(0.8)
                    d = page.evaluate(JS)
                except Exception as e:  # noqa: BLE001 - a missing page is normal (30 pairings, 15 matches)
                    if y < this_year:
                        missing.add(key)
                    n_fail += 1
                    continue
                d.update({"season": y, "home": h, "away": w, "url": url, "source": "rfu"})
                if len(d.get("players", [])) < 30:
                    print(f"WARN {key}: only {len(d.get('players', []))} players parsed", file=sys.stderr)
                f.write_text(json.dumps(d, separators=(",", ":")))
                n_new += 1
                print(f"{key}: {d.get('score')} players={len(d['players'])}")
        br.close()
    miss_f.write_text(json.dumps(sorted(missing)))
    print(f"scraped {n_new} pages, {n_fail} not available")


if __name__ == "__main__":
    main()
