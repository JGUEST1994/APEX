# NightShift morning report — 23 Nov 2026 (run started 23:37 UK, 22 Nov)

## Executive summary
APEX is in very good health. All 771 tests pass on both databases, the code-style check is
clean, and no known security holes were found in the libraries it uses. NightShift found one
problem worth fixing: one research strategy (the crypto weekend idea, MOM-WKND-001) uses a
pandas feature that pandas will delete in its next major version. It has been fixed in a pull
request, together with a guard so the tests catch the next one early. Nothing else needed work.

## Needs you
- Look at and merge (or ask me to merge) **PR #104**: https://github.com/jguest1994/apex/pull/104
  It changes one line of the strategy, two lines of a test, and adds the guard to the tests.
- No approval requests are pending.

## Overall health: 100 / 100 (Healthy)
Deductions: none.
Not measured (no deduction): type check (none set up in the project), build (Docker is not
available here; GitHub's own `docker` check covers it on every pull request).

## Critical findings
None.

## Bugs found
- **NS-001 (confirmed, priority 9):** MOM-WKND-001 uses pandas `DatetimeIndex.weekday`, which
  pandas 3 warns about 19 times per test run and pandas 4 removes. Because APEX installs the
  newest pandas, a fresh install after pandas 4 ships would crash that strategy and fail 7 tests.

## Bugs resolved (verified)
- NS-001 → CH-001, PR #104. Verified: the guard fails on the old code (7 failed) and passes with
  the fix (287 passed); full suite on SQLite + PostgreSQL 771 passed; lint clean.

## Improvements made
- PR #104: https://github.com/jguest1994/apex/pull/104 (not merged).

## Before and after
| Measure | Before | After |
|---|---|---|
| Affected tests with pandas-4 warnings as errors | 7 failed, 280 passed | 287 passed |
| Full suite | 771 passed, 4 warnings | 771 passed, 1 warning |
| pandas-4 warnings per run | 19 | 0 |

## Performance
Full test suite: 5 min 45 s locally for both databases (771 tests). Slowest test: the
multi-account paper restart test, ~25 s per database (NS-003, idea only; paper code is a
protected area).

## Security
pip-audit: 0 critical, 0 high, 0 moderate, 0 low. No secret-like strings in the code. The
dashboard never uses `innerHTML`; the server listens on 127.0.0.1 by default.

## Lean Six Sigma
- Capability: 773 automated checks (771 tests + lint + audit), 0 defects. Yield 100%, DPMO 0,
  sigma capped at 6.0.
- Pareto of findings: 1 confirmed reliability issue (fixed), 1 small upkeep item, 1 speed idea,
  1 false positive. No lint problems to rank.
- Flow (last 14 days of `main`): 105 commits over 3 active days, 4% fix commits, 0 reverts.
  0 open pull requests before this run.
- Root cause (NS-001): strategy reads `weekday` → pandas renamed it `day_of_week` → no upper
  version cap, so installs get the newest pandas → warnings never failed CI → nobody would
  notice until it broke. Control: warnings of this kind now fail the tests.

## Outstanding recommendations
- NS-002: Starlette (the web toolkit behind the API) now wants `httpx2` for its test client.
  Only tests are affected. Switching needs a change to the dependency list, so it waits for you;
  not urgent.
- Consider pinning library versions for Jake's PC installs (a lockfile or upper caps), since
  every fresh ZIP install takes the newest versions of everything. Needs your OK.

## Next-run priorities
1. NS-002 (priority 5) if you approve the dependency change.
2. NS-003 (priority 2) test speed, only with your OK for the paper-trading tests.
