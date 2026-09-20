"""Exception hierarchy.

Every failure mode the spec calls out as "fail loudly" gets a named exception
here rather than a log line and an empty return.
"""


class XactxError(Exception):
    """Base for every error raised by this package."""


class UnknownSeriesError(XactxError):
    """A series_id is not registered in series_metadata, or a source code is not
    known to its adapter.

    Spec 7, "Series discontinuation": a retired or renamed source code must raise
    here rather than yield an empty series.
    """


class EmptyFetchError(XactxError):
    """An adapter fetch returned no observations for a range that should contain
    data."""


class DataIntegrityError(XactxError):
    """A stored value conflicts with one already persisted under the same
    (series_id, value_date, as_of) key, or an observation failed validation."""


class LookaheadError(XactxError):
    """An input carries an as_of later than the evaluation timestamp.

    Spec 8, "No-lookahead assertion".
    """
