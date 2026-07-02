#!/usr/bin/env python3
"""
validate_site.py — CI sanity check for index.html, run AFTER the auto-updaters
and BEFORE "Commit if changed". The updaters rewrite index.html with regex, so
one malformed API response could otherwise commit a broken page. This script
fails the workflow (exit 1) instead, leaving the last good deploy live.

CHECKS
  1. Every inline <script> block parses as JavaScript (via `node --check`;
     skipped with a warning if node isn't on the runner).
  2. The RESULTS array parses and has exactly 72 group results, each with
     integer scores and non-empty team names.
  3. The KO_RESULTS array parses; every match number is in 73–104 with no
     duplicates; scores are integers; level games carry a pso winner that is
     one of the two teams.
  4. FIFA_STRENGTH has exactly 48 teams, every rating within 1000–2200.
  5. Basic structure: <div> open/close counts balance; the four core arrays/
     tables (RESULTS, KO_RESULTS, FIFA_STRENGTH, BRACKET_R32) are all present.

Add to the workflow between the updaters and the commit step:
    - name: Validate site
      run: python validate_site.py
"""

import re
import shutil
import subprocess
import sys
import tempfile

HTML_FILE = "index.html"
FAILURES = []


def fail(msg):
    FAILURES.append(msg)
    print(f"  ✗ {msg}")


def ok(msg):
    print(f"  ✓ {msg}")


def check_scripts(html):
    print("[1] JavaScript syntax")
    node = shutil.which("node")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        fail("no inline <script> blocks found")
        return
    if not node:
        print("  ! node not found on runner — skipping JS syntax check")
        return
    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            # Wrap in a function so top-level DOM calls aren't executed, only parsed.
            f.write("function __wrap(){\n" + block + "\n}")
            path = f.name
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"script block {i} fails to parse: "
                 f"{(r.stderr or '').strip().splitlines()[-1] if r.stderr else 'unknown'}")
        else:
            ok(f"script block {i} parses")


def parse_entries(body):
    """Split a JS array body into top-level {...} object strings."""
    return re.findall(r"\{[^{}]*\}", body)


def check_results(html):
    print("[2] Group RESULTS")
    m = re.search(r"const RESULTS = \[(.*?)\n\];", html, re.S)
    if not m:
        fail("RESULTS array not found")
        return
    entries = parse_entries(m.group(1))
    if len(entries) != 72:
        fail(f"RESULTS has {len(entries)} entries (expected 72 — full group stage)")
    else:
        ok("72 group results present")
    bad = 0
    for e in entries:
        a = re.search(r'a:"((?:[^"\\]|\\.)+)"', e)
        b = re.search(r'b:"((?:[^"\\]|\\.)+)"', e)
        ga = re.search(r"ga:\s*(\d+)", e)
        gb = re.search(r"gb:\s*(\d+)", e)
        if not (a and b and ga and gb):
            bad += 1
    if bad:
        fail(f"{bad} group results are malformed (missing team or integer score)")
    else:
        ok("every group result has two teams and integer scores")


def check_ko(html):
    print("[3] KO_RESULTS")
    m = re.search(r"const KO_RESULTS = \[(.*?)\n\];", html, re.S)
    if not m:
        fail("KO_RESULTS array not found")
        return
    entries = parse_entries(m.group(1))
    ok(f"{len(entries)} knockout results present")
    seen = set()
    for e in entries:
        mm = re.search(r"m:\s*(\d+)", e)
        if not mm:
            fail(f"knockout entry with no match number: {e[:60]}…")
            continue
        num = int(mm.group(1))
        if not (73 <= num <= 104):
            fail(f"knockout match number {num} outside 73–104")
        if num in seen:
            fail(f"duplicate knockout match number {num}")
        seen.add(num)
        a = re.search(r'a:"((?:[^"\\]|\\.)+)"', e)
        b = re.search(r'b:"((?:[^"\\]|\\.)+)"', e)
        ga = re.search(r"ga:\s*(\d+)", e)
        gb = re.search(r"gb:\s*(\d+)", e)
        if not (a and b and ga and gb):
            fail(f"knockout M{num} malformed (missing team or integer score)")
            continue
        if ga.group(1) == gb.group(1):  # level → needs a valid shootout winner
            pso = re.search(r'pso:"((?:[^"\\]|\\.)+)"', e)
            if not pso:
                fail(f"knockout M{num} is level with no pso winner")
            elif pso.group(1) not in (a.group(1), b.group(1)):
                fail(f"knockout M{num} pso winner '{pso.group(1)}' is not a participant")
    if not FAILURES:
        ok("match numbers unique and in range; level games carry valid pso")


def check_strength(html):
    print("[4] FIFA_STRENGTH")
    m = re.search(r"const FIFA_STRENGTH=\{(.*?)\};", html, re.S)
    if not m:
        fail("FIFA_STRENGTH block not found")
        return
    pairs = re.findall(r'"((?:[^"\\]|\\.)*?)":\s*(\d+)', m.group(1))
    if len(pairs) != 48:
        fail(f"FIFA_STRENGTH has {len(pairs)} teams (expected 48)")
    else:
        ok("48 teams in FIFA_STRENGTH")
    bad = [(n, int(v)) for n, v in pairs if not (1000 <= int(v) <= 2200)]
    if bad:
        fail(f"ratings out of range 1000–2200: {bad[:3]}")
    else:
        ok("all ratings within 1000–2200")


def check_structure(html):
    print("[5] Structure")
    opens, closes = html.count("<div"), html.count("</div>")
    if opens != closes:
        fail(f"<div> mismatch: {opens} open vs {closes} close")
    else:
        ok(f"<div> balanced ({opens})")
    for name in ("const RESULTS = [", "const KO_RESULTS = [",
                 "const FIFA_STRENGTH={", "const BRACKET_R32"):
        if name in html:
            ok(f"found {name.rstrip('[{= ')}")
        else:
            fail(f"missing {name.rstrip('[{= ')}")


def main():
    try:
        with open(HTML_FILE, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print(f"Cannot read {HTML_FILE}: {e}")
        sys.exit(1)

    check_scripts(html)
    check_results(html)
    check_ko(html)
    check_strength(html)
    check_structure(html)

    if FAILURES:
        print(f"\nVALIDATION FAILED — {len(FAILURES)} problem(s). Not committing.")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
