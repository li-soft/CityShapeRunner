"""The walk activity: intent parsing, distance bounds and routing profiles."""

from __future__ import annotations

import pytest

from gps_art_wizzard.agents.intent_agent import IntentAgent
from gps_art_wizzard.api.routes import GenerateRequest
from gps_art_wizzard.tools import ors_client, osrm_client


@pytest.mark.parametrize(
    ("prompt", "sport"),
    [
        ("a custom image in Budapest, walking, about 5 km", "walk"),
        ("a heart walk in Vienna, 6 km", "walk"),
        ("a star run in Budapest", "run"),
        ("a star bike ride in Debrecen", "bike"),
    ],
)
def test_rule_based_intent_detects_the_activity(prompt: str, sport: str) -> None:
    agent = IntentAgent()

    assert agent._parse(agent._fallback(prompt).text).sport == sport


def test_walk_distance_is_clamped_to_walking_bounds() -> None:
    intent = IntentAgent()._parse('{"sport": "walk", "distance_km": 90}')

    assert intent.sport == "walk"
    assert intent.distance_km == 40


def test_walk_is_an_accepted_api_activity() -> None:
    request = GenerateRequest(
        prompt="a heart in Budapest",
        intent_override={"sport": "walk", "city": "Budapest"},
    )

    assert request.intent_override is not None
    assert request.intent_override.sport == "walk"


def test_walk_uses_pedestrian_profiles_on_every_router() -> None:
    assert ors_client.profile_for("walk") == "foot-walking"
    assert osrm_client.profile_for("walk") == "foot"
    assert osrm_client.profile_for("bike") == "bike"
