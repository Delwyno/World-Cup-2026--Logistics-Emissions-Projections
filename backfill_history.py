#!/usr/bin/env python3
"""
backfill_history.py — reconstructs title_history.json for every day since the
tournament started, using your repo's own git history of index.html.

WHY THIS EXISTS
  update_history.py only started snapshotting the day it was added to the
  workflow — it has no way to retroactively know what index.html looked like
  on earlier days. This script fixes that ONE TIME by walking git history:
  for each calendar day from tournament start to today, it finds the last
  commit that touched index.html by the end of that day (UTC), pulls that
  version of the file straight out of git (git show — no checkout needed),
  and runs the exact same championProbabilities() harness against it that
  update_history.py uses going forward.

REQUIREMENTS
  - Run this from the root of a FULL clone of your repo (not shallow).
    GitHub Actions' actions/checkout defaults to fetch-depth: 1 (shallow),
    which won't have the history this needs. Either:
      (a) run it on your own machine where you cloned normally, or
      (b) temporarily add `with: {fetch-depth: 0}` to a checkout step in a
          one-off workflow run, then remove it again afterwards.
  - node on PATH (same requirement as update_history.py).

USAGE
  python backfill_history.py

  It writes/overwrites title_history.json in the current directory. Review
  the diff, then commit and push — update_history.py's daily runs take over
  normally from there, appending to what this script built.

NOTES
  - A given commit's index.html is only ever simulated once, even if it's
    "the last commit of the day" for several consecutive days (e.g. a quiet
    weekend) — the RNG seed is derived from the results content itself, so
    re-running an unchanged file would just reproduce identical numbers.
  - Days with no commit yet (before the repo existed) are skipped, not
    padded with guesses.
  - This never touches your working directory's current index.html — every
    historical version is read via `git show <hash>:index.html` into memory.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from update_history import run_harness  # reuse the exact same harness

TOURNAMENT_START = "2026-06-11"
HISTORY_FILE = "title_history.json"


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def last_commit_for_day(day):
    """Last commit touching index.html at or before 23:59:59 UTC on `day`."""
    cutoff = day.strftime("%Y-%m-%dT23:59:59")
    r = subprocess.run(
        ["git", "log", f"--before={cutoff}", "-1", "--format=%H", "--", "index.html"],
        capture_output=True, text=True
    )
    h = r.stdout.strip()
    return h or None


def html_at_commit(commit_hash):
    r = subprocess.run(["git", "show", f"{commit_hash}:index.html"],
                        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout


def main():
    # Sanity check: are we even in a git repo with real history?
    r = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                        capture_output=True, text=True)
    if r.stdout.strip() == "true":
        print("! This looks like a shallow clone (fetch-depth: 1). Run "
              "`git fetch --unshallow` first, or this will only find the "
              "single most recent commit for every day.")

    start = datetime.strptime(TOURNAMENT_START, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()

    print(f"Backfilling {start} -> {today}...")
    history = []
    cache = {}  # commit hash -> champ odds, so unchanged days aren't resimulated
    skipped = []

    for day in daterange(start, today):
        commit = last_commit_for_day(day)
        if not commit:
            skipped.append(str(day))
            continue

        if commit in cache:
            champ = cache[commit]
        else:
            html = html_at_commit(commit)
            if html is None:
                skipped.append(str(day))
                continue
            print(f"  {day} -> commit {commit[:8]} - simulating...")
            champ = run_harness(html)
            if champ is None:
                print(f"    x harness failed for {day}, skipping")
                skipped.append(str(day))
                continue
            cache[commit] = champ

        history.append({"date": str(day), "champ": champ})

    if not history:
        print("Nothing to write - no commits found touching index.html in range.")
        sys.exit(1)

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=1)
        f.write("\n")

    print(f"\nWrote {len(history)} days to {HISTORY_FILE}.")
    if skipped:
        print(f"Skipped {len(skipped)} day(s) with no usable commit: {', '.join(skipped)}")
    print("Review the diff, then commit and push - update_history.py takes it from there.")


if __name__ == "__main__":
    main()
