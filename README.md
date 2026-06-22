**World Cup 2026 — Logistics, Emissions & Predictions**

A self-contained, single-file HTML dashboard tracking the 2026 FIFA World Cup through the lens of travel logistics and carbon emissions. The first World Cup held across three countries means teams crisscross a continent rather than staying in one city — this tool follows what that actually costs, and who it favours.
Built with D3.js and TopoJSON, the entire application lives in one HTML file with no build step, no backend, and no API keys. Scores are updated manually as matches are played.

**Features**

Flight Map & Teams — every team's flight routes, distances, and jet-lag burden across North America, with an interactive map and sortable team table.

Host Cities & Emissions — the carbon cost of all that flying, broken down by host city, with altitude/elevation exposure per team.

Group Travel Fairness — how evenly travel demands are distributed within each group.

Standings — live group tables implementing the full FIFA 2026 ranking criteria, including the new head-to-head-before-goal-difference tiebreaker order and the fair-play disciplinary tiebreaker.

Fixtures — all 72 group matches with kick-off times, live-match indicators, and results.

Knockout Bracket — a symmetric Round-of-32 wall-chart with a mobile fit-to-screen view, plus live qualification and best-third tracking.

Qatar 2022 Comparison — a to-scale comparison of travel and emissions against the single-city Qatar tournament, with a real geographic map of the Gulf region and animated team bus routes.

Predictions — Monte Carlo qualification probabilities and title odds from an Elo-based simulation that re-runs in the browser as results come in.

**Methodology**

Emissions use UK DESNZ 2025 factors with radiative forcing (0.14253 kgCO₂e/pax-km for flights, 0.02776 for coach travel), assuming a 45-person travelling party. Qualification probabilities come from 4,000 simulations per group. All figures are modelled estimates, not predictions of any single outcome.

**Usage**

Open the HTML file in any modern browser. No installation, server, or dependencies beyond the CDN-loaded D3/TopoJSON libraries (cached locally after first load).

**Tech**
Single-file HTML · JavaScript · D3.js · TopoJSON · responsive (desktop & mobile) · light/dark theme
