"""Cutting a span out of the source and making it a vertical short.

ffmpeg directly, not MoviePy. One pass does the cut, the 16:9 to 9:16
reframe and the burned-in subtitles, which is several times faster than
loading the source into MoviePy, and it needs no ImageMagick — the
dependency that makes subtitle rendering fragile everywhere else in this
project.

Subtitles are written as ASS rather than SRT because a short needs them
large, centred and outlined, and ASS is the only format that carries that
styling. ffmpeg is invoked with its working directory set to the output
folder so the filter graph can name the subtitle file relatively: an
absolute Windows path inside a filter argument needs its drive colon
escaped, and that escaping is a reliable source of silent failures.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import textwrap
from typing import Callable, Sequence

from . import config as clips_config
from . import faces
from .segments import Word


SUBTITLE_NAME = "subs.ass"

#: Words per subtitle line. Short form puts two or three words on screen
#: at a time; a full sentence at this size does not fit and is not read.
WORDS_PER_LINE = 3

#: A line is held at least this long even if its words are faster, so the
#: text does not strobe.
MIN_LINE_SECONDS = 0.55


class RenderError(RuntimeError):
    """A clip could not be rendered, with a reason worth printing."""


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RenderError("`ffmpeg` is not on PATH")
    return found


def font_family(path: str) -> str:
    """The font's real family name, which is what ASS has to name.

    ASS resolves a font by family, not by filename, so
    `Montserrat-ExtraBold.ttf` has to be written as whatever its metadata
    calls it or libass silently falls back to a default.
    """
    try:
        from PIL import ImageFont

        font = ImageFont.truetype(path, 32)
        family, style = font.getname()
        if style and style.lower() not in ("regular", "book"):
            return f"{family} {style}".strip()
        return family or "Arial"
    except Exception:
        return "Arial"


def _timestamp(seconds: float) -> str:
    """ASS wants H:MM:SS.cc, centiseconds and a single-digit hour."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def group_lines(
    words: Sequence[Word], per_line: int = WORDS_PER_LINE
) -> list[tuple[float, float, str]]:
    """Timed subtitle lines from timed words.

    Pure, so the timing can be tested without rendering. A line ends early
    when its last word carries sentence-final punctuation: breaking on the
    sentence reads better than breaking on a word count.
    """
    lines: list[tuple[float, float, str]] = []
    bucket: list[Word] = []

    def flush() -> None:
        if not bucket:
            return
        start = bucket[0].start
        end = max(bucket[-1].end, start + MIN_LINE_SECONDS)
        text = " ".join(w.text for w in bucket).strip()
        if text:
            lines.append((start, end, text))
        bucket.clear()

    for word in words:
        bucket.append(word)
        if len(bucket) >= per_line or word.text.rstrip()[-1:] in ".!?…":
            flush()
    flush()

    # Never let one line's end run past the next line's start.
    fixed: list[tuple[float, float, str]] = []
    for index, (start, end, text) in enumerate(lines):
        if index + 1 < len(lines):
            end = min(end, max(start + 0.2, lines[index + 1][0]))
        fixed.append((start, end, text))
    return fixed


def write_ass(
    words: Sequence[Word],
    out_path: str,
    width: int,
    height: int,
    font_path: str = "",
    font_size: int = 54,
    margin_v: int | None = None,
    title: str = "",
    title_size: int = 88,
    title_alignment: int = 8,
    title_margin_v: int | None = None,
    duration: float = 0.0,
) -> str:
    """An ASS subtitle file styled for vertical video.

    `margin_v` is the distance from the bottom edge to the text. The
    default sits in the lower third; the facecam layout moves it up to the
    seam between the camera and the gameplay, where it covers neither.

    `title`, when given, is burned for the whole clip (`0..duration`) in a
    style of its own, on a layer above the subtitles: the hook text a clip
    with no speech otherwise lacks. `title_alignment` is an ASS numpad
    position (8 = top centre, 2 = bottom centre) and `title_margin_v` the
    distance from that edge; `title_placement()` works both out.
    """
    family = font_family(font_path) if font_path else "Arial"
    if margin_v is None:
        margin_v = int(height * 0.17)
    if title_margin_v is None:
        title_margin_v = int(height * 0.12)
    side = int(width * 0.07)
    # &HAABBGGRR — ASS is alpha-blue-green-red, not RGB.
    # WrapStyle 0 is libass's smart wrap: a title too wide for the frame
    # breaks into balanced lines. Subtitle lines are two or three words,
    # so they never reach the edge and are unaffected.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clip,{family},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,5,2,2,{side},{side},{int(margin_v)},1
