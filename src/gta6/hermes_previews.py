"""Render selected live moments for local review, independently of publication."""
import asyncio
import json
import shutil
import time
from pathlib import Path
import subprocess

from .hermes_live import command, configured, permitted, route_for, captions_ready
from clips.render import crop_to_fill


async def render_preview(manifest, moment):
    start, end = moment["start"], moment["end"]
    if not 0 <= start < end <= 120 or not 7 <= end - start <= 120:
        raise ValueError("Tramo inválido para el clip")
    target = manifest.with_suffix(".mp4")
    partial = manifest.with_suffix(".partial.mp4")
    try:
        await command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-i", manifest.with_suffix(".ts"), "-ss", str(start), "-t", str(end-start),
                       "-map", "0:v:0", "-map", "0:a:0?", "-vf", crop_to_fill(720, 1280),
                       "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", partial], 150)
        frames = await command(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=nb_frames", "-of", "csv=p=0", partial], 15)
        if not frames.isdigit() or int(frames) < 1:
            raise ValueError("FFmpeg terminó sin producir fotogramas")
        if manifest.exists():
            partial.replace(target)
            manifest.with_suffix(".portrait").touch()
    finally:
        partial.unlink(missing_ok=True)


def captioned_preview(manifest, moment, route):
    from clips.store import file_lock
    with file_lock(str(manifest.with_suffix(".caption.lock")), timeout=1800):
        if manifest.with_suffix(".mp4").is_file() and captions_ready(manifest, route, moment):
            return
        return _captioned_preview(manifest, moment, route)


def _captioned_preview(manifest, moment, route):
    """Fill the vertical frame and burn actual speech in the destination language."""
    from clips import config, render, transcribe
    from clips.segments import parse_words
    start, end = moment["start"], moment["end"]
    if not 0 <= start < end <= 120 or not 7 <= end-start <= 120:
        raise ValueError("Tramo inválido")
    language = route["language"]
    work = Path(config.clips_dir()) / "sources" / f"hermes-{manifest.stem}-{start}-{end}-{language}-{route.get('whisper_model', 'small')}"
    work.mkdir(parents=True, exist_ok=True)
    audio = work / "selected.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(manifest.with_suffix(".ts")),
                    "-ss", str(start), "-t", str(end-start), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
                   check=True, capture_output=True, timeout=60)
    transcript = transcribe.transcribe(str(audio), str(work), language=route.get("source_language", ""),
                                      task="translate" if language == "en" else "transcribe",
                                      model_name=route.get("whisper_model", "small"))
    if transcript["language"] != language:
        raise ValueError("La transcripción no corresponde al idioma de destino")
    words = parse_words(transcript["words"])
    # Production can reuse these captions on the full recording timeline.
    def timestamp(seconds):
        millis = max(0, round(seconds * 1000))
        return f"{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02},{millis%1000:03}"
    captions = "\n\n".join(f"{i}\n{timestamp(w.start+start)} --> {timestamp(max(w.end,w.start+0.05)+start)}\n{w.text}"
                            for i, w in enumerate(words, 1)) + "\n"
    manifest.with_suffix(".srt").write_text(captions, encoding="utf-8")
    partial = manifest.with_suffix(".captioned.partial.mp4")
    try:
        render.render_clip(str(manifest.with_suffix(".ts")), start, end-start, str(partial), words=words,
                           subtitles=True, layout="crop", title=moment["title"], title_overlay="always")
        if manifest.exists():
            partial.replace(manifest.with_suffix(".mp4"))
            manifest.with_suffix(".portrait").touch()
            manifest.with_suffix(".caption-language").write_text(language, encoding="utf-8")
            manifest.with_suffix(".caption-model").write_text(route.get("whisper_model", "small"), encoding="utf-8")
            manifest.with_suffix(".caption-layout").write_text("separate-text-v2", encoding="utf-8")
            manifest.with_suffix(".caption-span").write_text(json.dumps([start,end]), encoding="utf-8")
    finally:
        partial.unlink(missing_ok=True)


async def preview_loop(desk):
    retry_after = {}
    while True:
        try:
            state = desk.workspace.store.read()
            _, excluded = configured(state)
            # Upgrade existing review copies before offering newly detected moments.
            manifests = sorted(desk.directory.glob("*.json"),
                               key=lambda p: (p.with_suffix(".mp4").is_file(), p.stat().st_mtime), reverse=True)
            now = time.time()
            retry_after = {key: due for key, due in retry_after.items() if due > now}
            for manifest in manifests:
                if manifest in retry_after:
                    continue
                try:
                    record = json.loads(manifest.read_text(encoding="utf-8"))
                    route = route_for(state, record["url"])
                    if manifest.with_suffix(".mp4").is_file() and manifest.with_suffix(".portrait").is_file() and captions_ready(manifest, route, record.get("moment")):
                        continue
                    if not record.get("moment") or record["url"] in excluded or not permitted(state, record["url"]) or now-record["captured_at"] >= 21600:
                        continue
                    if shutil.disk_usage(desk.directory).free < 3*1024**3:
                        break
                    desk.previewing = manifest.stem
                    if route:
                        await asyncio.to_thread(captioned_preview, manifest, record["moment"], route)
                    else:
                        await render_preview(manifest, record["moment"])
                    desk.log("Corte local listo: " + record["moment"]["title"])
                    break
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    retry_after[manifest] = time.time()+120
                    desk.log(f"Error al cortar {manifest.stem}: {exc}")
                finally:
                    desk.previewing = ""
        except OSError as exc:
            desk.log(f"No se pudo leer la cola de cortes: {exc}")
        await asyncio.sleep(2)
