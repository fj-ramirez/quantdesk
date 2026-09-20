"""Rolling factor decomposition (spec 3.2).

PCA on the standardised daily-change matrix over a trailing window, then today's
change vector projected onto those loadings.

TWO THINGS THAT MAKE THIS HONEST
--------------------------------
**Sign anchoring.** An eigenvector is only defined up to sign: -v explains
exactly as much variance as v. Left alone, the sign flips arbitrarily between
consecutive windows and "today was a rates-level day" reverses meaning overnight.
Spec 3.2 calls this out. Each window's loadings are therefore aligned against the
previous window's by the sign of their dot product, and the first window is
anchored to a documented reference series per component.

**Component identity.** The spec does not mention it, but a bigger problem lurks
behind the sign: when two eigenvalues are close, the components themselves swap
order between windows. Aligning signs on swapped components is worse than doing
nothing, because it produces a stable-looking series that is actually two
different factors spliced together. So components are MATCHED to the previous
window by absolute correlation of their loadings, reordering is reported, and a
weak best match is flagged rather than assumed.

**The residual.** Spec 3.2: "The residual is as important as the factors." A
large residual on one asset means today's move in it is not explained by the
common factors, and the tool should say so rather than manufacture a macro
story. Residuals are returned per asset, always, alongside the variance shares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..errors import DataIntegrityError, EmptyFetchError
from ..logging import get_logger
from .panel import ChangePanel

log = get_logger("analytics.factors")

# Components retained. Spec 3.2 expects 3-4 to explain most variance.
N_COMPONENTS = 4

# Reference series used to anchor each component's sign in the FIRST window,
# before there is a previous window to align against. The rule is: if this
# series has a negative loading on the component, flip the whole component.
#
# Order matters only as a preference list -- the first of these present in the
# panel with a non-trivial loading is used. They are chosen so a positive factor
# score means something sayable: rates up, credit wider, dollar stronger.
SIGN_ANCHORS = (
    "ust.10y.nominal",
    "credit.hy.oas",
    "fx.usd.broad",
    "vol.vix",
)

# A component whose best match against the previous window is weaker than this
# has probably not been matched to the same factor at all.
MATCH_WARN_CORRELATION = 0.7

# Below this |loading| a series is not worth naming when labelling a factor.
LABEL_MIN_LOADING = 0.15

# Smallest squared length of a standardised change vector that variance shares
# may be computed from. A day that is flat across every series lands near
# floating-point zero rather than exactly on it, and dividing shares by 1e-34
# yields confident nonsense rather than an error.
MIN_TOTAL_VARIATION = 1e-12


@dataclass
class FactorModel:
    """One window's decomposition."""

    as_of: datetime
    series: list[str]
    loadings: np.ndarray                    # (n_series, n_components)
    explained_variance_ratio: np.ndarray    # (n_components,)
    n_rows: int
    reordered: bool = False
    warnings: list[str] = field(default_factory=list)

    def loading_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            self.loadings,
            index=self.series,
            columns=[f"f{i + 1}" for i in range(self.loadings.shape[1])],
        )

    def label(self, component: int) -> str:
        """A post-hoc description from the loadings, per spec 3.2.

        Deliberately descriptive rather than interpretive: it names the assets
        that load on the factor instead of asserting "this is the rates factor".
        The asset classes are the reader's evidence, not the tool's conclusion.
        """
        col = pd.Series(self.loadings[:, component], index=self.series)
        strong = col[col.abs() >= LABEL_MIN_LOADING]
        if strong.empty:
            return "diffuse (no series loads strongly)"
        top = strong.reindex(strong.abs().sort_values(ascending=False).index)[:4]
        return ", ".join(f"{k}{'+' if v > 0 else '-'}" for k, v in top.items())


@dataclass
class Attribution:
    """Today's change vector decomposed against a fitted model."""

    as_of: datetime
    value_date: object
    scores: np.ndarray                      # factor score per component
    variance_share: np.ndarray              # share of today's move per component
    residual: pd.Series                     # per-asset, in standardised units
    fitted: pd.Series
    actual: pd.Series
    unexplained_share: float

    def dominant_residuals(self, n: int = 5) -> pd.Series:
        return self.residual.reindex(
            self.residual.abs().sort_values(ascending=False).index
        )[:n]


def fit(
    panel: ChangePanel,
    n_components: int = N_COMPONENTS,
    previous: FactorModel | None = None,
) -> FactorModel:
    """Fit PCA to a panel, aligned to `previous` if one is supplied."""
    z = panel.standardized()
    if z.shape[0] <= n_components:
        raise EmptyFetchError(
            f"factors: {z.shape[0]} rows cannot support {n_components} "
            "components. Widen the window or accept fewer factors."
        )
    if z.isna().to_numpy().any():
        raise DataIntegrityError(
            "factors: the standardised panel still contains missing values. "
            "Alignment should have dropped incomplete rows (spec 7)."
        )

    matrix = z.to_numpy()
    # SVD rather than an eigendecomposition of the covariance: numerically
    # better conditioned, and scipy/sklearn are not worth a dependency here.
    _, singular, vt = np.linalg.svd(matrix, full_matrices=False)
    loadings = vt[:n_components].T
    variance = singular**2
    ratio = (variance / variance.sum())[:n_components]

    model = FactorModel(
        as_of=panel.as_of,
        series=list(z.columns),
        loadings=loadings,
        explained_variance_ratio=ratio,
        n_rows=z.shape[0],
    )

    if previous is None:
        _anchor_to_reference(model)
    else:
        _align_to_previous(model, previous)
    return model


