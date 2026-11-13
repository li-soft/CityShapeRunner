"""Offline tests: the pipeline runs end-to-end with no API keys, and the
geometry/shape tooling is sound.

Run with: ``GEOCODE_OFFLINE=1 pytest``  (the env skips the Nominatim network call).
"""

from __future__ import annotations

import string

import numpy as np
import pytest

from gps_art_wizzard.agents.shape_agent import _clear_custom_shape_cache
from gps_art_wizzard.api.routes import _state_to_response
from gps_art_wizzard.orchestrator import generate
from gps_art_wizzard.tools import geo, shape_library, shape_similarity, text_shapes


# --------------------------------------------------------------------------- #
# Pipeline (offline)
# --------------------------------------------------------------------------- #
def test_pipeline_produces_safe_preview_without_a_routing_provider():
    state = generate("a heart run in Budapest, about 8 km")
    assert state.workflow is not None
    assert state.workflow.status.value == "needs_review"
    assert state.workflow.step_attempts == {
        "intent": 1,
        "planning": 1,
        "shape": 1,
        "placement": 1,
        "preflight": 1,
        "snap": 1,
        "validation": 1,
        "export": 1,
    }
    response = _state_to_response(state)
    assert response["workflow"]["run_id"] == state.workflow.run_id
    assert response["workflow"]["status"] == "needs_review"
    assert "events" not in response["workflow"]
    assert state.export is None
    assert state.snapped is not None
    assert len(state.snapped.points) >= 2
    assert state.snapped.snapped is False
    assert state.validation is not None
    assert 0.0 <= state.validation.score <= 1.0
    assert state.validation.on_roads is False
    assert state.iterations == 0
    assert any("preview cannot be exported" in error.lower() for error in state.errors)
    assert len(state.candidates) >= 1
    assert state.plan is not None
    assert state.plan.shape_strategy in ("template", "text", "llm")
    assert state.plan.center_lat == pytest.approx(47.4979)
    assert state.plan.center_lon == pytest.approx(19.0402)


def test_pipeline_text_shape():
    state = generate("write HI in Berlin")
    assert state.shape is not None
    assert state.shape.source == "text"
    assert state.export is None
    assert any("preview cannot be exported" in error for error in state.errors)


def test_unknown_shape_uses_grounded_offline_scaffold_when_available():
    _clear_custom_shape_cache()
    state = generate("a platypus in Budapest, 8 km")
    assert state.intent is not None
    assert state.intent.shape == "platypus"
    assert state.shape is not None
    assert state.shape.name == "grounded:platypus"
    assert state.shape.name != "P label"
    assert state.shape.source == "fallback"
    assert state.shape.spec is not None
    assert state.shape.spec.subject == "platypus"
    assert any("fallback" in error for error in state.errors)


def test_bug_prompt_uses_an_insect_outline_even_without_an_ai_provider():
    state = generate("a bug run in Tatabánya, about 8 km")

    assert state.intent is not None
    assert state.intent.shape == "bug"
    assert state.shape is not None
    assert state.shape.name == "bug"
    assert state.shape.source == "template"
    assert state.shape.name != "B label"


# --------------------------------------------------------------------------- #
# Tooling
# --------------------------------------------------------------------------- #
def test_heart_template_closed():
    name, paths, closed = shape_library.heart()
    assert name == "heart"
    assert closed is True
    assert len(paths[0]) > 10


def test_normalize_shape_scales_to_one_and_centres_route_length():
    _, paths, _ = shape_library.star()
    norm = geo.normalize_shape(paths)
    pts = np.concatenate([np.asarray(p) for p in norm])
    extent = (pts.max(axis=0) - pts.min(axis=0)).max()
    assert abs(extent - 1.0) < 1e-6
    weighted_midpoints = np.zeros(2)
    total_length = 0.0
    for path in norm:
        path_points = np.asarray(path)
        lengths = np.linalg.norm(np.diff(path_points, axis=0), axis=1)
        weighted_midpoints += np.sum(
            ((path_points[:-1] + path_points[1:]) / 2.0) * lengths[:, np.newaxis],
            axis=0,
        )
        total_length += float(lengths.sum())
    assert abs(weighted_midpoints / total_length).max() < 1e-6


def test_text_shape_renders_letters():
    paths, closed = text_shapes.text_to_shape("AB")
    assert closed is True
    assert len(paths) == 1
    loop = paths[0]
    assert loop[0] == loop[-1]
    assert max(x for x, _ in loop) > text_shapes.GLYPH_W + 1.5  # B is drawn after A


