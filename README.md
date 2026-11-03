# FSN - Albert (Fantasy Six Nations projections)

Expected Fantasy Six Nations points per player for every round of the next championship, plus a squad builder.
Same shape as `fpl-projections`: Python model -> encrypted JSON -> single-page site on GitHub Pages, rebuilt by Actions.

## Data
- **Primary: RFU match centres** (`englandrugby.com`, Opta widgets). `scripts/scrape_rfu.py` renders each Six Nations match page with
  Playwright and reads the tables with `fsnmodel/rfu_extract.js`: lineup (shirt number, substitution minutes) and per player tries, metres, carries,
  defenders beaten, clean breaks, passes, offloads, turnovers conceded, try assists, tackles, missed tackles, **turnovers won**, kicks, conversions,
  penalty goals, drop goals, lineouts won/stolen, penalties conceded, cards. Finished matches are cached; only new pages are fetched.
- **Beyond the Six Nations:** the model is competition-agnostic. Every international in `data/rfu/` feeds one joint attack/defence rating per
  nation and the per-position priors; only players whose latest team is a Six Nations side are exported to the site. Extra matches come from
  `python scripts/scrape_rfu.py --england-tests` (England's autumn series pages, both squads) and `config/rfu_matches.json` (any `match-centre` /
  `match-centre-elite` URL, e.g. South Africa v New Zealand). Page types other than the Six Nations ones are untested against the live site.
- **Fallback: ESPN** public JSON (`fsnmodel/espn.py`) for matches the RFU scrape cannot get. ESPN has no turnovers won and no substitution minutes
  (minutes are estimated and the player is flagged).
- Not in either source: 50:22 kicks and Player of the Match. 50:22 is a config constant (0); POTM is a heuristic (win probability x share of attacking output).

## Model (`fsnmodel/model.py`)
1. Team strength: recency-weighted points for/against, shrunk to the league mean; home advantage; normal margin model -> win probability.
2. Appearance: P(start) and P(off the bench) from the player's last team matches (recency weighted, shrunk to position priors), minutes if starting / replacing.
3. Rates per 80 minutes for each scored action, empirical-Bayes shrunk to the position average.
4. Context: attacking actions scale with the team's expected score in that fixture relative to its norm; tackles with the opposition's.
5. Points under `config/rules.json`; captain x2 and supersub x3 / x0.5 handled in the squad builder.

## Competitions, snapshots and evaluation
- `config/competitions.json` picks the active competition (teams in scope, fixtures, rules). Now: **Nations Championship 2026** (rounds 4-6 projected; the three July
  rounds are history). Switch `active` to `six-nations` for February 2027. The NC fantasy rules are not confirmed: `config/rules_nc.json` assumes the 2025 Six Nations rules.
- Each run freezes a projection snapshot per round until its first fixture date (`data/snapshots`, cached between runs). `scripts/backtest.py` runs a walk-forward backtest
  (project each round from earlier matches only) and compares frozen snapshots with actual points; results appear in the Actions run summary.
- `scripts/scrape_espn.py --discover` prints every ESPN rugby league id; add more (Rugby Championship, autumn series) to `config/espn_leagues.json` for more history.

## Rules / config
- `config/rules.json`: scoring (2025 official), squad shape, budget (245), max 4 per nation, captain and supersub multipliers. Edit and push; the site footer shows what is in force.
- `config/fixtures_2027.json`: announced fixtures.
- `config/overrides.json`: exclude/include players, set availability (injuries), fix a position.
- `config/prices.csv` (optional, `id,price`): official prices once published; otherwise stars are estimated from projected points (marked *).

## Squad builder
`site/squad.js` annealer over 15 positional slots + bench; budget and nation limits; Keep/Ban from the Players tab. `python tests/exact_squad.py site/data/predictions.json`
compares it with an exact MILP (HiGHS); on test data it is within 0.3%.

## Deploy
Actions (`.github/workflows/update.yml`) scrape, run the model, encrypt with the `FSN_SITE_PASSWORD` secret and publish to GitHub Pages every 3 hours and on push.
Local: `python tests/make_fixture.py /tmp/rfu && python scripts/run_production.py --rfu /tmp/rfu --out site/data --no-encrypt`, then serve `site/`.
