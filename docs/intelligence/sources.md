# Approved free sources

Limits below were noted on 22 Nov 2026. Terms change: re-read the source's terms page and
rate limits before building or changing an adapter, and record the date you checked in the
adapter's docstring. Paid sources (Databento, paid Polygon tiers and similar) are parked
until the owner approves spending; mention that they exist, don't sign up.

## Official sources (use first)

| Source | What it gives | Access | Known limits and rules | History |
|---|---|---|---|---|
| SEC EDGAR filings feed | New 8-K, 10-Q, 10-K, Form 4 etc. as Atom | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom` | Max 10 requests/second across all SEC hosts. User-Agent with name and email required (`APEX_SEC_USER_AGENT`, reuse `marketdata/earnings.py`). No key. | Daily and full indexes back to 1993 under `/Archives/edgar/` give acceptance times for a rebuild |
| SEC submissions API | Every filing for a company with `acceptanceDateTime` | `https://data.sec.gov/submissions/CIK##########.json` | Same 10/s and User-Agent rules | Full company history |
| SEC XBRL company facts | Reported fundamentals (revenue, EPS, shares, debt) per filing | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | Same rules. Use each fact's `filed` date as published_at, never the period end. Restated values are new rows, not edits. | Roughly 2009 onward (XBRL era) |
| Federal Reserve | Press releases, FOMC statements | RSS at `https://www.federalreserve.gov/feeds/` | Public; poll politely (every 5 to 15 minutes is plenty) | FOMC calendar and statement archive pages |
| ECB | Press releases, policy decisions | RSS at `https://www.ecb.europa.eu/home/html/rss.en.html` | Public; poll politely | Press archive |
| Bank of England | News, rate decisions | RSS at `https://www.bankofengland.co.uk/rss` | Public; poll politely | News archive |
| BLS | Jobs, inflation release schedule and data | Release calendar (ICS) and API v2 | API v2 needs a free key; daily request caps apply (check current numbers) | Release calendars by year; past release dates are knowable in advance |
| BEA | GDP, PCE schedule and data | Release schedule page and API | Free key for the API | Release schedule archive |
| Company investor-relations RSS | Press releases | Each company's IR site | Only where the site's terms and robots.txt allow automated reading. Many IR sites are run by third parties with their own terms. | Usually none; forward only |

## Licensed free sources (check terms first)

| Source | What it gives | Known limits and rules |
|---|---|---|
| Alpaca news API | Headlines and summaries (Benzinga) with symbols, back to about 2015 | Needs the Alpaca paper keys already in the repo secrets. Rate limit around 200 requests a minute on the free plan. Personal research use; don't redistribute the text. |
| GDELT | Worldwide news events and tone, updated every 15 minutes | Free and open. The DOC API is rate limited (roughly one request every few seconds). Its timestamps are when GDELT saw the article, which is a fair first_seen_at. Noisy; good for broad themes, poor for single stocks. |

## Word lists and models

| Item | Rule |
|---|---|
| Loughran-McDonald finance word lists | Free for academic and personal research; commercial use needs a licence. Store the list version with every score. |
| AI summaries or scores | Only in `apex.ai`, optional, behind `get_provider()`. Store model name, prompt version and date with each score. |

## Do not use

- Scraped Yahoo Finance, Nasdaq.com, Google Finance or news-site pages: their terms forbid bots and their timestamps can't be trusted.
- Social media (X/Twitter, Reddit, StockTwits, Telegram) as facts or as a signal. Rumours are not news.
- Any source with no clear publish time, or that rewrites old items without saying so.
- Paid data, until the owner approves the cost.
