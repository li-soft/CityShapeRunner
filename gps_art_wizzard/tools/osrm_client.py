"""Keyless OSRM directions backend.

Used when no ORS key or self-hosted ORS is configured. The default server is
the public FOSSGIS OSRM instance (separate foot and bike graphs). Public OSRM
servers are shared, donated infrastructure, so every request goes through a
process-wide throttle; repeated identical requests inside one generation are
already served by the directions memo cache in :mod:`ors_client`.

OSRM provides no surface/steepness extras, so the returned route readiness is
the empty "unavailable" :class:`RouteReadiness`.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

import httpx

from ..state import RouteReadiness
from ..workflow_runtime import (
    active_workflow_runtime,
    record_routing_failure,
    record_routing_request,
)
from . import geo
from .routing_backend import (
    ERROR_POINT_NOT_ROUTABLE,
    ERROR_POINTS_NOT_CONNECTED,
    LatLon,
    RoutedPath,
    RouteFailure,
)

log = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_USER_AGENT = "paceasso-gps-art-planner/0.1 (+keyless OSRM routing)"
_PROFILE_MAP = {
    "run": "foot",
    "walk": "foot",
    "run-walk": "foot",
    "bike": "bike",
    "bike-road": "bike",
    "bike-mtb": "bike",
}
_ERROR_CODES = {
    "NoSegment": ERROR_POINT_NOT_ROUTABLE,
    "NoRoute": ERROR_POINTS_NOT_CONNECTED,
}

_throttle_lock = threading.Lock()
_last_request_at = 0.0


def profile_for(sport: str) -> str:
    return _PROFILE_MAP.get(sport, "foot")


def route_url(base_url: str, profile: str, via: list[LatLon]) -> str:
    """Build the OSRM ``/route/v1`` URL; ``{profile}`` in the base is substituted."""

    base = base_url.replace("{profile}", profile).rstrip("/")
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in via)
    return f"{base}/route/v1/{profile}/{coordinates}"


def route_params(
    via: list[LatLon],
    *,
    radius: int,
    start_direction_deg: float | None,
) -> dict[str, str]:
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        # GPS-art cusps and lettering often need U-turns at via-points.
        "continue_straight": "false",
        "radiuses": ";".join(str(int(radius)) for _ in via),
    }
    if start_direction_deg is not None and math.isfinite(start_direction_deg) and len(via) >= 2:
        # Only the start is constrained; empty entries leave other points free.
        params["bearings"] = ";".join(
            [f"{round(start_direction_deg % 360.0) % 360},45"] + [""] * (len(via) - 1)
        )
    return params


def _wait_for_fair_use_slot(min_interval_s: float) -> None:
    """Space requests to a public OSRM server at least ``min_interval_s`` apart."""

    global _last_request_at
    with _throttle_lock:
        wait_s = _last_request_at + min_interval_s - time.monotonic()
        if wait_s > 0:
            time.sleep(wait_s)
        _last_request_at = time.monotonic()


def _failure_from_response(response: httpx.Response) -> RouteFailure:
    message = response.text[:300] or f"HTTP {response.status_code}"
    code = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            message = str(payload.get("message") or code or message)
    except ValueError:
        pass
    return RouteFailure(response.status_code, _ERROR_CODES.get(str(code)), message)


def parse_route_response(payload: object) -> RoutedPath | RouteFailure:
    """Convert an OSRM ``Ok`` payload into a validated routed path."""

    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        code = payload.get("code") if isinstance(payload, dict) else None
        return RouteFailure(200, _ERROR_CODES.get(str(code)), f"OSRM returned {code!r}")
    routes = payload.get("routes") or []
    geometry = (routes[0].get("geometry") or {}) if routes else {}
    if geometry.get("type") != "LineString":
        return RouteFailure(200, None, "OSRM did not return a connected LineString")
    polyline: list[LatLon] = []
    for coordinate in geometry.get("coordinates") or []:
        lat, lon = float(coordinate[1]), float(coordinate[0])
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return RouteFailure(200, None, "OSRM returned non-finite coordinates")
        polyline.append((lat, lon))
    geometry_distance = geo.path_distance_m(polyline) if len(polyline) >= 2 else 0.0
    if not math.isfinite(geometry_distance) or geometry_distance <= 0:
        return RouteFailure(200, None, "OSRM returned zero-length route geometry")
    reported = routes[0].get("distance")
    distance = float(reported) if isinstance(reported, int | float) else geometry_distance
    if not math.isfinite(distance) or distance <= 0:
        distance = geometry_distance
    # A malformed summary must never under-report the geometry.
    return RoutedPath(polyline, max(distance, geometry_distance), RouteReadiness())


@dataclass(frozen=True)
class OsrmDirections:
    """:class:`~.routing_backend.DirectionsBackend` for an OSRM HTTP server."""

    base_url: str
    sport: str = "run"
    start_direction_deg: float | None = None
    min_interval_s: float = 1.0
    name: str = "osrm"

    @property
    def endpoint(self) -> str:
        return self.base_url

    def route(
        self,
        via: list[LatLon],
        *,
        radius: int,
        client: httpx.Client,
    ) -> RoutedPath | RouteFailure:
        runtime = active_workflow_runtime()
        if runtime is not None and runtime.routing_rate_limited("directions"):
            return RouteFailure(429, None, "Directions quota exhausted during this workflow")
        profile = profile_for(self.sport)
        url = route_url(self.base_url, profile, via)
        params = route_params(via, radius=radius, start_direction_deg=self.start_direction_deg)
        _wait_for_fair_use_slot(self.min_interval_s)
        record_routing_request("directions")
        try:
            response = client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as error:
            record_routing_failure("directions", "transport")
            log.warning("OSRM network error (%s): %s", type(error).__name__, error)
            return RouteFailure(
                None, None, str(error), read_timeout=isinstance(error, httpx.ReadTimeout)
            )
        if response.status_code != 200:
            failure = _failure_from_response(response)
            if failure.error_code is None:
                record_routing_failure("directions", f"http_{response.status_code}")
            log.warning(
                "OSRM returned HTTP %d (radius=%dm): %s",
                response.status_code,
                radius,
                failure.message[:240],
            )
            return failure
        try:
            result = parse_route_response(response.json())
        except (TypeError, ValueError, IndexError) as error:
            result = RouteFailure(200, None, f"OSRM returned an invalid response: {error}")
        if isinstance(result, RouteFailure) and result.error_code is None:
            record_routing_failure("directions", "invalid_response")
        return result
