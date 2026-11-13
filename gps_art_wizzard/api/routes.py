"""HTTP routes for the GPS art route planner."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextvars import copy_context
from dataclasses import asdict, replace
from threading import Event, Thread
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from ..agents.intent_agent import IntentAgent
from ..agents.placement_agent import estimated_scale_m
from ..agents.validation_agent import ValidationAgent
from ..config import get_settings
from ..logging_config import current_request_id
from ..orchestrator import generate
from ..quality import quality_gate_report, route_quality_rank
from ..state import (
    EvaluatedCandidate,
    Intent,
    MapPlacement,
    RouteDraft,
    RoutePreferences,
    Shape,
    SnappedRoute,
    WorkflowState,
)
from ..tools import (
    cloudinary_gallery,
    geo,
    geocoder,
    gpx_writer,
    image_reference,
    ors_client,
    route_safety,
    shape_library,
    shape_similarity,
)
from ..workflow_runtime import WorkflowCancelledError, cancellation_scope

router = APIRouter()
log = logging.getLogger(__name__)

PROMPT_MAX_LENGTH = 320


class IntentOverrideRequest(BaseModel):
    """A user-confirmed correction to the natural-language interpretation."""

    shape: str | None = Field(default=None, max_length=80)
    text: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    sport: Literal["run", "walk", "bike"] = "run"
    distance_km: float | None = Field(default=None, gt=0, le=500)
    style: str | None = Field(default=None, max_length=80)
    suggest: bool = False

    @field_validator("shape", "text", "city", "style")
    @classmethod
    def normalise_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class StartPointRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    label: str | None = Field(default=None, max_length=180)


class RoutePreferencesRequest(BaseModel):
    avoid_steps: bool = False
    avoid_ferries: bool = False
    avoid_fords: bool = False
    prefer_quiet: bool = False
    prefer_green: bool = False


class MapPlacementRequest(BaseModel):
    center_lat: float = Field(..., ge=-85, le=85)
    center_lon: float = Field(..., ge=-180, le=180)
    scale_m: float = Field(..., ge=100, le=50_000)
    rotation_deg: float = Field(default=0.0, ge=-360, le=360)
    search_radius_m: float = Field(default=900.0, ge=100, le=4_000)


class GenerateRequest(BaseModel):
    search_seed: int | None = Field(default=None, ge=0, le=4_294_967_295, strict=True)
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=PROMPT_MAX_LENGTH,
        examples=["a heart run in Budapest, about 8km", "suggest a run in Debrecen, 10km"],
        description="Natural-language prompt describing the shape, city, sport, and optional target distance. "
        "Use 'suggest' to let AI pick the best shape for the city.",
    )
    intent_override: IntentOverrideRequest | None = None
    start_point: StartPointRequest | None = None
    start_address: str | None = Field(default=None, max_length=180)
    start_direction_deg: float | None = Field(default=None, ge=0, lt=360)
    route_preferences: RoutePreferencesRequest = Field(
        default_factory=RoutePreferencesRequest
    )
    reference_image_url: str | None = Field(default=None, max_length=2_048)
    reference_svg: str | None = Field(
        default=None,
        max_length=image_reference.MAX_SVG_BYTES,
        description="Raw text of an uploaded SVG drawing to route instead of a template.",
    )
    reference_svg_name: str | None = Field(default=None, max_length=180)
    map_placement: MapPlacementRequest | None = None

    @field_validator("prompt", mode="before")
    @classmethod
    def normalise_prompt(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalised = unicodedata.normalize("NFKC", value)
        if any(
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
            for character in normalised
        ):
            raise ValueError("remove unsupported control characters from the route idea")

        cleaned = " ".join(normalised.split())
        if not cleaned:
            raise ValueError("enter a route idea")
        if len(cleaned) > PROMPT_MAX_LENGTH:
            raise ValueError(
                f"keep the route idea to {PROMPT_MAX_LENGTH} characters or fewer"
            )
        if not any(character.isalnum() for character in cleaned):
            raise ValueError("include a shape, word, letter, or number to draw")
        return cleaned

    @field_validator("start_address")
    @classmethod
    def normalise_start_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
        return cleaned or None

    @field_validator("reference_image_url")
    @classmethod
    def normalise_reference_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = "".join(value.split()).strip()
        if not cleaned:
            return None
        if not cleaned.casefold().startswith(("http://", "https://")):
            raise ValueError("enter a public HTTP or HTTPS image URL")
        return cleaned

    @field_validator("reference_svg")
    @classmethod
    def require_svg_content(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("the uploaded SVG file is empty")
        return value

    @model_validator(mode="after")
    def one_reference_source(self) -> GenerateRequest:
        if self.reference_svg is not None and self.reference_image_url is not None:
            raise ValueError("choose either an uploaded SVG or an image link, not both")
        if self.reference_svg_name is not None and self.reference_svg is None:
            raise ValueError("an SVG file name needs the uploaded SVG content")
        return self

    @model_validator(mode="after")
    def one_start_source(self) -> GenerateRequest:
        if self.start_point is not None and self.start_address is not None:
            raise ValueError("choose either a current/map point or a start address")
        if self.map_placement is not None and (
            self.start_point is not None
            or self.start_address is not None
            or self.start_direction_deg is not None
        ):
            raise ValueError(
                "a positioned drawing cannot also use a separate start point or direction"
            )
        return self


class WorkflowLimitsResponse(BaseModel):
    optimization_budget_seconds: float | None = Field(default=None, ge=1)
    max_duration_seconds: float = Field(ge=1)
    max_llm_calls: int = Field(ge=-1)


class WorkflowStepMetricResponse(BaseModel):
    duration_ms: int = Field(ge=0)
    routing_requests: dict[str, int] = Field(default_factory=dict)


class WorkflowStepsResponse(BaseModel):
    attempts: dict[str, int] = Field(default_factory=dict)
    completed: list[str] = Field(default_factory=list)
    failures: int = Field(ge=0)
    dropped_events: int = Field(ge=0)
    metrics: dict[str, WorkflowStepMetricResponse] = Field(default_factory=dict)


class WorkflowAIResponse(BaseModel):
    attempts: int = Field(ge=0)
    successful_calls: int = Field(ge=0)
    deterministic_fallbacks: int = Field(ge=0)
    provider_attempts: dict[str, int] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)


class WorkflowSummaryResponse(BaseModel):
    run_id: str
    status: Literal["running", "completed", "needs_review", "failed"]
    mode: Literal["ai", "hybrid", "deterministic"]
    duration_ms: int | None = Field(default=None, ge=0)
    limits: WorkflowLimitsResponse
    steps: WorkflowStepsResponse
    ai: WorkflowAIResponse
    degraded_reasons: list[str] = Field(default_factory=list)
    error_category: Literal["input", "dependency", "quality", "internal"] | None = None


class GenerateResponse(BaseModel):
    search_seed: int = 0
    search_areas: int = 0
    request_id: str | None = None
    workflow: WorkflowSummaryResponse | None = None
    prompt: str
    intent: dict | None
    shape: dict | None
    suggested_shape: str | None = None
    suggestion_reason: str | None = None
    requested_shape: str | None = None
    fit_decision: dict | None = None
    validation: dict | None
    distance_km: float | None
    snapped: bool | None
    iterations: int
    candidate_count: int = 0
    preflight_count: int = 0
    below_threshold: bool
    errors: list[str]
    history: list[dict]
    gpx: str | None
    tcx: str | None
    file_paths: dict
    points_preview: list[list[float]]
    ideal_preview: list[list[float]] = Field(default_factory=list)
    landmark_preview: list[list[float]] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    candidate_audit: list[dict] = Field(default_factory=list)
    candidate_summary: dict = Field(default_factory=dict)
    preflight_candidates: list[dict] = Field(default_factory=list)
    street_canvas: list[dict] = Field(default_factory=list)
    route_verification: dict | None = None
    route_details: dict | None = None
    planning_options: dict = Field(default_factory=dict)
    gallery_publish_token: str | None = None


class InterpretResponse(BaseModel):
    prompt: str
    intent: dict
    drawing_label: str
    drawing_kind: Literal["template", "custom", "text", "suggestion"]
    defaults_applied: list[str] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    needs_clarification: bool = False
    clarifications: list[dict] = Field(default_factory=list)


class EditedRouteRequest(BaseModel):
    control_points: list[list[float]] = Field(..., min_length=2, max_length=200)
    reference_points: list[list[float]] = Field(default_factory=list, max_length=500)
    sport: Literal["run", "walk", "bike"] = "run"
    closed: bool = False
    target_distance_km: float | None = Field(default=None, gt=0, le=500)
    name: str = Field(default="Edited GPS art route", min_length=1, max_length=120)
    shape_name: str = Field(default="edited", min_length=1, max_length=80)
    route_preferences: RoutePreferencesRequest = Field(
        default_factory=RoutePreferencesRequest
    )

    @field_validator("name", "shape_name")
    @classmethod
    def normalise_label(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("route and shape names must not be blank")
        return cleaned

    @field_validator("control_points", "reference_points")
    @classmethod
    def validate_points(cls, value: list[list[float]]) -> list[list[float]]:
        cleaned: list[list[float]] = []
        for point in value:
            if not isinstance(point, list | tuple) or len(point) < 2:
                raise ValueError("each point must contain latitude and longitude")
            lat, lon = float(point[0]), float(point[1])
            if (
                not math.isfinite(lat)
                or not math.isfinite(lon)
                or not -90 <= lat <= 90
                or not -180 <= lon <= 180
            ):
                raise ValueError("route points must be finite latitude/longitude pairs")
            cleaned.append([lat, lon])
        return cleaned


class EditedRouteResponse(BaseModel):
    request_id: str | None = None
    points_preview: list[list[float]]
    distance_km: float
    snapped: bool
    below_recommended: bool
    validation: dict
    route_verification: dict
    route_details: dict
    gpx: str | None
    tcx: str | None
    warnings: list[str] = Field(default_factory=list)


class RouteAcceptanceRequest(BaseModel):
    generation_request_id: str | None = Field(default=None, max_length=80)
    route_id: str = Field(..., min_length=1, max_length=80)
    shape_name: str = Field(..., min_length=1, max_length=80)
    automatic_checks_passed: bool = False
    scientifically_verified: bool | None = Field(
        default=None,
        description="Deprecated compatibility field; use automatic_checks_passed.",
    )
    snapped: bool = False
    failed_gates: list[str] = Field(default_factory=list, max_length=20)
    score: float | None = Field(default=None, ge=0, le=1)
    shape_fidelity: float | None = Field(default=None, ge=0, le=1)
    distance_km: float | None = Field(default=None, ge=0, le=1_000)

    @field_validator("route_id", "shape_name")
    @classmethod
    def normalise_acceptance_label(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("route and shape identifiers must not be blank")
        return cleaned

    @field_validator("failed_gates")
    @classmethod
    def normalise_failed_gates(cls, value: list[str]) -> list[str]:
        return [" ".join(item.split())[:80] for item in value if item.strip()]

    @property
    def checks_passed(self) -> bool:
        """Return the current flag while accepting older UI payloads."""

        return self.automatic_checks_passed or bool(self.scientifically_verified)


_MAX_PREVIEW_POINTS = 500


def _even_sample(points: list, n: int) -> list:
    """Evenly sample ``points`` to at most ``n`` entries, retaining endpoints."""
    if n < 1:
        raise ValueError("sample size must be positive")
    if len(points) <= n:
        return list(points)
    if n == 1:
        return [points[0]]
    indices = [round(index * (len(points) - 1) / (n - 1)) for index in range(n)]
    return [points[i] for i in indices]


def _candidate_response_rank(
    candidate: EvaluatedCandidate,
    selected_shape: str | None,
) -> tuple[bool, bool, bool, bool, float, float, float]:
    """Rank routes by the same independent gates shown to the user.

    Aggregate score is intentionally only a late tie-breaker. Otherwise a
    high average can put a route with one failed recognition gate ahead of a
    route that satisfies every required check.
    """

    selected_shape_match = bool(
        selected_shape
        and candidate.shape_name.casefold() == selected_shape.casefold()
    )
    report = quality_gate_report(
        candidate.validation,
        closed=candidate.closed,
        candidate_shape=candidate.shape_name,
        selected_shape=selected_shape,
    )
    return (
        selected_shape_match,
        bool(report["passed"]),
        bool(candidate.validation.on_roads),
        *route_quality_rank(candidate.validation, closed=candidate.closed)[1:],
    )


def _route_details(
    *,
    validation,
    shape_name: str,
    shape_source: str,
    sport: str,
    snapped: bool,
    closed: bool,
    distance_km: float,
    target_distance_km: float | None,
    route_point_count: int,
    guide_point_count: int,
    transform: dict | None = None,
    readiness=None,
) -> dict:
    difference_km = (
        distance_km - target_distance_km
        if target_distance_km is not None
        else None
    )
    difference_percent = (
        100.0 * difference_km / target_distance_km
        if difference_km is not None and target_distance_km
        else None
    )
    return {
        "shape": {
            "name": shape_name,
            "source": shape_source,
            "closed": closed,
        },
        "routing": {
            "activity": sport,
            "street_matched": snapped,
            "route_point_count": route_point_count,
            "guide_point_count": guide_point_count,
            "closure_gap_m": validation.closure_gap_m,
        },
        "distance": {
            "actual_km": distance_km,
            "target_km": target_distance_km,
            "difference_km": difference_km,
            "difference_percent": difference_percent,
            "route_to_guide_ratio": validation.route_length_ratio,
        },
        "deviation": {
            "mean_outline_deviation_ratio": validation.mean_deviation_ratio,
        },
        "placement": transform or {},
        "readiness": asdict(readiness) if readiness is not None else None,
    }


def _is_connected_route_geometry(
    points: object,
    total_distance_m: object,
    *,
    snapped: bool,
) -> bool:
    """Validate the minimum public contract for a street-routed polyline."""

    return route_safety.is_provider_routed_geometry(
        points,
        total_distance_m,
        routed=snapped,
    )


def _has_connected_route(route: object | None) -> bool:
    if route is None or not bool(getattr(route, "snapped", False)):
        return False
    return _is_connected_route_geometry(
        getattr(route, "points", None),
        getattr(route, "total_distance_m", None),
        snapped=True,
    )


def _state_to_response(state) -> dict:
    snapped = state.snapped
    export = state.export
    primary_street_routed = _has_connected_route(snapped)
    all_pts = snapped.points if snapped else []
    # The map must show the same street geometry as the download. Sampling
    # vertices would create artificial straight shortcuts through corners.
    preview = [[p[0], p[1]] for p in all_pts]
    ideal_points = state.route_draft.waypoints if state.route_draft else []
    ideal_preview = [
        [point[0], point[1]]
        for point in _even_sample(ideal_points, _MAX_PREVIEW_POINTS)
    ]
    # The selected candidate usually shares the final guide geometry, so
    # landmarks and downloads are memoised per unique polyline instead of
    # being recomputed for the primary route and again per candidate.
    landmark_memo: dict[tuple, list] = {}
    download_memo: dict[tuple, tuple[str, str | None]] = {}

    def memoised_landmarks(points):
        key = tuple(points)
        if key not in landmark_memo:
            landmark_memo[key] = shape_similarity.salient_route_landmarks(points)
        return landmark_memo[key]

    def memoised_downloads(points, name, sport, total_distance_m):
        key = (tuple(points), name, sport, total_distance_m)
        if key not in download_memo:
            gpx = gpx_writer.to_gpx(
                points,
                name=name,
                sport=sport,
                total_distance_m=total_distance_m,
            )
            try:
                tcx = gpx_writer.to_tcx(
                    points,
                    name=name,
                    sport=sport,
                    total_distance_m=total_distance_m,
                )
            except Exception:  # noqa: BLE001
                tcx = None
            download_memo[key] = (gpx, tcx)
        return download_memo[key]

    landmark_preview = [
        [point[0], point[1]]
        for point in memoised_landmarks(ideal_points)
    ]
    selected_shape = state.shape.name if state.shape else None
    selected_shape_source = state.shape.source if state.shape else "unknown"
    sport = state.intent.sport if state.intent else "run"
    ranked_candidates = sorted(
        enumerate(state.candidates, start=1),
        key=lambda item: _candidate_response_rank(item[1], selected_shape),
        reverse=True,
    )
    candidates = []
    candidate_audit = []
    verified_count = 0
    review_count = 0
    other_shape_count = 0
    for original_index, candidate in ranked_candidates:
        validation = candidate.validation
        candidate_street_routed = _is_connected_route_geometry(
            candidate.points,
            candidate.total_distance_m,
            snapped=candidate.snapped,
        )
        verification_validation = (
            validation
            if candidate_street_routed or not validation.on_roads
            else replace(validation, on_roads=False)
        )
        verification = quality_gate_report(
            verification_validation,
            closed=candidate.closed,
            candidate_shape=candidate.shape_name,
            selected_shape=selected_shape,
        )
        transform = {
            "rotation_deg": candidate.rotation_deg,
            "scale_m": candidate.scale_m,
            "lat_offset_m": candidate.lat_offset_m,
            "lon_offset_m": candidate.lon_offset_m,
            "preflight_score": candidate.preflight_score,
        }
        distance_km = candidate.total_distance_m / 1000.0
        details = _route_details(
            validation=validation,
            shape_name=candidate.shape_name,
            shape_source=candidate.shape_source,
            sport=sport,
            snapped=candidate_street_routed,
            closed=candidate.closed,
            distance_km=distance_km,
            target_distance_km=validation.target_distance_km,
            route_point_count=len(candidate.points),
            guide_point_count=len(candidate.ideal_points),
            transform=transform,
            readiness=candidate.readiness,
        )
        candidate_id = f"candidate-{original_index}"
        selected_shape_match = bool(
            selected_shape
            and candidate.shape_name.casefold() == selected_shape.casefold()
        )
        decision = (
            "other_shape"
            if not selected_shape_match
            else "verified" if verification["passed"] else "review"
        )
        candidate_audit.append(
            {
                "id": candidate_id,
                "shape_name": candidate.shape_name,
                "selected_shape_match": selected_shape_match,
                "accepted": verification["passed"],
                "verified": verification["passed"],
                "decision": decision,
                "failed_gates": verification["failed_gates"],
                "score": validation.score,
                "shape_fidelity": validation.shape_fidelity,
                "distance_km": distance_km,
                "issues": list(validation.issues),
            }
        )
        log_method = log.info if verification["passed"] else log.warning
        log_method(
            (
                "Route candidate %s: shape=%s, selected-shape match=%s, "
                "decision=%s, street matched=%s, overall=%.1f%%, "
                "likeness=%.1f%%, distance=%.2f km, failed checks=%s."
            ),
            candidate_id,
            candidate.shape_name,
            "yes" if selected_shape_match else "no",
            decision,
            "yes" if candidate_street_routed else "no",
            validation.score * 100,
            validation.shape_fidelity * 100,
            distance_km,
            ", ".join(verification["failed_gates"]) or "none",
            extra={
                "event": "route.candidate.evaluated",
                "candidate_id": candidate_id,
                "shape": candidate.shape_name,
                "city": state.intent.city if state.intent else None,
                "sport": sport,
                "decision": decision,
                "verified": verification["passed"],
                "failed_gates": verification["failed_gates"],
                "selected_shape_match": selected_shape_match,
                "snapped": candidate_street_routed,
                "score": validation.score,
                "fidelity": validation.shape_fidelity,
                "distance_km": distance_km,
                "target_distance_km": validation.target_distance_km,
                "distance_fit": validation.distance_fit,
                "closure": validation.closure,
                "route_point_count": len(candidate.points),
                "guide_point_count": len(candidate.ideal_points),
                **transform,
            },
        )
        # Attempts for fallback/suggestion shapes remain in the audit, but the
        # selector shows only routes drawn from the final selected shape.
        if not selected_shape_match:
            other_shape_count += 1
            continue
        # A guide that did not route through the street graph is diagnostic
        # evidence only. Never expose it as a selectable/downloadable GPS route.
        if not candidate_street_routed:
            continue
        if verification["passed"]:
            verified_count += 1
        else:
            review_count += 1
        export_name = (
            f"{candidate.shape_name} in {state.intent.city or 'route'}"
            if state.intent
            else candidate.shape_name
        )
        candidate_gpx, candidate_tcx = memoised_downloads(
            candidate.points,
            export_name,
            sport,
            candidate.total_distance_m,
        )
        candidates.append(
            {
                "id": candidate_id,
                "shape_name": candidate.shape_name,
                "shape_source": candidate.shape_source,
                "points_preview": [
                    [point[0], point[1]]
                    for point in candidate.points
                ],
                "ideal_preview": [
                    [point[0], point[1]]
                    for point in _even_sample(candidate.ideal_points, _MAX_PREVIEW_POINTS)
                ],
                "landmark_preview": [
                    [point[0], point[1]]
                    for point in memoised_landmarks(candidate.ideal_points)
                ],
                "distance_km": distance_km,
                "snapped": candidate_street_routed,
                "closed": candidate.closed,
                "target_distance_km": validation.target_distance_km,
                "validation": validation.__dict__,
                "below_recommended": not verification["passed"],
                "verification_status": decision,
                "requires_user_acceptance": not verification["passed"],
                "verification": verification,
                "details": details,
                "transform": transform,
                "gpx": candidate_gpx,
                "tcx": candidate_tcx,
                "gallery_publish_token": (
                    cloudinary_gallery.maybe_issue_publish_token()
                    if candidate_street_routed
                    else None
                ),
            }
        )

    from ..tools.route_diversity import diverse_route_order
    candidates = diverse_route_order(candidates)

    route_verification = (
        quality_gate_report(
            state.validation,
            closed=bool(state.shape and state.shape.closed),
            candidate_shape=selected_shape,
            selected_shape=selected_shape,
        )
        if state.validation
        else None
    )
    route_details = (
        _route_details(
            validation=state.validation,
            shape_name=selected_shape or "unknown",
            shape_source=selected_shape_source,
            sport=sport,
            snapped=primary_street_routed,
            closed=bool(state.shape and state.shape.closed),
            distance_km=(snapped.total_distance_m / 1000.0) if snapped else 0.0,
            target_distance_km=state.validation.target_distance_km,
            route_point_count=len(snapped.points) if snapped else 0,
            guide_point_count=len(ideal_points),
            transform=(
                {
                    "rotation_deg": state.route_draft.rotation_deg,
                    "scale_m": state.route_draft.scale_m,
                    "lat_offset_m": state.route_draft.lat_offset_m,
                    "lon_offset_m": state.route_draft.lon_offset_m,
                    "preflight_score": state.route_draft.preflight_score,
                }
                if state.route_draft
                else {}
            ),
            readiness=snapped.readiness if snapped else None,
        )
        if state.validation
        else None
    )
    candidate_summary = {
        "selected_shape": selected_shape,
        "accepted_count": verified_count,
        "verified_count": verified_count,
        "review_count": review_count,
        "shown_count": len(candidates),
        "rejected_selected_shape_count": review_count,
        "other_shape_count": other_shape_count,
        "audited_count": len(candidate_audit),
        "full_route_attempt_count": state.candidate_count,
        "preflight_count": state.preflight_count,
    }
    street_canvas = []
    if state.route_draft:
        for rank, item in enumerate(
            sorted(
                state.preflight_candidates,
                key=lambda candidate: candidate.get("score", 0.0),
                reverse=True,
            )[:12],
            start=1,
        ):
            lat, lon = geo.unit_to_latlon(
                float(item.get("lon_offset_m", 0.0)),
                float(item.get("lat_offset_m", 0.0)),
                state.route_draft.center_lat,
                state.route_draft.center_lon,
                1.0,
            )
            street_canvas.append(
                {
                    **item,
                    "rank": rank,
                    "latitude": lat,
                    "longitude": lon,
                    "readability_score": round(
                        0.55 * float(item.get("score", 0.0))
                        + 0.25 * float(item.get("shape_proxy", 0.0))
                        + 0.20 * float(item.get("landmark_proxy", 0.0)),
                        4,
                    ),
                }
            )
    log.info(
        (
            "Route response prepared for selected shape %s: showing %d route(s), "
            "%d passed automatic checks, %d available for user review, "
            "%d other-shape attempt(s) kept only in the audit."
        ),
        selected_shape or "unknown",
        len(candidates),
        verified_count,
        review_count,
        other_shape_count,
        extra={
            "event": "route.response.prepared",
            "shape": selected_shape,
            "city": state.intent.city if state.intent else None,
            "sport": sport,
            "shown_count": len(candidates),
            "verified_count": verified_count,
            "review_count": review_count,
            "other_shape_count": other_shape_count,
            "candidate_count": state.candidate_count,
            "preflight_count": state.preflight_count,
        },
    )
    return dict(
        request_id=state.request_id,
        workflow=(
            state.workflow.public_summary()
            if getattr(state, "workflow", None) is not None
            else None
        ),
        prompt=state.prompt,
        intent=state.intent.__dict__ if state.intent else None,
        shape=(
            {
                "name": state.shape.name,
                "closed": state.shape.closed,
                "source": state.shape.source,
                "n_paths": len(state.shape.paths),
                "recognition_features": list(state.shape.recognition_features),
                "shape_spec": (
                    asdict(state.shape.spec) if state.shape.spec else None
                ),
                "semantic_verification": (
                    asdict(state.shape.semantic_verification)
                    if state.shape.semantic_verification
                    else None
                ),
                "generator": {
                    "provider": state.shape.generator_provider,
                    "model": state.shape.generator_model,
                    "usage": dict(state.shape.generator_usage),
                },
                "generated_candidate_count": state.shape.generated_candidate_count,
                "selected_candidate": state.shape.selected_candidate,
            }
            if state.shape else None
        ),
        suggested_shape=state.plan.suggested_shape if state.plan else None,
        suggestion_reason=(
            state.plan.notes
            if state.plan and state.plan.suggested_shape
            else None
        ),
        requested_shape=state.requested_shape,
        fit_decision=state.fit_decision.__dict__ if state.fit_decision else None,
        validation=state.validation.__dict__ if state.validation else None,
        distance_km=(snapped.total_distance_m / 1000) if snapped else None,
        snapped=primary_street_routed if snapped else None,
        iterations=state.iterations,
        candidate_count=state.candidate_count,
        preflight_count=state.preflight_count,
        search_seed=getattr(state, "search_seed", 0),
        search_areas=getattr(state, "search_areas", 0),
        below_threshold=state.below_threshold,
        errors=state.errors,
        history=state.history,
        gpx=export.gpx if export and primary_street_routed else None,
        tcx=export.tcx if export and primary_street_routed else None,
        file_paths=(
            export.file_paths if export and primary_street_routed else {}
        ),
        points_preview=preview,
        ideal_preview=ideal_preview,
        landmark_preview=landmark_preview,
        candidates=candidates,
        candidate_audit=candidate_audit,
        candidate_summary=candidate_summary,
        preflight_candidates=state.preflight_candidates,
        street_canvas=street_canvas,
        route_verification=route_verification,
        route_details=route_details,
        planning_options={
            "start_point": list(state.start_point) if state.start_point else None,
            "start_label": state.start_label,
            "start_direction_deg": state.start_direction_deg,
            "route_preferences": asdict(state.route_preferences),
            "image_reference": (
                {"kind": state.reference_kind, "name": state.reference_name}
                if state.reference_kind
                else None
            ),
            "map_placement": (
                asdict(state.map_placement) if state.map_placement else None
            ),
        },
        gallery_publish_token=(
            cloudinary_gallery.maybe_issue_publish_token()
            if primary_street_routed
            else None
        ),
    )


def _interpretation_guidance(
    prompt: str,
    intent: Intent,
    *,
    drawing_kind: str,
    defaults_applied: list[str],
) -> tuple[dict[str, float], list[dict]]:
    """Return inspectable confidence and bounded semantic corrections.

    Confidence is deliberately field-specific: a clear drawing must not hide
    an assumed city or distance behind one misleading overall percentage.
    """

    low = prompt.casefold()
    explicit_sport = bool(
        re.search(
            r"\b(?:run|running|jog|jogging|bike|biking|cycle|cycling|"
            r"fut\w*|kocog\w*|bicikl\w*|kerékpár\w*|teker\w*)\b",
            low,
        )
    )
    explicit_distance = bool(re.search(r"\b\d+(?:[.,]\d+)?\s*km\b", low))
    confidence = {
        "drawing": (
            0.98
            if drawing_kind in {"template", "text"}
            else 0.86
            if drawing_kind == "suggestion"
            else 0.78
            if intent.shape
            else 0.35
        ),
        "city": 0.98 if intent.city and "city" not in defaults_applied else 0.38,
        "sport": 0.98 if explicit_sport else 0.62,
        "distance": 0.99 if explicit_distance else 0.55,
    }
    clarifications: list[dict] = []

    if not intent.shape and not intent.text and not intent.suggest:
        clarifications.append(
            {
                "field": "drawing",
                "question": "What should the route draw?",
                "required": True,
                "selected": None,
                "options": [],
            }
        )
    if "city" in defaults_applied:
        clarifications.append(
            {
                "field": "city",
                "question": "No city was found, so the planner used its default. Change it if needed.",
                "required": False,
                "selected": intent.city,
                "options": [],
            }
        )
    return confidence, clarifications


@router.get("/shape-templates")
def shape_templates() -> dict:
    """List deterministic templates available to the map-placement tool."""

    shapes = [
        {
            "id": name,
            "label": name.replace("_", " ").title(),
        }
        for name in sorted(shape_library.SHAPES)
    ]
    return {"count": len(shapes), "shapes": shapes}


def _preview_paths(normalised_paths: list) -> list[list[list[float]]]:
    """Evenly sample normalised unit-space paths for a lightweight UI preview."""

    points_per_path = max(16, 280 // max(1, len(normalised_paths)))
    return [
        [[float(x), float(y)] for x, y in _even_sample(path, points_per_path)]
        for path in normalised_paths
        if len(path) >= 2
    ]


SVG_UPLOAD_MEDIA_TYPES = frozenset({
    "image/svg+xml",
    "application/xml",
    "text/xml",
    "text/plain",
    "application/octet-stream",
})


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    """Read the raw request body, failing fast once it exceeds ``limit`` bytes."""

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="SVG files must be 2 MB or smaller.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="SVG files must be 2 MB or smaller.")
    return bytes(body)


@router.post("/shape-upload")
async def upload_svg_shape(
    request: Request,
    filename: str | None = Query(default=None, max_length=180),
) -> dict:
    """Validate an uploaded SVG and return its normalised outline for preview.

    Send the file as the raw request body (``Content-Type: image/svg+xml``).
    Generation is stateless: the client sends the same SVG text again as
    ``reference_svg`` in ``/generate``.
    """

    media_type = request.headers.get("content-type", "").split(";")[0].strip().casefold()
    if media_type and media_type not in SVG_UPLOAD_MEDIA_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Send the SVG file as the raw request body with Content-Type image/svg+xml.",
        )
    content = await _read_bounded_body(request, image_reference.MAX_SVG_BYTES)
    try:
        shape = await asyncio.to_thread(
            image_reference.uploaded_svg_shape, content, filename=filename
        )
    except image_reference.ImageReferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalised_paths = geo.normalize_shape(shape.paths)
    return {
        "shape": shape.name,
        "label": shape.name.title(),
        "closed": shape.closed,
        "paths": _preview_paths(normalised_paths),
        "path_count": len(shape.paths),
        "point_count": sum(len(path) for path in shape.paths),
    }


@router.get("/shape-placement-preview")
def shape_placement_preview(
    shape: str = Query(min_length=1, max_length=80),
    city: str = Query(min_length=1, max_length=100),
    sport: Literal["run", "walk", "bike"] = "run",
    distance_km: float = Query(default=10.0, ge=2.0, le=300.0),
) -> dict:
    """Return a normalised outline and a map-ready initial footprint."""

    generated = shape_library.get_shape(shape)
    if generated is None:
        raise HTTPException(status_code=404, detail="Choose a supported shape template.")
    name, paths, closed = generated
    normalised_paths = geo.normalize_shape(paths)
    resolved = geocoder.geocode(city)
    target_scale_m = estimated_scale_m(
        normalised_paths,
        sport,
        name,
        distance_km,
    )
    return {
        "shape": name,
        "label": name.replace("_", " ").title(),
        "closed": closed,
        "paths": _preview_paths(normalised_paths),
        "city": resolved.name,
        "city_substituted": resolved.substituted,
        "center": [resolved.lat, resolved.lon],
        "city_bbox": list(resolved.bbox),
        "scale_m": round(target_scale_m, 2),
        "rotation_deg": round(geo.bbox_long_axis_heading(resolved.bbox), 1),
        "distance_km": distance_km,
        "sport": sport,
    }


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "Paceasso",
        "version": "0.1.0",
        "gallery": {"configured": cloudinary_gallery.is_configured()},
    }


@router.post("/interpret", response_model=InterpretResponse)
def interpret_route_request(req: GenerateRequest) -> dict:
    """Return the route request as the planner understands it, before routing.

    This inexpensive, inspectable step lets the UI expose semantic mistakes
    before a user waits for placement and street routing. It intentionally uses
    the exact same IntentAgent as ``/generate`` so the preview cannot drift from
    the real pipeline.
    """

    state = WorkflowState(prompt=req.prompt, request_id=current_request_id())
    try:
        IntentAgent().run(state)
    except Exception as exc:  # noqa: BLE001
        log.exception("route interpretation failed")
        raise HTTPException(
            status_code=500,
            detail="We couldn’t interpret that route idea. Please try a shorter description.",
        ) from exc
    if state.intent is None:
        raise HTTPException(status_code=422, detail="We couldn’t identify a route idea.")

    intent = state.intent
    cfg = get_settings().workflow
    defaults_applied: list[str] = []
    payload = dict(intent.__dict__)
    if not intent.city:
        payload["city"] = cfg.city_default
        defaults_applied.append("city")
    if intent.distance_km is None:
        payload["distance_km"] = cfg.distance_defaults.get(intent.sport, 8.0)
        defaults_applied.append("distance")

    if intent.suggest:
        drawing_label = "Best shape for the streets"
        drawing_kind = "suggestion"
    elif intent.text:
        drawing_label = intent.text
        drawing_kind = "text"
    else:
        drawing_label = intent.shape or "Custom drawing"
        template = shape_library.get_shape(intent.shape or "")
        if template is None and intent.shape:
            template = shape_library.find_by_keyword(intent.shape)
            if template and not shape_library.template_match_covers_description(
                intent.shape,
                template[0],
            ):
                template = None
        drawing_kind = "template" if template else "custom"

    confidence, clarifications = _interpretation_guidance(
        req.prompt,
        intent,
        drawing_kind=drawing_kind,
        defaults_applied=defaults_applied,
    )

    return {
        "prompt": req.prompt,
        "intent": payload,
        "drawing_label": drawing_label,
        "drawing_kind": drawing_kind,
        "defaults_applied": defaults_applied,
        "confidence": confidence,
        "needs_clarification": any(
            bool(item.get("required")) for item in clarifications
        ),
        "clarifications": clarifications,
    }


@router.post("/route-acceptance")
def record_route_acceptance(req: RouteAcceptanceRequest) -> dict:
    """Record the user's explicit decision without retaining route geometry."""

    if not req.snapped:
        log.warning(
            "Rejected acceptance for a route that is not matched to streets",
            extra={
                "event": "route.user.acceptance.rejected",
                "generation_request_id": req.generation_request_id,
                "candidate_id": req.route_id,
                "shape": req.shape_name,
                "reason": "not_street_routed",
            },
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Only a route matched to connected streets can be approved for "
                "GPS export. Generate or edit the route again first."
            ),
        )

    failed_text = ", ".join(req.failed_gates) or "none"
    score_text = f"{req.score:.1%}" if req.score is not None else "unavailable"
    fidelity_text = (
        f"{req.shape_fidelity:.1%}"
        if req.shape_fidelity is not None
        else "unavailable"
    )
    distance_text = (
        f"{req.distance_km:.2f} km"
        if req.distance_km is not None
        else "unavailable"
    )
    log.warning(
        (
            "User accepted route for GPX: route=%s, shape=%s, "
            "system verified=%s, street matched=%s, overall=%s, "
            "likeness=%s, distance=%s, failed checks=%s."
        ),
        req.route_id,
        req.shape_name,
        "yes" if req.checks_passed else "no",
        "yes" if req.snapped else "no",
        score_text,
        fidelity_text,
        distance_text,
        failed_text,
        extra={
            "event": "route.user.accepted",
            "generation_request_id": req.generation_request_id,
            "candidate_id": req.route_id,
            "shape": req.shape_name,
            "decision": "user_accepted",
            "verified": req.checks_passed,
            "snapped": req.snapped,
            "failed_gates": req.failed_gates,
            "score": req.score,
            "fidelity": req.shape_fidelity,
            "distance_km": req.distance_km,
            "export_mode": "user_acceptance",
        },
    )
    return {"recorded": True}


