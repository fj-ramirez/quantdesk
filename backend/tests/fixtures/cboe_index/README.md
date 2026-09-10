Recorded 2026-09-09 21:00 ET from
`https://cdn.cboe.com/api/global/us_indices/daily_prices/{IX}_History.csv`, trimmed to keep
the tests small. Header line 1, no preamble; dates `MM/DD/YYYY`.

- `VIX_History` / `VIX3M_History` -- `DATE,OPEN,HIGH,LOW,CLOSE`. The 1990 rows in the VIX file
  are kept on purpose: Cboe published closes only back then and dressed them as OHLC
  (`17.24,17.24,17.24,17.24`), which is the precedent for how T54 stores the close-only series.
- `VVIX_History` / `SKEW_History` -- `DATE,VVIX` / `DATE,SKEW`, close only. VVIX's early
  history is sparse (03/06/2006 then 03/15/2006), also kept deliberately.
- `not-a-csv-error-body.html` -- for the `ProviderError`-on-non-CSV-body test.

See `plans/continuation/06-cross-asset-regime.md` "Verified facts" for the full survey.
