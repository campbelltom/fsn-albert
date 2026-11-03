"""Projection model: team strength -> match context -> player appearance and per-80 rates -> expected fantasy points.

Everything is recency weighted (half-life in days) and shrunk towards position priors, so a player with two caps is
pulled hard towards 'a typical player in his position' while a regular is mostly his own numbers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .common import FORWARDS, POSITIONS
from .data import MATCH_MIN
from .scoring import COMPONENTS, points_from_stats

HALF_LIFE_DAYS = 420.0       # player form
TEAM_HALF_LIFE = 540.0       # team strength
K_RATE = {"tries": 5.0, "assists": 6.0, "metres": 3.0, "db": 4.0, "tackles": 3.0, "tw": 6.0, "ls": 10.0,
          "pc": 6.0, "yc": 40.0, "rc": 80.0, "conv": 3.0, "pen": 3.0, "dg": 20.0}
STATS = list(K_RATE)
MARGIN_SD = 14.0
PARAMS = {"half_life": 900.0, "team_hl": TEAM_HALF_LIFE, "k_scale": 0.4, "ctx": 0.5}   # tuned on 2023-26 walk-forward backtests   # tunable (scripts/tune.py)


def wt(days: pd.Series | np.ndarray, hl: float) -> np.ndarray:
    return 0.5 ** (np.asarray(days, dtype=float) / hl)


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


HOME_MULT = 1.06


class TeamModel:
    """Attack/defence for every nation, fitted jointly: points = avg * attack(team) * defence(opp) * home factor.
    Recency weighted, shrunk to 1 with pseudo-matches, so a nation's rating reflects the quality of the opposition it faced."""

    def __init__(self, hist: pd.DataFrame, asof: pd.Timestamp, iters: int = 30, k: float = 3.0):
        m = hist.drop_duplicates(["match_key", "team"])[["team", "opp", "home", "pf", "pa", "date"]].copy()
        m["w"] = wt((asof - m["date"]).dt.days.clip(lower=0), PARAMS["team_hl"])
        m["ha"] = np.where(m["home"], HOME_MULT, 1 / HOME_MULT)
        self.avg = float(np.average(m["pf"], weights=m["w"])) if len(m) else 24.0
        teams = sorted(set(m["team"]) | set(m["opp"]))
        att = {t: 1.0 for t in teams}
        dfn = {t: 1.0 for t in teams}
        for _ in range(iters):
            for t in teams:
                g = m[m["team"] == t]
                if len(g):
                    exp = self.avg * g["opp"].map(dfn).values * g["ha"].values
                    att[t] = ((g["w"] * g["pf"]).sum() + k * self.avg) / ((g["w"].values * exp).sum() + k * self.avg)
            s = np.mean(list(att.values()))
            att = {t: v / s for t, v in att.items()}
            for t in teams:
                g = m[m["opp"] == t]                       # matches where t was the defence
                if len(g):
                    exp = self.avg * g["team"].map(att).values * g["ha"].values
                    dfn[t] = ((g["w"] * g["pf"]).sum() + k * self.avg) / ((g["w"].values * exp).sum() + k * self.avg)
        self.att, self.dfn = att, dfn
        self.base_pf, self.base_pa = {}, {}
        for t in teams:
            g = m[m["team"] == t]
            sw = g["w"].sum()
            kk = 4.0
            self.base_pf[t] = (np.average(g["pf"], weights=g["w"]) * sw + self.avg * kk) / (sw + kk) if sw else self.avg
            self.base_pa[t] = (np.average(g["pa"], weights=g["w"]) * sw + self.avg * kk) / (sw + kk) if sw else self.avg

    def expected_points(self, team: str, opp: str, home: bool) -> float:
        e = self.avg * self.att.get(team, 1.0) * self.dfn.get(opp, 1.0) * (HOME_MULT if home else 1 / HOME_MULT)
        return max(6.0, e)

    def p_win(self, home: str, away: str) -> tuple[float, float, float]:
        mu = self.expected_points(home, away, True) - self.expected_points(away, home, False)
        pw = _phi(mu / MARGIN_SD)
        pd_ = _phi((mu + 0.5) / MARGIN_SD) - _phi((mu - 0.5) / MARGIN_SD)
        return pw - pd_ / 2, pd_, 1 - pw - pd_ / 2    # win, draw, lose for home (draw mass split from the tails)


