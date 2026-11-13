"""Run: python -m pytest tests -q   (or python tests/test_basics.py)"""
import json, math, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fsnmodel.common import rules  # noqa: E402
from fsnmodel.data import minutes_on_pitch  # noqa: E402
from fsnmodel.scoring import points_from_stats  # noqa: E402


def test_minutes():
    assert minutes_on_pitch(1, [], 80) == (80.0, True)
    assert minutes_on_pitch(1, [{"type": "off", "min": 63}, {"type": "on", "min": 78}])[0] == 65.0   # blood bin: Gros v England
    assert minutes_on_pitch(8, [{"type": "off", "min": 39}, {"type": "on", "min": 41}, {"type": "off", "min": 44}, {"type": "on", "min": 52}, {"type": "off", "min": 76}])[0] == 39 + 3 + 24
    assert minutes_on_pitch(22, [{"type": "on", "min": 51}]) == (29.0, False)
    assert minutes_on_pitch(20, []) == (0.0, False)


def test_scoring_2025():
    R = rules()
    s = {"tries": 1, "assists": 1, "conv": 2, "pen": 1, "metres": 19, "db": 3, "tackles": 10, "tw": 1, "ls": 1, "pc": 1, "yc": 1}
    f = sum(points_from_stats(s, "PROP", R).values())          # 15+4+4+3+1+6+10+5+7-1-3
    b = sum(points_from_stats(s, "FH", R).values())
    assert f == 51 and b == 46, (f, b)


def test_pipeline_and_schema():
    import subprocess
    from make_fixture import make, make_extra
    d = Path(tempfile.mkdtemp())
    make(d / "rfu"); make_extra(d / "rfu")
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "run_production.py"), "--rfu", str(d / "rfu"), "--espn", str(d / "none"), "--super", str(d / "none"), "--out", str(d / "out"), "--no-encrypt", "--competition", "six-nations", "--snapshots", str(d / "snaps")])
    j = json.loads((d / "out" / "predictions.json").read_text())
    assert all(p["team"] != "South Africa" for p in j["players"]), "non-Six-Nations players must be filtered out"
    assert j["history"]["matches"] == 48          # 45 Six Nations + 3 tests
    assert len(j["rounds"]) == 5 and all(len(m["matches"]) == 3 for m in j["rounds"])
    for p in j["players"]:
        assert len(p["exp"]) == 5 and all(math.isfinite(x) for x in p["exp"] + p["sup"])
        assert p["pos"] in j["rules"]["squad"]["xv"] and 4 <= p["price"] <= 20


def test_lineups_and_nc_scoring():
    import subprocess
    from make_fixture import make
    from fsnmodel.common import load_json
    R = load_json(ROOT / "config" / "rules_nc.json")
    s = {"tries": 1, "assists": 1, "conv": 2, "pen": 1, "metres": 19, "db": 3, "tackles": 10, "missed": 2, "tw": 1, "ls": 1, "lw": 3, "pc": 1, "yc": 1, "offloads": 2, "cb": 1}
    got = sum(points_from_stats(s, "FH", R).values())
    want = 12 + 5 + 2 * (2 - 0.2) + (3 - 0.2) + 1 + 6 + 2 * 2 + 3 + 10 - 2 + 4 + 5 + 3 - 1 - 5
    assert abs(got - want) < 1e-9, (got, want)
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    (d / "lineups").mkdir()
    pl = [{"id": str(2000 + (2020 - 2000 if sh == 8 else 2008 - 2000 if sh == 20 else sh)), "name": f"Ire{sh}", "shirt": sh} for sh in range(1, 24)]
    (d / "lineups" / "ire.json").write_text(json.dumps({"round": 1, "team": "Ireland", "players": pl}))
    from fsnmodel.data import assign_positions, load_matches
    from fsnmodel.lineups import build_lineup_map, load_all
    from fsnmodel.model import project
    import pandas as pd
    hist = load_matches(d / "rfu")
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    lm, _ = build_lineup_map(load_all([d / "lineups"]), hist, fx)
    res = project(hist, assign_positions(hist), fx, rules(), pd.Timestamp("2026-06-01"), lineup_map=lm)
    by = {p["id"]: p for p in res["players"]}
    assert by["2020"]["fx"][0]["ln"] == "S" and by["2008"]["fx"][0]["ln"] == "B"
    assert by["2020"]["exp"][0] > by["2008"]["exp"][0]
    # a team with no history at all (e.g. a new nation in the competition) must not break the projection
    fx2 = [{"round": 1, "date": "2027-02-05", "home": "Argentina", "away": "England"}]
    lm2 = {(1, "Argentina"): {f"n:Arg:{i}": (i, f"Arg{i}") for i in range(1, 24)}}
    res2 = project(hist, assign_positions(hist), fx2, rules(), pd.Timestamp("2026-06-01"), lineup_map=lm2)
    assert any(p["team"] == "Argentina" and p["exp"][0] > 0 for p in res2["players"])


