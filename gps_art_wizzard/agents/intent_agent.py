"""IntentAgent: turn the user's natural-language prompt into a structured Intent."""

from __future__ import annotations

import json
import math
import re

from ..config import get_settings
from ..llm import LLMResponse, extract_json, try_complete
from ..prompts import render
from ..state import Intent, WorkflowState
from ..tools.geocoder import (
    BALATON_SHORE_CITIES,
    MAJOR_EUROPEAN_CITIES,
    MAJOR_HUNGARIAN_CITIES,
)
from .base import BaseAgent

_KNOWN_CITIES = [
    *MAJOR_HUNGARIAN_CITIES,
    *(city for city in BALATON_SHORE_CITIES if city not in MAJOR_HUNGARIAN_CITIES),
    *MAJOR_EUROPEAN_CITIES,
    "Visegrád", "Makó", "New York",
]

_UNLISTED_CITY_PATTERN = re.compile(
    r"""\b(?:in|near|around)\s+
        ([^\d,.;!?]{1,100}?)
        (?=
            \s+(?:in|near|around|about|for|while|during|on)\b
            |\s+\d+(?:[.,]\d+)?\s*km\b
            |[,.;!?]
            |$
        )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_CUSTOM_REQUEST_PREFIX = re.compile(
    r"""^\s*(?:
        (?:please\s+)?(?:draw|trace|make|create|sketch|plan|run|jog|cycle|ride)
            \s+(?:me\s+)?(?:(?:a|an|the)\s+)?
        |(?:kérlek\s+)?(?:rajzolj|rajzoljon|készíts|készítsen|alkoss|tervezz|tervezzen)
            \s+(?:nekem\s+)?(?:(?:egy|a|az)\s+)?
        |(?:fuss|fusson|kocogj|kocogjon|tekerj|tekerjen|biciklizz|kerékpározz)
            \s+(?:(?:egy|a|az)\s+)?
        |(?:a|an|the)\s+
        |(?:egy|az)\s+
        )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_SUGGESTION_PATTERNS = (
    re.compile(r"\b(?:suggest|recommend|surprise|inspire)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+should\s+(?:i|we)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:pick|choose)\s+(?:(?:a|an|the|my|our)\s+)?"
        r"(?:shape|drawing|route|idea)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:give|show)\s+me\s+(?:an?|some)\s+(?:idea|inspiration)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bany\s+(?:shape|drawing|route|idea)\b", re.IGNORECASE),
    re.compile(r"\b(?:ajánlj|javasolj|válassz|inspirálj)\b", re.IGNORECASE),
    re.compile(r"\blepj\s+meg\b", re.IGNORECASE),
)

_INTENT_JSON_SCHEMA = {
    "title": "GPS art route intent",
    "description": (
        "Semantic drawing intent and route metadata extracted from one user request."
    ),
    "type": "object",
    "properties": {
        "shape": {
            "description": (
                "Complete semantic subject to draw. Never an initial or abbreviation; "
                "null only for explicit text or suggestion requests."
            ),
            "type": ["string", "null"],
            "maxLength": 80,
        },
        "text": {
            "description": (
                "Characters explicitly requested as written text; never inferred from a "
                "named object."
            ),
            "type": ["string", "null"],
            "maxLength": 20,
        },
        "city": {
            "description": "Named city or area, preserving accents; null when absent.",
            "type": ["string", "null"],
            "maxLength": 100,
        },
        "sport": {
            "description": "Requested activity (run, walk or bike); running is the default.",
            "type": "string",
            "enum": ["run", "walk", "bike"],
        },
        "distance_km": {
            "description": "Positive target distance in kilometres; null when absent.",
            "type": ["number", "null"],
            "minimum": 0,
        },
        "style": {
            "description": (
                "Purely visual or sizing guidance; semantic subject modifiers stay in shape."
            ),
            "type": ["string", "null"],
            "maxLength": 80,
        },
        "suggest": {
            "description": "True only when the user asks the planner to choose the drawing.",
            "type": "boolean",
        },
    },
    "required": [
        "shape",
        "text",
        "city",
        "sport",
        "distance_km",
        "style",
        "suggest",
    ],
    "additionalProperties": False,
}


class IntentAgent(BaseAgent):
    name = "intent"

    def run(self, state: WorkflowState) -> WorkflowState:
        fallback = self._fallback(state.prompt)
        fallback_intent = self._parse(fallback.text)
        if (
            get_settings().llm.usage_mode == "essential"
            or self._is_complete_fallback(fallback_intent)
        ):
            # Common template/text requests are fully structured by local
            # rules. Essential mode also keeps ambiguous interpretation local
            # so hosted AI is reserved for genuinely custom shape geometry.
            intent = fallback_intent
        else:
            instructions = render("intent")
            user = json.dumps({"route_request": state.prompt}, ensure_ascii=False)
            resp = try_complete(
                lambda: fallback,
                messages=[{"role": "user", "content": user}],
                system=f"{self.system_prompt}\n\n{instructions}",
                json_mode=True,
                json_schema=_INTENT_JSON_SCHEMA,
                temperature=0.1,
            )
            intent = self._parse(resp.text)
        intent = _preserve_described_drawing(state.prompt, fallback_intent, intent)
        state.intent = intent
        self._record(state, f"intent={intent}")
        return state

    @staticmethod
    def _is_complete_fallback(intent: Intent) -> bool:
        """Whether local parsing captured enough fields to skip an LLM."""
        return bool(
            intent.city
            and (intent.shape or intent.text or intent.suggest)
        )

    # -- parsing ------------------------------------------------------------- #
    def _parse(self, text: str) -> Intent:
        try:
            data = extract_json(text)
        except Exception:  # noqa: BLE001
            data = {}
        data = data if isinstance(data, dict) else {}
        sport = str(data.get("sport") or "run").lower()
        if sport not in ("run", "walk", "bike"):
            sport = "run"
        dist = data.get("distance_km")
        try:
            dist = float(dist) if dist is not None else None
        except (TypeError, ValueError):
            dist = None
        if dist is not None:
            if not math.isfinite(dist) or dist <= 0:
                dist = None
            else:
                lower, upper = get_settings().workflow.distance_bounds.get(sport, [3, 60])
                dist = min(float(upper), max(float(lower), dist))
        suggest = _parse_bool(data.get("suggest", False))
        shape = _clean_optional_text(data.get("shape"), max_length=80)
        # If suggesting, force shape to None — PlanningAgent will pick one.
        if suggest:
            shape = None
        return Intent(
            shape=shape,
            text=_clean_optional_text(data.get("text"), max_length=20),
            city=_clean_optional_text(data.get("city"), max_length=100),
            sport=sport,
            distance_km=dist,
            style=_clean_optional_text(data.get("style"), max_length=80),
            suggest=suggest,
        )

    # -- deterministic fallback --------------------------------------------- #
    def _fallback(self, text: str) -> LLMResponse:
        cfg = get_settings().workflow
        low = text.lower()

        bike_request = re.search(
            r"\b(?:bike|biking|cycle|cycling|bicycle|bici|bicikl\w*|"
            r"kerékpár\w*|bringa\w*|teker\w*)\b",
            low,
        )
        run_request = re.search(
            r"\b(?:run|running|jog|jogging|marathon|fut\w*|kocog\w*)\b",
            low,
        )
        walk_request = re.search(r"\b(?:walk|walking|stroll|hike|hiking|s[eé]t[aá]\w*)\b", low)
        if bike_request:
            sport = "bike"
        elif run_request:
            sport = "run"
        elif walk_request:
            sport = "walk"
        else:
            sport = cfg.sport_default
        dist_match = re.search(r"(\d+(?:[.,]\d+)?)\s*km", low)
        dist = float(dist_match.group(1).replace(",", ".")) if dist_match else None
        city = next((c for c in _KNOWN_CITIES if c.lower() in low), None)
        if city is None:
            city = _extract_unlisted_city(text)

        # Detect suggestion requests.
        suggest = _is_suggestion_request(text)

        text_match = re.search(
            r"""\b(?:write|spell)\s+(?:the\s+(?:word|text)\s+)?["']?
                ([A-Za-z0-9 !?-]{1,20}?)
                (?=["']?(?:\s+(?:in|near|around|for|about)\b|$)|[,.;])""",
            text,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        labelled_glyph_match = re.search(
            r"""\b(?:letter|number|digits?)\s+["']?
                ([A-Za-z0-9]{1,6})
                (?=["']?(?:\s+(?:in|near|around|for|about|while)\b|$)|[,.;])""",
            text,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        drawn_text = (
            text_match.group(1).strip()
            if text_match
            else (
                labelled_glyph_match.group(1).strip()
                if labelled_glyph_match
                else None
            )
        )
        shape = None
        if not suggest:
            from ..tools import shape_library

            custom_shape = _extract_custom_shape(text, city=city)
            hit = shape_library.find_by_keyword(low)
            if drawn_text:
                shape = "text"
            elif hit and shape_library.template_match_covers_description(
                custom_shape,
                hit[0],
            ):
                shape = hit[0]
            else:
                # Preserve a named, unsupported drawing instead of dropping it
                # and forcing ShapeAgent to guess.  This also lets common custom
                # requests skip a redundant intent-model call: only the actual
                # vector drawing needs generative inference.
                shape = custom_shape or (hit[0] if hit else None)
        payload = {
            "shape": shape, "text": drawn_text, "city": city,
            "sport": sport, "distance_km": dist, "style": None, "suggest": suggest,
        }
        return LLMResponse(text=json.dumps(payload), provider="fallback", model="rules")


def _clean_optional_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:max_length] or None


def _preserve_described_drawing(
    prompt: str,
    locally_parsed: Intent,
    intent: Intent,
) -> Intent:
    """Keep a described subject from being reduced to its first letter."""
    low = prompt.casefold()
    explicit_text_request = re.search(r"\b(?:write|spell|letter|text)\b", low)
    described_shape = locally_parsed.shape
    if described_shape and described_shape != "text" and not explicit_text_request:
        intent.shape = described_shape
        intent.text = None
        intent.suggest = False
    return intent


def _extract_unlisted_city(text: str) -> str | None:
    """Conservatively recover an unlisted settlement from common phrasing."""
    matches = list(_UNLISTED_CITY_PATTERN.finditer(text))
    if not matches:
        return None
    candidate = matches[-1].group(1).strip(" \t\r\n\"'")
    candidate = re.sub(r"^the\s+(?:city|town|village)\s+of\s+", "", candidate, flags=re.I)
    candidate = " ".join(candidate.split())
    low = candidate.casefold()
    if (
        len(candidate) < 2
        or len(candidate.split()) > 6
        or low in {"a city", "any city", "my city", "the city", "here", "anywhere"}
        or any(
            token in low
            for token in (" style", " route", " running", " cycling", " bike", " run")
        )
    ):
        return None
    return candidate[:100]


def _is_suggestion_request(text: str) -> bool:
    """Detect an open-ended recommendation without matching named objects.

    The earlier substring checks treated words such as ``pickaxe`` and phrases
    such as ``idea bulb`` as requests to choose a shape.  Word-bounded,
    task-specific patterns keep those valid custom drawings intact.
    """

    return any(pattern.search(text) for pattern in _SUGGESTION_PATTERNS)


def _extract_custom_shape(text: str, *, city: str | None) -> str | None:
    """Recover a concise free-form drawing description from a route prompt.

    This parser is intentionally conservative.  It removes route metadata but
    keeps semantic modifiers such as ``flying`` or ``wearing a hat`` so the
    ShapeAgent has enough information to produce a distinctive silhouette.
    """

    candidate = " ".join(text.split()).strip(" \t\r\n\"'")
    candidate = re.sub(
        r"\b(?:about\s+)?\d+(?:[.,]\d+)?\s*(?:km|kilomet(?:re|er)s?)\b",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )

    if city:
        city_clause = re.compile(
            rf"\s+\b(?:in|near|around)\s+"
            rf"(?:(?:the\s+)?(?:city|town|village)\s+of\s+)?{re.escape(city)}\b",
            flags=re.IGNORECASE,
        )
        match = city_clause.search(candidate)
        if match:
            candidate = candidate[: match.start()]
        else:
            # Hungarian location suffixes commonly attach directly to the
            # settlement (Budapesten, Győrben, Pécsen). Handle both the usual
            # drawing-first form and the equally natural city-first form.
            locative = re.compile(
                rf"\b{re.escape(city)}(?:en|on|ön|n|ban|ben)\b",
                flags=re.IGNORECASE,
            )
            match = locative.search(candidate)
            if match:
                before = candidate[: match.start()].strip(" ,")
                after = candidate[match.end() :].strip(" ,")
                candidate = before if before else after

    # Comma-separated prompts conventionally put the drawing first.  Activity
    # and distance clauses that remain after city removal are not shape data.
    candidate = candidate.split(",", maxsplit=1)[0]
    candidate = re.sub(
        r"\s+\b(?:for|while|during|as)\s+(?:a\s+)?"
        r"(?:run|jog|ride|bike|bicycle|cycling|runner|cyclist)\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\s+\b(?:run|running|jog|jogging|bike|biking|cycle|cycling)\s+route\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\s+\b(?:run|running|jog|jogging|biking|cycling)\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\s+\b(?:futva|futás(?:sal|ként)?|kocog(?:va|ás(?:sal|ként)?)|"
        r"kerékpárral|biciklivel|bringával|tekerve)\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = _CUSTOM_REQUEST_PREFIX.sub("", candidate, count=1)
    candidate = candidate.strip(" \t\r\n\"'.,;:!?-")
    candidate = " ".join(candidate.split())

    generic = {
        "art",
        "drawing",
        "gps art",
        "route",
        "shape",
        "something",
        "something cool",
        "custom drawing",
        "custom route",
    }
    if (
        not candidate
        or candidate.casefold() in generic
        or len(candidate.split()) > 12
        or not any(character.isalnum() for character in candidate)
    ):
        return None
    return candidate[:80]


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, int | float):
        return value == 1
    return False
