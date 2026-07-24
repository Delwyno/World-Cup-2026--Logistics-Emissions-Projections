#!/usr/bin/env python3
"""
optimise_counterfactual.py — computes the "optimised tournament" counterfactual
for the Summary tab and bakes the result into index.html as static data.

TWO TIERS
  1. BASES  — keep every fixture exactly where it was played, but reassign
     which team uses which base camp (the same 48 camps actually used, as a
     multiset — two teams really did share Kansas City, so two Kansas City
     slots exist). Solved EXACTLY with the Hungarian algorithm: the
     assignment of teams to camp-slots that minimises total team flight CO2e.
  2. FIXTURES — additionally allow matches to swap venues, but only with
     matches on the SAME calendar day (so every stadium hosts exactly the
     games-per-day it really hosted — no impossible schedules), and never
     moving a host nation (USA / Mexico / Canada) out of its own country.
     Fan travel now moves too, because fans fly home→venue. Solved with
     simulated annealing over same-day swaps, alternated with re-running the
     Hungarian for bases (the two interact: better venues change which camp
     is best for each team).

SCOPE — matches actually PLAYED only (group + knockouts so far), no
projections, no return-home legs, mirroring the Summary tab's totals table.

FAN MODEL — recomputed from first principles here (home → each venue in
order, one-way tour, DESNZ banded factors), for both the before AND after
figures, so the comparison is apples-to-apples. The before figure may differ
slightly from the site's baked FAN_DATA headline (which came from the
spreadsheet model); the DELTA is the meaningful number.

USAGE
  python optimise_counterfactual.py            # optimise + inject into index.html
  python optimise_counterfactual.py --dry-run  # print results, don't touch the file

Re-run any time results update (e.g. after each knockout round) — injection is
idempotent, replacing the previous bake.
"""

import json
import math
import random
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from update_history import HARNESS_PREFIX, extract_scripts

HTML_FILE = "index.html"
SEED = 26  # fixed seed: deterministic output for a given set of results
ANNEAL_ITERS = 60000
# PAX and the emission-factor bands are read from index.html at runtime — see
# install_factors() below. Nothing about the emissions model is duplicated here.

# ── Home-city coordinates for fan journeys (FAN_DATA carries only names) ──
HOME_COORDS = {
    "Buenos Aires": (-34.6037, -58.3816), "London": (51.5074, -0.1278),
    "Tokyo": (35.6762, 139.6503), "Berlin": (52.5200, 13.4050),
    "Seoul": (37.5665, 126.9780), "Brasília": (-15.7939, -47.8828),
    "Canberra": (-35.2809, 149.1300), "Glasgow": (55.8642, -4.2518),
    "Washington DC": (38.9072, -77.0369), "Madrid": (40.4168, -3.7038),
    "Amsterdam": (52.3676, 4.9041), "Bogotá": (4.7110, -74.0721),
    "Ankara": (39.9334, 32.8597), "Riyadh": (24.7136, 46.6753),
    "Pretoria": (-25.7479, 28.2293), "Paris": (48.8566, 2.3522),
    "Rabat": (34.0209, -6.8416), "Lisbon": (38.7223, -9.1393),
    "Brussels": (50.8503, 4.3517), "Bern": (46.9480, 7.4474),
    "Zagreb": (45.8150, 15.9819), "Mexico City": (19.4326, -99.1332),
    "Stockholm": (59.3293, 18.0686), "Vienna": (48.2082, 16.3738),
    "Doha": (25.2854, 51.5310), "Prague": (50.0755, 14.4378),
    "Ottawa": (45.4215, -75.6972), "Montevideo": (-34.9011, -56.1645),
    "Asunción": (-25.2637, -57.5759), "Cairo": (30.0444, 31.2357),
    "Sarajevo": (43.8563, 18.4131), "Accra": (5.6037, -0.1870),
    "Quito": (-0.1807, -78.4678), "Algiers": (36.7538, 3.0588),
    "Kinshasa": (-4.4419, 15.2663), "Baghdad": (33.3152, 44.3661),
    "Oslo": (59.9139, 10.7522), "Tashkent": (41.2995, 69.2401),
    "Wellington": (-41.2865, 174.7762), "Tunis": (36.8065, 10.1815),
    "Amman": (31.9454, 35.9284), "Abidjan": (5.3600, -4.0083),
    "Dakar": (14.7167, -17.4677), "Panama City": (8.9824, -79.5199),
    "Praia": (14.9330, -23.5133), "Willemstad": (12.1224, -68.8824),
    "Tehran": (35.6892, 51.3890), "Port-au-Prince": (18.5944, -72.3074),
}

