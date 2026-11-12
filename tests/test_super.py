"""Super Rugby match files: shirts from the Opta table order, starters, replacements and minutes from the timeline."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.data import minutes_on_pitch  # noqa: E402

spec = importlib.util.spec_from_file_location("scrape_super", ROOT / "scripts" / "scrape_super.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

META = {"id": "1", "season": 2026, "date": "Friday 13 February 2026", "home": "Home", "away": "Away", "url": "u",
        "score": {"home": 12, "away": 3, "homeName": "Home", "awayName": "Away"}}


def player(pid, name, team, **stats):
    return {"id": pid, "name": name, "team": team, "stats": {"attack:T": 0, "kicking:C": 0, "kicking:PG": 0, "kicking:DG": 0, "attack:P": 1, **stats}}


def squad():
    """23 a side; player hN / aN wears shirt N. Replacements have empty stat lines unless they come on."""
    home = [player(f"h{i}", f"H{i}", "Home") for i in range(1, 24)]
    away = [player(f"a{i}", f"A{i}", "Away") for i in range(1, 24)]
    for p in home[15:] + away[15:]:
        p["stats"] = {k: 0 for k in p["stats"]}
    home[0]["stats"]["attack:T"] = 2; home[1]["stats"]["kicking:C"] = 1      # 12 points
    away[0]["stats"]["kicking:PG"] = 1                                       # 3 points
    return home + away


def page_order(players):
    """The order Opta's tables list them in: shirts 15..1, then 16..23."""
    out = []
    for t, pre in (("Home", "h"), ("Away", "a")):
        shirts = list(range(15, 0, -1)) + list(range(16, 24))
        out.append({"team": t, "rows": [[f"{pre}{n}", next(p["name"] for p in players if p["id"] == f"{pre}{n}")] for n in shirts]})
    return out


SUBS = [{"type": "off", "side": "home", "name": "H2", "min": 23}, {"type": "on", "side": "home", "name": "H16", "min": 23},
        {"type": "off", "side": "home", "name": "H16", "min": 28}, {"type": "on", "side": "home", "name": "H2", "min": 28},
        {"type": "on", "side": "away", "name": "A16", "min": 50}, {"type": "off", "side": "away", "name": "A3", "min": 50}]


def test_minutes_with_explicit_start():
    assert minutes_on_pitch(None, [], started=True) == (80.0, True)
    assert minutes_on_pitch(None, [{"type": "off", "min": 23}, {"type": "on", "min": 28}, {"type": "off", "min": 51}], started=True) == (46.0, True)
    assert minutes_on_pitch(None, [{"type": "on", "min": 60}], started=False) == (20.0, False)
    assert minutes_on_pitch(5, [], started=None) == (80.0, True), "a shirt still decides when no flag is given"


def test_shirts_come_from_the_table_order():
    ps = squad()
    m, warn = ss.build(META, {"players": ps}, SUBS, page_order(ps))
    by = {p["id"]: p for p in m["players"]}
    assert all(by[f"h{n}"]["shirt"] == n and by[f"a{n}"]["shirt"] == n for n in range(1, 24))
    assert by["h2"]["started"] and not by["h16"]["started"], "a blood-bin starter is still a starter; his temporary replacement is not"
    assert not by["a23"]["started"] and by["a23"]["events"] == [], "an unused replacement neither starts nor plays"
    assert [sum(p["started"] for p in m["players"] if p["team"] == t) for t in ("Home", "Away")] == [15, 15]
    assert warn == [], warn
    mins = {pid: minutes_on_pitch(by[pid]["shirt"], by[pid]["events"])[0] for pid in ("h2", "h16", "a3", "a16", "a23")}
    assert mins == {"h2": 75.0, "h16": 5.0, "a3": 50.0, "a16": 30.0, "a23": 0.0}


def test_shared_surnames_told_apart_by_who_is_on_the_pitch():
    ps = squad()
    by = {p["id"]: p for p in ps}
    by["h6"]["name"] = by["h18"]["name"] = "Savea"                  # a starter and a replacement with the same surname
    by["h18"]["stats"]["attack:P"] = 1
    subs = [{"type": "off", "side": "home", "name": "Savea", "min": 55}, {"type": "on", "side": "home", "name": "Savea", "min": 55}]
    m, warn = ss.build(META, {"players": ps}, subs, page_order(ps))
    out = {p["id"]: p for p in m["players"]}
    assert out["h6"]["events"] == [{"type": "off", "min": 55}] and out["h18"]["events"] == [{"type": "on", "min": 55}]
    assert warn == [], warn


def test_without_table_order_falls_back_and_leaves_out_shared_surnames():
    ps = squad()
    ps[5]["name"] = "Smith"; ps[6]["name"] = "Smith"                 # two Home starters called Smith
    bad = dict(META, score={"home": 99, "away": 3, "homeName": "Home", "awayName": "Away"})
    m, warn = ss.build(bad, {"players": ps}, [{"type": "off", "side": "home", "name": "Smith", "min": 55}], order=None)
    assert any("no shirt numbers" in w for w in warn)
    assert any("stat lines add up to 12, score 99" in w for w in warn)
    assert {"h6", "h7"}.isdisjoint(p["id"] for p in m["players"]), "minutes that cannot be told apart are left out"
    starters = {p["id"] for p in m["players"] if p["started"]}
    assert {"h1", "a1"} <= starters and "a23" not in starters, "starters still come from the timeline"


def test_a_short_table_is_not_trusted():
    ps = squad()
    order = page_order(ps)
    order[0]["rows"] = order[0]["rows"][:22]
    m, warn = ss.build(META, {"players": ps}, SUBS, order)
    assert all(p["shirt"] is None for p in m["players"] if p["team"] == "Home")
    assert all(p["shirt"] for p in m["players"] if p["team"] == "Away")
    assert any("Home: no shirt numbers" in w for w in warn)
