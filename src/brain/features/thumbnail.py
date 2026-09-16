"""Thumbnail features (spec §17, §37).

Cheap global image statistics — brightness, contrast, saturation, an edge
proxy for busyness, and how much of the frame the largest bright region
takes. No CLIP, no face detector: those are the right answer at a few
thousand labeled thumbnails, not at a few dozen, and they would make Brain
depend on a model download to score a topic.

Pillow is already a project dependency (MoviePy needs it), but the whole
group degrades to "unknown" if it is missing or the file cannot be read, so
a thumbnail-less candidate simply scores with lower coverage.
"""

from __future__ import annotations

from functools import lru_cache

from ..schema import Candidate, ChannelContext
from .base import PRE_PUBLISH, FeatureSpec

#: Long side the image is reduced to before measuring. Statistics are
#: scale-free and this keeps a 4K thumbnail from costing 40ms to score.
SAMPLE_SIZE = 128


@lru_cache(maxsize=256)
def image_stats(path: str) -> dict[str, float] | None:
    """Global statistics for one image file, or None if unreadable.

    Cached by path: ranking N thumbnail variants re-reads each file once,
    and a rendered thumbnail never changes under its own path mid-run.
    """
    if not path:
        return None
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        return None
    try:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
            image.thumbnail((SAMPLE_SIZE, SAMPLE_SIZE))
            pixels = list(image.getdata())
            if not pixels:
                return None
            width, height = image.size
            edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_pixels = list(edges.getdata())
    except Exception:
        return None

    count = len(pixels)
    luminance = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    brightness = sum(luminance) / count
    variance = sum((value - brightness) ** 2 for value in luminance) / count
    saturations = [(max(p) - min(p)) / (max(p) or 1) for p in pixels]
    saturation = sum(saturations) / count
    warm = sum(1 for r, g, b in pixels if r > b + 20) / count
    edge_hits = [1 if value > 60 else 0 for value in edge_pixels]
    edge_density = sum(edge_hits) / max(1, len(edge_hits))

    # Composition needs the grid, not just the histogram: where the detail
    # sits in the frame is a different question from how much there is.
    stats = {
        "brightness": brightness / 255.0,
        "contrast": (variance ** 0.5) / 255.0,
        "saturation": saturation,
        "warm_ratio": warm,
        "edge_density": edge_density,
        "dark_ratio": sum(1 for value in luminance if value < 40) / count,
        "saturation_spread": _stdev(saturations),
        "face_ratio": sum(1 for p in pixels if _is_skin(p)) / count,
    }
    stats.update(_composition(edge_hits, width, height))
    return stats


def _stdev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _is_skin(pixel: tuple[int, int, int]) -> bool:
    """Loose RGB skin-tone test.

    A crude stand-in for "is there a person in this thumbnail", which is one
    of the few visual properties with a large, repeatedly-measured effect on
    click-through. Deliberately not a face detector: that would mean OpenCV
    or a model download for a feature we can approximate in six comparisons.
    Over-counts warm backgrounds, and the model is free to discount it.
    """
    r, g, b = pixel
    return (
        r > 95 and g > 40 and b > 20
        and r > g and r > b
        and (max(pixel) - min(pixel)) > 15
        and abs(r - g) > 15
    )