def _anchor_to_reference(model: FactorModel) -> None:
    """First-window anchoring: flip any component whose reference series loads
    negative, so a positive score has a stable meaning from the outset."""
    for k in range(model.loadings.shape[1]):
        col = pd.Series(model.loadings[:, k], index=model.series)
        anchor = next(
            (s for s in SIGN_ANCHORS
             if s in col.index and abs(col[s]) >= LABEL_MIN_LOADING),
            None,
        )
        if anchor is None:
            # Nothing in the preference list loads on this component. Fall back
            # to the largest loading, which is deterministic if less meaningful.
            anchor = col.abs().idxmax()
            model.warnings.append(
                f"f{k + 1}: no reference series loads on it; sign anchored to "
                f"{anchor}, whose meaning may shift between windows"
            )
        if col[anchor] < 0:
            model.loadings[:, k] *= -1


def _align_to_previous(model: FactorModel, previous: FactorModel) -> None:
    """Match components to the previous window, then align their signs.

    Matching first is what stops a sign convention from being applied to the
    wrong factor when two eigenvalues swap places.
    """
    shared = [s for s in model.series if s in previous.series]
    if len(shared) < 3:
        model.warnings.append(
            "too few series in common with the previous window to align "
            "factors; signs may flip"
        )
        _anchor_to_reference(model)
        return

    now = model.loading_frame().loc[shared].to_numpy()
    was = previous.loading_frame().loc[shared].to_numpy()
    k = model.loadings.shape[1]

    # Correlation of every new component against every old one.
    corr = np.zeros((k, k))
    for i in range(k):
        for j in range(min(k, was.shape[1])):
            a, b = now[:, i], was[:, j]
            denom = np.linalg.norm(a) * np.linalg.norm(b)
            corr[i, j] = float(a @ b / denom) if denom else 0.0

    assignment: dict[int, int] = {}
    taken: set[int] = set()
    # Greedy on the strongest available match. With four components this is
    # both adequate and easier to reason about than the optimal assignment.
    for i in np.argsort(-np.abs(corr).max(axis=1)):
        order = np.argsort(-np.abs(corr[i]))
        choice = next((j for j in order if j not in taken), None)
        if choice is None:
            continue
        assignment[int(i)] = int(choice)
        taken.add(int(choice))

    reordered = any(i != j for i, j in assignment.items())
    new_loadings = np.zeros_like(model.loadings)
    new_ratio = np.zeros_like(model.explained_variance_ratio)
    for i, j in assignment.items():
        strength = abs(corr[i, j])
        if strength < MATCH_WARN_CORRELATION:
            model.warnings.append(
                f"f{j + 1}: best match to the previous window is only "
                f"{strength:.2f}; this component may not be the same factor as "
                "yesterday, so do not read its score as a continuation"
            )
        sign = 1.0 if corr[i, j] >= 0 else -1.0
        new_loadings[:, j] = model.loadings[:, i] * sign
        new_ratio[j] = model.explained_variance_ratio[i]

    if reordered:
        model.warnings.append(
            "components changed order against the previous window; they have "
            "been matched back by loading correlation rather than by rank"
        )
    model.loadings = new_loadings
    model.explained_variance_ratio = new_ratio
    model.reordered = reordered


def attribute(panel: ChangePanel, model: FactorModel) -> Attribution:
    """Project the most recent row of the panel onto the fitted loadings."""
    z = panel.standardized()
    common = [s for s in model.series if s in z.columns]
    if not common:
        raise DataIntegrityError("factors: model and panel share no series")

    today = z[common].iloc[-1]
    loadings = model.loading_frame().loc[common].to_numpy()
    actual = today.to_numpy()

    scores = actual @ loadings
    fitted = loadings @ scores
    residual = actual - fitted

    total = float(actual @ actual)
    if total <= MIN_TOTAL_VARIATION:
        raise DataIntegrityError(
            f"factors: the standardised change vector for {z.index[-1]} is "
            f"identically zero (squared length {total:.2e}). Every series sat "
            "exactly at its window mean, so there is no move to attribute and "
            "variance shares would divide by nothing."
        )
    share = np.array([(s**2) / total for s in scores])
    unexplained = float(residual @ residual) / total

    return Attribution(
        as_of=model.as_of,
        value_date=z.index[-1],
        scores=scores,
        variance_share=share,
        residual=pd.Series(residual, index=common),
        fitted=pd.Series(fitted, index=common),
        actual=pd.Series(actual, index=common),
        unexplained_share=unexplained,
    )


def rolling_fit(
    panels: list[ChangePanel], n_components: int = N_COMPONENTS
) -> list[FactorModel]:
    """Fit a sequence of windows, each aligned to the one before it.

    This is what the sign-stability test in spec 8 exercises.
    """
    models: list[FactorModel] = []
    previous: FactorModel | None = None
    for p in panels:
        m = fit(p, n_components, previous)
        models.append(m)
        previous = m
    return models
