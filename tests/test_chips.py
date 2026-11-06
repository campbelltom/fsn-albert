"""Chip planning in site/squad.js (triple captain and co-captains, each once, one per round). Runs the site code in node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "config" / "rules_nc.json").read_text())
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run site/squad.js")


def js(body: str):
    """Run `body` with S = squad.js and RULES loaded; it must print JSON."""
    code = f"const S = require({json.dumps(str(ROOT / 'site' / 'squad.js'))}); const RULES = {json.dumps(RULES)};\n{body}"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_one_chip_per_round_and_best_pair():
    # both chips are worth most in round 0, but they cannot share it: best legal pair is TC in round 1 + CC in round 0
    r = js("console.log(JSON.stringify(S.bestChips([{tc:10,cc:9},{tc:8,cc:2}], {tc:true, cc:true})))")
    assert (r["tc"], r["cc"], r["value"]) == (1, 0, 17)
    r = js("console.log(JSON.stringify(S.bestChips([{tc:10,cc:9}], {tc:true, cc:true})))")
    assert r["tc"] != r["cc"] and r["value"] == 10, "with one round, only the better chip is planned"


def test_used_chips_are_not_planned():
    r = js("console.log(JSON.stringify(S.bestChips([{tc:10,cc:9},{tc:8,cc:2}], {tc:false, cc:true})))")
    assert r["tc"] == -1 and r["cc"] == 0 and r["value"] == 9
    r = js("console.log(JSON.stringify(S.bestChips([{tc:10,cc:9}], {tc:false, cc:false})))")
    assert (r["tc"], r["cc"], r["value"]) == (-1, -1, 0)


def test_chip_values_follow_the_rules():
    # captain x2, triple captain x3: TC adds one more lot of the captain; CC doubles the second-best player
    r = js("console.log(JSON.stringify(S.chipGains(20, 12, RULES)))")
    assert r == {"tc": (RULES["triple_captain_multiplier"] - RULES["captain_multiplier"]) * 20, "cc": (RULES["captain_multiplier"] - 1) * 12}


POOL = """
const pos = Object.entries(RULES.squad.xv).flatMap(([p, n]) => Array(n * 3).fill(p));
const nations = ['England','France','Ireland','Italy','Scotland','Wales','Argentina','Australia','New Zealand','South Africa','Japan','Fiji'];
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const players = pos.map((p, i) => {
  const exp = [0, 1, 2].map(() => 4 + 16 * rnd());
  return { id: 'p' + i, name: 'P' + i, pos: p, team: nations[i % nations.length], price: 3 + Math.round(rnd() * 70) / 10,
           exp, sup: exp.map(x => 0.8 * x), fx: [0, 1, 2].map(r => ({ ln: r === 0 ? 'S' : null })) };
});
const hemi = Object.fromEntries(nations.map((n, i) => [n, i < 6 ? 'N' : 'S']));
"""


def test_squad_points_include_exactly_the_planned_chips():
    r = js(POOL + """
const base = { rules: RULES, rounds: [0, 1, 2], hemisphere: hemi, restarts: 2, iters: 4000 };
const a = S.optimise(players, Object.assign({}, base, { chips: { tc: true, cc: true } }));
const sumRounds = a.perRound.reduce((s, x) => s + x.points, 0);
const noChipPts = a.perRound.reduce((s, x, k) => s + x.points - (x.chip === 'TC' ? a.chips.rounds[k].tc : x.chip === 'CC' ? a.chips.rounds[k].cc : 0), 0);
console.log(JSON.stringify({ points: a.points, sumRounds, noChipPts, bonus: a.chips.best.value, chips: a.perRound.map(x => x.chip),
  coCaptain: a.perRound.map(x => x.coCaptain), captain: a.perRound.map(x => x.captain),
  prov: a.chips.rounds.map(x => x.provisional) }));
""")
    assert abs(r["points"] - r["sumRounds"]) < 1e-9
    assert abs(r["points"] - r["noChipPts"] - r["bonus"]) < 1e-9, "the chip bonus is counted exactly once"
    assert sorted(c for c in r["chips"] if c) == ["CC", "TC"], "both chips planned, in different rounds"
    k = r["chips"].index("CC")
    assert r["coCaptain"][k] and r["coCaptain"][k] != r["captain"][k], "the co-captain is a second player"
    assert r["prov"] == [False, True, True], "rounds with unnamed teams are provisional"


def test_no_chips_matches_the_old_objective():
    r = js(POOL + """
const opts = { rules: RULES, rounds: [0, 1, 2], hemisphere: hemi, restarts: 2, iters: 4000 };
const a = S.optimise(players, opts);
console.log(JSON.stringify({ chips: a.chips, chipRounds: a.perRound.map(x => x.chip) }));
""")
    assert r["chips"] is None and r["chipRounds"] == [None, None, None]
