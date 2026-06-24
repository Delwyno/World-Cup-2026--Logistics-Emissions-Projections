#!/usr/bin/env python3
"""
Auto-update the RESULTS array in index.html from a keyless World Cup 2026 API.

WHAT IT DOES
  - Fetches all matches from the API.
  - Keeps ONLY completed group-stage matches (ignores in-progress and knockouts).
  - Translates API team names -> the exact spellings your dashboard uses.
  - Rewrites the `const RESULTS = [ ... ];` block in index.html, in your format,
    grouped/commented just like you keep it.
  - Bumps RESULTS_AS_OF to today's date.
  - Has a SAFETY GUARD: if the API returns fewer completed results than the file
    already contains (or zero), it aborts and changes NOTHING. A flaky API
    response can therefore never wipe your scores.

It deliberately does NOT touch KO_RESULTS (knockouts stay manual) or any other
part of the file.

USAGE (locally, to test):  python update_results.py
The GitHub Action runs exactly this.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
HTML_FILE = "index.html"

# Keyless community API. If it ever dies, swap this URL (and adjust the parser
# in extract_matches() to match the new shape). No API key is used anywhere.
API_URL = "https://worldcup26.ir/get/games"

# Map the API's team names -> YOUR dashboard's exact spellings.
# The keys are lowercased API names; values are your canonical names.
# Add any the script reports as "UNMAPPED" when you do the first test run.
NAME_MAP = {
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "ivory coast": "Cote d'Ivoire",
    "cote d'ivoire": "Cote d'Ivoire",
    "côte d'ivoire": "Cote d'Ivoire",
    "south korea": "Korea Republic",
    "korea republic": "Korea Republic",
    "republic of korea": "Korea Republic",
    "curacao": "Curaçao",
    "curaçao": "Curaçao",
    "cape verde": "Cabo Verde",
    "cabo verde": "Cabo Verde",
    "dr congo": "Congo DR",
    "congo dr": "Congo DR",
    "democratic republic of the congo": "Congo DR",
    "usa": "United States",
    "united states": "United States",
    "iran": "Iran",
    "ir iran": "Iran",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    # straightforward ones (kept explicit so the script never guesses):
    "mexico": "Mexico", "south africa": "South Africa", "czechia": "Czechia",
    "canada": "Canada", "qatar": "Qatar", "switzerland": "Switzerland",
    "brazil": "Brazil", "morocco": "Morocco", "haiti": "Haiti",
    "scotland": "Scotland", "paraguay": "Paraguay", "australia": "Australia",
    "germany": "Germany", "ecuador": "Ecuador", "netherlands": "Netherlands",
    "japan": "Japan", "sweden": "Sweden", "tunisia": "Tunisia",
    "belgium": "Belgium", "egypt": "Egypt", "new zealand": "New Zealand",
    "spain": "Spain", "saudi arabia": "Saudi Arabia", "uruguay": "Uruguay",
    "france": "France", "senegal": "Senegal", "iraq": "Iraq", "norway": "Norway",
    "argentina": "Argentina", "algeria": "Algeria", "austria": "Austria",
    "jordan": "Jordan", "portugal": "Portugal", "uzbekistan": "Uzbekistan",
    "colombia": "Colombia", "england": "England", "croatia": "Croatia",
    "ghana": "Ghana", "panama": "Panama",
}

# The 12 groups, in YOUR canonical names, to drive the commented section order.
GROUPS = {
    "A": ["Mexico", "South Africa", "Korea Republic", "Czechia"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Türkiye"],
    "E": ["Germany", "Curaçao", "Cote d'Ivoire", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cabo Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Congo DR", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}
TEAM_GROUP = {t: g for g, ts in GROUPS.items() for t in ts}


def canonical(name: str):
    """Translate an API team name to your dashboard spelling, or None."""
    if name is None:
        return None
    key = str(name).strip().lower()
    return NAME_MAP.get(key)


def fetch_api():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "wc2026-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def extract_matches(payload):
    """
    Return a list of completed group matches as dicts:
        {"a": <canonical>, "b": <canonical>, "ga": int, "gb": int}

    Tuned to the worldcup26.ir response shape, e.g.:
        {
          "id":"6", "home_score":"2", "away_score":"0",
          "group":"D", "matchday":"1", "type":"group",
          "finished":"TRUE", "time_elapsed":"finished",
          "home_team_name_en":"Australia", "away_team_name_en":"Turkey", ...
        }
    Scores arrive as strings; finished is the string "TRUE".
    """
    # The matches live under "games"; stay defensive about the wrapper anyway.
    candidates = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for key in ("games", "matches", "data", "response", "result", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                candidates = v
                break
        else:
            for v in payload.values():
                if isinstance(v, list):
                    candidates = v
                    break

    def first(d, *keys):
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return d[k]
        return None

    def is_finished(m):
        # primary: finished == "TRUE"; backup: time_elapsed == "finished"
        fin = str(first(m, "finished") or "").strip().lower()
        if fin in ("true", "1", "yes"):
            return True
        te = str(first(m, "time_elapsed", "status", "state") or "").strip().lower()
        return te in ("finished", "ft", "full-time", "ended", "complete")

    out = []
    unmapped = set()
    for m in candidates:
        if not isinstance(m, dict):
            continue

        # group matches only
        mtype = str(first(m, "type") or "").strip().lower()
        if mtype and mtype != "group":
            continue

        if not is_finished(m):
            continue

        home = first(m, "home_team_name_en", "home_team", "home", "homeTeam")
        away = first(m, "away_team_name_en", "away_team", "away", "awayTeam")

        ga = first(m, "home_score", "score_home", "homeGoals", "ga")
        gb = first(m, "away_score", "score_away", "awayGoals", "gb")
        if ga is None or gb is None:
            continue
        try:
            ga, gb = int(str(ga).strip()), int(str(gb).strip())
        except (TypeError, ValueError):
            continue

        ca, cb = canonical(home), canonical(away)
        if ca is None:
            unmapped.add(str(home))
        if cb is None:
            unmapped.add(str(away))
        if ca is None or cb is None:
            continue

        # both teams must be in the same group (sanity)
        if TEAM_GROUP.get(ca) and TEAM_GROUP.get(ca) == TEAM_GROUP.get(cb):
            out.append({"a": ca, "b": cb, "ga": ga, "gb": gb})

    if unmapped:
        print("UNMAPPED team names (add to NAME_MAP):", sorted(unmapped),
              file=sys.stderr)
    return out


def dedupe(matches):
    """One result per unordered pair (last one wins)."""
    seen = {}
    for m in matches:
        key = tuple(sorted((m["a"], m["b"])))
        seen[key] = m
    return list(seen.values())


def render_results_block(matches):
    """Render the RESULTS array text, grouped & commented like your file."""
    by_group = {g: [] for g in GROUPS}
    for m in matches:
        by_group[TEAM_GROUP[m["a"]]].append(m)

    lines = ["const RESULTS = ["]
    # Matchday-style grouping is hard to reconstruct from results alone, so we
    # group by GROUP (clear and stable). Your engine doesn't care about order.
    for g in GROUPS:
        gm = by_group[g]
        if not gm:
            continue
        lines.append(f"  // \u2500\u2500 Group {g} \u2500\u2500")
        for m in gm:
            lines.append(
                f'  {{a:"{m["a"]}", b:"{m["b"]}", ga:{m["ga"]}, gb:{m["gb"]}}},'
            )
    lines.append("  // \u2500\u2500 Auto-updated from API; knockouts stay in KO_RESULTS \u2500\u2500")
    lines.append("];")
    return "\n".join(lines)


def main():
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # Count existing results for the safety guard.
    existing_block = re.search(r"const RESULTS\s*=\s*\[(.*?)\];", html, re.S)
    if not existing_block:
        print("ERROR: could not find RESULTS array in index.html", file=sys.stderr)
        sys.exit(1)
    existing_count = len(re.findall(r"\{a:", existing_block.group(1)))

    try:
        payload = fetch_api()
        matches = dedupe(extract_matches(payload))
    except Exception as e:
        print(f"ERROR fetching/parsing API: {e}", file=sys.stderr)
        print("Aborting; file unchanged.", file=sys.stderr)
        sys.exit(1)

    new_count = len(matches)
    print(f"Existing results: {existing_count}  |  API completed results: {new_count}")

    # SAFETY GUARD: never shrink. If the API gives us fewer than we have, or
    # zero, something is wrong -> change nothing.
    if new_count == 0 or new_count < existing_count:
        print("Guard triggered (API has fewer results than file). File unchanged.",
              file=sys.stderr)
        sys.exit(0)

    new_block = render_results_block(matches)
    new_html = re.sub(r"const RESULTS\s*=\s*\[.*?\];", new_block, html, count=1, flags=re.S)

    # Bump RESULTS_AS_OF to today (UTC), in your "24 June 2026" format.
    today = datetime.now(timezone.utc).strftime("%-d %B %Y") \
        if sys.platform != "win32" else datetime.now(timezone.utc).strftime("%d %B %Y")
    new_html = re.sub(r'const RESULTS_AS_OF\s*=\s*"[^"]*";',
                      f'const RESULTS_AS_OF = "{today}";', new_html, count=1)

    if new_html == html:
        print("No change after rewrite. File unchanged.")
        sys.exit(0)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"Updated index.html: {new_count} results, RESULTS_AS_OF = {today}")


if __name__ == "__main__":
    main()
