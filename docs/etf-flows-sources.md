# ETF shares-outstanding sources — survey

**Status:** T52's half supervisor-verified live on **2026-09-09** (probes run 20:30–21:00 ET);
T59 went back for the four VanEck/Invesco/USCF symbols on **2026-09-10** and found a working
endpoint for every one of them. T52 was written before any fetcher existed so that only
families with a working source got code; T59 updates this document in place rather than
appending, per its own brief. Every URL below was actually requested; every value quoted was
actually returned.

A creation or redemption changes a fund's shares outstanding, and that daily change times NAV
is the flow. So the only thing that has to be sourced is a per-fund, per-day **shares
outstanding** value with an **as-of date the issuer states itself**.

## Result at a glance

| Family | Symbols in our universe | Source | Verdict |
|---|---|---|---|
| State Street / SPDR | XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC XBI KRE XOP SPY DIA GLD (17) | one daily all-funds XLSX | **supported** |
| iShares | IWM TLT HYG EEM FXI SLV (6) | per-fund product page, embedded JSON | **supported** |
| VanEck | SMH GDX (2) | `Main/FundDetailsBlock/GetContent` JSON (T59) | **supported** |
| Invesco | QQQ (1) | `dng-api.invesco.com` shareclass JSON (T59) | **supported** |
| USCF | USO (1) | two-step token + `dailyprice` JSON (T59) | **supported** |

All 27 symbols are covered as of T59. `UNSUPPORTED_SYMBOLS` (`app.providers.etf_flows`) and the
`/flows` page's "no flow data" list are both empty.

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

## VanEck (SMH, GDX) — supported (T59)

T52 found nothing: `https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/` returns
200 (278 KB), but the only occurrence of "shares outstanding" in the body is inside the prose
definition of NAV — no share count server-side, and the older `.../overview/` path 302s to an
empty body. That much still stands; it is *why* T59 had to go find the endpoint the page's own
JavaScript calls, rather than repeating the page probe.

**Method:** drove `https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/` with
Playwright (`npx playwright`, headless Chromium) and watched every network response for the
string "outstanding". The page's initial render is mostly `Loading...` placeholders that fill
in via a family of `Main/<Block>Block/GetContent` AJAX calls; `Main/FundDetailsBlock/GetContent`
is the one carrying `Shares Outstanding`.

```
GET https://www.vaneck.com/Main/FundDetailsBlock/GetContent/
    ?blockid=229617&pageid={pageid}&ticker={TICKER}
    &reactlang=en&reactctr=us&epieditmode=false&latest=false&contextmode=Default
```

`blockid=229617` (the "Fund Details" panel) is the same for every VanEck fund; only `pageid`
and `ticker` vary. Confirmed for both symbols in our universe:

| Symbol | `pageid` |
|---|---|
| SMH | 233107 |
| GDX | 233083 |

Response shape (SMH, 2026-09-09, `Content-Type: application/json`):

```json
{"data":{"LongVersionAsOfDate":"09/09/2026","Values":[
  {"Title":"Exchange","Value":"Nasdaq",...},
  {"Title":"Shares Outstanding","Value":"123,891,874","AsOfDate":null,...},
  {"Title":"Options","Value":"Available",...},
  ...
]}}
```

- **Unit: ones, full precision** — `"123,891,874"`, not `"123.89 M"`. Strip commas and parse as
  int; no scaling.
- **As-of date is the block's, not the field's.** Every `Values` entry (including `Shares
  Outstanding`) carries `"AsOfDate": null` in every fixture recorded; the real date is
  `data.LongVersionAsOfDate` (`"MM/DD/YYYY"`), one date for the whole panel. A row is only
  written when this field is present — a missing one is a named failure, not a fallback to
  "today".
- **No NAV in this block.** VanEck's NAV is rendered by a different part of the page; `nav` is
  always `None` for this source, same optionality as iShares'.