def _composition(edge_hits: list[int], width: int, height: int) -> dict[str, float]:
    """Where the visual energy sits: centre vs frame, top vs bottom, bands.

    A cover whose detail is concentrated in the middle reads as one subject;
    one with detail spread to the corners reads as clutter at feed size. The
    band measure looks for rows that are far busier than their neighbours,
    which is what a line of overlaid text looks like to an edge filter.
    """
    if not edge_hits or width < 9 or height < 9:
        return {"center_energy": 0.0, "top_heavy": 0.5, "text_bands": 0.0}

    rows = [edge_hits[y * width:(y + 1) * width] for y in range(height)]
    total = sum(edge_hits) or 1

    x0, x1 = width // 3, 2 * width // 3
    y0, y1 = height // 3, 2 * height // 3
    center = sum(sum(row[x0:x1]) for row in rows[y0:y1])

    top = sum(sum(row) for row in rows[: height // 2])

    row_density = [sum(row) / width for row in rows]
    mean = sum(row_density) / len(row_density)
    spread = _stdev(row_density) or 1e-6
    bands = sum(1 for value in row_density if value > mean + 2.0 * spread)

    return {
        # Share of all edge energy inside the central ninth, over the 1/9 a
        # uniform image would put there: 1.0 is uniform, >1 is a subject.
        "center_energy": (center / total) / (1.0 / 9.0),
        "top_heavy": top / total,
        "text_bands": bands / len(row_density),
    }


def _stat(candidate: Candidate, key: str) -> float | None:
    stats = image_stats(candidate.thumbnail_path)
    return None if stats is None else stats.get(key)


def thumb_brightness(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "brightness")


def thumb_contrast(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "contrast")


def thumb_saturation(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "saturation")


def thumb_warm_ratio(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "warm_ratio")


def thumb_edge_density(c: Candidate, _: ChannelContext) -> float | None:
    """Busyness proxy — a thumbnail that is all detail reads as noise at
    the size a phone actually renders it."""
    return _stat(c, "edge_density")


def thumb_dark_ratio(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "dark_ratio")


def thumb_saturation_spread(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "saturation_spread")


def thumb_face_ratio(c: Candidate, _: ChannelContext) -> float | None:
    """Share of skin-tone pixels - a rough "is there a person here"."""
    return _stat(c, "face_ratio")


def thumb_center_energy(c: Candidate, _: ChannelContext) -> float | None:
    """Detail concentrated in the middle (one subject) vs spread (clutter)."""
    return _stat(c, "center_energy")


def thumb_top_heavy(c: Candidate, _: ChannelContext) -> float | None:
    return _stat(c, "top_heavy")


def thumb_text_bands(c: Candidate, _: ChannelContext) -> float | None:
    """Rows far busier than their neighbours - overlaid text, roughly."""
    return _stat(c, "text_bands")


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("thumb_brightness", "thumbnail", thumb_brightness, PRE_PUBLISH, 0.5,
                "Mean luminance"),
    FeatureSpec("thumb_contrast", "thumbnail", thumb_contrast, PRE_PUBLISH, 0.2,
                "Luminance standard deviation"),
    FeatureSpec("thumb_saturation", "thumbnail", thumb_saturation, PRE_PUBLISH, 0.4,
                "Mean colour saturation"),
    FeatureSpec("thumb_warm_ratio", "thumbnail", thumb_warm_ratio, PRE_PUBLISH, 0.5,
                "Share of warm-dominant pixels"),
    FeatureSpec("thumb_edge_density", "thumbnail", thumb_edge_density, PRE_PUBLISH,
                0.15, "Share of edge pixels (visual busyness)"),
    FeatureSpec("thumb_dark_ratio", "thumbnail", thumb_dark_ratio, PRE_PUBLISH,
                0.2, "Share of near-black pixels"),
    FeatureSpec("thumb_saturation_spread", "thumbnail", thumb_saturation_spread,
                PRE_PUBLISH, 0.2, "Variation in colour saturation"),
    FeatureSpec("thumb_face_ratio", "thumbnail", thumb_face_ratio, PRE_PUBLISH,
                0.05, "Share of skin-tone pixels (person present)"),
    FeatureSpec("thumb_center_energy", "thumbnail", thumb_center_energy,
                PRE_PUBLISH, 1.0, "Detail concentrated centrally vs spread"),
    FeatureSpec("thumb_top_heavy", "thumbnail", thumb_top_heavy, PRE_PUBLISH,
                0.5, "Share of detail in the top half"),
    FeatureSpec("thumb_text_bands", "thumbnail", thumb_text_bands, PRE_PUBLISH,
                0.0, "Rows that look like overlaid text"),
)
