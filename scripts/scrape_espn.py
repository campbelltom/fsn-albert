"""ESPN scraper: python scripts/scrape_espn.py --from 2023 --to 2027 --out data/espn   (leagues in config/espn_leagues.json)"""
import argparse, json, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.espn import discover_leagues, fetch_season  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="y0", type=int, default=2023)
ap.add_argument("--to", dest="y1", type=int, default=date.today().year + 1)
ap.add_argument("--out", default=str(ROOT / "data" / "espn"))
ap.add_argument("--discover", action="store_true", help="print every ESPN rugby league id/name and exit")
a = ap.parse_args()
if a.discover:
    for i, n in discover_leagues():
        print("LEAGUE", i, n)
    sys.exit(0)
for lg in json.loads((ROOT / "config" / "espn_leagues.json").read_text()):
    for y in range(a.y0, a.y1 + 1):
        try:
            print(lg["name"], y, fetch_season(y, a.out, lg["id"], lg["name"]), "new matches")
        except Exception as e:  # noqa: BLE001
            print(lg["name"], y, "ESPN unavailable:", e)
