"""One command to bring the RFU data up to date: find newly played matches, scrape them, commit and push, and ask Actions to rebuild.

    python scripts/sync_rfu.py            # find + scrape only
    python scripts/sync_rfu.py --push     # ... and commit data/rfu + the manifest, push, trigger the site rebuild

The RFU site refuses GitHub's own runners (HTTP 405), so this has to run on a normal machine: either by hand, on a schedule
(cron / launchd / Task Scheduler), or through the self-hosted runner workflow .github/workflows/scrape-rfu.yml.
Safe to run as often as you like: finished matches are cached, and nothing is committed when nothing changed.
Matches are listed by the RFU site the day after they are played."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=check)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="commit and push what was scraped, then trigger the site rebuild")
    ap.add_argument("--full", action="store_true", help="re-list every season, not just the current one (slow; for a first run)")
    ap.add_argument("--workflow", default="update.yml", help="workflow to trigger after a push (needs the gh CLI)")
    a = ap.parse_args()
    if a.push:
        run(["git", "pull", "--rebase", "--autostash", "-q"])
    run([PY, "scripts/discover_rfu.py"] + ([] if a.full else ["--recent"]), check=False)    # a failed listing must not stop the scrape of known matches
    run([PY, "scripts/scrape_rfu.py"])
    if not a.push:
        return
    run(["git", "add", "data/rfu", "config/rfu_matches.json"])
    if not git("diff", "--cached", "--name-only"):
        print("nothing new to commit")
        return
    n = len([x for x in git("diff", "--cached", "--name-only", "--", "data/rfu").splitlines() if not x.endswith("_missing.json")])
    if not git("config", "user.email"):
        run(["git", "config", "user.email", "fsn-albert@users.noreply.github.com"])
        run(["git", "config", "user.name", "fsn-albert sync"])
    run(["git", "commit", "-q", "-m", f"RFU scrape (automatic): {n} new match file(s)"])
    run(["git", "push", "-q"])
    if shutil.which("gh"):
        run(["gh", "workflow", "run", a.workflow], check=False)    # a push made with the Actions token does not start workflows by itself
    else:
        print("gh CLI not found: the site rebuilds on its next 3-hourly run")


if __name__ == "__main__":
    main()
