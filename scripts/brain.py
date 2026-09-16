"""MoneyPrinter Brain command line.

Everything Brain can do without touching the generation pipeline:

    python scripts/brain.py status                 what Brain knows right now
    python scripts/brain.py gaps                   what to start recording
    python scripts/brain.py snapshot               record today's metrics
    python scripts/brain.py dataset                rebuild the training set
    python scripts/brain.py validate               schema + leakage checks
    python scripts/brain.py train                  fit a challenger, maybe promote
    python scripts/brain.py backtest               would Brain have chosen better?
    python scripts/brain.py rank "topic a" "topic b" --account <uuid>
    python scripts/brain.py memory --account <uuid>
    python scripts/brain.py shadow                 has Brain been right so far?
    python scripts/brain.py research               ask Claude how to improve, test it
    python scripts/brain.py models                 versions, champion, challenger
    python scripts/brain.py promote v0002
    python scripts/brain.py rollback

The daily loop is `snapshot` then `dataset`; the weekly one adds `train`.
Every command accepts `--json` for scripted use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import brain  # noqa: E402
from brain.data import collector, dataset as ds, reference, validators  # noqa: E402
from brain.experiments import decisions  # noqa: E402
from brain.memory import channel_memory  # noqa: E402
from brain.models import registry as model_registry  # noqa: E402
from brain.training import backtest as bt, train as trainer  # noqa: E402


def _emit(payload, as_json: bool, lines=None) -> None:
    """Print machine JSON or the human summary."""
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    for line in lines or []:
        print(line)


def cmd_status(args) -> int:
    report = brain.status(args.account)
    data = report["data"]
    predictor = report["predictor"]
    lines = [
        "",
        "  MONEYPRINTER BRAIN",
        f"  version {report['brain_version']}   mode {report['mode']}   "
        f"tier {report['tier']}",
        f"  model: {predictor['model_version']}   "
        f"features: {report['feature_count']} ({report['feature_schema']})",
        "",
        f"  dataset      {data['rows']} rows ({data['mature_rows']} mature) "
        f"across {data['channels']} channel(s)",
        f"               {data.get('own_rows', data['rows'])} tuyas, "
        f"{data.get('reference_rows', 0)} de referencia",
        f"  snapshots    {report['snapshots']['snapshots']} readings on "
        f"{report['snapshots']['videos']} videos",
        f"  decisions    {report['shadow']['decisions']} logged, "
        f"{report['shadow'].get('reconciled', 0)} with outcomes",
        "",
    ]
    labeled = data.get("labeled_per_target") or {}
    if labeled:
        lines.append("  labels per target")
        for target, count in sorted(labeled.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {target:<20} {count}")
        lines.append("")
    for note in report["gaps"]["notes"]:
        lines.append(f"  ! {note}")
    for note in predictor.get("notes") or []:
        lines.append(f"  ! {note}")
    lines.append("")
    _emit(report, args.json, lines)
    return 0


def cmd_gaps(args) -> int:
    report = validators.data_gaps()
    lines = ["", f"  {report['published_videos']} published videos", ""]
    for gap in report["gaps"]:
        lines.append(f"  {gap['field']:<20} {gap['coverage']:>6.0%} covered")
        lines.append(f"    blocks: {gap['blocks']}")
        lines.append(f"    fix:    {gap['remedy']}")
        lines.append("")
    for note in report["notes"]:
        lines.append(f"  ! {note}")
    _emit(report, args.json, lines)
    return 0


def cmd_snapshot(args) -> int:
    result = brain.observe()
    _emit(result, args.json, [
        f"Recorded {result['written']} snapshot(s) "
        f"({result['skipped_unchanged']} unchanged) across {result['videos']} videos."
    ])
    return 0


def cmd_dataset(args) -> int:
    summary = ds.build_dataset()
    lines = [
        f"Wrote {summary['rows']} rows to {summary['path']}",
        f"  mature: {summary['mature_rows']}   "
        f"mean coverage: {summary['mean_coverage']:.0%}",
    ]
    for target, count in sorted(
        (summary.get("labeled_per_target") or {}).items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"  {target:<20} {count} labeled")
    _emit(summary, args.json, lines)
    return 0


def cmd_validate(args) -> int:
    rows = ds.load_dataset()
    report = validators.validate_dataset(rows)
    leakage = validators.leakage_report()
    payload = {"validation": report, "leakage": leakage}
    lines = [
        f"{report['rows']} rows, {report['invalid_rows']} invalid, "
        f"{len(report['duplicate_video_ids'])} duplicate id(s)",
        f"targets ready to train: {', '.join(report['targets_ready']) or 'none'}",
        "leakage: " + (
            "clean" if leakage["clean"]
            else f"PROBLEM - {leakage['post_publish_features_in_input']} "
                 f"{leakage['records_whose_features_moved']}"
        ),
    ]
    _emit(payload, args.json, lines)
    return 0 if leakage["clean"] and not report["invalid_rows"] else 1


def cmd_train(args) -> int:
    result = trainer.train_and_register(
        account_id=args.account, auto_promote=not args.no_promote
    )
    if not result["trained"]:
        reason = result["report"].get("reason", "unknown")
        _emit(result, args.json, [f"Not trained: {reason}"])
        return 0

    report = result["report"]
    evaluation = report.get("evaluation") or {}
    ranking = evaluation.get("ranking") or {}
    lines = [
        f"Trained {result['version']} on {report['mature_rows']} mature rows "
        f"(tier {report.get('tier')})",
        f"  heads: {', '.join(report.get('targets') or []) or 'none'}",
        f"  ranking spearman: {ranking.get('spearman')}   "
        f"ndcg@5: {ranking.get('ndcg@5')}",
        f"  promoted: {result['promoted']}",
    ]
    lines += [f"  - {reason}" for reason in result["promotion_reasons"]]
    _emit(result, args.json, lines)
    return 0


def cmd_backtest(args) -> int:
    if args.predictive:
        result = bt.predictive_backtest(warmup=args.warmup, account_id=args.account)
        if not result.get("ran"):
            _emit(result, args.json, [f"Backtest skipped: {result.get('reason')}"])
            return 0
        lines = [
            "",
            f"  {result['scored']} predicciones de un video, entrenando solo con su pasado",
            "",
            f"  RMSE modelo       {result['model_rmse']:.4f}",
            f"  RMSE modelo nulo  {result['null_rmse']:.4f}   (media historica)",
            f"  mejora RMSE       {result['rmse_gain_pct']:+.1f}%   "
            f"IC95% [{result['ci95'][0]:+.1f}%, {result['ci95'][1]:+.1f}%]",
            f"  mejora MAE        {result['mae_gain_pct']:+.1f}%",
            f"  mas cerca que el nulo en {result['closer_than_null']}/{result['scored']}",
            f"  correlacion       {result['spearman']:+.3f}",
            "",
            f"  RMSE nulo por formato  {result['format_null_rmse']:.4f}   "
            f"(media del mismo formato)",
            f"  mejora vs ese nulo     {result['format_rmse_gain_pct']:+.1f}%   "
            f"IC95% [{result['format_ci95'][0]:+.1f}%, {result['format_ci95'][1]:+.1f}%]",
            "",
            f"  {result['verdict']}",
            f"  {result['format_verdict']}",
            "",
        ]
        _emit(result, args.json, lines)
        return 0

    result = bt.walk_forward(warmup=args.warmup, batch_size=args.batch,
                             account_id=args.account)
    if not result.get("ran"):
        _emit(result, args.json, [f"Backtest skipped: {result.get('reason')}"])
        return 0
    lines = [
        f"{result['step_count']} simulated decisions over {result['rows']} rows",
        f"  mean lift vs batch mean: {result['mean_lift_vs_mean']:+.4f}",
        f"  top-1 accuracy:          {result['top1_accuracy']:.0%}",
        f"  mean rank correlation:   {result['mean_spearman']}",
        "",
        f"  {result['verdict']}",
    ]
    _emit(result, args.json, lines)
    return 0


def cmd_rank(args) -> int:
    result = brain.rank_topics(
        args.account, args.topics, top_k=args.top, log=not args.no_log
    )
    lines = [
        "",
        f"  model {result['model_version']}  tier {result['tier']}",
        "",
    ]
    for row in result["ranked"]:
        lines.append(
            f"  {row['rank']:>2}. {row['score']:>5.2f}  "
            f"(conf {row['confidence']:.2f})  {row['label'][:70]}"
        )
    lines.append("")
    if result.get("abstained"):
        lines.append(
            "  Brain abstains: confidence is below the floor, so the pipeline's "
            "own choice stands."
        )
    why = result.get("why") or {}
    for item in why.get("positive", [])[:4]:
        lines.append(f"  + {item['factor']}")
    for item in why.get("negative", [])[:4]:
        lines.append(f"  - {item['factor']}")
    lines.append("")
    _emit(result, args.json, lines)
    return 0


def cmd_memory(args) -> int:
    if args.account:
        channel = collector.find_channel(args.account)
        if not channel:
            _emit({"error": "unknown account"}, args.json,
                  [f"No channel with id {args.account}"])
            return 1
        profile = channel_memory.build_profile(channel)
        channel_memory.save_profile(profile)
        lines = [
            "",
            f"  {profile['nickname'] or profile['account_id']} - "
            f"{profile['videos']} videos",
            "",
            "  topic clusters",
        ]
        for cluster in profile["topic_clusters"][:8]:
            performance = cluster["performance_vs_channel"]
            lines.append(
                f"    {cluster['label'][:38]:<38} n={cluster['size']:<3} "
                + (f"{performance:.2f}x" if performance else "-")
            )
        lines += ["", channel_memory.briefing(args.account), ""]
        _emit(profile, args.json, lines)
        return 0

    paths = brain.refresh_memory()
    _emit({"written": paths}, args.json,
          [f"Refreshed {len(paths)} channel profile(s)."])
    return 0


def cmd_reference(args) -> int:
    """Manage the channels Brain learns from but never publishes to."""
    if args.remove:
        removed = reference.remove_channel(args.remove)
        _emit({"removed": removed}, args.json,
              [f"{'Quitado' if removed else 'No estaba'}: {args.remove}"])
        return 0 if removed else 1

    if args.add:
        result = reference.import_channel(args.add, limit=args.limit)
        _emit(result, args.json, [
            f"{result['handle']}: {result.get('imported', 0)} videos importados"
            + (f" ({result['failed']} fallidos)" if result.get("failed") else "")
            + (f" - ERROR {result['error']}" if result.get("error") else "")
        ])
        return 0 if result.get("imported") else 1

    rows = reference.summary()
    lines = ["", f"  {len(rows)} canal(es) de referencia", ""]
    for row in rows:
        lines.append(
            f"  {row['handle']:<16} {str(row['nickname'])[:24]:<26} "
            f"subs {row['subscribers']!s:<10} {row['videos']:>4} videos  "
            f"mediana {row['median_views']!s:<9} {row['first']} -> {row['last']}"
        )
    if not rows:
        lines.append("  (ninguno) - anade uno con: brain reference --add @canal")
    lines.append("")
    _emit({"channels": rows}, args.json, lines)
    return 0


def _wrap(text: str, indent: str = "    ", width: int = 88) -> list[str]:
    return textwrap.wrap(str(text or ""), width=width, initial_indent=indent,
                         subsequent_indent=indent) or [indent]


def _verdict_label(verdict: str) -> str:
    return {
        "recommended": "RECOMENDADO",
        "rejected_search": "rechazado en busqueda",
        "rejected_confirm": "rechazado en confirmacion",
        "insufficient_data": "datos insuficientes",
        "error": "error",
    }.get(verdict or "", verdict or "?")


def cmd_research(args) -> int:
    """Brain asks Claude how to improve, then checks every answer itself."""
    from brain.research import ledger as research_ledger, loop

    if args.session:
        from brain.research import session as research_session

        result = research_session.run_session(
            minutes=args.session,
            max_turns=args.max_turns,
            goal=args.goal,
            continue_from=args.continue_from or None,
            progress=(lambda message: None) if args.json else (
                lambda message: print(message, flush=True)
            ),
        )
        report = result.get("report") or {}
        lines = [
            "",
            f"  SESION {result['session']}   {result['minutes_used']} min, "
            f"{result['turns']} turnos, terminada por: {result['ended_by']}",
            "",
            "  Resumen",
        ]
        lines += _wrap(report.get("summary") or "-")
        lines += ["", "  Hallazgos"]
        for item in report.get("key_findings") or []:
            if isinstance(item, dict):
                lines += _wrap(f"[{item.get('confidence', '?')}] {item.get('finding')}")
        lines += ["", "  Experimentos"]
        for exp in result["experiments"] or []:
            lines.append(f"    {exp['changes']} -> {_verdict_label(exp['verdict'])}")
        if not result["experiments"]:
            lines.append("    (ninguno)")
        lines += [
            "",
            "  Transcripcion: " + os.path.join(result["directory"], "transcript.md"),
            "  Informe:       " + os.path.join(result["directory"], "report.md"),
            "",
        ]
        _emit(result, args.json, lines)
        return 0

    if args.history:
        rows = research_ledger.history(limit=200)
        lines = ["", f"  {len(rows)} experimento(s) registrados", ""]
        for row in rows:
            lines.append(
                f"  {row['round']}  {row['name']:<30} {_verdict_label(row['verdict']):<26} "
                f"busqueda {row['search_delta_pp']!s:>6} pp  "
                f"confirmacion {row['confirm_delta_pp']!s:>6} pp  {row['changes']}"
            )
        lines.append("")
        _emit({"history": rows}, args.json, lines)
        return 0

    if args.revert:
        result = loop.revert()
        lines = [f"Perillas eliminadas: {result['removed'] or '(no habia ninguna)'}"]
        lines += [f"  despues: {step}" for step in result["next_steps"]]
        _emit(result, args.json, lines)
        return 0

    if args.apply:
        result = loop.apply(args.apply, force=args.force)
        lines = [
            f"Adoptado: {result['applied']}",
            f"  config.json brain.training ahora: {result['now']}",
        ]
        if result["forced"]:
            lines.append("  (forzado: no estaba recomendado)")
        lines.append("  El siguiente entrenamiento aun tiene que ganarle al campeon:")
        lines += [f"    {step}" for step in result["next_steps"]]
        _emit(result, args.json, lines)
        return 0

    result = loop.run_round(
        max_experiments=args.max_experiments,
        objective=args.objective,
        dry_run=args.dry_run,
        progress=(lambda message: None) if args.json else (
            lambda message: print(message, flush=True)
        ),
    )

    if result.get("dry_run"):
        from brain.config import cache_file

        target = cache_file("research_prompt.txt")
        with open(target, "w", encoding="utf-8") as file:
            file.write(result["prompt"])
        lines = [
            "",
            f"  Prompt guardado en {target} ({len(result['prompt']):,} caracteres).",
            "  No se ha consultado a nadie. Esto es exactamente lo que recibiria Claude.",
            "",
        ]
        _emit({"prompt_path": target, "dossier": result["dossier"]}, args.json, lines)
        return 0

    if not result.get("ran"):
        _emit(result, args.json, [f"Ronda no ejecutada: {result.get('reason')}"])
        return 0

    current = result["current"]
    lines = [
        "",
        f"  RONDA {result['round_id']}   objetivo: {result['objective']}",
        "",
        "  Configuracion actual",
        f"    vs nulo por formato   {current['format_gain_pct']!s:>7}%",
        f"    vs nulo global        {current['global_gain_pct']!s:>7}%",
        f"    correlacion           {current['spearman']!s:>7}",
        "",
        "  Diagnostico de Claude",
    ]
    lines += _wrap(result["diagnosis"] or "(sin diagnostico)")
    lines.append("")

    lines.append("  Experimentos probados")
    if not result["results"]:
        lines.append("    (ninguno)")
    for item in result["results"]:
        comparison = item["comparison"]
        search = comparison.get("search") or {}
        confirm = comparison.get("confirm") or {}
        lines.append(f"    {item['name']}  {item['changes']}")
        lines += _wrap(item.get("hypothesis"), indent="      ")
        if search:
            lines.append(
                f"      busqueda {search.get('delta_pp')!s:>6} pp   "
                f"confirmacion {confirm.get('delta_pp')!s:>6} pp   "
                f"P(mejor) {confirm.get('probability_better')!s:>5}   "
                f"-> {_verdict_label(comparison.get('verdict'))}"
            )
        else:
            lines.append(f"      -> {_verdict_label(comparison.get('verdict'))}: "
                         f"{comparison.get('reason', '')}")
    lines.append("")

    if result["rejected"]:
        lines.append("  Rechazados antes de probar")
        for item in result["rejected"]:
            lines.append(f"    - {item.get('name', '?')}: {item.get('reason')}")
        lines.append("")

    if result["feature_ideas"]:
        lines.append("  Ideas de features (las lee una persona; nunca se ejecutan)")
        for idea in result["feature_ideas"]:
            lines.append(f"    - {idea['name']}")
            lines += _wrap(idea["rationale"], indent="        ")
            if idea.get("data_needed"):
                lines += _wrap("datos: " + idea["data_needed"], indent="        ")
        lines.append("")

    if result["data_priorities"]:
        lines.append("  Prioridades de datos")
        for number, item in enumerate(result["data_priorities"], start=1):
            lines += _wrap(f"{number}. {item}")
        lines.append("")

    used = result["era_experiments_before"] + len(result["results"])
    lines.append(f"  Era de confirmacion: {used}/{result['era_budget']} experimentos usados")
    recommended = [r for r in result["results"]
                   if (r.get("comparison") or {}).get("verdict") == "recommended"]
    for item in recommended:
        lines.append(
            f"  Para adoptar: python scripts/brain.py research --apply "
            f"{result['round_id']}:{item['name']}"
        )
    if not recommended:
        lines.append("  Nada supero las dos eras. No se cambia nada.")
    lines.append("")
    _emit(result, args.json, lines)
    return 0


def cmd_shadow(args) -> int:
    decisions.reconcile(args.account or None)
    report = decisions.shadow_report(args.account or None)
    lines = [
        "",
        f"  decisions logged   {report.get('decisions', 0)}",
        f"  applied by Brain   {report.get('applied', 0)}",
        f"  with outcomes      {report.get('reconciled', 0)}",
    ]
    if report.get("agreement_rate") is not None:
        lines.append(f"  agreed with pipeline {report['agreement_rate']:.0%}")
    if report.get("mean_absolute_error") is not None:
        lines.append(f"  mean abs error     {report['mean_absolute_error']:.2f}")
    lines += ["", f"  {report.get('message', '')}", ""]
    _emit(report, args.json, lines)
    return 0


def cmd_models(args) -> int:
    status = model_registry.registry_status()
    lines = [
        "",
        f"  champion   {status['champion'] or '(none - heuristic mode)'}",
        f"  challenger {status['challenger'] or '(none)'}",
        f"  versions   {', '.join(status['versions']) or '(none)'}",
        "",
    ]
    _emit(status, args.json, lines)
    return 0


def cmd_promote(args) -> int:
    model_registry.promote(args.version)
    _emit({"champion": args.version}, args.json,
          [f"Promoted {args.version} to champion."])
    return 0


def cmd_rollback(args) -> int:
    version = model_registry.rollback()
    _emit({"champion": version}, args.json, [f"Rolled back to {version}."])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain", description="MoneyPrinter Brain - learning layer CLI"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--account", default="", help="channel account uuid")

    # The same two flags on every subcommand, so `brain memory --account X`
    # works as naturally as `brain --account X memory`. SUPPRESS keeps the
    # subparser from overwriting a value the main parser already read.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")
    common.add_argument("--account", default=argparse.SUPPRESS,
                        help="channel account uuid")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def sub(name: str, help_text: str):
        return subparsers.add_parser(name, help=help_text, parents=[common])

    sub("status", "what Brain knows right now")
    sub("gaps", "what the pipeline should start recording")
    sub("snapshot", "record a metrics snapshot")
    sub("dataset", "rebuild the training dataset")
    sub("validate", "schema and leakage checks")

    train_parser = sub("train", "fit a challenger model")
    train_parser.add_argument(
        "--no-promote", action="store_true",
        help="store the challenger without promoting it",
    )

    backtest_parser = sub("backtest", "simulate Brain choosing through history")
    backtest_parser.add_argument("--warmup", type=int, default=bt.DEFAULT_WARMUP)
    backtest_parser.add_argument(
        "--predictive", action="store_true",
        help="score one video at a time against the historical mean")
    backtest_parser.add_argument("--batch", type=int, default=bt.DEFAULT_BATCH)

    rank_parser = sub("rank", "score candidate topics")
    rank_parser.add_argument("topics", nargs="+")
    rank_parser.add_argument("--top", type=int, default=10)
    rank_parser.add_argument("--no-log", action="store_true")

    reference_parser = sub(
        "reference", "channels Brain learns from but never publishes to"
    )
    reference_parser.add_argument("--add", default="", metavar="@handle",
                                  help="import a channel into the reference store")
    reference_parser.add_argument("--remove", default="", metavar="@handle")
    reference_parser.add_argument("--limit", type=int,
                                  default=reference.DEFAULT_LIMIT,
                                  help="most recent videos to import per channel")

    research_parser = sub(
        "research", "ask Claude how Brain could improve, then test the answers"
    )
    research_parser.add_argument("--dry-run", action="store_true",
                                 help="build the briefing and prompt; ask nobody")
    research_parser.add_argument("--history", action="store_true",
                                 help="list every experiment ever tried")
    research_parser.add_argument("--apply", default="", metavar="ROUND:NAME",
                                 help="adopt a recommended experiment's knob values")
    research_parser.add_argument("--force", action="store_true",
                                 help="with --apply: adopt even if not recommended")
    research_parser.add_argument("--revert", action="store_true",
                                 help="remove all adopted knob values")
    research_parser.add_argument("--max-experiments", type=int, default=4)
    research_parser.add_argument("--session", type=int, default=0, metavar="MINUTES",
                                 help="hold a research conversation with Claude")
    research_parser.add_argument("--max-turns", type=int, default=60)
    research_parser.add_argument("--goal", choices=("model", "strategy"), default="model",
                                 help="with --session: what the conversation investigates")
    research_parser.add_argument("--continue-from", default="", metavar="SESSION",
                                 help="with --session: resume an earlier session's "
                                      "Claude conversation, keeping its memory")
    research_parser.add_argument("--objective", choices=("format", "global"),
                                 default="format")

    sub("memory", "rebuild or show a channel profile")
    sub("shadow", "has Brain been right so far?")
    sub("models", "model versions and pointers")

    promote_parser = sub("promote", "make a version champion")
    promote_parser.add_argument("version")

    sub("rollback", "return to the previous champion")
    return parser


COMMANDS = {
    "status": cmd_status,
    "gaps": cmd_gaps,
    "snapshot": cmd_snapshot,
    "dataset": cmd_dataset,
    "validate": cmd_validate,
    "train": cmd_train,
    "backtest": cmd_backtest,
    "rank": cmd_rank,
    "reference": cmd_reference,
    "research": cmd_research,
    "memory": cmd_memory,
    "shadow": cmd_shadow,
    "models": cmd_models,
    "promote": cmd_promote,
    "rollback": cmd_rollback,
}


def _configure_output() -> None:
    """UTF-8 when redirected, the console's own encoding when interactive.

    Advisor text is Spanish. Redirected to a file, Windows would write it in
    cp1252 and every accent reads back as a replacement character; on an
    interactive console, forcing UTF-8 would garble it the other way. So only
    non-terminal output is switched, and either way an unencodable character
    degrades instead of crashing the command.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
