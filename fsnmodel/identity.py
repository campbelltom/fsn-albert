"""Player identity across sources: ESPN ids ('e123') and RFU/Opta ids are different id spaces. Match on (team, surname)."""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


def surname(name: str | None) -> str:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("'", "").replace("`", "")
    toks = [t for t in re.sub(r"[^a-z\- ]", " ", n).split() if len(t) > 1]
    return toks[-1] if toks else ""


class NameIndex:
    """(team, surname) -> pids seen in the history (latest team per player)."""

    def __init__(self, hist: pd.DataFrame):
        last = hist.sort_values("date").groupby("pid").tail(1)
        self.map: dict[tuple, set] = {}
        for r in last.itertuples():
            self.map.setdefault((r.team, surname(r.name)), set()).add(r.pid)

    def match(self, team: str, name: str) -> str | None:
        c = self.map.get((team, surname(name)), set())
        return next(iter(c)) if len(c) == 1 else None


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
    return df.assign(pid=df["pid"].replace(mapping)) if mapping else df
