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
    from make_fixture import make
    d = Path(tempfile.mkdtemp())
    make(d / "rfu")
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "run_production.py"), "--rfu", str(d / "rfu"), "--espn", str(d / "none"), "--out", str(d / "out"), "--no-encrypt"])
    j = json.loads((d / "out" / "predictions.json").read_text())
    assert len(j["rounds"]) == 5 and all(len(m["matches"]) == 3 for m in j["rounds"])
    for p in j["players"]:
        assert len(p["exp"]) == 5 and all(math.isfinite(x) for x in p["exp"] + p["sup"])
        assert p["pos"] in j["rules"]["squad"]["xv"] and 4 <= p["price"] <= 20


if __name__ == "__main__":
    test_minutes(); test_scoring_2025(); test_pipeline_and_schema(); print("ok")
