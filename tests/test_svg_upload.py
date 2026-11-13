"""Uploaded SVG shapes: parsing, normalisation and the upload/generate API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gps_art_wizzard.api import routes
from gps_art_wizzard.api.routes import GenerateRequest
from gps_art_wizzard.main import create_app
from gps_art_wizzard.tools import geo, image_reference

HEART_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path d="M50 88 C20 65 5 45 15 25 C25 8 45 12 50 30 C55 12 75 8 85 25 C95 45 80 65 50 88 Z"/>
</svg>"""

MIXED_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <polygon points="10,10 60,10 60,60 10,60"/>
  <polyline points="80,10 120,50 160,10" fill="none"/>
  <path d="M10 80 L60 80 M80 80 L120 95"/>
</svg>"""


def _bounds(paths):
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    return min(xs), max(xs), min(ys), max(ys)


def test_uploaded_heart_becomes_one_closed_reference_path() -> None:
    shape = image_reference.uploaded_svg_shape(HEART_SVG.encode(), filename="My-Heart.svg")

    assert shape.name == "My Heart"
    assert shape.source == "reference_svg"
    assert shape.closed is True
    assert len(shape.paths) == 1
    assert shape.paths[0][0] == shape.paths[0][-1]
    # SVG y points down; route geometry y points up, so the heart's tip is the
    # lowest point and sits horizontally in the middle.
    min_x, max_x, _, _ = _bounds(shape.paths)
    tip_x, _ = min(shape.paths[0], key=lambda point: point[1])
    assert tip_x == pytest.approx((min_x + max_x) / 2, rel=0.02)


def test_polygon_polyline_and_multiple_subpaths_are_all_sampled() -> None:
    shape = image_reference.uploaded_svg_shape(MIXED_SVG, filename="mixed.svg")

    # polygon + polyline + two subpaths of the <path> element
    assert len(shape.paths) == 4
    assert shape.closed is False  # open strokes make the drawing open
    assert all(len(path) >= 12 for path in shape.paths)


def test_uploaded_shape_normalises_like_a_template() -> None:
    shape = image_reference.uploaded_svg_shape(MIXED_SVG)
    normalised = geo.normalize_shape(shape.paths)

    min_x, max_x, min_y, max_y = _bounds(normalised)
    assert max(max_x - min_x, max_y - min_y) == pytest.approx(1.0)
    assert shape.name == "imported image"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b"   \n", "empty"),
        (b"hello world", "Upload an SVG file"),
        (b"\x89PNG\r\n\x1a\n0000", "Upload an SVG file"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "no usable vector outline"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><text>hi</text></svg>', "no usable vector outline"),
        (b"<svg><path d='M0 0 L10 10'", "could not be parsed"),
        (
            b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            "document types or entities",
        ),
        (b"<svg>" + b" " * image_reference.MAX_SVG_BYTES + b"</svg>", "2 MB"),
    ],
    ids=["empty", "blank", "text", "png", "no-shapes", "text-only", "broken-xml", "entity", "too-big"],
)
def test_invalid_uploads_are_rejected_with_a_clear_message(content: bytes, message: str) -> None:
    with pytest.raises(image_reference.ImageReferenceError, match=message):
        image_reference.uploaded_svg_shape(content)


def test_shape_upload_endpoint_returns_a_normalised_preview() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/shape-upload",
            params={"filename": "heart.svg"},
            content=HEART_SVG,
            headers={"Content-Type": "image/svg+xml"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["shape"] == "heart"
    assert body["closed"] is True
    assert body["path_count"] == 1
    assert body["point_count"] > 12
    min_x, max_x, min_y, max_y = _bounds(body["paths"])
    assert max(max_x - min_x, max_y - min_y) == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize(
    ("content", "content_type", "status"),
    [
        ("<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml", 422),
        ("not an svg", "image/svg+xml", 422),
        ("", "image/svg+xml", 422),
        (HEART_SVG, "multipart/form-data; boundary=x", 415),
        (HEART_SVG, "application/json", 415),
        ("<svg>" + " " * image_reference.MAX_SVG_BYTES + "</svg>", "image/svg+xml", 413),
    ],
    ids=["no-shapes", "not-svg", "empty", "multipart", "json", "too-big"],
)
def test_shape_upload_endpoint_rejects_bad_files(content: str, content_type: str, status: int) -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/shape-upload",
            content=content,
            headers={"Content-Type": content_type},
        )

    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)


def test_generate_request_rejects_two_reference_sources() -> None:
    with pytest.raises(ValidationError, match="either an uploaded SVG or an image link"):
        GenerateRequest(
            prompt="a custom image in Budapest",
            reference_svg=HEART_SVG,
            reference_image_url="https://example.com/a.svg",
        )


def test_generate_request_rejects_blank_svg_and_orphan_name() -> None:
    with pytest.raises(ValidationError, match="empty"):
        GenerateRequest(prompt="a custom image in Budapest", reference_svg="  ")
    with pytest.raises(ValidationError, match="needs the uploaded SVG"):
        GenerateRequest(prompt="a custom image in Budapest", reference_svg_name="heart.svg")


def test_generate_passes_the_uploaded_svg_into_the_pipeline(monkeypatch) -> None:
    captured: dict = {}

    def reject_after_recording(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        raise ValueError("captured uploaded reference")

    monkeypatch.setattr(routes, "generate", reject_after_recording)
    with TestClient(create_app()) as client:
        response = client.post(
            "/generate",
            json={
                "prompt": "a custom image in Budapest, walking, about 5 km",
                "reference_svg": HEART_SVG,
                "reference_svg_name": "heart.svg",
                "start_point": {"latitude": 47.5, "longitude": 19.05},
            },
        )

    assert response.status_code == 422
    assert captured["reference_kind"] == "svg"
    assert captured["reference_name"] == "heart"
    assert captured["reference_shape"].source == "reference_svg"
    assert captured["reference_shape"].closed is True
    assert captured["start_point"] == (47.5, 19.05)


def test_generate_reports_an_invalid_uploaded_svg_as_422(monkeypatch) -> None:
    monkeypatch.setattr(
        routes, "generate", lambda *_a, **_k: pytest.fail("generation must not start")
    )
    with TestClient(create_app()) as client:
        response = client.post(
            "/generate",
            json={
                "prompt": "a custom image in Budapest",
                "reference_svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            },
        )

    assert response.status_code == 422
    assert "no usable vector outline" in response.json()["detail"]