class PlayerModel:
    def __init__(self, hist: pd.DataFrame, pos: pd.Series, asof: pd.Timestamp, rules: dict):
        self.R, self.asof = rules, asof
        h = hist.copy()
        h["pos"] = h["pid"].map(pos)
        h["age"] = (asof - h["date"]).dt.days.clip(lower=0)
        h["w"] = wt(h["age"], PARAMS["half_life"])
        h["played"] = h["minutes"] > 0
        h["sub_on"] = h["played"] & ~h["started"]
        self.h = h
        self.pos = pos
        # per-80 position priors from appearances with real minutes
        a = h[h["minutes"] >= 10]
        self.prior = {}
        for p in POSITIONS:
            g = a[a["pos"] == p]
            mins = g["minutes"].sum() or 1.0
            self.prior[p] = {s: float(g[col].fillna(0).sum() / mins * MATCH_MIN) for s, col in self._cols().items()}
        gs = a[a["started"]]
        self.start_min = {p: self._m(gs[gs["pos"] == p]["minutes"], 65.0) for p in POSITIONS}
        gb = a[~a["started"]]
        self.sub_min = {p: self._m(gb[gb["pos"] == p]["minutes"], 22.0) for p in POSITIONS}
        # TW (turnovers won) can be missing for fallback data: use the matches that have it
        self.tw_known = h["tw"].notna().any()

    @staticmethod
    def _m(x: pd.Series, default: float) -> float:
        v = x.mean() if len(x) else float("nan")
        return default if v != v or v <= 0 else float(v)

    @staticmethod
    def _cols() -> dict:
        return {"tries": "tries", "assists": "assists", "metres": "metres", "db": "db", "tackles": "tackles", "tw": "tw",
                "ls": "ls", "pc": "pc", "yc": "yc", "rc": "rc", "conv": "conv", "pen": "pen", "dg": "dg"}

    # ---- availability
    def appearance(self, pid: str, team: str, pos: str, n_team_matches: int = 10) -> dict:
        g = self.h[self.h["pid"] == pid]
        t_hist = self.h[self.h["team"] == team].drop_duplicates(["match_key", "team"]).sort_values("date")
        t_hist = t_hist.tail(n_team_matches)
        first = g["date"].min() if len(g) else self.asof
        window = t_hist[t_hist["date"] >= first - pd.Timedelta(days=1)] if len(g) else t_hist.iloc[0:0]
        w_all = wt((self.asof - window["date"]).dt.days.clip(lower=0), 300.0) if len(window) else np.array([])
        gm = g.set_index("match_key")
        started = np.array([float(gm["started"].get(k, False)) if k in gm.index else 0.0 for k in window["match_key"]])
        subon = np.array([float(gm["sub_on"].get(k, False)) if k in gm.index else 0.0 for k in window["match_key"]])
        prior_s = 0.12 if pos != "BACK3" else 0.10
        prior_b = 0.20
        k = 0.75                             # pseudo-matches of prior: a run of 10 starts should read ~0.95, one start ~0.6
        n = len(window)                      # shrink by match count, not by decayed weight
        sw = w_all.sum()
        avg_s = (started * w_all).sum() / sw if sw else 0.0
        avg_b = (subon * w_all).sum() / sw if sw else 0.0
        ps = (n * avg_s + prior_s * k) / (n + k)
        pb = (n * avg_b + prior_b * k) / (n + k)
        tot = ps + pb
        if tot > 0.98:
            ps, pb = ps / tot * 0.98, pb / tot * 0.98
        # minutes if starting / if on as a replacement: this player's own history, shrunk to the position
        gs, gb = g[g["started"] & g["played"]], g[g["sub_on"]]
        ms = self._shrunk_mean(gs["minutes"], gs["w"], self.start_min[pos], 2.0)
        mb = self._shrunk_mean(gb["minutes"], gb["w"], self.sub_min[pos], 2.0)
        return dict(p_start=ps, p_sub=pb, min_start=ms, min_sub=mb, caps=int(g["played"].sum()), last=g["date"].max() if len(g) else pd.NaT)

    @staticmethod
    def _shrunk_mean(x: pd.Series, w: pd.Series, prior: float, k: float) -> float:
        if len(x) == 0:
            return prior
        return float((np.average(x, weights=w) * w.sum() + prior * k) / (w.sum() + k))

    # ---- per-80 rates (empirical Bayes)
    def rates(self, pid: str, pos: str) -> dict:
        g = self.h[(self.h["pid"] == pid) & (self.h["minutes"] >= 1)]
        e80 = (g["minutes"] * g["w"]).sum() / MATCH_MIN        # recency-weighted 80-minute equivalents
        out = {}
        for s, col in self._cols().items():
            pr = self.prior[pos][s]
            if s == "tw" and not self.tw_known:
                out[s] = pr; continue
            gg = g if s != "tw" else g[g["tw"].notna()]
            e = (gg["minutes"] * gg["w"]).sum() / MATCH_MIN
            tot = (gg[col].fillna(0) * gg["w"]).sum()
            k = K_RATE[s] * PARAMS["k_scale"]
            out[s] = float((tot + pr * k) / (e + k))
        return out, float(e80)