def _import_reference(req: GenerateRequest) -> image_reference.ImportedImageReference | None:
    """Turn an uploaded SVG or a public image link into a reference shape."""

    try:
        if req.reference_svg is not None:
            return image_reference.import_uploaded_svg(
                req.reference_svg.encode("utf-8"),
                filename=req.reference_svg_name,
            )
        if req.reference_image_url:
            return image_reference.import_image_reference(req.reference_image_url)
    except image_reference.ImageReferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return None


@router.post("/generate", response_model=GenerateResponse)
def generate_route(req: GenerateRequest, request: Request) -> dict | StreamingResponse:
    if "application/x-ndjson" in request.headers.get("accept", ""):
        return _stream_generation(req)
    return _generate_route_sync(req)


def _stream_generation(req: GenerateRequest) -> StreamingResponse:
    """Stream bounded, prompt-free progress beside one ordinary generation run."""
    context = copy_context()

    async def stream() -> AsyncIterator[str]:
        import json

        loop = asyncio.get_running_loop()
        messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancelled = Event()

        def send(message: dict[str, Any]) -> None:
            if not cancelled.is_set():
                try:
                    loop.call_soon_threadsafe(messages.put_nowait, message)
                except RuntimeError:  # the server loop has already shut down
                    cancelled.set()

        def work() -> None:
            try:
                with cancellation_scope(cancelled):
                    result = _generate_route_sync(
                        req,
                        event_sink=lambda data: send({"type": "progress", **data}),
                        preview_sink=lambda data: send({"type": "preview", **data}),
                    )
                validated = GenerateResponse.model_validate(result)
                send({"type": "result", "data": validated.model_dump(mode="json")})
            except WorkflowCancelledError:
                pass
            except HTTPException as exc:
                send({"type": "error", "status": exc.status_code, "detail": exc.detail})
            except Exception:  # noqa: BLE001 - headers have already been sent
                log.exception("streamed generation failed")
                send({"type": "error", "status": 500, "detail": "Route generation failed."})
            finally:
                send({"type": "end"})

        worker = Thread(target=lambda: context.run(work), name="gps-art-stream", daemon=True)
        worker.start()
        try:
            while True:
                message = await messages.get()
                if message["type"] == "end":
                    return
                yield json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        finally:
            cancelled.set()

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _generate_route_sync(
    req: GenerateRequest,
    *,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    preview_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict:
    log.info(
        "Route generation requested",
        extra={
            "event": "generation.requested",
            "prompt_length": len(req.prompt),
        },
    )
    intent_override = (
        Intent(**req.intent_override.model_dump())
        if req.intent_override is not None
        else None
    )
    preferences = RoutePreferences(**req.route_preferences.model_dump())
    map_placement = (
        MapPlacement(**req.map_placement.model_dump())
        if req.map_placement is not None
        else None
    )
    imported_reference = _import_reference(req)
    start_point = None
    start_label = None
    if req.start_point is not None:
        start_point = (req.start_point.latitude, req.start_point.longitude)
        start_label = req.start_point.label or "Selected location"
    elif req.start_address:
        resolved = geocoder.geocode_point(req.start_address)
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail="We couldn’t find that start address. Try a more complete address or use your current location.",
            )
        start_point = (resolved.lat, resolved.lon)
        start_label = resolved.name
    try:
        generation_options: dict[str, Any] = (
            {"event_sink": event_sink, "preview_sink": preview_sink}
            if event_sink is not None or preview_sink is not None
            else {}
        )
        if req.search_seed is not None:
            generation_options["search_seed"] = req.search_seed
        has_preferences = any(req.route_preferences.model_dump().values())
        if (
            not generation_options
            and intent_override is None
            and start_point is None
            and req.start_direction_deg is None
            and not has_preferences
            and imported_reference is None
            and map_placement is None
        ):
            # Preserve the original domain call for ordinary prompts and for
            # lightweight integrations that wrap the one-argument function.
            state = generate(req.prompt)
        else:
            state = generate(
                req.prompt,
                intent_override=intent_override,
                start_point=start_point,
                start_label=start_label,
                start_direction_deg=req.start_direction_deg,
                route_preferences=preferences,
                reference_shape=(
                    imported_reference.shape if imported_reference else None
                ),
                reference_image_data_url=(
                    imported_reference.image_data_url if imported_reference else None
                ),
                reference_name=(imported_reference.name if imported_reference else None),
                reference_kind=(imported_reference.kind if imported_reference else None),
                map_placement=map_placement,
                **generation_options,
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("route generation failed")
        raise HTTPException(
            status_code=500,
            detail="Route generation failed. Verify the routing and model-provider configuration.",
        ) from exc
    if not _has_connected_route(state.snapped):
        log.error(
            "Route generation produced no street-connected result; blocking unsafe guide",
            extra={
                "event": "generation.street_routing.unavailable",
                "shape": state.shape.name if state.shape else None,
                "city": state.intent.city if state.intent else None,
                "candidate_count": state.candidate_count,
                "preflight_count": state.preflight_count,
            },
        )
        detail = (
            "No connected street route could be created, so the planner did not "
            "return an unsafe straight-line GPS track. Try another city, shape, "
            "or distance, or retry when the routing service is available."
        )
        workflow = getattr(state, "workflow", None)
        failures = workflow.routing_failures if workflow is not None else {}
        if "directions.http_401" in failures or "directions.http_403" in failures:
            detail = (
                "The street-routing service could not authenticate the planner. "
                "Its configuration needs attention; changing your idea will not fix it. "
                "No GPS file was created. Please contact support with your request reference."
            )
        elif "directions.http_429" in failures:
            detail = (
                "The street-routing service has reached its request limit. "
                "No GPS file was created. Please wait before retrying your saved idea."
            )
        elif "directions.http_400" in failures:
            detail = (
                "The street-routing service could not accept the route settings. "
                "No GPS file was created. Please review the route setup or contact "
                "support with your request reference."
            )
        elif "directions.transport" in failures:
            detail = (
                "The street-routing service did not respond reliably. "
                "No GPS file was created. Please retry your saved idea shortly."
            )
        raise HTTPException(status_code=503, detail=detail)
    try:
        return _state_to_response(state)
    except Exception as exc:  # noqa: BLE001 - public serialisation boundary
        log.exception(
            "Route response preparation failed",
            extra={"event": "generation.response.failed"},
        )
        raise HTTPException(
            status_code=500,
            detail="The street route was created, but its response could not be prepared.",
        ) from exc


@router.post("/edit-route", response_model=EditedRouteResponse)
def edit_route(req: EditedRouteRequest) -> dict:
    """Re-route edited points and prepare an export with verification context."""
    control_points = [(point[0], point[1]) for point in req.control_points]
    reference_points = [
        (point[0], point[1])
        for point in (req.reference_points or req.control_points)
    ]
    if geo.path_distance_m(control_points) > 1_000_000:
        raise HTTPException(
            status_code=422,
            detail="Edited route guides must stay within a 1,000 km total span.",
        )

    edit_preferences = RoutePreferences(**req.route_preferences.model_dump())
    active_edit_preferences = (
        edit_preferences
        if any(req.route_preferences.model_dump().values())
        else None
    )
    try:
        if active_edit_preferences is None:
            points, distance_m, snapped, readiness = ors_client.snap_route_detailed(
                control_points,
                sport=req.sport,
                closed=req.closed,
            )
        else:
            points, distance_m, snapped, readiness = ors_client.snap_route_detailed(
                control_points,
                sport=req.sport,
                closed=req.closed,
                route_preferences=active_edit_preferences,
            )
    except Exception as exc:  # noqa: BLE001 - external router boundary
        log.exception(
            "Edited-route street routing raised an unexpected error",
            extra={
                "event": "route.edit.street_routing.error",
                "shape": req.shape_name,
                "sport": req.sport,
                "guide_point_count": len(control_points),
            },
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "The edited route could not be matched to connected streets, so no "
                "GPS file was created. Adjust the control points or retry when the "
                "routing service is available."
            ),
        ) from exc
    if not _is_connected_route_geometry(
        points,
        distance_m,
        snapped=snapped,
    ):
        log.error(
            "Edited route could not be matched to connected streets; blocking unsafe export",
            extra={
                "event": "route.edit.street_routing.unavailable",
                "shape": req.shape_name,
                "sport": req.sport,
                "guide_point_count": len(control_points),
            },
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "The edited route could not be matched to connected streets, so no "
                "GPS file was created. Adjust the control points or retry when the "
                "routing service is available."
            ),
        )
    temporary = WorkflowState(
        prompt="manual route edit",
        request_id=current_request_id(),
        intent=Intent(
            shape=req.shape_name,
            text=None,
            city=None,
            sport=req.sport,
            distance_km=req.target_distance_km,
            style=None,
        ),
        shape=Shape(
            name=req.shape_name,
            paths=[[(0.0, 0.0), (1.0, 1.0)]],
            closed=req.closed,
            source="manual",
        ),
        route_draft=RouteDraft(
            center_lat=reference_points[0][0],
            center_lon=reference_points[0][1],
            scale_m=1.0,
            rotation_deg=0.0,
            lat_offset_m=0.0,
            lon_offset_m=0.0,
            simplify_tolerance=0.0,
            waypoints=reference_points,
            closed=req.closed,
            target_distance_km=req.target_distance_km,
        ),
        snapped=SnappedRoute(
            points=points,
            total_distance_m=distance_m,
            snapped=snapped,
            readiness=readiness,
        ),
    )
    try:
        ValidationAgent().run(temporary)
    except Exception as exc:  # noqa: BLE001 - public validation boundary
        log.exception(
            "Edited route validation failed",
            extra={
                "event": "route.edit.validation.failed",
                "shape": req.shape_name,
                "sport": req.sport,
            },
        )
        raise HTTPException(
            status_code=500,
            detail="Edited route validation failed.",
        ) from exc
    if temporary.validation is None:
        raise HTTPException(status_code=500, detail="Edited route validation failed.")

    warnings = list(temporary.validation.issues)
    verification = quality_gate_report(
        temporary.validation,
        closed=req.closed,
        candidate_shape=req.shape_name,
        selected_shape=req.shape_name,
    )
    try:
        gpx = gpx_writer.to_gpx(
            points,
            name=req.name,
            sport=req.sport,
            total_distance_m=distance_m,
        )
    except Exception as exc:  # noqa: BLE001 - public export boundary
        log.exception(
            "Edited route GPX export failed",
            extra={
                "event": "route.edit.export.failed",
                "shape": req.shape_name,
                "sport": req.sport,
            },
        )
        raise HTTPException(
            status_code=500,
            detail="The edited street route was created, but its GPS file could not be prepared.",
        ) from exc
    tcx = None
    try:
        tcx = gpx_writer.to_tcx(
            points,
            name=req.name,
            sport=req.sport,
            total_distance_m=distance_m,
        )
    except Exception:  # noqa: BLE001
        tcx = None
    if not verification["passed"]:
        failed_labels = [
            gate["label"]
            for gate in verification["gates"]
            if gate["applies"] and not gate["passed"]
        ]
        warnings.append(
            "Automatic verification is below target for: "
            + ", ".join(failed_labels)
            + ". The route can still be exported after explicit user acceptance."
        )
    decision = "verified" if verification["passed"] else "review"
    log_method = log.info if verification["passed"] else log.warning
    log_method(
        (
            "Edited route prepared: shape=%s, decision=%s, street matched=%s, "
            "overall=%.1f%%, likeness=%.1f%%, distance=%.2f km, "
            "points=%d, failed checks=%s."
        ),
        req.shape_name,
        decision,
        "yes" if snapped else "no",
        temporary.validation.score * 100,
        temporary.validation.shape_fidelity * 100,
        distance_m / 1000.0,
        len(points),
        ", ".join(verification["failed_gates"]) or "none",
        extra={
            "event": "route.edit.completed",
            "shape": req.shape_name,
            "sport": req.sport,
            "snapped": snapped,
            "route_point_count": len(points),
            "guide_point_count": len(reference_points),
            "distance_km": round(distance_m / 1000.0, 3),
            "target_distance_km": req.target_distance_km,
            "score": temporary.validation.score,
            "fidelity": temporary.validation.shape_fidelity,
            "distance_fit": temporary.validation.distance_fit,
            "closure": temporary.validation.closure,
            "decision": decision,
            "verified": verification["passed"],
            "failed_gates": verification["failed_gates"],
            "export_mode": "verified" if verification["passed"] else "user_acceptance",
        },
    )
    route_details = _route_details(
        validation=temporary.validation,
        shape_name=req.shape_name,
        shape_source="manual",
        sport=req.sport,
        snapped=snapped,
        closed=req.closed,
        distance_km=distance_m / 1000.0,
        target_distance_km=req.target_distance_km,
        route_point_count=len(points),
        guide_point_count=len(reference_points),
        readiness=readiness,
    )
    return {
        "request_id": temporary.request_id,
        "points_preview": [
            [point[0], point[1]]
            for point in points
        ],
        "distance_km": distance_m / 1000.0,
        "snapped": snapped,
        "below_recommended": not verification["passed"],
        "validation": temporary.validation.__dict__,
        "route_verification": verification,
        "route_details": route_details,
        "gpx": gpx,
        "tcx": tcx,
        "warnings": warnings,
    }
