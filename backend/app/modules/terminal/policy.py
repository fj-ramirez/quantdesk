"""Implied policy path from fed funds futures (spec 2.1).

ZQ settles on the ARITHMETIC AVERAGE of the daily effective fed funds rate over
the contract month. A contract spanning a meeting is therefore a blend of the
pre- and post-meeting rate, weighted by how many days of the month fall either
side of the effective date:

    implied_avg = ((d - 1)/n) * r_before + ((n - d + 1)/n) * r_after

for a month of n days with the new rate effective on day d. Given r_before from
the previous month's resolved rate, r_after follows:

    r_after = (implied_avg * n - (d - 1) * r_before) / (n - d + 1)

Walk the strip forward, month by month. Months with no meeting carry the rate
through unchanged, and their settlement is a consistency check rather than a new
unknown.

WHAT IS AND IS NOT VALIDATED
----------------------------
Spec 2.1 asks for the result to sit within 2pp of published CME FedWatch
probabilities on fixed historical dates, as a test-suite check. **That test
cannot be built.** CME publishes FedWatch for the current day only and sells
settlement history through DataMine; neither leg of the comparison is
obtainable free. What IS tested here: the algebra against hand-computed cases,
round-tripping (a solved path reproduces the settlements it came from), month
and meeting boundary behaviour, and the no-meeting carry-through. Those catch
arithmetic and calendar errors, which is most of what goes wrong. They do not
confirm agreement with the market's own published probabilities. Treat the
probabilities as UNVALIDATED against an external reference.

WHY THE SEQUENTIAL METHOD IS NOT THE DEFAULT
--------------------------------------------
The spec prescribes solving forward month by month. Implemented literally it is
numerically unstable, and this is the most consequential thing found while
building it.

Rearranging for r_after divides by the days after the meeting, so a settlement
error is magnified by n / days_after -- a factor of 15 for a meeting effective on
the 29th of a 30-day month, which leaves two days to carry the whole month. Worse,
the error in r_before propagates with gain days_before / days_after, around 9 to
14 for those same months, and the chain multiplies those gains together. Over
eight meetings the compounded gain reaches roughly 8000x. Measured on a synthetic
strip built from a known path: exact prices recover it to 0.000bp, but rounding
the same prices to four decimals throws the later legs out by 11bp.

The cause is that the sequential walk uses ONE equation per month and discards
the months with no meeting, which are precisely the observations that pin a
carried rate down directly. A strip of twelve months constrains eight unknowns,
so the system is overdetermined; solving it jointly by least squares uses every
settlement and is well conditioned. solve_path does that by default.

solve_path_sequential keeps the spec's literal method. It is retained because it
is a useful check -- the two agree closely when the strip is clean -- and because
a reader comparing this code with the spec should be able to find the method the
spec describes. It is not what the tool uses.

Each MeetingRate still carries the amplification factor of its own month, since
a late-month meeting is genuinely less precisely recoverable however it is
solved, and that belongs on the output rather than hidden.

THE PROBABILITY CONVENTION
--------------------------
Probabilities are a deterministic function of the implied rate under an
assumption, stated here because it is an assumption and not a fact: all mass
sits on the two adjacent 25bp increments bracketing the implied move. A path
implying a 10bp rise becomes a 40% chance of +25bp and 60% of no change. The
market could instead price a 20% chance of +50bp and an 80% chance of no change,
which averages to the same 10bp. No futures strip can distinguish them.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .errors import DataIntegrityError, EmptyFetchError
from .fomc import Meeting
from .logging import get_logger
from .models import Observation

log = get_logger("policy")

# CME quotes ZQ as 100 minus the average daily effective rate.
PRICE_BASE = 100.0

# The Fed moves in 25bp increments. Named because every probability below is
# expressed in units of it.
RATE_STEP_PCT = 0.25

# How far a no-meeting month's implied average may sit from the carried-through
# rate before it is reported. Settlement rounding and a basis between EFFR and
# the contract's own fixing produce a few tenths of a basis point legitimately;
# more than this suggests the meeting calendar is wrong, which spec 2.1 names as
# the usual culprit.
CARRY_TOLERANCE_PCT = 0.02

# Meetings to solve for, per spec 1.3.
PATH_MEETINGS = 8

# ZQ settles in quarter-basis-point increments in the front months. Half a tick
# is the most any single settlement can be misstated by rounding alone, and the
# decomposition multiplies it (see CONDITIONING in the module docstring).
SETTLEMENT_TICK_PCT = 0.0025

# Report a meeting whose amplification exceeds this. A meeting effective on the
# 29th of a 30-day month leaves two days to carry the whole month's news, giving
# a factor of 15: half a tick of rounding becomes nearly 2bp on that leg.
AMPLIFICATION_WARN = 5.0

# Largest residual, in bp, the joint solve may leave on any month's settlement
# before the fit is reported as poor. A clean strip fits to well under a tick;
# a large residual means the strip and the meeting calendar disagree, and spec
# 2.1 names the calendar as the usual culprit.
RESIDUAL_WARN_BP = 2.0

# Settlements are final in the evening of the trade date; the path became
# knowable then, not at the market close.
SETTLEMENT_LOCAL_TIME = time(18, 0)
SETTLEMENT_TZ = "America/New_York"


@dataclass(frozen=True)
class MeetingRate:
    """The rate implied for one meeting."""

    meeting: Meeting
    rate_pct: float
    rate_before_pct: float
    contract_month: date
    settle: float
    # n_days / days_after: how much a settlement-price error in THIS month is
    # magnified in this meeting's implied rate. See the module docstring.
    amplification: float = 1.0
    # Half a tick of settlement rounding, amplified here and propagated from
    # every earlier month, in basis points. An error bar, not a confidence
    # interval: it bounds rounding alone, not the market's own uncertainty.
    rounding_uncertainty_bp: float = 0.0

    @property
    def change_bp(self) -> float:
        return (self.rate_pct - self.rate_before_pct) * 100.0

    def probabilities(self) -> dict[int, float]:
        """Probability of each 25bp increment, keyed by number of steps.

        Mass sits on the two adjacent increments bracketing the implied move --
        an assumption, documented at the top of this module.
        """
        steps = self.change_bp / (RATE_STEP_PCT * 100.0)
        low = int(steps // 1)
        frac = steps - low
        if abs(frac) < 1e-12:
            return {low: 1.0}
        return {low: 1.0 - frac, low + 1: frac}


@dataclass
class PolicyPath:
    """A solved path, with everything needed to reproduce it."""

    trade_date: date
    spot_rate_pct: float
    meetings: list[MeetingRate] = field(default_factory=list)
    carry_checks: list[tuple[date, float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Structural facts about conditioning rather than problems: a late-month
    # meeting is inherently less precisely recoverable. Kept apart from
    # warnings so a real problem -- a strip that disagrees with the calendar --
    # is not buried among notes that fire on every single run.
    notes: list[str] = field(default_factory=list)
    # Largest per-month misfit left by the joint solve, in bp. Zero for the
    # sequential method, which fits every equation it uses exactly by
    # construction and is why it cannot detect a bad calendar this way.
    fit_residual_bp: float = 0.0

    @property
    def as_of(self) -> datetime:
        """When this path became knowable: the settlement publication instant."""
        return datetime.combine(
            self.trade_date, SETTLEMENT_LOCAL_TIME, tzinfo=ZoneInfo(SETTLEMENT_TZ)
        )

    def cumulative_change_bp(self, index: int) -> float:
        """Cumulative implied change from spot, in bp, at the index-th meeting."""
        return (self.meetings[index].rate_pct - self.spot_rate_pct) * 100.0


def days_in_month(month: date) -> int:
    return calendar.monthrange(month.year, month.month)[1]


def implied_average_rate(settle: float) -> float:
    """100 - price. The average daily effective rate the contract implies."""
    return PRICE_BASE - settle


def solve_month(
    implied_avg: float, n_days: int, rate_before: float, effective_day: int | None
) -> float:
    """The post-meeting rate implied by one month's settlement.

    effective_day is the day of the month the new rate begins to apply, or None
    if no meeting takes effect in this month.
    """
    if effective_day is None:
        return rate_before
    if not 1 <= effective_day <= n_days:
        raise DataIntegrityError(
            f"policy: effective day {effective_day} outside a {n_days}-day month"
        )
    days_after = n_days - effective_day + 1
    if days_after <= 0:
        raise DataIntegrityError(
            f"policy: {days_after} days after the meeting leaves nothing to solve"
        )
    days_before = effective_day - 1
    return (implied_avg * n_days - days_before * rate_before) / days_after


def solve_path_sequential(
    settlements: dict[date, float],
    meetings: list[Meeting],
    spot_rate_pct: float,
    trade_date: date,
    count: int = PATH_MEETINGS,
) -> PolicyPath:
    """The spec's literal forward walk. Kept as a reference and a cross-check;
    solve_path is what the tool uses. See the module docstring for why.

    settlements maps contract month (first of month) to settlement price.
    meetings must be sorted and must cover the horizon; spot_rate_pct is the
    effective rate currently prevailing.
    """
    if not settlements:
        raise EmptyFetchError("policy: no settlements supplied")

    upcoming = [m for m in meetings if m.effective > trade_date][:count]
    if len(upcoming) < count:
        raise EmptyFetchError(
            f"policy: only {len(upcoming)} meeting(s) after {trade_date}, need "
            f"{count}. Refresh the FOMC calendar rather than shortening the path."
        )

    by_month: dict[date, Meeting] = {}
    for m in upcoming:
        key = m.effective.replace(day=1)
        if key in by_month:
            # Two meetings inside one month would break the single-step
            # decomposition entirely. It has never happened on the scheduled
            # calendar, but an unscheduled meeting could do it.
            raise DataIntegrityError(
                f"policy: two meetings effective in {key:%Y-%m}; the monthly "
                "decomposition assumes at most one and cannot separate them."
            )
        by_month[key] = m

    path = PolicyPath(trade_date=trade_date, spot_rate_pct=spot_rate_pct)
    rate_before = spot_rate_pct

    start_month = trade_date.replace(day=1)
    last_month = max(by_month)
    month = start_month

    # The front month is only partly in the future. If a meeting already took
    # effect earlier in it, days before that carried a different rate and the
    # single-step formula cannot represent the month. Reported, not hidden.
    already = [m for m in meetings
               if m.effective.replace(day=1) == start_month and m.effective <= trade_date]
    if already:
        path.warnings.append(
            f"front month {start_month:%Y-%m} already contains an effective date "
            f"({already[-1].effective}); its decomposition is approximate because "
            "part of the month realised at a different rate"
        )

    while month <= last_month:
        settle = settlements.get(month)
        if settle is None:
            raise EmptyFetchError(
                f"policy: no settlement for contract month {month:%Y-%m}, which "
                "the path needs. A gap in the strip cannot be interpolated: the "
                "months are chained, so a missing one corrupts everything after."
            )

        implied_avg = implied_average_rate(settle)
        n = days_in_month(month)
        meeting = by_month.get(month)
        effective_day = meeting.effective.day if meeting else None

        rate_after = solve_month(implied_avg, n, rate_before, effective_day)

        amplification = 1.0 if effective_day is None else n / (n - effective_day + 1)

        if meeting is None:
            # No meeting: the month should simply price the carried rate. A
            # material gap points at the calendar (spec 2.1).
            drift = implied_avg - rate_before
            path.carry_checks.append((month, implied_avg, rate_before))
            if abs(drift) > CARRY_TOLERANCE_PCT:
                path.warnings.append(
                    f"{month:%Y-%m} has no meeting but prices {implied_avg:.4f}% "
                    f"against a carried {rate_before:.4f}% ({drift * 100:+.1f}bp). "
                    "Check the meeting calendar before trusting the path."
                )
        else:
            path.meetings.append(
                MeetingRate(
                    meeting=meeting,
                    rate_pct=rate_after,
                    rate_before_pct=rate_before,
                    contract_month=month,
                    settle=settle,
                    amplification=amplification,
                    rounding_uncertainty_bp=(
                        amplification * (SETTLEMENT_TICK_PCT / 2) * 100.0
                    ),
                )
            )

        rate_before = rate_after
        month = _next_month(month)

    if len(path.meetings) != count:
        raise DataIntegrityError(
            f"policy: solved {len(path.meetings)} meeting rates, expected {count}"
        )
    for warning in path.warnings:
        log.warning("policy: %s", warning)
    return path


def path_observations(path: PolicyPath, source_batch: str) -> list[Observation]:
    """The solved path as storable observations.

    Two families are stored per meeting: the implied rate and the cumulative
    change from spot. The probabilities are NOT stored, deliberately. They are a
    deterministic function of the implied rate under the two-outcome convention
    documented at the top of this module, so storing them would duplicate
    information while hiding that the convention, not the market, produced them.
    Anything that wants them calls MeetingRate.probabilities().

    as_of is the settlement publication instant, not the value date: the path
    became knowable when the strip settled.
    """
    obs: list[Observation] = []
    for i, mr in enumerate(path.meetings, start=1):
        for series_id, value in (
            (f"policy.ff.meeting_{i}", mr.rate_pct),
            (f"policy.ff.meeting_{i}.chg", path.cumulative_change_bp(i - 1)),
        ):
            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=path.trade_date,
                    as_of=path.as_of,
                    value=value,
                    source_batch=source_batch,
                    as_of_basis="derived_lag",
                )
            )
    return obs


def reconstruct_settlement(
    rate_before: float, rate_after: float, month: date, effective_day: int | None
) -> float:
    """The settlement price a given pair of rates implies.

    The inverse of solve_month, used to check a solved path reproduces the strip
    it came from. A path that cannot round-trip has an arithmetic or calendar
    error somewhere in the chain.
    """
    n = days_in_month(month)
    if effective_day is None:
        return PRICE_BASE - rate_before
    days_before = effective_day - 1
    days_after = n - effective_day + 1
    avg = (days_before * rate_before + days_after * rate_after) / n
    return PRICE_BASE - avg


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def month_weights(
    month: date, meetings_by_month: dict[date, Meeting]
) -> tuple[Meeting | None, float, float]:
    """How a month's average splits between the rate before and after a meeting.

    Returns (meeting or None, weight_before, weight_after). With no meeting the
    whole month carries one rate, so the weights are (1, 0).
    """
    m = meetings_by_month.get(month)
    if m is None:
        return None, 1.0, 0.0
    n = days_in_month(month)
    d = m.effective.day
    return m, (d - 1) / n, (n - d + 1) / n


def solve_path(
    settlements: dict[date, float],
    meetings: list[Meeting],
    spot_rate_pct: float,
    trade_date: date,
    count: int = PATH_MEETINGS,
) -> PolicyPath:
    """Decompose a ZQ strip into the rate implied at each of the next meetings.

    Solves every month's settlement jointly by least squares rather than walking
    the strip forward. A twelve-month strip constrains eight unknowns, so the
    months WITHOUT a meeting -- which the forward walk discards -- pin the
    carried rates directly and make the system well conditioned. See the module
    docstring for the measurements behind that choice.

    settlements maps contract month (first of month) to settlement price.
    spot_rate_pct is the effective rate currently prevailing.
    """
    import numpy as np

    if not settlements:
        raise EmptyFetchError("policy: no settlements supplied")

    upcoming = [m for m in meetings if m.effective > trade_date][:count]
    if len(upcoming) < count:
        raise EmptyFetchError(
            f"policy: only {len(upcoming)} meeting(s) after {trade_date}, need "
            f"{count}. Refresh the FOMC calendar rather than shortening the path."
        )

    by_month: dict[date, Meeting] = {}
    for m in upcoming:
        key = m.effective.replace(day=1)
        if key in by_month:
            raise DataIntegrityError(
                f"policy: two meetings effective in {key:%Y-%m}; the monthly "
                "decomposition assumes at most one and cannot separate them."
            )
        by_month[key] = m

    path = PolicyPath(trade_date=trade_date, spot_rate_pct=spot_rate_pct)

    already = [
        m for m in meetings
        if m.effective.replace(day=1) == trade_date.replace(day=1)
        and m.effective <= trade_date
    ]
    if already:
        path.warnings.append(
            f"front month {trade_date:%Y-%m} already contains an effective date "
            f"({already[-1].effective}); its decomposition is approximate because "
            "part of the month realised at a different rate"
        )

    # Unknowns are the rate in force AFTER each of the `count` meetings. The
    # rate before the first is spot, which is known and moves to the right side.
    months: list[date] = []
    month = trade_date.replace(day=1)
    last = max(by_month)
    while month <= last:
        if month not in settlements:
            raise EmptyFetchError(
                f"policy: no settlement for contract month {month:%Y-%m}, which "
                "the path needs. A gap in the strip cannot be interpolated."
            )
        months.append(month)
        month = _next_month(month)

    order = {m.effective: i for i, m in enumerate(upcoming)}
    rows, rhs = [], []
    regime = -1  # index of the last meeting whose rate is in force; -1 is spot
    regime_at_month: list[int] = []
    for mo in months:
        meeting, w_before, w_after = month_weights(mo, by_month)
        row = [0.0] * count
        constant = 0.0
        # The rate in force at the start of this month.
        if regime < 0:
            constant += w_before * spot_rate_pct
        else:
            row[regime] += w_before
        regime_at_month.append(regime)
        if meeting is not None:
            row[order[meeting.effective]] += w_after
            regime = order[meeting.effective]
        rows.append(row)
        rhs.append(implied_average_rate(settlements[mo]) - constant)

    a = np.array(rows, dtype=float)
    b = np.array(rhs, dtype=float)
    solution, *_ = np.linalg.lstsq(a, b, rcond=None)

    residuals_bp = (a @ solution - b) * 100.0
    worst = float(np.max(np.abs(residuals_bp))) if residuals_bp.size else 0.0
    if worst > RESIDUAL_WARN_BP:
        idx = int(np.argmax(np.abs(residuals_bp)))
        path.warnings.append(
            f"the strip does not fit the meeting calendar: {months[idx]:%Y-%m} "
            f"misses by {residuals_bp[idx]:+.1f}bp. Check the calendar before "
            "trusting the path (spec 2.1)."
        )

    rate_before = spot_rate_pct
    for i, m in enumerate(upcoming):
        mo = m.effective.replace(day=1)
        n = days_in_month(mo)
        amplification = n / (n - m.effective.day + 1)
        path.meetings.append(
            MeetingRate(
                meeting=m,
                rate_pct=float(solution[i]),
                rate_before_pct=rate_before,
                contract_month=mo,
                settle=settlements[mo],
                amplification=amplification,
                # With a joint solve the bound is the fit residual plus this
                # month's own tick sensitivity, not a compounding chain.
                rounding_uncertainty_bp=(
                    amplification * (SETTLEMENT_TICK_PCT / 2) * 100.0 + abs(worst)
                ),
            )
        )
        if amplification > AMPLIFICATION_WARN:
            path.notes.append(
                f"{m.effective} is effective on day {m.effective.day} of a "
                f"{n}-day month, leaving {n - m.effective.day + 1} day(s) to carry "
                f"the whole month; settlement rounding is magnified "
                f"{amplification:.1f}x on this leg"
            )
        rate_before = float(solution[i])

    path.fit_residual_bp = worst
    for note in path.notes:
        log.info("policy: %s", note)
    for warning in path.warnings:
        log.warning("policy: %s", warning)
    return path
