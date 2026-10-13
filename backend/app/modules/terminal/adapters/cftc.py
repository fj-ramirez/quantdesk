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
from the release WEEKDAY rather than a fixed offset -- see release_as_of. No
holiday calendar is needed, which matters because the rest of the codebase
deliberately does without one (spec 7).

Positions are stored as raw contracts. Spec 2.2 wants them read as a percentile
of their own trailing three-year range, since contract counts are not comparable
across time as open interest grows; that percentile is a derived series
(derive.DERIVATIONS), computed from these raw counts rather than replacing them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

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
        """The instant a report dated `report_date` became public.

        The first RELEASE_WEEKDAY on or after report_date + MIN_RELEASE_LAG_DAYS.
        Derived from the weekday rather than a fixed offset so holiday-week
        reports, which are measured on the Monday but still published that
        Friday, do not get an as_of three days early.
        """
        earliest = report_date + timedelta(days=MIN_RELEASE_LAG_DAYS)
        days_to_release = (RELEASE_WEEKDAY - earliest.weekday()) % 7
        return self.at_local(
            earliest + timedelta(days=days_to_release), RELEASE_LOCAL_TIME
        )

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
