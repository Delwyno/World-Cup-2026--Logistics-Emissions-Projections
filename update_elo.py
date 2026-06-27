#!/usr/bin/env python3
"""
update_elo.py — refresh the FIFA_STRENGTH (Elo) table in index.html from the
live World Football Elo Ratings (eloratings.net), server-side.

WHY
  The Monte-Carlo simulation that drives the qualification % and title odds uses
  a per-team strength rating. Those ratings drift as teams play, so a hardcoded
  snapshot slowly goes stale. The browser can't fetch them (CORS), but a GitHub
  Action can — so this script pulls the current ratings at build time and writes
  them straight into the single HTML file, exactly like the score updater does.

SOURCE
  https://www.eloratings.net/World.tsv  — plain HTTPS GET, no key, no rate limit.
  Tab-separated, no header. Column 3 = team name, column 4 = current Elo rating.
  (Layout reverse-engineered from the site's own scripts/ratings.js; widely used.)

SAFETY
  • Soft-fails: any network/parse problem prints a message and exits 0 (success,
    no change), so a transient blip never fails the workflow or wipes the file.
  • Only rewrites ratings for teams already in FIFA_STRENGTH — never adds/removes
    teams, so the 48-team field is fixed regardless of what the feed contains.
  • Sanity-bounds every value (1000–2200) and ignores anything outside that.
  • Only writes if at least 40 of the 48 teams were matched and something changed.
"""

import re
import sys
import urllib.request

ELO_URL = "https://www.eloratings.net/World.tsv"
HTML_FILE = "index.html"

# eloratings.net team spelling  ->  dashboard spelling (FIFA_STRENGTH keys).
# Only differences need listing; exact matches pass through. eloratings uses
# plain ASCII and some alternative names, so map those explicitly.
NAME_MAP = {
    "Czech Republic": "Czechia",
    "Czechia": "Czechia",
    "Turkey": "Türkiye",
    "Türkiye": "Türkiye",
    "South Korea": "Korea Republic",
    "Korea Republic": "Korea Republic",
    "Korea": "Korea Republic",
    "Ivory Coast": "Cote d'Ivoire",
    "Côte d'Ivoire": "Cote d'Ivoire",
    "USA": "United States",
    "United States": "United States",
    "Cape Verde": "Cabo Verde",
    "Cabo Verde": "Cabo Verde",
    "Curacao": "Curaçao",
    "Curaçao": "Curaçao",
    "DR Congo": "Congo DR",
    "Congo DR": "Congo DR",
    "Congo DR (Zaire)": "Congo DR",
    "Bosnia/Herzegovina": "Bosnia and Herzegovina",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Saudi Arabia": "Saudi Arabia",
    "New Zealand": "New Zealand",
    "South Africa": "South Africa",
    # straightforward identical names (Spain, Brazil, etc.) need no entry
}

ELO_MIN, ELO_MAX = 1000, 2200   # plausible national-team Elo bounds


def fetch_tsv():
    req = urllib.request.Request(ELO_URL, headers={"User-Agent": "wc26-elo-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_ratings(tsv):
    """Return {dashboard_name: elo_int} for teams we can map and bound-check."""
    out = {}
    for line in tsv.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        raw_name = cols[2].strip()
        try:
            elo = int(round(float(cols[3])))
        except (ValueError, IndexError):
            continue
        if not (ELO_MIN <= elo <= ELO_MAX):
            continue
        name = NAME_MAP.get(raw_name, raw_name)
        out[name] = elo
    return out


def current_strength_block(html):
    m = re.search(r'const FIFA_STRENGTH=\{(.*?)\};', html, re.S)
    return m


def parse_existing(block_body):
    """Parse the existing {"Team":1886,...} into an ordered list of (name, elo)."""
    pairs = re.findall(r'"((?:[^"\\]|\\.)*?)":\s*(\d+)', block_body)
    return [(n.replace('\\"', '"'), int(v)) for n, v in pairs]


def main():
    try:
        tsv = fetch_tsv()
    except Exception as e:
        print(f"Elo fetch failed (leaving file unchanged): {e}")
        sys.exit(0)

    live = parse_ratings(tsv)
    if len(live) < 40:
        print(f"Only parsed {len(live)} ratings from the feed — looks incomplete; "
              f"leaving file unchanged.")
        sys.exit(0)

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    block = current_strength_block(html)
    if not block:
        print("Could not find FIFA_STRENGTH block — no change made.")
        sys.exit(1)

    existing = parse_existing(block.group(1))
    if not existing:
        print("FIFA_STRENGTH block looked empty — no change made.")
        sys.exit(1)

    # Rebuild in the SAME team order, swapping in live ratings where we have them.
    matched, changed = 0, 0
    new_pairs = []
    for name, old_elo in existing:
        if name in live:
            matched += 1
            new_elo = live[name]
            if new_elo != old_elo:
                changed += 1
            new_pairs.append((name, new_elo))
        else:
            # Keep the existing value if the feed didn't include this team.
            print(f"  · no live rating for '{name}' — keeping {old_elo}")
            new_pairs.append((name, old_elo))

    if matched < 40:
        print(f"Only matched {matched}/48 teams to the live feed — too few; "
              f"leaving file unchanged to avoid a partial/incorrect update.")
        sys.exit(0)

    if changed == 0:
        print(f"All {matched} matched ratings already current — no change.")
        sys.exit(0)

    body = ",".join(f'"{n.replace(chr(34), chr(92)+chr(34))}":{e}' for n, e in new_pairs)
    new_block = "const FIFA_STRENGTH={" + body + "};"
    updated = html[:block.start()] + new_block + html[block.end():]

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"Updated FIFA_STRENGTH: {changed} of {matched} matched teams changed.")


if __name__ == "__main__":
    main()
