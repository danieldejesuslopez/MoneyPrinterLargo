#!/usr/bin/env python3
"""GTA 6 workspace CLI; also used by the shared background job runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv=None):
    from gta6 import Workspace

    parser = argparse.ArgumentParser(prog="gta6", description="GTA 6 · fuentes, clips, calendario y aprendizaje")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="mostrar el estado del módulo")
    sub.add_parser("report", help="resumen editorial diario")
    ideas = sub.add_parser("ideas", help="generar ideas con el modelo configurado")
    ideas.add_argument("--count", type=int, default=15)
    ideas.add_argument("--no-llm", action="store_true")
    export = sub.add_parser("export", help="exportar la carpeta editorial como ZIP")
    export.add_argument("--output", default=str(ROOT / ".mp" / "gta6" / "GTA-6.zip"))
    clips = sub.add_parser("clips", help="ejecutar una solicitud de clips creada en el módulo")
    clips.add_argument("--request-id", required=True)
    news = sub.add_parser("news", help="generar un lote narrado creado en el módulo")
    news.add_argument("--request-id", required=True)
    auto = sub.add_parser("autopilot", help="revisar creadores y alimentar el productor de borradores")
    auto.add_argument("--scan-only", action="store_true", help="buscar novedades sin renderizar")
    auto.add_argument("--enable", action="store_true", help="activar las revisiones periódicas del backend")
    auto.add_argument("--disable", action="store_true", help="pausar el proceso automático")
    auto.add_argument("--status", action="store_true", help="mostrar configuración, candidatos y revisiones")
    args = parser.parse_args(argv)
    workspace = Workspace()
    try:
        if args.command == "status":
            state = workspace.snapshot()
            print(json.dumps({"module": "GTA 6", "timezone": state["settings"]["timezone"],
                              **{key: len(state[key]) for key in ("sources", "facts", "ideas", "posts", "metrics")},
                              "buffer_configured": state["buffer_configured"],
                              "automatic_enabled": state["autopilot"]["config"]["enabled"],
                              "automatic_candidates": len(state["autopilot"]["candidates"])}, ensure_ascii=False, indent=2))
        elif args.command == "report":
            print(workspace.daily_report())
        elif args.command == "ideas":
            print(json.dumps(workspace.generate_ideas(args.count, not args.no_llm), ensure_ascii=False, indent=2))
        elif args.command == "export":
            destination = Path(args.output).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(workspace.export())
            print(str(destination))
        elif args.command == "clips":
            from gta6.clipping import run
            result = run(workspace, args.request_id, on_status=lambda line: print(line, flush=True))
            print(f"GTA 6: {len(result['clips'])} clips y borradores listos para revisar.")
            if not result["clips"]:
                print("No se produjeron clips nuevos; revisa el catálogo o los errores del lote.")
        elif args.command == "news":
            from gta6.news import run
            result = run(workspace, args.request_id, on_status=lambda line: print(line, flush=True))
            print(f"GTA 6: {len(result['items'])} noticias y {len(result['text_post_ids'])} publicaciones de texto listas.")
        elif args.command == "autopilot":
            from gta6 import autopilot
            if args.enable and args.disable:
                raise ValueError("Elige activar o pausar, no ambos")
            if args.enable or args.disable:
                autopilot.configure(workspace, {"enabled": args.enable})
            if args.status or args.disable:
                result = autopilot.snapshot(workspace)
            else:
                result = autopilot.run(workspace, produce=not args.scan_only, on_status=lambda line: print(line, flush=True))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            parser.print_help()
        return 0
    except Exception as exc:
        print(f"GTA 6: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def interactive():
    while True:
        print("\nGTA 6\n1. Estado\n2. Informe diario\n3. Generar ideas\n4. Exportar carpeta\n5. Abrir módulo web\n6. Estado automático\n7. Escanear creadores\n8. Activar productor automático\n9. Pausar productor automático\n0. Volver")
        choice = input("Opción: ").strip()
        commands = {"1": ["status"], "2": ["report"], "3": ["ideas"], "4": ["export"],
                    "6": ["autopilot", "--status"], "7": ["autopilot", "--scan-only"],
                    "8": ["autopilot", "--enable"], "9": ["autopilot", "--disable"]}
        if choice == "0":
            return
        if choice == "5":
            import webbrowser
            webbrowser.open("http://127.0.0.1:5173/gta6")
        elif choice in commands:
            main(commands[choice])


if __name__ == "__main__":
    raise SystemExit(main())
