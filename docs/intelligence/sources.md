# Approved free sources

The full list of free feeds that could feed APEX's stock research, grouped by what they
tell us. Checked by web search on 23 Nov 2026; the build sandbox cannot reach these hosts,
so nothing here has been downloaded yet. Terms and limits change: re-read the source's
terms page and rate limits before building or changing an adapter, and record the date in
the adapter's docstring. Paid sources are parked (see the end) until the owner approves
spending.

"History" says whether a backtest can use the source honestly. **Archive** means past items
carry their real release or capture time. **Calendar** means dates were announced in
advance, so the past schedule is fair to use. **Forward-only** means start collecting now
and judge it later.

## Priority order for APEX

| # | Feed group | Why it matters for APEX's strategies | History |
|---|---|---|---|
| 1 | Economic calendar (FOMC, CPI, jobs, GDP) | Stand aside, or size down, on big announcement days; the cleanest first filter for the fund basket | Calendar |
| 2 | SEC filings (8-K earnings, Form 4, 13D/G) | Earnings and insider events for single stocks; already part-built in `marketdata/earnings.py` | Archive |
| 3 | FRED / ALFRED macro and stress series | Market-condition (regime) filters: VIX, yield curve, credit spreads, stress index | Archive (ALFRED vintages) |
| 4 | FINRA short-sale volume and short interest | Crowding and squeeze risk for mean-reversion entries | Archive |
| 5 | Alpaca news (Benzinga) | Headline flags and tone per symbol | Archive from 2015 |
| 6 | Treasury yields and auctions | Rates shocks; auction days | Archive |
| 7 | CFTC Commitments of Traders | Weekly positioning in index futures | Archive |
| 8 | GDELT | Broad world-news themes and tone | Archive (capture time) |
| 9 | Central bank and government press RSS | Live alerts and explanations on the dashboard | Mostly forward-only |
| 10 | Fama-French factors | Check a strategy isn't just a known factor in disguise | Archive (monthly updates) |

## 1. Company filings and events (SEC)

All SEC hosts: max 10 requests a second in total, User-Agent with name and email required
(`APEX_SEC_USER_AGENT`, reuse `marketdata/earnings.py`), no key.

| Feed | What it gives | Access | History |
|---|---|---|---|
| EDGAR latest filings | Every new filing as Atom, filterable by form type (8-K, 4, SC 13D...) | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom` | Forward; rebuild past from the daily index |
| EDGAR per-company feed | One company's filings as Atom | `browse-edgar?action=getcompany&CIK=<cik>&output=atom` | Same |
| Submissions API | Every filing per company with `acceptanceDateTime` | `https://data.sec.gov/submissions/CIK##########.json` | Archive, full history |
| Daily and full indexes | Every filing ever, by day | `https://www.sec.gov/Archives/edgar/daily-index/` and `full-index/` | Archive from 1993 |
| 8-K item 2.02 | Earnings releases (the real time results hit the market) | Filter the above by form 8-K, item 2.02 | Archive |
| 8-K items 1.01, 2.01, 5.02 | Big contracts, deals, CEO/CFO changes | Same | Archive |
| Form 4 | Insider buys and sells, filed within 2 business days | Same feeds, form 4 (XML inside) | Archive from 2003 |
| SC 13D / 13G | Activist or 5%+ holders | Same feeds | Archive |
| 13F | Big funds' quarterly holdings (45 days late) | Same feeds | Archive; slow, use filed date only |
| XBRL company facts | Reported fundamentals per filing | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | Archive from ~2009; use `filed`, not period end |
| XBRL frames | One fact across all companies for a period | `https://data.sec.gov/api/xbrl/frames/...` | Archive |
| XBRL financial-data RSS | Monthly archive of filings that include XBRL | `https://www.sec.gov/structureddata/rss-feeds` | Archive from 2005 |
| Fails-to-deliver | Shares not delivered, by symbol, twice a month | SEC "Fails-to-Deliver Data" page (zip files) | Archive; published with a lag, use publish date |
| SEC press releases and litigation RSS | Enforcement actions, trading suspensions | `https://www.sec.gov/about/secrss.shtml` lists them | Forward-only |

## 2. Market structure (who is short, who is positioned)

| Feed | What it gives | Access and limits | History |
|---|---|---|---|
| FINRA daily short-sale volume | Share of each day's volume that was short sales, by symbol | Free daily text files (Reg SHO daily files), next morning | Archive from 2009 |
| FINRA equity short interest | Shares sold short, twice a month | Free download and FINRA API (free account) | Archive; published ~2 weeks after settlement date, use publish date |
| Nasdaq Trader symbol directory | Every listed US symbol, ETF flag, test issues | `nasdaqlisted.txt` and `otherlisted.txt` from nasdaqtrader.com, updated daily | Forward-only; snapshot daily to build history |
| CFTC Commitments of Traders | Weekly positions in S&P, Nasdaq, Dow, VIX futures by trader type | `publicreporting.cftc.gov` (Socrata API, no key needed for light use) | Archive from 1986; Tuesday data released Friday, use Friday |

## 3. Macro, rates and market stress

