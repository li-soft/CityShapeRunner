"""Routing-provider selection and the keyless OSRM directions backend."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from gps_art_wizzard.tools import ors_client, osrm_client, routing_backend
from gps_art_wizzard.tools.routing_backend import select_routing_backend

PUBLIC_ORS = "https://api.heigit.org/openrouteservice"
OSRM_BASE = "https://osrm.test/routed-{profile}"


def _routing(**overrides) -> SimpleNamespace:
    values = {
        "backend": "auto",
        "ors_api_key": "",
        "ors_base_url": PUBLIC_ORS,
        "osrm_base_url": OSRM_BASE,
        "osrm_min_interval_s": 0.0,
        "snap_radius_m": 120,
        "preference": "shortest",
        "continue_straight": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(routing_backend.osm_data, "offline_mode", lambda: False)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"ors_api_key": "secret"}, "ors"),
        ({"ors_base_url": "http://localhost:8080/ors"}, "ors"),
        ({}, "osrm"),
        ({"osrm_base_url": ""}, "none"),
        ({"backend": "osrm", "ors_api_key": "secret"}, "osrm"),
        ({"backend": "ors"}, "none"),
        ({"backend": "ORS", "ors_api_key": "secret"}, "ors"),
    ],
)
def test_backend_selection(online, overrides, expected) -> None:
    assert select_routing_backend(_routing(**overrides)) == expected


def test_auto_selection_never_contacts_public_osrm_offline() -> None:
    # conftest sets GEOCODE_OFFLINE, which every public-service client honours.
    assert select_routing_backend(_routing()) == "none"
    assert select_routing_backend(_routing(ors_api_key="secret")) == "ors"


def test_unknown_backend_fails_fast() -> None:
    with pytest.raises(ValueError, match="ROUTING_BACKEND"):
        select_routing_backend(_routing(backend="graphhopper"))


def _osrm_ok(coordinates: list[list[float]], distance: float = 1234.0) -> dict:
    return {
        "code": "Ok",
        "routes": [
            {"distance": distance, "geometry": {"type": "LineString", "coordinates": coordinates}}
        ],
    }


@pytest.fixture
def osrm_server(monkeypatch, online):
    """Route ors_client through OSRM against a mocked HTTP transport."""

    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if responses:
            return responses.pop(0)
        coordinates = [
            [float(lon), float(lat)]
            for lon, lat in (pair.split(",") for pair in request.url.path.rsplit("/", 1)[1].split(";"))
        ]
        return httpx.Response(200, json=_osrm_ok(coordinates))

    @contextmanager
    def client():
        with httpx.Client(transport=httpx.MockTransport(handler)) as mocked:
            yield mocked

    routing = _routing()
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    monkeypatch.setattr(ors_client, "_routing_client", client)
    return SimpleNamespace(requests=requests, responses=responses, routing=routing)


WAYPOINTS = [(47.500, 19.040), (47.505, 19.050), (47.510, 19.045)]


@pytest.mark.parametrize(
    ("sport", "path_prefix"),
    [
        ("run", "/routed-foot/route/v1/foot/"),
        ("walk", "/routed-foot/route/v1/foot/"),
        ("bike", "/routed-bike/route/v1/bike/"),
    ],
)
def test_keyless_snap_routes_through_osrm(osrm_server, sport, path_prefix) -> None:
    route, distance, snapped, readiness = ors_client.snap_route_detailed(
        WAYPOINTS, sport=sport, closed=True
    )

    assert snapped is True
    assert route[0] == route[-1]
    assert distance >= 1234.0
    assert readiness.status == "unavailable"
    request = osrm_server.requests[0]
    assert request.url.host == "osrm.test"
    assert request.url.path.startswith(path_prefix)
    assert request.url.params["overview"] == "full"
    assert request.url.params["continue_straight"] == "false"
    via_count = len(request.url.path.rsplit("/", 1)[1].split(";"))
    assert request.url.params["radiuses"] == ";".join(["120"] * via_count)
    assert "Authorization" not in request.headers


def test_osrm_start_direction_becomes_a_bearing_on_the_first_point(osrm_server) -> None:
    ors_client.snap_route_detailed(WAYPOINTS, start_direction_deg=450.0)

    request = osrm_server.requests[0]
    via_count = len(request.url.path.rsplit("/", 1)[1].split(";"))
    assert request.url.params["bearings"] == "90,45" + ";" * (via_count - 1)


def test_osrm_no_segment_widens_the_radius_like_ors(osrm_server) -> None:
    osrm_server.responses.append(
        httpx.Response(400, json={"code": "NoSegment", "message": "Could not find a matching segment"})
    )

    _, _, snapped, _ = ors_client.snap_route_detailed(WAYPOINTS)

    assert snapped is True
    radii = [request.url.params["radiuses"].split(";")[0] for request in osrm_server.requests]
    assert radii == ["120", "200"]


def test_osrm_rate_limit_stops_retrying(osrm_server) -> None:
    osrm_server.responses.extend(httpx.Response(429, text="Too Many Requests") for _ in range(7))

    route, _, snapped, _ = ors_client.snap_route_detailed(WAYPOINTS)

    assert snapped is False
    assert len(osrm_server.requests) == 1
    assert len(route) >= 2


def test_preflight_snap_is_skipped_without_ors(osrm_server) -> None:
    assert ors_client.preflight_route_candidates([WAYPOINTS], closed=False) is None
    assert osrm_server.requests == []


def test_no_backend_keeps_the_unrouted_straight_line_contract(monkeypatch) -> None:
    routing = _routing()
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))

    _, _, snapped, _ = ors_client.snap_route_detailed(WAYPOINTS)

    assert snapped is False


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "NoRoute"},
        {"code": "Ok", "routes": []},
        {"code": "Ok", "routes": [{"geometry": {"type": "Point", "coordinates": [1, 2]}}]},
        _osrm_ok([[19.0, 47.0], [19.0, 47.0]]),
    ],
    ids=["no-route", "no-routes", "not-a-line", "zero-length"],
)
def test_osrm_rejects_responses_without_a_connected_route(payload) -> None:
    assert isinstance(osrm_client.parse_route_response(payload), routing_backend.RouteFailure)


def test_osrm_distance_never_under_reports_the_geometry() -> None:
    result = osrm_client.parse_route_response(_osrm_ok([[19.0, 47.0], [19.0, 47.01]], distance=1.0))

    assert isinstance(result, routing_backend.RoutedPath)
    assert result.distance_m == pytest.approx(1112, rel=0.01)


def test_public_osrm_requests_are_spaced_for_fair_use(monkeypatch) -> None:
    clock = {"now": 100.0}
    sleeps: list[float] = []
    monkeypatch.setattr(osrm_client.time, "monotonic", lambda: clock["now"])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(osrm_client.time, "sleep", sleep)
    monkeypatch.setattr(osrm_client, "_last_request_at", 0.0)

    osrm_client._wait_for_fair_use_slot(1.0)
    clock["now"] += 0.25
    osrm_client._wait_for_fair_use_slot(1.0)

    assert sleeps == [pytest.approx(0.75)]
