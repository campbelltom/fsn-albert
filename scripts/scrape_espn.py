"""ESPN fallback scraper: python scripts/scrape_espn.py --from 2023 --to 2027 --out data/espn"""
import argparse, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fsnmodel.espn import fetch_season  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="y0", type=int, default=2023)
ap.add_argument("--to", dest="y1", type=int, default=date.today().year + 1)
ap.add_argument("--out", default=str(ROOT / "data" / "espn"))
a = ap.parse_args()
for y in range(a.y0, a.y1 + 1):
    try:
        print(y, fetch_season(y, a.out), "new matches")
    except Exception as e:  # noqa: BLE001
        print(y, "ESPN unavailable:", e)
