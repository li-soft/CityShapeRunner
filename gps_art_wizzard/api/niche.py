"""Endpoints for team murals, recognition repair, and time-aware readiness."""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from ..state import RoutePreferences, RouteReadiness
from ..tools import (
    accessibility_readiness,
    art_rescue,
    cloudinary_gallery,
    destination_catalog,
    geo,
    gpx_writer,
    lesson_pack,
    night_readiness,
    occasions,
    ors_client,
    route_landmarks,
    route_safety,
    shape_similarity,
)
from ..tools.timed_readiness import time_readiness
from .routes import EditedRouteRequest, RoutePreferencesRequest, edit_route

router = APIRouter(tags=["GPS Art Intelligence"])


def _route_or_fail(
    points: list[tuple[float, float]],
    *,
    sport: str,
    closed: bool,
    route_preferences: RoutePreferences | None = None,
) -> tuple[list[tuple[float, float]], float, RouteReadiness]:
    """Require Directions evidence before any derived GPS file is created."""

    try:
        routed, distance_m, snapped, readiness = ors_client.snap_route_detailed(
            points,
            sport=sport,
            closed=closed,
            route_preferences=route_preferences,
        )
    except Exception as error:  # noqa: BLE001 - external routing boundary
        raise HTTPException(
            status_code=503,
            detail=("Connected street routing is unavailable, so no GPS file was created."),
        ) from error
    if not route_safety.is_provider_routed_geometry(
        routed,
        distance_m,
        routed=snapped,
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "The route could not be matched completely to the road/path network, "
                "so no GPS file was created."
            ),
        )
    return routed, distance_m, readiness


def _points(value: list[list[float]]) -> list[tuple[float, float]]:
    result = []
    for point in value:
        if len(point) < 2:
            raise ValueError("each point needs latitude and longitude")
        lat, lon = float(point[0]), float(point[1])
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            raise ValueError("points must be finite latitude and longitude pairs")
        result.append((lat, lon))
    return result


