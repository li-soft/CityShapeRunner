"""Bounded shape-aware graph proposals, always validated through Directions.

OSM node identity preserves bridge/tunnel topology. This local graph is a search
accelerator, not an alternative export authority. Unhandled conditional/via-way
restrictions conservatively remove their affected ways. ORS still validates the
actual activity route, including jurisdiction-specific access rules.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..state import RoutePreferences, WorkflowState
from ..workflow_runtime import active_workflow_runtime, check_cancellation, record_routing_request
from . import geo, osm_data
from .routing_backend import select_routing_backend


@dataclass(frozen=True)
class Edge:
    target: int
    way: int
    length: float


@dataclass
class StreetGraph:
    nodes: dict[int, geo.LatLon]
    edges: dict[int, list[Edge]]
    forbidden: set[tuple[int, int, int]] = field(default_factory=set)
    only: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    revision: str = ""
    components: dict[int, int] = field(default_factory=dict)
    no_uturn: set[tuple[int, int]] = field(default_factory=set)
    only_uturn: set[tuple[int, int]] = field(default_factory=set)
    # Graphs are immutable after construction. Bound the derived frame cache;
    # local repairs repeatedly query the same start without changing any OSM data.
    _frames: OrderedDict = field(default_factory=OrderedDict, repr=False, compare=False)
    _frame_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _node_ids: list[int] = field(default_factory=list, init=False, repr=False, compare=False)
    _node_coordinates: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)

    def projection(self, center: geo.LatLon, *, with_nodes: bool = True):
        with self._frame_lock:
            cached = self._frames.get(center)
            if cached is not None:
                self._frames.move_to_end(center)
                if not with_nodes or cached[2] is not None:
                    return cached
            if self._node_coordinates is None:
                self._node_ids = list(self.nodes)
                self._node_coordinates = np.asarray(list(self.nodes.values()), dtype=float)
            ids, points = self._node_ids, self._node_coordinates
        # Same arithmetic and order as latlon_to_unit(..., scale_m=1).
        # Vectorise only the independent node projections, retaining Python
        # float pairs in the search so its edge-cost arithmetic stays exact.
        coordinates = cached[1] if cached is not None else np.column_stack((
            np.radians(points[:, 1] - center[1]) * geo.EARTH_R_M * math.cos(math.radians(center[0])),
            np.radians(points[:, 0] - center[0]) * geo.EARTH_R_M,
        ))
        node_xy = dict(zip(ids, (tuple(point) for point in coordinates.tolist()), strict=True)) if with_nodes else None
        frame = (ids, coordinates, node_xy)
        with self._frame_lock:
            # Another worker may already have materialised the node mapping.
            present = self._frames.get(center)
            if present is not None and present[2] is not None:
                return present
            self._frames[center] = frame
            self._frames.move_to_end(center)
            while len(self._frames) > 4:
                self._frames.popitem(last=False)
        return frame

    def turn_allowed(self, incoming: int, via: int, outgoing: int, previous: int = -1, target: int = -2) -> bool:
        if (incoming, via) in self.no_uturn and previous == target:
            return False
        if (incoming, via) in self.only_uturn and (previous != target or outgoing != incoming):
            return False
        return ((incoming, via, outgoing) not in self.forbidden
                and ((incoming, via) not in self.only or outgoing in self.only[incoming, via]))

    def nearest(self, points: list[geo.LatLon], *, radius: float = 150, count: int = 3,
                common_component: bool = False) -> list[list[int]]:
        if not self.nodes or not points:
            return [[] for _ in points]
        center = points[0]
        ids, coordinates, _ = self.projection(center, with_nodes=False)
        ranked = []
        component_distances = []
        for point in points:
            xy = geo.latlon_to_unit(*point, *center, 1)
            distances = np.hypot(coordinates[:, 0] - xy[0], coordinates[:, 1] - xy[1])
            # Only in-radius nodes can be candidates. Sorting the whole city
            # for every guide is expensive during bounded graph polish; this
            # preserves the same distance ordering for eligible nodes.
            eligible = np.flatnonzero(distances <= radius)
            indices = eligible[np.argsort(distances[eligible])].tolist()
            ranked.append(indices)
            nearest_components: dict[int, float] = {}
            if common_component and self.components:
                for i in indices:
                    component = self.components.get(ids[i], ids[i])
                    nearest_components.setdefault(component, float(distances[i]))
            component_distances.append(nearest_components)
        selected_component = None
        if common_component and component_distances:
            shared = set(component_distances[0]).intersection(*component_distances[1:])
            if shared:
                selected_component = min(shared, key=lambda c: (
                    sum(row[c] for row in component_distances), c,
                ))
        # Nearby isolated paths must not consume every endpoint slot while a
        # connected alternative exists inside the same radius. Weak-component
        # membership is only a filter: directed edges and turns are still
        # checked by the actual search. With no shared component retain normal
        # nearest results so the proxy can distinguish disconnection from an
        # out-of-snapshot/missing-node result.
        return [[ids[i] for i in indices
                 if selected_component is None or self.components.get(ids[i], ids[i]) == selected_component][:count]
                for indices in ranked]


def _allowed(tags: dict, sport: str, preferences: RoutePreferences) -> bool:
    mode = "bicycle" if sport == "bike" else "foot"
    highway = tags.get("highway", "")
    if highway in {"motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed", "raceway"}:
        return False
    if any(key.endswith(":conditional") for key in tags):
        return False
    access = tags.get(mode, tags.get("vehicle", tags.get("access", "yes")) if sport == "bike" else tags.get("access", "yes"))
    if access not in {"yes", "designated", "permissive", "official"}:
        return False
    if sport == "bike" and highway in {"steps", "footway", "pedestrian"} and tags.get("bicycle") not in {"yes", "designated", "permissive"}:
        return False
    if preferences.avoid_steps and highway == "steps":
        return False
    if preferences.avoid_fords and tags.get("ford") in {"yes", "stepping_stones"}:
        return False
    return True


def build_graph(elements: list[dict], sport: str = "run", preferences: RoutePreferences | None = None) -> StreetGraph:
    preferences = preferences or RoutePreferences()
    mode = "bicycle" if sport == "bike" else "foot"
    nodes = {}
    blocked_nodes = set()
    forbidden: set[tuple[int, int, int]] = set()
    no_uturn: set[tuple[int, int]] = set()
    only_uturn: set[tuple[int, int]] = set()
    only: dict[tuple[int, int], set[int]] = {}
    blocked_ways = set()
    for item in elements:
        if item.get("type") == "node":
            try:
                point = (float(item["lat"]), float(item["lon"]))
                if not all(map(math.isfinite, point)) or not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180):
                    continue
                node = int(item["id"])
                nodes[node] = point
                tags = item.get("tags", {})
                access = tags.get(mode, tags.get("access", "yes"))
                if access not in {"yes", "designated", "permissive", "official"} or (tags.get("barrier") and mode not in tags):
                    blocked_nodes.add(node)
            except (KeyError, TypeError, ValueError):
                continue
        if item.get("type") != "relation":
            continue
        tags = item.get("tags", {})
        if not str(tags.get("type", "")).startswith("restriction"):
            continue
        if mode in tags.get("except", "").split(";"):
            continue
        restriction = tags.get("restriction:" + mode, tags.get("restriction", "") if sport == "bike" else "")
        conditional = "restriction:" + mode + ":conditional" in tags or (sport == "bike" and "restriction:conditional" in tags)
        if not restriction and not conditional:
            continue
        members = item.get("members", [])
        origins = [m["ref"] for m in members if m.get("role") == "from" and m.get("type") == "way"]
        targets = [m["ref"] for m in members if m.get("role") == "to" and m.get("type") == "way"]
        via = [m["ref"] for m in members if m.get("role") == "via" and m.get("type") == "node"]
        if conditional or len(via) != 1 or any(m.get("role") == "via" and m.get("type") == "way" for m in members):
            blocked_ways.update(origins)
            continue
        for origin in origins:
            if restriction in {"no_u_turn", "only_u_turn"} and targets == [origin]:
                (no_uturn if restriction == "no_u_turn" else only_uturn).add((origin, via[0]))
            elif restriction.startswith("only_"):
                only.setdefault((origin, via[0]), set()).update(targets)
            else:
                forbidden.update((origin, via[0], target) for target in targets)
    edges: dict[int, list[Edge]] = {}
    for item in elements:
        if item.get("type") != "way" or item.get("id") in blocked_ways:
            continue
        tags = item.get("tags", {})
        if not tags.get("highway") or not _allowed(tags, sport, preferences):
            continue
        refs = item.get("nodes", [])
        if len(refs) < 2:
            continue
        way = int(item["id"])
        oneway = tags.get("oneway:" + mode)
        if oneway is None:
            oneway = tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no") if sport == "bike" else "no"
        for a, b in zip(refs, refs[1:], strict=False):
            if a not in nodes or b not in nodes or a in blocked_nodes or b in blocked_nodes or a == b:
                continue
            length = geo.haversine(*nodes[a], *nodes[b])
            if length < 0.01:
                continue
            if oneway != "-1":
                edges.setdefault(a, []).append(Edge(b, way, length))
            if oneway not in {"yes", "1", "true"}:
                edges.setdefault(b, []).append(Edge(a, way, length))
    used = set(edges) | {edge.target for outgoing in edges.values() for edge in outgoing}
    graph = StreetGraph({node: nodes[node] for node in sorted(used)}, edges, forbidden, only,
                        hashlib.sha256(json.dumps(elements, sort_keys=True).encode()).hexdigest())
    graph.no_uturn = no_uturn
    graph.only_uturn = only_uturn
    undirected: dict[int, set[int]] = {}
    for node, outgoing in edges.items():
        for edge in outgoing:
            undirected.setdefault(node, set()).add(edge.target)
            undirected.setdefault(edge.target, set()).add(node)
    for node in graph.nodes:
        if node in graph.components:
            continue
        stack = [node]
        graph.components[node] = node
        while stack:
            current = stack.pop()
            for neighbour in undirected.get(current, ()):
                if neighbour not in graph.components:
                    graph.components[neighbour] = node
                    stack.append(neighbour)
    return graph


_CACHE: OrderedDict[tuple, tuple[float, StreetGraph | None]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def clear_graph_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def graph_for_state(state: WorkflowState) -> StreetGraph | None:
    cfg = get_settings().routing
    if not cfg.shape_graph_enabled or state.route_draft is None or state.intent is None:
        return None
    snapshot = cfg.shape_graph_snapshot
    # Graph proposals only help when a street router can validate them.
    if not snapshot and (osm_data.offline_mode() or select_routing_backend(cfg) == "none"):
        return None
    draft = state.route_draft
    # A frozen city graph can be supplied for reproducible runs. Online areas
    # are quantized to share graph fetches across nearby placements.
    if state.plan and state.plan.city_bbox and state.map_placement is None:
        south, north, west, east = state.plan.city_bbox
        bbox = (south, west, north, east)
    else:
        bbox = osm_data.route_bbox(draft.waypoints, pad_m=1000)
    bbox = (math.floor(bbox[0] * 50) / 50, math.floor(bbox[1] * 50) / 50,
            math.ceil(bbox[2] * 50) / 50, math.ceil(bbox[3] * 50) / 50)
    if not snapshot and osm_data.bbox_span_km(bbox) > 25:
        bbox = osm_data.route_bbox(draft.waypoints, pad_m=1500)
        bbox = (math.floor(bbox[0] * 50) / 50, math.floor(bbox[1] * 50) / 50,
                math.ceil(bbox[2] * 50) / 50, math.ceil(bbox[3] * 50) / 50)
        if osm_data.bbox_span_km(bbox) > 25:
            return None
    prefs = state.route_preferences
    file_revision = Path(snapshot).stat().st_mtime_ns if snapshot and Path(snapshot).is_file() else None
    key = (snapshot, file_revision, None if snapshot else bbox, state.intent.sport, prefs.avoid_steps, prefs.avoid_fords)
    # One download/build per region at a time, including concurrent generation
    # workers. Failed fetches have a short negative cache to prevent fan-out.
    with _CACHE_LOCK:
        found = _CACHE.get(key)
        if found and found[0] > time.monotonic():
            _CACHE.move_to_end(key)
            return found[1]
        graph = None
        try:
            if snapshot:
                payload = json.loads(Path(snapshot).read_text(encoding="utf-8"))
                elements = payload["elements"]
            else:
                clause = osm_data._bbox_clause(bbox)
                query = f'[out:json][timeout:12];way["highway"]{clause}->.roads;( .roads;node(w.roads);rel(bw.roads)["type"="restriction"];);out body;'
                record_routing_request("graph_overpass")
                elements = osm_data.overpass_query(query, cache_key="shape-graph:" + str(bbox))
            if isinstance(elements, list) and len(elements) <= 80000:
                graph = build_graph(elements, state.intent.sport, prefs)
                if not graph.nodes:
                    graph = None
        except (OSError, ValueError, TypeError, KeyError, osm_data.OsmUnavailable):
            graph = None
        _CACHE[key] = (time.monotonic() + (3600 if graph else 60), graph)
        while len(_CACHE) > 6 or sum(len(entry.nodes) for _, entry in _CACHE.values() if entry) > 100000:
            _CACHE.popitem(last=False)
        return graph


@dataclass
class GraphProposal:
    points: list[geo.LatLon]
    distance_m: float
    cost: float
    expanded: int
    revision: str


def shape_route(graph: StreetGraph, guides: list[geo.LatLon], *, target_m: float | None = None,
                closed: bool = False, seconds: float = 2, max_expansions: int = 30000,
                reuse_penalty: float = 0) -> GraphProposal | None:
    """Layered beam search: each layer advances to the next contour guide.

    Each Dijkstra state includes the incoming way and previous node, preserving
    turn restrictions and reversal costs across guide boundaries. Per-edge cost
    includes distance, contour displacement and direction. A length-diverse beam
    retains different endpoint/length choices for the final distance objective.
    Optional reuse cost proposes an alternative when measured street geometry
    has excess reversals; it never forbids an intentional repeated stroke.
    """
    if len(guides) < 2 or not graph.nodes:
        return None
    deadline = time.monotonic() + max(0.01, seconds)
    nearest = graph.nearest(guides, common_component=True)
    if any(not nodes for nodes in nearest):
        return None
    center = guides[0]
    _, _, xy = graph.projection(center)
    guide_xy = [geo.latlon_to_unit(*point, *center, 1) for point in guides]
    # Distinct legal exits can matter at a restricted guide: the cheapest
    # arrival may allow only a dead end, while another incoming way continues
    # along the drawing. Unrestricted guides retain their existing search.
    restricted = ({via for _, via, _ in graph.forbidden}
                  | {via for _, via in graph.only}
                  | {via for _, via in graph.no_uturn}
                  | {via for _, via in graph.only_uturn})
    restricted.intersection_update(node for group in nearest[1:-1] for node in group)

    def exits(node: int, incoming_way: int, previous_node: int) -> frozenset[tuple[int, int]]:
        return frozenset((edge.way, edge.target) for edge in graph.edges.get(node, ())
                         if graph.turn_allowed(incoming_way, node, edge.way, previous_node, edge.target))

    arrival_options: dict[int, set[frozenset[tuple[int, int]]]] = {}
    if restricted:
        for previous_node, outgoing in graph.edges.items():
            for edge in outgoing:
                if edge.target in restricted:
                    allowed = exits(edge.target, edge.way, previous_node)
                    if allowed:
                        arrival_options.setdefault(edge.target, set()).add(allowed)
    # cost, length, path, incoming way, previous node
    beam = [(6 * geo.haversine(*graph.nodes[node], *guides[0]), 0.0, [node], -1, -1) for node in nearest[0]]
    expanded = 0
    for index in range(1, len(guides)):
        start_xy, end_xy = guide_xy[index - 1], guide_xy[index]
        dx, dy = end_xy[0] - start_xy[0], end_xy[1] - start_xy[1]
        segment_length = max(1.0, math.hypot(dx, dy))
        corridor = max(25.0, min(120.0, segment_length * 0.3))
        edge_costs: dict[tuple[int, Edge], tuple[float, float] | None] = {}
        next_beam = []
        for prior_cost, prior_length, prior_path, incoming, previous in beam:
            used_edges = {frozenset((a, b)) for a, b in zip(prior_path, prior_path[1:], strict=False)} if reuse_penalty else set()
            targets = {prior_path[0]} if closed and index == len(guides) - 1 else set(nearest[index])
            initial = (prior_path[-1], incoming, previous)
            distances = {initial: 0.0}
            parents: dict[tuple[int, int, int], tuple[int, int, int]] = {}
            lengths = {initial: 0.0}
            heap = [(0.0, initial)]
            reached: set[int] = set()
            arrivals: dict[int, set[frozenset[tuple[int, int]]]] = {}
            while heap and len(reached) < len(targets):
                cost, current = heapq.heappop(heap)
                if cost != distances.get(current):
                    continue
                node, from_way, prev = current
                expanded += 1
                if expanded > max_expansions or time.monotonic() > deadline:
                    return None
                arrival_signature = exits(node, from_way, prev) if node in restricted else frozenset()
                if node in targets and node not in reached and arrival_signature not in arrivals.get(node, set()):
                    # A guide is an intermediate stop, not the final goal.
                    # Do not let the cheapest arrival close this layer when
                    # its turn restrictions make every continuation illegal.
                    # Dijkstra still holds other incoming-way states, which
                    # may reach the same guide with a legal onward movement.
                    if index < len(guides) - 1 and not any(
                        graph.turn_allowed(from_way, node, edge.way, prev, edge.target)
                        for edge in graph.edges.get(node, ())
                    ):
                        continue
                    arrivals.setdefault(node, set()).add(arrival_signature)
                    quota = min(2, len(arrival_options.get(node, ()))) if node in restricted else 1
                    if len(arrivals[node]) >= quota:
                        reached.add(node)
                    tail = [node]
                    cursor = current
                    while cursor in parents:
                        cursor = parents[cursor]
                        tail.append(cursor[0])
                    tail.reverse()
                    endpoint_error = 6 * geo.haversine(*graph.nodes[node], *guides[index])
                    next_beam.append((prior_cost + cost + endpoint_error, prior_length + lengths[current],
                                      prior_path + tail[1:], from_way, prev))
                for edge in graph.edges.get(node, ()):
                    if not graph.turn_allowed(from_way, node, edge.way, prev, edge.target):
                        continue
                    key = (node, edge)
                    if key not in edge_costs:
                        a, b = xy[node], xy[edge.target]
                        vx, vy = b[0] - a[0], b[1] - a[1]
                        midpoint = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                        t = max(0.0, min(1.0, ((midpoint[0] - start_xy[0]) * dx + (midpoint[1] - start_xy[1]) * dy) / segment_length**2))
                        deviation = math.hypot(midpoint[0] - start_xy[0] - t * dx, midpoint[1] - start_xy[1] - t * dy)
                        alignment = (vx * dx + vy * dy) / max(1.0, math.hypot(vx, vy) * segment_length)
                        factor = 1 + 3 * (deviation / corridor)**2 + 0.8 * (1 - alignment)
                        edge_costs[key] = (factor, alignment) if deviation <= max(400.0, segment_length * 1.5) else None
                    cached_cost = edge_costs[key]
                    if cached_cost is None:
                        continue
                    factor, alignment = cached_cost
                    reversal = 2.0 if edge.target == prev and alignment < 0.25 else 0.0
                    weight = edge.length * (factor + reversal)
                    if reuse_penalty and frozenset((node, edge.target)) in used_edges:
                        weight += reuse_penalty * edge.length
                    neighbour = (edge.target, edge.way, node)
                    total = cost + weight
                    if total < distances.get(neighbour, math.inf):
                        distances[neighbour] = total
                        lengths[neighbour] = lengths[current] + edge.length
                        parents[neighbour] = current
                        heapq.heappush(heap, (total, neighbour))
        if not next_beam:
            return None
        next_beam.sort(key=lambda row: row[0])
        beam = []
        signatures = set()
        for candidate in next_beam:
            signature = (candidate[2][-1], candidate[3], candidate[4], round(candidate[1] / 100))
            if signature not in signatures:
                signatures.add(signature)
                beam.append(candidate)
            if len(beam) == 4:
                break
    cost, length, path, _, _ = min(beam, key=lambda row: row[0] + (3 * abs(row[1] - target_m) if target_m else 0))
    if len(path) < 2:
        return None
    return GraphProposal([graph.nodes[node] for node in path], length, cost, expanded, graph.revision)


def contour_support(
    graph: StreetGraph, routes: list[list[geo.LatLon]], *, radius_m: float,
    cache: dict[tuple, float] | None = None,
) -> list[float]:
    """Cheap contour/edge alignment for placement sampling, never routing evidence."""
    from .shape_similarity import resample

    check_cancellation()
    runtime = active_workflow_runtime()
    keys = [(id(graph), radius_m, tuple(route)) for route in routes]
    if cache is not None and all(key in cache for key in keys):
        return [cache[key] for key in keys]
    if not graph.nodes:
        return [0.0] * len(routes)
    center = next(iter(graph.nodes.values()))
    _, _, xy = graph.projection(center)
    segments = np.asarray([(xy[node], xy[edge.target]) for node, edges in graph.edges.items() for edge in edges])
    if not len(segments):
        return [0.0] * len(routes)
    starts, ends = segments[:, 0], segments[:, 1]
    vectors = ends - starts
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    minimum, maximum = np.minimum(starts, ends), np.maximum(starts, ends)
    radius = max(1.0, radius_m)
    scores = []
    for route, key in zip(routes, keys, strict=True):
        check_cancellation()
        if runtime is not None and runtime.deadline_exceeded():
            # An incomplete prefix must not bias selection against the rows
            # that happened to fit in the optional exploration budget.
            return [0.0] * len(routes)
        if cache is not None and key in cache:
            scores.append(cache[key])
            continue
        if len(route) < 2:
            scores.append(0.0)
            continue
        outline = np.asarray([geo.latlon_to_unit(*point, *center, 1) for point in route])
        active = (np.all(maximum >= outline.min(axis=0) - radius, axis=1)
                  & np.all(minimum <= outline.max(axis=0) + radius, axis=1) & (lengths > 0))
        if not np.any(active):
            scores.append(0.0)
            continue
        a, v, length = starts[active], vectors[active], lengths[active]
        squared_length = length ** 2
        points = resample(outline, 64)
        costs = []
        for first, second in zip(points, points[1:], strict=False):
            check_cancellation()
            tangent = second - first
            tangent_length = float(np.linalg.norm(tangent))
            if tangent_length == 0:
                continue
            midpoint = (first + second) / 2
            projection = np.clip(np.sum((midpoint - a) * v, axis=1) / squared_length, 0, 1)
            distance = np.linalg.norm(midpoint - a - projection[:, None] * v, axis=1)
            alignment = np.clip(np.sum(v * tangent, axis=1) / (length * tangent_length), -1, 1)
            # Same displacement/direction costs as the full graph proposal,
            # without a shortest-path search for every sampled placement.
            costs.append(float(np.min(3 * (distance / radius) ** 2 + 0.8 * (1 - alignment))))
        scores.append(1 / (1 + float(np.mean(costs))) if costs else 0.0)
    if cache is not None:
        cache.update(zip(keys, scores, strict=True))
    return scores


def connectivity_proxy(graph: StreetGraph, guides: list[geo.LatLon], *, max_expansions: int = 1500) -> tuple[float | None, float | None]:
    """Bounded directed transition evidence. Unknown is distinct from failure."""
    nearest = graph.nearest(guides, count=2)
    if any(not nodes for nodes in nearest):
        return None, None  # outside snapshot / no nearby nodes
    total = 0.0
    expanded = 0
    for starts, ends in zip(nearest, nearest[1:], strict=False):
        if not {graph.components[node] for node in starts} & {graph.components[node] for node in ends}:
            return 0.0, None
        heap = [(0.0, (node, -1, -1)) for node in starts]
        heapq.heapify(heap)
        costs = {key: value for value, key in heap}
        found = None
        while heap:
            distance, (node, way, previous) = heapq.heappop(heap)
            if costs.get((node, way, previous)) != distance:
                continue
            if node in ends:
                found = distance
                break
            expanded += 1
            if expanded > max_expansions:
                return None, None
            for edge in graph.edges.get(node, ()):
                if not graph.turn_allowed(way, node, edge.way, previous, edge.target):
                    continue
                key = (edge.target, edge.way, node)
                candidate = distance + edge.length
                if candidate < costs.get(key, math.inf):
                    costs[key] = candidate
                    heapq.heappush(heap, (candidate, key))
        if found is None:
            return 0.0, None
        total += found
    return 1.0, total / max(1.0, geo.path_distance_m(guides))
