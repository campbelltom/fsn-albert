"""Named match-day squads (23 per team, announced before each match): files in data/lineups/ and config/lineups.json.

File format: {"round": 4, "team": "Ireland", "players": [{"id": "e123"|null, "name": "Sheehan", "shirt": 2}, ...]}   (shirt 1-15 start, 16-23 bench)
config/lineups.json: a list of such objects, for naming a team by hand (surnames are enough)."""
from __future__ import annotations

import json
from pathlib import Path

from .identity import NameIndex, surname


def parse_rosters(summ: dict) -> dict[str, list[dict]]:
    """ESPN summary['rosters'] -> {team: [{id, name, shirt}]} (defensive: structure differs by feed)."""
    out = {}
    for r in summ.get("rosters", []) or []:
        team = (r.get("team") or {}).get("displayName")
        pl = []
        for it in r.get("roster", []) or []:
            a = it.get("athlete") or {}
            j = it.get("jersey") or a.get("jersey")
            if a.get("id") is None:
                continue
            pl.append({"id": "e" + str(a["id"]), "name": a.get("shortName") or a.get("displayName"), "shirt": int(j) if str(j).isdigit() else None})
        if team and pl:
            out[team] = pl
    return out


def load_all(dirs, extra_file=None) -> list[dict]:
    items = []
    for d in dirs:
        for f in sorted(Path(d).glob("*.json")):
            try:
                items.append(json.loads(f.read_text()))
            except Exception:  # noqa: BLE001
                pass
    if extra_file and Path(extra_file).exists():
        items += json.loads(Path(extra_file).read_text())          # manual entries last: they win
    return items


def build_lineup_map(items: list[dict], hist, fixtures: list[dict]) -> tuple[dict, dict]:
    """-> ({(round, team): {pid: (shirt, name)}}, stats). Resolves names to history ids; unresolved players get a synthetic id (debutants)."""
    idx = NameIndex(hist) if hist is not None and len(hist) else None
    have = {(f["round"], t) for f in fixtures for t in (f["home"], f["away"])}
    known = set(hist["pid"]) if hist is not None and len(hist) else set()
    lm, n_new = {}, 0
    for it in items:
        key = (it["round"], it["team"])
        if key not in have:
            continue
        mp = {}
        for p in it["players"]:
            if not p.get("shirt"):
                continue
            pid = p.get("id") if p.get("id") in known else None
            if pid is None and idx:
                pid = idx.match(it["team"], p.get("name"), int(p["shirt"]))
            if pid is None:
                pid = f"n:{it['team']}:{surname(p.get('name'))}:{p['shirt']}" if not p.get("id") else p["id"]
                n_new += 1
            mp[pid] = (int(p["shirt"]), p.get("name"))
        if len(mp) >= 15:
            lm[key] = mp
    return lm, {"teams_named": len(lm), "without_history": n_new}
