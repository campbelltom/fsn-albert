"""Turn a copy-paste of the official fantasy 'create team' player list into config/nc_pool.csv.

    python scripts/import_pool.py <pasted.txt> [--out config/nc_pool.csv]

Each player is a block: name, name (truncated), position, hemisphere (N/S), then one tab-separated line
'<ownership>%  <price>M  <pts>  <c2> ... <c7>'. The meaning of c2..c7 is not labelled in the paste (kept raw)."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAT = re.compile(r"^(\d+(?:\.\d+)?)%\t(\d+(?:\.\d+)?)M\t(.*)$")
POS = {"Prop", "Hooker", "Lock", "Loose Forward", "Scrum Half", "Fly Half", "Centre", "Back Three"}


def parse(text: str) -> list[dict]:
    lines = [l.rstrip("\n") for l in text.splitlines()]
    nz = [l for l in lines if l.strip()]
    rows = []
    for i, l in enumerate(nz):
        m = STAT.match(l.strip("\r"))
        if not m or i < 4:
            continue
        hemi, pos, name = nz[i - 1].strip(), nz[i - 2].strip(), nz[i - 4].strip()
        if hemi not in ("N", "S") or pos not in POS:
            print("skip block at line", i, nz[i - 4:i + 1], file=sys.stderr)
            continue
        nums = [x for x in m.group(3).split("\t")]
        nums = [float(x) if re.fullmatch(r"-?\d+(\.\d+)?", x.strip()) else None for x in nums]
        nums += [None] * (7 - len(nums))
        rows.append(dict(name=name, pos=pos, hemi=hemi, own_pct=float(m.group(1)), price=float(m.group(2)), pts=nums[0],
                         c2=nums[1], c3=nums[2], c4=nums[3], c5=nums[4], c6=nums[5], c7=nums[6]))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=str(ROOT / "config" / "nc_pool.csv"))
    a = ap.parse_args()
    rows = parse(Path(a.src).read_text())
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} players -> {a.out}")


if __name__ == "__main__":
    main()
