#!/usr/bin/env python3
"""
update_history.py — appends today's title-odds snapshot to title_history.json.

Run this AFTER update_results.py / update_knockouts.py / update_elo.py (so the
snapshot reflects the freshest results and ratings) and BEFORE the commit step.

HOW IT WORKS
  index.html already computes live title odds client-side via the
  championProbabilities() function, using the page's own RESULTS, KO_RESULTS,
  BRACKET_* and FIFA_STRENGTH data. Rather than re-implementing that Monte
  Carlo model in Python (and risking it drifting out of sync with the JS), this
  script runs the *actual* function: it extracts the inline <script> blocks
  from index.html, executes them in a sandboxed Node.js vm with minimal
  DOM stubs (nothing in championProbabilities touches the DOM — the stubs
  exist only so unrelated top-level code, like event-listener registration,
  doesn't throw), then calls championProbabilities(3000) and reads the result
  back out as JSON.

  The result is appended to title_history.json as one entry per UTC calendar
  day. If a run already happened today, that day's entry is overwritten in
  place (not duplicated) so multiple runs per day don't bloat the file —
  each day just reflects the latest odds as of the last run that day.

OUTPUT FORMAT (title_history.json)
  [
    {"date": "2026-06-28", "champ": {"Spain": 28.1, "France": 26.0, ...}},
    {"date": "2026-06-29", "champ": {"Spain": 27.4, "France": 25.8, ...}},
    ...
  ]
  Percentages are stored to 1 decimal place, matching the site's own display
  rounding, so history and "now" always agree exactly.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HTML_FILE = "index.html"
HISTORY_FILE = "title_history.json"
ITERS = 3000

# Minimal, permissive stub so any incidental top-level DOM/window access in
# the page's scripts (event listener registration, CDN fallback IIFEs, etc.)
# no-ops instead of throwing. Nothing championProbabilities() touches needs
# real DOM — it's pure data + math over GROUPS/RESULTS/KO_RESULTS/BRACKET_*.
HARNESS_PREFIX = r"""
'use strict';
function makeStub(){
  const target = function(){};
  const handler = {
    get(t, prop){
      if(prop === 'then' || prop === 'catch' || prop === 'finally') return undefined;
      if(prop === Symbol.toPrimitive) return () => 0;
      if(prop === Symbol.iterator) return function*(){};
      return makeStub();
    },
    apply(){ return makeStub(); },
    set(){ return true; }
  };
  return new Proxy(target, handler);
}
const document = makeStub();
const window = {
  addEventListener: function(){},
  document: document,
  navigator: { userAgent: 'node' },
  location: { search: '', hash: '', href: '', protocol: 'https:' },
  history: { replaceState: function(){} },
  localStorage: undefined,
  d3: makeStub(),
  topojson: makeStub(),
};
const navigator = window.navigator;
const localStorage = undefined;
const d3 = window.d3;
const topojson = window.topojson;
const location = window.location;
const history = window.history;
"""

HARNESS_SUFFIX = r"""
try {
  const odds = championProbabilities(__ITERS__);
  const out = {};
  for (const team in odds) out[team] = Math.round(odds[team].champ * 1000) / 10;
  process.stdout.write("__HISTORY_JSON_START__" + JSON.stringify(out) + "__HISTORY_JSON_END__");
  process.exit(0);
} catch (e) {
  process.stderr.write("HARNESS_ERROR: " + (e && e.stack || e));
  process.exit(1);
}
"""


def extract_scripts(html):
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def run_harness(html):
    node = shutil.which("node")
    if not node:
        print("  ! node not found on runner — cannot compute title odds, skipping snapshot")
        return None

    blocks = extract_scripts(html)
    if not blocks:
        print("  ✗ no inline <script> blocks found in index.html")
        return None

    js = HARNESS_PREFIX + "\n".join(blocks) + HARNESS_SUFFIX.replace("__ITERS__", str(ITERS))

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name

    r = subprocess.run([node, path], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"  ✗ harness failed: {(r.stderr or '').strip()[-800:]}")
        return None

    m = re.search(r"__HISTORY_JSON_START__(.*)__HISTORY_JSON_END__", r.stdout, re.S)
    if not m:
        print("  ✗ harness produced no output")
        return None

    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  ✗ could not parse harness output: {e}")
        return None


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"  ! {HISTORY_FILE} was not a list — starting fresh")
            return []
        return data
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print(f"  ! {HISTORY_FILE} was malformed — starting fresh")
        return []


def main():
    try:
        with open(HTML_FILE, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print(f"Cannot read {HTML_FILE}: {e}")
        sys.exit(1)

    print("[1] Computing current title odds")
    champ = run_harness(html)
    if champ is None:
        print("Aborting; title_history.json unchanged.")
        # Soft-fail: a snapshot miss shouldn't fail the whole workflow run.
        sys.exit(0)
    ok_teams = len(champ)
    print(f"  ✓ computed odds for {ok_teams} teams")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("[2] Updating title_history.json")
    history = load_history()
    if history and history[-1].get("date") == today:
        history[-1]["champ"] = champ
        print(f"  ✓ replaced existing entry for {today} ({len(history)} days total)")
    else:
        history.append({"date": today, "champ": champ})
        print(f"  ✓ appended new entry for {today} ({len(history)} days total)")

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=1, sort_keys=False)
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
