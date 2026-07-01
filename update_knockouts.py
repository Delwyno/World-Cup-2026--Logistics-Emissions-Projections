#!/usr/bin/env python3
"""
update_knockouts.py — auto-fill the KO_RESULTS array in index.html as knockout
games finish, mirroring the group-stage updater (update_results.py).

WHAT IT DOES
  • Fetches completed matches from the keyless worldcup26.ir API.
  • Keeps only FINISHED knockout games (Round of 32 onward).
  • Maps each game to its bracket match number `m`. The API labels knockout
    games with a round (`type`/`group` = "r32"/"R32", etc.) AND carries an `id`
    that equals the FIFA match number (e.g. Brazil-Japan is id "76" = M76), so we
    place each game by its id, validated against the expected knockout range and
    round. A venue+date fallback covers any game whose id is out of range.
  • Translates team names to the dashboard's exact spellings.
  • Detects penalty shootouts and records the `pso` winner (+ psa/psb scores).
  • Rewrites ONLY the `const KO_RESULTS = [ ... ];` block in index.html.
  • Has a no-shrink guard: it will never reduce the number of knockout results
    already present, so a bad/partial API response can't wipe entered games.

Safe to run repeatedly — it only writes when something actually changed.
"""

import json
import re
import sys
import urllib.request

API_URL = "https://worldcup26.ir/get/games"
HTML_FILE = "index.html"

# Valid knockout match numbers: R32 73-88, R16 89-96, QF 97-100, SF 101-102,
# 3rd-place 103, Final 104.
KO_MATCH_MIN, KO_MATCH_MAX = 73, 104


def round_of_match(m):
    """Which round a bracket match number belongs to."""
    if 73 <= m <= 88:   return "r32"
    if 89 <= m <= 96:   return "r16"
    if 97 <= m <= 100:  return "qf"
    if m in (101, 102): return "sf"
    if m == 103:        return "3p"
    if m == 104:        return "final"
    return None


# Normalise the many ways the API might label a knockout round.
ROUND_ALIASES = {
    "r32": "r32", "round of 32": "r32", "round-of-32": "r32", "ro32": "r32",
    "r16": "r16", "round of 16": "r16", "round-of-16": "r16", "ro16": "r16",
    "qf": "qf", "quarter-final": "qf", "quarter-finals": "qf", "quarterfinal": "qf",
    "quarter final": "qf", "quarters": "qf",
    "sf": "sf", "semi-final": "sf", "semi-finals": "sf", "semifinal": "sf",
    "semi final": "sf", "semis": "sf",
    "3p": "3p", "third place": "3p", "third-place": "3p", "3rd place": "3p",
    "3rd-place": "3p", "play-off for third place": "3p", "bronze": "3p",
    "final": "final", "the final": "final",
}


def norm_round(raw):
    """Map an API round/type/group string to r32/r16/qf/sf/3p/final, or None."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in ROUND_ALIASES:
        return ROUND_ALIASES[s]
    for key, val in ROUND_ALIASES.items():   # tolerate "R32", "KO-R16", etc.
        if key in s:
            return val
    return None


# ── Team-name translation (API spelling -> dashboard spelling) ──
NAME_MAP = {
    "Czech Republic": "Czechia", "Czechia": "Czechia",
    "Turkey": "Türkiye", "Türkiye": "Türkiye", "Turkiye": "Türkiye",
    "South Korea": "Korea Republic", "Korea Republic": "Korea Republic",
    "Republic of Korea": "Korea Republic",
    "Ivory Coast": "Cote d'Ivoire", "Côte d'Ivoire": "Cote d'Ivoire",
    "Cote d'Ivoire": "Cote d'Ivoire",
    "Cabo Verde": "Cabo Verde", "Cape Verde": "Cabo Verde",
    "Curacao": "Curaçao", "Curaçao": "Curaçao",
    "DR Congo": "Congo DR", "Congo DR": "Congo DR",
    "Democratic Republic of the Congo": "Congo DR",
    "United States": "United States", "USA": "United States",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
}


def team(name):
    return NAME_MAP.get((name or "").strip(), (name or "").strip())


# ── Bracket schedule: match number -> (venue, ISO date) ──
# Used to fill venue/date on each record, and as a FALLBACK placement when the
# API id is somehow out of range.
KO_SCHEDULE = {
    73: ("Los Angeles", "2026-06-28"), 74: ("Boston", "2026-06-29"),
    75: ("Monterrey", "2026-06-30"), 76: ("Houston", "2026-06-29"),
    77: ("Atlanta", "2026-06-30"), 78: ("Guadalajara", "2026-06-30"),
    79: ("Mexico City", "2026-07-01"), 80: ("New York / New Jersey", "2026-07-01"),
    81: ("San Francisco Bay Area", "2026-07-02"), 82: ("Seattle", "2026-07-01"),
    83: ("Dallas", "2026-07-03"), 84: ("Miami", "2026-07-02"),
    85: ("Toronto", "2026-07-03"), 86: ("Miami", "2026-07-03"),
    87: ("Kansas City", "2026-07-04"), 88: ("Dallas", "2026-07-03"),
    89: ("Los Angeles", "2026-07-04"), 90: ("Boston", "2026-07-04"),
    91: ("Dallas", "2026-07-05"), 92: ("Mexico City", "2026-07-06"),
    93: ("Seattle", "2026-07-06"), 94: ("Atlanta", "2026-07-07"),
    95: ("Houston", "2026-07-05"), 96: ("Miami", "2026-07-07"),
    97: ("Kansas City", "2026-07-09"), 98: ("Miami", "2026-07-10"),
    99: ("Boston", "2026-07-11"), 100: ("Los Angeles", "2026-07-12"),
    101: ("Dallas", "2026-07-14"), 102: ("Atlanta", "2026-07-15"),
    103: ("Miami", "2026-07-18"), 104: ("New York / New Jersey", "2026-07-19"),
}

# Venue aliases the API might use -> dashboard venue spelling.
VENUE_MAP = {
    "East Rutherford": "New York / New Jersey", "New York": "New York / New Jersey",
    "New Jersey": "New York / New Jersey", "MetLife Stadium": "New York / New Jersey",
    "Santa Clara": "San Francisco Bay Area", "San Francisco": "San Francisco Bay Area",
    "Bay Area": "San Francisco Bay Area", "Inglewood": "Los Angeles",
    "Foxborough": "Boston", "Arlington": "Dallas", "Zapopan": "Guadalajara",
    "Guadalupe": "Monterrey",
}

# Map the API's numeric stadium_id -> dashboard venue. Derived from observed API
# rows (stadium_id "5" = Houston, seen in the Brazil-Japan R32 game). Extend as
# other ids are confirmed; unknown ids just fall back to id-based placement.
STADIUM_ID_VENUE = {
    "5": "Houston",
}


def venue(name):
    return VENUE_MAP.get((name or "").strip(), (name or "").strip())


SLOT_BY_VENUE_DATE = {(v, d): m for m, (v, d) in KO_SCHEDULE.items()}


def to_iso_date(raw):
    """Best-effort convert an API date to ISO yyyy-mm-dd.
    Handles 'MM/DD/YYYY [HH:MM]' (worldcup26.ir's local_date) and ISO."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split(" ")[0].split("T")[0]          # drop any time component
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return s
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)   # MM/DD/YYYY (US)
    if m:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    return ""


