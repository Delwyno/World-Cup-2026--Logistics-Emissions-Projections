#!/usr/bin/env python3
"""
update_elo.py — refresh the FIFA_STRENGTH (Elo) table in index.html from the
live World Football Elo Ratings (eloratings.net), server-side.

SOURCE
  https://www.eloratings.net/World.tsv — plain HTTPS GET, no key, no rate limit.
  Each row begins:  <rank> <rank> <CODE> <rating> ...
  e.g.  "1  1  AR  2144 ..."  (Argentina, rating 2144).

  IMPORTANT: the field delimiter has proven unreliable to assume (tabs vs spaces
  vary by how the file is served), so this parser is DELIMITER-AGNOSTIC: it splits
  each line on any run of whitespace, then finds the first 2-letter alphabetic
  token (the country code) and the first integer after it (the rating). This is
  robust whether the file is tab- or space-separated.

SAFETY
  • Soft-fails (exit 0) on any network/parse problem — never fails the workflow.
  • Only rewrites teams already in FIFA_STRENGTH; never adds/removes teams.
  • Bounds every value 1000–2200; only writes if >=40 matched and something changed.
"""

import re
import sys
import urllib.request

ELO_URL = "https://www.eloratings.net/World.tsv"
HTML_FILE = "index.html"

# eloratings.net 2-letter code -> dashboard team name. NOTE SA=Saudi Arabia,
# ZA=South Africa; eloratings uses some non-ISO codes (EN, SQ, KO=Curaçao).
CODE_TO_NAME = {
    "AR": "Argentina", "ES": "Spain", "FR": "France", "EN": "England",
    "PT": "Portugal", "BR": "Brazil", "NL": "Netherlands", "MA": "Morocco",
    "BE": "Belgium", "DE": "Germany", "HR": "Croatia", "CO": "Colombia",
    "MX": "Mexico", "SN": "Senegal", "US": "United States", "UY": "Uruguay",
    "JP": "Japan", "CH": "Switzerland", "KR": "Korea Republic", "IR": "Iran",
    "AU": "Australia", "TR": "Türkiye", "EC": "Ecuador",
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


def parse_ratings(text):
    """Delimiter-agnostic: for each line, find the first 2-letter code token and
    the first integer after it. Works for tab- or space-separated input."""
    out = {}
    for line in text.splitlines():
        toks = line.split()                 # split on ANY whitespace run
        if len(toks) < 4:
            continue
        # Find the first token that is exactly a known 2-letter code.
        code_idx = None
        for i, tk in enumerate(toks[:5]):   # code is always near the start
            if tk in CODE_TO_NAME:
                code_idx = i
                break
        if code_idx is None:
            continue
        # The rating is the first integer-looking token AFTER the code.
        elo = None
        for tk in toks[code_idx + 1:code_idx + 4]:
            t = tk.lstrip("+")
            if t.isdigit():
                elo = int(t)
                break
        if elo is None or not (ELO_MIN <= elo <= ELO_MAX):
            continue
        out[CODE_TO_NAME[toks[code_idx]]] = elo
    return out


def current_strength_block(html):
    return re.search(r'const FIFA_STRENGTH=\{(.*?)\};', html, re.S)


def parse_existing(block_body):
    pairs = re.findall(r'"((?:[^"\\]|\\.)*?)":\s*(\d+)', block_body)
    return [(n.replace('\\"', '"'), int(v)) for n, v in pairs]


def main():
    try:
        text = fetch_tsv()
    except Exception as e:
        print(f"Elo fetch failed (leaving file unchanged): {e}")
        sys.exit(0)

    live = parse_ratings(text)
    if len(live) < 40:
        print(f"Only parsed {len(live)} recognised ratings — looks incomplete; "
              f"leaving file unchanged.")
        # Helpful breadcrumb if it ever parses too few again:
        sample = "\\n".join(text.splitlines()[:3])
        print(f"First lines received were:\n{sample}")
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
