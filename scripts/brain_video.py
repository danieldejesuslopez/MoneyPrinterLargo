"""
Generate a Short whose topic was chosen by MoneyPrinter Brain.

  1. The normal topic generator proposes N candidate topics for the channel.
  2. Brain scores and ranks them (channel-relative predicted performance).
  3. The winning topic goes through the regular Shorts pipeline, with images
     rendered by the selected provider (default: Stability AI).

Upload is off unless --upload is passed.

Usage:
    python scripts/brain_video.py [--account Savirox] [--candidates 6]
                                  [--image-provider stability] [--upload]
                                  [--rank-only]
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
os.chdir(ROOT_DIR)

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass


def _account(selector: str) -> dict:
    with open(os.path.join(ROOT_DIR, ".mp", "youtube.json"), encoding="utf-8") as file:
        accounts = json.load(file)["accounts"]
    if not selector:
        return accounts[0]
    for acc in accounts:
        if selector in (acc["id"], acc["nickname"]):
            return acc
    raise SystemExit(f"account not found: {selector}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--account", default="")
    parser.add_argument("--candidates", type=int, default=6)
    parser.add_argument("--image-provider", default="stability")
    parser.add_argument("--image-mode", default="ai", choices=["ai", "photos"],
                        help="photos = real images (Wikimedia/NASA), no API key needed")
    parser.add_argument("--topic", default="",
                        help="skip topic generation/ranking and use this topic")
    parser.add_argument("--images", default="",
                        help="reuse existing PNGs: a folder (newest N) or a comma list")
    parser.add_argument("--image-count", type=int, default=6)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--rank-only", action="store_true",
                        help="stop after Brain picks the topic")
    args = parser.parse_args()

    os.environ["MP_IMAGE_PROVIDER"] = args.image_provider

    import brain
    from classes.YouTube import YouTube
    from config import get_llm_provider, get_ollama_model
    from llm_provider import select_model, set_llm_provider
    from status import error, info, success, warning

    set_llm_provider(get_llm_provider())
    if get_ollama_model():
        select_model(get_ollama_model())

    acc = _account(args.account)
    profile = acc.get("firefox_profile", "")
    if not os.path.isdir(profile):
        if args.upload:
            error("Subir requiere un perfil de Firefox valido en la cuenta.")
            return 1
        # The browser only starts at upload time; rendering needs no profile.
        import tempfile
        profile = tempfile.mkdtemp(prefix="brain_video_profile_")
    youtube = YouTube(
        acc["id"], acc["nickname"], profile, acc["niche"], acc["language"],
        image_style=acc.get("image_style", ""),
        short_voice=acc.get("short_voice", ""),
        long_voice=acc.get("long_voice", ""),
        hook_profile=acc.get("hook_profile", ""),
        voice_drama=acc.get("voice_drama", False),
    )

    if args.topic:
        return _render(youtube, args, args.topic.strip())

    info(f"\n  Generando {args.candidates} temas candidatos para {acc['nickname']}...")
    topics: list[str] = []
    for _ in range(args.candidates * 2):
        if len(topics) >= args.candidates:
            break
        topic = (youtube.generate_topic() or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
            info(f"    {len(topics)}. {topic}")
    if not topics:
        error("No se pudo generar ningun tema.")
        return 1

    result = brain.rank_topics(acc["id"], topics, top_k=len(topics))
    info(f"\n  Brain {result.get('model_version')} (tier {result.get('tier')}):")
    for row in result["ranked"]:
        info(f"    {row['rank']:>2}. {row['score']:>5.2f}  conf {row['confidence']:.2f}  {row['label']}")
    if result.get("abstained"):
        warning("  Brain se abstiene (confianza baja); se usa igualmente su primer puesto.")
    why = result.get("why") or {}
    for item in why.get("positive", [])[:3]:
        info(f"    + {item['factor']}")
    for item in why.get("negative", [])[:3]:
        info(f"    - {item['factor']}")

    best = result["ranked"][0]["label"]
    success(f"\n  Tema elegido por Brain: {best}")
    return _render(youtube, args, best)


def _reuse_images(youtube, spec: str, count: int) -> None:
    """Make the pipeline use existing PNGs instead of generating new ones."""
    import glob

    from status import info

    if os.path.isdir(spec):
        files = sorted(glob.glob(os.path.join(spec, "*.png")), key=os.path.getmtime)[-count:]
    else:
        files = [f.strip() for f in spec.split(",") if f.strip()]
    missing = [f for f in files if not os.path.isfile(f)]
    if not files or missing:
        raise SystemExit(f"imagenes no encontradas: {missing or spec}")
    info(f"  Reutilizando {len(files)} imagenes existentes:")
    for f in files:
        info(f"    {f}")

    def use_existing(prompts, image_mode="ai"):
        youtube.images = [os.path.abspath(f) for f in files]

    youtube.generate_images_batch = use_existing


def _render(youtube, args, topic: str) -> int:
    from classes.Tts import TTS
    from status import error, success

    if args.rank_only:
        return 0
    if args.images:
        _reuse_images(youtube, args.images, args.image_count)
    video_path = youtube.generate_video(TTS(), custom_topic=topic, image_mode=args.image_mode)
    if not video_path:
        error("La generacion del video se aborto.")
        return 1
    success(f"\n  Video listo: {video_path}")
    if args.upload:
        youtube.upload_video()
        success("  Subido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