DUMP_SUFFIX = r"""
try {
  const playedGroup = [];
  for (const f of FIXTURES) {
    const played = RESULTS.some(r => (r.a===f.a&&r.b===f.b)||(r.a===f.b&&r.b===f.a));
    if (played) playedGroup.push({date:f.d, a:f.a, b:f.b, venue:f.v, stage:"group"});
  }
  const playedKO = KO_RESULTS.map(r => ({date:r.d||"", a:r.a, b:r.b, venue:r.venue||"", m:r.m, stage:"ko"}));
  const teams = {};
  for (const t in TEAMS) teams[t] = {camp: TEAMS[t].tc, flag: TEAMS[t].flag};
  const cities = {};
  for (const c in CITY_DATA) cities[c] = {lat: CITY_DATA[c].lat, lng: CITY_DATA[c].lng, ctry: CITY_DATA[c].ctry||null};
  const fans = FAN_DATA.map(f => ({team:f.team, fans:f.fans, home:f.home}));
  // Emission factors are read out of the page rather than duplicated here, so
  // retuning the bands in index.html can never leave this script out of sync.
  // We sample the real functions across the band boundaries and rebuild the
  // step function from the results — no assumption about how many bands there
  // are or where they break.
  const probe = [];
  for (let km = 1; km <= 20000; km += 1) probe.push(km);
  const sample = fn => {
    const out = []; let prev = null;
    for (const km of probe) {
      const v = fn(km);
      if (prev === null || v !== prev) { out.push({from: km, ef: v}); prev = v; }
    }
    return out;
  };
  const factors = {
    team: sample(teamBandEF),
    fan: sample(typeof bandEF === "function" ? bandEF : fanLegEF),
    pax: (typeof PAX !== "undefined" ? PAX : 45)
  };
  const out = {matches: playedGroup.concat(playedKO), teams, cities, fans, factors};
  process.stdout.write("__CF_JSON__" + JSON.stringify(out) + "__CF_JSON__");
  process.exit(0);
} catch (e) { process.stderr.write("DUMP_ERROR: " + (e&&e.stack||e)); process.exit(1); }
"""


def hav(a, b):
    (la1, ln1), (la2, ln2) = a, b
    R, p = 6371, math.pi / 180
    x = math.sin((la2-la1)*p/2)**2 + math.cos(la1*p)*math.cos(la2*p)*math.sin((ln2-ln1)*p/2)**2
    return round(2*R*math.asin(math.sqrt(x)))


# ── Emission factors ──────────────────────────────────────────────────────
# These are NOT hardcoded: they're sampled from index.html's own teamBandEF()
# and bandEF() at runtime (see DUMP_SUFFIX) and installed here by
# install_factors(). Retuning the bands in index.html automatically retunes
# this script — the two can't drift apart. The module-level defaults below are
# only a fallback if extraction somehow yields nothing.
_TEAM_BANDS = [{"from": 1, "ef": 0.22928}]
_FAN_BANDS = [{"from": 1, "ef": 0.02776}]
PAX = 45


def install_factors(factors):
    """Adopt the emission factors sampled from index.html."""
    global _TEAM_BANDS, _FAN_BANDS, PAX
    if factors.get("team"): _TEAM_BANDS = factors["team"]
    if factors.get("fan"):  _FAN_BANDS = factors["fan"]
    PAX = factors.get("pax", 45)


def _lookup(bands, km):
    ef = bands[0]["ef"]
    for b in bands:
        if km >= b["from"]: ef = b["ef"]
        else: break
    return ef


def band_ef(km):        # fan legs (includes the coach band for short hops)
    return _lookup(_FAN_BANDS, km)


def team_band_ef(km):   # squads always fly: no coach band
    return _lookup(_TEAM_BANDS, km)