def test_text_loop_returns_to_start_along_an_underline():
    loop = text_shapes.close_with_underline([(0.0, 10.0), (0.0, 0.0), (6.0, 0.0)])

    assert loop[:3] == [(0.0, 10.0), (0.0, 0.0), (6.0, 0.0)]
    assert loop[-1] == (0.0, 10.0)
    baseline = -text_shapes.UNDERLINE_GAP
    return_leg = loop[3:]
    assert min(y for _, y in return_leg) == pytest.approx(baseline)
    assert (6.0, baseline) in return_leg and (0.0, baseline) in return_leg
    assert all(a != b for a, b in zip(loop, loop[1:], strict=False))


def test_text_loop_rejects_a_single_point():
    with pytest.raises(ValueError, match="at least two points"):
        text_shapes.close_with_underline([(0.0, 0.0)])


def test_vector_font_contains_every_letter_and_digit():
    assert set(string.ascii_uppercase + string.digits) <= set(text_shapes.FONT)


@pytest.mark.parametrize(
    ("prompt", "expected_name"),
    [
        ("draw the letter A in Miskolc, 10 km", "text:A"),
        ("draw the number 42 while cycling in Eger, 20 km", "text:42"),
    ],
)
def test_pipeline_builds_letter_and_number_shapes(prompt, expected_name):
    state = generate(prompt)

    assert state.shape is not None
    assert state.shape.name == expected_name
    assert state.shape.source == "text"
    assert state.intent is not None
    assert state.intent.city in {"Miskolc", "Eger"}


def test_similarity_identical_is_high():
    _, paths, _ = shape_library.heart()
    norm = geo.normalize_shape(paths)
    # Build a lat/lon route identical in shape to the normalised unit shape.
    route = [(y * 0.01, x * 0.01) for x, y in norm[0]]  # small, around (0,0)
    score = shape_similarity.shape_fidelity(norm, route)
    assert score > 0.95


def test_unit_to_latlon_roundtrip():
    lat, lon = geo.unit_to_latlon(0.5, -0.3, 47.5, 19.0, 10000.0)
    x, y = geo.latlon_to_unit(lat, lon, 47.5, 19.0, 10000.0)
    assert abs(x - 0.5) < 1e-9
    assert abs(y + 0.3) < 1e-9


# --------------------------------------------------------------------------- #
# Graph wiring                                                                #
# --------------------------------------------------------------------------- #
def test_build_nodes_wires_every_pipeline_stage():
    from gps_art_wizzard.graph import LINEAR_ORDER, build_nodes

    nodes = build_nodes()

    assert set(LINEAR_ORDER) <= set(nodes)
    assert {"refinement", "export"} <= set(nodes)
    for stage, node in nodes.items():
        assert node.name == stage
        assert callable(node.run)


# --------------------------------------------------------------------------- #
# Export formats parse back                                                   #
# --------------------------------------------------------------------------- #
def test_gpx_output_parses_back_with_point_and_metadata_integrity():
    import gpxpy

    from gps_art_wizzard.tools.gpx_writer import to_gpx

    points = [(47.4979, 19.0402), (47.5010, 19.0450), (47.5040, 19.0480)]
    xml = to_gpx(
        points,
        name="Heart in Budapest",
        sport="run",
        total_distance_m=8_400.0,
    )

    parsed = gpxpy.parse(xml)
    assert len(parsed.tracks) == 1
    assert parsed.name == "Heart in Budapest"
    track_points = parsed.tracks[0].segments[0].points
    assert [(p.latitude, p.longitude) for p in track_points] == points
    assert "8.40 km" in parsed.description
    assert "sport=run" in parsed.description


def test_tcx_output_is_well_formed_and_carries_every_trackpoint():
    import xml.etree.ElementTree as ET

    from gps_art_wizzard.tools.gpx_writer import to_tcx

    points = [(47.4979, 19.0402), (47.5010, 19.0450)]
    xml = to_tcx(
        points,
        name="Loop & Run <test>",
        sport="running",
        total_distance_m=2_500.0,
    )

    root = ET.fromstring(xml)  # raises on malformed XML
    tags = {element.tag.split("}")[-1]: element for element in root.iter()}
    assert tags["Name"].text == "Loop & Run <test>"  # escaped, then unescaped by XML
    assert float(tags["DistanceMeters"].text) == pytest.approx(2500.0)
    trackpoints = [el for el in root.iter() if el.tag.endswith("Trackpoint")]
    assert len(trackpoints) == len(points)
    latitudes = [el for el in root.iter() if el.tag.endswith("LatitudeDegrees")]
    assert [float(el.text) for el in latitudes] == pytest.approx(
        [point[0] for point in points]
    )
    # One synthetic timestamp per point at the default 1 s sampling.
    times = [
        el
        for el in root.iter()
        if el.tag.split("}")[-1] == "Time"
    ]
    assert len(times) == len(points)
    assert int(tags["TotalTimeSeconds"].text) == len(points)
