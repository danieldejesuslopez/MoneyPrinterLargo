"""One source in, finished vertical clips out.

The order is: get the video, transcribe it, cut it into sentence-aligned
candidates, score them against what wins, keep the best few that are not
the same moment twice, render them, and write down what was chosen and
what was not.

Every step reports through `on_status` rather than printing, so the same
function serves the CLI, a cron run and the webapp without any of them
inheriting the others' output.

Nothing here publishes. Clips land in the catalogue as `draft`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from typing import Any, Callable

from . import config as clips_config
from . import continuation, ingest, render, scoring, segments, store, transcribe, trends


class ClipError(RuntimeError):
    """The run could not produce clips, with a reason worth printing."""


def _noop(message: str) -> None:
    pass


def plan_clips(
    words: list[dict[str, Any]],
    count: int,
    trend_brief: str = "",
    source_title: str = "",
    language: str = "",
    use_llm: bool = True,
    profile: dict[str, Any] | None = None,
    on_status: Callable[[str], None] = _noop,
    duration_bounds: tuple[float, float] | None = None,
    editorial_brief: str = "",
) -> tuple[list[segments.Window], list[segments.Window], dict[str, Any]]:
    """Candidates, scored and chosen, without rendering anything.

    Returns `(chosen, all_candidates, report)`. Split out from `run()` so
    a caller can preview the picks — and their reasons — before spending
    minutes of encoding on them.
    """
    parsed = segments.parse_words(words)
    if not parsed:
        raise ClipError("the transcript has no usable word timings")

    sentences = segments.group_sentences(parsed)
    low, high = duration_bounds if duration_bounds is not None else trends.duration_window(profile)
    if duration_bounds is not None and (
        not all(math.isfinite(v) for v in (low, high)) or not 1 <= low < high <= 180
    ):
        raise ClipError("clip duration bounds must satisfy 1 <= minimum < maximum <= 180")
    on_status(f"{len(sentences)} sentences; targeting {low:.0f}-{high:.0f}s clips")

    candidates = segments.build_windows(sentences, low, high)
    if not candidates:
        raise ClipError(
            f"no span between {low:.0f}s and {high:.0f}s could be cut at sentence "
            "boundaries — the source may be too short, or have no speech"
        )
    on_status(f"{len(candidates)} candidate spans")

    report = scoring.score_windows(
        candidates,
        low,
        high,
        trend_brief=trend_brief + ("\nEditorial brief:\n" + editorial_brief if editorial_brief else ""),
        source_title=source_title,
        language=language,
        use_llm=use_llm,
        on_status=on_status,
    )
    if report.get("llm_error"):
        on_status(f"model scoring unavailable ({report['llm_error']}); heuristics only")

    chosen = segments.select_clips(
        candidates, count, float(clips_config.setting("max_overlap"))
    )
    return chosen, candidates, report


def _words_for(
    source,
    work_dir: str,
    language: str,
    captions: str,
    refresh: bool,
    on_status: Callable[[str], None],
) -> dict[str, Any]:
    """Timed words for a source, best available first.

    1. a caption file the caller passed explicitly;
    2. captions downloaded with the video (person-written, or the
       platform's own recognition in the original language);
    3. Whisper.

    Captions beat a small Whisper model on shouted or overlapping speech,
    which is most of gaming and reaction content, and the platform's
    recognition carries real per-word timing. Pass `captions="whisper"`
    to skip both caption sources.
    """
    use_whisper_only = captions == "whisper"
    if not captions and not use_whisper_only:
        fetched = getattr(source, "captions_path", "")
        if fetched and os.path.isfile(fetched):
            captions = fetched
    if captions and not use_whisper_only:
        if not os.path.isfile(captions):
            raise ClipError(f"no such caption file: {captions}")
        words = transcribe.words_from_captions(captions)
        if words:
            kind = getattr(source, "captions_kind", "") if captions == getattr(
                source, "captions_path", ""
            ) else "file"
            label = f"captions:{kind}" if kind else "captions"
            on_status(f"words from {label}: {len(words)}")
            return {
                "source": label,
                "language": getattr(source, "captions_language", "") or language,
                "word_count": len(words),
                "words": words,
            }
        on_status(f"no timed text in {os.path.basename(captions)}; using Whisper")
    transcript = transcribe.transcribe(
        source.path,
        work_dir,
        language=language,
        refresh=refresh,
        on_status=on_status,
    )
    on_status(f"transcript: {transcript['word_count']} words, {transcript['language']}")
    return {**transcript, "source": "whisper"}


def _title_burned(mode: str | None, has_subtitles: bool, title: str) -> bool:
    """Whether the renderer put the title on screen, for the catalogue."""
    return render.should_burn_title(
        str(mode or clips_config.setting("title_overlay") or "auto"),
        has_subtitles,
        title,
    )


def parse_span(text: str) -> tuple[float, float]:
    """"1:32-2:03", "92-123" or "0:01:32-0:02:03" to seconds.

    Pure, and strict: a span that does not parse, or ends before it
    starts, is an error rather than a guess about what was meant.
    """
    raw = (text or "").strip()
    parts = raw.split("-")
    if len(parts) != 2:
        raise ClipError(f"span '{text}' should look like 1:32-2:03")

    def seconds(value: str) -> float:
        fields = value.strip().split(":")
        if not 1 <= len(fields) <= 3 or not all(fields):
            raise ClipError(f"'{value}' in span '{text}' is not a time")
        try:
            numbers = [float(f) for f in fields]
        except ValueError as exc:
            raise ClipError(f"'{value}' in span '{text}' is not a time") from exc
        total = 0.0
        for number in numbers:
            total = total * 60 + number
        return total

    start, end = seconds(parts[0]), seconds(parts[1])
    if end <= start:
        raise ClipError(f"span '{text}' ends before it starts")
    return start, end


def snap_span(
    words: list[segments.Word], start: float, end: float
) -> tuple[float, float]:
    """Widen a hand-picked span so it does not cut a word in half.

    A person picking "1:32" means the moment, not the exact frame; if a
    word is being spoken across either edge, the edge moves to include
    the whole word. It only ever widens, never trims what was asked for.
    """
    for word in words:
        if word.start < start < word.end:
            start = word.start
        if word.start < end < word.end:
            end = word.end
    return start, end


def cut(
    target: str,
    spans: list[str],
    rights_note: str = "",
    titles: list[str] | None = None,
    language: str = "",
    captions: str = "",
    layout: str | None = None,
    facecam=None,
    subtitles: bool = True,
    refresh: bool = False,
    title_overlay: str | None = None,
    on_status: Callable[[str], None] = _noop,
) -> dict[str, Any]:
    """Render hand-picked spans with the same renderer as `run()`.

    For when a person (or an agent that watched the video) already knows
    which moments are good. The picks are logged as `manual` beside the
    automatic ones, which is what lets the two be compared later: if
    people keep choosing spans the scorer ranked low, the scorer is wrong.
    """
    if not spans:
        raise ClipError("give at least one --at span, like --at 1:32-2:03")
    parsed_spans = [parse_span(span) for span in spans]
    is_url = target.strip().lower().startswith(("http://", "https://"))
    if is_url and not rights_note.strip():
        raise ClipError(
            "a URL needs --rights explaining why this source may be clipped"
        )

    on_status("resolving source")
    hint = language or str(clips_config.setting("language") or "")
    source = ingest.resolve(
        target,
        rights_note.strip() or "local file",
        refresh=refresh,
        **({"language": hint, "on_status": on_status} if is_url else {}),
    )
    work_dir = clips_config.source_dir(source.id)
    language = (
        language or str(clips_config.setting("language") or "") or source.language
    )
    words: list[segments.Word] = []
    words_from = "none"
    if subtitles:
        transcript = _words_for(source, work_dir, language, captions, refresh, on_status)
        words = segments.parse_words(transcript["words"])
        words_from = transcript.get("source", "whisper")

    duration = source.duration_seconds or ingest.probe_duration(source.path)
    rendered: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, (start, end) in enumerate(parsed_spans):
        if duration and start >= duration:
            failures.append(f"span {index + 1} starts after the video ends")
            continue
        if duration:
            end = min(end, duration)
        start, end = snap_span(words, start, end)
        inside = segments.words_in_span(words, start, end)
        text = " ".join(w.text for w in inside)
        title = (titles[index] if titles and index < len(titles) else "").strip()
        if not title:
            title = scoring.fallback_title(
                segments.Window(start, end, text, 0, 0), source.title
            )

        clip_id = store.new_clip_id()
        out_path = os.path.join(clips_config.clips_dir(), f"{clip_id}.mp4")
        on_status(
            f"rendering {index + 1}/{len(parsed_spans)}: {end - start:.0f}s at "
            f"{int(start // 60)}m{int(start % 60):02d}s"
        )
        try:
            # A live snapshot is audio only: fetch the video for this span.
            media, offset = ingest.materialize_span(source, start, end, on_status)
            render_info: dict[str, Any] = {}
            path = render.render_clip(
                media,
                start - offset,
                end - start,
                out_path,
                words=inside,
                subtitles=subtitles,
                layout=layout,
                facecam=facecam,
                title=title,
                title_overlay=title_overlay,
                on_status=on_status,
                render_info=render_info,
            )
        except (render.RenderError, ingest.IngestError) as exc:
            failures.append(f"span {index + 1}: {exc}")
            continue
        rendered.append(
            store.add(
                {
                    "id": clip_id,
                    "title": title,
                    "path": path,
                    "source_id": source.id,
                    "source_path": source.path,
                    "source_duration_seconds": duration,
                    "source_title": source.title,
                    "source_url": source.url,
                    "source_uploader": source.uploader,
                    "rights_note": source.rights_note,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(end - start, 2),
                    "score": None,
                    "scores": {},
                    "transcript": text,
                    "picked_by": "manual",
                    "words_from": words_from,
                    **render_info,
                    "title_overlay": _title_burned(
                        title_overlay, bool(subtitles and inside), title
                    ),
                    "probe": render.probe(path),
                }
            )
        )

    store.log_decision(
        {
            "mode": "manual",
            "source_id": source.id,
            "source_url": source.url,
            "source_title": source.title,
            "chosen": [
                {
                    "start": c["start"],
                    "end": c["end"],
                    "score": 0.0,
                    "title": c["title"],
                }
                for c in rendered
            ],
            "render_failures": failures,
        }
    )
    if failures and not rendered:
        raise ClipError("every span failed:\n  " + "\n  ".join(failures))
    return {
        "source": source.to_dict(),
        "clips": rendered,
        "failures": failures,
        "words_from": words_from,
    }


def _write_sequence_json(path: str, payload: dict[str, Any]) -> None:
    """Write only into the module's source directory, atomically."""
    temporary = path + f".{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)


def _saved_sequence(path: str, identity: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            saved = json.load(file)
        if not isinstance(saved, dict) or saved.get("identity") != identity:
            return None
        spans = saved["spans"]
        previous = 0.0
        if (
            not isinstance(spans, list) or not spans
            or not saved.get("sequence_id") or not saved.get("source_signature")
        ):
            return None
        for raw in spans:
            if not isinstance(raw, list) or len(raw) != 2:
                return None
            start, end = map(float, raw)
            if not all(map(math.isfinite, (start, end))) or abs(start - previous) > 1e-6 or end <= start:
                return None
            previous = end
        if abs(previous - identity["duration"]) > 1e-6:
            return None
        return saved
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _sequence_records(plan: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """Ignore old media versions while still recognizing existing hand cuts."""
    return [
        record for record in store.listing(source_id=source_id)
        if record.get("sequence_id") == plan["sequence_id"]
        or (
            not plan.get("isolated_sequence", False)
            and (
                record.get("source_signature") == plan["source_signature"]
                or (plan.get("accept_legacy", True) and not record.get("sequence_id"))
            )
        )
    ]


def _full_target_lock_path(target: str) -> str:
    """Canonical identity available before a source can be downloaded."""
    target = target.strip()
    if target.lower().startswith(("http://", "https://")):
        identity = "url:" + ingest.source_id_for(target)
    else:
        identity = "local:" + os.path.normcase(os.path.realpath(os.path.expanduser(target)))
    token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return os.path.join(clips_config.clips_dir(), f".full-target-{token}.lock")


def full(
    target: str,
    clip_seconds: float = 60,
    batch_size: int = 0,
    resume: bool = True,
    rights_note: str = "",
    language: str = "",
    captions: str = "",
    layout: str | None = None,
    facecam=None,
    subtitles: bool = True,
    refresh: bool = False,
    title_overlay: str | None = None,
    max_minutes: float | None = None,
    on_status: Callable[[str], None] = _noop,
) -> dict[str, Any]:
    """Cover a whole source, guarding its download and every later render.

    The target lock is available before ingestion: a refresh must not
    replace a source file while another continuation is reading it.
    """
    try:
        with store.file_lock(_full_target_lock_path(target), timeout=0):
            return _full_impl(
                target, clip_seconds=clip_seconds, batch_size=batch_size,
                resume=resume, rights_note=rights_note, language=language,
                captions=captions, layout=layout, facecam=facecam,
                subtitles=subtitles, refresh=refresh, title_overlay=title_overlay,
                max_minutes=max_minutes, on_status=on_status,
            )
    except BlockingIOError as exc:
        raise ClipError("ya hay una continuación en curso para este video; espera a que termine") from exc


def _full_impl(
    target: str,
    clip_seconds: float = 60,
    batch_size: int = 0,
    resume: bool = True,
    rights_note: str = "",
    language: str = "",
    captions: str = "",
    layout: str | None = None,
    facecam=None,
    subtitles: bool = True,
    refresh: bool = False,
    title_overlay: str | None = None,
    max_minutes: float | None = None,
    on_status: Callable[[str], None] = _noop,
) -> dict[str, Any]:
    """Cover an entire source in consecutive parts, resuming missing parts.

    A zero batch size renders every remaining part. A saved plan keeps
    shared boundaries stable when subtitle settings change between batches.
    The source lock lasts through rendering, so two workers cannot create
    the same continuation at once. Nothing here starts an upload.
    """
    try:
        clip_seconds = float(clip_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ClipError("la duración de cada parte debe ser un número entre 5 y 180 segundos") from exc
    if not math.isfinite(clip_seconds) or not 5 <= clip_seconds <= 180:
        raise ClipError("la duración de cada parte debe estar entre 5 y 180 segundos")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 0:
        raise ClipError("el tamaño del lote debe ser un entero mayor o igual a cero")
    is_url = target.strip().lower().startswith(("http://", "https://"))
    if is_url and not rights_note.strip():
        raise ClipError("una URL necesita --rights indicando por qué se puede recortar esta fuente")
    on_status("resolving source for full-video continuation")
    hint = language or str(clips_config.setting("language") or "")
    source = ingest.resolve(
        target,
        rights_note.strip() or "local file",
        refresh=refresh,
        **({"max_minutes": max_minutes, "language": hint, "on_status": on_status} if is_url else {}),
    )
    duration = source.duration_seconds or ingest.probe_duration(source.path)
    try:
        duration = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ClipError("no se pudo determinar la duración completa del video") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ClipError("el video debe tener una duración conocida, finita y mayor que cero")
    source.duration_seconds = duration
    work_dir = clips_config.source_dir(source.id)
    token = hashlib.sha256(source.id.encode("utf-8")).hexdigest()[:24]
    lock_path = os.path.join(clips_config.clips_dir(), f".full-sequence-{token}.lock")
    try:
        with store.file_lock(lock_path, timeout=0):
            media_stat = os.stat(source.path)
            identity = {
                "source_id": source.id,
                "path": os.path.normcase(os.path.abspath(source.path)),
                "size": media_stat.st_size,
                "mtime_ns": media_stat.st_mtime_ns,
                "duration": duration,
                "clip_seconds": clip_seconds,
            }
            plan_path = os.path.join(work_dir, f"sequence-{clip_seconds:.12g}.json")
            plan_existed = os.path.isfile(plan_path)
            plan = _saved_sequence(plan_path, identity) if resume else None
            words: list[segments.Word] = []
            words_from = "none"
            language = hint or source.language
            # A complete saved sequence needs neither transcription nor a
            # second ffmpeg pass just because the user pressed Continue.
            previous_coverage = None
            if plan is not None:
                previous_coverage = continuation.coverage_plan(
                    plan["spans"], _sequence_records(plan, source.id), source.id
                )
            if subtitles and not (previous_coverage and previous_coverage["complete"]):
                try:
                    transcript = _words_for(source, work_dir, language, captions, refresh, on_status)
                    words = segments.parse_words(transcript["words"])
                    words_from = transcript.get("source", "whisper")
                except transcribe.TranscribeError as exc:
                    if not str(exc).startswith("Whisper produced no words"):
                        raise
                    on_status("no speech detected; continuing with consecutive parts without subtitles")
            if plan is None:
                try:
                    spans = continuation.plan_sequence(duration, clip_seconds, words=words)
                except ValueError as exc:
                    raise ClipError(str(exc)) from exc
                source_identity = {key: value for key, value in identity.items() if key != "clip_seconds"}
                signature = hashlib.sha256(
                    json.dumps(source_identity, sort_keys=True).encode("utf-8")
                ).hexdigest()[:20]
                sequence_id = hashlib.sha256(
                    json.dumps({
                        "identity": identity, "spans": spans,
                        # Starting a new series must not count an older,
                        # already complete generation as its own progress.
                        "generation": uuid.uuid4().hex if not resume else "",
                    }, sort_keys=True).encode("utf-8")
                ).hexdigest()[:20]
                plan = {
                    "source_id": source.id, "duration": duration,
                    "clip_seconds": clip_seconds, "spans": spans,
                    "sequence_id": sequence_id, "source_signature": signature,
                    "identity": identity, "accept_legacy": bool(resume and not plan_existed),
                    "isolated_sequence": not resume,
                }
                _write_sequence_json(plan_path, plan)
            _write_sequence_json(os.path.join(work_dir, ingest.MANIFEST_NAME), source.to_dict())
            coverage = continuation.coverage_plan(plan["spans"], _sequence_records(plan, source.id), source.id)
            pending = [part for part in coverage["parts"] if not (resume and part["covered"])]
            if batch_size:
                pending = pending[:batch_size]
            on_status(
                f"full-video sequence: {coverage['completed_parts']}/{coverage['total_parts']} parts ready; "
                f"rendering {len(pending)} remaining parts in this batch"
            )
            if source.live:
                on_status("live source: this sequence covers the saved snapshot only")
            rendered: list[dict[str, Any]] = []
            failures: list[str] = []
            total = coverage["total_parts"]
            for part in pending:
                index, start, end = part["index"], part["start"], part["end"]
                inside = segments.words_in_span(words, start, end)
                text = " ".join(word.text for word in inside)
                suffix = f" · Parte {index}"
                base_title = (source.title or os.path.splitext(os.path.basename(source.path))[0] or "Video").strip()
                title = base_title[:100 - len(suffix)].rstrip() + suffix
                clip_id = store.new_clip_id()
                out_path = os.path.join(clips_config.clips_dir(), f"{clip_id}.mp4")
                on_status(f"rendering part {index}/{total}: {start:.3f}-{end:.3f}s")
                try:
                    media, offset = ingest.materialize_span(source, start, end, on_status)
                    render_info: dict[str, Any] = {}
                    path = render.render_clip(
                        media, start - offset, end - start, out_path,
                        words=inside, subtitles=subtitles, layout=layout, facecam=facecam,
                        title=title, title_overlay=title_overlay,
                        on_status=on_status, render_info=render_info,
                    )
                except (render.RenderError, ingest.IngestError) as exc:
                    failures.append(f"part {index}: {exc}")
                    on_status(f"part {index} failed; continuation stopped so it can be retried")
                    break
                rendered.append(store.add({
                    "id": clip_id, "title": title, "path": path,
                    "source_id": source.id, "source_path": source.path,
                    "source_duration_seconds": duration,
                    "source_title": source.title, "source_url": source.url,
                    "source_uploader": source.uploader, "rights_note": source.rights_note,
                    "start": round(start, 6), "end": round(end, 6),
                    "duration": round(end - start, 6),
                    "score": None, "scores": {}, "transcript": text,
                    "picked_by": "full", "words_from": words_from,
                    "sequence_index": index, "sequence_total": total,
                    "sequence_id": plan["sequence_id"],
                    "source_signature": plan["source_signature"],
                    "clip_seconds": clip_seconds,
                    **render_info,
                    "title_overlay": _title_burned(title_overlay, bool(subtitles and inside), title),
                    "probe": render.probe(path),
                }))
            sequence = continuation.coverage_plan(plan["spans"], _sequence_records(plan, source.id), source.id)
            sequence.update(sequence_id=plan["sequence_id"], clip_seconds=clip_seconds, duration=duration)
            store.log_decision({
                "mode": "full", "source_id": source.id, "source_url": source.url,
                "sequence_id": plan["sequence_id"], "requested": len(pending),
                "chosen": [{"start": row["start"], "end": row["end"], "title": row["title"]} for row in rendered],
                "render_failures": failures,
            })
            if failures and not rendered:
                raise ClipError("no se pudo crear la siguiente parte:\n  " + "\n  ".join(failures))
            return {
                "source": source.to_dict(), "clips": rendered, "failures": failures,
                "words_from": words_from, "sequence": sequence, "resumed": bool(resume),
            }
    except BlockingIOError as exc:
        raise ClipError("ya hay una continuación en curso para este video; espera a que termine") from exc
    except OSError as exc:
        raise ClipError(f"no se pudo guardar la continuación del video: {exc}") from exc


def run(
    target: str,
    count: int | None = None,
    rights_note: str = "",
    language: str = "",
    use_llm: bool = True,
    subtitles: bool = True,
    refresh: bool = False,
    dry_run: bool = False,
    max_minutes: float | None = None,
    layout: str | None = None,
    facecam=None,
    captions: str = "",
    title_overlay: str | None = None,
    on_status: Callable[[str], None] = _noop,
    duration_bounds: tuple[float, float] | None = None,
    editorial_brief: str = "",
) -> dict[str, Any]:
    """Make clips from one URL or local file.

    `rights_note` records on what basis this source may be clipped. It is
    required for a download, because a clip of someone else's video is a
    derivative work and the catalogue should say why it exists.
    """
    count = int(count or clips_config.setting("count"))
    is_url = target.strip().lower().startswith(("http://", "https://"))
    if is_url and not rights_note.strip():
        raise ClipError(
            "a URL needs --rights explaining why this source may be clipped "
            '(for example: "my own channel", "clipping program", "licensed")'
        )

    on_status("resolving source")
    hint = language or str(clips_config.setting("language") or "")
    source = ingest.resolve(
        target,
        rights_note.strip() or "local file",
        refresh=refresh,
        **(
            {"max_minutes": max_minutes, "language": hint, "on_status": on_status}
            if is_url
            else {}
        ),
    )
    on_status(
        f"source: {source.title or os.path.basename(source.path)} "
        f"({source.duration_seconds / 60:.1f} min)"
    )

    # Always the module's own per-source directory, never the folder the
    # source lives in: a local file's folder is the user's, and two videos
    # side by side there would otherwise share one transcript cache.
    work_dir = clips_config.source_dir(source.id)
    language = (
        language or str(clips_config.setting("language") or "") or source.language
    )
    transcript = _words_for(
        source, work_dir, language, captions, refresh, on_status
    )

    profile = trends.profile()
    brief = trends.prompt_brief(profile)
    if not profile.get("ready"):
        on_status("no trend profile yet; using the configured length bounds")

    chosen, candidates, report = plan_clips(
        transcript["words"],
        count,
        trend_brief=brief,
        source_title=source.title,
        language=transcript.get("language") or language,
        use_llm=use_llm,
        profile=profile,
        on_status=on_status,
        **({"duration_bounds": duration_bounds} if duration_bounds is not None else {}),
        **({"editorial_brief": editorial_brief} if editorial_brief else {}),
    )

    words = segments.parse_words(transcript["words"])
    rendered: list[dict[str, Any]] = []
    failures: list[str] = []

    for index, window in enumerate(chosen, start=1):
        title = scoring.fallback_title(window, source.title)
        if dry_run:
            rendered.append(
                {
                    "title": title,
                    "start": round(window.start, 2),
                    "end": round(window.end, 2),
                    "duration": round(window.duration, 2),
                    "score": window.score,
                    "scores": window.scores,
                    "text": window.text,
                }
            )
            continue

        clip_id = store.new_clip_id()
        out_path = os.path.join(clips_config.clips_dir(), f"{clip_id}.mp4")
        on_status(
            f"rendering {index}/{len(chosen)}: {window.duration:.0f}s at "
            f"{int(window.start // 60)}m{int(window.start % 60):02d}s"
        )
        clip_words = segments.words_in_span(words, window.start, window.end)
        try:
            # A live snapshot is audio only: fetch the video for this span.
            media, offset = ingest.materialize_span(
                source, window.start, window.end, on_status
            )
            render_info: dict[str, Any] = {}
            path = render.render_clip(
                media,
                window.start - offset,
                window.duration,
                out_path,
                words=clip_words,
                subtitles=subtitles,
                layout=layout,
                facecam=facecam,
                title=title,
                title_overlay=title_overlay,
                on_status=on_status,
                render_info=render_info,
            )
        except (render.RenderError, ingest.IngestError) as exc:
            failures.append(f"clip {index}: {exc}")
            continue

        record = store.add(
            {
                "id": clip_id,
                "title": title,
                "path": path,
                "source_id": source.id,
                "source_path": source.path,
                "source_duration_seconds": source.duration_seconds,
                "source_title": source.title,
                "source_url": source.url,
                "source_uploader": source.uploader,
                "rights_note": source.rights_note,
                "start": round(window.start, 2),
                "end": round(window.end, 2),
                "duration": round(window.duration, 2),
                "score": window.score,
                "scores": window.scores,
                "transcript": window.text,
                "scored_by": "model+heuristics" if report.get("llm") else "heuristics",
                "picked_by": "auto",
                "words_from": transcript.get("source", "whisper"),
                **render_info,
                "title_overlay": _title_burned(
                    title_overlay, bool(subtitles and clip_words), title
                ),
                "probe": render.probe(path),
            }
        )
        rendered.append(record)

    store.log_decision(
        {
            "source_id": source.id,
            "source_url": source.url,
            "source_title": source.title,
            "dry_run": dry_run,
            "requested": count,
            "candidates": len(candidates),
            "scoring": report,
            "trend_profile_ready": bool(profile.get("ready")),
            "chosen": [
                {
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                    "score": w.score,
                    "scores": w.scores,
                    "title": w.title,
                }
                for w in chosen
            ],
            # The runners-up are the whole point of this log: without them
            # there is no way to ask later whether the scorer chose well.
            "rejected": [
                {
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                    "score": w.score,
                }
                for w in sorted(candidates, key=lambda c: -c.score)[:25]
                if w not in chosen
            ],
            "render_failures": failures,
        }
    )

    if failures and not rendered:
        raise ClipError("every clip failed to render:\n  " + "\n  ".join(failures))

    return {
        "source": source.to_dict(),
        "clips": rendered,
        "candidates": len(candidates),
        "scoring": report,
        "trend_profile_ready": bool(profile.get("ready")),
        "failures": failures,
        "dry_run": dry_run,
    }
