#!/usr/bin/env python3
"""
update_knockouts.py — auto-fill the KO_RESULTS array in index.html as knockout
games finish, mirroring the group-stage updater (update_results.py).

WHAT IT DOES
  • Fetches completed matches from the keyless worldcup26.ir API.
  • Keeps only FINISHED knockout games (Round of 32 onward, 28 June +).
  • Maps each game to its bracket match number `m` using a baked-in
    venue+date schedule (knockout pairings aren't known ahead of time, so we
    identify the slot by WHERE and WHEN it was played, which the bracket fixes).
  • Translates team names to the dashboard's exact spellings.
  • Detects penalty shootouts and records the `pso` winner.
  • Rewrites ONLY the `const KO_RESULTS = [ ... ];` block in index.html.
  • Has a no-shrink guard: it will never reduce the number of knockout results
    already present, so a bad/partial API response can't wipe entered games.

The group-stage script and this one are independent; run both (the workflow
does). Each rewrites only its own array, so they never collide.

Safe to run repeatedly — it only writes when something actually changed.
"""

import json
import re
import sys
import urllib.request

API_URL = "https://worldcup26.ir/get/games"
HTML_FILE = "index.html"

# ── Team-name translation (API spelling -> dashboard spelling) ──
# Mirrors update_results.py. Anything not listed passes through unchanged.
NAME_MAP = {
    "Czech Republic": "Czechia",
    "Czechia": "Czechia",
    "Turkey": "Türkiye",
    "Türkiye": "Türkiye",
    "Turkiye": "Türkiye",
    "South Korea": "Korea Republic",
    "Korea Republic": "Korea Republic",
    "Republic of Korea": "Korea Republic",
    "Ivory Coast": "Cote d'Ivoire",
    "Côte d'Ivoire": "Cote d'Ivoire",
    "Cote d'Ivoire": "Cote d'Ivoire",
    "Cabo Verde": "Cabo Verde",
    "Cape Verde": "Cabo Verde",
    "Curacao": "Curaçao",
    "Curaçao": "Curaçao",
    "DR Congo": "Congo DR",
    "Congo DR": "Congo DR",
    "Democratic Republic of the Congo": "Congo DR",
    "United States": "United States",
    "USA": "United States",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
}

def team(name):
    return NAME_MAP.get((name or "").strip(), (name or "").strip())

# ── Bracket schedule: match number -> (venue, ISO date) ──
# Taken straight from BRACKET_R32 / LATER_VENUE in index.html. A knockout game is
# matched to its slot by venue + date (both fixed by the bracket regardless of who
# plays). Dates are the LOCAL match date as the dashboard stores them (the `d:`
# "MM/DD" in the bracket -> "2026-MM-DD"). Round of 32 (73-88), R16 (89-96),
# QF (97-100), SF (101-102), 3rd place (103), Final (104).
KO_SCHEDULE = {
    73: ("Los Angeles", "2026-06-28"),
    74: ("Boston", "2026-06-29"),
    75: ("Monterrey", "2026-06-30"),
    76: ("Houston", "2026-06-29"),
    77: ("Atlanta", "2026-06-30"),
    78: ("Guadalajara", "2026-06-30"),
    79: ("Mexico City", "2026-07-01"),
    80: ("New York / New Jersey", "2026-07-01"),
    81: ("San Francisco Bay Area", "2026-07-02"),
    82: ("Seattle", "2026-07-01"),
    83: ("Dallas", "2026-07-03"),
    84: ("Miami", "2026-07-02"),
    85: ("Toronto", "2026-07-03"),
    86: ("Miami", "2026-07-03"),
    87: ("Kansas City", "2026-07-04"),
    88: ("Dallas", "2026-07-03"),
    # Round of 16
    89: ("Los Angeles", "2026-07-04"),
    90: ("Boston", "2026-07-04"),
    91: ("Dallas", "2026-07-05"),
    92: ("Mexico City", "2026-07-06"),
    93: ("Seattle", "2026-07-06"),
    94: ("Atlanta", "2026-07-07"),
    95: ("Houston", "2026-07-05"),
    96: ("Miami", "2026-07-07"),
    # Quarter-finals
    97: ("Kansas City", "2026-07-09"),
    98: ("Miami", "2026-07-10"),
    99: ("Boston", "2026-07-11"),
    100: ("Los Angeles", "2026-07-12"),
    # Semi-finals
    101: ("Dallas", "2026-07-14"),
    102: ("Atlanta", "2026-07-15"),
    # 3rd-place play-off & Final
    103: ("Miami", "2026-07-18"),
    104: ("New York / New Jersey", "2026-07-19"),
}

