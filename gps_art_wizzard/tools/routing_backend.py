"""Street-routing provider contract and automatic provider selection.

OpenRouteService (ORS) stays the primary provider whenever it is usable: an
``ORS_API_KEY`` is set or ``ORS_BASE_URL`` points at a self-hosted instance.
Without either, the planner falls back to a keyless OSRM server so local runs
still produce street-connected GPX tracks. ``ROUTING_BACKEND`` can force one
provider explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from ..state import RouteReadiness
from . import osm_data

LatLon = tuple[float, float]
RoutingBackendName = Literal["ors", "osrm", "none"]

ROUTING_BACKEND_CHOICES = frozenset({"auto", "ors", "osrm"})

PUBLIC_ORS_BASE_URLS = frozenset({
    "https://api.heigit.org/openrouteservice",
    # Kept for existing deployments until ORS shuts the legacy host down.
    "https://api.openrouteservice.org",
})

# Provider-neutral failure codes. They reuse the ORS numbers because the shared
# retry loop already understands them.
ERROR_POINTS_NOT_CONNECTED = 2009
ERROR_POINT_NOT_ROUTABLE = 2010


@dataclass(frozen=True)
class RouteFailure:
    """Structured routing failure used to choose the next bounded retry."""

    status_code: int | None
    error_code: int | None
    message: str
    read_timeout: bool = False


@dataclass(frozen=True)
class RoutedPath:
    """Successful provider response with its route-readiness evidence."""

    polyline: list[LatLon]
    distance_m: float
    readiness: RouteReadiness

    def __iter__(self):
        """Keep existing internal callers that unpack route and distance working."""

        yield self.polyline
        yield self.distance_m


class DirectionsBackend(Protocol):
    """One street-routing provider able to connect ordered via-points."""

    @property
    def name(self) -> str:
        """Short provider id (``ors`` or ``osrm``) used in logs and cache keys."""
        ...

    @property
    def endpoint(self) -> str:
        """Provider base URL; part of the directions cache key."""
        ...

    def route(
        self,
        via: list[LatLon],
        *,
        radius: int,
        client: httpx.Client,
    ) -> RoutedPath | RouteFailure:
        """Route through ``via`` (lat, lon) within ``radius`` metres of each point."""
        ...


def is_public_ors(base_url: str) -> bool:
    return base_url.rstrip("/").casefold() in PUBLIC_ORS_BASE_URLS


def ors_available(cfg) -> bool:
    """ORS works with a key, or keyless against a self-hosted instance."""

    return bool(cfg.ors_api_key) or not is_public_ors(cfg.ors_base_url)


def select_routing_backend(cfg) -> RoutingBackendName:
    """Pick the routing provider for the given :class:`RoutingConfig`.

    ``auto`` prefers ORS when it is usable and otherwise uses keyless OSRM,
    except in offline mode (tests), where no public service may be contacted.
    """

    choice = str(getattr(cfg, "backend", "auto")).strip().casefold()
    if choice not in ROUTING_BACKEND_CHOICES:
        raise ValueError(
            f"ROUTING_BACKEND must be one of {sorted(ROUTING_BACKEND_CHOICES)}, got {choice!r}"
        )
    if choice == "ors":
        return "ors" if ors_available(cfg) else "none"
    if choice == "osrm":
        return "osrm"
    if ors_available(cfg):
        return "ors"
    if osm_data.offline_mode() or not getattr(cfg, "osrm_base_url", ""):
        return "none"
    return "osrm"
