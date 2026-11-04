"""Scrape RFU (englandrugby.com) match centres: Opta widgets rendered client-side.

URLs come from config/rfu_matches.json (any match-centre / match-centre-elite URL), which scripts/discover_rfu.py fills from the
site's own competition and season filters:

    python scripts/discover_rfu.py
    python scripts/scrape_rfu.py

--generated also guesses URLs instead (six-nations-<year>-<home>-v-<away>-match-centre; with --england-tests, England's autumn
series). Most of those pages do not exist, so this is only for matches the filters do not list.

One JSON per match in data/rfu/. Matches with a score and player data are cached and never fetched again. Pages that do not exist are remembered
for past seasons only. Run this from a normal machine: the RFU site refuses GitHub's runners (HTTP 405)."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.common import SLUG, TEAMS, norm_team  # noqa: E402

SIX = "https://www.englandrugby.com/fixtures-and-results/match-centre/six-nations-{y}-{h}-v-{a}-match-centre"
COMP = "https://www.englandrugby.com/fixtures-and-results/match-centre/{c}-{y}-{h}-v-{a}-match-centre"
ENGLAND_COMPS = {"quilter-nations-series": "Autumn Nations Series", "autumn-nations-series": "Autumn Nations Series"}
OPPONENTS = ["argentina", "australia", "new-zealand", "south-africa", "fiji", "japan", "samoa", "tonga", "georgia", "usa", "canada", "romania", "portugal", "spain"]
JS = (ROOT / "fsnmodel" / "rfu_extract.js").read_text()


def candidates(y0: int, y1: int, england_tests: bool):
    for y in range(y0, y1 + 1):
        for h, w in itertools.permutations(TEAMS, 2):
            yield f"{y}_{h}_{w}", SIX.format(y=y, h=SLUG[h], a=SLUG[w]), {"season": y, "competition": "Six Nations", "home": h, "away": w}, y
        if england_tests:
            for c, name in ENGLAND_COMPS.items():
                for o in OPPONENTS:
                    for h, w in (("england", o), (o, "england")):
                        yield f"{y}_{c}_{h}_{w}", COMP.format(c=c, y=y, h=h, a=w), {"season": y, "competition": name}, y


def manifest_entries(path: Path):
    if not path.exists():
        return
    for e in json.loads(path.read_text()):
        url = e["url"]
        mid = re.search(r"matchId=(\d+)", url)
        key = "m" + (mid.group(1) if mid else hashlib.md5(url.encode()).hexdigest()[:10])
        yield key, url, {k: v for k, v in e.items() if k != "url"}, 9999


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="y0", type=int, default=2023)
    ap.add_argument("--to", dest="y1", type=int, default=date.today().year + 1)
    ap.add_argument("--out", default=str(ROOT / "data" / "rfu"))
    ap.add_argument("--generated", action="store_true", help="also guess URLs from fixture patterns (saves a match under a second name if it is also in the manifest)")
    ap.add_argument("--england-tests", action="store_true", help="with --generated, also try England's autumn-series pages")
    ap.add_argument("--manifest", default=str(ROOT / "config" / "rfu_matches.json"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    miss_f = out / "_missing.json"
    missing = set(json.loads(miss_f.read_text())) if miss_f.exists() else set()
    this_year = date.today().year

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    n_new = n_fail = 0
    reasons: dict[str, int] = {}
    generated = candidates(a.y0, a.y1, a.england_tests) if a.generated else []
    todo = list(generated) + list(manifest_entries(Path(a.manifest)))
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(user_agent="Mozilla/5.0 (fsn-albert personal fantasy model)")
        page = ctx.new_page()
        for key, url, meta, y in todo:
            f = out / f"{key}.json"
            cached = json.loads(f.read_text()) if f.exists() else {}
            if cached.get("score") and cached.get("players"):
                continue                                   # finished: cached for good
            if key in missing and y < this_year:
                continue
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if resp is None or resp.status >= 400:
                    raise PWTimeout("http " + str(getattr(resp, "status", "?")))
                if page.url.split("?")[0] != url.split("?")[0]:     # the site can redirect a guessed URL to another season's match
                    raise PWTimeout("redirected to " + page.url)
                page.wait_for_selector("table.Opta-Squad", state="attached", timeout=25000)  # may sit in a hidden tab
                page.wait_for_selector("table.Opta-Striped", state="attached", timeout=25000)
                time.sleep(0.8)
                d = page.evaluate(JS)
            except Exception as e:  # noqa: BLE001 - a missing page is normal when pages are generated
                if y < this_year:
                    missing.add(key)
                n_fail += 1
                why = str(e).split("\n")[0][:90]
                reasons[why] = reasons.get(why, 0) + 1
                if sum(reasons.values()) <= 3:
                    print(f"FAIL {key}: {type(e).__name__}: {why}; title={page.title()!r}")
                continue
            sc = d.get("score") or {}
            d.update({"url": url, "source": "rfu", **meta})
            d["home"] = norm_team(meta.get("home") or sc.get("homeName"))
            d["away"] = norm_team(meta.get("away") or sc.get("awayName"))
            if not d.get("season"):
                m = re.search(r"(19|20)\d\d", d.get("date") or "")
                d["season"] = int(m.group(0)) if m else this_year
            if len(d.get("players", [])) < 30:
                print(f"WARN {key}: only {len(d.get('players', []))} players parsed", file=sys.stderr)
            f.write_text(json.dumps(d, separators=(",", ":")))
            n_new += 1
            print(f"{key}: {d['home']} v {d['away']} {sc or 'no score'} players={len(d['players'])}")
        br.close()
    miss_f.write_text(json.dumps(sorted(missing)))
    print(f"scraped {n_new} pages, {n_fail} not available; reasons: {reasons}")


if __name__ == "__main__":
    main()
