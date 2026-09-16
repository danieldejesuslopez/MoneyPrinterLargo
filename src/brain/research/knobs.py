"""The closed menu of settings a research round is allowed to change.

This is the safety boundary of the whole research loop. Claude is asked for
ideas, and ideas arrive as text; nothing it writes is ever executed. What it
*can* do is name a knob from this list and a value inside that knob's range.
Anything else — an unknown knob, an out-of-range value, a code snippet — is
rejected before a single model is trained.

Why a menu rather than letting an LLM edit the training code: an unrestricted
"try whatever you think helps" loop run against 43 evaluation videos will
find something that scores well by chance every single round, and report it
as progress. A small, fixed set of honest levers keeps the number of things
tried countable, which is what makes the confirmation step meaningful.

Knobs patch module constants for the duration of a context manager and put
them back afterwards. The code they touch reads those constants at call
time, never as bound default arguments — a bound default silently ignores
the patch, which is how one experiment once reported five identical results.
"""

from __future__ import annotations

import contextlib
import importlib
from dataclasses import dataclass
from typing import Any, Iterator

#: Nesting depth of `applied()`. `configured()` only applies approved
#: overrides at the outermost level, so an experiment's own values are never
#: overwritten by the config when training runs inside it.
_depth = 0


@dataclass(frozen=True)
class Knob:
    name: str
    module: str
    attribute: str
    kind: str                  # "int" | "float" | "groups"
    low: float | None
    high: float | None
    description: str
    needs_rebuild: bool = False

    def _module(self):
        return importlib.import_module(self.module)

    def current(self) -> Any:
        value = getattr(self._module(), self.attribute)
        if self.kind == "groups":
            return excluded_groups(value)
        return value

    def coerce(self, value: Any) -> tuple[bool, Any, str]:
        """Validate one proposed value. Returns (ok, clean_value, error)."""
        if self.kind == "groups":
            return _coerce_groups(value)
        try:
            number = int(value) if self.kind == "int" else float(value)
        except (TypeError, ValueError):
            return False, None, f"{self.name}: expected {self.kind}, got {value!r}"
        if self.kind == "int" and isinstance(value, float) and not value.is_integer():
            return False, None, f"{self.name}: expected an integer, got {value!r}"
        if self.low is not None and number < self.low:
            return False, None, f"{self.name}: {number} is below the minimum {self.low}"
        if self.high is not None and number > self.high:
            return False, None, f"{self.name}: {number} is above the maximum {self.high}"
        return True, number, ""

    def to_attribute(self, value: Any) -> Any:
        if self.kind == "groups":
            return features_in_groups(value)
        return value

    def describe(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": self.name,
            "current": self.current(),
            "type": self.kind,
            "description": self.description,
        }
        if self.kind == "groups":
            entry["allowed"] = all_groups()
        else:
            entry["range"] = [self.low, self.high]
        if self.needs_rebuild:
            entry["note"] = "changes the label, so the dataset is rebuilt"
        return entry


KNOBS: tuple[Knob, ...] = (
    Knob(
        "peer_window", "brain.data.normalizer", "PEER_WINDOW", "int", 4, 60,
        "How many prior videos define 'normal' when a label is normalized. "
        "Smaller is more local and removes slow channel drift from the label; "
        "larger is a steadier baseline but mixes in older conditions.",
        needs_rebuild=True,
    ),
    Knob(
        "label_winsor", "brain.training.train", "LABEL_WINSOR", "float", 0.0, 0.2,
        "Share of each tail of the training labels pulled in before fitting. "
        "Evaluation labels are never touched.",
    ),
    Knob(
        "alpha_blend", "brain.training.train", "ALPHA_BLEND", "int", 1, 6,
        "How many of the best ridge regularization strengths are averaged.",
    ),
    Knob(
        "baseline_tolerance_k", "brain.training.train", "BASELINE_TOLERANCE_K",
        "float", 0.0, 1.5,
        "Slack for the 'must beat the null model' gate, as k/sqrt(validation rows). "
        "0 is the strict rule.",
    ),
    Knob(
        "validation_fraction", "brain.training.train", "VALIDATION_FRACTION",
        "float", 0.1, 0.35,
        "Share of the user's own most recent videos held out to choose alpha.",
    ),
    Knob(
        "exclude_feature_groups", "brain.data.dataset", "EXCLUDED_FEATURES",
        "groups", None, None,
        "Feature groups switched off entirely (their weights become exactly zero).",
    ),
)

