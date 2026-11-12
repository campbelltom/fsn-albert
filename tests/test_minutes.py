"""Bench-aware minutes: a starter whose position group has no replacement on the bench stays on longer.
(Sharing a group's minutes among its replacements by bench make-up was tried and backtested worse than each bench
shirt's usual pattern, so replacements keep that.)"""
import json
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
from fsnmodel.lineups import build_lineup_map, load_all  # noqa: E402
from fsnmodel.model import minute_group, project, uncovered_groups  # noqa: E402
from make_fixture import make  # noqa: E402


def test_groups_follow_position_for_replacements():
    assert minute_group(21, "BR") == "LBR", "a back-rower in 21 (6/2 bench) covers the forwards"
    assert minute_group(21, "SH") == "SH" and minute_group(9, "FH") == "SH" and minute_group(2, None) == "FR"


def test_uncovered_groups():
    starters = {f"s{n}": (n, minute_group(n, None)) for n in range(1, 16)}
    full = starters | {"r16": (16, "FR"), "r19": (19, "LBR"), "r21": (21, "SH"), "r22": (22, "BACK")}
    assert uncovered_groups(full) == set()
    no_sh = starters | {"r16": (16, "FR"), "r19": (19, "LBR"), "r20": (20, "LBR"), "r21": (21, "LBR"), "r22": (22, "BACK")}
    assert uncovered_groups(no_sh) == {"SH"}, "a 6/2 bench with a forward in 21 leaves the scrum-half uncovered"


@pytest.fixture(autouse=True)
def restore_params():
    saved = dict(model.PARAMS)
    yield
    model.PARAMS.clear()
    model.PARAMS.update(saved)


_HIST = {}


def _project(bench_shirts, bench_aware):
    """Same history every time (the fixture generator moves on between calls); only the named bench changes."""
    model.PARAMS["bench_aware"] = bench_aware
    if not _HIST:
        d = Path(tempfile.mkdtemp())
        make(d / "rfu")
        _HIST["hist"] = load_matches(d / "rfu")
    hist = _HIST["hist"]
    d = Path(tempfile.mkdtemp())
    (d / "lineups").mkdir()
    pl = [{"id": str(2000 + sh), "name": f"Ire{sh}", "shirt": sh} for sh in list(range(1, 16)) + bench_shirts]
    (d / "lineups" / "ire.json").write_text(json.dumps({"round": 1, "team": "Ireland", "players": pl}))
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    lm, _ = build_lineup_map(load_all([d / "lineups"]), hist, fx)
    res = project(hist, assign_positions(hist), fx, rules(), pd.Timestamp("2026-06-01"), lineup_map=lm)
    return {p["id"]: p for p in res["players"]}


def test_scrum_half_without_cover_is_projected_for_more_minutes():
    full = _project(list(range(16, 24)), True)          # 21 is the replacement scrum-half
    no_sh = _project([16, 17, 18, 19, 20, 22, 23], True)
    assert no_sh["2009"]["exp"][0] > full["2009"]["exp"][0], "round 1 (named): more minutes, more points"
    assert no_sh["2009"]["exp"][1:] == full["2009"]["exp"][1:], "unnamed rounds are untouched"
    own = lambda p: {k: v for k, v in p["comp"][0].items() if k != "potm"}  # noqa: E731  (POTM is shared across the squad)
    assert own(no_sh["2010"]) == own(full["2010"]), "covered starters are untouched"
    off = _project([16, 17, 18, 19, 20, 22, 23], False)
    assert off["2009"]["eMin"] == full["2009"]["eMin"], "with the switch off, the bench makes no difference"


def test_replacements_keep_their_bench_shirt_pattern():
    on = _project(list(range(16, 24)), True)
    off = _project(list(range(16, 24)), False)
    assert all(on[f"20{n}"]["exp"][0] == off[f"20{n}"]["exp"][0] for n in range(16, 24))
