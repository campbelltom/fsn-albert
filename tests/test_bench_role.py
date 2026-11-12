"""A replacement plays his own usual role, not his bench shirt's: a back-rower in 21 is no scrum-half."""
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
from fsnmodel.model import project  # noqa: E402
from make_fixture import make  # noqa: E402


@pytest.fixture(autouse=True)
def restore_params():
    saved = dict(model.PARAMS)
    yield
    model.PARAMS.clear()
    model.PARAMS.update(saved)


def _run(bench_role):
    model.PARAMS["bench_role"] = bench_role
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    hist = load_matches(d / "rfu")
    # Ireland's openside (2007) on the bench in 21; the usual 21 is left out
    pl = [{"id": str(2000 + sh), "name": f"Ire{sh}", "shirt": sh} for sh in list(range(1, 7)) + list(range(8, 16)) + [16, 17, 18, 19, 20]]
    pl += [{"id": "2021", "name": "Ire21", "shirt": 7}, {"id": "2007", "name": "Ire7", "shirt": 21},
           {"id": "2022", "name": "Ire22", "shirt": 22}, {"id": "2023", "name": "Ire23", "shirt": 23}]
    (d / "lineups").mkdir()
    (d / "lineups" / "ire.json").write_text(json.dumps({"round": 1, "team": "Ireland", "players": pl}))
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    lm, _ = build_lineup_map(load_all([d / "lineups"]), hist, fx)
    res = project(hist, assign_positions(hist), fx, rules(), pd.Timestamp("2026-06-01"), lineup_map=lm)
    return {p["id"]: p for p in res["players"]}


def test_back_rower_in_21_plays_back_row():
    by = _run("player")
    assert by["2007"]["fx"][0]["ln"] == "B"
    assert by["2007"]["fx"][0]["rl"] in ("FLK", "N8", "BR"), by["2007"]["fx"][0]
    assert all(r[0] != "SH" for r in by["2007"]["roles"]), "his history off the bench does not make him a scrum-half"


def test_hybrid_gives_the_same_role():
    by = _run("hybrid")
    assert by["2007"]["fx"][0]["rl"] in ("FLK", "N8", "BR") and all(r[0] != "SH" for r in by["2007"]["roles"])


def test_old_behaviour_kept_behind_the_switch():
    by = _run("shirt")
    assert by["2007"]["fx"][0]["rl"] == "SH"
