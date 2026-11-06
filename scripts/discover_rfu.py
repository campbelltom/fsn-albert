"""Find RFU match-centre URLs through the site's own competition and season filters, and add them to config/rfu_matches.json.

    python scripts/discover_rfu.py [--dry-run]
    python scripts/scrape_rfu.py

The listings (fixtures-and-results/search-results) link every match, including England's pages, whose hand-written slugs cannot
be generated. Only matches dated before today are added, so re-run this the day after each round. Existing manifest entries are kept
unchanged. Listings that show a "Load more" button may be cut short: the per-season counts printed here are the check on that."""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://www.englandrugby.com"
LISTING = SITE + "/fixtures-and-results/search-results?{query}&season={season}#results"
CLUB_SEASONS = ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]
# (name written to the manifest, filter query, seasons as the site's filter labels them)
SOURCES = [
    ("Six Nations", "competition=2", ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]),
    ("Nations Championship", "competition=2311", ["2026-2027"]),
    ("International", "competition=0&division=79054", ["2026-2027"]),
    # Club matches feed player form only: see CLUB_COMPETITIONS in fsnmodel/common.py
    ("Gallagher Prem", "competition=5", CLUB_SEASONS),
    ("Champions Cup", "competition=9", CLUB_SEASONS),
    ("Challenge Cup", "competition=10", CLUB_SEASONS),
]
LINK = re.compile(r'<a\b[^>]*href="([^"]*/match-centre[^"]*)"', re.I)
CARD_DATE = re.compile(r"cardContainer_[A-Za-z]+,(\d{2}[A-Za-z]{3}\d{4})")


def identity(url: str) -> str:
    """Same match, same identity: the match id where there is one, otherwise the URL."""
    m = re.search(r"matchId=(\d+)", url)
    return m.group(1) if m else url


def parse_listing(page_html: str, today: date, season_label: str) -> tuple[dict[str, dict], int]:
    """Return ({url: {"season": year}} for played matches, number of unplayed matches skipped)."""
    dated: dict[str, date | None] = {}
    for m in LINK.finditer(page_html):
        url = SITE + html.unescape(m.group(1)) if m.group(1).startswith("/") else html.unescape(m.group(1))
        cards = CARD_DATE.findall(page_html, 0, m.start())
        when = datetime.strptime(cards[-1], "%d%b%Y").date() if cards else None
        dated.setdefault(url, when)
    # Pages without a seasonId (England's) take the season most of their listing reports; failing that, the filter's end year.
    ids = collections.Counter(s for u in dated for s in re.findall(r"seasonId=(\d{4})", u))
    fallback = int(ids.most_common(1)[0][0]) if ids else int(season_label[-4:])
    found, unplayed = {}, 0
    for url, when in dated.items():
        if when and when >= today:                # today's matches may be unfinished, and a saved score is cached for good
            unplayed += 1
            continue
        sid = re.search(r"seasonId=(\d{4})", url)
        found[url] = {"season": int(sid.group(1)) if sid else fallback}
    return found, unplayed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "config" / "rfu_matches.json"))
    ap.add_argument("--dry-run", action="store_true", help="report what would be added without writing")
    a = ap.parse_args()
    path = Path(a.manifest)
    entries = json.loads(path.read_text()) if path.exists() else []
    known = {identity(e["url"]) for e in entries}
    today = date.today()

    from playwright.sync_api import sync_playwright
    added = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_context(user_agent="Mozilla/5.0 (fsn-albert personal fantasy model)").new_page()
        for name, query, seasons in SOURCES:
            for season in seasons:
                resp = page.goto(LISTING.format(query=query, season=season), wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                if resp is None or resp.status >= 400:
                    print(f"{name} {season}: FAILED http {getattr(resp, 'status', '?')}")
                    continue
                found, unplayed = parse_listing(page.content(), today, season)
                new = 0
                for url, meta in found.items():
                    if identity(url) not in known:
                        known.add(identity(url))
                        entries.append({"url": url, "competition": name, **meta})
                        new += 1
                added += new
                print(f"{name} {season}: {len(found)} played, {unplayed} not yet played, {new} new")
        br.close()
    if a.dry_run:
        print(f"dry run: would add {added}, manifest would have {len(entries)}")
        return
    path.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"added {added}; manifest now has {len(entries)}")


if __name__ == "__main__":
    main()
