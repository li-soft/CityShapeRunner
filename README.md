# Paceasso

**GPS Art Wizzard** — routes that turn a run or ride into a drawing on real streets.

[![CI](https://github.com/ak91hu/CityShapeRunner/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/ak91hu/CityShapeRunner/actions/workflows/ci.yml)
[![Technical docs](https://img.shields.io/badge/docs-MkDocs-08705d)](https://ak91hu.github.io/CityShapeRunner/)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 24](https://img.shields.io/badge/Node.js-24-5FA04E?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.140-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=111)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Northflank ready](https://img.shields.io/badge/Northflank-ready-6C5CE7)](docs/operations/deployment.md#northflank-developer-sandbox)
[![Grafana Cloud Logs](https://img.shields.io/badge/Grafana_Cloud-logs-F46800?logo=grafana&logoColor=white)](docs/operations/deployment.md#persistent-and-searchable-grafana-cloud-logs)

Turn a run or ride into a drawing. Describe an idea—or choose from 158 catalog
options—and Paceasso tests the outline against real streets, compares
nearby placements and orientations, and shows how recognisable the resulting
route is. Only a route returned by the connected street router can reach the
download flow; a road-routed result that misses a quality target requires
explicit review.

## This fork: SVG upload, walking and keyless routing

> **Licence note:** the upstream repository
> ([ak91hu/CityShapeRunner](https://github.com/ak91hu/CityShapeRunner)) publishes
> no licence file, so its code is not openly licensed even though
> `pyproject.toml` says `MIT`. Check with the upstream author before you
> redistribute or deploy this fork.

This fork adds four things to the planner:

- **Upload your own SVG shape.** Open *Other ways to start → Use your own SVG
  or an image link*, then drop an `.svg` file or choose one. The server checks
  the file and shows a preview. Then pick a city, an activity and a length, and
  generate the route. Paths, polygons, polylines, basic shapes and multi-part
  paths are supported, up to 2 MB. One closed outline (a heart, a country
  border) gives the best result, because separate strokes are joined with
  connecting legs. The uploaded outline goes through the same normalisation,
  placement search, street routing, quality gates and GPX/TCX export as a
  built-in template. Your start point (current location or an address) still
  applies.
- **Walking** as a third activity next to running and cycling (2–40 km,
  default 5 km). It routes on the pedestrian profile (`foot-walking` on ORS,
  `foot` on OSRM).
- **Written text returns to the start.** Text such as `write LOVE` is drawn
  as one loop: after the last letter the route runs back under the text, like
  an underline, and finishes where it began. Letter corners are all kept as
  routing guides (up to the provider's 50-coordinate limit).
- **Keyless street routing.** With no `ORS_API_KEY` and no self-hosted ORS,
  the planner routes through the public FOSSGIS OSRM server
  (`routing.openstreetmap.de`, separate foot and bike graphs). OpenRouteService
  is still used whenever `ORS_API_KEY` is set or `ORS_BASE_URL` points at your
  own ORS instance.

### Run it locally (Windows PowerShell)

You need Python 3.12 or newer and Node.js 22 or newer.

```powershell
# Backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env      # leave ORS_API_KEY empty for keyless mode
paceasso                         # API on http://127.0.0.1:8000 (/docs, /health)

# Frontend (second terminal)
cd frontend
npm ci --no-audit --no-fund
npm run dev                      # UI on http://127.0.0.1:5173
```

Smoke-check the API without the UI:

```powershell
# Preview an uploaded SVG (raw body, not multipart)
curl.exe -X POST "http://127.0.0.1:8000/shape-upload?filename=heart.svg" `
  -H "Content-Type: image/svg+xml" --data-binary "@heart.svg"

# Generate a walking route from the same SVG (the JSON field holds the SVG text)
python -c "import json,pathlib as p; p.Path('body.json').write_text(json.dumps({'prompt': 'a custom image in Budapest, walking, about 5 km', 'reference_svg': p.Path('heart.svg').read_text(), 'reference_svg_name': 'heart.svg'}))"
curl.exe -X POST http://127.0.0.1:8000/generate -H "Content-Type: application/json" --data-binary "@body.json"
```

`/shape-upload` returns the normalised outline (`paths` in unit space, y up).
It answers 413 for files over 2 MB, 415 for multipart or non-SVG content types,
and 422 for empty, malformed or shape-less SVGs (also for SVGs with a DOCTYPE
or entities). `/generate` accepts the same SVG text as `reference_svg`. You
cannot send it together with `reference_image_url`.

### Routing settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `ROUTING_BACKEND` | `auto` | `auto` uses ORS when it is configured and OSRM otherwise. `ors` and `osrm` force one provider. |
| `ORS_API_KEY` / `ORS_BASE_URL` | empty / HeiGIT | A key or a non-public ORS URL turns ORS on. |
| `OSRM_BASE_URL` | `https://routing.openstreetmap.de/routed-{profile}` | Any OSRM server. `{profile}` becomes `foot` or `bike`. |
| `OSRM_MIN_INTERVAL_S` | `1.0` | Minimum gap between OSRM requests across the process. |

Fair use on public OSRM: requests are throttled to one per second, and
identical requests inside one generation come from the existing directions
cache. The batched ORS snap preflight is skipped, because it would take one
OSRM request per point. A keyless generation therefore makes a few dozen
Directions calls and can take a minute. OSRM returns no surface or steepness
data, so route-readiness details show as unavailable. When OSRM reports that
guides cannot be connected (a guide snapped to an isolated path inside the
search radius), the request is retried once without the radius, so OSRM picks
the connected street network instead of dropping guides. Use your own OSRM or ORS
server for heavier use. Tests and `GEOCODE_OFFLINE=1` never contact public
routers.

The visual [engineering implementation guide](docs/architecture/implementation/index.md)
documents the backend sequence, data classes, placement-search funnel, ORS
retry state machine, quality formulas, React/Leaflet state flow, and production
topology with Mermaid diagrams.

The [documentation index](docs/index.md) organizes the maintained guides,
architecture, references, development workflow, operations, design, research,
dated reviews and runtime skills. New documentation belongs in those sections.

The [algorithmic background](docs/architecture/algorithmic-background.md) follows one request
from shape normalisation through geographic placement, the bounded preflight
search, street routing, and independent quality gates. It gives the implemented
distance and closure formulas, explains why a snapped-point proxy cannot prove
connectivity, and records which evidence is required before GPX/TCX export.

The [external API integration guide](docs/reference/external-apis.md) maps ORS,
OpenStreetMap/Nominatim, the model-provider adapters, Cloudinary, Open-Meteo,
and public image imports with payloads, credentials, retries, policies, and
failure boundaries.

The free-text planner sends the complete request directly to generation, where
the intent pipeline resolves the drawing, place, activity, and distance. In a
drawing request, `bug` always selects the insect template; a B route requires
an explicit `letter B` request. Optional controls can anchor the route at a
current location or address, set its first direction, avoid steps/ferries/fords, and prefer quieter
or greener walking streets. Result alternatives appear together as comparison
cards; route-readiness issues can be selected to zoom the map to their exact
preview segments.

The idea field starts with a random complete catalog example on each page load.
When session storage is available, the previous example in that tab is excluded;
editing the request or switching views does not randomize it again.

The project combines 145 deterministic shape templates, a complete A–Z/0–9
vector font, local profiles for 50 major Hungarian cities, all 45 official Lake
Balaton shore municipalities, and 136 other European cities, optional LLM planning,
OpenRouteService street routing, quantitative shape validation, and guarded
GPX/TCX export. Every fully routed result is retained as a selectable candidate.
Quality thresholds rank and warn instead of deleting a fully routed result, so
lower-scoring street routes remain available for comparison, manual correction,
and reviewed export. A straight-line diagnostic is never exposed as a
selectable or downloadable GPS route.

The structured planner contains 230 unique destinations: Siófok belongs to both
the Hungarian top-50 and official Balaton coverage, but appears only once in the
picker. Every Balaton destination resolves locally and has its own placement
profile for shoreline, terrain, rail, road, and wetland constraints. See
[Lake Balaton coverage](docs/guides/balaton-city-coverage.md) for the source, complete
list, and recommendation policy.

Sixteen additional one-stroke motifs—including paprika, puzzle cube, grey
cattle, water tower, thermal bath, and folk gate—are paired with practical
Hungarian-city starting points in the searchable catalog. Their research and
geometry audit are documented in
[Hungary-friendly GPS-art shapes](docs/reference/hungarian-shape-catalog.md).

Free-form prompts are not limited to the picker. Common phrases such as
`draw a heart in Lyon, 8 km` can recover an unlisted settlement locally, then
Nominatim is restricted to inhabited-place results and its administrative bbox
is reduced to a bounded urban search area. Invalid coordinates fall back
explicitly instead of entering placement math.

Free-form drawings are not limited to the 158-option catalog either. A named
custom idea is preserved locally, converted into a structured semantic brief
and one to four bounded route-native candidates, and checked for
degenerate proportions, transfer lines, duplicates, and self-intersections.
Compound requests reuse a related catalog contour as an anatomy/proportion
anchor without copying it unchanged. Executable geometry and cue review rank
the valid candidates; invalid or semantically weak candidates trigger at most
one bounded repair.
The zero-cost production profile requests one model candidate and adds a
deterministic semantic scaffold locally. Only successful
generated shapes enter the 128-entry cache. If no model is available, the
result uses an explicit full-word text fallback instead of reducing the request
to its initial or relabelling a
stock icon as the requested object. See the
[custom-shape research and decision record](docs/architecture/custom-shape-generation.md).

Route search is coarse-to-fine. Before spending Directions requests, up to three
batched road-snap rounds preserve the established 180-placement search and
add up to 90 seeded creative placements, refining each family independently.
They share the same three Snap round trips. The established family's entire
shortlist remains available even when creative proxy scores are higher.
A quality-and-diversity selector keeps seven established finalists and up to
two additional creative alternatives for the full router, while every
preflight score remains in the diagnostics. Eighteen curvature-preserving
guide points per placement improve the proxy without adding Directions calls.
If Directions rejects the top-ranked placement, the orchestrator tries the
remaining road-fit shortlist before giving up. If none produces a connected
street polyline, `POST /generate` fails closed with HTTP 503 instead of placing
the original drawing over buildings, water, or other unroutable areas.

Successful graph proposals compete with the original-guide route under the
same measured quality checks. A final bounded neighbourhood search improves
the best routed placement (`SHAPE_POLISH_CANDIDATES=4`, zero disables it).
If direct contour reconnection still leaves the turning gate below target, one
last bounded local-graph pass combines those windows with separated high-error
turning phases and can replace only one weak street section; Directions must
reroute and validate the complete line before it can win.
Within usable distance and closure, selection prioritises shape fidelity.
Every routed vertex is preserved in validation, the map and downloads; no
corner-cutting simplification or 500-point street preview sampling is applied.
Unrouted previews cannot be exported, including through the offline pipeline.

Recognition is evaluated from outline coverage, characteristic turns,
salient tips and notches, unintended U-turns, street-detour stretch, and
preserved proportions—not only average point distance. The map overlays the
intended dashed contour on the routed line. If
an explicitly requested drawing misses a recommended target, the planner
measures simpler city-aware templates and recommends the strongest result
without removing the original.

Generation remains a quality-preserving search, so complex shapes can take
time. The web app receives live workflow events and shows the actual stage,
screened-placement count, full Directions-request count, elapsed time, and
rotating route facts. Final polish names its active check and shows that check's
elapsed time and routing requests. Once ORS supplies a connected candidate, an early map
appears as a provisional preview; it has no download action until final checks
finish. Reduced-motion preferences disable nonessential animation. API clients
can request the same progress via `Accept: application/x-ndjson`; ordinary JSON
responses remain available. Final workflow metrics expose the duration and ORS
call count of each polish pass, and unchanged geometry skips redundant late
reconnection attempts without relaxing the route-quality gates.

Repeated requests add fresh, reproducible placement samples across sixteen
city sectors, twelve orientations and three scales. They supplement the
established city grid rather than replacing its good placements.
`PREFLIGHT_CREATIVE_PLACEMENTS=90` controls this additional screening budget
(zero retains the established search alone). Manual map positions, fixed starts and requested headings
retain their constraints. The first three shortlisted placements are measured
together through the bounded worker pool, including when the first route already
passes. This supplies real area alternatives rather than only local repairs.
Comparison cards keep the best recommendation first and prefer comparable routes
with different measured map coverage; every routed alternative remains available.
The strongest region can still win again when the street geometry favors it.
Measuring alternatives can add Directions calls when the primary already passes;
the worker limit bounds concurrency, rather than promising lower provider usage.

`INITIAL_PLACEMENT_CANDIDATES=3` controls that initial comparison (hard-capped at
three); `MEASUREMENT_WORKERS` controls concurrency. `OPTIMIZATION_BUDGET_SECONDS=35`
stops starting extra initial area comparisons and advisory AI review once the budget is reached.
Adaptive preflight, disconnected-route recovery, failing-shape alternatives,
recognition and distance repairs, and connectivity retries retain the original
`WORKFLOW_MAX_DURATION_SECONDS=175` budget. A weak first route does not lose its
repair time to the shorter exploration budget. Unmeasured queued placements
remain available in their original priority order for quality refinement.
Unstarted creative alternatives retain their extra measurement slots instead
of consuming the established finalists' attempts, within the same full deadline.
Mandatory validation still finishes;
an in-flight provider request can exceed either budget, so neither is an HTTP
timeout. Routing/export quality gates and the ban on exporting unconnected guides remain.
The established finalist budget is preserved; up to two additional creative
initial alternatives cannot evict those finalists. Without an extra creative
family, initial alternatives count against the serial placement-search budget
to avoid extra blind refinement iterations.

Directions and Snap share a bounded HTTP connection pool inside one workflow,
closed on both success and failure. Direction/phase variants in the Fréchet
metric are evaluated together using the same dynamic program, sample counts,
and exact float results. This removes repeated computation without lowering
recognition thresholds or changing the returned street geometry.
The local OSM graph caches four exact projection frames and reuses segment
edge-cost calculations across beam states. Turn restrictions, previous-edge
reversal costs and reuse penalties remain state-dependent. Connectivity
screening uses a fixed candidate/expansion bound, avoiding hardware-dependent
partial ranking from a short wall-clock cutoff.

The NDJSON progress feed also reports measured search areas and unique connected
candidates. Request counters update before each provider call, including retries.
The waiting screen provides a twelve-sketch guessing challenge and Shape Sprint,
an optional jumping runner with twelve selectable shapes, three obstacle types,
scoring, pause and restart. It supports keyboard and touch, pauses when closed
or backgrounded, and stops when generation finishes. The screen explains quiet
periods without inventing percentage progress and keeps Cancel reachable on
mobile. Games never submit another generation request.

For reproducible placement experiments, `/generate` accepts a 32-bit integer
`search_seed` and returns the chosen seed. It reproduces sampling, not live road
data or model output. The paired benchmark runner fixes the seed per case and
marks budget-limited searches incomplete. To isolate scheduling without any
provider call, run `python scripts/benchmark_search_scheduling.py`; its synthetic
latency results do not predict complete production response times.

## GPS Art Intelligence

The result view now turns route generation into a repeatable local craft. Street
Canvas marks the strongest nearby placement areas from the preflight search;
Recognition Repair re-routes a drawing through its highest-information visual
anchors; and Time-aware Readiness combines daylight with an optional hourly
weather check. Personal mode keeps individual route planning and multi-session
completion together. Group mode assigns named participants different distances,
optionally counts shared-start travel in their limits, and saves a local event
file for later completion. Uploaded recordings measure covered ink; the combined
download is explicitly a street reconstruction, with separate missing-ink missions.
Inkproof runs correlated
GPS-drift simulations before departure and highlights details likely to blur.
Every download-producing Intelligence action re-routes its submitted geometry
through activity-specific Directions first. Recognition Repair, Community GPS
Mural and Art Rescue return HTTP 503 and no file if ORS cannot prove one complete
road/path route; raw uploads, independent nearest-edge snaps and straight
connectors are never accepted as export evidence. The remaining analyses need no
paid map call or account. The privacy model and endpoint contracts are in
[GPS Art Intelligence](docs/guides/gps-art-intelligence.md).

Every new generation workflow requests fresh ORS Directions evidence. Identical
route attempts may reuse a response only inside that one workflow; a later user
request never inherits an earlier workflow's road result.

## Research basis

The [September generation improvements](docs/reports/generation-development.md) add
adaptive placement, bounded shape-aware OSM graph proposals, early connectivity
and detour checks, semantic feature preservation and repair, cached final-route
AI review, and a 200-case comparison runner. Graph proposals still pass through
Directions before becoming exportable street routes. AI opinions remain advisory.

The pipeline is an engineering adaptation of published GPS-art, computational
geometry, route-choice, and map-matching research. It does not claim to
reproduce every paper's custom graph algorithm; instead, it maps the findings
to operations available through the hosted OpenRouteService API.

| Research finding | Consequence in Paceasso |
|---|---|
| Ordinary waypoint routers can turn off-network drawing points into large detours or visually destructive U-turns; GPS art benefits from a shape-aware graph cost ([Waschk & Krüger, 2019](https://doi.org/10.1007/s41095-019-0146-z)). | Placements are screened before Directions routing, curvature-bearing guide points are preserved, and a separate gate rejects doubled-back strokes absent from the drawing. |
| Template placement, graph search, candidate comparison, and interactive adjustment are complementary stages rather than one routing call ([Powałka, 2023](https://repository.tudelft.nl/record/uuid%3A11e9b0c2-5d67-475a-8653-71c7afe03dad)). | A city-wide transform search produces several routed alternatives, while the browser editor lets the user correct control points and request a fresh route. |
| Turning functions compare polygonal shape in a way that can be normalised for translation, rotation, and scale ([Arkin et al., 1991](https://doi.org/10.1109/34.75509)). | Characteristic turns and their order contribute to recognition; mean point distance is never the sole likeness measure. |
| Turning angles, approximate road polylines, and internal length ratios help retrieve recognisable graphics and reject stretched lookalikes in road networks ([Li & Fu, 2026](https://doi.org/10.3390/ijgi15030098)). | Preflight and final validation score angular relations, extent, segment proportions, coverage, and collapse instead of accepting nearest-road distance alone. |
| European street networks vary substantially in orientation order, connectivity, segment length, and circuity ([Boeing, 2019](https://doi.org/10.1007/s41109-019-0189-1)). | Every catalogued city has a bounded local search area and obstacle-aware context; the transform search measures several positions and bearings rather than labelling an entire city “grid-like”. |
| Walkable and drivable networks can differ materially in circuity even within one city ([Boeing, 2017](https://arxiv.org/abs/1708.00836)). | Shape detail capacity is calculated separately for running and cycling and is combined with requested distance rather than treating activity as a display label. |
| Useful alternative sets must control overlap, or a top-*k* list can contain near-duplicates ([Nassir et al., 2014](https://doi.org/10.3141/2430-18)). | Seven established finalists remain available; up to two additional creative finalists balance road-fit evidence with separation in position, rotation, and scale. |
| Map matching requires plausible transitions and sequence continuity, not only independent nearest points ([Newson & Krumm, 2009](https://doi.org/10.1145/1653771.1653818); [Bang et al., 2016](https://doi.org/10.3390/s16101768)). | Batched snapping is treated only as a cheap proxy. Guides are submitted to activity-specific Directions routing and whole-curve validation before they are labelled road-routed; failed placements trigger the remaining shortlist, and an exhausted search returns no GPS export. |

These studies justify the architecture and metrics, but they do not prove that
a generated route is recognisable, safe, legal, or optimal in every city.
Thresholds remain engineering heuristics and should be calibrated with
labelled human-recognition tests. See the
[research notes](docs/research/gps-art-research.md) for the full evidence-to-algorithm
mapping and production funnel. The latest method-by-method review and the
implemented geometry changes are recorded in the
[August 2026 algorithm audit](docs/reports/gps-art-algorithm-audit-2026-08.md).

## Understanding and editing results

The candidate comparison cards keep every fully routed result for the final
chosen shape visible together. Routes that pass every automatic check appear first; a larger average
score cannot outrank a route that passes all of the independent recognition,
road, distance, and closure checks. Attempts for a different suggested shape
remain in the audit summary instead of being mixed into that selector.

“Automatic checks passed” means only that the route met the application's
documented engineering thresholds. It is not scientific proof of
recognisability, legality, accessibility, or safety. A route that misses a
target remains selectable and exportable after the user reviews and explicitly
accepts that exact geometry, but only when Directions returned a connected
street route. A straight-line diagnostic returned internally without road
routing never enters the API candidate selector, and the public response never
contains downloadable GPX/TCX data for it.

This invariant applies to every public file-producing workflow, not only
`/generate`: Recognition Repair, Community GPS Mural and Art Rescue first pass
their complete geometry through the same activity-specific Directions authority.
A malformed, zero-length, partially skipped or otherwise unproven provider
response produces HTTP 503 and no GPX/TCX. There is no public straight-line
fallback.

The built-in Leaflet editor exposes numbered draggable control points for every
candidate. After a correction, `/edit-route` routes the guide through the
activity-specific street graph again, recalculates quality and distance, and
returns a new GPX/TCX only after successful street routing. If routing is
unavailable or the edited points cannot be connected, `/edit-route` returns
HTTP 503 and creates no GPS file. Moving a point marks the editor as having
pending changes and disables downloads until the user updates the street route
or discards those changes; closing an unchanged editor does not imply that
anything was discarded.

The complete map can also be rotated manually from −180° to 180°, in 1° slider
steps or 15° buttons, with a one-click north-up reset. Tiles, intended outline,
street route, and markers share the same bearing. Gallery capture uses the same
geographic tile transform, so a published image keeps the orientation chosen
in the result viewer.

Structured JSON logs are written to the console and, by default, to the
rotating `logs/paceasso.log`. Every HTTP request receives an
`X-Request-ID`, allowing UI reports, API failures, candidate measurements, and
provider errors to be correlated without logging API keys or the prompt text.

An optional anonymous gallery captures the already-rendered Leaflet map as a
PNG, retaining street names, the route overlay, and visible OpenStreetMap
attribution. Signed server-side Cloudinary uploads and Cloudinary asset search
provide storage and listing without a gallery database. Publishing is opt-in
because the exact mapped location becomes public; gallery uploads never include
the prompt, request ID, GPX/TCX document, or user profile. Configure the
server-only `CLOUDINARY_URL` to enable it. The browser stores only the matching
removal capability for images published from that browser; failure to retain
that local token does not turn a successful upload into a failed publication.
The homepage features a public gallery thumbnail and, when multiple images
exist, chooses a different one on the next page load. It does not invent a
city or drawing name for gallery images that carry no such metadata.
On Northflank, the production image writes only to the captured console stream.
The platform's native Loki log sink forwards that stream to Grafana Cloud,
where entries can be searched by request ID, event, severity, environment, or
release revision without placing Grafana credentials in the application.

Start with the [complete project guide](docs/guides/project-overview.md). Production operators
should also read the [deployment guide](docs/operations/deployment.md). The dated
[2026-07-30 development and production lessons](docs/reports/2026-07-30-lessons-learned.md)
record the first Northflank incidents, measured performance fix, Grafana
queries, and the operational troubleshooting checklist.

## Quick ideas

The web app draws six shuffled starters from the catalog beside the prompt.
“Show different ideas” refreshes them while preserving the prompt and keyboard focus.
It places a searchable
158-option catalog behind “More shapes, letters, and numbers”. It contains 145
deterministic route templates plus 13 letter, number, and short-text presets:

- **Hungarian ideas (16):** paprika, puzzle cube, moustache, grape cluster,
  wine glass, cauldron, horseshoe, wheat, suspension bridge, water tower,
  grey cattle, stag, pomegranate, chimney cake, thermal bath, and folk gate
- **Simple shapes (19):** heart, star, circle, diamond, triangle, square,
  infinity, arrow, cross, lightning, wave, moon, hexagon, octagon, teardrop,
  shield, clover, spiral, and hourglass
- **Nature (23):** the original 16 plus acorn, banana, broccoli, feather,
  ice cream, volcano, and watermelon slice
- **Animals (33):** the original 20 (including the insect-aware bug template)
  plus ant, crab, dinosaur, frog, hedgehog,
  koala, octopus, paw print, seahorse, snake, spider, squid, and swan
- **Objects (44):** the original 14 plus 30 route-authored everyday objects,
  including robot, lighthouse, camera, paper plane, train, and windmill
- **Symbols (10):** crown, skull, DNA helix, speech bubble, location pin,
  chess pawn, compass, ghost, lock, and medal
- **Letters, numbers, and text:** A, C, L, M, N, S, U, V, Z, 2, 7, 42,
  and GPS

The complex templates are single continuous silhouettes wherever possible,
preserve recognisable high-curvature landmarks, and use longer cycling starts
when their detail needs more road-network resolution. They are starting points,
not guarantees: the result still depends on local connectivity, access rules,
and route-provider coverage. Cat, dog, bird, and bat now have separate
route-readable outlines and distinct catalog markers. A rotation-, scale-,
start-, and direction-independent audit checks every pair in the 145-template
registry; see the [shape-template research and uniqueness guard](docs/skills/shape-template-uniqueness.md).

The structured city picker follows the [KSH 2025 list of Hungary's 50 largest
settlements](https://www.ksh.hu/stadat_files/fol/en/fol0014.html). Each resolves locally without a public-geocoder call and has a
bounded urban search area; obstacle descriptions guide the first placement,
while measured preflight and Directions routing remain authoritative.

The Europe group now contains 136 regionally balanced cities guided by the
[Eurostat city-statistics framework](https://ec.europa.eu/eurostat/web/cities/methodology).
The original 30 are joined by 106 cities across western, northern, central,
eastern, and south-eastern Europe. Every entry resolves offline to a compact
urban search box and one of the documented grid, radial, river, coast, lake,
hill, or mountain recommendation profiles. These are planning priors, not a
claim that every neighbourhood is suitable for every drawing.

Smart suggestions no longer use a fixed city mascot. The planner measures all
145 route templates, combines their continuity, turning complexity, directional
order, aspect ratio, and routeability with the selected city's street context,
activity, and distance, then sends three diverse continuous shapes through the
real placement and routing checks. The result explains why the winning shape
was shortlisted. See the [city–shape recommendation audit](docs/guides/city-shape-recommendations.md).

```bash
python -m venv .venv
. .venv/bin/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,all]"
python -m uvicorn gps_art_wizzard.main:app --reload
```

The web client is then available after its Vite development server is started
from `frontend/`, or directly from FastAPI after `frontend/dist` has been built.

## Testing

Development follows the project-local [Ponytail method](docs/development/workflow.md): reuse
existing code, make small complete changes, run focused checks during iteration,
and retain the full release gate. Pinned development/review/audit/debt skills
are included in `.agents/skills/`; [AGENTS.md](AGENTS.md) records the GPS accuracy
and accessibility invariants.

The backend suite is deterministic and runs without paid providers. The UI
suite uses Playwright with mocked API, map-tile, and gallery responses, and runs
every functional scenario in desktop/mobile Chromium, Firefox, and WebKit.

```bash
GEOCODE_OFFLINE=1 python -m pytest -q
python -m ruff check .
python -m mypy --ignore-missing-imports gps_art_wizzard

cd frontend
npm ci
npm run check
npm run test:smoke
npm run test:e2e
npm run test:cross-browser
```

On Windows PowerShell, use `$env:GEOCODE_OFFLINE = "1"` before pytest. Install
the browsers once with `cd frontend && npx playwright install chromium firefox webkit`. See the
[testing guide](docs/development/testing.md) for targeted commands, suite ownership,
Playwright debugging, and the Windows temporary-directory workaround.

The second command exercises the required desktop Firefox and WebKit (Safari's
browser engine) compatibility leg. To run only that leg locally:

```bash
cd frontend
npx playwright install firefox webkit
npm run test:cross-browser
```

## Hosting

The searchable [MkDocs technical documentation](https://ak91hu.github.io/CityShapeRunner/)
is built with warnings treated as errors on every CI run and deployed to GitHub
Pages only after backend quality/tests, the production frontend, all five
browser profiles, the production container smoke, and documentation jobs pass
on `master`. For local authoring, install `.[docs]` and run
`python -m mkdocs serve`; the [CI/CD guide](docs/operations/ci-cd.md) documents the release
gate and one-time Pages configuration.

The recommended hobby deployment is the
[Northflank Developer Sandbox with Grafana Cloud Logs](docs/operations/deployment.md#northflank-developer-sandbox).
A Northflank combined service builds the repository's multi-stage Dockerfile,
serves the SPA and API from one HTTPS `*.code.run` endpoint, and accepts only
the exact tested `master` SHA from the GitHub deployment job. Direct Northflank
CI is disabled so a push cannot bypass the quality gate; the job waits for the
Northflank build, rollout, and public health check. The free Sandbox is
always-on but resource-limited and has no production SLA; the deployment guide
records the exact service, port, health-check, environment, and Loki-sink
settings.

The production image embeds a pinned OpenCode CLI and configures a zero-cost,
text-only model in `essential` mode. The 256 MB Northflank Sandbox profile keeps
`OPENCODE_SERVER_AUTOSTART=false`: a resident OpenCode child does not fit beside
the web process reliably, so production uses the deterministic shape pipeline
and remains responsive. Larger deployments can opt in to the free model by
enabling autostart. A failed opt-in child is terminated before the API starts,
so it cannot leak CPU or memory into the web service.

> Generated routes are planning candidates, not safety guarantees. Review every
> route against current access rules, crossings, closures, terrain, and local
> conditions before using it.

The historic “wizzard” spelling is retained in distribution, import, and
console-script identifiers for compatibility; public-facing copy uses the
conventional English spelling “wizard”.
