import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .score import write_waltz


MODULE = Path(__file__).resolve().parent
DEFAULT_FILM = MODULE / "films" / "jardin.json"


def assemble(film, visual, voices, destination, work):
    duration = film["duration"]
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(visual)]
    filters = []
    tracks = []
    for index, ((start, _), voice) in enumerate(zip(film["cues"], voices, strict=True)):
        end = film["cues"][index + 1][0] if index + 1 < len(voices) else duration
        seconds = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1", str(voice),
        ]))
        # Keep the entire last word inside the shot, with breathing room.
        tempo = max(1, seconds / ((end - start) * .96))
        args.extend(["-i", str(voice)])
        label = f"v{index}"
        filters.append(f"[{index+1}:a]atempo={tempo:.6f},adelay={round(start*1000)}:all=1[{label}]")
        tracks.append(f"[{label}]")
    if film.get("score") == "waltz":
        music = work / "music.wav"
        write_waltz(music, duration)
        args.extend(["-i", str(music)])
        tracks.append(f"[{len(voices)+1}:a]")
    if tracks:
        filters.append("".join(tracks) + f"amix=inputs={len(tracks)}:normalize=0,alimiter=limit=0.94:level=0,apad[mix]")
        args.extend(["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[mix]", "-c:a", "aac", "-b:a", "192k"])
    else:
        args.extend(["-map", "0:v", "-an"])
    subprocess.run(args + ["-t", str(duration), "-c:v", "copy", "-movflags", "+faststart", str(destination)], check=True)


def produce(film_path: Path, output: Path):
    from classes.Tts import TTS

    film_path = film_path.resolve()
    film = json.loads(film_path.read_text(encoding="utf-8"))
    starts = [start for start, _ in film["cues"]]
    if not all(0 <= start < film["duration"] for start in starts) or starts != sorted(set(starts)):
        raise ValueError("Los tiempos de voz deben ser crecientes y estar dentro del video.")
    for binary in ["node", "ffmpeg", "ffprobe"]:
        if not shutil.which(binary):
            raise RuntimeError(f"Falta {binary} en PATH")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        work = Path(directory)
        visual = work / "visual.mp4"
        subprocess.run(["node", str(MODULE / "render.mjs"), str(film_path), str(visual)], check=True)
        voices = []
        if film["cues"]:
            tts = TTS()
            for index, (_, line) in enumerate(film["cues"]):
                voice = work / f"voice-{index}.wav"
                tts.synthesize(line, str(voice), voice_id=film.get("voice"))
                voices.append(voice)
        result = work / "final.mp4"
        assemble(film, visual, voices, result, work)
        result.replace(output)
    return output.resolve()


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Renderizar un corto de Three.js con narración")
    parser.add_argument("--film", type=Path, default=DEFAULT_FILM)
    parser.add_argument("--output", type=Path, default=MODULE.parents[1] / "output/three_jardin/el-jardin-de-laton.mp4")
    args = parser.parse_args(argv)
    print(produce(args.film, args.output))
