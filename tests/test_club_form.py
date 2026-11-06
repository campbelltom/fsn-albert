"""Club matches may only move a player's form: never team strength, his current team, selection or position."""
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fsnmodel import model  # noqa: E402
from fsnmodel.common import load_json, rules  # noqa: E402
from fsnmodel.data import assign_positions, load_matches  # noqa: E402
from fsnmodel.model import project  # noqa: E402
from make_fixture import make  # noqa: E402

ASOF = pd.Timestamp("2026-06-01")
STAR = "2010"       # Ireland's fly-half in the fixture


@pytest.fixture(scope="module")
def hist():
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    return load_matches(d / "rfu")


@pytest.fixture(autouse=True)
def restore_params():
    saved = dict(model.PARAMS)
    yield
    model.PARAMS.clear()
    model.PARAMS.update(saved)


def with_club(hist, shirt=7):
    """Recent club matches, later than any Test: the star plays out of position for a club, with a huge scoring record, against big scores."""
    rows = []
    last = hist["date"].max()
    src = hist[hist["pid"] == STAR].tail(1).iloc[0].to_dict()
    for i in range(12):
        date = last + pd.Timedelta(days=7 * (i + 1))
        for home, (team, opp, pid, name, tries) in enumerate((("Club A", "Club B", STAR, "Club Star", 3.0), ("Club B", "Club A", "9001", "Club Only", 0.0))):
            rows.append({**src, "competition": "Gallagher Prem", "date": date, "season": 2026, "team": team, "opp": opp, "home": bool(home),
                         "pf": 90, "pa": 3, "pid": pid, "name": name, "shirt": shirt, "started": True, "minutes": 80.0, "tries": tries,
                         "match_key": date.strftime("%Y%m%d") + "_Club A_Club B"})
    return pd.concat([hist, pd.DataFrame(rows)], ignore_index=True)


def run(h):
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    pos = assign_positions(h)
    return project(h, pos, fx, rules(), ASOF), pos


def test_weight_zero_matches_having_no_club_data(hist):
    model.PARAMS["club_weight"] = 0.0
    assert run(with_club(hist))[0] == run(hist)[0]


def test_club_matches_cannot_change_team_or_position(hist):
    model.PARAMS["club_weight"] = 1.0
    plain, pos_plain = run(hist)
    res, pos = run(with_club(hist))
    assert res["teams"] == plain["teams"], "club scores must not feed team ratings"
    assert res["rounds"] == plain["rounds"], "club scores must not feed match predictions"
    by, base = {p["id"]: p for p in res["players"]}, {p["id"]: p for p in plain["players"]}
    assert by[STAR]["team"] == "Ireland", "a club match must not change a player's team"
    assert pos[STAR] == pos_plain[STAR] == "FH", "the Test position decides, not the club shirt"
    assert by[STAR]["caps"] == base[STAR]["caps"], "caps come from Tests only"
    assert by[STAR]["roles"] == base[STAR]["roles"], "roles come from Tests only"
    assert "9001" not in by, "a player with no national-team match is not projected"


def test_club_form_moves_only_the_player_with_club_data(hist):
    model.PARAMS["club_weight"] = 0.0
    plain = {p["id"]: p for p in run(with_club(hist))[0]["players"]}
    model.PARAMS["club_weight"] = 1.0
    res = {p["id"]: p for p in run(with_club(hist))[0]["players"]}
    tries = lambda p: sum(c["tries"] for c in p["comp"])  # noqa: E731
    assert tries(res[STAR]) > tries(plain[STAR]), "a strong club record should lift his try rate"
    # Player of the match is shared across a squad, so a better team-mate shifts it: that is the only part of another player that may move.
    own = lambda p: [{k: v for k, v in c.items() if k != "potm"} for c in p["comp"]]  # noqa: E731
    others = [pid for pid in plain if pid != STAR]
    assert all(own(res[pid]) == own(plain[pid]) for pid in others), "other players' own projections must not change"
    assert any(res[pid]["comp"] != plain[pid]["comp"] for pid in others if plain[pid]["team"] == "Ireland")


def test_positions_fall_back_to_club_when_uncapped(hist):
    pos = assign_positions(with_club(hist))
    assert pos["9001"] == "BR"