Style: Title,{family},{int(title_size)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,7,3,{int(title_alignment)},{side},{side},{int(title_margin_v)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def escape(text: str) -> str:
        return (
            text.replace("\\", "")
            .replace("{", "(")
            .replace("}", ")")
            .replace("\n", " ")
        )

    rows = []
    clean_title = re.sub(r"\s+", " ", escape(title or "")).strip()
    if clean_title:
        rows.append(
            f"Dialogue: 1,{_timestamp(0.0)},{_timestamp(max(0.1, duration))},"
            f"Title,,0,0,0,,{clean_title.upper()}"
        )
    for start, end, text in group_lines(words):
        rows.append(
            f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Clip,,0,0,0,,"
            f"{escape(text).upper()}"
        )
    with open(out_path, "w", encoding="utf-8") as file:
        file.write(header + "\n".join(rows) + "\n")
    return out_path


TITLE_OVERLAY_MODES = ("auto", "always", "never")


def should_burn_title(mode: str, has_subtitles: bool, title: str) -> bool:
    """Whether the title goes on screen. Pure, so the policy is tested.

    `auto` burns it only when the clip will carry no subtitles — a clip
    with no speech, or one rendered with subtitles off. That is exactly
    the clip with nothing on screen to hook a viewer; a clip that is
    already being read along with does not need a second block of text.
    """
    if not (title or "").strip():
        return False
    mode = (mode or "auto").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return not has_subtitles


def title_placement(
    layout: str,
    width: int,
    height: int,
    source_size: tuple[int, int] | None,
    text: str,
    size: int,
) -> tuple[int, int]:
    """Where the title sits: `(ass_alignment, margin_v)`. Pure.

    - `blur`: centred in the blurred band above the video, which is empty
      space on a 16:9 source. The band is what is left of the height once
      the source is scaled to the frame's width; the title block's height
      is estimated from how many lines it wraps to.
    - `crop`: the video fills the frame, so near the top, over the image.
    - `facecam`: just below the camera panel, above dialogue captions.
    """
    if layout == "facecam":
        cam_h = int(round(height * FACECAM_SHARE / 2)) * 2
        return 8, cam_h + int(height * 0.035)
    if layout == "crop" or not source_size or not source_size[0] or not source_size[1]:
        return 8, int(height * 0.12)

    source_w, source_h = source_size
    video_h = min(height, width * source_h / source_w)
    band = (height - video_h) / 2
    chars_per_line = max(1, int(width * 0.86 / (size * 0.55)))
    lines = max(1, math.ceil(len((text or "").strip()) / chars_per_line))
    block = lines * size * 1.25
    if band < block + 40:
        # A source tall enough to leave no band: fall back to over the image.
        return 8, int(height * 0.12)
    return 8, int(max(40, (band - block) / 2))


LAYOUTS = ("blur", "crop", "facecam")


def fitted_title_size(width: int, height: int, text: str, requested: int) -> int:
    """Keep a wrapped hook within its own upper text region."""
    size = max(16, requested)
    while size > 16:
        columns = max(1, int(width * 0.86 / (size * 0.65)))
        lines = max(1, len(textwrap.wrap((text or "").upper(), width=columns)))
        if lines * size * 1.25 <= height * 0.20:
            break
        size -= 1
    return size

#: Share of the vertical frame the facecam panel takes in the `facecam`
#: layout. Gaming shorts put the reaction on top and the game below; the
#: game still gets most of the screen because it is what is happening.
FACECAM_SHARE = 0.3125  # 600 of 1920


def parse_box(value) -> tuple[int, int, int, int] | None:
    """A crop box from "x,y,w,h" (source pixels), or None if unusable."""
    if not value:
        return None
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [p for p in str(value).replace(" ", "").split(",") if p]
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(float(p)) for p in parts)
    except (TypeError, ValueError, OverflowError):
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        return None
    return x, y, w, h


def facecam_geometry(
    width: int,
    height: int,
    source_w: int,
    source_h: int,
    box: tuple[int, int, int, int],
) -> dict[str, int]:
    """Where everything goes in the facecam layout. Pure, so it is tested.

    The gameplay panel is a full-height crop of the source at the panel's
    aspect ratio, centred, then pushed left if it would include a facecam
    in the top-right, so the face is not shown twice.
    """
    cam_h = int(round(height * FACECAM_SHARE / 2)) * 2
    game_h = height - cam_h
    crop_h = source_h
    crop_w = min(source_w, int(round(source_h * width / game_h / 2)) * 2)
    crop_x = (source_w - crop_w) // 2
    box_x = box[0]
    if crop_x + crop_w > box_x and box_x - crop_w >= 0 and box[1] < source_h / 2:
        crop_x = box_x - crop_w
    return {
        "cam_h": cam_h,
        "game_h": game_h,
        "crop_w": crop_w,
        "crop_h": crop_h,
        "crop_x": max(0, crop_x),
        "crop_y": 0,
    }


def crop_to_fill(width: int, height: int) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1")


def _filter_graph(
    width: int,
    height: int,
    reframe: str,
    subtitle_file: str = "",
    fonts_dir: str = "",
    facecam: tuple[int, int, int, int] | None = None,
    source_size: tuple[int, int] | None = None,
    face_track: faces.FaceTrack | None = None,
) -> str:
    """The reframe, and the subtitle burn when there is one.

    `blur` keeps the whole frame visible over a blurred, zoomed copy of
    itself — nothing is cropped away, which matters when the source is a
    talking head that is not centred. `crop` cuts the sides, which is
    tighter but loses whatever was at the edges. `facecam` stacks the
    streamer's camera on top of the gameplay, which is how gaming shorts
    are cut: at full-frame size a facecam in the corner is a few pixels
    wide on a phone.
    """
    if reframe == "facecam" and facecam and source_size:
        x, y, w, h = facecam
        geo = facecam_geometry(width, height, source_size[0], source_size[1], facecam)
        chain = (
            f"[0:v]split=2[cam][game];"
            f"[cam]crop={w}:{h}:{x}:{y},"
            f"scale={width}:{geo['cam_h']}:force_original_aspect_ratio=increase,"
            f"crop={width}:{geo['cam_h']}[camv];"
            f"[game]crop={geo['crop_w']}:{geo['crop_h']}:{geo['crop_x']}:{geo['crop_y']},"
            f"scale={width}:{geo['game_h']}[gamev];"
            f"[camv][gamev]vstack=inputs=2,setsar=1[v]"
        )
    elif reframe == "crop":
        chain = f"[0:v]{crop_to_fill(width, height)}[v]"
    else:
        chain = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=28[bgb];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
    if face_track:
        # Keep the original main branch intact: changing its scale/crop to
        # make room for the face would also change the user's existing zoom.
        # The camera is a second, full-motion view of the original source.
        cam_h = int(round(height * FACECAM_SHARE / 2)) * 2
        main = chain.replace("[0:v]", "[main]").replace("[v]", "[mainv]")
        chain = (
            "[0:v]split=2[main][tracked];" + main
            + f";[tracked]crop={face_track.crop_w}:{face_track.crop_h}:"
            f"x='{face_track.position_expression('x')}':"
            f"y='{face_track.position_expression('y')}',"
            f"scale={width}:{cam_h},setsar=1[facev];"
            f"[mainv][facev]overlay=0:0:"
            f"enable='{face_track.enable_expression()}'[v]"
        )
    if subtitle_file:
        # Relative filename, resolved against ffmpeg's working directory.
        # fontsdir needs its Windows drive colon escaped inside the filter.
        fonts = ""
        if fonts_dir:
            escaped = os.path.abspath(fonts_dir).replace("\\", "/").replace(":", r"\:")
            fonts = f":fontsdir='{escaped}'"
        chain = chain.replace("[v]", "[vr]") + f";[vr]subtitles={subtitle_file}{fonts}[v]"
    return chain


#: Encoder arguments to try in order. A hardware encoder can be *listed*
#: by ffmpeg and still refuse to open — no free session, a driver too old,
#: a resolution it will not take — so the list is a fallback chain that is
#: actually attempted, not a capability lookup. `YouTube.py` does the same
#: for the same reason; discovering it by trying is the only reliable way.
ENCODER_CANDIDATES: tuple[tuple[str, list[str]], ...] = (
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "23", "-preset", "p4"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "23"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "23"]),
    ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]),
)


