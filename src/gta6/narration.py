"""Narrated gaming clips using MoneyPrinter TTS and the existing clip renderer."""

from __future__ import annotations

import subprocess
from pathlib import Path


def narrate(clip: dict, script: str, settings: dict, on_status=print, facecam=None) -> dict:
    from classes.ScriptVoicePreview import ScriptVoicePreview, ScriptVoicePreviewConfig
    from clips import config, ingest, render, segments, store

    clip_id = store.new_clip_id()
    output = Path(config.clips_dir()) / f"{clip_id}.mp4"
    visual = output.with_name(f"{clip_id}-visual.mp4")
    on_status("GTA 6: sintetizando el comentario con la voz de MoneyPrinter")
    preview = ScriptVoicePreview(
        config=ScriptVoicePreviewConfig(language=settings["language"], short_voice=settings["voice"]),
        output_dir=config.clips_dir(),
    ).synthesize(subject=clip["title"], script=script, preview_id=clip_id)
    duration = float(clip["duration"])
    if preview.duration_seconds > duration + 0.1:
        raise ValueError(f"El comentario dura {preview.duration_seconds:.1f} s y el clip {duration:.1f} s; acorta el comentario o elige un tramo más largo")
    source = ingest.load_manifest(config.source_dir(clip["source_id"]))
    if source is None:
        source_path = clip.get("source_path", "")
        if not source_path or not Path(source_path).is_file():
            raise ValueError("La fuente original ya no está disponible para crear la versión narrada")
        source = ingest.Source(id=clip["source_id"], path=source_path)
    media, offset = ingest.materialize_span(source, float(clip["start"]), float(clip["end"]), on_status)
    words = segments.parse_words(preview.word_timestamps or [])
    # Captions now follow the narrator, never the source speaker whose audio was replaced.
    info = {}
    try:
        render.render_clip(media, float(clip["start"]) - offset, duration, str(visual), words=words,
                           subtitles=bool(words), layout=clip.get("main_layout") or clip.get("layout") or "blur",
                           facecam=facecam, title=clip["title"], title_overlay="always", on_status=on_status, render_info=info)
        command = [render._ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                   "-i", str(visual), "-i", preview.audio_path, "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-t", f"{duration:.3f}",
                   "-movflags", "+faststart", str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise ValueError("No se pudo unir la narración al clip: " + result.stderr[-400:])
        record = {k: v for k, v in clip.items() if k not in ("id", "path", "created_at", "status", "youtube_upload")}
        record.update(id=clip_id, path=str(output), transcript=script, narrated_from_clip=clip["id"],
                      narration_voice=preview.voice_id, narration_audio=preview.audio_path,
                      words_from="narrator", probe=render.probe(str(output)), **info)
        return store.add(record)
    finally:
        if visual.is_file():
            visual.unlink()
