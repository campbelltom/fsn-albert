"""Projection model: team strength -> match context -> player appearance and per-80 rates -> expected fantasy points.

Everything is recency weighted (half-life in days) and shrunk towards position priors, so a player with two caps is
pulled hard towards 'a typical player in his position' while a regular is mostly his own numbers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .common import BENCH_POS, CLUB_COMPETITIONS, FORWARDS, GROUP_DEFAULT_ROLE, POSITIONS, ROLE_GROUP, SHIRT_POS, group_of_role, role_of
from .data import MATCH_MIN, international
from .scoring import COMPONENTS, points_from_stats, scrums_for

HALF_LIFE_DAYS = 420.0       # player form
TEAM_HALF_LIFE = 540.0       # team strength
K_RATE = {"tries": 5.0, "assists": 6.0, "metres": 3.0, "db": 4.0, "tackles": 3.0, "tw": 6.0, "ls": 10.0,
          "pc": 6.0, "yc": 40.0, "rc": 80.0, "conv": 3.0, "pen": 3.0, "dg": 20.0,
          "offloads": 4.0, "cb": 5.0, "missed": 4.0, "lw": 4.0, "err": 5.0}
STATS = list(K_RATE)
MARGIN_SD = 14.0
PARAMS = {"half_life": 900.0, "team_hl": TEAM_HALF_LIFE, "k_scale": 0.4, "ctx": 0.5,   # tuned on 2023-26 walk-forward backtests (scripts/tune.py)
          "club_weight": 0.0,                        # how much one club match counts towards a player's form, relative to a Test (0 = ignore club matches)
          "role_detail": "shirt", "k_team": 8.0}     # role_detail: "shirt" = 11 roles by shirt number, "group" = the 8 fantasy positions; k_team: pseudo 80-min games pulling a team's role baseline to the global one


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
    """Rates per 80 minutes, built in three layers:
         global baseline per role  ->  this team's baseline in that role (team style, shrunk to global)  ->  x this player's multiplier (his edge over the baseline).
    A fly-half at full-back therefore gets the team's full-back baseline times his own edge, not fly-half numbers."""

    def __init__(self, hist: pd.DataFrame, pos: pd.Series, asof: pd.Timestamp, rules: dict):
        self.R, self.asof = rules, asof
        h = hist.copy()
        h["club"] = h["competition"].isin(CLUB_COMPETITIONS)
        h["pos"] = h["pid"].map(pos)
        h["age"] = (asof - h["date"]).dt.days.clip(lower=0)
        h["w"] = wt(h["age"], PARAMS["half_life"])
        h["played"] = h["minutes"] > 0
        h["sub_on"] = h["played"] & ~h["started"]
        det = PARAMS["role_detail"]
        h["role"] = [role_of(s, det) for s in h["shirt"]]
        known = h.dropna(subset=["role"])
        modal = lambda d: d.groupby("pid")["role"].agg(lambda x: x.value_counts().index[0]).to_dict() if len(d) else {}  # noqa: E731
        self.modal_role = modal(known[known["club"]]) | modal(known[~known["club"]])      # his Test role where he has one
        dflt = lambda g: (g if det == "group" else GROUP_DEFAULT_ROLE.get(g, "WING"))  # noqa: E731
        h["role"] = h["role"].fillna(h["pid"].map(self.modal_role)).fillna(h["pos"].map(dflt)).fillna("WING" if det != "group" else "BACK3")
        full, h = h, h[~h["club"]]                               # baselines, selection and minutes: national-team matches only
        self.h, self.pos, self.det = h, pos, det
        self.tw_known = h["tw"].notna().any()
        cols = self._cols()
        a_ = h[h["minutes"] >= 10]
        # ---- global per-role and per-group baselines (per 80 min)
        def per80(g):
            mins = g["minutes"].sum()
            return {s: (float(g[c].fillna(0).sum() / mins * MATCH_MIN) if mins > 0 else None) for s, c in cols.items()}
        allr = per80(a_)
        self.G = {}
        grp_rows = {}
        for r, g in a_.groupby("role"):
            self.G[r] = per80(g)
        for gname, g in a_.assign(grp=a_["role"].map(group_of_role)).groupby("grp"):
            grp_rows[gname] = per80(g)
        self._allr, self._grp = allr, grp_rows
        for r in list(self.G):                                   # fill gaps from the group, then the overall rate
            for s in cols:
                if self.G[r][s] is None:
                    self.G[r][s] = grp_rows.get(group_of_role(r), allr).get(s) or allr.get(s) or 0.0
        # ---- minutes by role
        gs = a_[a_["started"]]; gb = a_[~a_["started"]]
        self.start_min = {r: self._m(g["minutes"], 65.0) for r, g in gs.groupby("role")}
        self.sub_min = {r: self._m(g["minutes"], 22.0) for r, g in gb.groupby("role")}
        self._start_all, self._sub_all = self._m(gs["minutes"], 65.0), self._m(gb["minutes"], 22.0)
        # ---- who comes on from the bench, by shirt number (16-23): probability and minutes
        bn = h[h["shirt"].fillna(0) >= 16]
        allon = bn[bn["minutes"] > 0]
        self.bench_on, self.bench_min = {}, {}
        for sh in range(16, 24):
            g = bn[bn["shirt"] == sh]
            on = g[g["minutes"] > 0]
            self.bench_on[sh] = float(len(on) / len(g)) if len(g) >= 8 else (float(len(allon) / len(bn)) if len(bn) else 0.8)
            self.bench_min[sh] = float(on["minutes"].mean()) if len(on) >= 5 else (float(allon["minutes"].mean()) if len(allon) else 22.0)
        # ---- team x role baselines (recency weighted, shrunk to the global role rate)
        cw = PARAMS["club_weight"]
        p = full[(full["minutes"] >= 1) & (~full["club"] | (cw > 0))].copy()
        p["w"] = p["w"] * np.where(p["club"], cw, 1.0)
        p["e80"] = p["w"] * p["minutes"] / MATCH_MIN
        # clubs are compared with other clubs: a player's edge over his club's baseline then carries over to his national team's baseline
        club_a = full[full["club"] & (full["minutes"] >= 10)]
        self.Gc = {r: per80(g) for r, g in club_a.groupby("role")}
        club_teams = set(p.loc[p["club"], "team"])
        kT = PARAMS["k_team"]
        Tser = {}
        for s, c in cols.items():
            pp = p if s != "tw" else p[p["tw"].notna()]
            gb = pp.assign(_v=pp[c].fillna(0) * pp["w"]).groupby(["team", "role"]).agg(v=("_v", "sum"), e=("e80", "sum"))
            gr = np.array([self._club_rate(r, s) if t in club_teams else self.g_rate(r, s) for (t, r) in gb.index])
            gb["rate"] = (gb["v"] + kT * gr) / (gb["e"] + kT)
            Tser[s] = gb["rate"].to_dict()
        keys = set(zip(p["team"], p["role"]))
        self.T = {key: {s: Tser[s].get(key, self._club_rate(key[1], s) if key[0] in club_teams else self.g_rate(key[1], s)) for s in cols} for key in keys}
        # ---- player multipliers: observed / expected given the (team, role) baselines of the matches he played
        bdf = pd.DataFrame({s: [self.T[(t, r)][s] for t, r in zip(p["team"], p["role"])] for s in cols}, index=p.index)
        mask = {s: (p["tw"].notna().astype(float) if s == "tw" else 1.0) for s in cols}
        obs = pd.DataFrame({s: p[c].fillna(0) * p["w"] * mask[s] for s, c in cols.items()}).groupby(p["pid"]).sum()
        ex = pd.DataFrame({s: bdf[s] * p["e80"] * mask[s] for s in cols}).groupby(p["pid"]).sum()
        ee = pd.DataFrame({s: p["e80"] * mask[s] for s in cols}).groupby(p["pid"]).sum()
        kk = pd.Series({s: K_RATE[s] * PARAMS["k_scale"] for s in cols})
        rbar = (ex / ee).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        u = ((obs + kk * rbar) / (ex + kk * rbar)).where((ex > 1e-9) & (rbar > 0), 1.0).clip(0.2, 5.0)
        self.u = {pid: row.to_dict() for pid, row in u.iterrows()}

    def _club_rate(self, role: str, s: str) -> float:
        v = self.Gc.get(role, {}).get(s)
        return float(v) if v is not None else self.g_rate(role, s)

    def g_rate(self, role: str, s: str) -> float:
        g = self.G.get(role)
        v = g.get(s) if g else None
        if v is None:
            v = self._grp.get(group_of_role(role), self._allr).get(s)
        if v is None:
            v = self._allr.get(s)
        return float(v or 0.0)

    @staticmethod
    def _m(x: pd.Series, default: float) -> float:
        v = x.mean() if len(x) else float("nan")
        return default if v != v or v <= 0 else float(v)

    @staticmethod
    def _cols() -> dict:
        return {"tries": "tries", "assists": "assists", "metres": "metres", "db": "db", "tackles": "tackles", "tw": "tw",
                "ls": "ls", "pc": "pc", "yc": "yc", "rc": "rc", "conv": "conv", "pen": "pen", "dg": "dg",
                "offloads": "offloads", "cb": "cb", "missed": "missed", "lw": "lw", "err": "err"}

    # ---- roles
    def role_probs(self, pid: str, fallback: str) -> dict:
        """Share of his recent appearances in each role (recency weighted): a fly-half who also plays full-back gets both."""
        g = self.h[(self.h["pid"] == pid) & (self.h["minutes"] >= 1)]
        if len(g) == 0:
            return {fallback: 1.0}
        sh = g.groupby("role")["w"].sum()
        sh = sh / sh.sum()
        sh = sh[sh >= 0.08]
        return {r: float(v / sh.sum()) for r, v in sh.items()}

    def rates(self, pid: str, role: str, team: str) -> dict:
        base = self.T.get((team, role))
        u = self.u.get(pid, {})
        out = {s: (base[s] if base else self.g_rate(role, s)) * u.get(s, 1.0) for s in self._cols()}
        return out

    # ---- availability
    def appearance(self, pid: str, team: str, role: str, n_team_matches: int = 10) -> dict:
        g = self.h[self.h["pid"] == pid]
        t_hist = self.h[self.h["team"] == team].drop_duplicates(["match_key", "team"]).sort_values("date")
        t_hist = t_hist.tail(n_team_matches)
        first = g["date"].min() if len(g) else self.asof
        window = t_hist[t_hist["date"] >= first - pd.Timedelta(days=1)] if len(g) else t_hist.iloc[0:0]
        w_all = wt((self.asof - window["date"]).dt.days.clip(lower=0), 300.0) if len(window) else np.array([])
        gm = g.set_index("match_key")
        started = np.array([float(gm["started"].get(k, False)) if k in gm.index else 0.0 for k in window["match_key"]])
        subon = np.array([float(gm["sub_on"].get(k, False)) if k in gm.index else 0.0 for k in window["match_key"]])
        prior_s = 0.12 if group_of_role(role) != "BACK3" else 0.10
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
        # minutes if starting / if on as a replacement: this player's own history, shrunk to the role
        gs, gb = g[g["started"] & g["played"]], g[g["sub_on"]]
        ms = self._shrunk_mean(gs["minutes"], gs["w"], self.start_min.get(role, self._start_all), 2.0)
        mb = self._shrunk_mean(gb["minutes"], gb["w"], self.sub_min.get(role, self._sub_all), 2.0)
        return dict(p_start=ps, p_sub=pb, min_start=ms, min_sub=mb, caps=int(g["played"].sum()), last=g["date"].max() if len(g) else pd.NaT)

    @staticmethod
    def _shrunk_mean(x: pd.Series, w: pd.Series, prior: float, k: float) -> float:
        if len(x) == 0:
            return prior
        return float((np.average(x, weights=w) * w.sum() + prior * k) / (w.sum() + k))


