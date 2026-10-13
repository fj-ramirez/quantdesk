# Decisions made while the user was away (2026-10-13)

The user said: *"if a decision has to be made take the recommended one and write it somewhere
where I can review it later. if not recommended then defer it and continue working."*

**Taken** = the recommended option, applied. Reverse it by telling Claude which one.
**Deferred** = needs you; nothing was done beyond filing it.

| # | Decision | Status | What was done | Why |
|---|---|---|---|---|
| D1 | Scope | taken | COT only. Price action (T147's open registry write, the live structure read, the swing study) is untouched. | You said "work on cot". |
| D2 | Branch | taken | `feat/cot-positioning` is cut from `feat/t154-0dte-iwm`, not from `main`. | `main` lacks T142/T153/T154, and `TASKS.md` differs between the two. Cutting from `main` would conflict on merge. Merge T154 first, then this. |
| D3 | Which contract codes | taken | The minis (`13874A`, `209742`, `124603`), not the consolidated codes (`13874+` etc.). | TFF history from 2006 instead of 2010. The early-years caveat is written down in the plan. |
| D4 | COT `as_of` is early in shutdown and holiday weeks (T158) | **deferred** | Filed T158 only. The adapter is unchanged, and the study handles release dates itself. | Fixing `release_as_of` adds correct rows, but the early rows stay visible at their early `as_of`, because invariant 10 forbids overwrites. Whether to delete rows that record a *wrong* `as_of` (not a revision) is your call. Recommended when you decide: fix the rule, and delete the early rows in a one-off migration that documents the exception. |
| D5 | Which reports and groups | taken | TFF leveraged funds and asset managers for es, nas, dji and eur. Disaggregated managed money for gold and crude. Legacy net spec for nas, dji and eur. | These are the groups the literature reads. The dealer and producer/swap lines were left out to keep the universe and the study small. |
| D6 | Study price data | taken | Cash indices, GLD and `EURUSD=X` from Yahoo, 2003+, with an index carry adjustment and a roll spread. | `broker.bars` only reaches back to 2019. That is too few weekly extremes. |
| D7 | Study entry time | taken | The close of the first trading day *after* the release day. | No ambiguity about the FX daily bar's close against 15:30 ET. The cost is the Friday-to-Monday move. |
| D8 | Russell / `US2000` | taken | Not added. | It is a cash CFD with a 0.1-lot minimum, which you don't trade (memory: trades-futures-based-cfds). Russell e-mini TFF history on CME starts only in 2017. |
| D9 | Recording the 84 trials in `research.trials` | **deferred** | The CLI has `--record`, but it was not run. | Writing to the homeserver database from dev was denied last time (T147). Run it on the server. Until then the noise ceiling undercounts by 84. |
| D10 | How "the gate doubles the spread and the swap" treats a swap **credit** (a short on gold or EURUSD) | taken | Spread and swap *charges* are doubled. A credit is taken once, never doubled. | This is the conservative reading of the frozen text. Doubling a credit would make the doubled-cost gate easier to pass than the single-cost result. No trial's verdict depends on it: checked by re-running with credits doubled, and the same single trial (EURUSD Legacy follow 20 d, t 2.71) is still the only one with t > 2, and it still fails the OOS sign. |
| D11 | Baseline for gold and the indices | taken | Always-long and always-short gross R at the same holds was computed as a **diagnostic**, not a trial (+0.07 / +0.22 / +0.35R for gold). It is quoted in the Result and the search log. | This is the edge-research skill's criterion 5 ("not explained by buy-and-hold drift"). It was not one of the six gates in study.md, so it is reported beside them and decides nothing. |
| D12 | What the market-research skill says about COT | taken | "Context, not a signal": quote the percentile and its `value_date`, and do not predict a reversal. | That is what T156 found. |
| D13 | `follow` and `contrarian` as mirror images | taken (wording) | study.md says they are mirror images before costs. With a stop that is only approximately true, because the losing side is capped at −1R. The test pins the no-stop case. | It changes no number. It is noted here so the wording is not read as a claim about the results. |
| D14 | A positioning page in the terminal UI (phase 3 of the first proposal) | **deferred** | Not built. The Brief page already renders the backend brief, which now prints every COT series with its percentile. | It is worth building only if you want positioning charted over time. After T156 found no signal in it, I wouldn't spend the time without your say-so. |
| D15 | Git | taken | Six commits on `feat/cot-positioning`, one per step. **Not pushed, no PR.** | Pushing is outward-facing, so it waits for you. |
