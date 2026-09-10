# ETF shares-outstanding sources — survey

**Status:** supervisor-verified live on **2026-09-09** (probes run 20:30–21:00 ET). This is the
first deliverable of **T52** (`plans/continuation/05-etf-flows.md`), written before any fetcher
exists so that only families with a working source get code. Every URL below was actually
requested; every value quoted was actually returned.

A creation or redemption changes a fund's shares outstanding, and that daily change times NAV
is the flow. So the only thing that has to be sourced is a per-fund, per-day **shares
outstanding** value with an **as-of date the issuer states itself**.

## Result at a glance

| Family | Symbols in our universe | Source | Verdict |
|---|---|---|---|
| State Street / SPDR | XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC XBI KRE XOP SPY DIA GLD (17) | one daily all-funds XLSX | **supported** |
| iShares | IWM TLT HYG EEM FXI SLV (6) | per-fund product page, embedded JSON | **supported** |
| VanEck | SMH GDX (2) | none found in page HTML | **unsupported** |
| Invesco | QQQ (1) | value is JS-loaded from an internal API | **unsupported** |
| USCF | USO (1) | value is JS-loaded (`data-key="so"` empty in HTML) | **unsupported** |

23 of the 27 symbols are covered. The four that are not must appear in the page's "no flow
data" list and never as a zero bar.

## The finding that changes T52's design

**Issuer files lag by a full trading day, so "skip unless the as-of date is today" would
never insert a row.**

`plans/continuation/05-etf-flows.md` says the job must "compare the file's own as-of date and
skip when it is not today". Measured: at **20:56 ET on Wednesday 2026-09-09** — after the
close, after the 18:30 ET job would have run — the SPDR all-funds file still carried
`As of Sep 08 2026`. Every one of the 17 SPDR rows did. iShares is not uniform: IWM's shares
outstanding was stamped `Sep 09, 2026` while TLT's was `Sep 08, 2026`, at the same instant.

The rule the spec was reaching for — never write today's date over yesterday's value — is
right; the implementation it named is wrong for this data. The correct rule:

> **Key every row on the as-of date the issuer states, not on the day the job ran.** Insert
> when that `(symbol, as_of_date)` is unseen; do nothing when it is already stored. Never
> substitute the run date.

That keeps the guarantee (a stale file can never masquerade as a fresh day) and still
accumulates history, which the spec's version would not. It also makes the lag harmless: the
flow for Sep 08 simply lands on Sep 09. The health block must therefore report the **as-of
date of the newest stored row per family**, not the last successful fetch time — a fetch that
succeeds and returns a week-old file is not freshness.

## State Street / SPDR — supported

**One request covers all 17 symbols.**

```
https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-product-data-us-en.xlsx
```

Requires `-L`: the `/us/en/intermediary/library-content/...` path 301s to the
`/library-content/...` one above. Returned 200, 94 KB,
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

Layout, as measured: row 1 is a performance disclaimer, **row 2 is the header**, data starts at
row 4. One row per SPDR fund (53 columns wide; all 17 of our symbols present).

| Column | Header | Example (XLK, 2026-09-08) |
|---|---|---|
| A | `As of** ` (trailing space is real) | `Sep 08 2026` |
| B | `Ticker` | `XLK` |
| O | `Closing Price` | `$187.87` |
| U | `NAV` | `$187.88` |
| V | `Shares Outstanding` | `651.81 M` |
| W | `Total Net Assets` | `$122,461.30 M` |

Parsing notes, each one an observed trap:

- **The ticker carries a trademark glyph.** `GLD®`, `GLDM®` — but plain `XLK`, `SPY`, `DIA`.
  Normalise by dropping every non-A–Z character before matching.
- **Do not depend on column letters.** Read the header row and look up by name; the file is 53
  columns wide and State Street reorders it. Fail loudly if a header is missing.
- **XLSX, not CSV** — no CSV equivalent was found. `openpyxl` is not currently a backend
  dependency; add it, or parse `xl/worksheets/sheet1.xml` + `xl/sharedStrings.xml` out of the
  zip directly (that is how this survey read the file, ~25 lines).
- **Values are formatted strings, not numbers**: `$187.88`, `651.81 M`, `$122,461.30 M`. Strip
  `$`, `,` and the ` M` suffix and multiply by 1e6. Assert the magnitude on first fetch, per
  the plan's "record the unit per family" rule.

**Precision — a real constraint, and a judgment call for the implementer.** Shares outstanding
is published to two decimals in millions, i.e. rounded to 10,000 shares. The error on a single
day's flow is then ±5,000 shares × NAV: about **±$0.94 M for XLK**, and for a small fund like
XOP (`21.00 M` — three significant digits) **±$0.97 M on a $4.07 B fund**. Daily flows are
routinely smaller than that.

`Total Net Assets / NAV` recovers far more precision, because TNA is quoted to $0.01 M:
XOP is `4,069.80 M / 193.80 = 20,998,968` shares (±~540 shares, dominated by NAV's own 2dp
rounding) versus `21.00 M` flat. XLK is `122,461.30 M / 187.88 = 651,806,153` versus
`651.81 M`.

Recommendation: **derive shares from `TNA / NAV`, cross-check against the printed
`Shares Outstanding`, and flag when the two differ by more than one creation unit** (50,000
shares for these funds). Record the choice in `docs/validation-scan.md` with a worked example —
the derived figure is what the flow is computed from, so it needs an audit trail.

Rejected en route, recorded so nobody retries them:

