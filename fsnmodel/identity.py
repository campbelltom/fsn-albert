"""Player identity across sources: ESPN ids ('e123') and RFU/Opta ids are different id spaces. Match on (team, surname)."""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


def surname(name: str | None) -> str:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("'", "").replace("`", "")
    toks = [t for t in re.sub(r"[^a-z\- ]", " ", n).split() if len(t) > 1]
    return toks[-1] if toks else ""


def _tokens(name: str | None) -> list[str]:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("'", "").replace("`", "")
    return [t for t in re.sub(r"[^a-z\- ]", " ", n).split() if t]


def given(name: str | None) -> str:
    """The first given name or initial ('Zander Fagerson' -> 'zander', 'Z. Fagerson' -> 'z'), '' if only a surname is known."""
    t = _tokens(name)
    return t[0] if len(t) > 1 else ""


class NameIndex:
    """(team, surname) -> pids seen in the history (latest team per player). Brothers and namesakes are told apart by given name,
    then by the shirt they last wore (a fly-half Garbisi is not the scrum-half)."""

    def __init__(self, hist: pd.DataFrame):
        last = hist.sort_values("date").groupby("pid").tail(1)
        self.map: dict[tuple, set] = {}
        self.info: dict[str, tuple[str, int | None]] = {}
        for r in last.itertuples():
            self.map.setdefault((r.team, surname(r.name)), set()).add(r.pid)
            sh = getattr(r, "shirt", None)
            self.info[r.pid] = (given(r.name), int(sh) if sh == sh and sh is not None else None)

    def match(self, team: str, name: str, shirt: int | None = None) -> str | None:
        c = self.map.get((team, surname(name)), set())
        if len(c) <= 1:
            return next(iter(c)) if c else None
        g = given(name)
        if g:
            by_name = {p for p in c if self.info[p][0] and (self.info[p][0] == g or self.info[p][0][0] == g[0] and (len(g) == 1 or len(self.info[p][0]) == 1))}
            if len(by_name) == 1:
                return next(iter(by_name))
            c = by_name or c
        if shirt:
            from .common import BENCH_POS, SHIRT_POS
            want = SHIRT_POS.get(shirt) if shirt <= 15 else BENCH_POS.get(shirt)
            by_pos = {p for p in c if self.info[p][1] and (SHIRT_POS.get(self.info[p][1]) if self.info[p][1] <= 15 else BENCH_POS.get(self.info[p][1])) == want}
            if len(by_pos) == 1:
                return next(iter(by_pos))
        return None


def resolve_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Where a player appears both as an ESPN id and an RFU id, use the RFU id (unique (team, surname) on both sides only)."""
    if df.empty or not (df["source"] == "rfu").any() or not (df["source"] == "espn").any():
        return df
    key = lambda d: d.drop_duplicates("pid").assign(sn=lambda x: x["name"].map(surname))  # noqa: E731
    rfu = key(df[df["source"] == "rfu"])
    esp = key(df[df["source"] == "espn"])
    # a player's team can differ between matches only rarely; use all (team, surname) pairs
    rp = df[df["source"] == "rfu"].assign(sn=lambda x: x["name"].map(surname)).drop_duplicates(["pid", "team"])
    ep = df[df["source"] == "espn"].assign(sn=lambda x: x["name"].map(surname)).drop_duplicates(["pid", "team"])
    rg = rp.groupby(["team", "sn"])["pid"].nunique()
    eg = ep.groupby(["team", "sn"])["pid"].nunique()
    r1 = rp.set_index(["team", "sn"])["pid"]
    mapping = {}
    for r in ep.itertuples():
        k = (r.team, r.sn)
        if r.sn and rg.get(k, 0) == 1 and eg.get(k, 0) == 1:
            mapping[r.pid] = r1[k] if not isinstance(r1[k], pd.Series) else r1[k].iloc[0]
    # namesakes (the Barrett, Curry, Smith brothers) are unique only once the given name's initial is part of the key
    ini = lambda d: d["name"].map(lambda n: given(n)[:1])  # noqa: E731
    rp2, ep2 = rp.assign(gi=ini(rp)), ep.assign(gi=ini(ep))
    rg2 = rp2[rp2["gi"] != ""].groupby(["team", "sn", "gi"])["pid"].nunique()
    eg2 = ep2[ep2["gi"] != ""].groupby(["team", "sn", "gi"])["pid"].nunique()
    r2 = rp2[rp2["gi"] != ""].drop_duplicates(["team", "sn", "gi"]).set_index(["team", "sn", "gi"])["pid"]
    for r in ep2.itertuples():
        k = (r.team, r.sn, r.gi)
        if r.pid not in mapping and r.sn and r.gi and rg2.get(k, 0) == 1 and eg2.get(k, 0) == 1:
            mapping[r.pid] = r2[k]
    return df.assign(pid=df["pid"].replace(mapping)) if mapping else df