| Feed | What it gives | Access and limits | History |
|---|---|---|---|
| FRED | 800,000+ series: VIX (`VIXCLS`), yields, credit spreads, unemployment, CPI | Free API key; about 120 requests a minute | Latest values only; **revised data is look-ahead** |
| ALFRED | The same series as they were known on each past date (vintages) | Same API with `realtime_start/end` | Archive: use this, not FRED, for backtests of revised data |
| Treasury daily yield curve | Official par yields, daily | home.treasury.gov interest-rate XML/CSV feeds | Archive from 1990 |
| Treasury Fiscal Data API | Auction results, upcoming auctions, debt | `https://api.fiscaldata.treasury.gov/`, no key | Archive; auction dates are a calendar |
| NY Fed Markets Data API | SOFR and other reference rates, Fed operations | markets.newyorkfed.org API, no key | Archive |
| Fed H.15 data feed | Selected interest rates | `https://www.federalreserve.gov/feeds/h15_data.htm` | Archive via FRED/ALFRED |
| OFR Financial Stress Index | One daily number for market stress, plus parts | financialresearch.gov download (also on FRED) | Archive; check for revisions |
| Atlanta Fed GDPNow | Running estimate of current-quarter growth | atlantafed.org download (also on FRED) | Archive of each update |
| BLS | Jobs, CPI, PPI | API v2 with free key (daily caps, about 500 queries); release calendar (ICS) | Calendar; values via ALFRED for vintages |
| BEA | GDP, PCE inflation | Free API key; release schedule page | Calendar |
| Census economic indicators | Retail sales, durable goods, housing starts | `api.census.gov` EITS time series; release calendar | Calendar |
| EIA | Weekly oil and gas inventories (moves energy stocks) | Free API key | Calendar (Wednesdays) + archive |

## 4. Central bank and government news (RSS)

Poll each every 5 to 15 minutes at most. All public.

| Feed | URL |
|---|---|
| Federal Reserve, all press releases | `https://www.federalreserve.gov/feeds/press_all.xml` (more on `/feeds`: monetary policy, speeches, testimony) |
| FOMC meeting calendar | federalreserve.gov FOMC calendars page (dates set a year ahead; calendar history is fair) |
| Regional Fed banks | e.g. Atlanta Fed `https://www.atlantafed.org/rss` |
| ECB press releases | ECB RSS page (`ecb.europa.eu/home/html/rss.en.html`) |
| Bank of England | `https://www.bankofengland.co.uk/rss` |
| Bank of Japan | boj.or.jp "What's new" RSS |
| US Treasury press releases | home.treasury.gov news (RSS) |
| Federal Register API | `https://www.federalregister.gov/api/v1/` (tariffs, rules, executive orders), no key |
| FDA press releases | fda.gov RSS (drug approvals move biotech stocks) |

## 5. News aggregators

| Source | What it gives | Rules |
|---|---|---|
| Alpaca news API | Benzinga headlines and summaries tagged with symbols, history from 2015, plus a live stream | Uses the Alpaca paper keys already in the repo secrets; about 200 requests a minute on the free plan; personal research use, don't redistribute text |
| GDELT DOC 2.0 and event files | World news themes and tone, every 15 minutes | Free and open; about one API request every few seconds; capture time is a fair first_seen_at; noisy for single stocks |
| Press-release wires (Business Wire, PR Newswire, GlobeNewswire) | Company announcements, often minutes before the 8-K | Their public RSS feeds only, where terms allow automated reading; check each before use |
| Company investor-relations RSS | Press releases | Only where the site's terms and robots.txt allow it; forward-only |

## 6. Research data

| Source | What it gives | Rules |
|---|---|---|
| Kenneth French data library | Daily and monthly factor returns (market, size, value, momentum, quality) | Free for research; updated monthly with revisions, so store each download as a dated version |
| Loughran-McDonald word lists | Finance-specific positive/negative/uncertain words for tone scores | Free for academic and personal research; commercial use needs a licence; store the list version with each score |

## Already in APEX

Price feeds are covered: Alpaca (IEX daily bars ~2020+), Tiingo (decades of daily stocks,
~50 requests an hour), Polygon (free tier), Coinbase, Kraken, Binance, Dukascopy CSV, and
SEC earnings dates (`marketdata/earnings.py`). AI scoring hooks: `apex.ai` only.

## Do not use

- Scraped Yahoo Finance, Nasdaq.com, Google Finance, Finviz, Investing.com or Seeking Alpha
  pages: their terms forbid bots and their timestamps can't be trusted.
- Social media (X/Twitter, Reddit, StockTwits, Telegram) as facts or as a signal.
- Free "economic calendar" websites and scraper services (Apify actors etc.): use the
  official calendars above instead.
- Option prices from exchange websites (Cboe pages are not a licensed feed).
- Any source with no clear publish time, or that rewrites old items without saying so.
- FRED's latest values in backtests of data that gets revised (use ALFRED).

## Paid, parked until the owner approves

Databento (futures and full-depth equities), paid Polygon tiers, Benzinga direct, options
data (ORATS, Cboe DataShop), alternative data (Quiver, Unusual Whales). Mention, don't
sign up.