- **Plain, unauthenticated `GET`.** Verified with `curl`, no cookies at all, both before and
  after loading the page in a browser — same response either way. `Set-Cookie` headers present
  on the response (`ARRAffinity`, `TiPMix`, a locale preference) are ordinary load-balancer/
  session cookies, never required on the request; no login, no anti-bot challenge, no captcha.

## Invesco (QQQ) — supported (T59)

T52 found the client-fill label only:
`https://www.invesco.com/qqq-etf/en/about.html` → `{"fundDetailsLabel":"Shares Outstanding",
"fundDetailsType":"ShareOutstanding"}`. That still stands.

**Method:** drove the same page with Playwright and captured every `xhr`/`fetch` request. Among
~20 analytics calls, one stood out: `dng-api.invesco.com`, Invesco's own data-gateway domain.

```
GET https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{TICKER}
    ?idType=ticker&variationType=fundDetails&productType=ETF
```

Response (QQQ, 2026-09-09):

```json
{ "cusip": "QQQ", "effectiveDate": "2026-09-09", "effectiveBusinessDate": "2026-09-09",
  "shareclassTotalNetAssets": 481706269448.89, "nav": 716.185355, "marketValue": 481706269448.89,
  "sharesOutstanding": 672600000, "feeValue": 0.18, "exchange": "Nasdaq/NMS (Global Market)", ... }
```