def test_position_flexibility():
    """A fly-half named at full-back is projected in the full-back role (the shirt decides), with his own multiplier on that baseline."""
    from make_fixture import make
    from fsnmodel.common import load_json
    from fsnmodel.data import assign_positions, load_matches
    from fsnmodel.lineups import build_lineup_map, load_all
    from fsnmodel.model import project
    import pandas as pd
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    (d / "lineups").mkdir()
    swap = {10: 15, 15: 10}
    pl = [{"id": str(2000 + swap.get(sh, sh)), "name": f"Ire{sh}", "shirt": sh} for sh in range(1, 24)]     # 2010 (the FH) wears 15, 2015 wears 10
    (d / "lineups" / "ire.json").write_text(json.dumps({"round": 1, "team": "Ireland", "players": pl}))
    hist = load_matches(d / "rfu")
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    lm, _ = build_lineup_map(load_all([d / "lineups"]), hist, fx)
    res = project(hist, assign_positions(hist), fx, rules(), pd.Timestamp("2026-06-01"), lineup_map=lm)
    by = {p["id"]: p for p in res["players"]}
    assert by["2010"]["fx"][0]["rl"] == "FB" and by["2015"]["fx"][0]["rl"] == "FH", (by["2010"]["fx"][0], by["2015"]["fx"][0])
    assert by["2010"]["pos"] == "FH"                          # his listed position does not change
    assert all(len(p["roles"]) >= 1 for p in res["players"])
    # scoring follows the listed fantasy position, not the shirt: a listed FH in the 15 shirt earns no front-row scrum points
    from fsnmodel.scoring import scrums_for
    R_ = load_json(ROOT / "config" / "rules_nc.json")
    assert scrums_for("FH", 80, R_) == 0 and scrums_for("PROP", 80, R_) > 0


def test_backtest_runs():
    import subprocess
    from make_fixture import make
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    out = subprocess.check_output([sys.executable, str(ROOT / "scripts" / "backtest.py"), "--competition", "Six Nations", "--rules", str(ROOT / "config" / "rules.json"),
                                   "--rfu", str(d / "rfu"), "--espn", str(d / "none"), "--super", str(d / "none"), "--snapshots", str(d / "none")]).decode()
    assert "Walk-forward: Six Nations" in out and "MAE" in out


if __name__ == "__main__":
    test_lineups_and_nc_scoring(); test_position_flexibility(); test_backtest_runs(); 
    test_minutes(); test_scoring_2025(); test_pipeline_and_schema(); print("ok")


