"""OpenRouteService client: snap-to-road + routing for a drawn waypoint list.

Given an ordered list of [lat, lon] waypoints drawn on the map, this asks ORS
to route through them along the real road network, returning a single
connected, road-following candidate. Without a hosted-service API key (or a configured
self-hosted service) it degrades to a great-circle preview (``snapped=False``)
so the pipeline stays exercisable without claiming road feasibility.
"""

from __future__ import annotations

import copy
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
from shapely.geometry import LineString

from ..config import get_settings
from ..state import RouteConcern, RoutePreferences, RouteReadiness, RouteSurface
from ..workflow_runtime import (
    active_workflow_runtime,
    record_routing_failure,
    record_routing_request,
)
from . import geo, osrm_client, shape_similarity
from .routing_backend import (
    DirectionsBackend,
    RoutedPath,
    RouteFailure,
    is_public_ors,
    select_routing_backend,
)

log = logging.getLogger(__name__)

LatLon = tuple[float, float]

# The hosted ORS Directions API accepts at most 50 coordinates per request.
# A closed route's repeated start/end point counts toward that limit.
_MAX_ORS_COORDINATES = 50
_MAX_GUIDE_COORDINATES = 24
_MAX_SNAP_LOCATIONS = 5_000
_RADIUS_RETRIES = [80, 120, 200, 350]  # bounded shape-preserving search radii (m)
_MAX_ORS_ATTEMPTS = 7
_ACCEPTABLE_FIDELITY = 0.70
# Bound gaps between visual guides while protecting authored corners. Ideal
# drawings use 24 guides; street-graph proposals can use the full 50-point limit.
_GUIDANCE_SPACING_M = 400.0
_CORNER_TURN_DEG = 14.0
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_CONNECTIVITY_ERROR_CODES = {2009, 2013, 2014, 2015, 2016}
_FAILED_PAIR_PATTERN = re.compile(
    r"between\s+points?\s+(\d+).*?\band\s+(\d+)",
    flags=re.IGNORECASE,
)

# Memoised Directions responses. Refinement and recovery re-route nearly
# identical guide geometry; serving the identical request from cache removes
# duplicate paid calls without altering any returned coordinate.
_DIRECTIONS_CACHE_MAX = 256
_DIRECTIONS_CACHE_TTL_S = 300.0
_directions_cache: OrderedDict[tuple, tuple[float, list[LatLon], float, bool, RouteReadiness]] = (
    OrderedDict()
)
_directions_cache_lock = threading.Lock()


@contextmanager
def _routing_client():
    """Reuse this workflow's HTTP pool; standalone calls own their client."""
    runtime = active_workflow_runtime()
    if runtime is not None and callable(getattr(runtime, "routing_client", None)):
        yield runtime.routing_client()
    else:
        with httpx.Client() as client:
            yield client


def clear_directions_cache() -> None:
    """Drop every memoised Directions response (used by tests and admin tooling)."""

    with _directions_cache_lock:
        _directions_cache.clear()


def _route_preferences_key(
    preferences: RoutePreferences | None,
) -> tuple[tuple[str, object], ...]:
    prefs = preferences or RoutePreferences()
    return tuple(sorted(vars(prefs).items()))


def _directions_cache_key(
    prepared: list[LatLon],
    *,
    profile: str,
    closed: bool,
    start_radius: int,
    route_preferences: RoutePreferences | None,
    start_direction_deg: float | None = None,
    guide_budget: int = _MAX_GUIDE_COORDINATES,
    workflow_scope: str | None = None,
    endpoint: str | None = None,
) -> tuple:
    cfg = get_settings().routing
    return (
        tuple((lat, lon) for lat, lon in prepared),
        profile,
        bool(closed),
        (endpoint or cfg.ors_base_url).rstrip("/"),
        cfg.preference,
        bool(cfg.continue_straight),
        int(start_radius),
        _route_preferences_key(route_preferences),
        None if start_direction_deg is None else start_direction_deg % 360.0,
        guide_budget,
        workflow_scope,
    )


def _directions_cache_get(key: tuple):
    now = time.monotonic()
    with _directions_cache_lock:
        entry = _directions_cache.get(key)
        if entry is None:
            return None
        stored_at, polyline, distance, snapped, readiness = entry
        if now - stored_at > _DIRECTIONS_CACHE_TTL_S:
            _directions_cache.pop(key, None)
            return None
        _directions_cache.move_to_end(key)
    # Shallow-copy the mutable containers so callers can never mutate the
    # cached geometry; individual points are immutable tuples.
    return list(polyline), distance, snapped, copy.deepcopy(readiness)


def _directions_cache_put(
    key: tuple,
    polyline: list[LatLon],
    distance: float,
    snapped: bool,
    readiness: RouteReadiness,
) -> None:
    with _directions_cache_lock:
        _directions_cache[key] = (
            time.monotonic(),
            list(polyline),
            distance,
            snapped,
            copy.deepcopy(readiness),
        )
        _directions_cache.move_to_end(key)
        while len(_directions_cache) > _DIRECTIONS_CACHE_MAX:
            _directions_cache.popitem(last=False)

_PROFILE_MAP = {
    "run": "foot-walking",
    "walk": "foot-walking",
    "run-walk": "foot-walking",
    "bike": "cycling-regular",
    "bike-road": "cycling-road",
    "bike-mtb": "cycling-mountain",
}

_SURFACE_LABELS = {
    0: "Unknown",
    1: "Paved",
    2: "Unpaved",
    3: "Asphalt",
    4: "Concrete",
    6: "Metal",
    7: "Wood",
    8: "Compacted gravel",
    10: "Gravel",
    11: "Dirt",
    12: "Ground or mud",
    13: "Ice or snow",
    14: "Paving stones",
    15: "Sand",
    17: "Grass",
    18: "Grass paver",
}
_PAVED_SURFACES = {1, 3, 4, 6, 7, 14, 18}
_UNPAVED_SURFACES = {2, 8, 10, 11, 12, 13, 15, 17}
_STEEPNESS_LOWER_BOUNDS = {0: 0.0, 1: 1.0, 2: 4.0, 3: 7.0, 4: 10.0, 5: 16.0}
_MAX_CONCERN_SEGMENTS = 6
_MAX_CONCERN_POINTS = 36


