#!/usr/bin/env python3
"""
update_elo.py — refresh the FIFA_STRENGTH (Elo) table in index.html from the
live World Football Elo Ratings (eloratings.net), server-side.

SOURCE
  https://www.eloratings.net/World.tsv — plain HTTPS GET, no key, no rate limit.
  Tab-separated, no header. Columns:
    [0] rank  [1] rank  [2] TWO-LETTER COUNTRY CODE  [3] current Elo rating  ...
  e.g.  1<TAB>1<TAB>AR<TAB>2144<TAB>...  (Argentina, 2144).
  We map the code in column 3 to the dashboard name and read the rating in col 4.

SAFETY
  • Soft-fails (exit 0) on any network/parse problem — never fails the workflow.
  • Only rewrites teams already in FIFA_STRENGTH; never adds/removes teams.
  • Bounds every value to 1000–2200; only writes if >=40 matched and something changed.
"""

import re
import sys
import urllib.request

ELO_URL = "https://www.eloratings.net/World.tsv"
HTML_FILE = "index.html"

# eloratings.net 2-letter code -> dashboard team name. NOTE SA=Saudi Arabia,
# ZA=South Africa; eloratings uses some non-ISO codes (EN, SQ, WA, KO=Curaçao).
CODE_TO_NAME = {
    "AR": "Argentina", "ES": "Spain", "FR": "France", "EN": "England",
    "PT": "Portugal", "BR": "Brazil", "NL": "Netherlands", "MA": "Morocco",
    "BE": "Belgium", "DE": "Germany", "HR": "Croatia", "CO": "Colombia",
    "MX": "Mexico", "SN": "Senegal", "US": "United States", "UY": "Uruguay",
    "JP": "Japan", "CH": "Switzerland", "KR": "Korea Republic", "IR": "Iran",
    "AU": "Australia", "TR": "Türkiye", "DK": "Denmark", "EC": "Ecuador",
    "AT": "Austria", "NO": "Norway", "PA": "Panama", "EG": "Egypt",
    "DZ": "Algeria", "SQ": "Scotland", "CA": "Canada", "PY": "Paraguay",
    "TN": "Tunisia", "CI": "Cote d'Ivoire", "SA": "Saudi Arabia", "QA": "Qatar",
    "ZA": "South Africa", "CZ": "Czechia", "SE": "Sweden", "KO": "Curaçao",
    "JO": "Jordan", "CV": "Cabo Verde", "UZ": "Uzbekistan", "NZ": "New Zealand",
    "BA": "Bosnia and Herzegovina", "IQ": "Iraq", "HT": "Haiti", "GH": "Ghana",
    "CD": "Congo DR",
}

ELO_MIN, ELO_MAX = 1000, 2200


def fetch_tsv():
    req = urllib.request.Request(ELO_URL, headers={"User-Agent": "wc26-elo-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_ratings(tsv):
    out = {}
    for line in tsv.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        name = CODE_TO_NAME.get(cols[2].strip())
        if not name:
            continue
        try:
            elo = int(round(float(cols[3])))
        except (ValueError, IndexError):
            continue
        if not (ELO_MIN <= elo <= ELO_MAX):
            continue
        out[name] = elo
    return out


def current_strength_block(html):
    return re.search(r'const FIFA_STRENGTH=\{(.*?)\};', html, re.S)


def parse_existing(block_body):
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
        print(f"Only parsed {len(live)} recognised ratings — looks incomplete; "
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

    matched, changed, new_pairs = 0, 0, []
    for name, old_elo in existing:
        if name in live:
            matched += 1
            new_elo = live[name]
            if new_elo != old_elo:
                changed += 1
            new_pairs.append((name, new_elo))
        else:
            print(f"  · no live rating for '{name}' — keeping {old_elo}")
            new_pairs.append((name, old_elo))

    if matched < 40:
        print(f"Only matched {matched} teams to the live feed — too few; leaving "
              f"file unchanged.")
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
