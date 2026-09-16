"""Ridge regression in plain Python — the V0 baseline (spec §87).

Before any neural network, the plan asks a blunt question: do these features
predict anything at all? A regularized linear model answers it honestly, and
has three properties a deeper model does not have at this data scale:

  - it cannot overfit 74 features to 60 videos the way a free-form network
    can, because the L2 penalty holds the weights down;
  - every prediction decomposes exactly into per-feature contributions, so
    `WHY?` is arithmetic rather than a post-hoc attribution method;
  - it needs no dependency at all, so Brain runs anywhere the pipeline does.

Fitted by solving the normal equations `(X'X + aI)w = X'y` with Gaussian
elimination. At 74 features that is a 74x74 solve — microseconds — and the
ridge term guarantees the matrix is invertible even with fewer rows than
columns, which is exactly the regime this project starts in.

Features are standardized before fitting so one column measured in
characters cannot dominate another measured in [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

#: Ridge strength. Deliberately high for a default: with tens of samples the
#: right prior is "almost nothing matters much", and training sweeps it.
DEFAULT_ALPHA = 1.0

#: Standardized values are clipped to this many standard deviations, during
#: fitting *and* prediction.
#:
#: Without it, a feature that barely varied in training becomes a landmine.
#: `channel_days_since_last_upload` is a real example: a channel that posted
#: every two days has a standard deviation near zero for it, so the first
#: candidate scheduled six weeks later lands 300 sigma out and single-
#: handedly decides the prediction. Clipping says the honest thing instead —
#: "far outside anything I have seen" — and caps how much one such feature
#: can move the answer.
STANDARDIZED_CLIP = 4.0


def standardize(value: float, mean: float, std: float) -> float:
    """Center, scale, and clip one feature value."""
    if std <= 0:
        return 0.0
    scaled = (value - mean) / std
    return max(-STANDARDIZED_CLIP, min(STANDARDIZED_CLIP, scaled))


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve `A x = b` by Gaussian elimination with partial pivoting.

    Raises ValueError when the system is singular — with a positive ridge
    term that cannot happen, so a failure here means a caller passed alpha=0
    on collinear data and should hear about it rather than get zeros.
    """
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot_row][column]) < 1e-12:
            raise ValueError("singular matrix")
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]

        pivot = augmented[column][column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / pivot
            if factor == 0.0:
                continue
            for col in range(column, size + 1):
                augmented[row][col] -= factor * augmented[column][col]

    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = augmented[row][size]
        for col in range(row + 1, size):
            total -= augmented[row][col] * solution[col]
        solution[row] = total / augmented[row][row]
    return solution


@dataclass
class RidgeModel:
    """A fitted linear head for one target."""

    target: str = ""
    feature_names: list[str] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    means: list[float] = field(default_factory=list)
    stds: list[float] = field(default_factory=list)
    alpha: float = DEFAULT_ALPHA
    n_samples: int = 0
    label_mean: float = 0.0
    label_std: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)

    # -- fitting --------------------------------------------------------

    @classmethod
    def fit(
        cls,
        features: Sequence[Sequence[float]],
        labels: Sequence[float],
        feature_names: Sequence[str],
        alpha: float = DEFAULT_ALPHA,
        target: str = "",
    ) -> "RidgeModel":
        rows = len(features)
        if rows == 0:
            raise ValueError("cannot fit on an empty dataset")
        columns = len(feature_names)

        means = [0.0] * columns
        stds = [1.0] * columns
        for column in range(columns):
            values = [float(row[column]) for row in features]
            mean = sum(values) / rows
            variance = sum((v - mean) ** 2 for v in values) / rows
            means[column] = mean
            # A constant column carries no information; std=1 leaves its
            # standardized value at exactly 0 so its weight is inert.
            stds[column] = math.sqrt(variance) if variance > 1e-12 else 1.0

        standardized = [
            [standardize(float(row[c]), means[c], stds[c]) for c in range(columns)]
            for row in features
        ]
        label_mean = sum(labels) / rows
        centered = [float(y) - label_mean for y in labels]

        gram = [[0.0] * columns for _ in range(columns)]
        for i in range(columns):
            for j in range(i, columns):
                total = sum(row[i] * row[j] for row in standardized)
                gram[i][j] = total
                gram[j][i] = total
            gram[i][i] += alpha
        moment = [
            sum(standardized[r][c] * centered[r] for r in range(rows))
            for c in range(columns)
        ]

        try:
            weights = solve(gram, moment)
        except ValueError:
            weights = [0.0] * columns

        label_variance = sum(value ** 2 for value in centered) / rows
        return cls(
            target=target,
            feature_names=list(feature_names),
            weights=weights,
            bias=label_mean,
            means=means,
            stds=stds,
            alpha=alpha,
            n_samples=rows,
            label_mean=label_mean,
            label_std=math.sqrt(label_variance),
        )

    # -- prediction -----------------------------------------------------

    def predict(
        self, values: Sequence[float], missing: set[int] | None = None
    ) -> float:
        return self.bias + sum(
            weight * self._standardize(index, values[index], missing)
            for index, weight in enumerate(self.weights)
        )

    def predict_many(self, rows: Sequence[Sequence[float]]) -> list[float]:
        return [self.predict(row) for row in rows]

    def _standardize(
        self, index: int, value: float, missing: set[int] | None = None
    ) -> float:
        """Standardized value, with unknowns imputed to the training mean.

        A feature nobody could measure must contribute *nothing*, and the
        contribution of a standardized value is proportional to its distance
        from the training mean — so an unknown is 0, not the spec's display
        default. Skipping this is how a topic-stage candidate, whose script
        and lab scores do not exist yet, ends up several standard deviations
        below everything the model ever saw and gets an absurd prediction.
        """
        if missing and index in missing:
            return 0.0
        try:
            return standardize(float(value), self.means[index], self.stds[index])
        except (IndexError, TypeError, ValueError):
            return 0.0

    def contributions(
        self, values: Sequence[float], missing: set[int] | None = None
    ) -> dict[str, float]:
        """Per-feature contribution to this prediction, in label units.

        Exact by construction: they sum to `predict(values) - bias`. This is
        what the explainer turns into "+ strong curiosity gap" lines, and
        what makes counterfactuals cheap (spec §45, §46).
        """
        return {
            name: self.weights[index] * self._standardize(index, values[index], missing)
            for index, name in enumerate(self.feature_names)
            if index < len(self.weights)
        }

    def importance(self) -> list[tuple[str, float]]:
        """Features ranked by absolute weight — the model's global view."""
        pairs = [
            (name, self.weights[index])
            for index, name in enumerate(self.feature_names)
            if index < len(self.weights)
        ]
        pairs.sort(key=lambda item: abs(item[1]), reverse=True)
        return pairs

    @classmethod
    def average(cls, models: Sequence["RidgeModel"]) -> "RidgeModel":
        """Average several fits of the same data at different alphas.

        Choosing one regularization strength on a validation split of a dozen
        rows is a coin toss; averaging the best few is the standard way to
        keep most of the benefit without betting on the winner. Because every
        model here was standardized against the *same* training matrix, the
        average of their predictions is exactly the prediction of their
        averaged weights - so this stays one linear model, and per-feature
        contributions remain exact rather than becoming an approximation of
        an ensemble.
        """
        models = [m for m in models if m and m.weights]
        if not models:
            raise ValueError("cannot average an empty set of models")
        if len(models) == 1:
            return models[0]

        width = min(len(m.weights) for m in models)
        count = float(len(models))
        first = models[0]
        return cls(
            target=first.target,
            feature_names=list(first.feature_names),
            weights=[sum(m.weights[i] for m in models) / count for i in range(width)],
            bias=sum(m.bias for m in models) / count,
            means=list(first.means),
            stds=list(first.stds),
            alpha=sum(m.alpha for m in models) / count,
            n_samples=first.n_samples,
            label_mean=first.label_mean,
            label_std=first.label_std,
        )

    # -- persistence ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "ridge",
            "target": self.target,
            "feature_names": list(self.feature_names),
            "weights": [round(w, 8) for w in self.weights],
            "bias": self.bias,
            "means": self.means,
            "stds": self.stds,
            "alpha": self.alpha,
            "n_samples": self.n_samples,
            "label_mean": self.label_mean,
            "label_std": self.label_std,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RidgeModel":
        return cls(
            target=str(data.get("target") or ""),
            feature_names=list(data.get("feature_names") or []),
            weights=[float(w) for w in (data.get("weights") or [])],
            bias=float(data.get("bias") or 0.0),
            means=[float(m) for m in (data.get("means") or [])],
            stds=[float(s) or 1.0 for s in (data.get("stds") or [])],
            alpha=float(data.get("alpha") or DEFAULT_ALPHA),
            n_samples=int(data.get("n_samples") or 0),
            label_mean=float(data.get("label_mean") or 0.0),
            label_std=float(data.get("label_std") or 0.0),
            metrics=dict(data.get("metrics") or {}),
        )
