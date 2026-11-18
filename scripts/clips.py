"""MoneyPrinter Clips command line.

Learn what wins in short form, then cut it out of a long video:

    python scripts/clips.py trends                  what wins, and how it knows
    python scripts/clips.py trends --refresh        re-read the channel history
    python scripts/clips.py preview <url|file>      pick clips, render nothing
    python scripts/clips.py run <url|file> --rights "my own channel"
    python scripts/clips.py full <url|file> --clip-seconds 60 --rights "my own channel"
    python scripts/clips.py full <url|file> --batch-size 3  # continue the next parts
    python scripts/clips.py cut <url|file> --at 1:32-2:03 --at 8:07-8:48
                                --layout facecam --facecam 898,11,360,200
                                --title "..." --title-overlay always
    python scripts/clips.py list                   clips waiting for review
    python scripts/clips.py approve <clip_id>
    python scripts/clips.py reject <clip_id>
    python scripts/clips.py upload <clip_id> --channel-id <channel_id>
    python scripts/clips.py decisions               what was picked, and what lost

A URL needs `--rights`: a clip is a derivative work, and the note is kept
with every clip cut from that source. Creation leaves drafts in `.mp/clips/`;
only the explicit `upload` command uploads a reviewed clip to YouTube.

Every command accepts `--json`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import clips  # noqa: E402
from clips import trends  # noqa: E402
from clips import upload as clips_upload  # noqa: E402


def _print(message: str = "") -> None:
    """Print without dying on a Windows console that is not UTF-8."""
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(message.encode(encoding, "replace").decode(encoding))
    sys.stdout.flush()


def _status(message: str) -> None:
    _print(f"  .. {message}")


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


# -- Commands ----------------------------------------------------------


def cmd_trends(args) -> int:
    profile = clips.trend_profile(refresh=args.refresh)
    if args.json:
        _print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0

    if not profile.get("ready"):
        _print("Not enough history to say what wins yet.")
        _print(f"  {profile.get('reason', '')}")
        _print("")
        _print("Import reference channels so there is something to learn from:")
        _print("  python scripts/brain.py  (see docs/Brain.md)")
        return 0

    _print(
        f"Learned from {profile['sample_size']} shorts across "
        f"{len(profile.get('channels') or [])} channels "
        f"({profile['exemplars']} winners vs {profile['baseline']} others)."
    )
    _print("")
    _print(trends.prompt_brief(profile))
    _print("")

    traits = profile.get("title_traits") or {}
    noise = [name for name, stats in traits.items() if not stats.get("meaningful")]
    if noise:
        _print("Explains nothing either way (winners and losers do it alike):")
        _print("  " + ", ".join(sorted(noise)))
    low, high = trends.duration_window(profile)
    _print("")
    _print(f"Clips will target {low:.0f}-{high:.0f}s.")
    return 0


def _run(args, dry_run: bool) -> int:
    try:
        result = clips.make(
            args.target,
            count=args.count,
            rights_note=args.rights,
            language=args.language,
            use_llm=not args.no_llm,
            subtitles=not args.no_subtitles,
            refresh=args.refresh,
            dry_run=dry_run,
            max_minutes=args.max_minutes,
            layout=args.layout or None,
            facecam=args.facecam or None,
            captions="whisper" if args.whisper else args.captions,
            title_overlay=args.title_overlay or None,
            on_status=None if args.json else _status,
        )
    except (clips.ClipError, clips.ingest.IngestError, clips.transcribe.TranscribeError) as exc:
        if args.json:
            _print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            _print(f"Stopped: {exc}")
        return 1

    if args.json:
        _print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    _print("")
    scoring = result["scoring"]
    how = "model + heuristics" if scoring.get("llm") else "heuristics only"
    _print(
        f"{len(result['clips'])} of {result['candidates']} candidates chosen ({how})."
    )
    if not result["trend_profile_ready"]:
        _print("No trend profile yet — lengths came from the configured bounds.")
    _print("")

    for index, clip in enumerate(result["clips"], start=1):
        _print(f"{index}. {clip['title']}")
        _print(
            f"   {_minutes(clip['start'])}-{_minutes(clip['end'])} "
            f"({clip['duration']:.0f}s)  score {clip['score']:.2f}"
        )
        if dry_run:
            text = clip["text"]
            _print(f"   {text[:160]}{'...' if len(text) > 160 else ''}")
        else:
            probe = clip.get("probe") or {}
            _print(
                f"   {clip['path']}"
                + (
                    f"  [{probe.get('width')}x{probe.get('height')}, "
                    f"{probe.get('duration')}s, "
                    f"{'audio' if probe.get('has_audio') else 'NO AUDIO'}]"
                    if probe
                    else ""
                )
            )
        _print("")

    for failure in result.get("failures") or []:
        _print(f"! {failure}")

    if not dry_run and result["clips"]:
        _print("All drafts. Nothing has been uploaded.")
        _print("  python scripts/clips.py list")
    return 0


def cmd_run(args) -> int:
    return _run(args, dry_run=False)


def cmd_preview(args) -> int:
    return _run(args, dry_run=True)


def cmd_cut(args) -> int:
    try:
        result = clips.cut(
            args.target,
            args.at,
            rights_note=args.rights,
            titles=args.title,
            language=args.language,
            captions="whisper" if args.whisper else args.captions,
            layout=args.layout or None,
            facecam=args.facecam or None,
            subtitles=not args.no_subtitles,
            refresh=args.refresh,
            title_overlay=args.title_overlay or None,
            on_status=None if args.json else _status,
        )
    except (clips.ClipError, clips.ingest.IngestError, clips.transcribe.TranscribeError) as exc:
        _print(json.dumps({"error": str(exc)}) if args.json else f"Stopped: {exc}")
        return 1
    if args.json:
        _print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _print("")
    _print(f"{len(result['clips'])} clip(s), subtitles from {result['words_from']}.")
    _print("")
    for index, clip in enumerate(result["clips"], start=1):
        probe = clip.get("probe") or {}
        _print(f"{index}. {clip['title']}")
        _print(
            f"   {_minutes(clip['start'])}-{_minutes(clip['end'])} "
            f"({clip['duration']:.0f}s)  [{clip['layout']}]"
        )
        _print(
            f"   {clip['path']}  [{probe.get('width')}x{probe.get('height')}, "
            f"{probe.get('duration')}s, {'audio' if probe.get('has_audio') else 'NO AUDIO'}]"
        )
        _print("")
    for failure in result.get("failures") or []:
        _print(f"! {failure}")
    if result["clips"]:
        _print("All drafts. Nothing has been uploaded.")
    return 0


def cmd_full(args) -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            result = clips.full(
                args.target, clip_seconds=args.clip_seconds,
                batch_size=args.batch_size, resume=not args.no_resume,
                rights_note=args.rights, language=args.language,
                captions="whisper" if args.whisper else args.captions,
                layout=args.layout or None, facecam=args.facecam or None,
                subtitles=not args.no_subtitles, refresh=args.refresh,
                title_overlay=args.title_overlay or None, max_minutes=args.max_minutes,
                on_status=None if args.json else _status,
            )
    except (clips.ClipError, clips.ingest.IngestError, clips.transcribe.TranscribeError) as exc:
        if args.json:
            _print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            _print(f"Stopped: {exc}")
        return 1
    if args.json:
        _print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sequence = result["sequence"]
        _print(
            f"{len(result['clips'])} new clip(s); "
            f"{sequence['completed_parts']}/{sequence['total_parts']} parts complete."
        )
        for clip in result["clips"]:
            _print(f"  {clip['title']}  {_minutes(clip['start'])}-{_minutes(clip['end'])}")
            _print(f"  {clip['path']}")
        if sequence["complete"]:
            _print("The whole video is covered. All clips remain drafts.")
        else:
            _print(
                f"{sequence['remaining_parts']} part(s) left, starting at "
                f"{_minutes(sequence['next_start'])}. Run full again to continue."
            )
        for failure in result.get("failures") or []:
            _print(f"! {failure}")
    return 1 if result.get("failures") else 0


def cmd_list(args) -> int:
    rows = clips.catalogue(status=args.status, source_id=args.source)
    if args.json:
        _print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        _print("No clips yet. Make some:")
        _print('  python scripts/clips.py run <url> --rights "my own channel"')
        return 0
    for clip in rows:
        _print(f"{clip['id']}  [{clip.get('status', '?'):9}] {clip.get('title', '')}")
        _print(
            f"              {clip.get('duration', 0):.0f}s from "
            f"{clip.get('source_title') or clip.get('source_url') or 'a local file'}"
            f"  ({clip.get('rights_note', 'no rights note')})"
        )
        _print(f"              {clip.get('path', '')}")
    _print("")
    _print(f"{len(rows)} clip(s).")
    return 0


def _set_status(args, status: str) -> int:
    updated = clips.review(args.clip_id, status)
    if not updated:
        _print(f"No clip with id '{args.clip_id}'.")
        return 1
    if args.json:
        _print(json.dumps(updated, ensure_ascii=False, indent=2))
    else:
        _print(f"{updated['id']} -> {status}: {updated.get('title', '')}")
    return 0


def cmd_approve(args) -> int:
    return _set_status(args, clips.store.STATUS_APPROVED)


def cmd_reject(args) -> int:
    return _set_status(args, clips.store.STATUS_REJECTED)


def cmd_upload(args) -> int:
    try:
        # The shared Firefox uploader writes its own logs. In JSON mode,
        # keep those on stderr so stdout remains a single JSON result.
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            record = clips_upload.upload_clip(
                args.clip_id, args.channel_id,
                title=args.title, description=args.description,
                on_status=None if args.json else _status,
            )
    except clips_upload.UploadError as exc:
        if args.json:
            _print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            _print(f"Stopped: {exc}")
        return 1
    upload = record.get("youtube_upload") or {}
    if args.json:
        _print(json.dumps(record, ensure_ascii=False, indent=2))
    elif upload.get("status") == "uploaded":
        _print(f"Uploaded to YouTube: {upload.get('url', '')}")
    elif upload.get("status") == "pending_confirmation":
        _print("Upload needs confirmation in YouTube Studio. Do not upload this clip again.")
    else:
        _print(f"Upload failed: {upload.get('error') or 'check the upload log'}")
    if upload.get("status") in ("uploaded", "pending_confirmation"):
        return 0
    return 1


def cmd_decisions(args) -> int:
    entries = clips.decisions(limit=args.limit)
    if args.json:
        _print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        _print("Nothing decided yet.")
        return 0
    for entry in entries:
        _print(
            f"{entry.get('at', '')}  {entry.get('source_title') or entry.get('source_id', '')}"
        )
        _print(
            f"  {len(entry.get('chosen') or [])} chosen of "
            f"{entry.get('candidates', 0)} candidates"
            + ("  (dry run)" if entry.get("dry_run") else "")
        )
        for clip in entry.get("chosen") or []:
            _print(
                f"    + {_minutes(clip['start'])} ({clip['score']:.2f}) "
                f"{clip.get('title', '')}"
            )
        runners = (entry.get("rejected") or [])[:3]
        for clip in runners:
            _print(f"    - {_minutes(clip['start'])} ({clip['score']:.2f})")
    return 0


# -- Wiring ------------------------------------------------------------


def _add_render_options(sub) -> None:
    sub.add_argument(
        "--layout",
        choices=("blur", "crop", "facecam"),
        default="",
        help="blur: whole frame; crop: centre; facecam: manual camera panel; "
        "automatic face panels appear only when the framing cuts off the face",
    )
    sub.add_argument(
        "--facecam",
        default="",
        help='optional camera box in source pixels for --layout facecam, "x,y,w,h"; '
        "a manual box forces the top panel; without one, add a panel only if the face is cut off",
    )
    sub.add_argument(
        "--captions",
        default="",
        help="a .vtt/.srt file to take the subtitles from "
        "(downloads use the video's own captions automatically)",
    )
    sub.add_argument(
        "--whisper",
        action="store_true",
        help="ignore any captions and transcribe with Whisper",
    )
    sub.add_argument(
        "--title-overlay",
        choices=("auto", "always", "never"),
        default="",
        help="burn the title on screen: auto = only when the clip has no "
        "subtitles (default: clips.title_overlay)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clips",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers = parser.add_subparsers(dest="command")

    trends_parser = subparsers.add_parser("trends", help="what wins in short form")
    trends_parser.add_argument(
        "--refresh", action="store_true", help="re-read the channel history"
    )
    trends_parser.set_defaults(func=cmd_trends)

    for name, handler, help_text in (
        ("run", cmd_run, "cut and render clips"),
        ("preview", cmd_preview, "pick clips without rendering"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("target", help="a video URL, or a path to a local file")
        sub.add_argument("--count", type=int, default=None, help="how many clips")
        sub.add_argument(
            "--rights",
            default="",
            help='why this source may be clipped, e.g. "my own channel"',
        )
        sub.add_argument("--language", default="", help="transcription language hint")
        sub.add_argument(
            "--no-llm", action="store_true", help="heuristics only, no model call"
        )
        sub.add_argument(
            "--no-subtitles", action="store_true", help="do not burn in subtitles"
        )
        sub.add_argument(
            "--refresh",
            action="store_true",
            help="re-download and re-transcribe instead of reusing the cache",
        )
        sub.add_argument(
            "--max-minutes",
            type=float,
            default=None,
            help="accept a source longer than the configured limit",
        )
        _add_render_options(sub)
        sub.set_defaults(func=handler)

    cut_parser = subparsers.add_parser("cut", help="render hand-picked spans")
    cut_parser.add_argument("target", help="a video URL, or a path to a local file")
    cut_parser.add_argument(
        "--at", action="append", required=True, help="a span, e.g. 1:32-2:03 (repeatable)"
    )
    cut_parser.add_argument(
        "--title", action="append", default=None, help="title per span, in order"
    )
    cut_parser.add_argument("--rights", default="", help="why this source may be clipped")
    cut_parser.add_argument("--language", default="", help="transcription language hint")
    cut_parser.add_argument("--no-subtitles", action="store_true")
    cut_parser.add_argument("--refresh", action="store_true")
    _add_render_options(cut_parser)
    cut_parser.set_defaults(func=cmd_cut)

    full_parser = subparsers.add_parser("full", help="split the entire video into consecutive, resumable clips")
    full_parser.add_argument("target", help="a video URL, or a path to a local file")
    full_parser.add_argument("--clip-seconds", type=float, default=60.0, help="target duration per part, 5 to 180 seconds")
    full_parser.add_argument("--batch-size", type=int, default=0, help="parts to create now; 0 processes every remaining part")
    full_parser.add_argument("--no-resume", action="store_true", help="render all parts again instead of skipping existing ones")
    full_parser.add_argument("--rights", default="", help="why this source may be clipped")
    full_parser.add_argument("--language", default="", help="transcription language hint")
    full_parser.add_argument("--no-subtitles", action="store_true")
    full_parser.add_argument("--refresh", action="store_true")
    full_parser.add_argument("--max-minutes", type=float, default=None)
    _add_render_options(full_parser)
    full_parser.set_defaults(func=cmd_full)

    list_parser = subparsers.add_parser("list", help="the clip catalogue")
    list_parser.add_argument("--status", default="", help="draft, approved, rejected...")
    list_parser.add_argument("--source", default="", help="only one source's clips")
    list_parser.set_defaults(func=cmd_list)

    for name, handler in (("approve", cmd_approve), ("reject", cmd_reject)):
        sub = subparsers.add_parser(name, help=f"mark a clip {name}d")
        sub.add_argument("clip_id")
        sub.set_defaults(func=handler)

    upload_parser = subparsers.add_parser("upload", help="upload one clip to YouTube using Firefox")
    upload_parser.add_argument("clip_id")
    upload_parser.add_argument("--channel-id", required=True, help="existing YouTube channel account id")
    upload_parser.add_argument("--title", default=None, help="upload title (defaults to the clip title)")
    upload_parser.add_argument("--description", default=None, help="upload description (defaults to source and transcript)")
    upload_parser.set_defaults(func=cmd_upload)

    decisions_parser = subparsers.add_parser(
        "decisions", help="what was picked, and what lost"
    )
    decisions_parser.add_argument("--limit", type=int, default=10)
    decisions_parser.set_defaults(func=cmd_decisions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