def fetch_api():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "wc26-ko-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def is_finished(g):
    return str(g.get("finished", "")).strip().upper() == "TRUE"


def _int(*vals):
    for v in vals:
        if v is None:
            continue
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            continue
    return None


def match_number(g):
    """Determine the bracket match number for a knockout game.
    Primary: the API `id` (equals the FIFA match number), cross-checked against
    the expected round. Fallback: stadium/venue + date lookup."""
    api_round = norm_round(g.get("type") or g.get("group") or g.get("stage")
                           or g.get("round"))
    # 1) id-based placement (most reliable — id == match number).
    mid = _int(g.get("id"), g.get("match_id"), g.get("game_id"))
    if mid is not None and KO_MATCH_MIN <= mid <= KO_MATCH_MAX:
        exp = round_of_match(mid)
        if api_round is None or api_round == exp:
            return mid
    # 2) venue + date fallback.
    v = STADIUM_ID_VENUE.get(str(g.get("stadium_id") or "").strip(), "")
    if not v:
        v = venue(g.get("venue") or g.get("stadium") or g.get("city"))
    d = to_iso_date(g.get("local_date") or g.get("date") or g.get("match_date"))
    return SLOT_BY_VENUE_DATE.get((v, d))


def is_knockout(g):
    """True if this game is a knockout game (by round label or id range)."""
    if norm_round(g.get("type") or g.get("group") or g.get("stage") or g.get("round")):
        return True
    mid = _int(g.get("id"), g.get("match_id"), g.get("game_id"))
    return mid is not None and KO_MATCH_MIN <= mid <= KO_MATCH_MAX


def parse_knockouts(games):
    """Return {m: result-dict} for every finished knockout game we can place."""
    out = {}
    for g in games:
        if not is_finished(g):
            continue
        if not is_knockout(g):
            continue
        a = team(g.get("home_team_name_en") or g.get("home_team_name"))
        b = team(g.get("away_team_name_en") or g.get("away_team_name"))
        if not a or not b:
            continue
        ga = _int(g.get("home_score"), g.get("home_goals"))
        gb = _int(g.get("away_score"), g.get("away_goals"))
        if ga is None or gb is None:
            continue
        m = match_number(g)
        if m is None:
            print(f"  ? Could not place knockout game {a} {ga}-{gb} {b} "
                  f"(id={g.get('id')}, round={g.get('type') or g.get('group')}, "
                  f"stadium_id={g.get('stadium_id')}, "
                  f"date={g.get('local_date') or g.get('date')})")
            continue
        v, d = KO_SCHEDULE.get(m, (venue(g.get("venue") or g.get("stadium") or ""),
                                   to_iso_date(g.get("local_date") or g.get("date"))))
        rec = {"m": m, "a": a, "b": b, "ga": ga, "gb": gb, "venue": v, "d": d}
        # Penalty shootout: level score after extra time.
        if ga == gb:
            pa = _int(g.get("home_penalties"), g.get("home_pen"), g.get("home_pens"),
                      g.get("home_penalty"), g.get("home_shootout"))
            pb = _int(g.get("away_penalties"), g.get("away_pen"), g.get("away_pens"),
                      g.get("away_penalty"), g.get("away_shootout"))
            if pa is not None and pb is not None:
                rec["pso"] = a if pa > pb else b
                rec["psa"], rec["psb"] = pa, pb
            else:
                print(f"  ! M{m} {a} {ga}-{gb} {b}: level score but no penalty "
                      f"data; skipping until resolved.")
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
            if "psa" in r and "psb" in r:
                line += f' psa:{r["psa"]}, psb:{r["psb"]},'
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

    updated, n = re.subn(r"const KO_RESULTS = \[.*?\];", new_block, html,
                         count=1, flags=re.S)
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