# Historic names: the shared retry loop and its tests predate other providers.
_ORSFailure = RouteFailure
_ORSRouteResult = RoutedPath


@dataclass(frozen=True)
class SnapPreflightResult:
    """Cheap road-fit evidence for one transformed guide shape."""

    candidate_index: int
    score: float
    snap_coverage: float
    mean_snap_distance_m: float
    shape_fidelity: float
    turning_similarity: float
    length_similarity: float
    route_length_ratio: float
    landmark_similarity: float = 0.0
    network_connectivity: float | None = None
    network_detour_ratio: float | None = None


def profile_for(sport: str) -> str:
    return _PROFILE_MAP.get(sport, "foot-walking")


_is_public_ors = is_public_ors


def _snap_request(
    url: str,
    headers: dict,
    locations: list[list[float]],
    *,
    radius: int,
    client: httpx.Client | None = None,
) -> list[object] | None:
    """Snap a batch of locations without calculating routes between them."""
    runtime = active_workflow_runtime()
    if runtime is not None and runtime.routing_rate_limited("snap"):
        return None
    try:
        sender = client if client is not None else httpx
        record_routing_request("snap")
        response = sender.post(
            url,
            json={"locations": locations, "radius": radius},
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            record_routing_failure("snap", f"http_{response.status_code}")
            log.warning(
                "ORS snap preflight returned HTTP %d: %s",
                response.status_code,
                response.text[:240],
            )
            return None
        payload = response.json()
        snapped = payload.get("locations") if isinstance(payload, dict) else None
        if not isinstance(snapped, list) or len(snapped) != len(locations):
            record_routing_failure("snap", "invalid_response")
            log.warning("ORS snap preflight returned an incomplete location list")
            return None
        return snapped
    except httpx.HTTPError as error:
        record_routing_failure("snap", "transport")
        log.warning("ORS snap preflight network error (%s): %s", type(error).__name__, error)
        return None
    except Exception as error:  # noqa: BLE001
        record_routing_failure("snap", "invalid_response")
        log.warning("ORS snap preflight error: %s", error)
        return None


def preflight_route_candidates(
    candidate_routes: list[list[LatLon]],
    *,
    sport: str = "run",
    closed: bool = False,
    radius_m: int | None = None,
    max_guide_points: int = 12,
) -> list[SnapPreflightResult] | None:
    """Rank many placements with one cheap batched road-snap request.

    The snap endpoint does not prove connectivity. It is deliberately used as
    a coarse filter: final candidates still have to pass Directions routing
    and the full validation gates.
    """
    if not candidate_routes:
        return []
    cfg = get_settings().routing
    # Batch snap is ORS-only; keyless OSRM would need one request per point,
    # which public servers' fair-use limits do not allow.
    if select_routing_backend(cfg) != "ors":
        return None

    guide_budget = min(
        _MAX_GUIDE_COORDINATES,
        max(4 if closed else 2, int(max_guide_points)),
    )
    guides: list[list[LatLon]] = []
    flattened: list[list[float]] = []
    location_indices: dict[LatLon, int] = {}
    guide_indices: list[list[int]] = []
    candidate_indices: list[int] = []
    for candidate_index, route in enumerate(candidate_routes):
        guide = _subsample(route, closed=closed, max_points=guide_budget)
        if len(guide) < 2:
            continue
        new_points = dict.fromkeys(point for point in guide if point not in location_indices)
        if len(flattened) + len(new_points) > _MAX_SNAP_LOCATIONS:
            break
        # Snap is pointwise: repeated loop endpoints and shared guides need
        # one lookup, but keep their original positions in every contour.
        for point in new_points:
            location_indices[point] = len(flattened)
            flattened.append([point[1], point[0]])
        guide_indices.append([location_indices[point] for point in guide])
        guides.append(guide)
        candidate_indices.append(candidate_index)
    if not flattened:
        return []

    profile = profile_for(sport)
    url = f"{cfg.ors_base_url.rstrip('/')}/v2/snap/{profile}/json"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.ors_api_key:
        headers["Authorization"] = cfg.ors_api_key
    radius = max(1, min(350, int(radius_m or cfg.snap_radius_m)))

    with _routing_client() as client:
        response_locations = _snap_request(
            url,
            headers,
            flattened,
            radius=radius,
            client=client,
        )
    if response_locations is None:
        return None

    scoring_started = time.perf_counter()
    results: list[SnapPreflightResult] = []
    for candidate_index, guide, indices in zip(
        candidate_indices,
        guides,
        guide_indices,
        strict=True,
    ):
        snapped_valid: list[LatLon] = []
        snap_distances: list[float] = []
        for index in indices:
            item = response_locations[index]
            if not isinstance(item, dict):
                continue
            location = item.get("location")
            if (
                not isinstance(location, list | tuple)
                or len(location) < 2
            ):
                continue
            try:
                snapped_point = _validate_waypoint(
                    (float(location[1]), float(location[0]))
                )
                snap_distance = float(item.get("snapped_distance", 0.0))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(snap_distance) or snap_distance < 0:
                continue
            snapped_valid.append(snapped_point)
            snap_distances.append(snap_distance)

        coverage = len(snapped_valid) / len(guide)
        if len(snapped_valid) >= 2:
            diagnostics = shape_similarity.similarity_diagnostics_between_routes(
                # Missing street support must not erase a tip or notch from
                # the reference and turn a damaged outline into a perfect fit.
                guide,
                snapped_valid,
                n=64,
                closed_sample_floor=64,
            )
        else:
            diagnostics = shape_similarity.SimilarityDiagnostics(
                fidelity=0.0,
                spatial_similarity=0.0,
                coverage_similarity=0.0,
                turning_similarity=0.0,
                length_similarity=0.0,
                extent_similarity=0.0,
                route_length_ratio=0.0,
                mean_deviation_ratio=float("inf"),
            )
        mean_snap = (
            sum(snap_distances) / len(snap_distances)
            if snap_distances
            else float(radius)
        )
        snap_distance_score = math.exp(-mean_snap / max(radius * 0.75, 20.0))
        distinct_ratio = (
            len({(round(lat, 7), round(lon, 7)) for lat, lon in snapped_valid})
            / len(snapped_valid)
            if snapped_valid
            else 0.0
        )
        score = coverage * math.sqrt(distinct_ratio) * (
            0.40 * diagnostics.fidelity
            + 0.15 * diagnostics.turning_similarity
            + 0.15 * diagnostics.landmark_similarity
            + 0.12 * diagnostics.length_similarity
            + 0.08 * diagnostics.coverage_similarity
            + 0.10 * snap_distance_score
        )
        if coverage < 0.75:
            score = min(score, 0.25 * coverage)
        results.append(
            SnapPreflightResult(
                candidate_index=candidate_index,
                score=float(min(1.0, max(0.0, score))),
                snap_coverage=float(coverage),
                mean_snap_distance_m=float(mean_snap),
                shape_fidelity=diagnostics.fidelity,
                turning_similarity=diagnostics.turning_similarity,
                length_similarity=diagnostics.length_similarity,
                route_length_ratio=diagnostics.route_length_ratio,
                landmark_similarity=diagnostics.landmark_similarity,
            )
        )

    duration_ms = round((time.perf_counter() - scoring_started) * 1000.0, 2)
    log.info(
        "ORS snap preflight scored %d placements in %.2fms",
        len(results),
        duration_ms,
        extra={
            "event": "preflight.scoring.completed",
            "candidate_count": len(results),
            "sample_count": 64,
            "duration_ms": duration_ms,
        },
    )
    return sorted(results, key=lambda result: result.score, reverse=True)


def _validate_waypoint(point: LatLon) -> LatLon:
    if len(point) != 2:
        raise ValueError("each waypoint must contain latitude and longitude")
    lat, lon = float(point[0]), float(point[1])
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("waypoint coordinates must be finite")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("waypoint coordinates fall outside valid latitude/longitude ranges")
    return lat, lon


def _prepare_waypoints(waypoints: list[LatLon], *, closed: bool) -> list[LatLon]:
    """Validate, de-duplicate, and optionally close a waypoint sequence."""
    prepared: list[LatLon] = []
    for raw_point in waypoints:
        point = _validate_waypoint(raw_point)
        if not prepared or point != prepared[-1]:
            prepared.append(point)
    if closed and len(prepared) > 1 and prepared[-1] != prepared[0]:
        prepared.append(prepared[0])
    return prepared


def _straight_line_connector(waypoints: list[LatLon], *, closed: bool) -> tuple[list[LatLon], float, bool]:
    """Great-circle preview used when no routing service is available.

    The returned ``snapped`` flag is always false: this geometry is useful for
    previewing the selected shape but is not claimed to be street-feasible.
    """
    pts = _prepare_waypoints(waypoints, closed=closed)
    if not pts:
        return [], 0.0, False
    # Densify so the GPX isn't just corner-to-corner segments.
    densified: list[LatLon] = []
    for a, b in zip(pts, pts[1:], strict=False):
        densified.append(a)
        densified.extend(_interpolate(a, b, step_m=40.0))
    densified.append(pts[-1])
    dist = geo.path_distance_m(densified)
    return densified, dist, False


def _interpolate(a: LatLon, b: LatLon, *, step_m: float) -> list[LatLon]:
    seg = geo.haversine(*a, *b)
    if seg <= step_m:
        return []
    n = max(1, int(seg / step_m))
    out: list[LatLon] = []
    for i in range(1, n):
        f = i / n
        lat = a[0] + (b[0] - a[0]) * f
        lon = a[1] + (b[1] - a[1]) * f
        out.append((lat, lon))
    return out


def _subsample(
    waypoints: list[LatLon], *, closed: bool, max_points: int = _MAX_ORS_COORDINATES
) -> list[LatLon]:
    """Build a bounded, curvature-preserving set of ORS guide points.

    Sparse templates used to send only their authored corners: a 10 km diamond
    therefore gave ORS five coordinates and several kilometres of freedom
    between each pair. Dense templates had the opposite problem and were
    simplified to a similarly sparse outline. This sampler protects meaningful
    turns, then splits the largest remaining arc-length gaps until consecutive
    guides are roughly 400 m apart (subject to the caller's visual-guide
    budget and ORS's hard 50-coordinate limit).

    When a drawing contains more protected turns than the budget can hold, a
    metre-space Douglas-Peucker pass provides the least aggressive fallback.
    """
    minimum = 3 if closed else 2
    if max_points < minimum:
        raise ValueError(f"max_points must be at least {minimum}")
    pts = _prepare_waypoints(waypoints, closed=closed)
    if len(pts) < 2:
        return pts

    center_lat = sum(point[0] for point in pts) / len(pts)
    center_lon = sum(point[1] for point in pts) / len(pts)
    cos_lat = max(abs(math.cos(math.radians(center_lat))), 1e-8)

    def to_metres(point: LatLon) -> tuple[float, float]:
        lat, lon = point
        return (
            math.radians(lon - center_lon) * geo.EARTH_R_M * cos_lat,
            math.radians(lat - center_lat) * geo.EARTH_R_M,
        )

    def to_latlon(point: tuple[float, float]) -> LatLon:
        x, y = point
        return (
            center_lat + math.degrees(y / geo.EARTH_R_M),
            center_lon + math.degrees(x / (geo.EARTH_R_M * cos_lat)),
        )

    metre_points = [to_metres(point) for point in pts]
    line = LineString(metre_points)
    total_length = float(line.length)
    if total_length <= 1e-6:
        return pts[:minimum]
    segment_lengths = [
        math.hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(metre_points, metre_points[1:], strict=False)
    ]
    if len(pts) <= max_points and max(segment_lengths, default=0.0) <= _GUIDANCE_SPACING_M:
        return pts

    protected_indices = {0, len(pts) - 1}
    turn_threshold = math.radians(_CORNER_TURN_DEG)
    for index in range(1, len(metre_points) - 1):
        previous = metre_points[index - 1]
        current = metre_points[index]
        following = metre_points[index + 1]
        incoming = (current[0] - previous[0], current[1] - previous[1])
        outgoing = (following[0] - current[0], following[1] - current[1])
        incoming_length = math.hypot(*incoming)
        outgoing_length = math.hypot(*outgoing)
        if incoming_length <= 1e-6 or outgoing_length <= 1e-6:
            continue
        cosine = (
            incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
        ) / (incoming_length * outgoing_length)
        turn = math.acos(min(1.0, max(-1.0, cosine)))
        if turn >= turn_threshold:
            protected_indices.add(index)

    if len(protected_indices) > max_points:
        return _simplify_to_budget(
            pts,
            line,
            to_latlon=to_latlon,
            closed=closed,
            max_points=max_points,
        )

    cumulative = [0.0]
    for length in segment_lengths:
        cumulative.append(cumulative[-1] + length)

    selected: dict[float, LatLon | None] = {
        cumulative[index]: pts[index] for index in protected_indices
    }
    desired_count = min(
        max_points,
        max(minimum, len(selected), math.ceil(total_length / _GUIDANCE_SPACING_M) + 1),
    )

    # Repeatedly bisect the largest uncovered interval. Unlike plain uniform
    # resampling, this never displaces a protected corner to make room.
    while len(selected) < max_points:
        positions = sorted(selected)
        gap_start, gap_end = max(
            zip(positions, positions[1:], strict=False),
            key=lambda interval: interval[1] - interval[0],
        )
        if (
            len(selected) >= desired_count
            and gap_end - gap_start <= _GUIDANCE_SPACING_M
        ):
            break
        midpoint = (gap_start + gap_end) / 2.0
        if midpoint in selected or gap_end - gap_start <= 1e-6:
            break
        selected[midpoint] = None

    guided: list[LatLon] = []
    for position in sorted(selected):
        authored = selected[position]
        if authored is not None:
            guided.append(authored)
            continue
        point = line.interpolate(position)
        guided.append(to_latlon((float(point.x), float(point.y))))

    guided[0] = pts[0]
    guided[-1] = pts[-1]
    return _prepare_waypoints(guided, closed=closed)


def _simplify_to_budget(
    pts: list[LatLon],
    line: LineString,
    *,
    to_latlon,
    closed: bool,
    max_points: int,
) -> list[LatLon]:
    """Least-aggressive metre-space simplification for corner-heavy shapes."""
    min_x, min_y, max_x, max_y = line.bounds
    low, high = 0.0, max(math.hypot(max_x - min_x, max_y - min_y), 1.0)
    best: list[LatLon] | None = None
    for _ in range(24):
        tolerance = (low + high) / 2.0
        simplified_line = line.simplify(tolerance, preserve_topology=True)
        if line.is_simple and not simplified_line.is_simple:
            low = tolerance
            continue
        simplified = [to_latlon(point) for point in simplified_line.coords]
        if len(simplified) <= max_points:
            best = simplified
            high = tolerance
        else:
            low = tolerance

    if best is None:
        # Defensive fallback for unusual/degenerate geometries. Even sampling
        # preserves both endpoints (including the repeated endpoint of a loop).
        indices = [
            round(index * (len(pts) - 1) / (max_points - 1))
            for index in range(max_points)
        ]
        best = [pts[index] for index in indices]

    best[0] = pts[0]
    best[-1] = pts[-1]
    return _prepare_waypoints(best, closed=closed)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _extra_section(extras: dict, *names: str) -> dict:
    for name in names:
        section = extras.get(name)
        if isinstance(section, dict):
            return section
    return {}


def _extra_summary(section: dict, route_distance_m: float) -> list[dict]:
    """Normalise an ORS extra summary and recover distance from amount."""

    normalised = []
    for item in section.get("summary") or []:
        if not isinstance(item, dict):
            continue
        raw_code = item.get("value")
        if isinstance(raw_code, bool) or not isinstance(raw_code, (int, float, str)):
            continue
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            continue
        distance = _finite_number(item.get("distance"))
        amount = _finite_number(item.get("amount"))
        if distance is None and amount is not None and route_distance_m > 0:
            distance = route_distance_m * amount / 100.0
        if distance is None or distance < 0:
            continue
        normalised.append(
            {
                "code": code,
                "distance_m": distance,
                "share": min(1.0, distance / route_distance_m)
                if route_distance_m > 0
                else 0.0,
            }
        )
    return normalised


def _extra_values(section: dict) -> list[tuple[int, int, int]]:
    values = []
    for item in section.get("values") or []:
        if not isinstance(item, list | tuple) or len(item) < 3:
            continue
        try:
            start, end, code = int(item[0]), int(item[1]), int(item[2])
        except (TypeError, ValueError):
            continue
        if start >= 0 and end > start:
            values.append((start, end, code))
    return values


def _sample_concern_segment(points: list[LatLon]) -> list[LatLon]:
    if len(points) <= _MAX_CONCERN_POINTS:
        return points
    indices = [
        round(index * (len(points) - 1) / (_MAX_CONCERN_POINTS - 1))
        for index in range(_MAX_CONCERN_POINTS)
    ]
    return [points[index] for index in indices]


def _segments_for_codes(
    values: list[tuple[int, int, int]],
    codes: set[int],
    polyline: list[LatLon],
) -> list[list[LatLon]]:
    segments = []
    for start, end, code in values:
        if code not in codes or start >= len(polyline) - 1:
            continue
        last = min(end, len(polyline) - 1)
        segment = polyline[start : last + 1]
        if len(segment) >= 2:
            segments.append(_sample_concern_segment(segment))
        if len(segments) >= _MAX_CONCERN_SEGMENTS:
            break
    return segments


def _elevation_metrics(
    coordinates: list,
    summary: dict,
) -> tuple[float | None, float | None, float | None]:
    """Return ascent, descent, and a noise-resistant steepest climb."""

    elevated = []
    for coordinate in coordinates:
        if not isinstance(coordinate, list | tuple) or len(coordinate) < 3:
            continue
        lon = _finite_number(coordinate[0])
        lat = _finite_number(coordinate[1])
        elevation = _finite_number(coordinate[2])
        if lon is None or lat is None or elevation is None:
            continue
        elevated.append((lat, lon, elevation))
    if len(elevated) < 2:
        return None, None, None

    ascent = _finite_number(summary.get("ascent"))
    descent = _finite_number(summary.get("descent"))
    if ascent is None or descent is None:
        rises = [
            elevated[index + 1][2] - elevated[index][2]
            for index in range(len(elevated) - 1)
        ]
        if ascent is None:
            ascent = sum(max(0.0, rise) for rise in rises)
        if descent is None:
            descent = sum(max(0.0, -rise) for rise in rises)

    cumulative = [0.0]
    for start, end in zip(elevated, elevated[1:], strict=False):
        cumulative.append(
            cumulative[-1] + geo.haversine(start[0], start[1], end[0], end[1])
        )

    max_grade = 0.0
    minimum_window_m = 30.0
    for start_index in range(len(elevated) - 1):
        end_index = start_index + 1
        while (
            end_index < len(elevated)
            and cumulative[end_index] - cumulative[start_index] < minimum_window_m
        ):
            end_index += 1
        if end_index >= len(elevated):
            continue
        horizontal = cumulative[end_index] - cumulative[start_index]
        rise = elevated[end_index][2] - elevated[start_index][2]
        if horizontal > 0:
            max_grade = max(max_grade, 100.0 * rise / horizontal)

    if cumulative[-1] < minimum_window_m:
        horizontal = cumulative[-1]
        rise = elevated[-1][2] - elevated[0][2]
        max_grade = max(0.0, 100.0 * rise / horizontal) if horizontal > 0 else 0.0
    return max(0.0, ascent), max(0.0, descent), max(0.0, max_grade)


def _build_route_readiness(
    *,
    properties: dict,
    coordinates: list,
    polyline: list[LatLon],
    route_distance_m: float,
    sport: str,
) -> RouteReadiness:
    """Build cautious readiness facts from ORS and OpenStreetMap attributes."""

    extras = properties.get("extras")
    extras = extras if isinstance(extras, dict) else {}
    surface_section = _extra_section(extras, "surface", "surfaces")
    steepness_section = _extra_section(extras, "steepness")
    waytype_section = _extra_section(extras, "waytype", "waytypes")
    suitability_section = _extra_section(extras, "suitability")

    surface_summary = _extra_summary(surface_section, route_distance_m)
    steepness_summary = _extra_summary(steepness_section, route_distance_m)
    waytype_summary = _extra_summary(waytype_section, route_distance_m)
    suitability_summary = _extra_summary(suitability_section, route_distance_m)
    surface_values = _extra_values(surface_section)
    steepness_values = _extra_values(steepness_section)
    waytype_values = _extra_values(waytype_section)
    suitability_values = _extra_values(suitability_section)

    surfaces = []
    for item in surface_summary:
        code = item["code"]
        category = (
            "paved"
            if code in _PAVED_SURFACES
            else "unpaved"
            if code in _UNPAVED_SURFACES
            else "unknown"
        )
        surfaces.append(
            RouteSurface(
                code=code,
                label=_SURFACE_LABELS.get(code, f"Surface type {code}"),
                distance_m=item["distance_m"],
                share=item["share"],
                category=category,
            )
        )
    surfaces.sort(key=lambda item: item.distance_m, reverse=True)
    known_distance = sum(
        item.distance_m for item in surfaces if item.category != "unknown"
    )
    unpaved_distance = sum(
        item.distance_m for item in surfaces if item.category == "unpaved"
    )
    surface_available = bool(surfaces)
    known_share = (
        min(1.0, known_distance / route_distance_m)
        if surface_available and route_distance_m > 0
        else None
    )
    unpaved_share = (
        min(1.0, unpaved_distance / route_distance_m)
        if surface_available and route_distance_m > 0
        else None
    )

    summary = properties.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    ascent, descent, calculated_grade = _elevation_metrics(coordinates, summary)
    positive_steepness = max(
        (item["code"] for item in steepness_summary if item["code"] > 0),
        default=0,
    )
    lower_bound_grade = _STEEPNESS_LOWER_BOUNDS.get(positive_steepness)
    max_grade = calculated_grade
    grade_is_lower_bound = False
    if lower_bound_grade is not None and (
        max_grade is None or lower_bound_grade > max_grade
    ):
        max_grade = lower_bound_grade
        grade_is_lower_bound = positive_steepness > 0
    elevation_available = any(
        value is not None for value in (ascent, descent, max_grade)
    )

    concerns: list[RouteConcern] = []

    def add_concern(
        *,
        code: str,
        label: str,
        detail: str,
        severity: str,
        matching_codes: set[int],
        summary_rows: list[dict],
        value_rows: list[tuple[int, int, int]],
        minimum_share: float = 0.0,
    ) -> None:
        matching = [item for item in summary_rows if item["code"] in matching_codes]
        distance = sum(item["distance_m"] for item in matching)
        share = (
            min(1.0, distance / route_distance_m)
            if route_distance_m > 0
            else 0.0
        )
        if distance <= 0 or share < minimum_share:
            return
        segments = _segments_for_codes(value_rows, matching_codes, polyline)
        concerns.append(
            RouteConcern(
                code=code,
                label=label,
                detail=detail,
                severity=severity,
                distance_m=distance,
                share=share,
                segment_count=sum(
                    1 for _, _, value in value_rows if value in matching_codes
                ),
                segments_preview=segments,
            )
        )

    add_concern(
        code="low_suitability",
        label="Low suitability",
        detail="The routing profile rates these mapped ways as a weak fit.",
        severity="warning",
        matching_codes={1, 2, 3},
        summary_rows=suitability_summary,
        value_rows=suitability_values,
        minimum_share=0.02,
    )
    steep_codes = {3, 4, 5} if sport.startswith("bike") else {4, 5}
    add_concern(
        code="steep_climb",
        label="Steep section",
        detail="This part reaches a demanding mapped gradient.",
        severity="warning",
        matching_codes=steep_codes,
        summary_rows=steepness_summary,
        value_rows=steepness_values,
        minimum_share=0.01,
    )
    add_concern(
        code="construction",
        label="Mapped construction",
        detail="OpenStreetMap currently classifies this way as construction.",
        severity="warning",
        matching_codes={10},
        summary_rows=waytype_summary,
        value_rows=waytype_values,
    )
    if sport.startswith("bike"):
        add_concern(
            code="steps",
            label="Steps",
            detail="The mapped route includes steps that may require carrying the bike.",
            severity="warning",
            matching_codes={8},
            summary_rows=waytype_summary,
            value_rows=waytype_values,
        )
        add_concern(
            code="unpaved",
            label="Unpaved riding",
            detail="Check that the bike and conditions suit these unpaved sections.",
            severity="warning",
            matching_codes=_UNPAVED_SURFACES,
            summary_rows=surface_summary,
            value_rows=surface_values,
            minimum_share=0.03,
        )
    else:
        add_concern(
            code="loose_surface",
            label="Loose or difficult surface",
            detail="Mapped ground, ice, snow, or sand may slow this section.",
            severity="warning",
            matching_codes={12, 13, 15},
            summary_rows=surface_summary,
            value_rows=surface_values,
            minimum_share=0.01,
        )
    add_concern(
        code="ferry",
        label="Ferry connection",
        detail="Timing and availability need a separate check.",
        severity="warning",
        matching_codes={9},
        summary_rows=waytype_summary,
        value_rows=waytype_values,
    )
    add_concern(
        code="unknown_surface",
        label="Surface data gap",
        detail="The map does not identify the surface on this part.",
        severity="info",
        matching_codes={0},
        summary_rows=surface_summary,
        value_rows=surface_values,
        minimum_share=0.05,
    )
    add_concern(
        code="unknown_waytype",
        label="Road type data gap",
        detail="The map has limited way-type detail for this part.",
        severity="info",
        matching_codes={0},
        summary_rows=waytype_summary,
        value_rows=waytype_values,
        minimum_share=0.05,
    )
    concerns.sort(
        key=lambda item: (item.severity == "warning", item.distance_m),
        reverse=True,
    )

    segment_data_available = bool(
        steepness_summary or waytype_summary or suitability_summary
    )
    available_groups = sum(
        (elevation_available, surface_available, segment_data_available)
    )
    data_quality = (
        "good" if available_groups == 3 else "partial" if available_groups else "unavailable"
    )
    has_warning = any(item.severity == "warning" for item in concerns)
    has_uncertainty = any(item.severity == "info" for item in concerns)
    status = (
        "unavailable"
        if data_quality == "unavailable"
        else "review"
        if has_warning or has_uncertainty or data_quality == "partial"
        else "ready"
    )
    return RouteReadiness(
        status=status,
        data_quality=data_quality,
        elevation_available=elevation_available,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        max_grade_percent=max_grade,
        max_grade_is_lower_bound=grade_is_lower_bound,
        surface_available=surface_available,
        surface_known_share=known_share,
        unpaved_share=unpaved_share,
        surfaces=surfaces,
        concerns=concerns,
    )


def _bearing_constraints(
    coords: list,
    start_direction_deg: float | None,
) -> list[list[float]] | None:
    """Build ORS bearing filters for the first routed street segment.

    ORS requires one bearing pair per routed source waypoint. The requested
    start gets a useful ±45° constraint; intermediate guide points use a 180°
    deviation so they remain unrestricted.
    """

    if start_direction_deg is None or len(coords) < 2:
        return None
    direction = float(start_direction_deg)
    if not math.isfinite(direction):
        return None
    return [[direction % 360.0, 45.0]] + [
        [0.0, 180.0] for _ in range(max(0, len(coords) - 2))
    ]


def _ors_request(
    url: str, headers: dict, coords: list, *, preference: str, continue_straight: bool,
    radius: int, sport: str = "run",
    route_preferences: RoutePreferences | None = None,
    start_direction_deg: float | None = None,
    client: httpx.Client | None = None,
) -> _ORSRouteResult | _ORSFailure:
    """Return a route or a structured failure from one ORS request."""
    runtime = active_workflow_runtime()
    if runtime is not None and runtime.routing_rate_limited("directions"):
        return _ORSFailure(429, None, "Directions quota exhausted during this workflow")
    payload = {
        "coordinates": coords,
        "preference": preference,
        "geometry_simplify": False,
        "instructions": False,
        "continue_straight": continue_straight,
        "radiuses": [radius] * len(coords),
        "elevation": True,
        "extra_info": ["surface", "steepness", "waytype", "suitability"],
    }
    bearings = _bearing_constraints(coords, start_direction_deg)
    if bearings is not None:
        payload["bearings"] = bearings
        # ORS documents dynamic routing as mandatory when bearing filters are
        # supplied. Without this, the request can succeed while ignoring them.
        payload["optimized"] = False
    preferences = route_preferences or RoutePreferences()
    avoid_features = [
        feature
        for feature, enabled in (
            ("steps", preferences.avoid_steps),
            ("ferries", preferences.avoid_ferries),
            ("fords", preferences.avoid_fords),
        )
        if enabled
    ]
    options: dict[str, object] = {}
    if avoid_features:
        options["avoid_features"] = avoid_features
    if not sport.startswith("bike") and (preferences.prefer_quiet or preferences.prefer_green):
        weightings: dict[str, float] = {}
        if preferences.prefer_quiet:
            weightings["quiet"] = 1.0
        if preferences.prefer_green:
            weightings["green"] = 0.8
        options["profile_params"] = {"weightings": weightings}
    if options:
        payload["options"] = options
    try:
        sender = client if client is not None else httpx
        record_routing_request("directions")
        r = sender.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
        if r.status_code != 200:
            failure = _response_failure(r)
            if not (r.status_code in {400, 404}
                    and failure.error_code in _CONNECTIVITY_ERROR_CODES | {2010}):
                record_routing_failure("directions", f"http_{r.status_code}")
            log.warning(
                "ORS returned HTTP %d, code=%s (radius=%dm): %s",
                r.status_code,
                failure.error_code if failure.error_code is not None else "unknown",
                radius,
                failure.message[:240],
            )
            return failure
        data = r.json()
        features = data.get("features") or []
        if not features:
            record_routing_failure("directions", "invalid_response")
            log.warning("ORS returned no features (radius=%dm)", radius)
            return _ORSFailure(r.status_code, None, "ORS returned no route features")
        feature = features[0]
        geom = feature.get("geometry") or {}
        if geom.get("type") != "LineString":
            record_routing_failure("directions", "invalid_response")
            return _ORSFailure(r.status_code, None, "ORS did not return a connected LineString")
        coords_xy = geom.get("coordinates") or []
        polyline = [_validate_waypoint((coord[1], coord[0])) for coord in coords_xy]
        if len(polyline) < 2:
            record_routing_failure("directions", "invalid_response")
            log.warning("ORS returned fewer than two geometry points (radius=%dm)", radius)
            return _ORSFailure(r.status_code, None, "ORS returned incomplete route geometry")

        properties = feature.get("properties") or {}
        # ORS warning 3 denotes skipped (unrouted) segments. A HTTP 200 with
        # those straight connectors is not complete street-routing evidence.
        warnings = properties.get("warnings") or []
        metadata_query = (data.get("metadata") or {}).get("query") or {}
        if (any(str(warning.get("code")) == "3" for warning in warnings)
                or metadata_query.get("skip_segments")):
            record_routing_failure("directions", "invalid_response")
            return _ORSFailure(r.status_code, None, "ORS returned skipped, unrouted segments")
        summary = properties.get("summary") or {}
        distance = summary.get("distance")
        if distance is None:
            segment_distances = [
                segment.get("distance")
                for segment in (properties.get("segments") or [])
                if segment.get("distance") is not None
            ]
            distance = sum(float(value) for value in segment_distances) if segment_distances else None
        geometry_distance = geo.path_distance_m(polyline)
        if not math.isfinite(geometry_distance) or geometry_distance <= 0:
            record_routing_failure("directions", "invalid_response")
            return _ORSFailure(r.status_code, None, "ORS returned zero-length route geometry")
        distance = float(distance) if distance is not None else geometry_distance
        if not math.isfinite(distance) or distance <= 0:
            distance = geometry_distance
        # A malformed/partial summary must never under-report the geometry.
        distance = max(distance, geometry_distance)
        readiness = _build_route_readiness(
            properties=properties,
            coordinates=coords_xy,
            polyline=polyline,
            route_distance_m=distance,
            sport=sport,
        )
        return _ORSRouteResult(polyline, distance, readiness)
    except httpx.HTTPError as e:
        record_routing_failure("directions", "transport")
        log.warning("ORS network error (%s, radius=%dm): %s", type(e).__name__, radius, e)
        return _ORSFailure(None, None, str(e), read_timeout=isinstance(e, httpx.ReadTimeout))
    except Exception as e:  # noqa: BLE001
        record_routing_failure("directions", "invalid_response")
        log.warning("ORS routing error (radius=%dm): %s", radius, e)
        return _ORSFailure(None, None, str(e))


def _response_failure(response) -> _ORSFailure:
    """Extract the ORS internal error code without trusting its response body."""
    error_code: int | None = None
    message = response.text[:300] or f"HTTP {response.status_code}"
    try:
        data = response.json()
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            raw_code = error.get("code")
            error_code = int(raw_code) if raw_code is not None else None
            raw_message = error.get("message")
            if isinstance(raw_message, str) and raw_message.strip():
                message = raw_message.strip()
    except (TypeError, ValueError):
        pass
    return _ORSFailure(int(response.status_code), error_code, message)


def _prune_failed_pair(
    waypoints: list[LatLon], message: str, *, closed: bool
) -> list[LatLon] | None:
    """Drop one interior via-point from the pair named by ORS error 2009.

    ORS reports zero-based indexes for the unconnectable pair. Removing the
    second interior point normally preserves the incoming shape edge while
    allowing the router to bridge over an isolated footway or turn trap.
    Start/end points and a closed loop's repeated endpoint are never removed.
    """
    match = _FAILED_PAIR_PATTERN.search(message)
    if match is None:
        return None
    first, second = (int(match.group(1)), int(match.group(2)))
    last = len(waypoints) - 1
    for index in (second, first):
        if index <= 0 or index >= last:
            continue
        reduced = waypoints[:index] + waypoints[index + 1 :]
        minimum = 4 if closed else 2
        if len(reduced) >= minimum:
            return _prepare_waypoints(reduced, closed=closed)
    return None


def _reduce_waypoints(waypoints: list[LatLon], *, closed: bool) -> list[LatLon] | None:
    """Reduce detail by roughly 28%, preserving endpoints and loop closure."""
    minimum = 4 if closed else 2
    if len(waypoints) <= minimum:
        return None
    next_budget = max(minimum, math.floor(len(waypoints) * 0.72))
    if next_budget >= len(waypoints):
        next_budget = len(waypoints) - 1
    return _subsample(waypoints, closed=closed, max_points=next_budget)


@dataclass(frozen=True)
class _OrsDirections:
    """:class:`~.routing_backend.DirectionsBackend` for hosted or self-hosted ORS."""

    base_url: str
    api_key: str
    preference: str
    continue_straight: bool
    sport: str = "run"
    route_preferences: RoutePreferences | None = None
    start_direction_deg: float | None = None
    name: str = "ors"

    @property
    def endpoint(self) -> str:
        return self.base_url

    def route(
        self,
        via: list[LatLon],
        *,
        radius: int,
        client: httpx.Client,
    ) -> _ORSRouteResult | _ORSFailure:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = self.api_key
        return _ors_request(
            f"{self.base_url.rstrip('/')}/v2/directions/{profile_for(self.sport)}/geojson",
            headers,
            [[lon, lat] for lat, lon in via],
            preference=self.preference,
            continue_straight=self.continue_straight,
            radius=radius,
            sport=self.sport,
            route_preferences=self.route_preferences,
            start_direction_deg=self.start_direction_deg,
            client=client,
        )


def _directions_backend(
    cfg,
    *,
    sport: str,
    route_preferences: RoutePreferences | None,
    start_direction_deg: float | None,
) -> DirectionsBackend | None:
    """Build the configured directions provider, or ``None`` when none is usable."""

    selected = select_routing_backend(cfg)
    if selected == "ors":
        return _OrsDirections(
            base_url=cfg.ors_base_url,
            api_key=cfg.ors_api_key,
            preference=cfg.preference,
            continue_straight=cfg.continue_straight,
            sport=sport,
            route_preferences=route_preferences,
            start_direction_deg=start_direction_deg,
        )
    if selected == "osrm":
        return osrm_client.OsrmDirections(
            base_url=cfg.osrm_base_url,
            sport=sport,
            start_direction_deg=start_direction_deg,
            min_interval_s=cfg.osrm_min_interval_s,
        )
    return None


def snap_route_detailed(
    waypoints: list[LatLon], *, sport: str = "run", closed: bool = False,
    route_preferences: RoutePreferences | None = None,
    start_direction_deg: float | None = None,
    guide_budget: int | None = None,
) -> tuple[list[LatLon], float, bool, RouteReadiness]:
    """Snap waypoints and include readiness evidence for the returned route.

    Ideal drawings default to 24 guides. Already street-routed graph proposals
    may request up to 50 to preserve their road choices. The budget is part of
    the cache key and only affects request guides, never returned geometry.

    Bounded retry strategies:
    - **Radius widening** (error 2010): a via-point lands on a building/park
      with no road nearby → widen the search radius and retry.
    - **Waypoint reduction** (error 2009): two consecutive via-points can't be
      connected (e.g. shape crosses a lake) → reduce the via-point budget so
      ORS has freedom to find a detour around the obstacle.
    - **Read timeout**: retry the same request once within the existing attempt
      limit and workflow deadline.
    """
    cfg = get_settings().routing
    budget = _MAX_GUIDE_COORDINATES if guide_budget is None else guide_budget
    if type(budget) is not int or not (3 if closed else 2) <= budget <= _MAX_ORS_COORDINATES:
        raise ValueError("guide_budget must fit the route endpoints and ORS's 50-coordinate limit")
    prepared = _prepare_waypoints(waypoints, closed=closed)
    if len(prepared) < 2:
        route, distance, snapped = _straight_line_connector(prepared, closed=closed)
        return route, distance, snapped, RouteReadiness()

    backend = _directions_backend(
        cfg,
        sport=sport,
        route_preferences=route_preferences,
        start_direction_deg=start_direction_deg,
    )
    if backend is None:
        route, distance, snapped = _straight_line_connector(prepared, closed=closed)
        return route, distance, snapped, RouteReadiness()

    start = max(1, int(cfg.snap_radius_m))
    runtime = active_workflow_runtime()
    cache_key = _directions_cache_key(
        prepared,
        profile=f"{backend.name}:{sport}",
        endpoint=backend.endpoint,
        closed=closed,
        start_radius=start,
        route_preferences=route_preferences,
        start_direction_deg=start_direction_deg,
        guide_budget=budget,
        # Reuse identical ORS results only inside one generation. A later user
        # request always asks ORS again, so stale road evidence cannot silently
        # satisfy a new GPS-art workflow.
        workflow_scope=runtime.cache_scope if runtime is not None else None,
    )
    cached = _directions_cache_get(cache_key) if runtime is not None else None
    if cached is not None:
        polyline, distance, snapped, readiness = cached
        log.info(
            "%s directions served from memo cache (%d pts, %.0f m)",
            backend.name.upper(),
            len(polyline),
            distance,
        )
        return polyline, distance, snapped, readiness

    # The hosted API permits 50 coordinates, but treating that limit as a
    # target forces the router through unnecessary off-grid points and creates
    # U-turn scribbles. Preserve authored corners within a smaller visual-guide
    # budget; validation reports the resulting quality if detail is lost.
    via = _subsample(
        prepared,
        closed=closed,
        max_points=budget,
    )
    radii = [start] + [radius for radius in _RADIUS_RETRIES if radius > start]

    attempts = 0
    read_timeout_retried = False
    radius_index = 0
    current_via = via
    with _routing_client() as client:
        while attempts < _MAX_ORS_ATTEMPTS:
            if attempts and runtime is not None and runtime.deadline_exceeded(quality_required=True):
                break
            radius = radii[min(radius_index, len(radii) - 1)]
            attempts += 1
            result = backend.route(current_via, radius=radius, client=client)
            if isinstance(result, _ORSRouteResult | tuple):
                if isinstance(result, _ORSRouteResult):
                    polyline = result.polyline
                    distance = result.distance_m
                    readiness = result.readiness
                else:
                    # Compatibility for lightweight internal test doubles.
                    polyline, distance = result
                    readiness = RouteReadiness()
                fidelity = shape_similarity.fidelity_between_routes(prepared, polyline, n=96)
                log_method = log.info if fidelity >= _ACCEPTABLE_FIDELITY else log.warning
                log_method(
                    "%s succeeded (attempt=%d, radius=%dm, via=%d, fidelity=%.3f%s)",
                    backend.name.upper(),
                    attempts,
                    radius,
                    len(current_via),
                    fidelity,
                    "" if fidelity >= _ACCEPTABLE_FIDELITY else "; refinement required",
                )
                if runtime is not None:
                    _directions_cache_put(
                        cache_key, polyline, distance, True, readiness
                    )
                return polyline, distance, True, readiness

            # Error 2009 means two snapped graph locations cannot be joined.
            # Widening the search radius does not repair that. Remove the
            # precise failing interior via-point when ORS identifies it, then
            # fall back to a curvature-preserving detail reduction.
            if result is None:
                failure = _ORSFailure(None, None, "routing request failed")
            else:
                failure = result
            if (
                failure.read_timeout
                and not read_timeout_retried
                and attempts < _MAX_ORS_ATTEMPTS
                and (runtime is None or not runtime.deadline_exceeded(quality_required=True))
            ):
                read_timeout_retried = True
                log.info("ORS response timed out; retrying the same street guides once")
                continue
            if failure.error_code in _CONNECTIVITY_ERROR_CODES:
                reduced = _prune_failed_pair(
                    current_via, failure.message, closed=closed
                ) or _reduce_waypoints(current_via, closed=closed)
                if reduced is None or reduced == current_via:
                    break
                log.info(
                    "ORS connectivity retry: via-points %d -> %d",
                    len(current_via),
                    len(reduced),
                )
                current_via = reduced
                radius_index = 0
                continue

            # Error 2010 means a point was not found near a routable edge.
            # Only this class of error benefits from a larger snap radius.
            if failure.error_code == 2010 and radius_index + 1 < len(radii):
                radius_index += 1
                continue

            # Authentication, quota, invalid request, and other network failures
            # should not fan out into seven identical paid API calls.
            if failure.status_code in {400, 401, 403, 413, 429} or failure.status_code is None:
                break

            reduced = _reduce_waypoints(current_via, closed=closed)
            if reduced is None or reduced == current_via:
                break
            current_via = reduced
            radius_index = 0

    log.warning("%s routing failed; using straight-line fallback", backend.name.upper())
    route, distance, snapped = _straight_line_connector(prepared, closed=closed)
    return route, distance, snapped, RouteReadiness()


def snap_route(
    waypoints: list[LatLon], *, sport: str = "run", closed: bool = False,
    start_direction_deg: float | None = None,
) -> tuple[list[LatLon], float, bool]:
    """Compatibility wrapper returning route geometry, distance, and snap state."""

    route, distance, snapped, _ = snap_route_detailed(
        waypoints,
        sport=sport,
        closed=closed,
        start_direction_deg=start_direction_deg,
    )
    return route, distance, snapped