# ---- schedule: moving on by itself ----
def test_schedule_moves_on_and_builds_finals():
    import pandas as pd
    from datetime import datetime, timezone
    from fsnmodel.schedule import kickoff, status, standings, finals_fixtures
    fx = [{"round": 4, "date": "2026-11-06", "ko": "20:10", "home": "Ireland", "away": "Argentina"},
          {"round": 4, "date": "2026-11-08", "ko": "15:10", "home": "England", "away": "Australia"},
          {"round": 5, "date": "2026-11-13", "ko": "20:10", "home": "France", "away": "South Africa"}]
    assert kickoff(fx[0]) == datetime(2026, 11, 6, 20, 10, tzinfo=timezone.utc)      # November: UK time is UTC
    assert kickoff({"date": "2026-07-04", "ko": "15:00"}) == datetime(2026, 7, 4, 14, 0, tzinfo=timezone.utc)   # July: BST
    u = lambda *a: datetime(*a, tzinfo=timezone.utc)                                   # noqa: E731
    assert status(fx, u(2026, 11, 8, 16, 0))["current"] == 4                           # last match of R4 still in play
    s = status(fx, u(2026, 11, 8, 18, 30))
    assert s["finished"] == [4] and s["current"] == 5 and s["next_kickoff"] == u(2026, 11, 13, 20, 10)
    assert status(fx, u(2026, 11, 20, 0, 0))["current"] is None
    # tables: 4 tries in a loss by <=7 earns both bonuses; try bonus for winners; sorted per hemisphere
    rows = []
    def match(k, a, b, pa, pb, ta, tb):
        for t, o, pf, pq, tr in ((a, b, pa, pb, ta), (b, a, pb, pa, tb)):
            rows.append(dict(match_key=k, team=t, opp=o, pf=pf, pa=pq, tries=tr, competition="Nations Championship"))
    match("m1", "England", "Fiji", 30, 25, 4, 3)       # England 4+1, Fiji 0+1
    match("m2", "France", "Japan", 20, 20, 2, 2)       # 2 each
    t = standings(pd.DataFrame(rows), "Nations Championship", {"England": "N", "France": "N", "Fiji": "S", "Japan": "S"})
    pts = dict(zip(t["team"], t["pts"]))
    assert pts == {"England": 5, "Fiji": 1, "France": 2, "Japan": 2}
    assert list(t[t.hemi == "N"]["team"]) == ["England", "France"] and list(t[t.hemi == "S"]["team"]) == ["Japan", "Fiji"]
    # finals wait for all 36 league results
    comp = {"short": "Nations Championship", "hemisphere": {"England": "N", "France": "N", "Fiji": "S", "Japan": "S"}}
    ff, note = finals_fixtures(pd.DataFrame(rows), comp, fx, 7, ROOT)
    assert ff == [] and "2 of 36" in note


def test_finals_pairings_from_full_league():
    import pandas as pd
    from fsnmodel.schedule import finals_fixtures
    N = ["Ireland", "France", "England", "Scotland", "Italy", "Wales"]
    S = ["South Africa", "New Zealand", "Australia", "Argentina", "Fiji", "Japan"]       # strength order, strongest first
    rows, k = [], 0
    for i, n in enumerate(N):
        for j, s in enumerate(S):
            k += 1
            north_wins = i < j                                  # the better-placed side wins; the pair (i, j) with i < j goes north
            pn, ps = (30, 10) if north_wins else (10, 30)
            for t, o, pf, pq in ((n, s, pn, ps), (s, n, ps, pn)):
                rows.append(dict(match_key=f"m{k}", team=t, opp=o, pf=pf, pa=pq, tries=3, competition="Nations Championship"))
    comp = {"short": "Nations Championship", "hemisphere": {**{t: "N" for t in N}, **{t: "S" for t in S}}}
    ff, note = finals_fixtures(pd.DataFrame(rows), comp, [], 7, ROOT)
    assert len(ff) == 6 and all(f["round"] == 7 for f in ff)
    final = [f for f in ff if f["final"] == 1][0]
    assert (final["home"], final["away"], final["date"], final["ko"]) == ("Ireland", "South Africa", "2026-11-29", "16:40")
    assert {f["final"]: (f["date"], f["ko"]) for f in ff}[6] == ("2026-11-27", "16:40")


def test_lineup_names_split_brothers_and_keep_returners():
    import pandas as pd
    from fsnmodel.identity import NameIndex
    h = pd.DataFrame([
        dict(pid="a", name="Paolo Garbisi", team="Italy", date=pd.Timestamp("2026-07-01"), shirt=10),
        dict(pid="b", name="Alessandro Garbisi", team="Italy", date=pd.Timestamp("2026-07-01"), shirt=9),
        dict(pid="c", name="Zander Fagerson", team="Scotland", date=pd.Timestamp("2026-07-01"), shirt=3),
        dict(pid="d", name="Matt Fagerson", team="Scotland", date=pd.Timestamp("2026-07-01"), shirt=8),
        dict(pid="e", name="Grégory Alldritt", team="France", date=pd.Timestamp("2025-01-01"), shirt=8)])
    ix = NameIndex(h)
    assert ix.match("Scotland", "Zander Fagerson", 3) == "c" and ix.match("Scotland", "M. Fagerson", 6) == "d"
    assert ix.match("Italy", "A. Garbisi", 22) == "b"                 # initial alone is enough, whatever the shirt
    assert ix.match("Italy", "Garbisi", 9) == "b"                     # no given name: the shirt's position decides
    assert ix.match("Italy", "Paolo Garbisi", 10) == "a"
    assert ix.match("France", "Gregory Alldritt", 8) == "e"           # accents