def _listed_encoders() -> set[str]:
    try:
        listing = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        available = f"{listing.stdout}\n{listing.stderr}"
    except (RenderError, subprocess.SubprocessError):
        return {"libx264"}
    found = {name for name, _ in ENCODER_CANDIDATES if name in available}
    found.add("libx264")
    return found


def encoder_chain() -> list[tuple[str, list[str]]]:
    """The encoders worth attempting, best first, libx264 always last."""
    listed = _listed_encoders()
    chain = [(name, args) for name, args in ENCODER_CANDIDATES if name in listed]
    if not any(name == "libx264" for name, _ in chain):
        chain.append(ENCODER_CANDIDATES[-1])
    return chain


def visible_source_region(
    width: int, height: int, source_size: tuple[int, int]
) -> faces.Box:
    """Source pixels retained by the existing centred `crop` layout."""
    source_w, source_h = source_size
    scale = max(width / source_w, height / source_h)
    visible_w, visible_h = width / scale, height / scale
    return (
        (source_w - visible_w) / 2, (source_h - visible_h) / 2,
        visible_w, visible_h,
    )


def render_clip(
    source_path: str,
    start: float,
    duration: float,
    out_path: str,
    words: Sequence[Word] | None = None,
    subtitles: bool = True,
    layout: str | None = None,
    facecam=None,
    title: str = "",
    title_overlay: str | None = None,
    on_status: Callable[[str], None] | None = None,
    render_info: dict | None = None,
) -> str:
    """Cut, reframe and subtitle one clip. Returns the output path.

    `title_overlay` ("auto", "always", "never"; the `clips.title_overlay`
    setting when None) decides whether `title` is burned in as hook text.
    """
    width = int(clips_config.setting("width"))
    height = int(clips_config.setting("height"))
    fps = int(clips_config.setting("fps"))
    reframe = str(layout or clips_config.setting("reframe"))
    box = parse_box(facecam if facecam is not None else clips_config.setting("facecam"))

    source_size = None
    margin_v = None
    face_track = None
    auto_facecam = bool(clips_config.setting("auto_facecam"))
    if reframe == "facecam" or (auto_facecam and reframe == "crop"):
        info = probe(source_path)
        if info.get("width") and info.get("height"):
            source_size = (int(info["width"]), int(info["height"]))

    if reframe == "facecam" and box:
        if not source_size:
            raise RenderError("could not read source dimensions for the camera panel")
        x, y, w, h = box
        if x + w > source_size[0] or y + h > source_size[1]:
            raise RenderError("the camera box extends beyond the source video")
    else:
        if reframe == "facecam":
            reframe = "blur"
        # The full-frame layout already contains every source face. Only
        # a layout that actually removes source pixels needs a rescue panel.
        if auto_facecam and reframe == "crop":
            if not source_size:
                raise RenderError("could not read source dimensions for face detection")
            if on_status:
                on_status("checking whether the clip crop cuts a face")
            cam_h = int(round(height * FACECAM_SHARE / 2)) * 2
            try:
                face_track = faces.scan_faces(
                    source_path, max(0.0, start), max(0.1, duration),
                    source_size, panel_aspect=width / cam_h,
                    visible_region=visible_source_region(width, height, source_size),
                )
            except faces.FaceDetectionError as exc:
                raise RenderError(str(exc)) from exc
            if on_status:
                on_status(
                    "face outside the crop: showing it above only where needed; keeping source zooms"
                    if face_track else "no face needs a top panel; keeping the selected framing"
                )

    has_face_panel = reframe == "facecam" or face_track is not None
    if has_face_panel or reframe == "crop":
        # Lower-middle captions stay clear of both the upper hook and
        # dialogue already burned near the bottom of the source footage.
        margin_v = int(height * 0.25)
    if render_info is not None:
        render_info.update({
            "layout": "facecam" if has_face_panel else reframe,
            "main_layout": reframe,
            "auto_facecam": face_track is not None,
        })

    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    has_subtitles = bool(subtitles and words)
    mode = str(title_overlay or clips_config.setting("title_overlay") or "auto")
    burn_title = should_burn_title(mode, has_subtitles, title)
    title_size = int(clips_config.setting("title_font_size"))
    if has_face_panel or reframe == "crop":
        title_size = fitted_title_size(width, height, title, title_size)
    title_alignment, title_margin_v = 8, None
    if burn_title:
        if reframe == "blur" and source_size is None:
            info = probe(source_path)
            if info.get("width") and info.get("height"):
                source_size = (int(info["width"]), int(info["height"]))
        title_alignment, title_margin_v = title_placement(
            "facecam" if has_face_panel else reframe,
            width, height, source_size, title, title_size
        )

    subtitle_file = ""
    if has_subtitles or burn_title:
        # Named after the clip so two clips in one directory cannot share
        # a subtitle file.
        subtitle_file = os.path.splitext(os.path.basename(out_path))[0] + ".ass"
        write_ass(
            words if has_subtitles else [],
            os.path.join(out_dir, subtitle_file),
            width,
            height,
            clips_config.font_path(),
            int(clips_config.setting("subtitle_font_size")),
            margin_v=margin_v,
            title=title if burn_title else "",
            title_size=title_size,
            title_alignment=title_alignment,
            title_margin_v=title_margin_v,
            duration=duration,
        )

    final = os.path.join(out_dir, os.path.basename(out_path))
    graph = _filter_graph(
        width,
        height,
        reframe,
        subtitle_file,
        clips_config.fonts_dir(),
        facecam=box,
        source_size=source_size,
        face_track=face_track,
    )
    failures: list[str] = []

    for name, encoder_args in encoder_chain():
        command = [
            _ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
            # Before -i: ffmpeg seeks the input instead of decoding up to
            # the cut, which on a long source is the difference between
            # seconds and minutes. Accurate because the output is
            # re-encoded anyway.
            "-ss", f"{max(0.0, start):.3f}",
            "-t", f"{max(0.1, duration):.3f}",
            "-i", os.path.abspath(source_path),
            "-filter_complex", graph,
            "-map", "[v]",
            "-map", "0:a?",
            *encoder_args,
            "-r", str(fps),
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            # Same compatibility flags the rest of the project renders with.
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart",
            os.path.basename(out_path),
        ]
        result = subprocess.run(
            command,
            cwd=out_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if result.returncode == 0 and os.path.isfile(final):
            return final
        tail = [
            line.strip()
            for line in (result.stderr or result.stdout or "").splitlines()
            if line.strip()
        ]
        failures.append(f"{name}: " + (tail[-1] if tail else f"exit {result.returncode}"))

    raise RenderError("every encoder failed:\n  " + "\n  ".join(failures))


def probe(path: str) -> dict[str, float | int | str]:
    """What actually came out: dimensions, duration, whether it has audio."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries",
                "stream=codec_type,width,height:format=duration",
                "-of", "default=noprint_wrappers=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.SubprocessError:
        return {}
    out: dict[str, float | int | str] = {"has_audio": 0}
    for line in (result.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "width" and value.isdigit():
            out["width"] = int(value)
        elif key == "height" and value.isdigit():
            out["height"] = int(value)
        elif key == "duration":
            try:
                out["duration"] = round(float(value), 2)
            except ValueError:
                pass
        elif key == "codec_type" and value.strip() == "audio":
            out["has_audio"] = 1
    return out