def team_leg_co2(km):
    return PAX * team_band_ef(km) * km  # kg, one-way


def describe_factors():
    def fmt(bands):
        return ", ".join(f"{b['from']}km+ → {b['ef']}" for b in bands)
    return f"team: {fmt(_TEAM_BANDS)}\n  fan:  {fmt(_FAN_BANDS)}\n  PAX:  {PAX}"


def dump_site_data():
    with open(HTML_FILE, encoding="utf-8") as f:
        html = f.read()
    js = HARNESS_PREFIX + "\n".join(extract_scripts(html)) + DUMP_SUFFIX
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    r = subprocess.run(["node", path], capture_output=True, text=True, timeout=120)
    m = re.search(r"__CF_JSON__(.*)__CF_JSON__", r.stdout, re.S)
    if not m:
        print("Could not extract site data:", (r.stderr or "")[-500:])
        sys.exit(1)
    return json.loads(m.group(1)), html


def hungarian(cost):
    """Kuhn-Munkres with potentials, O(n^3). cost: n×n list. Returns col-of-row list."""
    n = len(cost)
    INF = float("inf")
    u = [0.0]*(n+1); v = [0.0]*(n+1)
    p = [0]*(n+1); way = [0]*(n+1)
    for i in range(1, n+1):
        p[0] = i
        j0 = 0
        minv = [INF]*(n+1)
        used = [False]*(n+1)
        while True:
            used[j0] = True
            i0 = p[j0]; delta = INF; j1 = -1
            for j in range(1, n+1):
                if used[j]: continue
                cur = cost[i0-1][j-1] - u[i0] - v[j]
                if cur < minv[j]: minv[j] = cur; way[j] = j0
                if minv[j] < delta: delta = minv[j]; j1 = j
            for j in range(n+1):
                if used[j]: u[p[j]] += delta; v[j] -= delta
                else: minv[j] -= delta
            j0 = j1
            if p[j0] == 0: break
        while j0:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
    ans = [0]*n
    for j in range(1, n+1):
        if p[j]: ans[p[j]-1] = j-1
    return ans


