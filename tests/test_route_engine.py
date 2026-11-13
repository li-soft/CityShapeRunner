"""Focused regression tests for route geometry, snapping, and validation."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import LineString

from gps_art_wizzard.agents.export_agent import ExportAgent
from gps_art_wizzard.agents.intent_agent import IntentAgent
from gps_art_wizzard.agents.placement_agent import PlacementAgent
from gps_art_wizzard.agents.planning_agent import PlanningAgent
from gps_art_wizzard.agents.preflight_agent import PreflightAgent
from gps_art_wizzard.agents.refinement_agent import RefinementAgent
from gps_art_wizzard.agents.shape_agent import (
    _CUSTOM_SHAPE_JSON_SCHEMA,
    _SHAPE_SPEC_JSON_SCHEMA,
    ShapeAgent,
    _clear_custom_shape_cache,
    _reference_shape_payload,
    _validated_paths,
)
from gps_art_wizzard.agents.snap_agent import SnapAgent
from gps_art_wizzard.agents.validation_agent import ValidationAgent
from gps_art_wizzard.api.routes import (
    EditedRouteRequest,
    _even_sample,
    _state_to_response,
    edit_route,
)
from gps_art_wizzard.config import RoutingConfig
from gps_art_wizzard.llm import LLMResponse
from gps_art_wizzard.llm import factory as llm_factory
from gps_art_wizzard.orchestrator import Orchestrator, generate
from gps_art_wizzard.prompts import render
from gps_art_wizzard.quality import quality_gate_report
from gps_art_wizzard.state import (
    EvaluatedCandidate,
    Export,
    Intent,
    MapPlacement,
    Plan,
    RouteConcern,
    RouteDraft,
    RoutePreferences,
    RouteReadiness,
    RouteSurface,
    Shape,
    SnappedRoute,
    Validation,
    WorkflowState,
)
from gps_art_wizzard.tools import (
    geo,
    geocoder,
    gpx_writer,
    ors_client,
    shape_library,
    shape_recommender,
    shape_similarity,
    shape_uniqueness,
)
from gps_art_wizzard.tools.european_city_catalog import ADDITIONAL_EUROPEAN_CITIES
from gps_art_wizzard.tools.extended_shape_catalog import AUTHORED_OUTLINES
from gps_art_wizzard.tools.hungarian_shape_catalog import (
    HUNGARIAN_OUTLINES,
    HUNGARIAN_SHAPE_ALIASES,
)
from gps_art_wizzard.workflow_runtime import WorkflowRuntime

EXTENDED_SHAPE_NAMES = frozenset(
    {
        "airplane", "apple", "bat", "bear", "bell", "cactus", "car", "castle",
        "clover", "cloud", "duck", "elephant", "flame", "fox", "guitar", "hexagon",
        "hourglass", "house", "leaf", "location_pin", "maple_leaf", "mushroom",
        "octagon", "owl", "pear", "penguin", "pine_tree", "rocket", "shark", "shield",
        "snail", "snowflake", "speech_bubble", "spiral", "teardrop", "trophy", "tulip",
        "turtle", "umbrella", "whale",
    }
)


def test_keyword_matching_does_not_treat_letters_inside_city_as_shapes():
    cat = shape_library.find_by_keyword("draw a cat in London")
    assert cat is not None
    assert cat[0] == "cat"

    assert shape_library.find_by_keyword("jog around London") is None
    circle = shape_library.find_by_keyword("draw an O in London")
    assert circle is not None
    assert circle[0] == "circle"


def test_intent_fallback_distinguishes_a_shape_from_written_text():
    agent = IntentAgent()
    intent = agent._parse(agent._fallback("draw a heart in Budapest, 8 km").text)
    assert intent.shape == "heart"
    assert intent.text is None
    assert intent.distance_km == 8.0

    text_intent = agent._parse(agent._fallback("write HI in Berlin").text)
    assert text_intent.shape == "text"
    assert text_intent.text == "HI"

    object_word_as_text = agent._parse(agent._fallback("write BUG in Berlin").text)
    assert object_word_as_text.shape == "text"
    assert object_word_as_text.text == "BUG"


@pytest.mark.parametrize(
    ("prompt", "text", "sport"),
    [
        ("write GPS while cycling in Budapest, about 25 km", "GPS", "bike"),
        ("write LOVE while running in Poznań, about 12 km", "LOVE", "run"),
    ],
)
def test_intent_fallback_excludes_the_activity_clause_from_written_text(
    prompt: str, text: str, sport: str
) -> None:
    agent = IntentAgent()
    intent = agent._parse(agent._fallback(prompt).text)

    assert intent.shape == "text"
    assert intent.text == text
    assert intent.sport == sport


def test_bug_prompt_is_understood_as_an_insect_template_not_a_letter():
    prompt = "a bug run in Tatabánya, about 8 km"
    agent = IntentAgent()
    intent = agent._parse(agent._fallback(prompt).text)

    assert intent.shape == "bug"
    assert intent.text is None
    assert intent.city == "Tatabánya"
    assert intent.sport == "run"
    assert intent.distance_km == pytest.approx(8.0)
    assert shape_library.find_by_keyword(intent.shape)[0] == "bug"

    letter_intent = agent._parse(
        agent._fallback("draw the letter B in Tatabánya, about 8 km").text
    )
    assert letter_intent.shape == "text"
    assert letter_intent.text == "B"


@pytest.mark.parametrize("described_shape", ["bug", "platypus", "robot"])
def test_described_shape_cannot_be_reinterpreted_as_its_initial_by_an_llm(
    monkeypatch,
    described_shape,
):
    def misread_bug(*_args, **_kwargs):
        return LLMResponse(
            text=json.dumps(
                {
                    "shape": "text",
                    "text": described_shape[0].upper(),
                    "city": None,
                    "sport": "run",
                    "distance_km": None,
                    "style": None,
                    "suggest": False,
                }
            ),
            provider="test",
            model="misreading-model",
        )

    monkeypatch.setattr("gps_art_wizzard.agents.intent_agent.try_complete", misread_bug)
    state = WorkflowState(prompt=described_shape)

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == described_shape
    assert state.intent.text is None


def test_intent_model_receives_high_priority_rules_and_isolated_request_data(monkeypatch):
    captured = {}

    def capture_request(*_args, **kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps(
                {
                    "shape": "text",
                    "text": "Q",
                    "city": None,
                    "sport": "run",
                    "distance_km": None,
                    "style": None,
                    "suggest": False,
                }
            ),
            provider="test",
            model="captured-model",
        )

    monkeypatch.setattr("gps_art_wizzard.agents.intent_agent.try_complete", capture_request)
    prompt = "quokka carrying a lantern"
    state = WorkflowState(prompt=prompt)

    IntentAgent().run(state)

    assert "# Classification order" in captured["system"]
    assert "NEVER replace a named subject with its first letter" in captured["system"]
    assert prompt not in captured["system"]
    assert json.loads(captured["messages"][0]["content"]) == {"route_request": prompt}
    schema = captured["json_schema"]
    assert "Complete semantic subject" in schema["properties"]["shape"]["description"]
    assert state.intent is not None
    assert state.intent.shape == prompt
    assert state.intent.text is None


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("draw the letter A in Miskolc, 10 km", "A"),
        ("draw the number 42 while cycling in Eger, 20 km", "42"),
    ],
)
def test_intent_fallback_parses_labelled_letters_and_numbers(prompt, expected):
    agent = IntentAgent()
    intent = agent._parse(agent._fallback(prompt).text)

    assert intent.shape == "text"
    assert intent.text == expected
    assert intent.city in {"Miskolc", "Eger"}


def test_common_template_intent_skips_remote_llm(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a fully parsed template request must not call an LLM")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.intent_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(prompt="draw an 8 km heart run in Tatabánya")

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == "heart"
    assert state.intent.city == "Tatabánya"
    assert state.intent.distance_km == 8.0


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("draw a platypus in Budapest, 8 km", "platypus"),
        ("create a robot riding a bicycle in Berlin for 18 km", "robot riding a bicycle"),
        ("flying pig, Debrecen, running, 10 km", "flying pig"),
        ("trace 'octopus wearing a crown' near Győr, 12 km", "octopus wearing a crown"),
    ],
)
def test_intent_fallback_preserves_named_custom_drawings(prompt, expected):
    agent = IntentAgent()
    intent = agent._parse(agent._fallback(prompt).text)

    assert intent.shape == expected
    assert intent.suggest is False


def test_custom_shape_intent_skips_redundant_intent_model_call(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a locally parsed custom request must skip intent inference")

    monkeypatch.setattr("gps_art_wizzard.agents.intent_agent.try_complete", fail_if_called)
    state = WorkflowState(prompt="draw a platypus in Budapest, 8 km")

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == "platypus"


@pytest.mark.parametrize(
    ("prompt", "expected_shape", "expected_city", "expected_sport", "expected_distance"),
    [
        (
            "Rajzolj egy koronás polipot Budapesten, futva, 12 km",
            "koronás polipot",
            "Budapest",
            "run",
            12.0,
        ),
        (
            "Budapesten rajzolj egy szárnyas oroszlánt futva 10,5 km",
            "szárnyas oroszlánt",
            "Budapest",
            "run",
            10.5,
        ),
        (
            "Készíts egy robotot biciklin Győrben, 18 km",
            "robotot biciklin",
            "Győr",
            "bike",
            18.0,
        ),
    ],
)
def test_intent_fallback_separates_hungarian_shape_from_route_metadata(
    prompt,
    expected_shape,
    expected_city,
    expected_sport,
    expected_distance,
):
    intent = IntentAgent()._parse(IntentAgent()._fallback(prompt).text)

    assert intent.shape == expected_shape
    assert intent.city == expected_city
    assert intent.sport == expected_sport
    assert intent.distance_km == expected_distance


def test_hungarian_custom_shape_skips_redundant_intent_model_call(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a locally parsed Hungarian request must skip intent inference")

    monkeypatch.setattr("gps_art_wizzard.agents.intent_agent.try_complete", fail_if_called)
    state = WorkflowState(
        prompt="Rajzolj egy koronás polipot Budapesten, futva, 12 km"
    )

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == "koronás polipot"


def test_hungarian_suggestion_request_is_not_misread_as_a_custom_shape():
    intent = IntentAgent()._parse(
        IntentAgent()._fallback("Javasolj egy alakzatot Pécsen 8 km futással").text
    )

    assert intent.suggest is True
    assert intent.shape is None
    assert intent.city == "Pécs"


@pytest.mark.parametrize("prompt", ["draw a pickaxe in Eger", "draw an idea bulb in Pécs"])
def test_named_custom_objects_are_not_misread_as_suggestion_requests(prompt):
    agent = IntentAgent()
    intent = agent._parse(agent._fallback(prompt).text)

    assert intent.suggest is False
    assert intent.shape is not None


def test_intent_rejects_non_finite_distance_and_bounds_large_requests():
    agent = IntentAgent()
    invalid = agent._parse(
        json.dumps({"sport": "run", "distance_km": "NaN", "suggest": "false"})
    )
    assert invalid.distance_km is None
    assert invalid.suggest is False

    bounded = agent._parse(json.dumps({"sport": "run", "distance_km": 100_000}))
    assert bounded.distance_km == 60.0


def test_stitch_paths_reverses_next_stroke_to_minimise_transfer():
    paths = [
        [(0.0, 0.0), (1.0, 0.0)],
        [(5.0, 0.0), (2.0, 0.0)],
        [(8.0, 0.0), (6.0, 0.0)],
    ]
    stitched = geo.stitch_paths(paths)
    assert stitched == [
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.0),
        (5.0, 0.0),
        (6.0, 0.0),
        (8.0, 0.0),
    ]
    assert geo.unit_path_length(stitched) == pytest.approx(8.0)


def test_normalize_shape_is_invariant_to_uneven_control_point_density():
    sparse = [[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0), (0.0, 0.0)]]
    dense = [[
        (0.0, 0.0), (2.0, 0.0), (2.0, 0.2), (2.0, 0.4),
        (2.0, 0.6), (2.0, 0.8), (2.0, 1.0), (0.0, 1.0), (0.0, 0.0),
    ]]

    sparse_normalised = geo.normalize_shape(sparse)
    dense_normalised = geo.normalize_shape(dense)

    assert sparse_normalised[0][0] == pytest.approx((-0.5, -0.25))
    assert dense_normalised[0][0] == pytest.approx(sparse_normalised[0][0])
    assert dense_normalised[0][-1] == pytest.approx(sparse_normalised[0][-1])


def test_stitch_paths_globally_optimises_multi_stroke_custom_geometry():
    paths = [
        [(-1.0, 0.0), (0.0, 0.0)],
        [(1.0, 0.0), (100.0, 0.0)],
        [(2.0, 0.0), (3.0, 0.0)],
        [(101.0, 0.0), (102.0, 0.0)],
    ]

    stitched = geo.stitch_paths(paths)

    assert stitched == [
        (-1.0, 0.0),
        (0.0, 0.0),
        (2.0, 0.0),
        (3.0, 0.0),
        (1.0, 0.0),
        (100.0, 0.0),
        (101.0, 0.0),
        (102.0, 0.0),
    ]
    assert geo.unit_path_length(stitched) == pytest.approx(107.0)


def test_centripetal_smoothing_is_finite_simple_and_interpolates_uneven_controls():
    controls = [(0.0, 0.0), (0.02, 0.0), (0.8, 0.08), (1.0, 0.9), (1.4, 1.0)]

    smoothed = geo.catmull_rom_smooth(controls, subdivisions=5)

    assert all(math.isfinite(value) for point in smoothed for value in point)
    assert LineString(smoothed).is_simple
    assert np.allclose(
        [smoothed[index] for index in range(0, len(smoothed), 5)],
        controls,
    )


def test_corner_aware_smoothing_keeps_semantic_right_angle_segments_linear():
    controls = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (2.0, 1.0)]

    smoothed = geo.catmull_rom_smooth(
        controls,
        subdivisions=4,
        corner_threshold_deg=70.0,
    )

    assert controls[1] in smoothed and controls[2] in smoothed
    assert all(
        y == pytest.approx(0.0)
        for x, y in smoothed
        if 0.0 <= x < 1.0
    )
    assert all(
        x == pytest.approx(1.0)
        for x, y in smoothed
        if 0.0 < y < 1.0
    )


def test_wave_and_helix_templates_keep_recognisable_proportions():
    _, wave_paths, _ = shape_library.wave()
    assert all(
        all(next_point[0] > point[0] for point, next_point in zip(path, path[1:], strict=False))
        for path in wave_paths
    )

    _, helix_paths, _ = shape_library.helix()
    helix_y = [point[1] for path in helix_paths[:2] for point in path]
    assert max(helix_y) - min(helix_y) == pytest.approx(2.0)


def test_butterfly_is_a_short_closed_routable_silhouette():
    name, paths, closed = shape_library.butterfly()
    path = paths[0]

    assert name == "butterfly"
    assert closed is True
    assert path[0] == path[-1]
    assert 50 <= len(path) <= 150
    # The former mathematical curve travelled roughly forty unit-lengths and
    # repeatedly crossed itself. A compact outline gives ORS a feasible loop.
    assert 8.0 < geo.unit_path_length(path) < 14.0

    left = min(point[0] for point in path)
    right = max(point[0] for point in path)
    assert left == pytest.approx(-right)
    assert any(point[1] > 1.0 for point in path)  # recognisable antennae
    assert any(point[1] < -0.9 for point in path)  # lower-wing/body tip


def test_bug_template_keeps_antennae_and_three_leg_pairs_in_one_route():
    name, paths, closed = shape_library.bug()
    path = paths[0]

    assert name == "bug"
    assert closed is True
    assert path[0] == path[-1]
    assert len(paths) == 1
    assert 4.0 < geo.unit_path_length(geo.normalize_shape(paths)[0]) < 7.0
    assert min(x for x, _ in path) == pytest.approx(-1.0)
    assert max(x for x, _ in path) == pytest.approx(1.0)
    assert sum(1 for x, y in path if abs(x) >= 0.85 and y > 0.2) >= 2
    assert sum(1 for x, y in path if abs(x) >= 0.85 and abs(y) <= 0.1) >= 2
    assert sum(1 for x, y in path if abs(x) >= 0.85 and y < -0.2) >= 2


def _segments_cross(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    def orientation(
        start: tuple[float, float],
        end: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return ((end[0] - start[0]) * (point[1] - start[1])) - (
            (end[1] - start[1]) * (point[0] - start[0])
        )

    first_a = orientation(first_start, first_end, second_start)
    first_b = orientation(first_start, first_end, second_end)
    second_a = orientation(second_start, second_end, first_start)
    second_b = orientation(second_start, second_end, first_end)
    return first_a * first_b < -1e-10 and second_a * second_b < -1e-10


def test_shape_catalog_includes_both_expansion_sets():
    assert len(EXTENDED_SHAPE_NAMES) == 40
    assert EXTENDED_SHAPE_NAMES <= shape_library.SHAPES.keys()
    assert len(AUTHORED_OUTLINES) == 55
    assert AUTHORED_OUTLINES.keys() <= shape_library.SHAPES.keys()
    assert len(HUNGARIAN_OUTLINES) == 16
    assert HUNGARIAN_OUTLINES.keys() <= shape_library.SHAPES.keys()
    assert EXTENDED_SHAPE_NAMES.isdisjoint(AUTHORED_OUTLINES)
    assert EXTENDED_SHAPE_NAMES.isdisjoint(HUNGARIAN_OUTLINES)
    assert AUTHORED_OUTLINES.keys().isdisjoint(HUNGARIAN_OUTLINES)
    assert len(shape_library.SHAPES) == 145


def test_robot_template_keeps_large_robot_landmarks_after_road_snapping():
    path = AUTHORED_OUTLINES["robot"]

    assert len(path) >= 40
    assert max(y for _, y in path) >= 1.5  # bold antenna above the head
    assert any(abs(x) >= 1.1 and y >= 0.3 for x, y in path)  # broad arms
    assert any(abs(x) <= 0.3 and y >= 1.25 for x, y in path)  # antenna stem/cap
    assert any(x < -0.5 and y <= -0.95 for x, y in path)  # left leg
    assert any(x > 0.5 and y <= -0.95 for x, y in path)  # right leg
    assert any(abs(x) <= 0.16 and -0.7 <= y <= -0.6 for x, y in path)  # leg gap


@pytest.mark.parametrize(
    "shape_name",
    sorted(EXTENDED_SHAPE_NAMES | AUTHORED_OUTLINES.keys() | HUNGARIAN_OUTLINES.keys()),
)
def test_extended_shapes_are_single_routable_non_self_intersecting_paths(shape_name):
    generated = shape_library.get_shape(shape_name)
    assert generated is not None
    returned_name, paths, closed = generated

    assert returned_name == shape_name
    assert len(paths) == 1
    path = paths[0]
    assert len({(round(x, 8), round(y, 8)) for x, y in path}) >= 8
    assert closed is (shape_name != "spiral")
    assert (path[0] == path[-1]) is closed

    normalized = geo.normalize_shape(paths)[0]
    normalized_length = geo.unit_path_length(normalized)
    assert 2.2 < normalized_length < 7.0

    for first_index in range(len(path) - 1):
        for second_index in range(first_index + 2, len(path) - 1):
            if closed and first_index == 0 and second_index == len(path) - 2:
                continue
            assert not _segments_cross(
                path[first_index],
                path[first_index + 1],
                path[second_index],
                path[second_index + 1],
            ), f"{shape_name} crosses itself at segments {first_index} and {second_index}"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("draw a maple leaf in Budapest", "maple_leaf"),
        ("make a speech bubble in Debrecen", "speech_bubble"),
        ("cycle a location pin in Győr", "location_pin"),
        ("run a pine tree in Sopron", "pine_tree"),
        ("draw an airplane in Szeged", "airplane"),
        ("draw a robot in Budapest", "robot"),
        ("cycle a paper airplane in Bristol", "paper_plane"),
        ("make a watermelon in Valencia", "watermelon_slice"),
        ("fuss egy paprika alakot Szegeden", "paprika"),
        ("draw a Rubik-kocka in Budapest", "puzzle_cube"),
        ("rajzolj szürkemarhát Debrecenben", "grey_cattle"),
        ("a kürtőskalács run in Budapest", "chimney_cake"),
        ("cycle a thermal bath in Hajdúszoboszló", "thermal_bath"),
    ],
)
def test_extended_shape_keywords_resolve_to_canonical_templates(prompt, expected):
    generated = shape_library.find_by_keyword(prompt)
    assert generated is not None
    assert generated[0] == expected


def test_hungarian_shape_names_and_aliases_resolve_without_an_llm():
    for canonical_name, aliases in HUNGARIAN_SHAPE_ALIASES.items():
        probes = (canonical_name.replace("_", " "), *aliases)
        for probe in probes:
            generated = shape_library.find_by_keyword(f"rajzolj egy {probe} alakot")
            assert generated is not None, (canonical_name, probe)
            assert generated[0] == canonical_name, (canonical_name, probe, generated[0])


@pytest.mark.parametrize(
    ("prompt", "expected_shape", "expected_city", "expected_sport"),
    [
        ("Fuss egy paprika alakot Szegeden, 12 km", "paprika", "Szeged", "run"),
        ("Egy kürtőskalács Budapesten biciklivel, 24 km", "chimney_cake", "Budapest", "bike"),
        ("Rajzolj szürkemarhát Debrecenben kerékpárral, 28 km", "grey_cattle", "Debrecen", "bike"),
        ("Rajzolj egy Rubik-kockát Budapesten, 14 km", "puzzle_cube", "Budapest", "run"),
    ],
)
def test_hungarian_catalog_prompts_take_the_local_template_fast_path(
    monkeypatch,
    prompt,
    expected_shape,
    expected_city,
    expected_sport,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a Hungarian catalog request must not call an LLM")

    monkeypatch.setattr("gps_art_wizzard.agents.intent_agent.try_complete", fail_if_called)
    state = WorkflowState(prompt=prompt)

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == expected_shape
    assert state.intent.city == expected_city
    assert state.intent.sport == expected_sport


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("draw a turtle in Budapest, 14 km", "turtle"),
        ("cycle a maple leaf in Debrecen, 24 km", "maple_leaf"),
        ("draw a castle in Székesfehérvár, 20 km", "castle"),
        ("a robot run in Budapest, about 8 km", "robot"),
    ],
)
def test_extended_templates_use_the_local_intent_to_shape_pipeline(
    prompt,
    expected,
    monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("known extended templates must not call an LLM")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.intent_agent.try_complete",
        fail_if_called,
    )
    monkeypatch.setattr(
        "gps_art_wizzard.agents.shape_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(prompt=prompt)

    IntentAgent().run(state)
    ShapeAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == expected
    assert state.shape is not None
    assert state.shape.name == expected
    assert state.shape.source == "template"


def test_all_empty_shape_paths_normalise_safely():
    assert geo.normalize_shape([[], []]) == [[(0.0, 0.0)]]


def test_projection_rejects_invalid_scale_and_polar_centre():
    with pytest.raises(ValueError, match="positive"):
        geo.unit_to_latlon(0.0, 0.0, 47.5, 19.0, 0.0)
    with pytest.raises(ValueError, match="strictly"):
        geo.unit_to_latlon(0.0, 0.0, 90.0, 19.0, 100.0)


def test_unknown_offline_city_is_explicitly_marked_as_substituted():
    result = geocoder._default("Atlantis")
    assert result.name == "Budapest"
    assert result.substituted is True
    assert geocoder._default("Berlin").substituted is False


def test_known_city_geocoding_uses_local_route_database(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("known cities must not call public Nominatim")

    monkeypatch.setattr(geocoder.httpx, "get", fail_if_called)

    result = geocoder.geocode("Tatabánya")

    assert result.name == "Tatabánya"
    assert result.substituted is False
    assert result.lat == pytest.approx(47.5853)


@pytest.mark.parametrize(
    "city",
    [
        "Miskolc", "Eger", "Érd", "Szolnok", "Szigetszentmiklós", "Ózd",
        "Hajdúböszörmény", "Budaörs", "Kiskunfélegyháza", "Ajka", "Szentes",
        "Gyál", "Dunaharaszti", "Tata",
    ],
)
def test_requested_additional_cities_have_local_route_profiles(city, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("supported Hungarian cities must resolve locally")

    monkeypatch.setattr(geocoder.httpx, "get", fail_if_called)
    result = geocoder.geocode(city)

    assert result.name == city
    assert result.substituted is False
    assert geocoder.city_context(city, result)


def test_unlisted_settlement_geocoding_is_filtered_and_search_box_is_bounded(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.delenv("GEOCODE_OFFLINE", raising=False)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{
                "lat": "45.7640",
                "lon": "4.8357",
                "boundingbox": ["45.1", "46.3", "3.9", "5.8"],
                "display_name": "Lyon, France",
            }]

    def fake_get(url, *, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(
        geocoder,
        "get_settings",
        lambda: SimpleNamespace(
            geocoder=SimpleNamespace(
                nominatim_email="",
                nominatim_base_url="https://nominatim.example",
            )
        ),
    )
    monkeypatch.setattr(geocoder.httpx, "get", fake_get)

    result = geocoder.geocode("Testville")

    assert result.name == "Lyon"
    assert result.substituted is False
    assert result.bbox == pytest.approx((45.684, 45.844, 4.7157, 4.9557))
    assert captured["url"] == "https://nominatim.example/search"
    assert captured["params"]["layer"] == "address"
    assert captured["params"]["featureType"] == "settlement"


def test_invalid_unlisted_geocoder_coordinates_fall_back_safely(monkeypatch):
    monkeypatch.delenv("GEOCODE_OFFLINE", raising=False)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"lat": "nan", "lon": "4.8357", "display_name": "Broken"}]

    monkeypatch.setattr(geocoder.httpx, "get", lambda *_args, **_kwargs: Response())

    result = geocoder.geocode("Unlisted broken place")

    assert result.name == "Budapest"
    assert result.substituted is True


def test_major_hungarian_city_catalog_covers_the_ksh_top_fifty():
    assert len(geocoder.MAJOR_HUNGARIAN_CITIES) == 50
    assert len(set(geocoder.MAJOR_HUNGARIAN_CITIES)) == 50
    assert geocoder.MAJOR_HUNGARIAN_CITIES[:3] == (
        "Budapest",
        "Debrecen",
        "Szeged",
    )
    assert all(geocoder._known_default(city) is not None for city in geocoder.MAJOR_HUNGARIAN_CITIES)


def test_balaton_catalog_covers_all_official_shore_municipalities_locally(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("catalogued Balaton municipalities must resolve locally")

    monkeypatch.setattr(geocoder.httpx, "get", fail_if_called)
    cities = geocoder.BALATON_SHORE_CITIES

    assert len(cities) == 45
    assert len(set(cities)) == 45
    assert cities[0] == "Alsóörs"
    assert cities[-1] == "Zánka"
    assert set(cities) & set(geocoder.MAJOR_HUNGARIAN_CITIES) == {"Siófok"}
    assert {
        "Alsópáhok", "Cserszegtomaj", "Felsőörs", "Felsőpáhok",
        "Hévíz", "Kőröshegy", "Lovas",
    }.isdisjoint(cities)

    for city in cities:
        result = geocoder.geocode(city)
        context = geocoder.city_context(city, result)
        assert result.name == city
        assert result.substituted is False
        assert city.casefold() in geocoder._CITY_GEOGRAPHY
        assert any(term in context.casefold() for term in ("balaton", "lake", "shore"))


def test_every_balaton_shore_intent_is_parsed_without_an_llm_call(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a catalogued Balaton request must use local intent parsing")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.intent_agent.try_complete",
        fail_if_called,
    )

    for city in geocoder.BALATON_SHORE_CITIES:
        state = WorkflowState(prompt=f"draw a heart in {city}, 8 km")
        IntentAgent().run(state)
        assert state.intent is not None
        assert state.intent.city == city
        assert state.intent.shape == "heart"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("draw a heart in Lyon, 8 km", "Lyon"),
        ("a cat run near Saint-Étienne about 9 km", "Saint-Étienne"),
        ("suggest a bike route around Ho Chi Minh City for 25 km", "Ho Chi Minh City"),
        ("draw a heart in a minimalist style", None),
    ],
)
def test_unlisted_city_fallback_is_conservative(prompt, expected):
    parsed = IntentAgent()._parse(IntentAgent()._fallback(prompt).text)

    assert parsed.city == expected


def test_major_european_city_catalog_has_136_local_profiled_cities(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("catalogued European cities must resolve locally")

    monkeypatch.setattr(geocoder.httpx, "get", fail_if_called)
    cities = geocoder.MAJOR_EUROPEAN_CITIES

    assert len(ADDITIONAL_EUROPEAN_CITIES) == 106
    assert len(cities) == 136
    assert len(set(cities)) == 136
    assert set(cities).isdisjoint(geocoder.MAJOR_HUNGARIAN_CITIES)
    for city in cities:
        result = geocoder.geocode(city)
        assert result.name == city
        assert result.substituted is False
        south, north, west, east = result.bbox
        assert south < result.lat < north
        assert west < result.lon < east
        if city in ADDITIONAL_EUROPEAN_CITIES:
            assert north - south <= 0.15
            assert east - west <= 0.23
        assert city.casefold() in geocoder._CITY_GEOGRAPHY
        assert geocoder.city_context(city, result)


@pytest.mark.parametrize(
    "city",
    [
        "Érd", "Szolnok", "Szigetszentmiklós", "Stockholm", "Athens", "Kraków",
        "Balatonföldvár", "Kővágóörs", "Ábrahámhegy",
    ],
)
def test_new_major_city_intents_are_parsed_without_a_network_call(city, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a known city and shape must not call an LLM")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.intent_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(prompt=f"draw a heart in {city}, 8 km")

    IntentAgent().run(state)

    assert state.intent is not None
    assert state.intent.city == city
    assert state.intent.shape == "heart"


def test_resample_expands_two_point_line_by_arc_length():
    sampled = shape_similarity.resample(np.array([[0.0, 0.0], [1.0, 0.0]]), 9)
    assert sampled.shape == (9, 2)
    assert sampled[4].tolist() == pytest.approx([0.5, 0.0])


def test_route_similarity_is_direction_independent_and_detects_distortion():
    reference = [
        (47.0, 19.0),
        (47.0, 19.01),
        (47.01, 19.01),
        (47.01, 19.0),
    ]
    reversed_score = shape_similarity.fidelity_between_routes(reference, reference[::-1])
    distorted = [
        (47.0, 19.0),
        (47.0, 19.01),
        (47.0001, 19.02),
        (47.0, 19.03),
    ]
    distorted_score = shape_similarity.fidelity_between_routes(reference, distorted)
    assert reversed_score > 0.99
    assert distorted_score < reversed_score - 0.15


def test_closed_route_similarity_ignores_start_vertex():
    loop = [
        (47.0, 19.0),
        (47.0, 19.01),
        (47.01, 19.01),
        (47.01, 19.0),
        (47.0, 19.0),
    ]
    shifted = loop[2:-1] + loop[:3]
    assert shifted[0] == shifted[-1]
    assert shape_similarity.fidelity_between_routes(loop, shifted) > 0.98


def test_closed_route_preflight_resolution_still_ignores_start_vertex():
    loop = [
        (47.0, 19.0),
        (47.0, 19.01),
        (47.01, 19.01),
        (47.01, 19.0),
        (47.0, 19.0),
    ]
    shifted = loop[2:-1] + loop[:3]

    diagnostics = shape_similarity.similarity_diagnostics_between_routes(
        loop,
        shifted,
        n=64,
        closed_sample_floor=64,
    )

    assert diagnostics.fidelity > 0.9


def test_similarity_rejects_backtracking_scribble_inside_the_right_outline():
    reference = [
        (47.0, 19.0),
        (47.0, 19.01),
        (47.01, 19.01),
        (47.01, 19.0),
        (47.0, 19.0),
    ]
    backtracking = [
        (47.0, 19.0),
        (47.0, 19.008),
        (47.0, 19.003),
        (47.0, 19.01),
        (47.008, 19.01),
        (47.003, 19.01),
        (47.01, 19.01),
        (47.01, 19.002),
        (47.01, 19.008),
        (47.01, 19.0),
        (47.002, 19.0),
        (47.007, 19.0),
        (47.0, 19.0),
    ]

    diagnostics = shape_similarity.similarity_diagnostics_between_routes(
        reference,
        backtracking,
    )

    assert diagnostics.coverage_similarity > 0.9
    assert diagnostics.turning_similarity < 0.5
    assert diagnostics.reversal_similarity < 0.7
    assert diagnostics.route_length_ratio > 1.8
    assert diagnostics.fidelity < 0.7

    identical = shape_similarity.similarity_diagnostics_between_routes(
        reference,
        reference,
    )
    assert identical.reversal_similarity > 0.99


def test_landmark_similarity_detects_a_lost_arrow_tip_and_notches():
    reference_xy = [
        (-1.0, 0.0),
        (0.6, 0.0),
        (0.6, 0.4),
        (1.0, 0.0),
        (0.6, -0.4),
        (0.6, 0.0),
        (-1.0, 0.0),
    ]
    rounded_head_xy = [
        (-1.0, 0.0),
        (0.6, 0.0),
        (0.8, 0.2),
        (1.0, 0.0),
        (0.8, -0.2),
        (0.6, 0.0),
        (-1.0, 0.0),
    ]
    reference = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 1_000.0)
        for x, y in reference_xy
    ]
    rounded_head = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 1_000.0)
        for x, y in rounded_head_xy
    ]

    identical = shape_similarity.similarity_diagnostics_between_routes(
        reference,
        reference[::-1],
    )
    distorted = shape_similarity.similarity_diagnostics_between_routes(
        reference,
        rounded_head,
    )

    assert identical.landmark_similarity > 0.99
    assert distorted.coverage_similarity > 0.8
    assert distorted.landmark_similarity < 0.5
    assert distorted.fidelity < identical.fidelity - 0.3


def test_display_landmarks_expose_corners_but_not_arbitrary_circle_phases():
    _, arrow_paths, _ = shape_library.arrow()
    arrow_route = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 1_000.0)
        for x, y in geo.normalize_shape(arrow_paths)[0]
    ]
    _, circle_paths, _ = shape_library.circle()
    circle_route = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 1_000.0)
        for x, y in geo.normalize_shape(circle_paths)[0]
    ]

    landmarks = shape_similarity.salient_route_landmarks(arrow_route)

    assert 4 <= len(landmarks) <= 12
    assert all(-90 <= lat <= 90 and -180 <= lon <= 180 for lat, lon in landmarks)
    assert shape_similarity.salient_route_landmarks(circle_route) == []


def test_subsample_honours_closed_coordinate_budget_and_extrema():
    _, paths, _ = shape_library.heart()
    route = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 2_000.0)
        for x, y in geo.normalize_shape(paths)[0]
    ]
    sampled = ors_client._subsample(route, closed=True, max_points=50)
    assert len(sampled) <= 50
    assert sampled[0] == sampled[-1]
    original_lats = [point[0] for point in route]
    original_lons = [point[1] for point in route]
    sampled_lats = [point[0] for point in sampled]
    sampled_lons = [point[1] for point in sampled]
    assert max(sampled_lats) - min(sampled_lats) >= 0.98 * (
        max(original_lats) - min(original_lats)
    )
    assert max(sampled_lons) - min(sampled_lons) >= 0.98 * (
        max(original_lons) - min(original_lons)
    )


def test_subsample_adds_guidance_along_sparse_long_edges_and_keeps_corners():
    route = [
        geo.unit_to_latlon(x, y, 47.5, 19.0, 2_500.0)
        for x, y in geo.normalize_shape(shape_library.diamond()[1])[0]
    ]

    sampled = ors_client._subsample(route, closed=True, max_points=50)

    assert 20 <= len(sampled) <= 50
    assert sampled[0] == sampled[-1]
    assert all(corner in sampled for corner in route)
    assert max(
        geo.haversine(*start, *end)
        for start, end in zip(sampled, sampled[1:], strict=False)
    ) <= 400.0



def test_tree_is_a_single_closed_route_without_transfer_stroke():
    name, paths, closed = shape_library.tree()

    assert name == "tree"
    assert closed is True
    assert len(paths) == 1
    assert paths[0][0] == paths[0][-1]


@pytest.mark.parametrize("shape_name", ["bat", "bird", "cat", "dog"])
def test_animal_templates_are_single_closed_street_routable_silhouettes(shape_name):
    generated = shape_library.get_shape(shape_name)

    assert generated is not None
    name, paths, closed = generated
    assert name == shape_name
    assert closed is True
    assert len(paths) == 1
    assert paths[0][0] == paths[0][-1]
    assert 50 <= len(paths[0]) <= 80
    assert LineString(paths[0]).is_simple


def test_featured_animal_silhouettes_are_geometrically_distinct():
    animal_names = ("cat", "dog", "bird", "bat")

    distances = {
        (first, second): shape_uniqueness.template_distance(first, second)
        for index, first in enumerate(animal_names)
        for second in animal_names[index + 1 :]
    }

    assert min(distances.values()) > 0.20, distances


def test_shape_uniqueness_distance_ignores_safe_placement_and_traversal_changes():
    original = [[
        (-0.8, -0.5), (0.7, -0.4), (0.9, 0.2),
        (0.2, 0.9), (-0.6, 0.6), (-0.8, -0.5),
    ]]
    angle = math.radians(37.0)
    transformed_core = [
        (
            3.4 * (x * math.cos(angle) - y * math.sin(angle)) + 12.0,
            3.4 * (x * math.sin(angle) + y * math.cos(angle)) - 7.0,
        )
        for x, y in original[0][:-1]
    ]
    shifted = transformed_core[2:] + transformed_core[:2]
    reversed_shifted = list(reversed(shifted))
    transformed = [[*reversed_shifted, reversed_shifted[0]]]

    assert (
        shape_uniqueness.contour_distance(original, transformed)
        < shape_uniqueness.DUPLICATE_DISTANCE_THRESHOLD
    )


def test_registered_shape_targets_do_not_duplicate_each_other():
    distances = shape_uniqueness.catalog_pair_distances()

    assert len(distances) == math.comb(len(shape_library.SHAPES), 2)
    assert shape_uniqueness.find_catalog_duplicates() == []
    # These were formerly the exact same square under a 45-degree rotation.
    assert shape_uniqueness.template_distance("diamond", "square") > 0.10


def test_dog_keyword_uses_template_instead_of_llm_fallback(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("known dog template must not call an LLM")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.shape_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(
        prompt="a dog run in Tatabánya, 10 km",
        intent=Intent("a dog", None, "Tatabánya", "run", 10.0, None),
        plan=Plan(shape_strategy="template"),
    )

    ShapeAgent().run(state)

    assert state.shape is not None
    assert state.shape.name == "dog"
    assert state.shape.source == "template"


def test_straight_preview_closes_once_and_is_not_claimed_as_routable():
    points = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]
    route, distance, snapped = ors_client._straight_line_connector(points, closed=True)
    assert route[0] == route[-1]
    assert route[-2] != route[-1]
    assert distance > 0
    assert snapped is False


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [19.0, 47.0],
                            [19.001, 47.0],
                            [19.002, 47.0],
                        ]
                    },
                    "properties": {
                        "segments": [
                            {"distance": 100.0},
                            {"distance": 120.0},
                        ]
                    },
                }
            ]
        }


class _FakeClient:
    def __init__(self):
        self.payload = None

    def post(self, _url, *, json, headers, timeout):
        assert headers["Content-Type"] == "application/json"
        assert timeout == ors_client._HTTP_TIMEOUT
        self.payload = json
        weights = json.get("options", {}).get("profile_params", {}).get("weightings", {})
        assert all(type(value) in (int, float) for value in weights.values())
        return _FakeResponse()


@pytest.mark.parametrize("timeout_count", [1, 2])
def test_directions_retries_one_read_timeout_without_changing_guides(monkeypatch, timeout_count):
    monkeypatch.setattr(ors_client.get_settings().routing, "ors_api_key", "test-no-network")
    calls = []

    def post(_client, _url, **kwargs):
        calls.append(copy.deepcopy(kwargs["json"]))
        if len(calls) <= timeout_count:
            raise ors_client.httpx.ReadTimeout("private provider detail")
        return _FakeResponse()

    monkeypatch.setattr(ors_client.httpx.Client, "post", post)
    runtime = WorkflowRuntime(WorkflowState("square"), max_duration_seconds=75, max_llm_calls=0)
    points = [(47.0, 19.0), (47.0, 19.001), (47.0, 19.002)]
    try:
        with runtime.activate():
            routed, _, snapped, _ = ors_client.snap_route_detailed(points)
        assert len(calls) == 2
        assert calls[0] == calls[1]
        assert snapped is (timeout_count == 1)
        if snapped:
            assert routed == points
        assert runtime.trace.routing_requests == {"directions": 2}
        assert runtime.trace.routing_failures == {"directions.transport": timeout_count}
    finally:
        runtime.close_routing_client()
        ors_client.clear_directions_cache()


def test_read_timeout_retry_consumes_the_existing_directions_attempt_budget(monkeypatch):
    monkeypatch.setattr(ors_client.get_settings().routing, "ors_api_key", "test-no-network")
    calls = []

    def post(_client, _url, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == ors_client._MAX_ORS_ATTEMPTS:
            raise ors_client.httpx.ReadTimeout("late response")
        return _FakeErrorResponse()

    monkeypatch.setattr(ors_client.httpx.Client, "post", post)
    points = [(47.0 + index * 0.0001, 19.001 if index % 2 else 19.0) for index in range(100)]
    assert ors_client.snap_route_detailed(points)[2] is False
    assert len(calls) == ors_client._MAX_ORS_ATTEMPTS


@pytest.mark.parametrize("stop", ["deadline", "cancellation"])
def test_read_timeout_retry_respects_workflow_deadline_and_cancellation(monkeypatch, stop):
    import threading

    from gps_art_wizzard.workflow_runtime import WorkflowCancelledError, cancellation_scope

    monkeypatch.setattr(ors_client.get_settings().routing, "ors_api_key", "test-no-network")
    clock = [0.0]
    calls = []
    cancelled = threading.Event()

    def post(*_args, **_kwargs):
        calls.append(True)
        if stop == "deadline":
            clock[0] = 76.0
        else:
            cancelled.set()
        raise ors_client.httpx.ReadTimeout("late response")

    monkeypatch.setattr(ors_client.httpx.Client, "post", post)
    runtime = WorkflowRuntime(WorkflowState("square"), max_duration_seconds=75, max_llm_calls=0, clock=lambda: clock[0])
    try:
        with runtime.activate(), cancellation_scope(cancelled):
            if stop == "cancellation":
                with pytest.raises(WorkflowCancelledError):
                    ors_client.snap_route_detailed([(47, 19), (47, 19.001)])
            else:
                assert ors_client.snap_route_detailed([(47, 19), (47, 19.001)])[2] is False
        assert len(calls) == 1
    finally:
        runtime.close_routing_client()


@pytest.mark.parametrize("error", ["ConnectError", "ConnectTimeout", "WriteTimeout", "PoolTimeout"])
def test_other_transport_failures_do_not_multiply_routing_requests(monkeypatch, error):
    monkeypatch.setattr(ors_client.get_settings().routing, "ors_api_key", "test-no-network")
    calls = []

    def post(*_args, **_kwargs):
        calls.append(True)
        raise getattr(ors_client.httpx, error)("private provider detail")

    monkeypatch.setattr(ors_client.httpx.Client, "post", post)
    assert ors_client.snap_route_detailed([(47, 19), (47, 19.001)])[2] is False
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint", ["snap", "directions"])
def test_provider_quota_stops_later_calls_only_in_the_same_workflow(monkeypatch, endpoint):
    calls = []

    def limited_post(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(status_code=429, text="Quota exceeded", json=lambda: {})

    monkeypatch.setattr(ors_client.httpx, "post", limited_post)

    def request():
        if endpoint == "snap":
            return ors_client._snap_request("https://example.test", {}, [[19, 47]], radius=80)
        return ors_client._ors_request(
            "https://example.test", {}, [[19, 47], [19.001, 47]],
            preference="recommended", continue_straight=True, radius=80,
        )

    for _ in range(2):
        runtime = WorkflowRuntime(WorkflowState("heart"), max_duration_seconds=175, max_llm_calls=0)
        with runtime.activate():
            first = request()
            second = request()
        assert first is None if endpoint == "snap" else first.status_code == 429
        assert second is None if endpoint == "snap" else second.status_code == 429
        assert runtime.trace.routing_requests == {endpoint: 1}
        assert runtime.trace.routing_failures == {f"{endpoint}.http_429": 1}
        assert not runtime.routing_rate_limited("directions" if endpoint == "snap" else "snap")
    assert len(calls) == 2


def test_quota_keeps_cached_provider_geometry_and_cancellation(monkeypatch):
    import threading

    from gps_art_wizzard.workflow_runtime import WorkflowCancelledError, cancellation_scope

    monkeypatch.setattr(ors_client.get_settings().routing, "ors_api_key", "test-no-network")
    responses = iter([
        _FakeResponse(),
        SimpleNamespace(status_code=429, text="Quota exceeded", json=lambda: {}),
    ])
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        return next(responses)

    monkeypatch.setattr(ors_client.httpx.Client, "post", post)
    runtime = WorkflowRuntime(WorkflowState("heart"), max_duration_seconds=175, max_llm_calls=0)
    first = [(47, 19), (47, 19.001), (47, 19.002)]
    changed = [(47.001, 19), *first[1:]]
    try:
        with runtime.activate():
            routed = ors_client.snap_route_detailed(first)
            assert routed[2] is True
            assert ors_client.snap_route_detailed(changed)[2] is False
            assert ors_client.snap_route_detailed(changed)[2] is False
            assert ors_client.snap_route_detailed(first) == routed
            cancelled = threading.Event()
            cancelled.set()
            with cancellation_scope(cancelled), pytest.raises(WorkflowCancelledError):
                ors_client.snap_route_detailed(changed)
        assert len(calls) == 2
        assert runtime.trace.routing_requests == {"directions": 2}
        assert runtime.trace.routing_failures == {"directions.http_429": 1}
    finally:
        runtime.close_routing_client()
        ors_client.clear_directions_cache()


def test_ors_request_uses_boolean_and_sums_all_segment_distances():
    client = _FakeClient()
    result = ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference="recommended",
        continue_straight=True,
        radius=350,
        client=client,
    )
    assert result is not None
    route, distance = result
    assert len(route) == 3
    assert distance == pytest.approx(220.0)
    assert client.payload["continue_straight"] is True
    assert client.payload["elevation"] is True
    assert client.payload["extra_info"] == [
        "surface",
        "steepness",
        "waytype",
        "suitability",
    ]


@pytest.mark.parametrize("signal", ["warning", "metadata"])
def test_ors_request_rejects_any_skipped_unrouted_segment(signal):
    class SkippedResponse(_FakeResponse):
        def json(self):
            payload = super().json()
            if signal == "warning":
                payload["features"][0]["properties"]["warnings"] = [
                    {"code": 3, "message": "Segment was skipped"}
                ]
            else:
                payload["metadata"] = {"query": {"skip_segments": [1]}}
            return payload

    class SkippedClient:
        def post(self, *_args, **_kwargs):
            return SkippedResponse()

    result = ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference="recommended",
        continue_straight=False,
        radius=120,
        client=SkippedClient(),
    )

    assert isinstance(result, ors_client._ORSFailure)
    assert "skipped, unrouted segments" in result.message


def test_ors_request_applies_supported_route_preferences():
    client = _FakeClient()
    ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference="recommended",
        continue_straight=False,
        radius=120,
        sport="run",
        route_preferences=RoutePreferences(
            avoid_steps=True,
            avoid_ferries=True,
            avoid_fords=True,
            prefer_quiet=True,
            prefer_green=True,
        ),
        client=client,
    )

    assert client.payload["options"]["avoid_features"] == [
        "steps",
        "ferries",
        "fords",
    ]
    assert client.payload["options"]["profile_params"]["weightings"] == {
        "quiet": 1.0,
        "green": 0.8,
    }


@pytest.mark.parametrize("preference", ["shortest", "recommended"])
@pytest.mark.parametrize("quiet, green, expected", [
    (True, False, {"quiet": 1.0}),
    (False, True, {"green": 0.8}),
    (True, True, {"quiet": 1.0, "green": 0.8}),
])
def test_foot_weightings_match_the_hosted_ors_numeric_contract(preference, quiet, green, expected):
    client = _FakeClient()
    result = ors_client._ors_request(
        "https://example.test/route", {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference=preference, continue_straight=False, radius=120, sport="run",
        route_preferences=RoutePreferences(prefer_quiet=quiet, prefer_green=green), client=client,
    )
    assert isinstance(result, ors_client._ORSRouteResult)
    assert client.payload["preference"] == preference
    assert client.payload["options"]["profile_params"]["weightings"] == expected


@pytest.mark.parametrize(
    ("coordinate_count", "expected"),
    [
        (1, None),
        (2, [[90.0, 45.0]]),
        (4, [[90.0, 45.0], [0.0, 180.0], [0.0, 180.0]]),
    ],
)
def test_bearing_constraints_cover_every_routed_source_waypoint(
    coordinate_count,
    expected,
):
    coords = [[19.0 + index * 0.001, 47.0] for index in range(coordinate_count)]

    assert ors_client._bearing_constraints(coords, 450.0) == expected


def test_bearing_constraints_ignore_missing_and_non_finite_headings():
    coords = [[19.0, 47.0], [19.001, 47.0]]

    assert ors_client._bearing_constraints(coords, None) is None
    assert ors_client._bearing_constraints(coords, math.nan) is None


def test_ors_request_applies_the_first_heading_to_real_street_routing():
    client = _FakeClient()

    ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference="recommended",
        continue_straight=False,
        radius=120,
        start_direction_deg=315.0,
        client=client,
    )

    assert client.payload["bearings"] == [[315.0, 45.0], [0.0, 180.0]]
    assert client.payload["optimized"] is False


def test_ors_request_does_not_send_foot_weightings_for_a_bike_route():
    client = _FakeClient()

    ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0]],
        preference="recommended",
        continue_straight=False,
        radius=120,
        sport="bike",
        route_preferences=RoutePreferences(
            avoid_steps=True,
            prefer_quiet=True,
            prefer_green=True,
        ),
        client=client,
    )

    assert client.payload["options"] == {"avoid_features": ["steps"]}


class _ReadinessResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [19.0, 47.0, 100.0],
                            [19.001, 47.0, 104.0],
                            [19.002, 47.0, 112.0],
                            [19.003, 47.0, 106.0],
                        ]
                    },
                    "properties": {
                        "summary": {
                            "distance": 300.0,
                            "ascent": 15.0,
                            "descent": 9.0,
                        },
                        "extras": {
                            "surface": {
                                "values": [[0, 2, 3], [2, 3, 0]],
                                "summary": [
                                    {"value": 3, "distance": 240.0, "amount": 80.0},
                                    {"value": 0, "distance": 60.0, "amount": 20.0},
                                ],
                            },
                            "steepness": {
                                "values": [[0, 2, 1], [2, 3, 4]],
                                "summary": [
                                    {"value": 1, "distance": 240.0, "amount": 80.0},
                                    {"value": 4, "distance": 60.0, "amount": 20.0},
                                ],
                            },
                            "waytypes": {
                                "values": [[0, 2, 6], [2, 3, 8]],
                                "summary": [
                                    {"value": 6, "distance": 270.0, "amount": 90.0},
                                    {"value": 8, "distance": 30.0, "amount": 10.0},
                                ],
                            },
                            "suitability": {
                                "values": [[0, 1, 2], [1, 3, 8]],
                                "summary": [
                                    {"value": 2, "distance": 40.0, "amount": 13.33},
                                    {"value": 8, "distance": 260.0, "amount": 86.67},
                                ],
                            },
                        },
                    },
                }
            ]
        }


class _ReadinessClient:
    def post(self, _url, *, json, headers, timeout):
        return _ReadinessResponse()


def test_ors_readiness_reports_elevation_surfaces_and_concern_segments():
    result = ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.001, 47.0], [19.002, 47.0], [19.003, 47.0]],
        preference="recommended",
        continue_straight=False,
        radius=120,
        sport="bike",
        client=_ReadinessClient(),
    )

    assert isinstance(result, ors_client._ORSRouteResult)
    readiness = result.readiness
    assert readiness.status == "review"
    assert readiness.data_quality == "good"
    assert readiness.elevation_gain_m == pytest.approx(15.0)
    assert readiness.elevation_loss_m == pytest.approx(9.0)
    assert readiness.max_grade_percent == pytest.approx(10.55, abs=0.02)
    assert readiness.max_grade_is_lower_bound is False
    assert readiness.surface_known_share == pytest.approx(0.8)
    assert readiness.unpaved_share == pytest.approx(0.0)
    assert [surface.label for surface in readiness.surfaces] == ["Asphalt", "Unknown"]
    concern_codes = {concern.code for concern in readiness.concerns}
    assert {"low_suitability", "steep_climb", "steps", "unknown_surface"} <= concern_codes
    assert all(
        segment
        for concern in readiness.concerns
        for segment in concern.segments_preview
    )


class _FakeErrorResponse:
    status_code = 404
    text = "route not found"

    def json(self):
        return {
            "error": {
                "code": 2009,
                "message": (
                    "Route could not be found - Unable to find a route "
                    "between points 4 (19.1 47.5) and 5 (19.2 47.5)."
                ),
            }
        }


class _FakeErrorClient:
    def post(self, _url, *, json, headers, timeout):
        assert json["continue_straight"] is False
        return _FakeErrorResponse()


def test_ors_failure_preserves_internal_code_and_failed_pair():
    failure = ors_client._ors_request(
        "https://example.test/route",
        {"Content-Type": "application/json"},
        [[19.0, 47.0], [19.1, 47.1]],
        preference="recommended",
        continue_straight=False,
        radius=350,
        client=_FakeErrorClient(),
    )
    assert isinstance(failure, ors_client._ORSFailure)
    assert failure.status_code == 404
    assert failure.error_code == 2009
    assert "between points 4" in failure.message


def test_routing_default_allows_gps_art_u_turns(monkeypatch):
    monkeypatch.delenv("ORS_CONTINUE_STRAIGHT", raising=False)
    assert RoutingConfig().continue_straight is False


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.heigit.org/openrouteservice",
        "https://api.heigit.org/openrouteservice/",
        "https://api.openrouteservice.org",
    ],
)
def test_public_ors_hosts_require_an_api_key(base_url):
    assert ors_client._is_public_ors(base_url) is True


def test_self_hosted_ors_is_not_mistaken_for_the_public_service():
    assert ors_client._is_public_ors("http://ors.internal/ors") is False


def test_failed_pair_pruning_preserves_closed_loop_endpoints():
    waypoints = [
        (47.0, 19.0),
        (47.1, 19.1),
        (47.2, 19.2),
        (47.3, 19.3),
        (47.0, 19.0),
    ]
    reduced = ors_client._prune_failed_pair(
        waypoints,
        "Unable to find a route between points 1 (x) and 2 (y).",
        closed=True,
    )
    assert reduced == [
        (47.0, 19.0),
        (47.1, 19.1),
        (47.3, 19.3),
        (47.0, 19.0),
    ]


def test_snap_route_uses_self_hosted_ors_without_api_key(monkeypatch):
    routing = SimpleNamespace(
        ors_api_key="",
        ors_base_url="http://ors.internal/ors",
        snap_radius_m=350,
        preference="recommended",
        continue_straight=True,
    )
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    requests: list[list[list[float]]] = []

    def fake_request(_url, _headers, coords, **_kwargs):
        requests.append(coords)
        polyline = [(lat, lon) for lon, lat in coords]
        return polyline, geo.path_distance_m(polyline)

    monkeypatch.setattr(ors_client, "_ors_request", fake_request)
    waypoints = [
        geo.unit_to_latlon(math.cos(t), math.sin(t), 47.5, 19.0, 1_000.0)
        for t in np.linspace(0, 2 * math.pi, 200)
    ]
    route, distance, snapped = ors_client.snap_route(waypoints, closed=True)
    assert requests
    assert len(requests[0]) <= ors_client._MAX_GUIDE_COORDINATES
    assert requests[0][0] == requests[0][-1]
    assert route[0] == route[-1]
    assert distance > 0
    assert snapped is True


def test_failed_snap_reduces_waypoint_budget_without_duplicate_round(monkeypatch):
    routing = SimpleNamespace(
        ors_api_key="test",
        ors_base_url="https://api.openrouteservice.org",
        snap_radius_m=350,
        preference="recommended",
        continue_straight=True,
    )
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    request_sizes: list[int] = []

    def always_fail(_url, _headers, coords, **_kwargs):
        request_sizes.append(len(coords))
        return ors_client._ORSFailure(
            404,
            2009,
            "Route could not be found without a reported waypoint pair.",
        )

    monkeypatch.setattr(ors_client, "_ors_request", always_fail)
    waypoints = [
        (47.0 + index * 0.0001, 19.001 if index % 2 else 19.0)
        for index in range(100)
    ]
    _, _, snapped = ors_client.snap_route(waypoints)
    assert snapped is False
    assert len(request_sizes) <= ors_client._MAX_ORS_ATTEMPTS
    distinct_sizes = list(dict.fromkeys(request_sizes))
    assert len(distinct_sizes) >= 2
    assert distinct_sizes == sorted(distinct_sizes, reverse=True)


def test_snap_preflight_ranks_shape_preservation_above_collapsed_points(monkeypatch):
    routing = SimpleNamespace(
        ors_base_url="http://localhost:8080/ors",
        ors_api_key="",
        snap_radius_m=120,
    )
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))

    collapsed = [
        (47.0000, 19.0000),
        (47.0000, 19.0010),
        (47.0010, 19.0010),
        (47.0010, 19.0000),
        (47.0000, 19.0000),
    ]
    preserved = [
        (47.1000, 19.1000),
        (47.1000, 19.1010),
        (47.1010, 19.1010),
        (47.1010, 19.1000),
        (47.1000, 19.1000),
    ]

    def fake_snap(_url, _headers, locations, *, radius, client):
        assert radius == 120
        midpoint = len(locations) // 2
        output = []
        for index, location in enumerate(locations):
            snapped_location = locations[0] if index < midpoint else location
            output.append(
                {
                    "location": snapped_location,
                    "snapped_distance": 70.0 if index < midpoint else 2.0,
                }
            )
        return output

    monkeypatch.setattr(ors_client, "_snap_request", fake_snap)

    results = ors_client.preflight_route_candidates(
        [collapsed, preserved],
        sport="run",
        closed=True,
        max_guide_points=5,
    )

    assert results is not None
    assert results[0].candidate_index == 1
    assert results[0].score > results[1].score
    assert results[0].snap_coverage == 1.0
    assert results[0].shape_fidelity > 0.9


def test_snap_preflight_keeps_original_index_when_an_invalid_route_is_skipped(
    monkeypatch,
):
    routing = SimpleNamespace(
        ors_base_url="http://localhost:8080/ors",
        ors_api_key="",
        snap_radius_m=120,
    )
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))

    def identity_snap(_url, _headers, locations, *, radius, client):
        return [
            {"location": location, "snapped_distance": 1.0}
            for location in locations
        ]

    monkeypatch.setattr(ors_client, "_snap_request", identity_snap)
    results = ors_client.preflight_route_candidates(
        [
            [(47.0, 19.0)],
            [(47.1, 19.1), (47.101, 19.101), (47.102, 19.100)],
        ],
        closed=False,
    )

    assert results is not None
    assert [result.candidate_index for result in results] == [1]


def test_preflight_shared_points_use_one_lookup_without_merging_nearby_points(monkeypatch):
    routing = SimpleNamespace(ors_base_url="http://localhost:8080/ors", ors_api_key="", snap_radius_m=120)
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    monkeypatch.setattr(ors_client, "_MAX_SNAP_LOCATIONS", 5)
    square = [(47.0, 19.0), (47.0, 19.001), (47.001, 19.001), (47.001, 19.0), (47.0, 19.0)]
    nearby = [square[0], (47.00000001, 19.001), *square[2:]]
    batches = []

    def snap(_url, _headers, locations, **kwargs):
        batches.append(locations)
        return [{"location": point, "snapped_distance": 0} for point in locations]

    monkeypatch.setattr(ors_client, "_snap_request", snap)
    results = ors_client.preflight_route_candidates([square, square, nearby], closed=True, max_guide_points=5)
    by_index = {result.candidate_index: result for result in results}
    assert len(batches) == 1
    assert len(batches[0]) == len({tuple(point) for point in batches[0]}) == 5
    assert [19.001, 47.00000001] in batches[0]
    assert set(by_index) == {0, 1, 2}
    assert all(result.snap_coverage == 1 and result.shape_fidelity > 0.99 for result in results)
    assert by_index[0].score == by_index[1].score


def test_preflight_missing_tip_is_measured_against_the_complete_contour(monkeypatch):
    routing = SimpleNamespace(ors_base_url="http://localhost:8080/ors", ors_api_key="", snap_radius_m=120)
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    # Losing a straight-edge guide and losing the salient tip have equal Snap
    # coverage, but only the latter destroys this house-shaped silhouette.
    guide = [(47.0, 19.0), (47.0, 19.001), (47.0, 19.002),
             (47.001, 19.002), (47.002, 19.001), (47.001, 19.0), (47.0, 19.0)]
    measured = []
    for missing in (guide[1], guide[4]):
        def snap(_url, _headers, locations, *, missing=missing, **kwargs):
            return [None if (point[1], point[0]) == missing else
                    {"location": point, "snapped_distance": 0} for point in locations]

        monkeypatch.setattr(ors_client, "_snap_request", snap)
        measured.append(ors_client.preflight_route_candidates([guide], closed=True, max_guide_points=7)[0])
    edge, tip = measured
    assert edge.snap_coverage == tip.snap_coverage == 6 / 7
    assert edge.shape_fidelity > tip.shape_fidelity
    assert edge.score > tip.score
    assert tip.shape_fidelity < 0.9


def test_connectivity_retry_drops_reported_via_point_before_success(monkeypatch):
    routing = SimpleNamespace(
        ors_api_key="test",
        ors_base_url="https://api.openrouteservice.org",
        snap_radius_m=350,
        preference="recommended",
        continue_straight=False,
    )
    monkeypatch.setattr(ors_client, "get_settings", lambda: SimpleNamespace(routing=routing))
    request_sizes: list[int] = []
    request_radii: list[int] = []

    def fail_once_then_route(_url, _headers, coords, **kwargs):
        request_sizes.append(len(coords))
        request_radii.append(kwargs["radius"])
        assert kwargs["continue_straight"] is False
        if len(request_sizes) == 1:
            return ors_client._ORSFailure(
                404,
                2009,
                "Unable to find a route between points 4 (x) and 5 (y).",
            )
        polyline = [(lat, lon) for lon, lat in coords]
        return polyline, geo.path_distance_m(polyline)

    monkeypatch.setattr(ors_client, "_ors_request", fail_once_then_route)
    waypoints = [
        (47.0 + index * 0.0001, 19.0 + (index % 3) * 0.0001)
        for index in range(12)
    ]
    route, distance, snapped = ors_client.snap_route(waypoints)
    assert snapped is True
    assert len(route) == 11
    assert distance > 0
    assert request_sizes == [12, 11]
    assert request_radii == [350, 350]


def test_refinement_ignores_non_finite_values_and_clamps_extremes():
    draft = RouteDraft(
        center_lat=47.5,
        center_lon=19.0,
        scale_m=1_000.0,
        rotation_deg=0.0,
        lat_offset_m=0.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.8,
        waypoints=[],
        closed=False,
    )
    RefinementAgent()._apply(
        draft,
        {
            "scale_factor": "NaN",
            "rotation_delta_deg": 999,
            "lat_offset_m": 1_000_000,
            "lon_offset_m": -1_000_000,
            "simplify_tolerance": 1_000,
        },
    )
    assert draft.scale_m == 1_000.0
    assert draft.rotation_deg == 90.0
    assert draft.lat_offset_m == 20_000.0
    assert draft.lon_offset_m == -20_000.0
    assert draft.simplify_tolerance == 25.0


def test_preflight_agent_scans_city_wide_transforms_and_builds_shortlist(
    monkeypatch,
):
    workflow = SimpleNamespace(
        preflight_enabled=True,
        preflight_max_placements=180,
        preflight_shortlist=2,
        preflight_guide_points=12,
    )
    monkeypatch.setattr(
        "gps_art_wizzard.agents.preflight_agent.get_settings",
        lambda: SimpleNamespace(workflow=workflow),
    )
    observed: dict[str, int] = {}

    def fake_preflight(routes, **_kwargs):
        observed["count"] = len(routes)
        return [
            ors_client.SnapPreflightResult(1, 0.91, 1.0, 2.0, 0.9, 0.9, 0.9, 1.0),
            ors_client.SnapPreflightResult(0, 0.82, 1.0, 4.0, 0.8, 0.8, 0.8, 1.0),
        ]

    monkeypatch.setattr(ors_client, "preflight_route_candidates", fake_preflight)
    draft = RouteDraft(
        center_lat=47.5853,
        center_lon=18.4041,
        scale_m=600.0,
        rotation_deg=0.0,
        lat_offset_m=0.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.8,
        waypoints=[],
        closed=True,
        target_distance_km=8.0,
    )
    shape_data = shape_library.triangle()
    state = WorkflowState(
        prompt="triangle in Tatabánya",
        intent=Intent("triangle", None, "Tatabánya", "run", 8.0, None),
        plan=Plan(
            shape_strategy="template",
            center_lat=draft.center_lat,
            center_lon=draft.center_lon,
            city_bbox=(47.56, 47.61, 18.35, 18.46),
        ),
        shape=Shape("triangle", shape_data[1], shape_data[2]),
        route_draft=draft,
    )

    PreflightAgent().run(state)

    assert observed["count"] >= 50
    assert state.preflight_count == observed["count"]
    assert state.route_draft is not None
    assert state.route_draft.preflight_score == pytest.approx(0.91)
    assert len(state.placement_candidates) == 1
    assert state.placement_candidates[0].preflight_score == pytest.approx(0.82)


def test_orchestrator_tries_remaining_preflight_placements_until_one_routes():
    points = [(47.5, 19.0), (47.501, 19.001), (47.5, 19.0)]

    def draft(rotation: float, score: float) -> RouteDraft:
        return RouteDraft(
            center_lat=47.5,
            center_lon=19.0,
            scale_m=700.0,
            rotation_deg=rotation,
            lat_offset_m=0.0,
            lon_offset_m=0.0,
            simplify_tolerance=0.8,
            waypoints=list(points),
            closed=True,
            target_distance_km=8.0,
            preflight_score=score,
        )

    state = WorkflowState(
        prompt="heart in Budapest",
        route_draft=draft(0.0, 0.95),
        placement_candidates=[draft(30.0, 0.9), draft(90.0, 0.86)],
        snapped=SnappedRoute(list(points), 500.0, snapped=False),
        validation=Validation(0.4, 1.0, 0.3, 0.3, on_roads=False),
        errors=["snap: ORS routing failed; route is straight-line, not on roads"],
    )
    attempts: list[float] = []

    class SnapNode:
        def run(self, current):
            attempts.append(current.route_draft.rotation_deg)
            routed = current.route_draft.rotation_deg == 90.0
            current.errors = [
                error for error in current.errors if not error.startswith("snap:")
            ]
            if not routed:
                current.errors.append("snap: ORS routing failed")
            current.snapped = SnappedRoute(list(points), 8_000.0, snapped=routed)
            return current

    class ValidationNode:
        def run(self, current):
            routed = current.snapped.snapped
            current.validation = Validation(
                0.84 if routed else 0.4,
                1.0,
                0.94 if routed else 0.3,
                0.82 if routed else 0.3,
                on_roads=routed,
            )
            return current

    Orchestrator(nodes={})._recover_unroutable_placement(
        state,
        {"snap": SnapNode(), "validation": ValidationNode()},
    )

    # Candidates are measured concurrently; only coverage of every placement
    # is guaranteed, not the physical invocation order across worker threads.
    assert sorted(attempts) == [30.0, 90.0]
    assert state.route_draft.rotation_deg == 90.0
    assert state.snapped.snapped is True
    assert state.validation.on_roads is True
    assert not any(error.startswith("snap:") for error in state.errors)
    assert state.history[-1]["agent"] == "road_recovery"


def test_road_recovery_is_a_noop_without_a_recoverable_failed_primary():
    points = [(47.5, 19.0), (47.501, 19.001)]
    draft = RouteDraft(
        47.5,
        19.0,
        700.0,
        0.0,
        0.0,
        0.0,
        0.8,
        points,
        False,
        8.0,
    )

    class MustNotRun:
        def run(self, _state):
            pytest.fail("road recovery must not run for this state")

    cases = [
        WorkflowState(
            prompt="missing validation",
            route_draft=copy.deepcopy(draft),
            placement_candidates=[copy.deepcopy(draft)],
            snapped=SnappedRoute(points, 100.0, snapped=False),
            validation=None,
        ),
        WorkflowState(
            prompt="already connected",
            route_draft=copy.deepcopy(draft),
            placement_candidates=[copy.deepcopy(draft)],
            snapped=SnappedRoute(points, 100.0, snapped=True),
            validation=Validation(0.8, 1.0, 0.8, 0.8, on_roads=True),
        ),
        WorkflowState(
            prompt="no alternatives",
            route_draft=copy.deepcopy(draft),
            placement_candidates=[],
            snapped=SnappedRoute(points, 100.0, snapped=False),
            validation=Validation(0.4, 1.0, 0.3, 0.3, on_roads=False),
        ),
    ]

    for state in cases:
        original_candidates = copy.deepcopy(state.placement_candidates)
        Orchestrator(nodes={})._recover_unroutable_placement(
            state,
            {"snap": MustNotRun(), "validation": MustNotRun()},
        )
        assert state.placement_candidates == original_candidates
        assert state.history == []


def test_road_recovery_restores_primary_state_after_every_alternative_fails():
    points = [(47.5, 19.0), (47.501, 19.001)]

    def draft(rotation: float) -> RouteDraft:
        return RouteDraft(
            47.5,
            19.0,
            700.0,
            rotation,
            0.0,
            0.0,
            0.8,
            points,
            False,
            8.0,
            preflight_score=0.9 - rotation / 1_000.0,
        )

    primary_draft = draft(0.0)
    primary_snapped = SnappedRoute(points, 100.0, snapped=False)
    primary_validation = Validation(0.4, 1.0, 0.3, 0.3, on_roads=False)
    primary_errors = ["snap: primary failed", "planning: preserved"]
    state = WorkflowState(
        prompt="heart in Budapest",
        route_draft=copy.deepcopy(primary_draft),
        placement_candidates=[draft(30.0), draft(60.0)],
        snapped=copy.deepcopy(primary_snapped),
        validation=copy.deepcopy(primary_validation),
        errors=list(primary_errors),
    )

    class SnapNode:
        def run(self, current):
            current.errors = ["snap: alternative failed"]
            current.snapped = SnappedRoute(points, 120.0, snapped=False)
            return current

    class ValidationNode:
        def run(self, current):
            current.validation = Validation(
                0.35,
                1.0,
                0.25,
                0.25,
                on_roads=False,
            )
            return current

    Orchestrator(nodes={})._recover_unroutable_placement(
        state,
        {"snap": SnapNode(), "validation": ValidationNode()},
    )

    assert state.route_draft == primary_draft
    assert state.snapped == primary_snapped
    assert state.validation == primary_validation
    assert state.errors == primary_errors
    assert state.placement_candidates == []
    assert [item["attempt"] for item in state.history] == [1, 2]
    assert all(item["on_roads"] is False for item in state.history)


def test_road_recovery_skips_a_broken_alternative_and_uses_the_next_one():
    points = [(47.5, 19.0), (47.501, 19.001)]

    def draft(rotation: float) -> RouteDraft:
        return RouteDraft(
            47.5,
            19.0,
            700.0,
            rotation,
            0.0,
            0.0,
            0.8,
            points,
            False,
            8.0,
        )

    state = WorkflowState(
        prompt="heart in Budapest",
        route_draft=draft(0.0),
        placement_candidates=[draft(30.0), draft(90.0)],
        snapped=SnappedRoute(points, 100.0, snapped=False),
        validation=Validation(0.4, 1.0, 0.3, 0.3, on_roads=False),
        errors=["snap: primary failed"],
    )

    class SnapNode:
        def run(self, current):
            if current.route_draft.rotation_deg == 30.0:
                current.errors.append("snap: transient alternative error")
                raise ValueError("invalid alternative draft")
            current.errors = [
                error for error in current.errors if not error.startswith("snap:")
            ]
            current.snapped = SnappedRoute(points, 8_000.0, snapped=True)
            return current

    class ValidationNode:
        def run(self, current):
            current.validation = Validation(0.82, 1.0, 0.9, 0.8, on_roads=True)
            return current

    Orchestrator(nodes={})._recover_unroutable_placement(
        state,
        {"snap": SnapNode(), "validation": ValidationNode()},
    )

    assert state.route_draft.rotation_deg == 90.0
    assert state.snapped.snapped is True
    assert state.validation.on_roads is True
    assert state.errors == []
    assert state.history[0]["error_type"] == "ValueError"
    assert state.history[0]["on_roads"] is False
    assert state.history[1]["on_roads"] is True


def test_road_recovery_does_not_trust_validation_without_a_snapped_route():
    points = [(47.5, 19.0), (47.501, 19.001)]

    def draft(rotation: float) -> RouteDraft:
        return RouteDraft(
            47.5,
            19.0,
            700.0,
            rotation,
            0.0,
            0.0,
            0.8,
            points,
            False,
            8.0,
        )

    state = WorkflowState(
        prompt="heart in Budapest",
        route_draft=draft(0.0),
        placement_candidates=[draft(90.0)],
        snapped=SnappedRoute(points, 100.0, snapped=False),
        validation=Validation(0.8, 1.0, 0.8, 0.8, on_roads=True),
    )

    class SnapNode:
        def run(self, current):
            current.snapped = SnappedRoute(points, 8_000.0, snapped=True)
            return current

    class ValidationNode:
        def run(self, current):
            current.validation = Validation(0.82, 1.0, 0.9, 0.8, on_roads=True)
            return current

    Orchestrator(nodes={})._recover_unroutable_placement(
        state,
        {"snap": SnapNode(), "validation": ValidationNode()},
    )

    assert state.route_draft.rotation_deg == 90.0
    assert state.snapped.snapped is True


def test_preflight_shortlist_prefers_a_diverse_high_quality_alternative():
    def draft(rotation, lat_offset, lon_offset):
        return RouteDraft(
            47.5,
            19.0,
            1_000.0,
            rotation,
            lat_offset,
            lon_offset,
            0.8,
            [(47.5, 19.0), (47.51, 19.01)],
            True,
            8.0,
        )

    drafts = [
        draft(0.0, 0.0, 0.0),
        draft(2.0, 20.0, 15.0),
        draft(90.0, 3_000.0, -2_500.0),
    ]
    results = [
        ors_client.SnapPreflightResult(0, 0.90, 1.0, 2.0, 0.9, 0.9, 0.9, 1.0),
        ors_client.SnapPreflightResult(1, 0.89, 1.0, 2.0, 0.9, 0.9, 0.9, 1.0),
        ors_client.SnapPreflightResult(2, 0.84, 1.0, 3.0, 0.84, 0.84, 0.84, 1.0),
    ]

    selected = PreflightAgent._diverse_shortlist(results, drafts, 2)

    assert [result.candidate_index for result in selected] == [0, 2]


@pytest.mark.parametrize("iteration,distance_fit,corrected,limit", [
    (0, 0.4, False, None), (4, 0.4, False, None), (5, 0.4, True, None),
    (6, 0.4, True, None), (5, 0.8, False, None),
    (6, 0.4, False, 8), (7, 0.4, True, 8), (3, 0.4, True, 4),
])
def test_refinement_reserves_final_slots_for_failed_distance(monkeypatch, iteration, distance_fit, corrected, limit):
    from gps_art_wizzard.config import get_settings
    monkeypatch.setattr(get_settings().workflow, "max_refinement_iterations", 6)
    current = RouteDraft(
        47.5,
        19.0,
        1_000.0,
        0.0,
        0.0,
        0.0,
        0.8,
        [(47.5, 19.0), (47.51, 19.01)],
        True,
        8.0,
    )
    shortlisted = RouteDraft(
        47.5,
        19.0,
        900.0,
        60.0,
        1_200.0,
        -800.0,
        0.8,
        [(47.51, 18.99), (47.52, 19.0)],
        True,
        8.0,
        preflight_score=0.88,
    )
    state = WorkflowState(
        prompt="heart in Budapest",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        shape=Shape("heart", shape_library.heart()[1], True),
        route_draft=current,
        snapped=SnappedRoute(current.waypoints, 12_000.0, snapped=True),
        validation=Validation(0.5, 1.0, distance_fit, 0.45, on_roads=True),
        placement_candidates=[shortlisted],
        iterations=iteration,
        refinement_limit=limit,
    )

    RefinementAgent().run(state)

    assert state.route_draft is not shortlisted
    assert state.iterations == iteration  # the orchestrator owns the budget
    if corrected:
        assert state.route_draft.scale_m == pytest.approx(1000 * 8 / 12)
        assert state.route_draft.rotation_deg == 0.0
        assert state.placement_candidates == [shortlisted]
        assert "measured distance" in state.history[-1]["note"]
        return
    assert state.route_draft.rotation_deg == 60.0
    assert state.route_draft.lat_offset_m == 1_200.0
    assert state.placement_candidates == []
    assert "shortlist" in state.history[-1]["note"]


def test_omitted_running_distance_uses_practical_eight_kilometre_default():
    state = WorkflowState(
        prompt="draw an arrow in Tatabánya",
        intent=Intent("arrow", None, "Tatabánya", "run", None, None),
        plan=Plan(
            shape_strategy="template",
            center_lat=47.58,
            center_lon=18.39,
            city_bbox=(47.5, 18.3, 47.7, 18.5),
        ),
        shape=Shape(
            name="arrow",
            paths=shape_library.arrow()[1],
            closed=False,
        ),
    )

    PlacementAgent().run(state)

    assert state.route_draft is not None
    assert state.route_draft.target_distance_km == pytest.approx(8.0)
    assert state.route_draft.scale_m < 3_000.0


def test_user_positioned_shape_controls_the_initial_map_footprint():
    placement = MapPlacement(
        center_lat=47.5123,
        center_lon=19.0712,
        scale_m=2_350.0,
        rotation_deg=32.0,
        search_radius_m=650.0,
    )
    state = WorkflowState(
        prompt="heart in Budapest, 8 km",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        plan=Plan(
            shape_strategy="template",
            center_lat=47.4979,
            center_lon=19.0402,
            city_bbox=(47.45, 47.56, 18.95, 19.15),
            rotation_hint_deg=90.0,
            lat_offset_m=1_500.0,
        ),
        shape=Shape("heart", geo.normalize_shape(shape_library.heart()[1]), True),
        map_placement=placement,
    )

    PlacementAgent().run(state)

    assert state.route_draft is not None
    assert state.route_draft.center_lat == placement.center_lat
    assert state.route_draft.center_lon == placement.center_lon
    assert state.route_draft.scale_m == placement.scale_m
    assert state.route_draft.rotation_deg == placement.rotation_deg
    assert state.route_draft.lat_offset_m == 0.0
    assert state.route_draft.lon_offset_m == 0.0


def test_generate_wrapper_forwards_user_positioned_map_footprint(
    monkeypatch: pytest.MonkeyPatch,
):
    placement = MapPlacement(47.5123, 19.0712, 2_350.0, 32.0, 650.0)
    captured: dict[str, object] = {}

    class StubOrchestrator:
        def run(self, prompt: str, **kwargs: object) -> WorkflowState:
            captured["prompt"] = prompt
            captured.update(kwargs)
            return WorkflowState(prompt=prompt)

    monkeypatch.setattr(
        "gps_art_wizzard.orchestrator.get_orchestrator",
        lambda: StubOrchestrator(),
    )

    state = generate("heart in Budapest", map_placement=placement)

    assert state.prompt == "heart in Budapest"
    assert captured["map_placement"] is placement


def test_user_positioned_preflight_stays_near_the_selected_footprint():
    placement = MapPlacement(47.5123, 19.0712, 2_350.0, 32.0, 650.0)
    shape_data = shape_library.heart()
    state = WorkflowState(
        prompt="heart in Budapest, 8 km",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        plan=Plan(
            shape_strategy="template",
            center_lat=47.4979,
            center_lon=19.0402,
            city_bbox=(40.0, 40.1, 10.0, 10.1),
        ),
        shape=Shape(
            "heart",
            geo.normalize_shape(shape_data[1]),
            shape_data[2],
        ),
        map_placement=placement,
    )
    PlacementAgent().run(state)

    drafts = PreflightAgent()._candidate_drafts(state)

    assert drafts
    assert len(drafts) <= 135
    assert all(
        math.hypot(draft.lat_offset_m, draft.lon_offset_m)
        <= placement.search_radius_m + 1e-6
        for draft in drafts
    )
    assert {round(draft.rotation_deg, 1) for draft in drafts} <= {
        7.0,
        20.0,
        32.0,
        44.0,
        57.0,
    }


@pytest.mark.parametrize("direction", [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0])
def test_start_anchor_and_direction_control_the_first_route_segment(direction):
    anchor = (47.5853, 18.4041)
    state = WorkflowState(
        prompt="eastbound arrow in Tatabánya",
        intent=Intent("arrow", None, "Tatabánya", "run", 8.0, None),
        plan=Plan(
            shape_strategy="template",
            center_lat=47.58,
            center_lon=18.39,
            city_bbox=(47.5, 47.7, 18.3, 18.5),
        ),
        shape=Shape(
            name="line",
            paths=[[(0.0, 0.0), (1.0, 0.0)]],
            closed=False,
        ),
        start_point=anchor,
        start_direction_deg=direction,
    )

    PlacementAgent().run(state)

    assert state.route_draft is not None
    assert state.route_draft.waypoints[0] == pytest.approx(anchor)
    assert geo.bearing(*state.route_draft.waypoints[0], *state.route_draft.waypoints[1]) == pytest.approx(
        direction,
        abs=0.1,
    )


def test_snap_agent_forwards_the_first_heading_to_street_routing(monkeypatch):
    captured = {}

    def fake_snap_route_detailed(
        waypoints,
        *,
        sport,
        closed,
        route_preferences,
        start_direction_deg,
    ):
        captured.update(
            waypoints=waypoints,
            sport=sport,
            closed=closed,
            route_preferences=route_preferences,
            start_direction_deg=start_direction_deg,
        )
        return list(waypoints), 1_000.0, True, RouteReadiness()

    monkeypatch.setattr(ors_client, "snap_route_detailed", fake_snap_route_detailed)
    preferences = RoutePreferences(avoid_steps=True)
    state = WorkflowState(
        prompt="eastbound line in Tatabánya",
        intent=Intent("line", None, "Tatabánya", "run", 8.0, None),
        route_preferences=preferences,
        route_draft=RouteDraft(
            center_lat=47.58,
            center_lon=18.39,
            scale_m=1_000.0,
            rotation_deg=0.0,
            lat_offset_m=0.0,
            lon_offset_m=0.0,
            simplify_tolerance=0.8,
            waypoints=[(47.5853, 18.4041), (47.5853, 18.4141)],
            closed=False,
            target_distance_km=8.0,
            preferred_start_direction_deg=90.0,
        ),
    )

    SnapAgent().run(state)

    assert captured["start_direction_deg"] == 90.0
    assert captured["route_preferences"] is preferences


def test_refinement_shrinks_a_measured_route_that_is_over_target():
    draft = RouteDraft(
        center_lat=47.5,
        center_lon=19.0,
        scale_m=1_000.0,
        rotation_deg=0.0,
        lat_offset_m=0.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.8,
        waypoints=[(47.5, 19.0), (47.51, 19.01)],
        closed=True,
        target_distance_km=8.0,
    )
    state = WorkflowState(
        prompt="heart in Budapest, 8 km",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        shape=Shape("heart", shape_library.heart()[1], True),
        route_draft=draft,
        snapped=SnappedRoute(draft.waypoints, 10_650.0, snapped=True),
        validation=Validation(
            score=0.66,
            closure=0.9,
            distance_fit=0.37,
            shape_fidelity=0.699,
            on_roads=True,
        ),
        iterations=1,
    )

    RefinementAgent().run(state)

    assert draft.scale_m == pytest.approx(1_000.0 * 8.0 / 10.65)
    assert draft.scale_m < 1_000.0
    assert "shrink" in state.history[-1]["note"]


def test_refinement_prioritises_failed_shape_cue_within_twenty_percent_distance():
    points = [(46.25, 20.15), (46.26, 20.16), (46.25, 20.15)]
    draft = RouteDraft(
        center_lat=46.25,
        center_lon=20.15,
        scale_m=1_000.0,
        rotation_deg=0.0,
        lat_offset_m=0.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.8,
        waypoints=points,
        closed=True,
        target_distance_km=10.0,
    )
    state = WorkflowState(
        prompt="heart in Szeged, 10 km",
        intent=Intent("heart", None, "Szeged", "run", 10.0, None),
        shape=Shape("heart", shape_library.heart()[1], True),
        route_draft=draft,
        snapped=SnappedRoute(points, 8_500.0, snapped=True),
        validation=Validation(
            score=0.75, closure=1.0, distance_fit=math.exp(-0.45),
            shape_fidelity=0.8, on_roads=True,
            spatial_similarity=0.8, coverage_similarity=0.8,
            turning_similarity=0.6, landmark_similarity=0.8,
            reversal_similarity=0.8, length_similarity=0.8,
            extent_similarity=0.8,
        ),
    )

    RefinementAgent().run(state)

    assert draft.scale_m == 1_000.0
    assert draft.lon_offset_m < 0
    assert "west grid" in state.history[-1]["note"]


def test_refinement_preserves_promising_grid_alignment_during_large_scale_fix():
    draft = RouteDraft(
        center_lat=46.9,
        center_lon=18.05,
        scale_m=3_247.6,
        rotation_deg=330.0,
        lat_offset_m=-1_500.0,
        lon_offset_m=900.0,
        simplify_tolerance=0.3,
        waypoints=[(46.9, 18.05), (46.91, 18.06)],
        closed=True,
        target_distance_km=21.0,
    )
    state = WorkflowState(
        prompt="wave in Siófok",
        intent=Intent("wave", None, "Siófok", "run", 21.0, None),
        shape=Shape("wave", shape_library.wave()[1], False),
        route_draft=draft,
        snapped=SnappedRoute(draft.waypoints, 14_770.0, snapped=True),
        validation=Validation(
            score=0.63,
            closure=1.0,
            distance_fit=0.41,
            shape_fidelity=0.616,
            on_roads=True,
        ),
        iterations=3,
    )

    RefinementAgent().run(state)

    assert draft.scale_m == pytest.approx(3_247.6 * 21.0 / 14.77)
    assert draft.rotation_deg == 330.0
    assert draft.lat_offset_m == -1_500.0
    assert draft.lon_offset_m == 900.0


def test_refinement_brackets_then_escapes_a_rejected_scale_candidate():
    draft = RouteDraft(
        center_lat=47.69,
        center_lon=17.65,
        scale_m=772.1,
        rotation_deg=0.0,
        lat_offset_m=1_500.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.5,
        waypoints=[(47.69, 17.65), (47.70, 17.66)],
        closed=True,
        target_distance_km=10.0,
    )
    actual_km = 7.24
    full_factor = 10.0 / actual_km
    damped_factor = math.sqrt(full_factor)
    common = dict(
        agent="refinement",
        rotation_deg=0.0,
        lat_offset_m=1_500.0,
        lon_offset_m=0.0,
        simplify_tolerance=0.325,
    )
    state = WorkflowState(
        prompt="star in Győr, 10 km",
        intent=Intent("star", None, "Győr", "run", 10.0, None),
        shape=Shape("star", shape_library.star()[1], True),
        route_draft=draft,
        snapped=SnappedRoute(draft.waypoints, actual_km * 1_000, snapped=True),
        validation=Validation(
            score=0.557,
            closure=1.0,
            distance_fit=0.437,
            shape_fidelity=0.451,
            on_roads=True,
        ),
        history=[
            {
                **common,
                "scale_m": draft.scale_m * full_factor,
            }
        ],
        iterations=3,
    )
    agent = RefinementAgent()

    damped = agent._heuristic(state)
    assert damped["scale_factor"] == pytest.approx(damped_factor)
    assert damped["rotation_delta_deg"] is None

    state.history.append(
        {
            **common,
            "scale_m": draft.scale_m * damped_factor,
        }
    )
    grid = agent._heuristic(state)
    assert grid["scale_factor"] is None
    assert grid["lon_offset_m"] < 0
    assert "grid" in str(grid["rationale"])


def test_shape_recommender_profiles_every_registered_template():
    profiles = shape_recommender.shape_catalog_profiles()

    assert profiles.keys() == shape_library.SHAPES.keys()
    assert len(profiles) == 145
    assert all(profile.path_count >= 1 for profile in profiles.values())
    assert all(0.0 <= profile.complexity <= 1.0 for profile in profiles.values())
    assert all(0.0 <= profile.routeability <= 1.0 for profile in profiles.values())
    assert all(
        math.isfinite(profile.unit_length)
        and math.isfinite(profile.turn_energy)
        and math.isfinite(profile.axis_order)
        for profile in profiles.values()
    )


def test_every_balaton_city_has_an_explicit_numeric_route_prior():
    expected = {city.casefold() for city in geocoder.BALATON_SHORE_CITIES}

    assert set(shape_recommender.BALATON_CITY_ROUTE_PRIORS) == expected
    for city in geocoder.BALATON_SHORE_CITIES:
        context = geocoder.city_context(city, geocoder.geocode(city))
        profile = shape_recommender.analyse_city(city, context)
        assert (
            profile.grid_order,
            profile.connectivity,
            profile.barrier_risk,
            profile.terrain_risk,
            profile.radial_order,
        ) == shape_recommender.BALATON_CITY_ROUTE_PRIORS[city.casefold()]


def test_recommendation_ranking_cache_is_bounded_and_returns_fresh_lists():
    shape_recommender._rank_shapes_cached.cache_clear()
    context = geocoder.city_context("Tihany", geocoder.geocode("Tihany"))

    first = shape_recommender.rank_shapes("Tihany", context, "run", 8.0)
    first.pop()
    second = shape_recommender.rank_shapes("Tihany", context, "run", 8.0)
    info = shape_recommender._rank_shapes_cached.cache_info()

    assert len(second) == len(shape_library.SHAPES)
    assert info.hits == 1
    assert info.maxsize == 512


def test_recommendation_ranking_normalises_unexpected_request_values():
    context = geocoder.city_context("Tihany", geocoder.geocode("Tihany"))

    unexpected = shape_recommender.rank_shapes(
        "Tihany",
        context,
        "hoverboard",
        float("nan"),
    )
    defaulted = shape_recommender.rank_shapes("Tihany", context, "run", None)

    assert [item.name for item in unexpected] == [item.name for item in defaulted]


def test_balaton_recommendation_document_matches_the_runtime_ranker():
    document = (
        Path(__file__).resolve().parents[1] / "docs" / "guides" / "balaton-city-coverage.md"
    ).read_text(encoding="utf-8")
    table = document.split("## Baseline recommendation list", 1)[1].split(
        "## Regression coverage",
        1,
    )[0]
    documented: dict[str, tuple[str, str]] = {}
    for line in table.splitlines():
        if not line.startswith("| ") or line.startswith("| Municipality"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 3 and cells[0] != "---":
            documented[cells[0]] = (cells[1], cells[2])

    assert set(documented) == set(geocoder.BALATON_SHORE_CITIES)
    for city in geocoder.BALATON_SHORE_CITIES:
        context = geocoder.city_context(city, geocoder.geocode(city))
        expected = (
            ", ".join(
                item.name
                for item in shape_recommender.recommend_shapes(
                    city, context, "run", 8.0
                )
            ),
            ", ".join(
                item.name
                for item in shape_recommender.recommend_shapes(
                    city, context, "bike", 25.0
                )
            ),
        )
        assert documented[city] == expected


def test_shape_suggestion_reduces_detail_for_hilly_sparse_streets():
    sparse = shape_recommender.recommend_shapes(
        "Hilltown",
        "Hilly city with sparse, irregular and winding streets",
        "run",
        18,
    )
    dense = shape_recommender.recommend_shapes(
        "Gridtown",
        "Flat city with an excellent near-perfect dense grid",
        "run",
        18,
    )

    assert sparse[0].shape.complexity <= dense[0].shape.complexity
    assert all(item.shape.path_count == 1 for item in sparse)
    assert all(item.shape.path_count == 1 for item in dense)


def test_every_catalogued_city_gets_three_measurable_recommendations():
    cities = tuple(dict.fromkeys((
        *geocoder.MAJOR_HUNGARIAN_CITIES,
        *geocoder.BALATON_SHORE_CITIES,
        *geocoder.MAJOR_EUROPEAN_CITIES,
    )))
    primary_shapes: set[str] = set()

    for city in cities:
        geo_result = geocoder.geocode(city)
        context = geocoder.city_context(city, geo_result)
        for sport, distance_km in (("run", 10.0), ("bike", 25.0)):
            recommendations = shape_recommender.recommend_shapes(
                city,
                context,
                sport,
                distance_km,
            )
            names = [item.name for item in recommendations]
            primary_shapes.add(names[0])
            assert len(names) == 3
            assert len(set(names)) == 3
            assert "lightning" not in names
            assert all(item.shape.path_count == 1 for item in recommendations)
            assert all(item.reason.startswith("For ") for item in recommendations)

    assert len(primary_shapes) >= 4
    assert primary_shapes & EXTENDED_SHAPE_NAMES


def test_web_copy_does_not_use_long_dash_characters():
    root = Path(__file__).resolve().parents[1]
    for relative_path in ("frontend/index.html", "frontend/src/App.jsx"):
        copy = (root / relative_path).read_text(encoding="utf-8")
        assert "—" not in copy
        assert "–" not in copy


def test_distance_and_activity_change_supported_shape_detail():
    city = "Debrecen"
    context = geocoder.city_context(city, geocoder.geocode(city))
    short_run = shape_recommender.recommend_shapes(city, context, "run", 6.0)[0]
    long_run = shape_recommender.recommend_shapes(city, context, "run", 24.0)[0]
    same_distance_bike = shape_recommender.recommend_shapes(city, context, "bike", 24.0)[0]

    assert long_run.shape.complexity > short_run.shape.complexity
    assert same_distance_bike.name != long_run.name
    assert same_distance_bike.shape.complexity < long_run.shape.complexity


def test_city_suggestion_builds_three_distinct_measurable_candidates():
    agent = PlanningAgent()
    result = agent._suggestion_candidates(
        "Eger is hilly with irregular and winding streets",
        "run",
        city="Eger",
    )

    assert len(result) == 3
    assert len(set(result)) == 3
    assert all(shape_library.get_shape(name) for name in result)
    assert all(shape_recommender.analyse_shape(name).path_count == 1 for name in result)


def test_planning_records_a_plain_language_suggestion_reason():
    state = WorkflowState(prompt="suggest a 12 km run in Debrecen")
    state.intent = Intent(
        shape=None,
        text=None,
        city="Debrecen",
        sport="run",
        distance_km=12.0,
        style=None,
        suggest=True,
    )

    PlanningAgent().run(state)

    assert state.plan is not None
    assert state.plan.suggested_shape in shape_library.SHAPES
    assert len(state.plan.suggestion_candidates) == 3
    assert state.plan.suggestion_reasons.keys() == set(
        state.plan.suggestion_candidates
    )
    assert state.plan.notes is not None
    assert state.plan.notes.startswith("For running,")


def test_candidate_selection_prefers_visible_shape_over_distance_only_score():
    incumbent = Validation(
        score=0.666,
        closure=1.0,
        distance_fit=0.947,
        shape_fidelity=0.363,
        on_roads=True,
        spatial_similarity=0.363,
        coverage_similarity=0.363,
        turning_similarity=0.363,
        landmark_similarity=0.363,
        length_similarity=0.363,
        extent_similarity=0.363,
    )
    candidate = Validation(
        score=0.631,
        closure=1.0,
        distance_fit=0.411,
        shape_fidelity=0.616,
        on_roads=True,
        spatial_similarity=0.616,
        coverage_similarity=0.616,
        turning_similarity=0.616,
        landmark_similarity=0.616,
        length_similarity=0.616,
        extent_similarity=0.616,
    )

    assert Orchestrator._candidate_is_better(candidate, incumbent) is True
    assert Orchestrator._candidate_is_better(incumbent, candidate) is False


def _unexpected_node(reason: str):
    """A pipeline node that must never execute in the given scenario."""

    class UnexpectedNode:
        def run(self, _state):
            pytest.fail(reason)

    return UnexpectedNode()


class OperationNode:
    """Minimal node wrapping a callable, mirroring the agent interface."""

    def __init__(self, operation):
        self.operation = operation

    def run(self, state):
        self.operation(state)
        return state


def _template_pipeline_state(
    *,
    prompt: str,
    shape_name: str,
    city: str,
    plan: Plan,
    validation: Validation,
    requested_shape: str | None = None,
    errors: list[str] | None = None,
    candidate_count: int = 0,
    open_route: bool = False,
) -> WorkflowState:
    """Shared scaffolding for suggestion/fallback search unit tests."""
    points = (
        [(47.0, 19.0), (47.01, 19.01)]
        if open_route
        else [(47.0, 19.0), (47.01, 19.01), (47.0, 19.0)]
    )
    generated = shape_library.get_shape(shape_name)
    assert generated is not None
    return WorkflowState(
        prompt=prompt,
        requested_shape=requested_shape,
        intent=Intent(generated[0], None, city, "run", 10.0, None),
        plan=plan,
        shape=Shape(generated[0], generated[1], generated[2]),
        route_draft=RouteDraft(
            47.0, 19.0, 1_000.0, 0.0, 0.0, 0.0, 0.8, list(points), True, 10.0
        ),
        snapped=SnappedRoute(points, 10_000.0, snapped=True),
        validation=validation,
        errors=list(errors or []),
        candidate_count=candidate_count,
    )


def test_suggestion_search_skips_extra_routes_when_primary_is_already_good():
    plan = Plan(
        shape_strategy="template",
        suggested_shape="butterfly",
        suggestion_candidates=["butterfly", "heart", "diamond"],
    )
    state = _template_pipeline_state(
        prompt="suggest a run in Debrecen",
        shape_name="butterfly",
        city="Debrecen",
        plan=plan,
        validation=Validation(
            0.82,
            1.0,
            0.9,
            0.78,
            on_roads=True,
            spatial_similarity=0.78,
            coverage_similarity=0.78,
            turning_similarity=0.78,
            landmark_similarity=0.78,
            length_similarity=0.78,
            extent_similarity=0.78,
        ),
    )
    nodes = {
        name: _unexpected_node("a passing primary suggestion must not request another route")
        for name in ("shape", "placement", "snap", "validation")
    }

    Orchestrator(nodes={})._evaluate_suggestion_candidates(state, nodes)

    assert state.shape.name == "butterfly"
    assert state.history == []


def test_suggestion_search_measures_alternatives_and_keeps_best_shape():
    points = [(47.0, 19.0), (47.01, 19.01)]
    plan = Plan(
        shape_strategy="template",
        suggested_shape="crown",
        suggestion_candidates=["crown", "triangle", "diamond"],
        suggestion_reasons={
            "crown": "Crown reason.",
            "triangle": "Triangle reason.",
            "diamond": "Diamond reason.",
        },
        notes="Crown reason.",
    )
    state = _template_pipeline_state(
        prompt="suggest a run in Eger",
        shape_name="crown",
        city="Eger",
        plan=plan,
        validation=Validation(0.44, 1.0, 0.9, 0.37, on_roads=True),
        errors=["snap: stale primary failure"],
        open_route=True,
    )
    fidelity_by_shape = {"triangle": 0.58, "diamond": 0.76}

    def shape_node(current):
        name = current.intent.shape
        generated = shape_library.get_shape(name)
        assert generated is not None
        current.shape = Shape(name, generated[1], generated[2])

    def placement_node(current):
        current.route_draft = RouteDraft(
            47.0, 19.0, 1_000.0, 0.0, 0.0, 0.0, 0.8, points, True, 10.0
        )

    def snap_node(current):
        current.snapped = SnappedRoute(points, 10_000.0, snapped=True)
        current.errors = []

    def validation_node(current):
        fidelity = fidelity_by_shape[current.shape.name]
        current.validation = Validation(
            score=0.8 if fidelity >= 0.7 else 0.6,
            closure=1.0,
            distance_fit=0.9,
            shape_fidelity=fidelity,
            on_roads=True,
            spatial_similarity=fidelity,
            coverage_similarity=fidelity,
            turning_similarity=fidelity,
            landmark_similarity=fidelity,
            length_similarity=fidelity,
            extent_similarity=fidelity,
        )

    nodes = {
        "shape": OperationNode(shape_node),
        "placement": OperationNode(placement_node),
        "snap": OperationNode(snap_node),
        "validation": OperationNode(validation_node),
    }

    Orchestrator(nodes={})._evaluate_suggestion_candidates(state, nodes)

    assert state.shape.name == "diamond"
    assert state.intent.shape == "diamond"
    assert state.plan.suggested_shape == "diamond"
    assert state.plan.notes == "Diamond reason."
    assert state.validation.shape_fidelity == pytest.approx(0.76)
    assert state.errors == []
    assert {
        entry["shape"]
        for entry in state.history
        if entry.get("agent") == "suggestion_search"
    } == {"triangle", "diamond"}


def test_failed_explicit_shape_is_retained_for_user_review():
    points = [(47.0, 19.0), (47.01, 19.01), (47.0, 19.0)]
    plan = Plan(
        shape_strategy="template",
        fallback_candidates=["triangle", "diamond", "arrow"],
    )
    state = _template_pipeline_state(
        prompt="a cat run in Tatabánya",
        shape_name="cat",
        city="Tatabánya",
        plan=plan,
        requested_shape="cat",
        validation=Validation(
            0.48,
            1.0,
            0.9,
            0.42,
            on_roads=True,
            coverage_similarity=0.45,
            turning_similarity=0.40,
            length_similarity=0.50,
            extent_similarity=0.60,
            route_length_ratio=1.8,
        ),
        candidate_count=7,
    )
    fidelity_by_shape = {"triangle": 0.62, "diamond": 0.82}

    def shape_node(current):
        generated = shape_library.get_shape(current.intent.shape)
        assert generated is not None
        current.shape = Shape(generated[0], generated[1], generated[2])

    def placement_node(current):
        if current.route_draft is None:
            current.route_draft = RouteDraft(
                47.0, 19.0, 1_000.0, 0.0, 0.0, 0.0, 0.8, points, True, 10.0
            )
        current.route_draft.waypoints = points

    def snap_node(current):
        current.candidate_count += 1
        current.snapped = SnappedRoute(points, 10_000.0, snapped=True)

    def validation_node(current):
        fidelity = fidelity_by_shape[current.shape.name]
        current.validation = Validation(
            score=0.84 if fidelity >= 0.7 else 0.66,
            closure=1.0,
            distance_fit=0.95,
            shape_fidelity=fidelity,
            on_roads=True,
            spatial_similarity=fidelity,
            coverage_similarity=fidelity,
            turning_similarity=fidelity,
            landmark_similarity=fidelity,
            length_similarity=fidelity,
            extent_similarity=fidelity,
            route_length_ratio=1.0,
        )

    nodes = {
        "shape": OperationNode(shape_node),
        "placement": OperationNode(placement_node),
        "snap": OperationNode(snap_node),
        "validation": OperationNode(validation_node),
    }

    Orchestrator(nodes={})._evaluate_fallback_candidates(state, nodes)

    assert state.shape.name == "cat"
    assert state.intent.shape == "cat"
    assert state.fit_decision is not None
    assert state.fit_decision.substituted is False
    assert state.fit_decision.requested_shape == "cat"
    assert state.fit_decision.selected_shape == "cat"
    assert state.fit_decision.candidates_tested == ["cat"]
    assert state.validation.shape_fidelity == pytest.approx(0.42)
    assert any("retained for your review" in reason for reason in state.fit_decision.reasons)
    assert state.candidate_count == 7


def test_validation_cap_preserves_fidelity_ordering(monkeypatch):
    points = [(47.0, 19.0), (47.01, 19.01), (47.0, 19.0)]

    def validate_with_fidelity(fidelity):
        monkeypatch.setattr(
            shape_similarity,
            "similarity_diagnostics_between_routes",
            lambda *_args, **_kwargs: shape_similarity.SimilarityDiagnostics(
                fidelity=fidelity,
                spatial_similarity=fidelity,
                coverage_similarity=fidelity,
                turning_similarity=fidelity,
                length_similarity=fidelity,
                extent_similarity=fidelity,
                route_length_ratio=1.0,
                mean_deviation_ratio=0.0,
            ),
        )
        state = WorkflowState(
            prompt="shape route",
            intent=Intent("circle", None, "Tatabánya", "run", 10.0, None),
            shape=Shape("circle", shape_library.circle()[1], True),
            route_draft=RouteDraft(
                47.0,
                19.0,
                1_000.0,
                0.0,
                0.0,
                0.0,
                0.8,
                points,
                True,
                10.0,
            ),
            snapped=SnappedRoute(points, 10_000.0, snapped=True),
        )
        ValidationAgent().run(state)
        assert state.validation is not None
        return state.validation.score

    low = validate_with_fidelity(0.363)
    promising = validate_with_fidelity(0.616)

    assert promising > low + 0.1
    assert promising < 0.72


def test_known_template_planning_is_local_and_uses_stable_city_offset(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("known template planning must not call an LLM")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.planning_agent.try_complete",
        fail_if_called,
    )
    budapest = WorkflowState(
        prompt="heart in Budapest",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
    )
    tatabanya = WorkflowState(
        prompt="heart in Tatabánya",
        intent=Intent("heart", None, "Tatabánya", "run", 8.0, None),
    )

    PlanningAgent().run(budapest)
    PlanningAgent().run(tatabanya)

    assert budapest.plan is not None
    assert budapest.plan.rotation_hint_deg == 0.0
    assert budapest.plan.lat_offset_m == 0.0
    assert budapest.plan.lon_offset_m == 1_500.0
    assert tatabanya.plan is not None
    assert tatabanya.plan.lat_offset_m == 0.0
    assert tatabanya.plan.lon_offset_m == -1_500.0


def test_template_keyword_alias_skips_llm_planning(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("template keyword aliases must not call an LLM planner")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.planning_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(
        prompt="a dog run in Győr, 10 km",
        intent=Intent("a dog", None, "Győr", "run", 10.0, None),
    )

    PlanningAgent().run(state)

    assert state.plan is not None
    assert state.plan.shape_strategy == "template"
    assert state.plan.lat_offset_m == 1_500.0


def test_custom_shape_planning_is_local_and_commits_free_draw_strategy(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("custom placement planning must not spend a model call")

    monkeypatch.setattr(
        "gps_art_wizzard.agents.planning_agent.try_complete",
        fail_if_called,
    )
    state = WorkflowState(
        prompt="a platypus in Budapest, 8 km",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
    )

    PlanningAgent().run(state)

    assert state.plan is not None
    assert state.plan.shape_strategy == "llm"
    assert state.plan.difficulty == "hard"


def test_llm_shape_validation_drops_bad_points_and_bounds_payload():
    paths = _validated_paths(
        [
            [[0, 0], [0, 0], [1, 1], [float("nan"), 2], [2_000_000, 1]],
            [["bad", 0]],
        ]
    )
    assert paths == [[(0.0, 0.0), (1.0, 1.0)]]
    with pytest.raises(ValueError, match="no drawable"):
        _validated_paths([[[0, 0]], [["bad", 1]]])


def test_custom_shape_generation_keeps_requested_name_and_closes_outline(monkeypatch):
    _clear_custom_shape_cache()
    response = LLMResponse(
        text=json.dumps(
            {
                "name": "generic animal",
                "paths": [
                    [[0, 0], [1, 0], [1.2, 0.5], [0.7, 1], [0, 1], [-0.2, 0.5]]
                ],
                "closed": True,
            }
        ),
        provider="test-provider",
    )
    monkeypatch.setattr(
        "gps_art_wizzard.agents.shape_agent.try_complete",
        lambda *_args, **_kwargs: response,
    )
    state = WorkflowState(
        prompt="draw a platypus in Budapest",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert state.shape is not None
    assert state.shape.name == "platypus"
    assert state.shape.source == "llm"
    assert state.shape.closed is True
    assert state.shape.paths[0][0] == state.shape.paths[0][-1]
    assert LineString(state.shape.paths[0]).is_simple


@pytest.mark.parametrize(
    ("prompt", "expected_shape", "expected_city", "expected_sport", "expected_distance"),
    [
        (
            "a lighthouse with crashing waves run in Barcelona, about 9 km",
            "lighthouse with crashing waves",
            "Barcelona",
            "run",
            9.0,
        ),
        (
            "a dragon wearing a crown in Berlin, about 24 km while cycling",
            "dragon wearing a crown",
            "Berlin",
            "bike",
            24.0,
        ),
        (
            "Rajzolj egy gőzölgő kávéscsészét Budapesten, futva, 8 km",
            "gőzölgő kávéscsészét",
            "Budapest",
            "run",
            8.0,
        ),
    ],
)
def test_arbitrary_described_shapes_reach_ai_generation_with_route_metadata(
    monkeypatch,
    prompt,
    expected_shape,
    expected_city,
    expected_sport,
    expected_distance,
):
    _clear_custom_shape_cache()
    captured = {}
    response = LLMResponse(
        text=json.dumps(
            {
                "name": expected_shape,
                "paths": [[
                    [-1, 2], [1, 2], [1, 1.2], [2, 1.2], [2, 0.6],
                    [1.2, 0.6], [1.2, -1], [0.8, -1], [0.8, -2],
                    [0.2, -2], [0.2, -1], [-0.2, -1], [-0.2, -2],
                    [-0.8, -2], [-0.8, -1], [-1.2, -1], [-1.2, 0.6],
                    [-2, 0.6], [-2, 1.2], [-1, 1.2], [-1, 2],
                ]],
                "closed": True,
            }
        ),
        provider="test-provider",
    )

    def complete(*_args, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(prompt=prompt)

    IntentAgent().run(state)
    PlanningAgent().run(state)
    ShapeAgent().run(state)

    assert state.intent is not None
    assert state.intent.shape == expected_shape
    assert state.intent.city == expected_city
    assert state.intent.sport == expected_sport
    assert state.intent.distance_km == expected_distance
    assert state.plan is not None
    assert state.plan.shape_strategy == "llm"
    assert state.shape is not None
    assert state.shape.name == expected_shape
    assert state.shape.source == "llm"
    assert state.shape.closed is True
    assert state.errors == []
    assert captured["json_schema"] is _SHAPE_SPEC_JSON_SCHEMA


def test_custom_shape_generation_requests_provider_enforced_schema(monkeypatch):
    _clear_custom_shape_cache()
    captured = {}
    response = LLMResponse(
        text=json.dumps(
            {
                "name": "platypus",
                "paths": [
                    [[0, 0], [1, 0], [1.1, 0.4], [0.8, 1], [0, 1], [-0.2, 0.4]]
                ],
                "closed": True,
            }
        ),
        provider="test-provider",
    )

    def complete(*_args, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(
        prompt="draw a platypus in Budapest",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert captured["json_schema"] is _SHAPE_SPEC_JSON_SCHEMA
    schema = _CUSTOM_SHAPE_JSON_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "name",
        "variants",
        "preferred_variant",
    }
    variants = schema["properties"]["variants"]
    assert variants["minItems"] == 1
    assert variants["maxItems"] == 4
    assert variants["items"]["additionalProperties"] is False
    program = variants["items"]["properties"]["program"]
    assert program["properties"]["strokes"]["maxItems"] == 8


def test_custom_shape_uses_valid_alternative_when_ai_preference_crosses_itself(monkeypatch):
    _clear_custom_shape_cache()
    calls = 0
    response = LLMResponse(
        text=json.dumps(
            {
                "name": "platypus",
                "recognition_features": [
                    "wide duck bill",
                    "low rounded body",
                    "broad beaver tail",
                ],
                "variants": [
                    {
                        "paths": [
                            [[0, 0], [1, 1], [0, 1], [1, 0], [0.5, -0.2], [0, 0]]
                        ],
                        "closed": True,
                    },
                    {
                        "paths": [[
                            [0, 0], [1, 0], [1.1, 0.4], [0.8, 1],
                            [0, 1], [-0.2, 0.4], [0, 0],
                        ]],
                        "closed": True,
                    },
                ],
                "preferred_variant": 0,
            }
        ),
        provider="test-provider",
    )

    def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return response

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(
        prompt="draw a platypus in Budapest",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert calls == 1
    assert state.shape is not None
    assert state.shape.source == "llm"
    assert state.errors == []


def _semantic_review_response(feature_ids: list[str]) -> LLMResponse:
    score = 0.9
    return LLMResponse(
        text=json.dumps(
            {
                "reviews": [
                    {
                        "candidate_index": 0,
                        "score": score,
                        "subject_match": score,
                        "silhouette_quality": score,
                        "route_readability": score,
                        "cue_results": [
                            {
                                "feature_id": feature_id,
                                "present": True,
                                "score": score,
                                "reason": "large and visible",
                            }
                            for feature_id in feature_ids
                        ],
                        "missing_features": [],
                        "wrong_relations": [],
                        "repair_instructions": [],
                    }
                ],
                "recommended_candidate": 0,
            }
        ),
        provider="reviewer",
    )


def test_invalid_custom_geometry_retains_validated_semantic_scaffold(monkeypatch):
    _clear_custom_shape_cache()
    responses = iter(
        [
            LLMResponse(
                text=json.dumps(
                    {
                        "name": "bow tie",
                        "paths": [
                            [[0, 0], [1, 1], [0, 1], [1, 0], [0.5, -0.2], [0, 0]]
                        ],
                        "closed": True,
                    }
                ),
                provider="test-provider",
            ),
            _semantic_review_response(
                ["overall_silhouette", "main_body_mass", "distinctive_feature"]
            ),
        ]
    )
    calls = 0

    def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(
        prompt="draw a platypus in Budapest",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert calls == 2
    assert state.shape is not None
    assert state.shape.name == "platypus"
    assert state.shape.source == "llm"
    assert state.shape.generation_strategy == "platypus ShapeSpec scaffold"


def test_catalog_duplicate_custom_geometry_uses_distinct_semantic_scaffold(monkeypatch):
    _clear_custom_shape_cache()
    _, star_paths, star_closed = shape_library.star()
    responses = iter(
        [
            LLMResponse(
                text=json.dumps(
                    {
                        "name": "stock star",
                        "paths": star_paths,
                        "closed": star_closed,
                    }
                ),
                provider="test-provider",
            ),
            _semantic_review_response(
                ["overall_silhouette", "main_body_mass", "distinctive_feature"]
            ),
        ]
    )
    prompts = []

    def complete(*_args, **kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return next(responses)

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(
        prompt="draw a crowned platypus in Budapest",
        intent=Intent("crowned platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert len(prompts) == 2
    assert "strict visual critic" in prompts[1]
    assert state.shape is not None
    assert state.shape.source == "llm"
    assert state.shape.generation_strategy == "platypus ShapeSpec scaffold"
    assert (
        shape_uniqueness.nearest_catalog_shape(state.shape.paths).distance
        > shape_uniqueness.DUPLICATE_DISTANCE_THRESHOLD
    )


def test_far_apart_custom_strokes_use_connected_semantic_scaffold(monkeypatch):
    _clear_custom_shape_cache()
    responses = iter(
        [
            LLMResponse(
                text=json.dumps(
                    {
                        "name": "disconnected doodle",
                        "paths": [
                            [[-1, -0.6], [-0.8, -0.6], [-0.9, -0.4], [-1, -0.6]],
                            [[0.8, 0.4], [1, 0.4], [0.9, 0.6], [0.8, 0.4]],
                        ],
                        "closed": True,
                    }
                ),
                provider="test-provider",
            ),
            _semantic_review_response(
                ["overall_silhouette", "main_body_mass", "distinctive_feature"]
            ),
        ]
    )
    prompts = []

    def complete(*_args, **kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return next(responses)

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)
    state = WorkflowState(
        prompt="draw a platypus in Budapest",
        intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
        plan=Plan(shape_strategy="llm"),
    )

    ShapeAgent().run(state)

    assert len(prompts) == 2
    assert "strict visual critic" in prompts[1]
    assert state.shape is not None
    assert len(state.shape.paths) == 1
    assert state.shape.generation_strategy == "platypus ShapeSpec scaffold"


def test_custom_shape_prompt_requires_semantic_cues_and_non_stock_contours():
    spec_prompt = render(
        "shape_spec", shape='"platypus"', style='"none"', route_context="{}"
    ).lower()
    prompt = render(
        "shape",
        shape='"platypus"',
        style='"none"',
        spec="{}",
        candidate_count=3,
        route_context="{}",
        references="[]",
    ).lower()

    assert "3-6 cues" in spec_prompt
    assert "meaningfully different route-native candidates" in prompt
    assert "feature_id" in prompt
    assert "catalog anchors" in prompt
    assert "stock icon" in prompt


def test_compound_custom_shape_uses_its_first_known_subject_as_reference():
    reference = _reference_shape_payload("a robot holding an umbrella")

    assert reference is not None
    assert reference["name"] == "robot"
    assert reference["closed"] is True
    assert len(reference["paths"]) == 1
    assert len(reference["paths"][0]) >= 40
    assert _reference_shape_payload("a completely unknown frobnicator") is None


def test_successful_custom_geometry_is_cached_without_sharing_mutable_paths(monkeypatch):
    _clear_custom_shape_cache()
    calls = 0
    response = LLMResponse(
        text=json.dumps(
            {
                "name": "platypus",
                "paths": [
                    [[0, 0], [1, 0], [1.1, 0.4], [0.8, 1], [0, 1], [-0.2, 0.4], [0, 0]]
                ],
                "closed": True,
            }
        ),
        provider="test-provider",
    )

    def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return response

    monkeypatch.setattr("gps_art_wizzard.agents.shape_agent.try_complete", complete)

    def custom_state() -> WorkflowState:
        return WorkflowState(
            prompt="draw a platypus in Budapest",
            intent=Intent("platypus", None, "Budapest", "run", 8.0, None),
            plan=Plan(shape_strategy="llm"),
        )

    first = custom_state()
    ShapeAgent().run(first)
    assert first.shape is not None
    first.shape.paths[0][0] = (99.0, 99.0)

    second = custom_state()
    ShapeAgent().run(second)

    assert calls == 1
    assert second.shape is not None
    assert second.shape.paths[0][0] != (99.0, 99.0)


def test_preview_sampler_never_exceeds_limit_and_keeps_endpoints():
    points = list(range(1_000))
    sampled = _even_sample(points, 500)
    assert len(sampled) == 500
    assert sampled[0] == 0
    assert sampled[-1] == 999


def test_candidate_download_keeps_full_geometry_beyond_the_map_preview():
    points = [(47.0 + index * 0.00001, 19.0) for index in range(600)]
    readiness = RouteReadiness(
        status="ready",
        data_quality="good",
        elevation_available=True,
        elevation_gain_m=42.0,
        elevation_loss_m=38.0,
        max_grade_percent=4.2,
        surface_available=True,
        surface_known_share=1.0,
        unpaved_share=0.0,
        surfaces=[RouteSurface(3, "Asphalt", 650.0, 1.0, "paved")],
        concerns=[
            RouteConcern(
                "unknown_waytype",
                "Road type data gap",
                "Check this mapped section.",
                "info",
                12.0,
                0.02,
                1,
                [points[:2]],
            )
        ],
    )
    validation = Validation(
        score=0.9,
        closure=1.0,
        distance_fit=0.9,
        shape_fidelity=0.9,
        on_roads=True,
        spatial_similarity=0.9,
        coverage_similarity=0.9,
        turning_similarity=0.9,
        landmark_similarity=0.9,
        length_similarity=0.9,
        extent_similarity=0.9,
        route_point_count=len(points),
        guide_point_count=len(points),
    )
    candidate = EvaluatedCandidate(
        shape_name="line",
        shape_source="test",
        points=points,
        ideal_points=points,
        total_distance_m=geo.path_distance_m(points),
        snapped=True,
        closed=False,
        target_distance_km=None,
        validation=validation,
        rotation_deg=0.0,
        scale_m=1_000.0,
        lat_offset_m=0.0,
        lon_offset_m=0.0,
        readiness=readiness,
    )
    state = WorkflowState(
        prompt="line route",
        intent=Intent("line", None, "Budapest", "run", None, None),
        shape=Shape("line", [[(0.0, 0.0), (1.0, 0.0)]], False, "test"),
        snapped=SnappedRoute(
            points,
            candidate.total_distance_m,
            snapped=True,
            readiness=readiness,
        ),
        validation=validation,
        candidates=[candidate],
    )

    response = _state_to_response(state)

    assert response["candidates"][0]["points_preview"] == [list(point) for point in points]
    assert response["points_preview"] == [list(point) for point in points]
    assert response["candidates"][0]["gpx"].count("<trkpt") == 600
    candidate_readiness = response["candidates"][0]["details"]["readiness"]
    assert candidate_readiness["elevation_gain_m"] == 42.0
    assert candidate_readiness["surfaces"][0]["label"] == "Asphalt"
    assert candidate_readiness["concerns"][0]["segments_preview"]
    assert response["route_details"]["readiness"]["status"] == "ready"


def test_export_is_stateless_for_a_valid_road_route(monkeypatch):
    monkeypatch.delenv("EXPORT_DIR", raising=False)
    points = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]
    state = WorkflowState(
        prompt="cat route",
        intent=Intent("cat", None, "Budapest", "run", 1.0, None),
        shape=Shape("diamond", [[(0.0, 0.0), (1.0, 1.0)]], True),
        snapped=SnappedRoute(points, geo.path_distance_m(points), snapped=True),
        validation=Validation(
            score=0.9,
            closure=1.0,
            distance_fit=0.8,
            shape_fidelity=0.9,
            on_roads=True,
            spatial_similarity=0.9,
            coverage_similarity=0.9,
            turning_similarity=0.9,
            landmark_similarity=0.9,
            length_similarity=0.9,
            extent_similarity=0.9,
        ),
    )
    ExportAgent().run(state)
    assert state.export is not None
    assert "<gpx" in state.export.gpx
    assert state.export.name == "diamond in Budapest"
    assert "diamond in Budapest" in state.export.gpx
    assert state.export.file_paths == {}


def test_export_prepares_route_that_misses_automatic_quality_targets():
    points = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]
    state = WorkflowState(
        prompt="heart route",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        snapped=SnappedRoute(points, 10_650.0, snapped=True),
        validation=Validation(
            score=0.66,
            closure=1.0,
            distance_fit=0.37,
            shape_fidelity=0.75,
            on_roads=True,
        ),
    )

    ExportAgent().run(state)

    assert state.export is not None
    assert "<gpx" in state.export.gpx
    assert any("explicit user acceptance" in error for error in state.errors)


def test_one_failed_shape_component_rejects_an_otherwise_high_scoring_route():
    validation = Validation(
        score=0.92,
        closure=1.0,
        distance_fit=0.95,
        shape_fidelity=0.86,
        on_roads=True,
        spatial_similarity=0.9,
        coverage_similarity=0.9,
        turning_similarity=0.88,
        landmark_similarity=0.52,
        length_similarity=0.9,
        extent_similarity=0.91,
    )

    report = quality_gate_report(
        validation,
        closed=True,
        candidate_shape="arrow",
        selected_shape="arrow",
    )

    assert report["passed"] is False
    assert report["shape_following"] is False
    assert report["failed_gates"] == ["landmark_similarity"]


def test_unintended_route_reversals_are_an_independent_quality_gate():
    validation = Validation(
        score=0.92,
        closure=1.0,
        distance_fit=0.95,
        shape_fidelity=0.86,
        on_roads=True,
        spatial_similarity=0.9,
        coverage_similarity=0.9,
        turning_similarity=0.88,
        landmark_similarity=0.9,
        reversal_similarity=0.45,
        length_similarity=0.9,
        extent_similarity=0.91,
    )

    report = quality_gate_report(
        validation,
        closed=True,
        candidate_shape="heart",
        selected_shape="heart",
    )

    assert report["passed"] is False
    assert report["failed_gates"] == ["reversal_similarity"]


def test_validation_retains_every_fully_routed_candidate_for_the_editor():
    ideal = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]
    target_km = geo.path_distance_m(ideal) / 1000.0
    state = WorkflowState(
        prompt="heart route",
        intent=Intent("heart", None, "Budapest", "run", target_km, None),
        shape=Shape("heart", shape_library.heart()[1], True),
        route_draft=RouteDraft(
            47.0,
            19.0,
            1_000.0,
            0.0,
            0.0,
            0.0,
            0.8,
            ideal,
            True,
            target_km,
        ),
        snapped=SnappedRoute(ideal, geo.path_distance_m(ideal), snapped=True),
    )

    ValidationAgent().run(state)
    state.route_draft.rotation_deg = 90.0
    ValidationAgent().run(state)

    assert len(state.candidates) == 2
    assert state.candidates[0].rotation_deg == 0.0
    assert state.candidates[1].rotation_deg == 90.0
    # Attempts generated while evaluating fallbacks/suggestions stay in the
    # audit, but must not leak into the selected shape's route selector.
    state.candidates[1].shape_name = "diamond"
    state.plan = Plan(
        shape_strategy="template",
        suggested_shape="heart",
        notes="For running, it matches the available street-network detail and stays on one continuous stroke.",
    )
    response = _state_to_response(state)
    assert response["suggested_shape"] == "heart"
    assert response["suggestion_reason"].startswith("For running,")
    assert len(response["candidates"]) == 1
    assert response["candidates"][0]["shape_name"] == "heart"
    assert response["candidates"][0]["verification"]["passed"] is True
    assert "<gpx" in response["candidates"][0]["gpx"]
    assert len(response["candidate_audit"]) == 2
    assert response["candidate_summary"]["other_shape_count"] == 1
    assert all(candidate["points_preview"] for candidate in response["candidates"])
    assert "landmark_preview" in response
    assert all("landmark_preview" in candidate for candidate in response["candidates"])
    assert all(
        -90 <= point[0] <= 90 and -180 <= point[1] <= 180
        for candidate in response["candidates"]
        for point in candidate["landmark_preview"]
    )

    # Missing an automatic component target keeps the selected-shape route
    # visible and exportable after user review; it is no longer silently
    # removed from the selector.
    state.candidates[0].validation.landmark_similarity = 0.4
    review_response = _state_to_response(state)
    assert len(review_response["candidates"]) == 1
    assert review_response["candidates"][0]["verification"]["passed"] is False
    assert review_response["candidates"][0]["requires_user_acceptance"] is True
    assert "<gpx" in review_response["candidates"][0]["gpx"]
    assert review_response["candidate_summary"]["verified_count"] == 0
    assert review_response["candidate_summary"]["review_count"] == 1


def test_response_withholds_every_export_for_unrouted_or_malformed_geometry(
    monkeypatch,
):
    points = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]
    passing = Validation(
        score=0.86,
        closure=0.95,
        distance_fit=0.9,
        shape_fidelity=0.84,
        on_roads=True,
        spatial_similarity=0.82,
        coverage_similarity=0.82,
        turning_similarity=0.82,
        landmark_similarity=0.82,
        reversal_similarity=0.9,
        length_similarity=0.82,
        extent_similarity=0.82,
        route_length_ratio=1.05,
        target_distance_km=8.0,
    )

    def candidate(
        candidate_points,
        *,
        snapped: bool,
        total_distance_m: float = 8_000.0,
    ) -> EvaluatedCandidate:
        return EvaluatedCandidate(
            shape_name="heart",
            shape_source="template",
            points=candidate_points,
            ideal_points=points,
            total_distance_m=total_distance_m,
            snapped=snapped,
            closed=True,
            target_distance_km=8.0,
            validation=copy.deepcopy(passing),
            rotation_deg=0.0,
            scale_m=1_000.0,
            lat_offset_m=0.0,
            lon_offset_m=0.0,
        )

    state = WorkflowState(
        prompt="heart route",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        shape=Shape("heart", shape_library.heart()[1], True),
        route_draft=RouteDraft(
            47.0,
            19.0,
            1_000.0,
            0.0,
            0.0,
            0.0,
            0.8,
            points,
            True,
            8.0,
        ),
        snapped=SnappedRoute(points, 8_000.0, snapped=False),
        validation=replace(passing, on_roads=False),
        export=Export(
            gpx="<gpx>unsafe primary</gpx>",
            tcx="<TrainingCenterDatabase>unsafe primary</TrainingCenterDatabase>",
            file_paths={"gpx": "unsafe.gpx"},
            name="unsafe",
        ),
        candidates=[
            candidate(points, snapped=False),
            candidate([], snapped=True),
            candidate(points, snapped=True, total_distance_m=0.0),
        ],
    )

    def must_not_serialize(*_args, **_kwargs):
        pytest.fail("unsafe candidate geometry must never be serialized")

    monkeypatch.setattr(gpx_writer, "to_gpx", must_not_serialize)
    monkeypatch.setattr(gpx_writer, "to_tcx", must_not_serialize)
    response = _state_to_response(state)

    assert response["snapped"] is False
    assert response["gpx"] is None
    assert response["tcx"] is None
    assert response["file_paths"] == {}
    assert response["gallery_publish_token"] is None
    assert response["candidates"] == []
    assert response["candidate_summary"] == {
        "selected_shape": "heart",
        "accepted_count": 0,
        "verified_count": 0,
        "review_count": 0,
        "shown_count": 0,
        "rejected_selected_shape_count": 0,
        "other_shape_count": 0,
        "audited_count": 3,
        "full_route_attempt_count": 0,
        "preflight_count": 0,
    }
    assert all(item["accepted"] is False for item in response["candidate_audit"])
    assert all(
        "road_network" in item["failed_gates"]
        for item in response["candidate_audit"]
    )


def test_response_ranks_a_gate_passing_route_before_a_higher_average_failure():
    points = [(47.0, 19.0), (47.001, 19.001), (47.0, 19.0)]

    def validation(*, score: float, landmark_similarity: float) -> Validation:
        return Validation(
            score=score,
            closure=0.95,
            distance_fit=0.9,
            shape_fidelity=0.86,
            on_roads=True,
            spatial_similarity=0.84,
            coverage_similarity=0.82,
            turning_similarity=0.8,
            landmark_similarity=landmark_similarity,
            length_similarity=0.85,
            extent_similarity=0.88,
            route_length_ratio=1.05,
        )

    def candidate(candidate_validation: Validation) -> EvaluatedCandidate:
        return EvaluatedCandidate(
            shape_name="heart",
            shape_source="template",
            points=points,
            ideal_points=points,
            total_distance_m=8_000.0,
            snapped=True,
            closed=True,
            target_distance_km=8.0,
            validation=candidate_validation,
            rotation_deg=0.0,
            scale_m=1_000.0,
            lat_offset_m=0.0,
            lon_offset_m=0.0,
        )

    high_average_failure = candidate(
        validation(score=0.95, landmark_similarity=0.4)
    )
    gate_passing_route = candidate(
        validation(score=0.82, landmark_similarity=0.8)
    )
    state = WorkflowState(
        prompt="heart route",
        intent=Intent("heart", None, "Budapest", "run", 8.0, None),
        shape=Shape("heart", [[(0.0, 0.0), (1.0, 1.0)]], True),
        snapped=SnappedRoute(points, 8_000.0, snapped=True),
        validation=gate_passing_route.validation,
        candidates=[high_average_failure, gate_passing_route],
    )

    response = _state_to_response(state)

    assert response["candidates"][0]["id"] == "candidate-2"
    assert response["candidates"][0]["verification"]["passed"] is True
    assert response["candidates"][1]["id"] == "candidate-1"
    assert response["candidates"][1]["verification"]["failed_gates"] == [
        "landmark_similarity"
    ]


def test_edit_route_reroutes_control_points_and_builds_verified_shape_gpx(monkeypatch):
    routed = [
        (47.0, 19.0),
        (47.0005, 19.0007),
        (47.001, 19.001),
    ]

    def fake_snap(waypoints, *, sport, closed):
        assert sport == "run"
        assert closed is False
        assert len(waypoints) == 3
        return (
            routed,
            geo.path_distance_m(routed),
            True,
            RouteReadiness(status="ready", data_quality="good"),
        )

    monkeypatch.setattr(ors_client, "snap_route_detailed", fake_snap)
    response = edit_route(
        EditedRouteRequest(
            control_points=[[lat, lon] for lat, lon in routed],
            reference_points=[[lat, lon] for lat, lon in routed],
            sport="run",
            closed=False,
            target_distance_km=geo.path_distance_m(routed) / 1000.0,
            name="Edited route",
            shape_name="heart",
        )
    )

    assert response["snapped"] is True
    assert response["points_preview"] == [[lat, lon] for lat, lon in routed]
    assert "<gpx" in response["gpx"]
    assert "Edited route" in response["gpx"]
    assert response["route_details"]["shape"]["name"] == "heart"
    assert response["route_details"]["readiness"]["status"] == "ready"
    assert response["route_verification"]["gates"][0]["value"] == "heart"


def test_edit_route_passes_preferences_and_keeps_gpx_when_optional_tcx_fails(
    monkeypatch,
):
    routed = [
        (47.0, 19.0),
        (47.0005, 19.0007),
        (47.001, 19.001),
    ]
    observed_preferences = None

    def fake_snap(
        waypoints,
        *,
        sport,
        closed,
        route_preferences,
    ):
        nonlocal observed_preferences
        observed_preferences = route_preferences
        assert waypoints == routed
        assert sport == "bike"
        assert closed is False
        return (
            routed,
            geo.path_distance_m(routed),
            True,
            RouteReadiness(status="ready", data_quality="good"),
        )

    monkeypatch.setattr(ors_client, "snap_route_detailed", fake_snap)
    monkeypatch.setattr(
        gpx_writer,
        "to_tcx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tcx failed")),
    )

    response = edit_route(
        EditedRouteRequest(
            control_points=[[lat, lon] for lat, lon in routed],
            reference_points=[[lat, lon] for lat, lon in routed],
            sport="bike",
            closed=False,
            target_distance_km=geo.path_distance_m(routed) / 1000.0,
            name="Edited route",
            shape_name="heart",
            route_preferences={
                "avoid_steps": True,
                "avoid_ferries": True,
                "prefer_green": True,
            },
        )
    )

    assert observed_preferences == RoutePreferences(
        avoid_steps=True,
        avoid_ferries=True,
        prefer_green=True,
    )
    assert response["snapped"] is True
    assert "<gpx" in response["gpx"]
    assert response["tcx"] is None


def test_server_side_export_sanitises_user_derived_filename(tmp_path):
    paths = gpx_writer.write_files(
        [(47.0, 19.0), (47.001, 19.001)],
        name="../../outside route",
        sport="run",
        total_distance_m=150.0,
        out_dir=str(tmp_path),
    )
    assert paths
    assert all(Path(path).resolve().parent == tmp_path.resolve() for path in paths.values())


def test_unavailable_model_provider_is_probed_only_once(monkeypatch):
    class UnavailableProvider:
        name = "unavailable-test"

        def __init__(self):
            self.probes = 0

        def is_available(self):
            self.probes += 1
            return False

    provider = UnavailableProvider()
    monkeypatch.setattr(llm_factory, "available_providers", lambda: (provider,))
    llm_factory.reset_sticky()
    try:
        assert llm_factory.try_complete(lambda: "fallback") == "fallback"
        assert llm_factory.try_complete(lambda: "fallback") == "fallback"
        assert provider.probes == 1
    finally:
        llm_factory.reset_sticky()


def test_explicit_model_provider_is_prioritised_without_losing_fallbacks():
    config = llm_factory.LLMConfig(
        provider="anthropic",
        fallback_order=["opencode", "anthropic", "openai", "opencode"],
    )
    assert llm_factory._provider_order(config) == [
        "anthropic",
        "opencode",
        "openai",
    ]


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t"])
def test_orchestrator_rejects_blank_prompt(prompt):
    with pytest.raises(ValueError, match="empty"):
        Orchestrator(nodes={}).run(prompt)