# Venue aliases the API might use -> dashboard venue spelling.
VENUE_MAP = {
    "East Rutherford": "New York / New Jersey",
    "New York": "New York / New Jersey",
    "New Jersey": "New York / New Jersey",
    "MetLife Stadium": "New York / New Jersey",
    "Santa Clara": "San Francisco Bay Area",
    "San Francisco": "San Francisco Bay Area",
    "Bay Area": "San Francisco Bay Area",
    "Inglewood": "Los Angeles",
    "Foxborough": "Boston",
    "Arlington": "Dallas",
    "Zapopan": "Guadalajara",
    "Guadalupe": "Monterrey",
}

def venue(name):
    return VENUE_MAP.get((name or "").strip(), (name or "").strip())

# Build a reverse lookup: (venue, date) -> match number.
SLOT_BY_VENUE_DATE = {(v, d): m for m, (v, d) in KO_SCHEDULE.items()}


def fetch_api():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "wc26-ko-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def is_finished(g):
    # API marks finished games with finished == "TRUE" (string) per the group updater.
    return str(g.get("finished", "")).strip().upper() == "TRUE"


def parse_knockouts(games):
    """Return {m: result-dict} for every finished knockout game we can place."""
    out = {}
    for g in games:
        if not is_finished(g):
            continue
        a = team(g.get("home_team_name_en") or g.get("home_team_name"))
        b = team(g.get("away_team_name_en") or g.get("away_team_name"))
        if not a or not b:
            continue
        # Scores
        try:
            ga = int(g.get("home_score", g.get("home_goals")))
            gb = int(g.get("away_score", g.get("away_goals")))
        except (TypeError, ValueError):
            continue
        # Date: API date field -> ISO yyyy-mm-dd (best-effort).
        raw_date = str(g.get("date") or g.get("match_date") or "")[:10]
        v = venue(g.get("venue") or g.get("stadium") or g.get("city"))
        m = SLOT_BY_VENUE_DATE.get((v, raw_date))
        if m is None:
            # Not a knockout slot we recognise (group game, or venue/date mismatch).
            continue
        rec = {"m": m, "a": a, "b": b, "ga": ga, "gb": gb, "venue": v, "d": raw_date}
        # Penalty shootout: if level after extra time, the API should carry a
        # shootout score / winner. Record the pso winner so the bracket advances.
        if ga == gb:
            pa = g.get("home_penalties", g.get("home_pen"))
            pb = g.get("away_penalties", g.get("away_pen"))
            try:
                pa, pb = int(pa), int(pb)
                rec["pso"] = a if pa > pb else b
            except (TypeError, ValueError):
                # Level score with no shootout data — skip rather than guess a winner.
                print(f"  ! M{m} {a} {ga}-{gb} {b}: level score but no penalty data; skipping until resolved.")
                continue
        out[m] = rec
    return out


def render_block(results):
    """Render the KO_RESULTS array body (sorted by match number)."""
    lines = []
    for m in sorted(results):
        r = results[m]
        a = r["a"].replace('"', '\\"')
        b = r["b"].replace('"', '\\"')
        v = r["venue"].replace('"', '\\"')
        line = f'  {{m:{m}, a:"{a}", b:"{b}", ga:{r["ga"]}, gb:{r["gb"]},'
        if "pso" in r:
            line += f' pso:"{r["pso"]}",'
        line += f' venue:"{v}", d:"{r["d"]}"}},'
        lines.append(line)
    if not lines:
        return "  // (no knockout games played yet — added automatically as they finish)"
    return "\n".join(lines)


def count_existing(html):
    block = re.search(r"const KO_RESULTS = \[(.*?)\];", html, re.S)
    if not block:
        return 0
    return len(re.findall(r"\{m:\s*\d+", block.group(1)))


def main():
    try:
        games = fetch_api()
    except Exception as e:
        print(f"API fetch failed: {e}")
        sys.exit(0)  # soft-fail: leave the file untouched

    games = games if isinstance(games, list) else games.get("games", [])
    parsed = parse_knockouts(games)
    print(f"API finished knockout games placed: {len(parsed)}")

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    existing = count_existing(html)

    # ── No-shrink guard: never reduce the number of knockout results. ──
    if len(parsed) < existing:
        print(f"Refusing to shrink KO_RESULTS ({existing} -> {len(parsed)}). "
              f"API response looks incomplete; leaving file unchanged.")
        sys.exit(0)

    new_body = render_block(parsed)
    new_block = f"const KO_RESULTS = [\n{new_body}\n];"

    updated, n = re.subn(r"const KO_RESULTS = \[.*?\];", new_block, html, count=1, flags=re.S)
    if n == 0:
        print("Could not find the KO_RESULTS block in index.html — no change made.")
        sys.exit(1)

    if updated == html:
        print("No knockout changes to write.")
        sys.exit(0)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"Updated KO_RESULTS: {existing} -> {len(parsed)} knockout results.")


if __name__ == "__main__":
    main()