- `holdings-daily-us-en-{ticker}.xlsx` — 200, but holdings only; no shares outstanding.
- `nav-history-us-en-{ticker}.xlsx`, `fund-summary-us-en-{ticker}.xlsx` — 404 (the real name is
  `navhist-us-en-{ticker}.xlsx`, which is NAV history and carries no share count).
- `bin/v1/ssmp/fund/fundfinder?...&ticker=XLK` — 400.
- **`sectorspdrs.com` no longer exists as a separate site**; `https://www.sectorspdrs.com/api/index/XLK`
  and `/mainfund/XLK` both return 200 with State Street's own HTML. Do not build against it.
- The fund page (`https://www.ssga.com/us/en/intermediary/etfs/...-xlk`) does render
  `Shares Outstanding 651.81 M` in a plain `<table>`, at the same precision as the XLSX. It is a
  per-fund request for numbers the one all-funds file already gives — keep it only as a
  documented fallback.

## iShares — supported

**One request per fund.** The value lives in a JSON blob embedded in the product page.

| Symbol | URL |
|---|---|
| IWM | `https://www.ishares.com/us/products/239710/ishares-russell-2000-etf` |
| TLT | `https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf` |
| HYG | `https://www.ishares.com/us/products/239565/ishares-iboxx-high-yield-corporate-bond-etf` |
| EEM | `https://www.ishares.com/us/products/239637/ishares-msci-emerging-markets-etf` |
| FXI | `https://www.ishares.com/us/products/239536/ishares-china-large-cap-etf` |
| SLV | `https://www.ishares.com/us/products/239855/ishares-silver-trust` |

All six returned 200 (1.5–1.9 MB each) with a browser `User-Agent`. No login, no cookie wall,
no captcha.

The payload, as returned for TLT on 2026-09-09:

```
"sharesOutstanding":{"visible":true,"label":"Shares Outstanding","formattedValue":"570,300,000",
 "sortOrder":44,"prefix":null,"infoBubble":"","formattedAsOfDate":"Sep 08, 2026","name":"sharesOutstanding"}
"navAmount":{...,"asOfDate":20260909,"formattedAsOfDate":"Sep 09, 2026","formattedValue":"290.65",...}
```

- **Full precision** — `570,300,000`, `269,850,000` — unlike SPDR's two decimals in millions.
- **Shares outstanding and NAV carry separate as-of dates**, and they disagree in practice
  (IWM: SO `Sep 09`, NAV `Sep 08`). Store each with its own date; do not assume they match.
- **`navAmount` also gives a numeric `asOfDate` (`20260909`)** — parse that, not the formatted
  string.
- **Unescape the HTML first.** Some pages serve the blob raw (`"sharesOutstanding":{`), others
  escaped (`&quot;sharesOutstanding&quot;:{`). Unescape entities before the regex, and cover
  both forms in fixtures. IWM came back raw, TLT escaped, on the same afternoon.
- **Product ids are the fragile part.** They are stable in practice but a fund reorganisation
  changes them. A 200 whose body has no `sharesOutstanding` key is the signature of a moved
  page — treat it as a per-symbol failure with the symbol named, not a crash.

Rejected, with evidence:

- **The documented `?fileType=csv` download no longer returns CSV.**
  `.../239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_fund&dataType=fund`
  returns **200 with `Content-Type: text/csv` and an HTML body** (1.4 MB, `<!DOCTYPE html>`,
  `<title>iShares Russell 2000 ETF | IWM | US Class</title>`). Content-type is not evidence
  here; a fetcher must sniff the body. This is the source `05-etf-flows.md` assumed — it is gone.
- **The product screener does not carry share counts.**
  `https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/ishares-product-screener-backend-config&siteEntryPassthrough=true`
  returns 200, 1.9 MB of clean JSON covering every iShares fund, with `navAmount`,
  `navAmountAsOf` and performance — but the string `sharesOutstanding` appears **zero** times.
  It would have been one request for all six; it is not an option. Worth revisiting only for NAV.

## VanEck (SMH, GDX) — unsupported

`https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/` returns 200 (278 KB), but the
only occurrence of "shares outstanding" in the body is inside the prose definition of NAV. No
share count is rendered server-side. (The older `.../overview/` path 302s to an empty body.)
Their internal API was not located within the time box. Mark unsupported; revisit only if the
user wants SMH/GDX flows specifically.

## Invesco (QQQ) — unsupported

`https://www.invesco.com/qqq-etf/en/about.html` returns 200 (208 KB) and contains only the
*label*, wired for client-side fill:

```
{"fundDetailsLabel":"Shares Outstanding","fundDetailsType":"ShareOutstanding"}
```

The value arrives from an internal endpoint after page load. Not scraped here.

## USCF (USO) — unsupported

`https://www.uscfinvestments.com/uso` returns 200 (48 KB) with the table skeleton present and
empty: `<th>Shares Outstanding</th><td data-key="so"></td>`. JS-filled, same as Invesco.

## Constraints this survey respected

No page requiring a login was touched, and nothing here set an anti-bot cookie or served a
captcha. Every family above is a plain `GET` with a browser `User-Agent`. Per the plan: every
fetcher T52 writes is tested against a **recorded fixture**, never the live site.

## What T52 should build, given the above

1. `SharesOutstandingProvider` ABC returning `(symbol, as_of_date, shares_outstanding, nav | None)`.
2. **`SpdrAllFundsProvider`** — one fetch, 17 symbols, `TNA / NAV` derived share count with the
   printed value as a cross-check.
3. **`ISharesProductPageProvider`** — six per-fund fetches, full precision, per-field as-of dates.
4. Nothing for VanEck, Invesco or USCF; those four symbols are listed as "no flow data".
5. The as-of-date keying rule from the section above, replacing the spec's skip-unless-today rule.
