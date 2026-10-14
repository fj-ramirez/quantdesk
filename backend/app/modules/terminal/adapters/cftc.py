"""CFTC Commitments of Traders adapter (spec 1.2, 1.3).

Source codes name a report, a contract and a trader group. A bare contract market
code (043602 = UST 10Y note) is the Legacy report's large-speculator net position,
the series this adapter has carried since T79:

    net_spec = noncomm_positions_long_all - noncomm_positions_short_all

T155 added the two reports that split that "non-commercial" crowd apart, addressed
as `<report>:<code>:<group>` (plans/cot/README.md):

    tff:13874A:lev_money     Traders in Financial Futures, leveraged funds
    tff:13874A:asset_mgr     ... asset managers
    disagg:088691:m_money    Disaggregated, managed money

Each value is that group's long minus short, spreads excluded. All three reports
measure the same Tuesday and publish on the same day, so the release lag below
applies to every one of them unchanged. Bare codes stay bare so the rows already
stored under them keep their source_code.

**The release lag is the reason this adapter needs care.** A COT report is dated
the Tuesday its positions were measured, but it is not published until the
following Friday at 15:30 ET. Storing the Tuesday date as as_of would hand every
backtest three days of free foresight on a weekly series -- exactly the trap
spec 1.2 flags. value_date is therefore the Tuesday and as_of is the Friday
release instant.

In weeks containing a federal holiday the measurement date moves, nearly always
to the Monday, while publication stays on the Friday. as_of is therefore derived
from the release WEEKDAY rather than a fixed offset -- see release_date.

**The Friday is not always the release day (T158).** When the Friday is a federal
holiday, the day after Thanksgiving, or a day the government closed by order,
CFTC publishes the next business day: the 2025-11-25 report went out Monday
12-01. Three government shutdowns delayed reports by weeks, and those dates come
from CFTC's published catch-up schedules (DELAYED_RELEASES), as do the reports
the 2023 ION cyber incident held back. Until T158 every one
of these got the Friday as its as_of, so it was visible before it was public.

Positions are stored as raw contracts. Spec 2.2 wants them read as a percentile
of their own trailing three-year range, since contract counts are not comparable
across time as open interest grows; that percentile is a derived series
(derive.DERIVATIONS), computed from these raw counts rather than replacing them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

from pandas.tseries.holiday import USFederalHolidayCalendar

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

# Socrata endpoint for the Legacy Futures-Only report.
BASE_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Reports usually measure positions at the close of Tuesday, but in weeks
# containing a federal holiday the measurement date moves, nearly always to the
# Monday. 17 of the 1237 reports since 2003 are not Tuesday-dated.
REPORT_WEEKDAY = 1  # Monday is 0

# Publication is on a Friday, at 15:30 ET.
RELEASE_WEEKDAY = 4
RELEASE_LOCAL_TIME = time(15, 30)

# Shortest gap ever seen between measurement and publication. as_of is the first
# RELEASE_WEEKDAY on or after value_date + this, which is correct for the normal
# Tuesday report (+3, the same week's Friday) and for the holiday-week Monday
# report (+4, still that Friday). A fixed +3 would have put the Monday reports'
# as_of on a Thursday -- a day of lookahead on 15 observations. For the two
# reports dated Wednesday or Friday the rule overshoots to the following week,
# which is the safe direction: a value can be invisible too long, never visible
# too early.
MIN_RELEASE_LAG_DAYS = 3

_FEDERAL_HOLIDAYS = frozenset(
    d.date() for d in USFederalHolidayCalendar().holidays("1986-01-01", "2040-12-31")
)

# Days federal offices closed by order, outside the holiday calendar, that fell on
# a release day. 2004-06-11 was the national day of mourning for President Reagan;
# 2008-12-26 and 2025-12-26 / 2026-01-02 were closed by executive order (CFTC's
# "original publish date" column in release 9138-25 moves those weeks to Monday).
_AD_HOC_CLOSURES = frozenset({
    date(2004, 6, 11),
    date(2008, 12, 26),
    date(2025, 12, 26),
    date(2026, 1, 2),
})


def _d(s: str) -> date:
    return date.fromisoformat(s)


# Report date -> the day it was actually published, for reports a government
# shutdown (or, in 2023, the ION cyber incident) delayed.
#
# - 2025: exact, from the accelerated schedule CFTC posted on 2025-12-09 (COT
#   Historical Special Announcements). It superseded release 9138-25's table from
#   the 2025-11-10 report on, and was back on schedule with 12-23's report on 12-29.
# - 2023: exact, from CFTC's announcement of each catch-up report, 2023-02-24 to
#   2023-03-21. The 2023-01-31 report was the first held back.
# - 2018-19: CFTC release 7864-19 put the 2018-12-24 report on 2019-02-01, then
#   "one report on Tuesday and another on Friday of each week until the reports are
#   current". That cadence makes 2019-03-05's report the first current one (03-08),
#   which matches contemporaneous reporting (Saxo, 2019-02-18, on the week to
#   January 22).
# - 2013: release 6745-13 gave weeks, not days: the 10-01 report on 10-25, two more
#   in the week of 10-28, at least two more in the week of 11-04, and back on
#   schedule by 11-08. Each is dated to the Friday ending its week, which can only
#   be late, never early.
DELAYED_RELEASES: dict[date, date] = {
    _d(r): _d(p) for r, p in (
        ("2013-10-01", "2013-10-25"),
        ("2013-10-08", "2013-11-01"),
        ("2013-10-15", "2013-11-01"),
        ("2013-10-22", "2013-11-08"),
        ("2013-10-29", "2013-11-08"),
        ("2018-12-24", "2019-02-01"),
        ("2018-12-31", "2019-02-05"),
        ("2019-01-08", "2019-02-08"),
        ("2019-01-15", "2019-02-12"),
        ("2019-01-22", "2019-02-15"),
        ("2019-01-29", "2019-02-19"),
        ("2019-02-05", "2019-02-22"),
        ("2019-02-12", "2019-02-26"),
        ("2019-02-19", "2019-03-01"),
        ("2019-02-26", "2019-03-05"),
        ("2023-01-31", "2023-02-24"),
        ("2023-02-07", "2023-03-03"),
        ("2023-02-14", "2023-03-08"),
        ("2023-02-21", "2023-03-10"),
        ("2023-02-28", "2023-03-14"),
        ("2023-03-07", "2023-03-16"),
        ("2023-03-14", "2023-03-21"),
        ("2025-09-30", "2025-11-19"),
        ("2025-10-07", "2025-11-21"),
        ("2025-10-14", "2025-11-25"),
        ("2025-10-21", "2025-12-02"),
        ("2025-10-28", "2025-12-05"),
        ("2025-11-04", "2025-12-09"),
        ("2025-11-10", "2025-12-10"),
        ("2025-11-18", "2025-12-12"),
        ("2025-11-25", "2025-12-15"),
        ("2025-12-02", "2025-12-17"),
        ("2025-12-09", "2025-12-19"),
        ("2025-12-16", "2025-12-23"),
        ("2025-12-23", "2025-12-29"),
    )
}

# The span of report dates each delay covered, and the day the last of them came out. A report inside one that is not in
# DELAYED_RELEASES (a contract reporting on an unusual date) gets the window's last
# catch-up date: late, but never early.
SHUTDOWN_WINDOWS = (
    (date(2013, 10, 1), date(2013, 10, 29), date(2013, 11, 8)),
    (date(2018, 12, 22), date(2019, 2, 26), date(2019, 3, 5)),
    (date(2023, 1, 31), date(2023, 3, 14), date(2023, 3, 21)),
    (date(2025, 9, 30), date(2025, 12, 23), date(2025, 12, 29)),
)


def _no_release_on(d: date) -> bool:
    """CFTC does not publish on weekends, federal holidays, closures or Black Friday."""
    if d.weekday() >= 5 or d in _FEDERAL_HOLIDAYS or d in _AD_HOC_CLOSURES:
        return True
    # The Friday after Thanksgiving: the fourth Thursday is the 22nd-28th. Testing only
    # "yesterday was a November holiday" would also catch a Thursday Veterans Day.
    thursday = d - timedelta(days=1)
    return (
        d.month == 11 and d.weekday() == 4 and thursday.day >= 22
        and thursday in _FEDERAL_HOLIDAYS
    )


def release_date(report_date: date) -> date:
    """The day a report dated `report_date` was published.

    A shutdown-delayed report takes its date from the catch-up schedule. Otherwise it
    is the first RELEASE_WEEKDAY on or after report_date + MIN_RELEASE_LAG_DAYS,
    pushed to the next day CFTC could publish on. Derived from the weekday rather
    than a fixed offset so holiday-week reports, which are measured on the Monday
    but still published that Friday, do not get an as_of three days early.
    """
    if report_date in DELAYED_RELEASES:
        return DELAYED_RELEASES[report_date]
    for lo, hi, resumed in SHUTDOWN_WINDOWS:
        if lo <= report_date <= hi:
            return resumed
    earliest = report_date + timedelta(days=MIN_RELEASE_LAG_DAYS)
    d = earliest + timedelta(days=(RELEASE_WEEKDAY - earliest.weekday()) % 7)
    while _no_release_on(d):
        d += timedelta(days=1)
    return d

# Socrata caps a page; paginate rather than assume one request holds a history
# that reaches back to 1986.
PAGE_SIZE = 5000

DATE_FIELD = "report_date_as_yyyy_mm_dd"
CODE_FIELD = "cftc_contract_market_code"

# Contract market codes this adapter knows, with the market name the API
# returns, verified live on 2026-09-12 (the last three on 2026-10-13, T155). Every
# financial contract was renamed on 2022-02-08 without changing its code, which
# the reassignment check in parse tolerates by reading only the newest row. Used
# to fail loudly on an unknown code and to detect a code being reassigned to a
# different contract.
KNOWN_CONTRACTS = {
    "043602": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
    "042601": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
    "13874A": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "098662": "USD INDEX - ICE FUTURES U.S.",
    "067651": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    "088691": "GOLD - COMMODITY EXCHANGE INC.",
    "209742": "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE",
    "124603": "DJIA x $5 - CHICAGO BOARD OF TRADE",
    "099741": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
}


@dataclass(frozen=True)
class Report:
    """One CFTC report: its Socrata dataset and the groups it splits traders into."""

    url: str
    #: group -> (long field, short field). Verified live on 2026-10-13.
    groups: dict[str, tuple[str, str]]


LEGACY = "legacy"
REPORTS = {
    LEGACY: Report(
        BASE_URL,
        {"noncomm": ("noncomm_positions_long_all", "noncomm_positions_short_all")},
    ),
    # Traders in Financial Futures, futures only. History from 2006-06-13.
    "tff": Report(
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
        {
            "lev_money": ("lev_money_positions_long", "lev_money_positions_short"),
            "asset_mgr": ("asset_mgr_positions_long", "asset_mgr_positions_short"),
            "dealer": ("dealer_positions_long_all", "dealer_positions_short_all"),
        },
    ),
    # Disaggregated, futures only. History from 2006-06-13. The swap dealers' short
    # field really is spelled with a double underscore in the API.
    "disagg": Report(
        "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
        {
            "m_money": ("m_money_positions_long_all", "m_money_positions_short_all"),
            "prod_merc": ("prod_merc_positions_long", "prod_merc_positions_short"),
            "swap": ("swap_positions_long_all", "swap__positions_short_all"),
        },
    ),
}


@dataclass(frozen=True)
class SourceCode:
    report: str
    contract: str
    group: str


def parse_source_code(source_code: str) -> SourceCode:
    """`043602` -> Legacy non-commercial; `tff:13874A:lev_money` -> that report and group.

    Raises UnknownSeriesError on a report, contract or group this adapter does not
    know, rather than letting a typo become a series that fetches nothing.
    """
    parts = source_code.split(":")
    if len(parts) == 1:
        sc = SourceCode(LEGACY, parts[0], "noncomm")
    elif len(parts) == 3:
        sc = SourceCode(*parts)
    else:
        raise UnknownSeriesError(
            f"cftc: {source_code!r} is neither a bare contract code nor "
            "'<report>:<code>:<group>'"
        )
    report = REPORTS.get(sc.report)
    if report is None:
        raise UnknownSeriesError(
            f"cftc: unknown report {sc.report!r} in {source_code!r}; known {sorted(REPORTS)}"
        )
    if sc.group not in report.groups:
        raise UnknownSeriesError(
            f"cftc: report {sc.report!r} has no group {sc.group!r}; "
            f"known {sorted(report.groups)}"
        )
    if sc.contract not in KNOWN_CONTRACTS:
        raise UnknownSeriesError(
            f"cftc: {sc.contract!r} is not a contract code this adapter knows. "
            f"Known: {sorted(KNOWN_CONTRACTS)}"
        )
    return sc


class CftcAdapter(BaseAdapter):
    name = "cftc"

    def resolve(self, source_code: str) -> str:
        parse_source_code(source_code)
        return super().resolve(source_code)

    def release_as_of(self, report_date: date):
        """The instant a report dated `report_date` became public: release_date at 15:30 ET."""
        return self.at_local(release_date(report_date), RELEASE_LOCAL_TIME)

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        out: list[Observation] = []
        for code in series_codes:
            series_id = self.resolve(code)
            sc = parse_source_code(code)
            rows = self._download(REPORTS[sc.report].url, sc.contract, start, end)
            out.extend(self.parse(rows, code, series_id, source_batch))
        return out

    def _download(self, url: str, contract: str, start: date, end: date) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            r = self.client.get(
                url,
                params={
                    CODE_FIELD: contract,
                    "$where": (
                        f"{DATE_FIELD} >= '{start.isoformat()}T00:00:00.000' "
                        f"AND {DATE_FIELD} <= '{end.isoformat()}T00:00:00.000'"
                    ),
                    "$order": f"{DATE_FIELD} ASC",
                    "$limit": PAGE_SIZE,
                    "$offset": offset,
                },
            )
            r.raise_for_status()
            page = r.json()
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def parse(
        self, rows: list[dict], code: str, series_id: str, source_batch: str
    ) -> list[Observation]:
        """Rows from the API into net-position observations for `code`'s group.

        Separate from the download so contract tests run against a fixture.
        """
        sc = parse_source_code(code)
        long_field, short_field = REPORTS[sc.report].groups[sc.group]
        if not rows:
            raise EmptyFetchError(
                f"cftc: {code} ({series_id}) returned no reports in range. The "
                "code resolves but the window is empty."
            )

        expected_name = KNOWN_CONTRACTS[sc.contract]

        # Contracts get RENAMED without being reassigned: 098662 reported as
        # "U.S. DOLLAR INDEX - NEW YORK COTTON EXCHANGE" for years before NYCE
        # became ICE Futures U.S., and 067651 was "CRUDE OIL, LIGHT SWEET"
        # before "WTI-PHYSICAL". Checking every historical row against today's
        # name would reject decades of perfectly good data.
        #
        # What actually needs catching is REASSIGNMENT -- a code coming to mean
        # a different contract -- and that shows up in the current identity. So
        # the newest row is checked and the renames are merely reported.
        names = [
            row.get("market_and_exchange_names", "").strip()
            for row in rows
            if row.get("market_and_exchange_names")
        ]
        if names and names[-1] != expected_name:
            raise UnknownSeriesError(
                f"cftc: {code} now reports as {names[-1]!r} but this adapter "
                f"expects {expected_name!r}. The code may have been reassigned "
                "to a different contract; verify before trusting the series."
            )
        historical = sorted(set(names) - {expected_name})
        if historical:
            self.log.info(
                "%s (%s): reported under %d earlier name(s), same contract: %s",
                code, series_id, len(historical), historical,
            )

        obs: list[Observation] = []
        skipped = 0
        off_cycle = 0
        for row in rows:
            raw_date = row.get(DATE_FIELD)
            long_raw, short_raw = row.get(long_field), row.get(short_field)
            if not raw_date or long_raw is None or short_raw is None:
                skipped += 1
                continue

            report_date = date.fromisoformat(raw_date[:10])
            if report_date.weekday() != REPORT_WEEKDAY:
                # Holiday weeks. release_as_of handles them; counted so the
                # frequency stays visible rather than assumed.
                off_cycle += 1

            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=report_date,
                    as_of=self.release_as_of(report_date),
                    value=float(int(long_raw) - int(short_raw)),
                    source_batch=source_batch,
                    as_of_basis="derived_lag",
                )
            )

        if skipped:
            self.log.warning(
                "%s (%s): %d row(s) missing a date or position field, skipped",
                code, series_id, skipped,
            )
        if off_cycle:
            self.log.warning(
                "%s (%s): %d report(s) are not Tuesday-dated (holiday weeks); "
                "as_of derived from the release weekday, not a fixed offset",
                code, series_id, off_cycle,
            )
        if not obs:
            raise EmptyFetchError(
                f"cftc: {code} ({series_id}) parsed no usable rows from "
                f"{len(rows)} returned."
            )
        self.log.info("%s (%s): %d weekly reports", code, series_id, len(obs))
        return obs
