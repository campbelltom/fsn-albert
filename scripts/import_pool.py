"""Turn a copy-paste of the official fantasy 'create team' player list into config/nc_pool.csv.

    python scripts/import_pool.py <pasted.txt> [--out config/nc_pool.csv]

Each player is a block: name, name (truncated), position, hemisphere (N/S), then one tab-separated line
'<ownership>%  <price>M  <points> <tackles> <metres> <tries> <assists> <transfers in> <transfers out>' (column order confirmed by the user;
points, tackles, metres, tries and assists are the player's totals so far, i.e. the July rounds)."""
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
                         tackles=nums[1], metres=nums[2], tries=nums[3], assists=nums[4], transfers_in=nums[5], transfers_out=nums[6]))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=str(ROOT / "config" / "nc_pool.csv"))
    ap.add_argument("--merge", action="store_true", help="keep players missing from the paste (the page is sorted by points and often cuts off before the 0-point players)")
    a = ap.parse_args()
    rows = parse(Path(a.src).read_text())
    if a.merge and Path(a.out).exists():
        seen = {(r["name"], r["hemi"], r["pos"]) for r in rows}
        keep = [r for r in csv.DictReader(open(a.out)) if (r["name"], r["hemi"], r["pos"]) not in seen]
        for r in keep:
            r.update({k: (float(v) if v not in ("", None) else None) for k, v in r.items() if k not in ("name", "pos", "hemi")})
        print(f"kept {len(keep)} players the paste did not list")
        rows += keep
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} players -> {a.out}")


if __name__ == "__main__":
    main()
