"""Configuration loaded from environment + ``config/settings.yaml``.

Provider-agnostic by design: no LLM key is required for the package to import.
Agents fall back to deterministic, rule-based behaviour when no provider is
available, so the pipeline can be exercised end-to-end offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_YAML = ROOT / "config" / "settings.yaml"


def _ors_base_url() -> str:
    configured = os.getenv("ORS_BASE_URL", "https://api.heigit.org/openrouteservice")
    # Northflank may still carry the old host as a runtime override. Its quota
    # is being retired, while the API path and key stay the same on HeiGIT.
    if configured.rstrip("/") == "https://api.openrouteservice.org":
        return "https://api.heigit.org/openrouteservice"
    return configured


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto"))
    usage_mode: str = field(
        default_factory=lambda: os.getenv("LLM_USAGE_MODE", "balanced").strip().lower()
    )
    fallback_order: list[str] = field(default_factory=lambda: _split_env("LLM_FALLBACK", ["opencode", "anthropic", "openai", "ollama"]))
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 2048))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    opencode_model: str = field(default_factory=lambda: os.getenv("OPENCODE_MODEL", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", ""))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    groq_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    opencode_key: str = field(default_factory=lambda: os.getenv("OPENCODE_API_KEY", ""))
    opencode_base_url: str = field(default_factory=lambda: os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/inference/openai/v1"))
    opencode_transport: str = field(
        default_factory=lambda: os.getenv("OPENCODE_TRANSPORT", "api").strip().lower()
    )
    opencode_server_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_SERVER_URL",
            "http://127.0.0.1:4097",
        )
    )
    opencode_server_autostart: bool = field(
        default_factory=lambda: _bool("OPENCODE_SERVER_AUTOSTART", True)
    )
    opencode_structured_model: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_STRUCTURED_MODEL",
            "gpt-6-luna",
        )
    )
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))

    @property
    def has_any_provider(self) -> bool:
        return bool(self.opencode_key or self.groq_key or self.openai_key or self.anthropic_key or self.ollama_base_url)


@dataclass
class RoutingConfig:
    shape_graph_enabled: bool = field(default_factory=lambda: _bool("SHAPE_GRAPH_ENABLED", True))
    shape_graph_snapshot: str = field(default_factory=lambda: os.getenv("SHAPE_GRAPH_SNAPSHOT", ""))
    shape_graph_seconds: float = field(default_factory=lambda: _float("SHAPE_GRAPH_SECONDS", 2.0))
    ors_api_key: str = field(default_factory=lambda: os.getenv("ORS_API_KEY", ""))
    ors_base_url: str = field(default_factory=lambda: _ors_base_url())
    snap_radius_m: int = field(default_factory=lambda: _int("ORS_SNAP_RADIUS_M", 120))
    # GPS-art cusps and lettering often require U-turns at via-points.
    continue_straight: bool = field(
        default_factory=lambda: _bool("ORS_CONTINUE_STRAIGHT", False)
    )
    preference: str = field(
        default_factory=lambda: os.getenv("ORS_PREFERENCE", "recommended")
    )
    # auto: ORS when a key or self-hosted ORS is configured, else keyless OSRM.
    backend: str = field(
        default_factory=lambda: os.getenv("ROUTING_BACKEND", "auto").strip().lower()
    )
    # ``{profile}`` is replaced by the OSRM profile (foot or bike).
    osrm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OSRM_BASE_URL", "https://routing.openstreetmap.de/routed-{profile}"
        )
    )
    # Public OSRM servers allow roughly one request per second.
    osrm_min_interval_s: float = field(
        default_factory=lambda: _float("OSRM_MIN_INTERVAL_S", 1.0)
    )


@dataclass
class GeocoderConfig:
    nominatim_base_url: str = field(default_factory=lambda: os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"))
    nominatim_email: str = field(default_factory=lambda: os.getenv("NOMINATIM_EMAIL", ""))


@dataclass
class WorkflowConfig:
    optimization_budget_seconds: float = field(
        default_factory=lambda: _float("OPTIMIZATION_BUDGET_SECONDS", 35.0)
    )
    initial_placement_candidates: int = field(
        default_factory=lambda: _int("INITIAL_PLACEMENT_CANDIDATES", 3)
    )
    shape_polish_candidates: int = field(default_factory=lambda: _int("SHAPE_POLISH_CANDIDATES", 4))
    max_refinement_iterations: int = field(default_factory=lambda: _int("MAX_REFINEMENT_ITERATIONS", 6))
    validation_score_threshold: float = field(default_factory=lambda: _float("VALIDATION_SCORE_THRESHOLD", 0.72))
    min_shape_fidelity: float = 0.7
    preflight_enabled: bool = field(
        default_factory=lambda: _bool("PREFLIGHT_ENABLED", True)
    )
    preflight_max_placements: int = field(
        default_factory=lambda: _int("PREFLIGHT_MAX_PLACEMENTS", 180)
    )
    preflight_creative_placements: int = field(
        default_factory=lambda: _int("PREFLIGHT_CREATIVE_PLACEMENTS", 90)
    )
    preflight_shortlist: int = field(
        default_factory=lambda: _int("PREFLIGHT_SHORTLIST", 7)
    )
    preflight_guide_points: int = field(
        default_factory=lambda: _int("PREFLIGHT_GUIDE_POINTS", 18)
    )
    preflight_adaptive: bool = field(
        default_factory=lambda: _bool("PREFLIGHT_ADAPTIVE", True)
    )
    # Candidate route measurements (road recovery, suggestion and fallback
    # searches) run concurrently through this bounded worker pool. Higher
    # values trade API rate limits for lower wall-clock time.
    measurement_workers: int = field(
        default_factory=lambda: _int("MEASUREMENT_WORKERS", 3)
    )
    ai_shape_verifier_enabled: bool = field(
        default_factory=lambda: _bool("AI_SHAPE_VERIFIER_ENABLED", True)
    )
    ai_route_verifier_enabled: bool = field(
        default_factory=lambda: _bool("AI_ROUTE_VERIFIER_ENABLED", True)
    )
    ai_route_verifier_candidates: int = field(
        default_factory=lambda: _int("AI_ROUTE_VERIFIER_CANDIDATES", 3)
    )
    ai_shape_min_semantic_score: float = field(
        default_factory=lambda: _float("AI_SHAPE_MIN_SEMANTIC_SCORE", 0.68)
    )
    ai_shape_max_candidates: int = field(
        default_factory=lambda: _int("AI_SHAPE_MAX_CANDIDATES", 4)
    )
    max_duration_seconds: float = field(
        default_factory=lambda: _float("WORKFLOW_MAX_DURATION_SECONDS", 175.0)
    )
    max_llm_calls: int = field(
        default_factory=lambda: _int("WORKFLOW_MAX_LLM_CALLS", 8)
    )
    max_trace_events: int = field(
        default_factory=lambda: _int("WORKFLOW_MAX_TRACE_EVENTS", 256)
    )
    sport_default: str = field(default_factory=lambda: os.getenv("DEFAULT_SPORT", "run"))
    city_default: str = field(default_factory=lambda: os.getenv("DEFAULT_CITY", "Budapest"))
    distance_bounds: dict[str, list[float]] = field(default_factory=lambda: {"run": [3, 60], "walk": [2, 40], "bike": [10, 200]})
    distance_defaults: dict[str, float] = field(
        default_factory=lambda: {
            "run": _float("DEFAULT_RUN_DISTANCE_KM", 8.0),
            "walk": _float("DEFAULT_WALK_DISTANCE_KM", 5.0),
            "bike": _float("DEFAULT_BIKE_DISTANCE_KM", 20.0),
        }
    )


@dataclass
class Settings:
    llm: LLMConfig = field(default_factory=LLMConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    geocoder: GeocoderConfig = field(default_factory=GeocoderConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)


def _split_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalised = raw.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    return default


def _load_yaml_overlays() -> dict:
    if SETTINGS_YAML.exists():
        with SETTINGS_YAML.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build a :class:`Settings` object, merging env vars with yaml defaults."""
    settings = Settings()
    yaml_data = _load_yaml_overlays()

    wf_yaml = yaml_data.get("workflow", {})
    if wf_yaml:
        for k, v in wf_yaml.items():
            if k == "distance_bounds":
                settings.workflow.distance_bounds = v
            elif k == "distance_defaults":
                settings.workflow.distance_defaults = {
                    "run": _float(
                        "DEFAULT_RUN_DISTANCE_KM",
                        float(v.get("run", 8.0)),
                    ),
                    "walk": _float(
                        "DEFAULT_WALK_DISTANCE_KM",
                        float(v.get("walk", 5.0)),
                    ),
                    "bike": _float(
                        "DEFAULT_BIKE_DISTANCE_KM",
                        float(v.get("bike", 20.0)),
                    ),
                }
            elif hasattr(settings.workflow, k):
                # Env vars win over yaml; only apply yaml when env didn't set it.
                env_name = {
                    "max_refinement_iterations": "MAX_REFINEMENT_ITERATIONS",
                    "optimization_budget_seconds": "OPTIMIZATION_BUDGET_SECONDS",
                    "initial_placement_candidates": "INITIAL_PLACEMENT_CANDIDATES",
                    "shape_polish_candidates": "SHAPE_POLISH_CANDIDATES",
                    "validation_score_threshold": "VALIDATION_SCORE_THRESHOLD",
                    "preflight_enabled": "PREFLIGHT_ENABLED",
                    "preflight_max_placements": "PREFLIGHT_MAX_PLACEMENTS",
                    "preflight_creative_placements": "PREFLIGHT_CREATIVE_PLACEMENTS",
                    "preflight_shortlist": "PREFLIGHT_SHORTLIST",
                    "preflight_guide_points": "PREFLIGHT_GUIDE_POINTS",
                    "preflight_adaptive": "PREFLIGHT_ADAPTIVE",
                    "measurement_workers": "MEASUREMENT_WORKERS",
                    "ai_shape_verifier_enabled": "AI_SHAPE_VERIFIER_ENABLED",
                    "ai_route_verifier_enabled": "AI_ROUTE_VERIFIER_ENABLED",
                    "ai_route_verifier_candidates": "AI_ROUTE_VERIFIER_CANDIDATES",
                    "ai_shape_min_semantic_score": "AI_SHAPE_MIN_SEMANTIC_SCORE",
                    "ai_shape_max_candidates": "AI_SHAPE_MAX_CANDIDATES",
                    "max_duration_seconds": "WORKFLOW_MAX_DURATION_SECONDS",
                    "max_llm_calls": "WORKFLOW_MAX_LLM_CALLS",
                    "max_trace_events": "WORKFLOW_MAX_TRACE_EVENTS",
                    "sport_default": "DEFAULT_SPORT",
                    "city_default": "DEFAULT_CITY",
                }.get(k)
                if env_name and os.getenv(env_name):
                    continue
                setattr(settings.workflow, k, v)

    rt_yaml = yaml_data.get("routing", {})
    if rt_yaml:
        for k, v in rt_yaml.items():
            if hasattr(settings.routing, k):
                env_name = {
                    "snap_radius_m": "ORS_SNAP_RADIUS_M",
                    "shape_graph_enabled": "SHAPE_GRAPH_ENABLED",
                    "shape_graph_snapshot": "SHAPE_GRAPH_SNAPSHOT",
                    "shape_graph_seconds": "SHAPE_GRAPH_SECONDS",
                    "continue_straight": "ORS_CONTINUE_STRAIGHT",
                    "preference": "ORS_PREFERENCE",
                }.get(k)
                if env_name and os.getenv(env_name):
                    continue
                setattr(settings.routing, k, v)

    return settings