def project(hist: pd.DataFrame, pos: pd.Series, fixtures: list[dict], rules: dict, asof: pd.Timestamp,
            overrides: dict | None = None, max_age_days: int = 450, scope_teams: set | None = None) -> dict:
    """Returns {players: [...], rounds: [...]} ready for export. scope_teams: only players whose current team is in it (None = everyone)."""
    overrides = overrides or {}
    tm = TeamModel(hist, asof)
    pm = PlayerModel(hist, pos, asof, rules)
    R = rules
    a = R["assumptions"]
    ov = overrides.get("players", {})
    exclude = set(overrides.get("exclude", []))
    include = set(overrides.get("include", []))

    # current team per player = team of latest appearance (override wins)
    latest = hist.sort_values("date").groupby("pid").tail(1).set_index("pid")
    cand = []
    for pid, r in latest.iterrows():
        if pid in exclude:
            continue
        if scope_teams is not None and ov.get(pid, {}).get("team", r["team"]) not in scope_teams:
            continue                      # capped for another nation: not part of this competition
        age = (asof - r["date"]).days
        if age <= max_age_days or pid in include:
            cand.append(pid)
    rounds = sorted({f["round"] for f in fixtures})
    by_round_team = {}
    for f in fixtures:
        by_round_team[(f["round"], f["home"])] = (f["away"], True, f)
        by_round_team[(f["round"], f["away"])] = (f["home"], False, f)

    players = []
    for pid in cand:
        r = latest.loc[pid]
        team = ov.get(pid, {}).get("team", r["team"])
        p = ov.get(pid, {}).get("pos") or pos[pid]
        ap = pm.appearance(pid, team, p)
        rt, e80 = pm.rates(pid, p)
        avail = float(ov.get(pid, {}).get("availability", 1.0))
        flags = []
        if ap["caps"] <= 2:
            flags.append("few caps")
        if (asof - r["date"]).days > 300:
            flags.append("not seen for a while")
        if avail < 1:
            flags.append("availability %.0f%%" % (avail * 100))
        per_round, comp_round, sup_round, es_r, pst_r = [], [], [], [], []
        for rd in rounds:
            fx = by_round_team.get((rd, team))
            if not fx:
                per_round.append(0.0); comp_round.append({c: 0.0 for c in COMPONENTS}); sup_round.append(0.0); es_r.append(0.0); pst_r.append(0.0)
                continue
            opp, is_home, f = fx
            exp_for = tm.expected_points(team, opp, is_home)
            exp_against = tm.expected_points(opp, team, not is_home)
            atk = float(np.clip(exp_for / tm.base_pf[team], 0.6, 1.6))
            dfm = float(np.clip(exp_against / tm.base_pa[team], 0.6, 1.6))
            ctx = PARAMS["ctx"]
            atk, dfm = atk ** ctx, dfm ** ctx
            mult = {"tries": atk, "assists": atk, "conv": atk, "pen": 1.0, "dg": 1.0, "metres": atk ** 0.5, "db": atk ** 0.5,
                    "tackles": dfm ** 0.4, "tw": 1.0, "ls": 1.0, "pc": 1.0, "yc": 1.0, "rc": 1.0}
            w_, d_, l_ = tm.p_win(team, opp) if is_home else tuple(reversed(tm.p_win(opp, team)))
            ps, pb = ap["p_start"] * avail, ap["p_sub"] * avail
            em_start, em_sub = ap["min_start"], ap["min_sub"]
            exp_min = ps * em_start + pb * em_sub
            # points if the player starts / comes off the bench (per-80 rates x minutes x context)
            def stat_line(mins: float) -> dict:
                return {s: rt[s] * mult[s] * mins / MATCH_MIN for s in STATS} | {"f2250": a.get("fifty22_per_80", 0.0) * mins / MATCH_MIN}
            ls_, lb_ = stat_line(em_start), stat_line(em_sub)
            cs = points_from_stats(ls_, p, R, floor_metres=False)
            cb = points_from_stats(lb_, p, R, floor_metres=False)
            cs["carrying"] -= 0.45 * 1.0 if ls_["metres"] > 0 else 0   # floor-to-10m continuity correction
            cb["carrying"] -= 0.45 * 1.0 if lb_["metres"] > 0 else 0
            comp = {c: ps * cs.get(c, 0) + pb * cb.get(c, 0) for c in COMPONENTS if c not in ("potm", "team")}
            es = sum(cs.values()); eb = sum(cb.values())
            P = R["points"]
            win_draw = (w_ * P["win"] + d_ * P["draw"])
            comp["team"] = (ps * em_start + pb * em_sub) / MATCH_MIN * win_draw
            comp["potm"] = 0.0   # filled below once the whole squad is known
            pst_r.append(ps)
            es_r.append((es, eb, w_, ps, pb, opp, is_home, f, exp_min))
            comp_round.append(comp)
            per_round.append(0.0)
            sup_round.append(0.0)
        players.append(dict(pid=pid, name=r["name"], team=team, pos=p, caps=ap["caps"], e80=e80, flags=flags,
                            p_start=ap["p_start"] * avail, p_sub=ap["p_sub"] * avail, comp=comp_round, raw=es_r))

    # POTM heuristic: P(team wins) share-weighted by the squad's attacking output
    potm_share = R["assumptions"].get("potm_win_share", 0.85)
    for ri, rd in enumerate(rounds):
        for team in sorted({f["home"] for f in fixtures} | {f["away"] for f in fixtures}):
            grp = [pl for pl in players if pl["team"] == team and isinstance(pl["raw"][ri], tuple)]
            if not grp:
                continue
            w_win = grp[0]["raw"][ri][2]
            wts = []
            for pl in grp:
                es, eb, _, ps, pb, *_ = pl["raw"][ri]
                base = ps * es + pb * eb
                wts.append(max(base, 0.01) ** 2 * (ps + 0.3 * pb))
            tot = sum(wts) or 1.0
            p_team = potm_share * w_win + (1 - potm_share) * (1 - w_win)
            for pl, wv in zip(grp, wts):
                pl["comp"][ri]["potm"] = R["points"]["potm"] * p_team * wv / tot

    out_players = []
    for pl in players:
        exps, sups, comps = [], [], []
        for ri, rd in enumerate(rounds):
            c = pl["comp"][ri]
            raw = pl["raw"][ri]
            if not isinstance(raw, tuple):
                exps.append(0.0); sups.append(0.0); comps.append({k: 0.0 for k in COMPONENTS}); continue
            es, eb, w_, ps, pb, opp, is_home, f, exp_min = raw
            total = sum(c.values())
            S = R["supersub"]
            # value if nominated supersub: x3 when he comes on, x0.5 when he starts / does not come on
            sup = pb * S["on"] * eb + S["off"] * ps * es
            exps.append(round(total, 2)); sups.append(round(sup, 2)); comps.append({k: round(v, 2) for k, v in c.items()})
        fixtures_p = []
        for ri, rd in enumerate(rounds):
            raw = pl["raw"][ri]
            fixtures_p.append(None if not isinstance(raw, tuple) else {"opp": raw[5], "home": raw[6]})
        out_players.append(dict(id=pl["pid"], name=pl["name"], team=pl["team"], pos=pl["pos"], caps=pl["caps"],
                                pStart=round(pl["p_start"], 3), pSub=round(pl["p_sub"], 3),
                                eMin=round(sum(r_[8] for r_ in pl["raw"] if isinstance(r_, tuple)) / max(1, sum(isinstance(r_, tuple) for r_ in pl["raw"])), 1),
                                flags=pl["flags"], exp=exps, sup=sups, comp=comps, fx=fixtures_p))
    # estimated stars (official prices are not published until the game opens): rank-based, top = 20
    _estimate_prices(out_players)
    rd_info = []
    for rd in rounds:
        ms = []
        for f in fixtures:
            if f["round"] == rd:
                w_, d_, l_ = tm.p_win(f["home"], f["away"])
                ms.append({"home": f["home"], "away": f["away"], "date": f["date"],
                           "pHome": round(w_ + d_ / 2, 3), "xHome": round(tm.expected_points(f["home"], f["away"], True), 1),
                           "xAway": round(tm.expected_points(f["away"], f["home"], False), 1)})
        rd_info.append({"round": rd, "matches": ms})
    fteams = sorted({f["home"] for f in fixtures} | {f["away"] for f in fixtures})
    teams = {t: {"att": round(tm.att.get(t, 1.0), 3), "def": round(tm.dfn.get(t, 1.0), 3)} for t in fteams}
    return {"players": sorted(out_players, key=lambda x: -sum(x["exp"])), "rounds": rd_info, "teams": teams}


def _estimate_prices(players: list[dict]) -> None:
    """Estimated stars (official prices are not published until the game opens): linear in the percentile of average projected points,
    4 at the bottom and 20 at the top, so a full squad of stars does not fit a 230-star budget."""
    if not players:
        return
    avg = np.array([np.mean(p["exp"]) for p in players])
    q = avg.argsort().argsort() / max(1, len(avg) - 1)
    for p, qi in zip(players, q):
        p["price"] = int(round(4 + 16 * qi))
        p["priceEst"] = True
