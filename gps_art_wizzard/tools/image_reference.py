"""Safely import a public or uploaded SVG/raster image as a GPS-art reference."""

from __future__ import annotations

import base64
import ipaddress
import math
import re
import socket
import warnings
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from shapely import concave_hull
from shapely.geometry import MultiPoint, MultiPolygon, Polygon
from svgelements import SVG, Close
from svgelements import Path as SvgPath
from svgelements import Shape as SvgShape

from ..state import Shape
from . import shape_program

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_SVG_SEGMENTS = 5_000
MAX_SVG_PATHS = 8
MAX_RASTER_PIXELS = 40_000_000
REFERENCE_IMAGE_MAX_DIMENSION = 1_024


class ImageReferenceError(ValueError):
    """An image link or upload could not be safely converted into route geometry."""


@dataclass(frozen=True)
class ImportedImageReference:
    name: str
    kind: str
    shape: Shape | None = None
    image_data_url: str | None = None


def import_image_reference(
    url: str,
    *,
    client: httpx.Client | None = None,
) -> ImportedImageReference:
    """Download and import one bounded public image URL.

    SVG geometry is retained as a deterministic fallback and rendered for the
    multimodal shape agent. Every raster format Pillow can safely decode is
    normalised to a bounded PNG before it is sent to the model.
    """

    cleaned_url = " ".join(str(url).split())
    if client is None:
        return deepcopy(_import_public_image_cached(cleaned_url))
    return _import_public_image(cleaned_url, client)


@lru_cache(maxsize=64)
def _import_public_image_cached(url: str) -> ImportedImageReference:
    with httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={
            "Accept": "image/svg+xml,image/*,*/*;q=0.1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        },
    ) as client:
        return _import_public_image(url, client)


def _import_public_image(url: str, client: httpx.Client) -> ImportedImageReference:
    content, final_url = _download_public_image(url, client)

    media_type = _detect_media_type(content)
    name = _reference_name(final_url)
    if media_type == "image/svg+xml":
        return _svg_reference(shape_from_svg_bytes(content, name=name))
    image_data_url, fallback_shape = _normalise_raster_image(content, name=name)
    return ImportedImageReference(
        name=name,
        kind="raster",
        shape=fallback_shape,
        image_data_url=image_data_url,
    )


def uploaded_svg_shape(content: bytes, *, filename: str | None = None) -> Shape:
    """Parse an SVG file uploaded by the user (path, polygon, polyline, shapes)."""

    if not content.strip():
        raise ImageReferenceError("The uploaded SVG file is empty.")
    if _detect_media_type(content) != "image/svg+xml":
        raise ImageReferenceError("Upload an SVG file (an <svg> document).")
    return shape_from_svg_bytes(content, name=clean_reference_name(filename or ""))


def import_uploaded_svg(content: bytes, *, filename: str | None = None) -> ImportedImageReference:
    """Import an uploaded SVG file as a generation reference."""

    return _svg_reference(uploaded_svg_shape(content, filename=filename))


def _svg_reference(shape: Shape) -> ImportedImageReference:
    name = shape.name
    return ImportedImageReference(
        name=name,
        kind="svg",
        shape=shape,
        image_data_url=shape_program.render_paths_png_data_url(
            shape.paths,
            size=512,
            padding=28,
        ),
    )


