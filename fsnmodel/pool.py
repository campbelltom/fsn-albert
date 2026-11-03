"""The official game's player pool (config/nc_pool.csv, pasted from the 'create team' page): names, official position, hemisphere, price.

Pool names carry no nation, so players are matched to the history on (hemisphere of their nation, surname, first initial, position)."""
from __future__ import annotations

import pandas as pd

from .identity import surname

POOL_POS = {"Prop": "PROP", "Hooker": "HOOK", "Lock": "LOCK", "Loose Forward": "BR", "Scrum Half": "SH", "Fly Half": "FH", "Centre": "CEN", "Back Three": "BACK3"}


def initial(name: str | None) -> str | None:
    n = (name or "").strip()
    if ". " in n or (len(n) > 2 and "." in n[:4]):
        return n[0].lower()
    return None            # RFU names are surnames only


def load_pool(path) -> pd.DataFrame:
    d = pd.read_csv(path)
    d["grp"] = d["pos"].map(POOL_POS)
    d["sn"] = d["name"].map(surname)
    d["ini"] = d["name"].map(initial)
    return d


def _pick(pool: pd.DataFrame, used: set, name: str, hemi: str, grp: str | None):
    c = pool[(pool["sn"] == surname(name)) & (pool["hemi"] == hemi) & (~pool.index.isin(used))]
    ini = initial(name)
    if ini:                                       # a known first initial is binding: same surname, different person otherwise
        c = c[(c["ini"] == ini) | c["ini"].isna()]
    if len(c) > 1 and grp:
        c2 = c[c["grp"] == grp]
        c = c2 if len(c2) else c
    return c.index[0] if len(c) == 1 else None


def match_history(hist: pd.DataFrame, pool: pd.DataFrame, hemi: dict, pos: pd.Series) -> tuple[dict, dict]:
    """pid -> pool row index. Most-capped players are matched first so common surnames resolve sensibly."""
    last = hist.sort_values("date").groupby("pid").tail(1).set_index("pid")
    caps = hist[hist["minutes"] > 0].groupby("pid").size()
    order = sorted(last.index, key=lambda p: -caps.get(p, 0))
    used, out, ambiguous = set(), {}, 0
    for pid in order:
        r = last.loc[pid]
        h = hemi.get(r["team"])
        if not h:
            continue
        idx = _pick(pool, used, r["name"], h, pos.get(pid))
        if idx is not None:
            out[pid] = idx; used.add(idx)
        else:
            ambiguous += 1
    return out, {"matched": len(out), "unmatched_history": ambiguous, "used": used}


def match_one(pool: pd.DataFrame, used: set, name: str, team: str, hemi: dict, grp: str | None):
    h = hemi.get(team)
    return _pick(pool, used, name, h, grp) if h else None
