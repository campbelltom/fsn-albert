() => {
  // Runs inside an RFU (englandrugby.com) match-centre page after the Opta widgets have rendered.
  // Returns one match: teams, score, date, and per-player lineup + stats keyed by Opta player id.
  const txt = s => (s || '').replace(/\s+/g, ' ').trim();
  const num = s => { const v = parseFloat(txt(s)); return Number.isFinite(v) ? v : 0; };
  const out = { teams: [], score: null, date: null, venue: null, players: {} };

  // ---- hero: "France 48 - 46 England", date, venue
  const main = document.querySelector('main');
  const lines = main ? main.innerText.split('\n').map(txt).filter(Boolean) : [];
  const di = lines.indexOf('-');
  if (di > 1 && /^\d+$/.test(lines[di - 1]) && /^\d+$/.test(lines[di + 1])) {
    out.score = { home: +lines[di - 1], away: +lines[di + 1], homeName: lines[di - 2], awayName: lines[di + 2] };
  }
  const dm = lines.find(l => /^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day \d{1,2} \w+ \d{4}$/.test(l));
  if (dm) out.date = dm;
  const vi = lines.findIndex(l => l.startsWith('Venue'));
  if (vi >= 0) out.venue = lines[vi].replace(/^Venue/, '');

  // ---- lineup: shirt, id, substitution events
  document.querySelectorAll('table.Opta-Squad tbody.Opta-Team').forEach(tb => {
    const team = txt(tb.querySelector('tr.Opta-Name h3')?.innerText);
    if (!out.teams.includes(team)) out.teams.push(team);
    tb.querySelectorAll('tr.Opta-Player').forEach(tr => {
      const m = tr.className.match(/Opta-Player-(\d+)/);
      if (!m) return;
      const id = m[1];
      const p = out.players[id] || (out.players[id] = { id, stats: {} });
      p.team = team;
      p.shirt = parseInt(txt(tr.querySelector('td.Opta-Shirt')?.innerText), 10) || null;
      p.name = txt(tr.querySelector('a')?.innerText);
      p.events = [];
      tr.querySelectorAll('span.Opta-Icon').forEach(ic => {
        const nx = ic.nextElementSibling;
        const t = nx ? nx.querySelector('.Opta-Event-Time') : null;
        const type = ic.classList.contains('Opta-IconOff') ? 'off' : ic.classList.contains('Opta-IconOn') ? 'on'
          : (ic.getAttribute('title') || ic.className).toString();
        p.events.push({ type, min: t ? parseInt(t.textContent, 10) : null });
      });
    });
  });

  // ---- stats tables (hidden tabs are still in the DOM)
  const kind = heads => heads.includes('DB') ? 'attack' : heads.includes('MT') ? 'defence'
    : heads.includes('PG') ? 'kicking' : heads.includes('LS') ? 'setplay' : heads.includes('PC') ? 'discipline' : null;
  document.querySelectorAll('[class*=c111] table.Opta-Striped').forEach(t => {
    const hr = t.rows[0];
    if (!hr) return;
    const cells = [...hr.cells].map(c => txt(c.innerText));
    const team = cells[0];
    const heads = cells.slice(1);
    const k = kind(heads);
    if (!k) return;
    [...t.rows].slice(1).forEach(r => {
      const th = r.querySelector('th');
      const m = th && th.className.match(/Opta-Player-(\d+)/);
      if (!m) return;
      const p = out.players[m[1]] || (out.players[m[1]] = { id: m[1], stats: {}, team });
      if (!p.name) p.name = txt(th.innerText);
      if (!p.team) p.team = team;
      const tds = [...r.querySelectorAll('td')];
      heads.forEach((h, i) => { p.stats[k + ':' + h] = num(tds[i] ? (tds[i].getAttribute('data-srt') ?? tds[i].innerText) : ''); });
    });
  });
  out.players = Object.values(out.players);
  return out;
}