def project(hist: pd.DataFrame, pos: pd.Series, fixtures: list[dict], rules: dict, asof: pd.Timestamp,
            overrides: dict | None = None, max_age_days: int = 450, scope_teams: set | None = None, lineup_map: dict | None = None, only_pids: set | None = None) -> dict:
    """Returns {players: [...], rounds: [...]} ready for export. scope_teams: only players whose current team is in it (None = everyone)."""
    overrides = overrides or {}
    nat = international(hist)
    tm = TeamModel(nat, asof)
    pm = PlayerModel(hist, pos, asof, rules)
    R = rules
    a = R["assumptions"]
    ov = overrides.get("players", {})
    exclude = set(overrides.get("exclude", []))
    include = set(overrides.get("include", []))

    # current team per player = team of latest appearance (override wins)
    latest = nat.sort_values("date").groupby("pid").tail(1).set_index("pid")
    cand = []
    for pid, r in latest.iterrows():
        if pid in exclude or (only_pids is not None and pid not in only_pids):
            continue
        if scope_teams is not None and ov.get(pid, {}).get("team", r["team"]) not in scope_teams:
            continue                      # capped for another nation: not part of this competition
        age = (asof - r["date"]).days
        if age <= max_age_days or pid in include:
            cand.append(pid)
    lineup_map = lineup_map or {}
    extras = {}
    for (rd_, tm_), mp_ in lineup_map.items():
        for pid_, (sh_, nm_) in mp_.items():
            if pid_ not in latest.index and pid_ not in extras:
                extras[pid_] = (nm_, tm_, sh_)
    rounds = sorted({f["round"] for f in fixtures})
    by_round_team = {}
    for f in fixtures:
        by_round_team[(f["round"], f["home"])] = (f["away"], True, f)
        by_round_team[(f["round"], f["away"])] = (f["home"], False, f)

    players = []
    for pid in list(cand) + list(extras):
        if pid in extras:
            nm_, tm_, sh_ = extras[pid]
            r = {"name": nm_, "team": tm_, "date": asof}
            p = ov.get(pid, {}).get("pos") or (SHIRT_POS.get(sh_) if sh_ <= 15 else BENCH_POS.get(sh_)) or "BACK3"
            team = tm_
        else:
            r = latest.loc[pid]
            team = ov.get(pid, {}).get("team", r["team"])
            p = ov.get(pid, {}).get("pos") or pos[pid]
        if pid in extras:
            mrole = role_of(extras[pid][2], PARAMS["role_detail"]) or (GROUP_DEFAULT_ROLE.get(p, "WING") if PARAMS["role_detail"] != "group" else p)
        else:
            mrole = pm.modal_role.get(pid) or (GROUP_DEFAULT_ROLE.get(p, "WING") if PARAMS["role_detail"] != "group" else p)
        roles_default = pm.role_probs(pid, mrole)
        rate_cache: dict = {}
        rates_for = lambda role, _t=team, _pid=pid: rate_cache.setdefault(role, pm.rates(_pid, role, _t))  # noqa: E731
        ap = pm.appearance(pid, team, mrole)
        e80 = 0.0
        avail = float(ov.get(pid, {}).get("availability", 1.0))
        flags = []
        if ap["caps"] == 0:
            flags.append("no data: position averages")
        elif ap["caps"] <= 2:
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
            atk = float(np.clip(exp_for / tm.base_pf.get(team, tm.avg), 0.6, 1.6))
            dfm = float(np.clip(exp_against / tm.base_pa.get(team, tm.avg), 0.6, 1.6))
            ctx = PARAMS["ctx"]
            atk, dfm = atk ** ctx, dfm ** ctx
            mult = {"tries": atk, "assists": atk, "conv": atk, "pen": 1.0, "dg": 1.0, "metres": atk ** 0.5, "db": atk ** 0.5,
                    "tackles": dfm ** 0.4, "missed": dfm ** 0.4, "tw": 1.0, "ls": 1.0, "pc": 1.0, "yc": 1.0, "rc": 1.0,
                    "offloads": atk ** 0.5, "cb": atk ** 0.5, "lw": 1.0, "err": 1.0}
            w_, d_, l_ = tm.p_win(team, opp) if is_home else tuple(reversed(tm.p_win(opp, team)))
            ps, pb = ap["p_start"] * avail, ap["p_sub"] * avail
            em_start, em_sub = ap["min_start"], ap["min_sub"]
            ln, roles, rl = None, roles_default, None
            lm = lineup_map.get((rd, team))
            if lm is not None:                       # the team's 23 are named: no need to predict who starts
                ent = lm.get(pid)
                if ent is None:
                    ln, ps, pb = "X", 0.0, 0.0
                else:
                    rl = role_of(ent[0], PARAMS["role_detail"]) or mrole          # the shirt he wears decides his role (23 = utility: his usual one)
                    roles = {rl: 1.0}
                    if ent[0] <= 15:
                        ln, ps, pb = "S", 1.0 * avail, 0.0
                    else:
                        ln, ps, pb = "B", 0.0, pm.bench_on.get(ent[0], 0.85) * avail
                        em_sub = pm.bench_min.get(ent[0], em_sub)
            exp_min = ps * em_start + pb * em_sub
            # points if the player starts / comes off the bench: per-80 rates for the role(s) he plays x minutes x context
            def pts_for(mins: float) -> dict:
                acc = {c: 0.0 for c in COMPONENTS if c not in ("potm", "team")}
                for r_, q_ in roles.items():
                    rt = rates_for(r_)          # what he does on the pitch comes from the role (shirt) ...
                    # ... but the game scores him by his listed fantasy position (scrum points only for listed props/hookers, try value by position)
                    sl = {s: rt[s] * mult[s] * mins / MATCH_MIN for s in STATS} | {"f2250": a.get("fifty22_per_80", 0.0) * mins / MATCH_MIN, "scrums": scrums_for(p, mins, R)}
                    c_ = points_from_stats(sl, p, R, floor_metres=False)
                    if sl["metres"] > 0:
                        c_["carrying"] -= 0.45                         # floor-to-10m continuity correction
                    for k_, v_ in c_.items():
                        acc[k_] += q_ * v_
                return acc
            cs, cb = pts_for(em_start), pts_for(em_sub)
            comp = {c: ps * cs.get(c, 0) + pb * cb.get(c, 0) for c in COMPONENTS if c not in ("potm", "team")}
            es = sum(cs.values()); eb = sum(cb.values())
            P = R["points"]
            win_draw = (w_ * P["win"] + d_ * P["draw"])
            comp["team"] = (ps * em_start + pb * em_sub) / MATCH_MIN * win_draw
            comp["potm"] = 0.0   # filled below once the whole squad is known
            pst_r.append(ps)
            es_r.append((es, eb, w_, ps, pb, opp, is_home, f, exp_min, ln, rl))
            comp_round.append(comp)
            per_round.append(0.0)
            sup_round.append(0.0)
        top = sorted(roles_default.items(), key=lambda kv: -kv[1])
        if len(top) > 1 and top[0][1] < 0.85:
            flags.append("plays " + "/".join(r_ for r_, _ in top[:2]))
        players.append(dict(pid=pid, name=r["name"], team=team, pos=p, caps=ap["caps"], e80=e80, flags=flags, roles=[[r_, round(q_, 2)] for r_, q_ in top[:3]],
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
            es, eb, w_, ps, pb, opp, is_home, f, exp_min, ln, rl = raw
            total = sum(c.values())
            S = R["supersub"]
            # value if nominated supersub: x3 when he comes on, x0.5 when he starts / does not come on
            sup = pb * S["on"] * eb + S["off"] * ps * es
            exps.append(round(total, 2)); sups.append(round(sup, 2)); comps.append({k: round(v, 2) for k, v in c.items()})
        fixtures_p = []
        for ri, rd in enumerate(rounds):
            raw = pl["raw"][ri]
            fixtures_p.append(None if not isinstance(raw, tuple) else {"opp": raw[5], "home": raw[6], "ln": raw[9], "rl": raw[10]})
        out_players.append(dict(id=pl["pid"], name=pl["name"], team=pl["team"], pos=pl["pos"], caps=pl["caps"],
                                pStart=round(pl["p_start"], 3), pSub=round(pl["p_sub"], 3),
                                eMin=round(sum(r_[8] for r_ in pl["raw"] if isinstance(r_, tuple)) / max(1, sum(isinstance(r_, tuple) for r_ in pl["raw"])), 1),
                                flags=pl["flags"], roles=pl["roles"], exp=exps, sup=sups, comp=comps, fx=fixtures_p))
    # estimated stars (official prices are not published until the game opens): rank-based, top = 20
    _estimate_prices(out_players, rules)
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


def _estimate_prices(players: list[dict], rules: dict | None = None) -> None:
    """Estimated prices (official ones are not published until the game opens): linear in the percentile of average projected points.
    Stars game: 4..20. Money game: squad.price_min..price_max (e.g. 3.0M..10.0M), so a squad of stars does not fit the budget."""
    if not players:
        return
    sq = (rules or {}).get("squad", {})
    lo, hi, unit = sq.get("price_min", 4), sq.get("price_max", 20), sq.get("price_unit", "star")
    avg = np.array([np.mean(p["exp"]) for p in players])
    q = avg.argsort().argsort() / max(1, len(avg) - 1)
    for p, qi in zip(players, q):
        v = lo + (hi - lo) * qi
        p["price"] = round(v, 1) if unit == "M" else int(round(v))
        p["priceEst"] = True
