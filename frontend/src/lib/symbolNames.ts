/**
 * T123 — what a ticker *is*, for the reader who does not carry 125 tickers in their head.
 * `/rotation` ranks `XLRE` against `XLC`; without a name the reader leaves the page to look
 * them up.
 *
 * A static map over the default `SCAN_UNIVERSE` (`backend/app/core/config.py`) and the chain
 * underlyings: the names are reference data, not market data, and none of them changes on a
 * timescale this app cares about. A symbol missing from the map (a `SCAN_UNIVERSE` override
 * in `.env`) returns `null` and renders as the bare ticker — never a guessed name.
 *
 * ETF entries name the *exposure* ("Regional banks"), not the fund's legal name ("SPDR S&P
 * Regional Banking ETF"): the exposure is the thing the rotation and scan pages compare.
 */
const NAMES: Readonly<Record<string, string>> = {
  // Broad equity indices and benchmarks
  SPX: 'S&P 500 index',
  SPY: 'S&P 500',
  QQQ: 'Nasdaq-100',
  DIA: 'Dow Jones Industrials',
  IWM: 'Russell 2000 small caps',
  RSP: 'S&P 500 equal weight',

  // SPDR Select Sector ETFs (GICS sectors)
  XLK: 'Technology',
  XLF: 'Financials',
  XLE: 'Energy',
  XLV: 'Health care',
  XLI: 'Industrials',
  XLY: 'Consumer discretionary',
  XLP: 'Consumer staples',
  XLU: 'Utilities',
  XLB: 'Materials',
  XLRE: 'Real estate',
  XLC: 'Communication services',

  // Industry and thematic ETFs
  SMH: 'Semiconductors',
  XBI: 'Biotech',
  KRE: 'Regional banks',
  XOP: 'Oil & gas exploration',
  ITB: 'Home construction',
  XHB: 'Homebuilders & housing',
  XRT: 'Retail',
  IGV: 'Software',
  ARKK: 'Disruptive innovation (ARK)',
  JETS: 'Airlines',
  GDX: 'Gold miners',
  COPX: 'Copper miners',

  // Commodities
  GLD: 'Gold',
  SLV: 'Silver',
  USO: 'Crude oil',
  UNG: 'Natural gas',
  DBA: 'Agriculture',

  // Rates, credit and currencies
  TLT: 'Treasuries 20y+',
  IEF: 'Treasuries 7–10y',
  HYG: 'High-yield credit',
  UUP: 'US dollar',
  FXE: 'Euro',
  FXY: 'Japanese yen',

  // International equities
  EEM: 'Emerging markets',
  EFA: 'Developed markets ex-US',
  FXI: 'China large caps',
  EWJ: 'Japan',
  EWZ: 'Brazil',
  EWG: 'Germany',

  // Volatility and skew indices
  '^VIX': 'S&P 500 30-day volatility',
  '^VIX9D': 'S&P 500 9-day volatility',
  '^VIX3M': 'S&P 500 3-month volatility',
  '^VIX6M': 'S&P 500 6-month volatility',
  '^VVIX': 'Volatility of VIX',
  '^SKEW': 'S&P 500 tail skew',

  // Single names
  AAPL: 'Apple',
  MSFT: 'Microsoft',
  NVDA: 'Nvidia',
  GOOGL: 'Alphabet',
  META: 'Meta Platforms',
  AMZN: 'Amazon',
  TSLA: 'Tesla',
  AVGO: 'Broadcom',
  AMD: 'AMD',
  MU: 'Micron',
  QCOM: 'Qualcomm',
  INTC: 'Intel',
  ORCL: 'Oracle',
  CRM: 'Salesforce',
  ADBE: 'Adobe',
  NFLX: 'Netflix',
  SMCI: 'Super Micro Computer',
  ARM: 'Arm Holdings',
  LRCX: 'Lam Research',
  AMAT: 'Applied Materials',
  TXN: 'Texas Instruments',
  PLTR: 'Palantir',
  NOW: 'ServiceNow',
  SNOW: 'Snowflake',
  CRWD: 'CrowdStrike',
  PANW: 'Palo Alto Networks',
  NET: 'Cloudflare',
  SHOP: 'Shopify',
  UBER: 'Uber',
  JPM: 'JPMorgan Chase',
  BAC: 'Bank of America',
  WFC: 'Wells Fargo',
  GS: 'Goldman Sachs',
  MS: 'Morgan Stanley',
  C: 'Citigroup',
  SCHW: 'Charles Schwab',
  AXP: 'American Express',
  V: 'Visa',
  MA: 'Mastercard',
  UNH: 'UnitedHealth',
  LLY: 'Eli Lilly',
  JNJ: 'Johnson & Johnson',
  PFE: 'Pfizer',
  MRK: 'Merck',
  ABBV: 'AbbVie',
  AMGN: 'Amgen',
  ISRG: 'Intuitive Surgical',
  XOM: 'Exxon Mobil',
  CVX: 'Chevron',
  COP: 'ConocoPhillips',
  SLB: 'Schlumberger',
  OXY: 'Occidental Petroleum',
  FCX: 'Freeport-McMoRan',
  NEM: 'Newmont',
  BA: 'Boeing',
  CAT: 'Caterpillar',
  DE: 'Deere',
  GE: 'GE Aerospace',
  RTX: 'RTX',
  UPS: 'UPS',
  FDX: 'FedEx',
  HD: 'Home Depot',
  COST: 'Costco',
  WMT: 'Walmart',
  NKE: 'Nike',
  SBUX: 'Starbucks',
  MCD: "McDonald's",
  DIS: 'Disney',
  COIN: 'Coinbase',
  HOOD: 'Robinhood',
  DKNG: 'DraftKings',
  RIVN: 'Rivian',
  MSTR: 'MicroStrategy',
};

/** The exposure or company behind `symbol`, or `null` when this map does not know it. */
export function symbolName(symbol: string): string | null {
  return NAMES[symbol] ?? null;
}
