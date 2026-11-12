"""Scrape Super Rugby Pacific match centres (super.rugby, Opta widgets) into data/super/, in the same shape as data/rfu/.

    python scripts/scrape_super.py [--from 2023] [--to 2026] [--limit N]

Club matches feed player form only (see CLUB_COMPETITIONS in fsnmodel/common.py). Stats come from the same Opta tables the
RFU pages use (fsnmodel/rfu_extract.js). Shirt numbers come from the order of each team's stats table (15..1, then 16..23,
checked against ESPN's published shirts); substitutions (surname + minute) come from the timeline, and team-mates sharing a
surname are told apart by who is on the pitch at the time. Finished matches with player data are cached and never fetched
again. One page every 10 s (robots.txt asks for 5); after 3 failures in a row it pauses 15 minutes, and stops after 3 pauses."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://super.rugby"
ARCHIVE = SITE + "/superrugby/fixtures/archives/{y}-super-rugby-pacific/"
CURRENT = SITE + "/superrugby/fixtures/"
UA = "Mozilla/5.0 (fsn-albert personal fantasy model)"
CRAWL_DELAY = 10.0          # robots.txt asks for 5 s; long runs were still slowed down at 5 s, so 10 s
FAIL_STREAK, PAUSE_S, MAX_PAUSES = 3, 900, 3   # 3 failures in a row: pause 15 min; stop after 3 pauses
FORMAT = 2                  # 2: shirt numbers from the table order
EXTRACT = (ROOT / "fsnmodel" / "rfu_extract.js").read_text()
DATE_RE = re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) \d{1,2} [A-Z][a-z]+ \d{4}")
CARD_RE = re.compile(r"FT \| (?P<home>[^|]+?) \| (?P<hs>\d+) \| v \| (?P<as>\d+) \| (?P<away>[^|]+?) \|")
LINK_RE = re.compile(r'href="[^"]*match-centre/\?competition=(?P<comp>\d+)&(?:amp;)?season=(?P<season>\d+)&(?:amp;)?match=(?P<id>\d+)"')

# Each team's player order in its first stats table (rows = player id + displayed name), to read shirt numbers from.
ORDER_JS = """() => [...document.querySelectorAll('table.Opta-Striped')].slice(0, 2).map(t => ({
  team: (t.rows[0] && t.rows[0].cells[0] ? t.rows[0].cells[0].innerText.trim() : ''),
  rows: [...t.rows].slice(1).map(r => { const th = r.querySelector('th'); const m = th && th.className.match(/Opta-Player-(\\d+)/); return m ? [m[1], th.innerText.trim()] : null; }).filter(Boolean)
}))"""

# Substitutions from the match timeline: side (home/away from the enclosing Opta block), surname, minute, on/off.
SUBS_JS = """() => [...document.querySelectorAll('#timelinetab li.Opta-MatchEvent')].flatMap(li => {
  const ic = li.querySelector('.Opta-IconOn, .Opta-IconOff');
  if (!ic) return [];
  const side = li.closest('.Opta-Home') ? 'home' : li.closest('.Opta-Away') ? 'away' : null;
  const name = (li.querySelector('.Opta-Event-Player') || {}).textContent || '';
  const t = parseInt((li.querySelector('.Opta-Event-Time') || {}).textContent || '', 10);
  return [{ type: ic.classList.contains('Opta-IconOn') ? 'on' : 'off', side, name: name.trim(), min: Number.isFinite(t) ? t : null }];
})"""


class Polite:
    """One page every CRAWL_DELAY seconds at most."""
    def __init__(self, page):
        self.page, self.last = page, 0.0

    def goto(self, url):
        wait = CRAWL_DELAY - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        return self.page.goto(url, wait_until="domcontentloaded", timeout=45000)


def season_matches(nav: Polite, year: int) -> list[dict]:
    """Finished matches of a season from its fixture list: id, date, home, away, score."""
    out = []
    for url in (ARCHIVE.format(y=year), CURRENT):
        resp = nav.goto(url)
        if resp is None or resp.status >= 400:
            continue
        nav.page.wait_for_timeout(5000)
        c = nav.page.content()
        text_at = lambda i: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", c[max(0, i - 3000):i]))  # noqa: E731
        seen = set()
        heads = [(d.start(), d.group(0)) for d in DATE_RE.finditer(c)]   # date headings: a busy day's heading can be far above its last match
        for m in LINK_RE.finditer(c):
            if int(m["season"]) != year or m["id"] in seen:
                continue
            before = text_at(m.start())
            dates = [h for pos, h in heads if pos < m.start()]
            card = list(CARD_RE.finditer(re.sub(r"(\| )+", "| ", before)))
            if not dates or not card:
                continue                                   # not played yet (no FT result)
            k = card[-1]
            seen.add(m["id"])                              # only once its result card is read: a match is also linked from elsewhere on the page
            out.append({"id": m["id"], "comp": m["comp"], "season": year, "date": dates[-1], "home": k["home"].strip(), "away": k["away"].strip(),
                        "score": {"home": int(k["hs"]), "away": int(k["as"]), "homeName": k["home"].strip(), "awayName": k["away"].strip()},
                        "url": f"{SITE}/superrugby/match-centre/?competition={m['comp']}&season={year}&match={m['id']}"})
        if out:
            return out
    return out


def surname(name: str) -> str:
    return (name or "").strip().split(" ")[-1].lower()


# Front-rowers are replaced by front-rowers (scrum law): on the 2023-26 timelines a 16-18 replaced a 1-3 in 97% of clear
# one-for-one swaps, and any other replacement replaced someone else in 99%. Other positions are too fluid to use (6/2 benches).
FRONT_ROW, FRONT_ROW_BENCH = {1, 2, 3}, {16, 17, 18}


def shirts_from_order(order: list[dict]) -> dict[str, int]:
    """Opta lists each team's 23 as shirts 15..1, then 16..23 (checked against ESPN's published shirts). Player id -> shirt,
    only for teams with exactly 23 rows."""
    out = {}
    for t in order:
        rows = t.get("rows") or []
        if len(rows) == 23:
            for i, (pid, _) in enumerate(rows):
                out[pid] = 15 - i if i < 15 else i + 1
    return out


def build(meta: dict, d: dict, subs: list[dict], order: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Match file in the data/rfu shape, plus warnings."""
    warn = []
    raw_players = json.loads(json.dumps(d["players"]))   # as extracted, before events and shirts are added
    players = d["players"]
    teams = sorted({p.get("team") for p in players if p.get("team")})
    side_team = {"home": meta["home"], "away": meta["away"]}
    if set(teams) != {meta["home"], meta["away"]}:
        warn.append(f"team names {teams} vs card {meta['home']}/{meta['away']}")
    shirt = shirts_from_order(order or [])
    for p in players:
        p["events"] = []
        p["shirt"] = shirt.get(str(p["id"]))
    for t in (meta["home"], meta["away"]):
        if not all(p["shirt"] for p in players if p.get("team") == t):
            warn.append(f"{t}: no shirt numbers (table order not 23 rows)")
            for p in players:
                if p.get("team") == t:
                    p["shirt"] = None
    by_team_name: dict[tuple, list] = {}
    for p in players:
        by_team_name.setdefault((p.get("team"), surname(p.get("name"))), []).append(p)
    # replay the substitutions in order; team-mates sharing a surname are told apart by who is on the pitch at the time
    on_pitch = {id(p): bool(p["shirt"] and p["shirt"] <= 15) for p in players}
    ambiguous = set()
    for s in sorted(subs, key=lambda s: (s["min"] is None, s["min"] or 0, s["type"] == "on")):
        cands = by_team_name.get((side_team.get(s["side"]), surname(s["name"])), []) if s["side"] else \
            [p for (t, n), ps in by_team_name.items() if n == surname(s["name"]) for p in ps]
        if len(cands) > 1 and all(p["shirt"] for p in cands):
            cands = [p for p in cands if on_pitch[id(p)] == (s["type"] == "off")]
            if s["type"] == "on" and len(cands) > 1:     # nobody comes straight back on in the minute he went off
                cands = [p for p in cands if p["events"][-1:] != [{"type": "off", "min": s["min"]}]]
            if s["type"] == "on" and len(cands) > 1:     # a replacement who never came on has an empty stat line
                played = [p for p in cands if any(v for v in p["stats"].values())]
                cands = played or cands
            if len(cands) > 1 and s["side"]:
                # two same-named players could be the one: the other half of a one-for-one swap that minute shows whether
                # a front-rower went off or came on (front-rowers are replaced by front-rowers)
                team, other = side_team[s["side"]], "on" if s["type"] == "off" else "off"
                partners = []
                for x in subs:
                    if x is not s and x["type"] == other and x["side"] == s["side"] and x["min"] == s["min"]:
                        rep = by_team_name.get((team, surname(x["name"])), [])
                        if len(rep) == 1 and rep[0]["shirt"]:
                            partners.append(rep[0]["shirt"])
                fit = []
                if len(partners) == 1:
                    front = partners[0] in FRONT_ROW | FRONT_ROW_BENCH
                    fit = [p for p in cands if (p["shirt"] in FRONT_ROW | FRONT_ROW_BENCH) == front]
                if len(fit) == 1:
                    cands = fit
                    warn.append(f"'{s['name']}' {s['type']} {s['min']}' decided by position (shirt {fit[0]['shirt']})")
        if len(cands) == 1 and s["min"] is not None:
            cands[0]["events"].append({"type": s["type"], "min": s["min"]})
            on_pitch[id(cands[0])] = s["type"] == "on"
        else:
            warn.append(f"substitution '{s['name']}' {s['type']} {s['min']}' matched {len(cands)} players")
            ambiguous.update(id(p) for (t, n), ps in by_team_name.items() if n == surname(s["name"]) for p in ps)
    if ambiguous:                    # minutes that cannot be told apart: those players are left out of this match
        warn.append(f"left out {len(ambiguous)} players with a shared surname")
    for p in players:
        did_something = any(v for v in p["stats"].values()) or p["events"]
        by_events = bool(did_something and (not p["events"] or p["events"][0]["type"] == "off"))
        if p["shirt"]:
            p["started"] = p["shirt"] <= 15
            if did_something and p["started"] != by_events:
                warn.append(f"{p.get('name')} ({p.get('team')}) shirt {p['shirt']} disagrees with the timeline")
        else:
            # no shirts: a starter's first substitution is going off (blood / HIA replacements go off, on, off),
            # a replacement's is coming on, and an unused replacement has no events and an empty stat line
            p["started"] = by_events
    for t in (meta["home"], meta["away"]):
        n = sum(p["started"] for p in players if p.get("team") == t)
        if n != 15 and not any(id(p) in ambiguous for p in players if p.get("team") == t):
            warn.append(f"{t}: {n} starters")
    # the stat lines must add up to the score (penalty tries have no scorer, so a short count is only a warning)
    for side in ("home", "away"):
        t = side_team[side]
        pts = sum(5 * p["stats"].get("attack:T", 0) + 2 * p["stats"].get("kicking:C", 0) + 3 * p["stats"].get("kicking:PG", 0) + 3 * p["stats"].get("kicking:DG", 0)
                  for p in players if p.get("team") == t)
        if pts != meta["score"][side]:
            warn.append(f"{t}: stat lines add up to {pts}, score {meta['score'][side]}")
    out = {"format": FORMAT, "source": "super-rugby", "competition": "Super Rugby", "season": meta["season"], "date": meta["date"],
           "home": meta["home"], "away": meta["away"], "teams": [meta["home"], meta["away"]], "score": meta["score"],
           "players": [p for p in players if id(p) not in ambiguous], "url": meta["url"], "warnings": warn,
           "raw": {"subs": subs, "order": order or [], "players": raw_players}}   # kept so the rules above can be re-applied without fetching again
    return out, warn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="y0", type=int, default=2023)
    ap.add_argument("--to", dest="y1", type=int, default=date.today().year)
    ap.add_argument("--out", default=str(ROOT / "data" / "super"))
    ap.add_argument("--limit", type=int, default=0, help="stop after N new matches (for testing)")
    ap.add_argument("--rebuild", action="store_true", help="re-apply the rules to saved files that kept their raw extract; fetches nothing")
    ap.add_argument("--refetch-left-out", action="store_true", help="fetch again the matches that left players out (saved before the raw timeline was kept)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.rebuild:
        n = 0
        for f in sorted(out.glob("s*.json")):
            old = json.loads(f.read_text())
            raw = old.get("raw", {})
            if "players" not in raw:
                continue
            meta = {"id": f.stem[1:], **{k: old[k] for k in ("season", "date", "home", "away", "score", "url")}}
            m, _ = build(meta, {"players": json.loads(json.dumps(raw["players"]))}, raw["subs"], raw["order"])
            f.write_text(json.dumps(m, separators=(",", ":")))
            n += 1
        print(f"rebuilt {n} matches from their saved extracts")
        return

    from playwright.sync_api import sync_playwright
    n_new = n_fail = n_warn = streak = pauses = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        nav = Polite(br.new_context(user_agent=UA, viewport={"width": 1280, "height": 2400}).new_page())
        for y in range(a.y0, a.y1 + 1):
            ms = season_matches(nav, y)
            if not ms:                                   # the fixture list is built by script: a slow page can come back empty
                ms = season_matches(nav, y)
            print(f"{y}: {len(ms)} finished matches listed", flush=True)
            for meta in ms:
                f = out / f"s{meta['id']}.json"
                if f.exists():
                    old = json.loads(f.read_text())
                    redo = a.refetch_left_out and "players" not in old.get("raw", {}) and any("left out" in w for w in old.get("warnings", []))
                    if old.get("format") == FORMAT and old.get("score") and old.get("players") and not redo:
                        continue
                try:
                    resp = nav.goto(meta["url"])
                    if resp is None or resp.status >= 400:
                        raise RuntimeError(f"http {getattr(resp, 'status', '?')}")
                    nav.page.wait_for_selector("table.Opta-Striped", state="attached", timeout=30000)
                    nav.page.wait_for_selector("#timelinetab li.Opta-MatchEvent", state="attached", timeout=15000)
                    nav.page.wait_for_timeout(1000)
                    d = nav.page.evaluate(EXTRACT)
                    subs = nav.page.evaluate(SUBS_JS)
                    order = nav.page.evaluate(ORDER_JS)
                except Exception as e:  # noqa: BLE001
                    n_fail += 1
                    streak += 1
                    print(f"FAIL s{meta['id']} {meta['home']} v {meta['away']}: {str(e).splitlines()[0][:120]}", flush=True)
                    if streak >= FAIL_STREAK:            # the site is slowing us down: back off rather than push on
                        pauses += 1
                        if pauses > MAX_PAUSES:
                            print(f"STOP: {FAIL_STREAK} failures in a row after {MAX_PAUSES} pauses; run again later", flush=True)
                            br.close()
                            print(f"scraped {n_new} matches, {n_fail} failed, {n_warn} with warnings")
                            sys.exit(2)
                        print(f"PAUSE {PAUSE_S // 60} min after {streak} failures in a row", flush=True)
                        time.sleep(PAUSE_S)
                        streak = 0
                    continue
                streak = 0
                if len(d.get("players", [])) < 30:
                    n_fail += 1
                    print(f"FAIL s{meta['id']}: only {len(d.get('players', []))} players", flush=True)
                    continue
                m, warn = build(meta, d, subs, order)
                f.write_text(json.dumps(m, separators=(",", ":")))
                n_new += 1
                n_warn += bool(warn)
                print(f"s{meta['id']}: {meta['date']} {meta['home']} {meta['score']['home']}-{meta['score']['away']} {meta['away']} "
                      f"players={len(m['players'])} subs={len(subs)}" + (f" WARN {warn}" if warn else ""), flush=True)
                if a.limit and n_new >= a.limit:
                    break
            if a.limit and n_new >= a.limit:
                break
        br.close()
    print(f"scraped {n_new} matches, {n_fail} failed, {n_warn} with warnings")


if __name__ == "__main__":
    main()
