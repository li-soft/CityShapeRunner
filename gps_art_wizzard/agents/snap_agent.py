"""SnapAgent: snap drawn waypoints to real roads via OpenRouteService."""

from __future__ import annotations

from ..config import get_settings
from ..state import SnappedRoute, WorkflowState
from ..tools import ors_client
from ..tools.routing_backend import select_routing_backend
from .base import BaseAgent


class SnapAgent(BaseAgent):
    name = "snap"

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.route_draft is None or state.intent is None:
            raise RuntimeError("road snapping requires route draft and intent")
        # Clear stale snap errors from previous iterations. If this snap
        # succeeds, any prior "ORS routing failed" error is obsolete.
        state.errors = [e for e in state.errors if not e.startswith("snap:")]
        draft = state.route_draft
        direct_guides = state.pending_direct_guides
        direct_budget = state.pending_direct_guide_budget if direct_guides is not None else None
        state.pending_direct_guides = None
        state.pending_direct_guide_budget = None
        waypoints = direct_guides if direct_guides is not None else draft.waypoints
        if draft.feature_repair and direct_guides is None:
            from ..tools.feature_tracking import feature_repair_guides
            waypoints = feature_repair_guides(state)
        state.candidate_count += 1
        if len(waypoints) < 2:
            state.errors.append("snap: fewer than 2 waypoints to snap")
            state.snapped = SnappedRoute(points=list(waypoints), total_distance_m=0.0, snapped=False)
            return state

        original_guides = waypoints
        from ..tools.street_graph import graph_for_state, shape_route
        graph = graph_for_state(state) if direct_guides is None else None
        if graph is not None:
            proposal = shape_route(
                graph, ors_client._subsample(waypoints, closed=draft.closed, max_points=18),
                target_m=draft.target_distance_km * 1000 if draft.target_distance_km else None,
                closed=draft.closed, seconds=get_settings().routing.shape_graph_seconds,
            )
            state.history.append({"agent": "snap", "graph_proposal": proposal is not None,
                                  "graph_revision": graph.revision,
                                  "graph_expansions": proposal.expanded if proposal else None})
            if proposal is not None:
                waypoints = proposal.points
                # Preserve explicit user start/end anchors in the provider
                # request; graph nearest-node offsets cannot replace them.
                if draft.anchored_start is not None:
                    waypoints = [original_guides[0], *waypoints]
                    if draft.closed:
                        waypoints.append(original_guides[0])

        polyline, dist_m, snapped, readiness = ors_client.snap_route_detailed(
            waypoints,
            sport=state.intent.sport,
            closed=draft.closed,
            route_preferences=state.route_preferences,
            start_direction_deg=draft.preferred_start_direction_deg,
            **({"guide_budget": 50} if waypoints is not original_guides else
               {"guide_budget": direct_budget} if direct_budget is not None else {}),
        )
        if snapped and waypoints is not original_guides:
            state.pending_direct_guides = list(original_guides)
        if not snapped and waypoints is not original_guides:
            polyline, dist_m, snapped, readiness = ors_client.snap_route_detailed(
                original_guides, sport=state.intent.sport, closed=draft.closed,
                route_preferences=state.route_preferences,
                start_direction_deg=draft.preferred_start_direction_deg,
            )
        # Preserve every Directions vertex. Even metre-space simplification
        # can cut a corner across a building; shape improvement must change
        # routing guides, never rewrite the measured street geometry.
        state.snapped = SnappedRoute(
            points=polyline,
            total_distance_m=dist_m,
            snapped=snapped,
            readiness=readiness,
        )
        if not snapped and select_routing_backend(get_settings().routing) != "none":
            state.errors.append(
                "snap: ORS routing failed; route is straight-line, not on roads"
            )
        self._record(
            state,
            f"snapped={snapped} pts={len(polyline)} dist={dist_m/1000:.2f}km; geometry preserved",
        )
        return state