class MuralMember(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    max_distance_km: float = Field(ge=0.05, le=500, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a participant name.")
        return value.strip()


class MuralPlanRequest(BaseModel):
    points: list[list[float]] = Field(min_length=4, max_length=2_000)
    participants: int | None = Field(default=None, ge=2, le=24)
    members: list[MuralMember] | None = Field(default=None, min_length=2, max_length=24)
    name: str = Field(default="Community GPS mural", min_length=1, max_length=100)
    sport: str = Field(default="run", pattern="^(run|walk|bike)$")
    return_to_start: bool = False
    route_preferences: RoutePreferences = Field(default_factory=RoutePreferences)

    @model_validator(mode="after")
    def validate_roster(self):
        if self.members is None and self.participants is None:
            raise ValueError("Supply participants or a named member roster.")
        if self.members is not None:
            if self.participants is not None and self.participants != len(self.members):
                raise ValueError("Participant count must match the member roster.")
            names = [member.name.casefold() for member in self.members]
            if len(names) != len(set(names)):
                raise ValueError("Participant names must be distinct.")
        return self

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


def _split_by_distance(
    points: list[tuple[float, float]], weights: list[float]
) -> list[list[tuple[float, float]]]:
    total = geo.path_distance_m(points)
    if total <= 0:
        return []
    targets = [0.0]
    total_weight = sum(weights)
    for weight in weights:
        targets.append(targets[-1] + total * weight / total_weight)
    targets[-1] = total
    pieces, current, covered, target_index = [], [points[0]], 0.0, 1
    for start, end in zip(points, points[1:], strict=False):
        segment = geo.haversine(*start, *end)
        while target_index < len(targets) and covered + segment >= targets[target_index]:
            ratio = (targets[target_index] - covered) / segment if segment else 0.0
            cut = (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
            current.append(cut)
            pieces.append(current)
            current = [cut]
            target_index += 1
        current.append(end)
        covered += segment
    if len(current) > 1:
        pieces.append(current)
    return [piece for piece in pieces if len(piece) > 1][: len(weights)]


async def _event_route(
    http_request: Request,
    points: list[tuple[float, float]],
    *,
    sport: str,
    closed: bool,
    route_preferences: RoutePreferences | None = None,
) -> tuple[list[tuple[float, float]], float, RouteReadiness]:
    # Stop starting participant routes when the organiser leaves or cancels.
    if await http_request.is_disconnected():
        raise HTTPException(status_code=499, detail="The event request was cancelled.")
    return await run_in_threadpool(
        _route_or_fail,
        points,
        sport=sport,
        closed=closed,
        route_preferences=route_preferences,
    )


@router.post("/mural-plan")
async def mural_plan(request: MuralPlanRequest, http_request: Request) -> dict:
    guides = _points(request.points)
    points, routed_distance_m, readiness = await _event_route(
        http_request,
        guides,
        sport=request.sport,
        closed=guides[0] == guides[-1],
        route_preferences=request.route_preferences,
    )
    members = request.members or [
        MuralMember(name=f"Artist {index + 1}", max_distance_km=500)
        for index in range(request.participants or 0)
    ]
    if request.members and sum(
        member.max_distance_km for member in members
    ) * 1000 < geo.path_distance_m(points):
        raise HTTPException(
            status_code=422,
            detail="The drawing exceeds the group's combined distance limits. Choose a shorter route or raise the limits.",
        )
    # shortcut: allocate proportionally before travel; revisit if shared-start events need redistribution.
    pieces = _split_by_distance(points, [member.max_distance_km for member in members])
    if len(pieces) != len(members) or any(len(set(piece)) < 2 for piece in pieces):
        raise HTTPException(
            status_code=422,
            detail="The route is too short to divide into that many mural sections.",
        )
    sections = []
    for index, (piece, member) in enumerate(zip(pieces, members, strict=True), start=1):
        art_distance_m = geo.path_distance_m(piece)
        approach: list[tuple[float, float]] = []
        return_path: list[tuple[float, float]] = []
        for endpoints, path in (
            ([points[0], piece[0]], approach),
            ([piece[-1], points[0]], return_path),
        ):
            if request.return_to_start and endpoints[0] != endpoints[1]:
                connector, _, _ = await _event_route(
                    http_request,
                    endpoints,
                    sport=request.sport,
                    closed=False,
                    route_preferences=request.route_preferences,
                )
                if (
                    geo.haversine(*connector[0], *endpoints[0]) > 1
                    or geo.haversine(*connector[-1], *endpoints[-1]) > 1
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="A meeting-point connection missed its endpoint; no group GPS files were created.",
                    )
                path.extend(connector)
        # Keep connector snaps as separate tracks: never invent a line across a gap.
        paths = [path for path in (approach, piece, return_path) if path]
        distance_m = sum(geo.path_distance_m(path) for path in paths)
        if request.members and distance_m > member.max_distance_km * 1000 + 0.01:
            raise HTTPException(
                status_code=422,
                detail=f"{member.name}'s allocation needs {distance_m / 1000:.2f} km including meeting-point travel, above their {member.max_distance_km:g} km limit. Raise the limit, disable the shared meeting point, or choose a shorter drawing.",
            )
        sections.append(
            {
                "id": f"mural-{index}",
                "label": member.name,
                "points_preview": [[lat, lon] for lat, lon in piece],
                "distance_km": distance_m / 1000,
                "art_distance_km": art_distance_m / 1000,
                "travel_distance_km": (distance_m - art_distance_m) / 1000,
                "max_distance_km": member.max_distance_km if request.members else None,
                "start": piece[0],
                "finish": piece[-1],
                "travel_segments_preview": [path for path in (approach, return_path) if path],
                "gpx": gpx_writer.to_segmented_gpx(
                    paths,
                    name=f"{request.name} - {member.name}",
                    sport=request.sport,
                ),
            }
        )
    return {
        "name": request.name,
        "participant_count": len(sections),
        "total_distance_km": routed_distance_m / 1000,
        "points_preview": points,
        "meeting_point": points[0] if request.return_to_start else None,
        "travel_note": "Meeting-point travel is included in each limit. Record the whole outing; access tracks may add lines outside the drawing."
        if request.return_to_start
        else "Meet at each section's start. Travel to the start and home from the finish is not planned or included in the distance limit.",
        "snapped": True,
        "readiness": asdict(readiness),
        "sections": sections,
    }


class TimedReadinessRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    departure_at: datetime


@router.post("/timed-readiness")
def timed_readiness(request: TimedReadinessRequest) -> dict:
    when = request.departure_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return time_readiness(request.latitude, request.longitude, when)


class NightReadinessRequest(BaseModel):
    points: list[list[float]] = Field(min_length=2, max_length=5_000)

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/night-readiness")
def night_readiness_check(request: NightReadinessRequest) -> dict:
    """Street-lighting and traffic exposure evidence for one routed polyline."""

    return night_readiness.analyse(_points(request.points))


class RouteLandmarksRequest(BaseModel):
    points: list[list[float]] = Field(min_length=4, max_length=5_000)

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/route-landmarks")
def route_landmarks_along(request: RouteLandmarksRequest) -> dict:
    """Named sights within a short corridor of the planned route."""

    return route_landmarks.find_landmarks(_points(request.points))


class AccessibilityReadinessRequest(BaseModel):
    points: list[list[float]] = Field(min_length=2, max_length=5_000)

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/accessibility-readiness")
def accessibility_readiness_check(request: AccessibilityReadinessRequest) -> dict:
    """Wheelchair, steps, and surface evidence for one routed polyline."""

    return accessibility_readiness.analyse(_points(request.points))


class LessonPackRequest(BaseModel):
    reference_points: list[list[float]] = Field(min_length=3, max_length=500)
    closed: bool = True
    title: str = Field(default="My GPS drawing", min_length=1, max_length=80)
    shape_name: str = Field(default="drawing", min_length=1, max_length=60)

    @field_validator("reference_points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/lesson-pack")
def build_a_lesson_pack(request: LessonPackRequest) -> dict:
    """Classroom worksheet: lettered waypoints with bearings and distances."""

    return lesson_pack.build_lesson_pack(
        _points(request.reference_points),
        closed=request.closed,
        title=request.title,
        shape_name=request.shape_name,
    )


@router.get("/destinations")
def curated_destination_art() -> dict:
    """Curated city art picks for the composer's inspiration strip."""

    return {
        "destinations": [
            {
                "city": entry.city,
                "shape_prompt": entry.shape_prompt,
                "name": entry.name,
                "blurb": entry.blurb,
                "distance_km": entry.distance_km,
                "country_code": entry.country_code,
                "sport": entry.sport,
                "partner_ready": entry.partner_ready,
            }
            for entry in destination_catalog.CATALOGUE
        ]
    }


@router.get("/occasions")
def upcoming_occasions(
    request: Request,
    days_ahead: int = Query(default=60, ge=1, le=365),
    country: str | None = Query(default=None, min_length=2, max_length=8),
    timezone: str | None = Query(default=None, max_length=80),
    locale: str | None = Query(default=None, max_length=35),
) -> dict:
    """Date-aware suggestions localised from privacy-safe region hints."""

    resolved_country = occasions.normalise_country_code(country)
    location_basis = "country"

    if not resolved_country:
        for header in (
            "cf-ipcountry",
            "x-vercel-ip-country",
            "cloudfront-viewer-country",
            "x-country-code",
        ):
            resolved_country = occasions.normalise_country_code(request.headers.get(header))
            if resolved_country:
                location_basis = "network_region"
                break

    if not resolved_country:
        resolved_country = occasions.country_from_timezone(timezone)
        location_basis = "timezone"

    if not resolved_country:
        resolved_country = occasions.country_from_locale(locale)
        location_basis = "locale"

    if not resolved_country:
        accept_language = request.headers.get("accept-language", "").split(",", 1)[0]
        resolved_country = occasions.country_from_locale(accept_language)
        location_basis = "browser_language"

    if not resolved_country:
        resolved_country = "HU"
        location_basis = "default"

    return {
        "generated_on": date.today().isoformat(),
        "days_ahead": days_ahead,
        "country_code": resolved_country,
        "country_name": occasions.COUNTRY_NAMES.get(resolved_country, "your region"),
        "location_basis": location_basis,
        "occasions": occasions.upcoming_occasions(
            days_ahead=days_ahead,
            country_code=resolved_country,
        ),
    }


class InkproofRequest(BaseModel):
    points: list[list[float]] = Field(min_length=4, max_length=5_000)
    accuracy_m: float = Field(default=10.0, ge=3.0, le=50.0)

    @field_validator("points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/inkproof-analysis")
def inkproof(request: InkproofRequest) -> dict:
    """Forecast whether realistic GPS drift can erase fine drawing details."""

    return art_rescue.inkproof_analysis(_points(request.points), request.accuracy_m)


class RecordedGPX(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    gpx: str = Field(min_length=1, max_length=2_000_000)


class ArtRescueRequest(BaseModel):
    planned_points: list[list[float]] = Field(min_length=4, max_length=5_000)
    recordings: list[RecordedGPX] = Field(min_length=1, max_length=24)
    tolerance_m: float = Field(default=25.0, ge=5.0, le=100.0)
    name: str = Field(default="GPS art rescue", min_length=1, max_length=100)
    sport: str = Field(default="run", pattern="^(run|walk|bike)$")

    @field_validator("planned_points")
    @classmethod
    def validate_planned_points(cls, value):
        _points(value)
        return value


@router.post("/art-rescue")
async def rescue_recordings(request: ArtRescueRequest, http_request: Request) -> dict:
    """Merge sessions and isolate missing ink only on provider-routed geometry."""

    try:
        recordings = []
        segment_count, point_count = 0, 0
        for upload in request.recordings:
            recording = await run_in_threadpool(
                art_rescue.parse_recording,
                upload.name,
                upload.gpx,
            )
            segment_count += len(recording.segments)
            point_count += sum(len(segment) for segment in recording.segments)
            if segment_count > 48:
                raise ValueError("The recordings contain more than 48 track segments.")
            if point_count > 80_000:
                raise ValueError(
                    "The recordings contain more than 80,000 points; simplify them first."
                )
            recordings.append(recording)
        routed_plan, _, _ = await _event_route(
            http_request,
            _points(request.planned_points),
            sport=request.sport,
            closed=request.planned_points[0] == request.planned_points[-1],
        )
        routed_recordings = []
        for recording in recordings:
            routed_segments = [
                (
                    await _event_route(
                        http_request,
                        segment,
                        sport=request.sport,
                        closed=segment[0] == segment[-1],
                    )
                )[0]
                for segment in recording.segments
            ]
            routed_recordings.append(art_rescue.Recording(recording.name, routed_segments))
        return await run_in_threadpool(
            art_rescue.rescue_analysis,
            routed_plan,
            recordings,
            export_recordings=routed_recordings,
            tolerance_m=request.tolerance_m,
            name=request.name,
            sport=request.sport,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class RecognitionRepairRequest(BaseModel):
    reference_points: list[list[float]] = Field(min_length=4, max_length=500)
    sport: Literal["run", "walk", "bike"] = "run"
    closed: bool = True
    name: str = Field(default="Refined GPS art", min_length=1, max_length=100)
    shape_name: str = Field(default="edited", min_length=1, max_length=80)
    target_distance_km: float | None = Field(default=None, gt=0, le=500)
    route_preferences: dict[str, bool] = Field(default_factory=dict)

    @field_validator("name", "shape_name")
    @classmethod
    def normalise_label(cls, value: str) -> str:
        return EditedRouteRequest.normalise_label(value)

    @field_validator("reference_points")
    @classmethod
    def validate_points(cls, value):
        _points(value)
        return value


@router.post("/recognition-repair")
def recognition_repair(request: RecognitionRepairRequest) -> dict:
    """Re-route a cue-preserving guide with fewer, stronger visual anchors."""

    guide = _points(request.reference_points)
    landmarks = shape_similarity.salient_route_landmarks(guide)
    anchors = [guide[0], *landmarks, guide[-1]]
    deduped: list[tuple[float, float]] = []
    for point in anchors:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    if request.closed and deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    if len(set(deduped)) < (3 if request.closed else 2):
        deduped = ors_client._subsample(guide, closed=request.closed, max_points=12)
    if len(set(deduped)) < (3 if request.closed else 2):
        raise HTTPException(status_code=422, detail="The drawing needs distinct route points.")
    allowed_preference_keys = set(RoutePreferences.__dataclass_fields__)
    preference_values = {
        key: bool(value)
        for key, value in request.route_preferences.items()
        if key in allowed_preference_keys
    }
    result = edit_route(
        EditedRouteRequest(
            control_points=[[lat, lon] for lat, lon in deduped],
            reference_points=request.reference_points,
            sport=request.sport,
            closed=request.closed,
            name=request.name,
            shape_name=request.shape_name,
            target_distance_km=request.target_distance_km,
            route_preferences=RoutePreferencesRequest(**preference_values),
        )
    )
    routed = [(lat, lon) for lat, lon in result["points_preview"]]
    fidelity = shape_similarity.fidelity_between_routes(guide, routed, n=96)
    return {
        **result,
        "guide_points": [[lat, lon] for lat, lon in deduped],
        "recognition_score": fidelity,
        "readiness": result["route_details"]["readiness"],
        "gallery_publish_token": cloudinary_gallery.maybe_issue_publish_token(),
        "message": "Re-routed from the shape's strongest visual anchors.",
    }
