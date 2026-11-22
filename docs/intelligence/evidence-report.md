# Evidence report template

Copy this into the PR description and a short plain-language version into the report to
the owner. Fill every line; write "not done" rather than leaving one out.

```
## Evidence report: <idea name, e.g. "MR-IBS-BASKET-001 skips days after Fed decisions">

What was tested
- Base strategy: <id> on <markets>, <timeframe>, <date range>
- Added layer: <news filter / scanner condition / fundamental filter>, version <v>
- Data: <sources>, history from <date>; forward-only parts: <none / list>
- Research run: <GitHub Actions run id>, code <commit>

Tries counted
- Variants tried (filters, thresholds, word lists, windows): <n>, all listed below
- Family try count used in the luck test: <n>

Results (same markets, same dates, with vs without the layer)
| | Without | With |
|---|---|---|
| Unseen trades | | |
| Luck test (DSR) | | |
| Profitable folds | | |
| Worst drop | | |
| Gates passed | | |

Fresh-market check (markets nobody looked at while designing)
- Markets: <list>   Result: <pass / fail, numbers>

Point-in-time checks
- Look-ahead test: <test name>, passing
- Items with first_seen_at later than published_at: <share>, handled by using the later

Verdict
- <helps / no difference / hurts>, and why, in one or two sentences
- Decision needed from the owner: <none / what and why>
```

Plain-language version for the owner: one short paragraph saying what was tried, whether it
made the strategy safer or more profitable on markets it hadn't seen, and what (if
anything) needs his decision.