def _download_public_image(url: str, client: httpx.Client) -> tuple[bytes, str]:
    current_url = " ".join(str(url).split())
    for redirect_count in range(MAX_REDIRECTS + 1):
        _require_public_http_url(current_url)
        parsed_url = urlsplit(current_url)
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        try:
            with client.stream(
                "GET",
                current_url,
                follow_redirects=False,
                headers={"Referer": referer},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count >= MAX_REDIRECTS:
                        raise ImageReferenceError(
                            "The image link redirects too many times or has no destination."
                        )
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code != 200:
                    raise ImageReferenceError(
                        f"The image link could not be downloaded (HTTP {response.status_code})."
                    )
                declared_size = response.headers.get("content-length")
                if declared_size:
                    try:
                        if int(declared_size) > MAX_IMAGE_BYTES:
                            raise ImageReferenceError("The linked image must be 5 MB or smaller.")
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ImageReferenceError("The linked image must be 5 MB or smaller.")
                    chunks.append(chunk)
                if not chunks:
                    raise ImageReferenceError("The image link returned an empty file.")
                return b"".join(chunks), current_url
        except ImageReferenceError:
            raise
        except httpx.HTTPError as exc:
            raise ImageReferenceError(
                "The image link could not be reached. Check that it is public and try again."
            ) from exc
    raise ImageReferenceError("The image link redirects too many times.")


def _require_public_http_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    except ValueError as exc:
        raise ImageReferenceError("Enter a valid public image URL.") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ImageReferenceError("Enter a public HTTP or HTTPS image URL.")
    if parsed.username or parsed.password:
        raise ImageReferenceError("Image URLs containing credentials are not supported.")
    try:
        addresses: set[str] = set()
        for item in socket.getaddrinfo(
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        ):
            raw_address = item[4][0]
            if not isinstance(raw_address, str):
                raise ImageReferenceError(
                    "The image link resolved to an invalid address."
                )
            addresses.add(raw_address)
    except socket.gaierror as exc:
        raise ImageReferenceError("The image link hostname could not be found.") from exc
    if not addresses:
        raise ImageReferenceError("The image link hostname could not be found.")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise ImageReferenceError("The image link resolved to an invalid address.") from exc
        if not parsed_address.is_global:
            raise ImageReferenceError(
                "The image link must point to a public internet address."
            )


def _detect_media_type(content: bytes) -> str:
    prefix = content[:1_024].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if b"<svg" in prefix:
        return "image/svg+xml"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _normalise_raster_image(content: bytes, *, name: str) -> tuple[str, Shape | None]:
    """Decode a raster by content and produce a small provider-safe PNG."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > MAX_RASTER_PIXELS:
                    raise ImageReferenceError(
                        "The linked image dimensions are too large to process safely."
                    )
                opened.seek(0)
                opened.load()
                image = ImageOps.exif_transpose(opened).copy()
    except ImageReferenceError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageReferenceError(
            "The linked image dimensions are too large to process safely."
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageReferenceError(
            "Use a direct SVG or a valid raster image link."
        ) from exc

    image.thumbnail(
        (REFERENCE_IMAGE_MAX_DIMENSION, REFERENCE_IMAGE_MAX_DIMENSION),
        Image.Resampling.LANCZOS,
        reducing_gap=3.0,
    )
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    normalised = background.convert("RGB")
    fallback_shape = _shape_from_raster(normalised, name=name)
    output = BytesIO()
    normalised.save(output, format="PNG", compress_level=3)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}", fallback_shape


def _shape_from_raster(image: Image.Image, *, name: str) -> Shape | None:
    """Build a quick routeable silhouette for AI failure or timeout.

    This is deliberately a fallback, not the primary visual interpreter. It
    estimates the background from the border, keeps the largest contrasting
    subject contour, and uses a topology-preserving concave hull.
    """

    preview = image.copy()
    preview.thumbnail((192, 192), Image.Resampling.BILINEAR, reducing_gap=3.0)
    rgb = np.asarray(preview.convert("RGB"), dtype=np.int16)
    height, width = rgb.shape[:2]
    if min(width, height) < 4:
        return None

    border = np.concatenate(
        (rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]),
        axis=0,
    )
    background = np.median(border, axis=0)
    colour_distance = np.sqrt(
        np.sum((rgb.astype(np.float32) - background) ** 2, axis=2)
    )
    # A fixed floor suppresses JPEG noise; the border percentile adapts to
    # photographic or textured backgrounds without relying on the file type.
    threshold = max(24.0, float(np.percentile(colour_distance, 72)))
    mask = colour_distance >= threshold
    coverage = float(mask.mean())
    if coverage < 0.01 or coverage > 0.82:
        grayscale = np.asarray(preview.convert("L"), dtype=np.float32)
        pivot = float(np.median(grayscale))
        darker = grayscale < pivot - 18
        lighter = grayscale > pivot + 18
        mask = darker if 0.01 <= darker.mean() <= lighter.mean() else lighter
        coverage = float(mask.mean())
    if coverage < 0.01 or coverage > 0.82:
        return None

    padded = np.pad(mask, 1, constant_values=False)
    interior = mask.copy()
    for y_offset in range(3):
        for x_offset in range(3):
            interior &= padded[
                y_offset : y_offset + height,
                x_offset : x_offset + width,
            ]
    boundary_y, boundary_x = np.nonzero(mask & ~interior)
    if len(boundary_x) < 12:
        return None
    if len(boundary_x) > 2_500:
        stride = math.ceil(len(boundary_x) / 2_500)
        boundary_x = boundary_x[::stride]
        boundary_y = boundary_y[::stride]

    try:
        hull = concave_hull(
            MultiPoint(
                [(float(x), float(-y)) for x, y in zip(boundary_x, boundary_y, strict=True)]
            ),
            ratio=0.08,
            allow_holes=True,
        )
        if isinstance(hull, MultiPolygon):
            hull = max(hull.geoms, key=lambda polygon: polygon.area)
        if not isinstance(hull, Polygon) or hull.is_empty:
            return None
        simplified = hull.simplify(max(width, height) * 0.012, preserve_topology=True)
        if not isinstance(simplified, Polygon) or simplified.is_empty:
            return None
    except Exception:  # noqa: BLE001 - optional best-effort fallback geometry
        return None

    rings = [simplified.exterior, *simplified.interiors]
    paths: list[list[tuple[float, float]]] = []
    for ring in sorted(rings, key=lambda item: abs(Polygon(item).area), reverse=True)[:4]:
        coordinates = [(float(x), float(y)) for x, y in ring.coords]
        if len(coordinates) > 96:
            stride = math.ceil((len(coordinates) - 1) / 95)
            coordinates = [*coordinates[:-1:stride], coordinates[-1]]
        if len(coordinates) >= 4:
            paths.append(coordinates)
    if not paths:
        return None
    return Shape(
        name=name,
        paths=paths,
        closed=True,
        source="reference_raster",
    )


def shape_from_svg_bytes(content: bytes, *, name: str) -> Shape:
    """Sample every visible SVG outline into unit-space paths (y pointing up).

    Keeps the ``MAX_SVG_PATHS`` longest subpaths; the shape agent normalises
    position and scale later, exactly as for library templates.
    """
    if len(content) > MAX_SVG_BYTES:
        raise ImageReferenceError("SVG files must be 2 MB or smaller.")
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ImageReferenceError("SVG files with document types or entities are not supported.")
    try:
        document = SVG.parse(BytesIO(content), reify=True, width=1_000, height=1_000)
    except Exception as exc:  # noqa: BLE001 - normalised to a safe public error
        raise ImageReferenceError("The SVG could not be parsed.") from exc

    candidates: list[tuple[float, list[tuple[float, float]], bool]] = []
    segment_count = 0
    try:
        for element in document.elements():
            if element is document or not isinstance(element, SvgShape):
                continue
            values = getattr(element, "values", {}) or {}
            if str(values.get("visibility", "")).casefold() == "hidden":
                continue
            if str(values.get("display", "")).casefold() == "none":
                continue
            path = SvgPath(element)
            path.reify()
            segment_count += len(path)
            if segment_count > MAX_SVG_SEGMENTS:
                raise ImageReferenceError("The SVG is too complex to turn into a route.")
            for subpath in path.as_subpaths():
                sampled_path = SvgPath(subpath)
                if len(sampled_path) < 2:
                    continue
                length = float(sampled_path.length(error=1e-3))
                if not math.isfinite(length) or length <= 0:
                    continue
                closed = isinstance(sampled_path[-1], Close)
                point_count = min(96, max(12, len(sampled_path) * 6))
                denominator = point_count if closed else point_count - 1
                points: list[tuple[float, float]] = []
                for index in range(point_count):
                    point = sampled_path.point(index / denominator, error=1e-3)
                    if point is None:
                        continue
                    x, y = float(point.x), -float(point.y)
                    if math.isfinite(x) and math.isfinite(y):
                        points.append((x, y))
                if closed and len(points) >= 3:
                    points.append(points[0])
                if len(points) >= 2:
                    candidates.append((length, points, closed))
    except ImageReferenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party parser error
        raise ImageReferenceError("The SVG contains unsupported geometry.") from exc

    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:MAX_SVG_PATHS]
    if not selected:
        raise ImageReferenceError("The SVG contains no usable vector outline.")
    return Shape(
        name=name,
        paths=[points for _, points, _ in selected],
        closed=all(closed for _, _, closed in selected),
        source="reference_svg",
    )


def _reference_name(url: str) -> str:
    return clean_reference_name(unquote(PurePosixPath(urlsplit(url).path).name))


def clean_reference_name(filename: str) -> str:
    """Turn a file name into a short human-readable shape name."""

    filename = PurePosixPath(filename.replace("\\", "/")).name
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    cleaned = re.sub(r"[^\w -]+", " ", stem, flags=re.UNICODE)
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = " ".join(cleaned.split())[:80]
    return cleaned or "imported image"
