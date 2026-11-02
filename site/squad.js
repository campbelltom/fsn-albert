/* Squad optimiser for the Fantasy Six Nations page. Pure functions; also loadable in node for tests.
 *
 * A team = 15 typed slots (BACK3 x3, CEN x2, FH, SH, BR x3, LOCK x2, PROP x2, HOOK) + `bench` free slots.
 * Value over the chosen rounds R:   sum_{XV} exp  +  sum_r (capMult-1) * max_{XV} exp_r  +  sum_r max_{bench} sup_r
 *                                   + eps * sum_{bench} exp          (tiny, so the bench is the best affordable depth)
 * Constraints: budget, max players per nation (over all 18), no repeats.
 * Method: simulated annealing over slot swaps with penalties for budget / nation breaches, several restarts. */
(function (root) {
  'use strict';

  function slotList(rules) {
    const s = [];
    for (const [pos, n] of Object.entries(rules.squad.xv)) for (let i = 0; i < n; i++) s.push(pos);
    for (let i = 0; i < rules.squad.bench; i++) s.push('BENCH');
    return s;
  }

  function mulberry(seed) {
    return function () { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  function evaluate(team, slots, rounds, rules, eps) {
    // team: array of player objects aligned with slots
    const capX = rules.captain_multiplier - 1;
    let v = 0;
    for (let i = 0; i < slots.length; i++) {
      const p = team[i];
      if (slots[i] === 'BENCH') { for (const r of rounds) v += eps * p.exp[r]; }
      else for (const r of rounds) v += p.exp[r];
    }
    for (const r of rounds) {
      let c = -1e9, s = 0;
      for (let i = 0; i < slots.length; i++) {
        if (slots[i] === 'BENCH') { if (team[i].sup[r] > s) s = team[i].sup[r]; }
        else if (team[i].exp[r] > c) c = team[i].exp[r];
      }
      v += capX * Math.max(0, c) + s;
    }
    return v;
  }

  function penalty(team, rules, budget) {
    let cost = 0; const nat = {};
    for (const p of team) { cost += p.price; nat[p.team] = (nat[p.team] || 0) + 1; }
    let pen = Math.max(0, cost - budget) * 40;
    for (const k in nat) pen += Math.max(0, nat[k] - rules.squad.max_per_nation) * 200;
    return { pen, cost };
  }

  function optimise(players, opts) {
    const rules = opts.rules, rounds = opts.rounds, budget = opts.budget ?? rules.squad.budget;
    const keep = new Set(opts.keep || []), ban = new Set(opts.ban || []);
    const eps = opts.eps ?? 0.02;
    const slots = slotList(rules);
    const pool = players.filter(p => !ban.has(p.id));
    const byPos = {};
    for (const p of pool) (byPos[p.pos] = byPos[p.pos] || []).push(p);
    const cand = slot => slot === 'BENCH' ? pool : (byPos[slot] || []);
    const total = p => rounds.reduce((a, r) => a + p.exp[r], 0);
    const rng = mulberry(opts.seed ?? 12345);
    const restarts = opts.restarts ?? 8, iters = opts.iters ?? 30000;
    const keepers = pool.filter(p => keep.has(p.id));
    let best = null;

    for (let rs = 0; rs < restarts; rs++) {
      // start: place keepers, then fill each slot with a random good-value cheap-ish player
      const team = new Array(slots.length).fill(null);
      const used = new Set();
      const frozen = new Array(slots.length).fill(false);
      for (const k of keepers) {
        let i = slots.findIndex((s, ix) => !team[ix] && s === k.pos);
        if (i < 0) i = slots.findIndex((s, ix) => !team[ix] && s === 'BENCH');
        if (i >= 0) { team[i] = k; used.add(k.id); frozen[i] = true; }
      }
      for (let i = 0; i < slots.length; i++) {
        if (team[i]) continue;
        const c = cand(slots[i]).filter(p => !used.has(p.id));
        c.sort((a, b) => (total(b) / (b.price + 3)) - (total(a) / (a.price + 3)) + (rng() - 0.5) * 0.6 * (rs ? 1 : 0));
        team[i] = c[Math.min(c.length - 1, Math.floor(rng() * Math.min(6, c.length)))];
        used.add(team[i].id);
      }
      const score = t => { const e = penalty(t, rules, budget); return evaluate(t, slots, rounds, rules, eps) - e.pen; };
      let cur = score(team);
      let T0 = 4, T1 = 0.02;
      const free = slots.map((_, i) => i).filter(i => !frozen[i]);
      let bestLocal = null, bestLocalScore = -1e18;
      for (let it = 0; it < iters; it++) {
        const T = T0 * Math.pow(T1 / T0, it / iters);
        let nt = team, ok = true;
        const mv = rng();
        if (mv < 0.85 || free.length < 2) {
          const i = free[Math.floor(rng() * free.length)];
          const c = cand(slots[i]);
          const p = c[Math.floor(rng() * c.length)];
          if (!p || used.has(p.id)) continue;
          nt = team.slice(); nt[i] = p;
          const sc = score(nt), d = sc - cur;
          if (d >= 0 || rng() < Math.exp(d / T)) { used.delete(team[i].id); used.add(p.id); team[i] = p; cur = sc; }
        } else {                                   // swap an XV player with a bench player (or two XV players of same pos group)
          const i = free[Math.floor(rng() * free.length)], j = free[Math.floor(rng() * free.length)];
          if (i === j) continue;
          const si = slots[i], sj = slots[j];
          if (!((si === 'BENCH' || team[j].pos === si) && (sj === 'BENCH' || team[i].pos === sj))) continue;
          nt = team.slice(); const t = nt[i]; nt[i] = nt[j]; nt[j] = t;
          const sc = score(nt), d = sc - cur;
          if (d >= 0 || rng() < Math.exp(d / T)) { team[i] = nt[i]; team[j] = nt[j]; cur = sc; }
        }
        const e = penalty(team, rules, budget);
        if (e.pen === 0 && cur > bestLocalScore) { bestLocalScore = cur; bestLocal = team.slice(); }
      }
      if (bestLocal && (!best || bestLocalScore > best.score)) best = { team: bestLocal, score: bestLocalScore };
    }
    if (!best) return null;
    return describe(best.team, slots, rounds, rules, budget, eps);
  }

  function describe(team, slots, rounds, rules, budget, eps) {
    const xv = [], bench = [];
    team.forEach((p, i) => (slots[i] === 'BENCH' ? bench : xv).push(p));
    const perRound = rounds.map(r => {
      const cap = xv.reduce((a, p) => (p.exp[r] > a.exp[r] ? p : a), xv[0]);
      const sub = bench.reduce((a, p) => (p.sup[r] > a.sup[r] ? p : a), bench[0]);
      const pts = xv.reduce((a, p) => a + p.exp[r], 0) + (rules.captain_multiplier - 1) * cap.exp[r] + (sub ? sub.sup[r] : 0);
      return { round: r, captain: cap.id, supersub: sub ? sub.id : null, points: pts };
    });
    const objective = evaluate(team, slots, rounds, rules, eps);
    const cost = team.reduce((a, p) => a + p.price, 0);
    return { xv, bench, perRound, points: perRound.reduce((a, x) => a + x.points, 0), objective, cost, budget };
  }

  const api = { optimise, evaluate, slotList };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.SQUAD = api;
})(typeof window !== 'undefined' ? window : globalThis);