class Model:
    def __init__(self, data):
        self.cities = data["cities"]
        self.teams = sorted(data["teams"].keys())
        self.camp_of = {t: data["teams"][t]["camp"] for t in self.teams}
        self.flag_of = {t: data["teams"][t]["flag"] for t in self.teams}
        self.camp_slots = [self.camp_of[t] for t in self.teams]  # multiset of camps in use
        self.fans = {f["team"]: f for f in data["fans"]}
        # host-nation constraint lookup: team -> required venue country
        self.host_ctry = {"United States": "USA", "Mexico": "MEX", "Canada": "CAN"}
        # matches sorted chronologically; venue is the mutable slot
        self.matches = sorted([m for m in data["matches"] if m["venue"] in self.cities],
                              key=lambda m: (m["date"], m.get("m", 0)))
        self.venues = [m["venue"] for m in self.matches]          # current assignment
        self.by_date = {}
        for i, m in enumerate(self.matches):
            self.by_date.setdefault(m["date"], []).append(i)
        # per-team chronological match indices
        self.team_mi = {t: [] for t in self.teams}
        for i, m in enumerate(self.matches):
            for t in (m["a"], m["b"]):
                if t in self.team_mi: self.team_mi[t].append(i)

    def coord(self, place):
        c = self.cities[place]
        return (c["lat"], c["lng"])

    def team_cost(self, team, camp, venues):
        """Team flight cost, mirroring index.html's own model exactly.

        GROUP legs are round trips: the squad flies out from base camp to the
        match city and back again between fixtures.

        KNOCKOUT legs are NOT round trips — the site models the knockout run as
        one continuous journey, hopping venue → venue without returning to base
        (which is what actually happens: teams relocate for the duration). The
        first knockout hop starts from the last group venue, not from camp.

        Getting this wrong is what made an earlier version of this script report
        a baseline ~30% above the site's own totals table for the same tournament.
        """
        cc = self.coord(camp)
        km = co2 = 0
        idxs = self.team_mi[team]
        group_idx = [i for i in idxs if self.matches[i]["stage"] == "group"]
        ko_idx = [i for i in idxs if self.matches[i]["stage"] == "ko"]

        for i in group_idx:                      # round trip camp → venue → camp
            d = hav(cc, self.coord(venues[i]))
            km += d * 2
            co2 += team_leg_co2(d) * 2

        # Knockout: chain from wherever the squad finished the group stage.
        # M103 (third-place playoff) is deliberately excluded: index.html's
        # knockoutTravel() walks the bracket R32→R16→QF→SF→Final, and 103 sits
        # off that path, so the site never counts the hop to the third-place
        # game. Counting it here would make France and England disagree with the
        # site's own totals table by ~10 t each. Arguably both should count it —
        # those flights really happened — but the two figures must agree, and
        # the site's is the published one.
        prev = self.coord(venues[group_idx[-1]]) if group_idx else cc
        for i in ko_idx:                         # one-way hops, no return home
            if self.matches[i].get("m") == 103:
                continue
            cur = self.coord(venues[i])
            d = hav(prev, cur)
            km += d
            co2 += team_leg_co2(d)
            prev = cur
        return km, co2

    def total_team(self, assign, venues):
        km = co2 = 0.0
        for ti, t in enumerate(self.teams):
            k, c = self.team_cost(t, self.camp_slots[assign[ti]], venues)
            km += k; co2 += c
        return km, co2/1000.0

    def fan_cost(self, team, venues):
        fd = self.fans.get(team)
        if not fd or fd["home"] not in HOME_COORDS: return 0, 0
        prev = HOME_COORDS[fd["home"]]
        km = co2 = 0.0
        for i in self.team_mi[team]:
            cur = self.coord(venues[i])
            d = hav(prev, cur)
            km += d; co2 += d*band_ef(d)
            prev = cur
        return km*fd["fans"], co2*fd["fans"]/1000.0  # total km, tCO2e

    def total_fan(self, venues):
        km = co2 = 0.0
        for t in self.teams:
            k, c = self.fan_cost(t, venues)
            km += k; co2 += c
        return km, co2

    def best_bases(self, venues):
        """Hungarian: minimise total team CO2 over camp-slot assignment."""
        n = len(self.teams)
        cost = [[self.team_cost(t, self.camp_slots[j], venues)[1] for j in range(n)]
                for t in self.teams]
        return hungarian(cost)

    def swap_ok(self, i, j, venues):
        """Host teams must stay in their own country after swapping venues i<->j."""
        vi, vj = venues[j], venues[i]  # post-swap venues for match i, j
        for mi, v in ((i, vi), (j, vj)):
            m = self.matches[mi]
            for t in (m["a"], m["b"]):
                req = self.host_ctry.get(t)
                if req and self.cities[v].get("ctry") != req:
                    return False
        return True


def objective(model, assign, venues):
    _, tco2 = model.total_team(assign, venues)
    _, fco2 = model.total_fan(venues)
    return tco2 + fco2


def anneal(model, assign, venues, iters=ANNEAL_ITERS):
    rnd = random.Random(SEED)
    venues = venues[:]
    cur = objective(model, assign, venues)
    best = cur; best_v = venues[:]; best_a = assign[:]
    dates = [d for d, idx in model.by_date.items() if len(idx) > 1]
    t0, t1 = cur*0.002, cur*0.000002
    for it in range(iters):
        T = t0 * (t1/t0) ** (it/iters)
        d = rnd.choice(dates)
        idx = model.by_date[d]
        i, j = rnd.sample(idx, 2)
        if venues[i] == venues[j] or not model.swap_ok(i, j, venues):
            continue
        venues[i], venues[j] = venues[j], venues[i]
        new = objective(model, assign, venues)
        if new < cur or rnd.random() < math.exp((cur-new)/max(T, 1e-9)):
            cur = new
            if cur < best:
                best, best_v = cur, venues[:]
        else:
            venues[i], venues[j] = venues[j], venues[i]
        # periodically re-optimise bases for the evolving venue map
        if (it+1) % 8000 == 0:
            assign = model.best_bases(venues)
            cur = objective(model, assign, venues)
            if cur < best:
                best, best_v, best_a = cur, venues[:], assign[:]
    assign = model.best_bases(best_v)
    return assign, best_v


