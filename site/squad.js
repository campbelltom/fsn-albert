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

  /* Chips, each playable once, at most one per round:
   *   triple captain: the captain scores xT instead of xC   -> worth (T - C) x the captain's points
   *   co-captains:    a second captain is also doubled      -> worth (C - 1) x the second-best player's points
   * gains[k] = {tc, cc} for the k-th round of the horizon; avail = {tc, cc} (false once used). */
  function chipGains(c1, c2, rules) {
    const C = rules.captain_multiplier, T = rules.triple_captain_multiplier || C;
    return { tc: (T - C) * Math.max(0, c1), cc: (C - 1) * Math.max(0, c2) };
  }

  function bestChips(gains, avail) {
    const R = gains.length, a = avail || {};
    const tcs = a.tc ? [-1, ...Array(R).keys()] : [-1], ccs = a.cc ? [-1, ...Array(R).keys()] : [-1];   // -1 = keep the chip for later
    let best = { value: 0, tc: -1, cc: -1 };
    for (const i of tcs) for (const j of ccs) {
      if (i >= 0 && i === j) continue;
      const v = (i >= 0 ? gains[i].tc : 0) + (j >= 0 ? gains[j].cc : 0);
      if (v > best.value) best = { value: v, tc: i, cc: j };
    }
    return best;
  }

  function evaluate(team, slots, rounds, rules, eps, chips) {
    // team: array of player objects aligned with slots
    const capX = rules.captain_multiplier - 1;
    let v = 0;
    for (let i = 0; i < slots.length; i++) {
      const p = team[i];
      if (slots[i] === 'BENCH') { for (const r of rounds) v += eps * p.exp[r]; }
      else for (const r of rounds) v += p.exp[r];
    }
    const gains = [];
    for (const r of rounds) {
      let c = -1e9, c2 = -1e9, s = 0;
      for (let i = 0; i < slots.length; i++) {
        if (slots[i] === 'BENCH') { if (team[i].sup[r] > s) s = team[i].sup[r]; }
        else { const e = team[i].exp[r]; if (e > c) { c2 = c; c = e; } else if (e > c2) c2 = e; }
      }
      v += capX * Math.max(0, c) + s;
      if (chips) gains.push(chipGains(c, c2, rules));
    }
    return chips ? v + bestChips(gains, chips).value : v;
  }

  function penalty(team, rules, budget, hemi, K) {
    // K = { lam: penalty per unit of overspend, big: penalty per player over a nation/hemisphere limit }, scaled to the pool's value so it works for any price scale
    let cost = 0; const nat = {}, hm = {};
    for (const p of team) { cost += p.price; nat[p.team] = (nat[p.team] || 0) + 1; if (hemi) hm[hemi[p.team]] = (hm[hemi[p.team]] || 0) + 1; }
    let pen = Math.max(0, cost - budget) * K.lam;
    for (const k in nat) pen += Math.max(0, nat[k] - rules.squad.max_per_nation) * K.big;
    if (hemi && rules.squad.max_per_hemisphere) for (const k in hm) pen += Math.max(0, hm[k] - rules.squad.max_per_hemisphere) * K.big;
    return { pen, cost };
  }

  function optimise(players, opts) {
    const rules = opts.captainMult ? Object.assign({}, opts.rules, { captain_multiplier: opts.captainMult }) : opts.rules;
    const rounds = opts.rounds, budget = opts.budget ?? rules.squad.budget, hemi = opts.hemisphere || null;
    const keep = new Set(opts.keep || []), ban = new Set(opts.ban || []);
    const eps = opts.eps ?? 0.02;
    const chips = opts.chips || null;               // {tc, cc}: chips still available to plan
    const slots = slotList(rules);
    const pool = players.filter(p => !ban.has(p.id));
    const byPos = {};
    for (const p of pool) (byPos[p.pos] = byPos[p.pos] || []).push(p);
    const cand = slot => slot === 'BENCH' ? pool : (byPos[slot] || []);
    const total = p => rounds.reduce((a, r) => a + p.exp[r], 0);
    const ratios = pool.map(p => total(p) / p.price).sort((a, b) => a - b);
    const K = { lam: 2.5 * (ratios[Math.floor(ratios.length * 0.75)] || 1), big: 3 * Math.max(1, ...pool.map(total)) };
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
      const score = t => { const e = penalty(t, rules, budget, hemi, K); return evaluate(t, slots, rounds, rules, eps, chips) - e.pen; };
      let cur = score(team);
      let T0 = 0.15 * (Math.max(1, ...pool.map(total)) / Math.max(1, rounds.length)) , T1 = 0.02 * rounds.length;
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
        const e = penalty(team, rules, budget, hemi, K);
        if (e.pen === 0 && cur > bestLocalScore) { bestLocalScore = cur; bestLocal = team.slice(); }
      }
      if (bestLocal && (!best || bestLocalScore > best.score)) best = { team: bestLocal, score: bestLocalScore };
    }
    if (!best) return null;
    return describe(best.team, slots, rounds, rules, budget, eps, chips);
  }

  // Where to play each available chip for a squad over the horizon; a round is provisional while any of its XV is not yet named.
  function planChips(xv, rounds, rules, chips) {
    const ranked = rounds.map(r => xv.slice().sort((a, b) => b.exp[r] - a.exp[r]));
    const gains = ranked.map((s, k) => chipGains(s[0].exp[rounds[k]], s[1] ? s[1].exp[rounds[k]] : 0, rules));
    const best = bestChips(gains, chips);
    return {
      best,
      rounds: rounds.map((r, k) => ({
        round: r, tc: gains[k].tc, cc: gains[k].cc, captain: ranked[k][0].id, second: ranked[k][1] ? ranked[k][1].id : null,
        provisional: xv.some(p => !(p.fx && p.fx[r] && p.fx[r].ln)),
      })),
    };
  }

  function describe(team, slots, rounds, rules, budget, eps, chips) {
    const xv = [], bench = [];
    team.forEach((p, i) => (slots[i] === 'BENCH' ? bench : xv).push(p));
    const plan = chips ? planChips(xv, rounds, rules, chips) : null;
    const perRound = rounds.map((r, k) => {
      const cap = xv.reduce((a, p) => (p.exp[r] > a.exp[r] ? p : a), xv[0]);
      const sub = bench.reduce((a, p) => (p.sup[r] > a.sup[r] ? p : a), bench[0]);
      const chip = plan && plan.best.tc === k ? 'TC' : plan && plan.best.cc === k ? 'CC' : null;
      const bonus = chip === 'TC' ? plan.rounds[k].tc : chip === 'CC' ? plan.rounds[k].cc : 0;
      const pts = xv.reduce((a, p) => a + p.exp[r], 0) + (rules.captain_multiplier - 1) * cap.exp[r] + (sub ? sub.sup[r] : 0) + bonus;
      return { round: r, captain: cap.id, coCaptain: chip === 'CC' ? plan.rounds[k].second : null, chip, supersub: sub ? sub.id : null, points: pts };
    });
    const objective = evaluate(team, slots, rounds, rules, eps, chips);
    const cost = team.reduce((a, p) => a + p.price, 0);
    return { xv, bench, perRound, chips: plan, points: perRound.reduce((a, x) => a + x.points, 0), objective, cost, budget };
  }

  const api = { optimise, evaluate, slotList, planChips, bestChips, chipGains };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.SQUAD = api;
})(typeof window !== 'undefined' ? window : globalThis);
