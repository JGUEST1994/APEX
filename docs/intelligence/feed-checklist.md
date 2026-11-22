# Checklists

## Adding a new feed

1. **Allowed?** The source is listed in [sources.md](sources.md), or you have read its terms,
   robots.txt and rate limits today and added it there with the date. No social media, no
   scraping against terms.
2. **Adapter** follows the `marketdata/providers` pattern: one module, an `httpx.Client`
   passed in (so tests can inject `httpx.MockTransport`), `ProviderError` on failure,
   the User-Agent the source asks for, and backoff on 429/503 that honours `Retry-After`.
3. **Store** each item once, keyed by the source's id (or a hash of URL + title when there is
   none). Columns at least: source, source_id, url, title, published_at, first_seen_at,
   fetched_at, content_hash, licence note. Symbols go in a link table with how the link
   was made (source-tagged, ticker match, CIK).
4. **Migration**: one new Alembic file in `src/apex/migrations/versions/`. Never edit an
   applied migration. Timestamps are timezone-aware UTC.
5. **Edits and duplicates**: if a source changes an item, insert a new version row with its
   own first_seen_at; never overwrite. Duplicates across sources are linked, not merged.
6. **Polling**: a new scheduler job type in `scheduler.py` (failing job is logged and audited
   and never stops the others; the job is idempotent).
7. **Dashboard**: every item or reason shows the source link, the timestamp, and a label:
   live, delayed or historical.
8. **Tests** (no network): parsing, duplicate and edited items, backoff, a missing User-Agent
   refused, and the look-ahead test below.
9. **Layering**: nothing in core, domain, db, marketdata, strategies, backtest, risk or
   execution imports `apex.ai` (`tests/test_architecture.py` checks this).
10. `ruff check src tests` and `pytest` pass; `docs/STATUS.md` updated.

## Point in time

- [ ] Every item has published_at (what the source claims) and first_seen_at (when APEX, or
      an archive with honest capture times, first saw it).
- [ ] Backtests use `known_at = max(published_at, first_seen_at)` and the item can only affect
      the first bar that opens after `known_at` (bar `ts` is the bar open time).
- [ ] A test proves an item cannot change a position on any bar before `known_at`.
- [ ] Historical rebuilds use archive capture or acceptance times (EDGAR `acceptanceDateTime`,
      GDELT capture time). If a source has no honest history, it is forward-only: say so and
      don't backtest with it.
- [ ] Fundamentals use the filing date, not the period end. Restatements are new rows.
- [ ] Stored scores are never re-scored in place. A new method is a new, versioned feature
      (for example `tone_lm_v2`), stored next to the old one.
- [ ] Symbol links use only what was known then (no today's ticker map applied to 2015 news
      without a dated mapping).
- [ ] Every filter, threshold and score variant tried is counted in the family try count.
- [ ] Anything designed after seeing results is also run on fresh markets nobody looked at.