def main():
    dry = "--dry-run" in sys.argv
    print("[1] Extracting played matches, camps, coordinates from index.html")
    data, html = dump_site_data()
    install_factors(data.get("factors", {}))
    model = Model(data)
    print(f"  ✓ {len(model.matches)} played matches, {len(model.teams)} teams, "
          f"{len(set(model.camp_slots))} distinct camps ({len(model.camp_slots)} slots)")
    print("  ✓ emission factors read from index.html —")
    print("  " + describe_factors())

    actual_assign = list(range(len(model.teams)))  # identity: team i in its own camp
    venues0 = model.venues[:]

    km_b, co2_b = model.total_team(actual_assign, venues0)
    fkm_b, fco2_b = model.total_fan(venues0)
    print(f"  actual: team {km_b:,.0f} km / {co2_b:,.1f} t · fans {fkm_b:,.0f} km / {fco2_b:,.0f} t")

    print("[2] Tier 1 — optimal base assignment (Hungarian, exact)")
    a1 = model.best_bases(venues0)
    km_1, co2_1 = model.total_team(a1, venues0)
    print(f"  ✓ optimised bases: {km_1:,.0f} km / {co2_1:,.1f} t "
          f"(−{(1-co2_1/co2_b)*100:.1f}% team CO2e)")

    print(f"[3] Tier 2 — venue reassignment (annealing, {ANNEAL_ITERS:,} iterations)")
    a2, v2 = anneal(model, a1[:], venues0)
    km_2, co2_2 = model.total_team(a2, v2)
    fkm_2, fco2_2 = model.total_fan(v2)
    moved = sum(1 for i in range(len(venues0)) if venues0[i] != v2[i])
    print(f"  ✓ optimised fixtures: team {km_2:,.0f} km / {co2_2:,.1f} t · "
          f"fans {fkm_2:,.0f} km / {fco2_2:,.0f} t · {moved} matches moved")

    assignments = []
    for ti, t in enumerate(model.teams):
        frm, to = model.camp_of[t], model.camp_slots[a1[ti]]
        if frm != to:
            k_from, c_from = model.team_cost(t, frm, venues0)
            k_to, c_to = model.team_cost(t, to, venues0)
            assignments.append({"team": t, "flag": model.flag_of[t],
                                "from": frm, "to": to,
                                "saveKm": k_from - k_to,
                                "saveT": round((c_from - c_to)/1000.0, 1)})
    # Sort by emissions saved — the optimiser's actual objective. (A move can
    # save CO2e while adding km: the DESNZ band steps make some longer legs
    # cheaper per-km, so km is a secondary detail here, not the ranking.)
    assignments.sort(key=lambda a: -a["saveT"])

    result = {
        "computed": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "note": ("Fan figures are recomputed here from first principles (home → each venue, "
                 "DESNZ banded factors) for both scenarios, so before/after is like-for-like."),
        "bases": {
            "nCamps": len(set(model.camp_slots)),
            "teamKmBefore": round(km_b), "teamCo2Before": round(co2_b, 1),
            "teamKmAfter": round(km_1), "teamCo2After": round(co2_1, 1),
            "assignments": assignments,
        },
        "fixtures": {
            "matchesMoved": moved,
            "teamKmAfter": round(km_2), "teamCo2After": round(co2_2, 1),
            "fanKmBefore": round(fkm_b), "fanCo2Before": round(fco2_b),
            "fanKmAfter": round(fkm_2), "fanCo2After": round(fco2_2),
        },
    }

    if dry:
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return

    print("[4] Injecting into index.html")
    blob = "const COUNTERFACTUAL=" + json.dumps(result, ensure_ascii=False) + "; /* COUNTERFACTUAL_INJECT */"
    new_html, n = re.subn(r"const COUNTERFACTUAL=.*?; /\* COUNTERFACTUAL_INJECT \*/",
                          blob.replace("\\", "\\\\"), html, count=1, flags=re.S)
    if n != 1:
        print("  ✗ COUNTERFACTUAL_INJECT sentinel not found — is this the updated index.html?")
        sys.exit(1)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("  ✓ injected. Reload the Summary tab to see it.")


if __name__ == "__main__":
    main()
