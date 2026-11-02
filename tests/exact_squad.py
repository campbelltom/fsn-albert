"""Exact MILP (HiGHS) for the same objective as site/squad.js, to check the annealer.

    python tests/exact_squad.py site_data/predictions.json [--rounds 0,1,2,3,4]"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

ROOT = Path(__file__).resolve().parent.parent


def exact(players, rules, rounds, eps=0.02, budget=None):
    n, R = len(players), len(rounds)
    budget = budget or rules["squad"]["budget"]
    # vars: xv[n], bn[n], cap[n*R], sub[n*R]
    nv = 2 * n + 2 * n * R
    ix_x = lambda i: i                       # noqa: E731
    ix_b = lambda i: n + i                   # noqa: E731
    ix_c = lambda i, k: 2 * n + k * n + i    # noqa: E731
    ix_s = lambda i, k: 2 * n + n * R + k * n + i  # noqa: E731
    c = np.zeros(nv)
    capx = rules["captain_multiplier"] - 1
    for i, p in enumerate(players):
        tot = sum(p["exp"][r] for r in rounds)
        c[ix_x(i)] = -tot
        c[ix_b(i)] = -eps * tot
        for k, r in enumerate(rounds):
            c[ix_c(i, k)] = -capx * p["exp"][r]
            c[ix_s(i, k)] = -p["sup"][r]
    rows, lo, hi = [], [], []
    def add(coefs, l, h):
        rows.append(coefs); lo.append(l); hi.append(h)
    for pos, cnt in rules["squad"]["xv"].items():
        add({ix_x(i): 1 for i, p in enumerate(players) if p["pos"] == pos}, cnt, cnt)
    add({ix_b(i): 1 for i in range(n)}, rules["squad"]["bench"], rules["squad"]["bench"])
    for i in range(n):
        add({ix_x(i): 1, ix_b(i): 1}, 0, 1)
    add({**{ix_x(i): p["price"] for i, p in enumerate(players)}, **{ix_b(i): p["price"] for i, p in enumerate(players)}}, 0, budget)
    for t in sorted({p["team"] for p in players}):
        d = {}
        for i, p in enumerate(players):
            if p["team"] == t:
                d[ix_x(i)] = 1; d[ix_b(i)] = 1
        add(d, 0, rules["squad"]["max_per_nation"])
    for k in range(R):
        add({ix_c(i, k): 1 for i in range(n)}, 1, 1)
        add({ix_s(i, k): 1 for i in range(n)}, 1, 1)
        for i in range(n):
            add({ix_c(i, k): 1, ix_x(i): -1}, -1, 0)
            add({ix_s(i, k): 1, ix_b(i): -1}, -1, 0)
    A = lil_matrix((len(rows), nv))
    for r, d in enumerate(rows):
        for j, v in d.items():
            A[r, j] = v
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=np.ones(nv), bounds=Bounds(0, 1), options={"time_limit": 120})
    return -res.fun, res


def js_value(data_path, rounds):
    code = f"""
const S=require('{ROOT}/site/squad.js'); const d=JSON.parse(require('fs').readFileSync('{data_path}','utf8'));
const r=S.optimise(d.players,{{rules:d.rules,rounds:{json.dumps(rounds)},restarts:8,iters:30000}});
console.log(JSON.stringify({{obj:r.objective,cost:r.cost,pts:r.points}}));"""
    return json.loads(subprocess.check_output(["node", "-e", code]).decode())


if __name__ == "__main__":
    path = sys.argv[1]
    d = json.load(open(path))
    for rounds in ([0], [0, 1, 2], [0, 1, 2, 3, 4]):
        ex, _ = exact(d["players"], d["rules"], rounds)
        js = js_value(path, rounds)
        gap = (ex - js["obj"]) / ex * 100
        print(f"rounds {rounds}: exact {ex:.2f}  annealer {js['obj']:.2f}  gap {gap:.3f}%  cost {js['cost']}")