KNOBS_BY_NAME: dict[str, Knob] = {knob.name: knob for knob in KNOBS}

#: Most groups one experiment may switch off. Excluding nearly everything is
#: not an experiment about features, it is a way of discovering that a model
#: with no inputs predicts the mean.
MAX_EXCLUDED_GROUPS = 4


# -- feature groups -------------------------------------------------------


def all_groups() -> list[str]:
    from ..features import registry as fr

    return sorted({spec.group for spec in fr.INPUT_SPECS})


def features_in_groups(groups: Any) -> frozenset[str]:
    from ..features import registry as fr

    wanted = set(groups or ())
    return frozenset(spec.name for spec in fr.INPUT_SPECS if spec.group in wanted)


def excluded_groups(excluded_features: frozenset[str]) -> list[str]:
    """Groups whose every feature is currently excluded."""
    from ..features import registry as fr

    by_group: dict[str, set[str]] = {}
    for spec in fr.INPUT_SPECS:
        by_group.setdefault(spec.group, set()).add(spec.name)
    return sorted(
        group for group, names in by_group.items()
        if names and names <= set(excluded_features or ())
    )


def _coerce_groups(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return False, None, f"exclude_feature_groups: expected a list, got {value!r}"
    known = set(all_groups())
    cleaned = sorted({str(item).strip() for item in value if str(item).strip()})
    unknown = [group for group in cleaned if group not in known]
    if unknown:
        return False, None, (
            f"exclude_feature_groups: unknown group(s) {unknown}; "
            f"known groups are {sorted(known)}"
        )
    if len(cleaned) > MAX_EXCLUDED_GROUPS:
        return False, None, (
            f"exclude_feature_groups: at most {MAX_EXCLUDED_GROUPS} groups "
            f"may be excluded, got {len(cleaned)}"
        )
    return True, cleaned, ""


# -- validation and application -------------------------------------------


def validate(changes: Any) -> tuple[dict[str, Any], list[str]]:
    """Split a proposed change set into (clean_changes, errors)."""
    if not isinstance(changes, dict):
        return {}, [f"changes must be an object, got {type(changes).__name__}"]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for name, value in changes.items():
        knob = KNOBS_BY_NAME.get(str(name))
        if knob is None:
            errors.append(
                f"unknown knob {name!r}; allowed knobs are {sorted(KNOBS_BY_NAME)}"
            )
            continue
        ok, cleaned, error = knob.coerce(value)
        if ok:
            clean[knob.name] = cleaned
        else:
            errors.append(error)
    return clean, errors


def needs_rebuild(changes: dict[str, Any]) -> bool:
    return any(
        KNOBS_BY_NAME[name].needs_rebuild for name in changes if name in KNOBS_BY_NAME
    )


def snapshot() -> dict[str, Any]:
    """Current value of every knob, as a research round would see it."""
    return {knob.name: knob.current() for knob in KNOBS}


@contextlib.contextmanager
def applied(changes: dict[str, Any]) -> Iterator[None]:
    """Set knob values for the duration of the block, then restore them.

    Invalid changes raise before anything is touched: a half-applied
    experiment would be measured as something it is not.
    """
    global _depth

    clean, errors = validate(changes or {})
    if errors:
        raise ValueError("; ".join(errors))

    saved: list[tuple[Any, str, Any]] = []
    entered = False
    try:
        for name, value in clean.items():
            knob = KNOBS_BY_NAME[name]
            module = knob._module()
            saved.append((module, knob.attribute, getattr(module, knob.attribute)))
            setattr(module, knob.attribute, knob.to_attribute(value))
        _depth += 1
        entered = True
        yield
    finally:
        if entered:
            _depth -= 1
        for module, attribute, original in reversed(saved):
            setattr(module, attribute, original)


def approved_overrides() -> tuple[dict[str, Any], list[str]]:
    """Knob values the user adopted, from `config.json` -> brain.training."""
    from ..config import brain_config

    section = brain_config().get("training")
    if not isinstance(section, dict) or not section:
        return {}, []
    return validate(section)


@contextlib.contextmanager
def configured() -> Iterator[None]:
    """Apply approved overrides - unless an experiment is already in charge.

    Training and dataset building both enter this. Inside a research
    experiment `_depth` is already positive, so the experiment's own values
    stand; everywhere else, whatever the user approved takes effect.
    """
    if _depth > 0:
        yield
        return
    overrides, _ = approved_overrides()
    if not overrides:
        yield
        return
    with applied(overrides):
        yield
