"""Live network viewer for MoneyPrinter Brain.

Serves the real thing, not a diagram of it: the feature schema, the fitted
weights of whatever model is currently champion, the rule table, and the
blend that turns all of it into one score. Everything is read live from
`src/brain`, so training a new model in another terminal changes what the
page shows within a couple of seconds.

    python scripts/brain_viz.py            # your real channels
    python scripts/brain_viz.py --demo     # a trained synthetic channel
    python scripts/brain_viz.py --allow-train

`--demo` builds a throwaway project root with 180 synthetic videos, trains
on them, and serves that. Useful before your own history is big enough to
train anything: it shows what a populated network actually looks like
without touching `.mp/`.

Stdlib only (`http.server`), no new dependencies, binds to localhost.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PAGE_PATH = Path(__file__).resolve().parent / "brain_viz.html"

#: Set by --allow-train; the train endpoint writes a new model version.
ALLOW_TRAIN = False


# -- State ---------------------------------------------------------------


def _feature_catalog() -> list[dict[str, Any]]:
    from brain.features import registry as fr

    return [
        {
            "name": spec.name,
            "group": spec.group,
            "description": spec.description,
            "default": spec.default,
        }
        for spec in fr.INPUT_SPECS
    ]


def _rule_catalog() -> list[dict[str, Any]]:
    from brain.models import heuristic

    return [
        {
            "name": rule.name,
            "inputs": list(rule.inputs),
            "detail": rule.detail,
            "negative_detail": rule.negative_detail,
        }
        for rule in heuristic.RULES
    ]


def _head_summaries(model) -> list[dict[str, Any]]:
    if not model:
        return []
    summaries = []
    for target in model.targets:
        head = model.heads[target]
        calibration = model.calibration.get(target) or {}
        summaries.append({
            "target": target,
            "n_samples": head.n_samples,
            "alpha": head.alpha,
            "bias": head.bias,
            "label_mean": head.label_mean,
            "label_std": head.label_std,
            "r2": calibration.get("r2"),
            "rmse": calibration.get("rmse"),
            "residual_std": calibration.get("residual_std"),
            "confidence": model.confidence(target),
        })
    return summaries


def build_state(account_id: str = "") -> dict[str, Any]:
    """Everything the page needs to draw the network as it is right now."""
    import brain
    from brain import config as brain_config
    from brain.data import collector
    from brain.data import dataset as ds
    from brain.experiments import decisions
    from brain.features import registry as fr
    from brain.inference.predictor import Predictor
    from brain.models import registry as model_registry

    predictor = Predictor(account_id)
    model = predictor.model

    weights: dict[str, dict[str, float]] = {}
    if model:
        for target, head in model.heads.items():
            weights[target] = {
                name: head.weights[index]
                for index, name in enumerate(head.feature_names)
                if index < len(head.weights) and abs(head.weights[index]) > 1e-9
            }

    summary = ds.summarize(ds.load_dataset())
    channels = [
        {
            "account_id": channel.account_id,
            "nickname": channel.nickname or channel.account_id,
            "niche": channel.niche,
            "published": len(channel.published()),
        }
        for channel in collector.load_channels()
    ]

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "brain_version": brain.__version__,
        "mode": brain_config.get_mode(account_id),
        "enabled": brain_config.is_enabled(account_id),
        "tier": predictor.tier,
        "model_version": predictor.model_version,
        "feature_schema": fr.SCHEMA_HASH,
        "features": _feature_catalog(),
        "rules": _rule_catalog(),
        "heads": _head_summaries(model),
        "weights": weights,
        "reward_weights": predictor.reward_weights,
        "hybrid_weights": predictor.hybrid_weights,
        "abstain_confidence": predictor.abstain_below,
        "dataset": summary,
        "registry": model_registry.registry_status(),
        "channels": channels,
        "decisions": len(decisions.load_decisions()),
        "notes": predictor.notes,
        "allow_train": ALLOW_TRAIN,
        "root": brain_config.root_dir(),
    }


def score_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """Push one candidate through the network and report every activation."""
    from brain.data import collector
    from brain.features import registry as fr
    from brain.inference.explainer import explain_prediction
    from brain.inference.predictor import Predictor
    from brain.models import heuristic
    from brain.models.linear import standardize
    from brain.schema import Candidate, ChannelContext, now_utc

    account_id = str(payload.get("account_id") or "")
    context = collector.find_channel(account_id) if account_id else None
    if context is None:
        context = ChannelContext(account_id=account_id, now=now_utc())

    candidate = Candidate(
        topic=str(payload.get("topic") or ""),
        title=str(payload.get("title") or ""),
        hook=str(payload.get("hook") or ""),
        script=str(payload.get("script") or ""),
        kind=str(payload.get("kind") or "short"),
        target_duration_seconds=payload.get("target_duration_seconds") or None,
        retention_score=payload.get("retention_score"),
        hook_score=payload.get("hook_score"),
        first_image_score=payload.get("first_image_score"),
        planned_publish_at=context.now,
    )

    predictor = Predictor(account_id)
    vector = fr.vectorize(candidate, context)
    prediction = predictor.predict(candidate, context)

    missing = set(vector.missing)
    missing_indices = {
        index for index, name in enumerate(vector.names) if name in missing
    }

    model = predictor.model
    activations: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(vector.names):
        entry: dict[str, Any] = {
            "value": vector.values[index],
            "missing": name in missing,
            "standardized": 0.0,
        }
        if model and model.heads:
            head = next(iter(model.heads.values()))
            if index < len(head.means) and name not in missing:
                entry["standardized"] = standardize(
                    vector.values[index], head.means[index], head.stds[index]
                )
        activations[name] = entry

    contributions: dict[str, dict[str, float]] = {}
    if model:
        for target in model.targets:
            contributions[target] = {
                name: value
                for name, value in model.contributions(
                    target, vector.values, missing_indices
                ).items()
                if abs(value) > 1e-9
            }

    _, fired = heuristic.score(vector.as_dict(), missing)
    components = _components_from_notes(prediction)

    return {
        "score": prediction.score,
        "confidence": prediction.confidence,
        "source": prediction.source,
        "tier": prediction.tier,
        "model_version": prediction.model_version,
        "novelty": prediction.novelty,
        "targets": prediction.targets,
        "success_probabilities": prediction.success_probabilities,
        "coverage": vector.coverage,
        "activations": activations,
        "contributions": contributions,
        "rules_fired": [
            {"name": factor.name, "points": factor.contribution,
             "detail": factor.detail}
            for factor in fired
        ],
        "components": components,
        "should_decide": predictor.should_decide(prediction),
        "why": explain_prediction(prediction),
    }


def _components_from_notes(prediction) -> dict[str, float]:
    """Recover the blend components the explainer recorded as a note."""
    components: dict[str, float] = {}
    for note in (prediction.explanation.notes if prediction.explanation else []):
        if not note.startswith("components: "):
            continue
        for part in note[len("components: "):].split(", "):
            bits = part.rsplit(" ", 1)
            if len(bits) == 2:
                try:
                    components[bits[0]] = float(bits[1])
                except ValueError:
                    continue
    return components


def train_now(account_id: str = "") -> dict[str, Any]:
    from brain.training import train as trainer

    result = trainer.train_and_register(account_id=account_id)
    report = result.get("report") or {}
    return {
        "trained": result.get("trained", False),
        "version": result.get("version"),
        "promoted": result.get("promoted", False),
        "reasons": result.get("promotion_reasons", []),
        "reason": report.get("reason"),
        "targets": report.get("targets", []),
        "ranking": (report.get("evaluation") or {}).get("ranking", {}),
    }


# -- Demo root -----------------------------------------------------------

_DEMO_SUBJECTS = [
    "el cometa Halley", "la sonda Voyager", "los agujeros negros",
    "la luna Europa", "el desierto de Atacama", "las auroras boreales",
    "la fosa de las Marianas", "el volcan Krakatoa", "la Antartida",
    "los anillos de Saturno", "la nebulosa de Orion", "el meteorito de Tunguska",
    "la estacion Mir", "las piramides de Guiza", "el mar de Aral",
]
_DEMO_CURIOUS = [
    "Lo que nadie te conto sobre {s}",
    "El secreto de {s} que la NASA oculto",
    "{n} datos imposibles sobre {s}",
    "Por que {s} desconcerto a los cientificos",
]
_DEMO_FLAT = [
    "Todo sobre {s}",
    "{s}: informacion general",
    "Un video sobre {s}",
    "Datos de {s} parte {n}",
]


def install_demo_root(videos: int = 180, seed: int = 20260601) -> str:
    """Build and train a synthetic channel in a temp root, and point Brain at it.

    The planted truth is mild and noisy on purpose: curiosity-gap titles earn
    about 1.8x, everything else is lognormal noise. A demo where the signal
    is obvious would make the network look far more certain than any real
    channel ever is.
    """
    from brain import config as brain_config

    temp_root = Path(tempfile.mkdtemp(prefix="brain_viz_demo_"))
    (temp_root / ".mp").mkdir()
    (temp_root / "models").mkdir()
    brain_config.root_dir = lambda: str(temp_root)  # type: ignore[assignment]

    rng = random.Random(seed)
    now = dt.datetime.now(dt.timezone.utc)
    entries = []
    for index in range(videos):
        published = now - dt.timedelta(days=(videos - index) * 2 + 20)
        subject = _DEMO_SUBJECTS[index % len(_DEMO_SUBJECTS)]
        curious = rng.random() < 0.5
        template = rng.choice(_DEMO_CURIOUS if curious else _DEMO_FLAT)
        title = template.format(s=subject, n=rng.randint(3, 12))
        views = 1200.0 * (1.8 if curious else 1.0) * rng.lognormvariate(0, 0.55)
        retention = (48.0 if curious else 38.0) + rng.gauss(0, 6)

        entry = {
            "title": title,
            "subject": subject,
            "description": "",
            "url": f"https://youtu.be/dem{index:08d}",
            "date": published.strftime("%Y-%m-%d %H:%M:%S"),
            "is_short": True,
            "duration_seconds": 47.0,
            "view_count": int(views),
            "like_count": int(views * rng.uniform(0.02, 0.06)),
            "comment_count": int(views * rng.uniform(0.002, 0.008)),
            "stats_synced_at": (published + dt.timedelta(days=25)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "retention_score": round(rng.uniform(6.0, 9.2), 1),
            "hook_score": round(rng.uniform(5.5, 9.0), 1),
        }
        if rng.random() < 0.7:
            entry["avg_percentage_viewed"] = round(max(5.0, min(95.0, retention)), 1)
        entries.append(entry)

    (temp_root / ".mp" / "youtube.json").write_text(
        json.dumps({"accounts": [{
            "id": "demo-channel",
            "nickname": "Cosmos Demo",
            "niche": "cosmos",
            "language": "es",
            "videos": entries,
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )

    from brain.data import dataset as ds
    from brain.training import train as trainer

    ds.build_dataset()
    trainer.train_and_register(account_id="demo-channel")
    return str(temp_root)


# -- Server --------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "BrainViz"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        """Quiet by default; the page polls twice a second."""
        return

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                body = PAGE_PATH.read_bytes()
            except OSError:
                self._send({"error": f"missing page at {PAGE_PATH}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/state":
            account = self._query("account")
            try:
                self._send(build_state(account))
            except Exception as error:  # a viewer must never take the tool down
                self._send({"error": str(error)}, 500)
            return

        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._send({"error": "invalid json"}, 400)
            return

        try:
            if path == "/api/score":
                self._send(score_candidate(payload))
                return
            if path == "/api/train":
                if not ALLOW_TRAIN:
                    self._send({"error": "training disabled; start with "
                                         "--allow-train"}, 403)
                    return
                self._send(train_now(str(payload.get("account_id") or "")))
                return
        except Exception as error:
            self._send({"error": str(error)}, 500)
            return

        self._send({"error": "not found"}, 404)

    def _query(self, key: str) -> str:
        from urllib.parse import parse_qs, urlparse

        values = parse_qs(urlparse(self.path).query).get(key)
        return values[0] if values else ""


def main(argv: list[str] | None = None) -> int:
    global ALLOW_TRAIN

    parser = argparse.ArgumentParser(
        prog="brain_viz", description="Live network viewer for MoneyPrinter Brain"
    )
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--demo", action="store_true",
        help="serve a trained synthetic channel in a temp root",
    )
    parser.add_argument(
        "--allow-train", action="store_true",
        help="enable the Train button (writes a new model version)",
    )
    parser.add_argument("--open", action="store_true", help="open a browser")
    args = parser.parse_args(argv)

    ALLOW_TRAIN = bool(args.allow_train or args.demo)

    if args.demo:
        print("Building a synthetic channel and training on it...")
        root = install_demo_root()
        print(f"  demo root: {root}")

    url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  Brain network viewer -> {url}")
    print("  Ctrl+C to stop.\n")

    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
