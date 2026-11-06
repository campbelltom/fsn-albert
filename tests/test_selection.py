"""Selection is never predicted: it comes from the named 23, and before they are named every player is valued as a starter."""
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fsnmodel.common import load_json, rules  # noqa: E402
from fsnmodel.data import assign_positions, load_matches  # noqa: E402
from fsnmodel.lineups import build_lineup_map, load_all  # noqa: E402
from fsnmodel.model import project  # noqa: E402
from make_fixture import make  # noqa: E402

ASOF = pd.Timestamp("2026-06-01")


def _setup(lineup=None):
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    hist = load_matches(d / "rfu")
    fx = load_json(ROOT / "config" / "fixtures_2027.json")["fixtures"]
    lm = None
    if lineup:
        (d / "lineups").mkdir()
        (d / "lineups" / "ire.json").write_text(json.dumps(lineup))
        lm, _ = build_lineup_map(load_all([d / "lineups"]), hist, fx)
    res = project(hist, assign_positions(hist), fx, rules(), ASOF, lineup_map=lm)
    return hist, {p["id"]: p for p in res["players"]}


def test_unnamed_round_values_everyone_as_a_starter():
    hist, by = _setup()
    ire = [p for p in by.values() if p["team"] == "Ireland"]
    assert ire and all(p["fx"][0]["ln"] is None for p in ire)
    assert all("pStart" not in p and "pSub" not in p for p in by.values()), "no predicted selection in the output"
    # a squad player who rarely started is still valued on a full start, not discounted for selection
    starts = hist[hist["team"] == "Ireland"].groupby("pid")["started"].mean()
    rare, regular = starts.idxmin(), starts.idxmax()
    assert starts[rare] < 0.5 <= starts[regular]
    assert by[rare]["eMin"] >= 40, "an unnamed player is projected for a starter's minutes"


def test_named_23_decides_who_plays():
    pl = [{"id": str(2000 + sh), "name": f"Ire{sh}", "shirt": sh} for sh in range(1, 23)]     # 2023 is left out
    _, by = _setup({"round": 1, "team": "Ireland", "players": pl})
    assert by["2001"]["fx"][0]["ln"] == "S" and by["2016"]["fx"][0]["ln"] == "B"
    assert by["2023"]["fx"][0]["ln"] == "X" and by["2023"]["exp"][0] == 0, "a player outside the named 23 scores nothing"
    assert by["2001"]["exp"][0] > by["2016"]["exp"][0] > 0