- **Unit: ones, full precision** — `672600000`, a plain JSON number.
- **As-of date: `effectiveDate`** (`"YYYY-MM-DD"`). NAV's own `effectiveDate` matches in every
  sample seen; stored from the same field regardless, per the "one row, one date" model this
  source uses (unlike iShares' separate SO/NAV dates).
- **Unrecognized ticker returns literal `""`** (still HTTP `200`), not an object or an error
  status — the "no data" signature this source's fetcher treats as a named per-symbol failure.
- **No headers are actually required — corrected on supervisor re-verification.** T59 recorded
  this endpoint as needing an `Origin: https://www.invesco.com` header, having isolated headers
  one at a time and seen `406 Not Acceptable` without it. That did **not** reproduce the next
  morning (2026-09-10): five consecutive requests sent with *no* headers at all — no
  `User-Agent`, no `Origin`, no `Referer` — each returned `200` with the full payload
  (`sharesOutstanding: 672600000`, `effectiveDate: 2026-09-09`). Whatever produced those 406s
  was transient or specific to that moment, not the missing header.

  The fetcher still sends `Origin`/`Referer`/`Accept` defensively: they cost nothing, they are
  what the site's own JS sends, and if the gateway ever does gate intermittently or from another
  network, that is the shape known to pass. They are fixed public values, never a cookie,
  session token or credential, so this stays a plain public GET either way — no login, no
  captcha, no cookie. **Do not infer from their presence that they are load-bearing**; if they
  ever cause trouble, drop them and re-measure.

## USCF (USO) — supported (T59)

T52 found the empty table skeleton: `https://www.uscfinvestments.com/uso` → `<th>Shares
Outstanding</th><td data-key="so"></td>`, JS-filled. That still stands.

**Method:** drove the same page with Playwright and captured every `xhr`/`fetch` request.
`secure.alpsinc.com/MarketingAPI/api/v1/dailyprice/USO` carries the value (`"so"`), but a bare
`curl` against it 401s with `WWW-Authenticate: Bearer` — this is the one T59 family that needed
a second step.

**Step 1 — mint a token.** The page's own `<script src="assets/javascript/api_key.php">` tag
(resolved against the page's `<base href="https://www.uscfinvestments.com/site-template/">` —
*not* against `www.uscfinvestments.com/assets/...` directly, which serves an unrelated sitemap
page for that path; the `<base>` tag is what makes the relative script path resolve correctly):

```
GET https://www.uscfinvestments.com/site-template/assets/javascript/api_key.php
```

returns a small JS snippet, not JSON:

```js
var token = 'eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9...';var api_url_v2 = 'https://secure.alpsinc.com/MarketingAPI/api/v1/';...
```

Decoded JWT payload: `{"iat": <unix>, "sub": ".../Token/New", "nbf": <iat>, "exp": <iat +
86400>, "mkt": "false"}` — an anonymous, one-day token every visitor's browser gets from this
same public URL. Verified with **no cookies at all** (`curl`, fresh each time) that this still
returns a fresh, working token — no login, no session required to mint it.

**Step 2 — call the price/shares endpoint with it:**

```
GET https://secure.alpsinc.com/MarketingAPI/api/v1/dailyprice/{TICKER}
Authorization: Bearer <token from step 1>
```

Response (USO, 2026-09-09), a one-element array:

```json
[{"symbol":"USO","nav":149.1700,"navtotal":2121736459.9800,"so":14223603.0000,
  "displaydate":"2026-09-09T05:00:00", ...}]
```

- **Unit: ones, full precision** — `14223603.0000`, a JSON float with a `.0000` tail; round to
  the nearest int, don't truncate.
- **As-of date: `displaydate`**, `"YYYY-MM-DDTHH:MM:SS"` with a fixed time-of-day and no
  timezone marker. Only the date component is meaningful (this table stores a calendar date,
  not an instant — see `app.models.db.EtfSharesOutstanding`'s docstring); the `05:00:00` is
  presumed to be a display artifact of whatever internal timezone ALPS's feed uses, not
  something worth parsing further.
- **Unrecognized ticker 404s** with a plain-text body (`"No resources found for given
  resource: {ticker}."`) — unlike Invesco's 200-with-empty-body shape.
- **No login, no anti-bot cookie, no captcha at any step.** The `__cf_bm` cookie Cloudflare
  sets on responses from both `uscfinvestments.com` and `alpsinc.com` is bot-management
  telemetry, never required to be echoed back — confirmed by making both requests fresh, with
  no cookie jar at all, and getting a 200 every time.

## Constraints this survey respected

No page requiring a login was touched, and nothing here set an anti-bot cookie or served a
captcha, in either T52's probes or T59's. Every family above is a plain `GET` (USCF: two plain
`GET`s) with a browser `User-Agent`, found by watching what each page's own JavaScript actually
requested — never by guessing a path against the live site — per this task's brief. Per the
plan: every fetcher is tested against a **recorded fixture**, never the live site.

## What T52 built

1. `SharesOutstandingProvider` ABC returning `(symbol, as_of_date, shares_outstanding, nav | None)`.
2. **`SpdrAllFundsProvider`** — one fetch, 17 symbols, `TNA / NAV` derived share count with the
   printed value as a cross-check.
3. **`ISharesProductPageProvider`** — six per-fund fetches, full precision, per-field as-of dates.
4. The as-of-date keying rule from the section above, replacing the spec's skip-unless-today rule.

## What T59 added

5. **`VanEckFundDetailsProvider`** — two per-fund fetches (SMH, GDX), full precision, block-level
   as-of date, no NAV.
6. **`InvescoShareclassProvider`** — one fetch (QQQ), full precision, `effectiveDate`, NAV
   included. Sends the `Origin`/`Referer` headers documented above defensively; re-measurement
   showed the endpoint answers a bare request with no headers at all.
7. **`USCFDailyPriceProvider`** — a token fetch plus one price fetch (USO), full precision,
   `displaydate`, NAV included. The token fetch is family-level: a failure there fails the whole
   `fetch()` call (`ProviderError`), never a per-symbol entry, since no USCF symbol can be
   fetched without it.
8. All four of T52's "no flow data" symbols removed from `UNSUPPORTED_SYMBOLS`
   (`app.providers.etf_flows`) and from `/api/scan/flows`'s `no_flow_data` list, which both
   derive from it. `FAMILY_SYMBOLS` and `ALL_SUPPORTED_SYMBOLS` extended accordingly; the health
   block (`/api/health/capture`) picks up the three new families automatically since it iterates
   `FAMILY_SYMBOLS`.
