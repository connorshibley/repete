# Judge calibration history

`knowledge/judge_calibration.json` is the marginal distribution
`src/judge_model.py` applies to every simulated entry when a spec declares
`judge_model: true`. Changing it changes what those specs measure.

**It is not covered by the pre-registration hash.** A spec freezes its own
overlay, its snapshot sha256 and its aux shas — but nothing in
`research/registrations.jsonl` references this file. Grep it: zero hits. So the
guarantee `scripts/run_gate.py` offers is *"the overlay has not moved"*, not
*"the measurement has not moved."* `config.yaml` sits outside the hash for the
same reason (`bt.load_config()` reads it from the working directory at run
time).

That is why this file exists. Until the calibration is hashed into the specs
that consume it, a written record is the only thing standing between a
regenerated calibration and a silently re-meant archive.

---

## 2026-08-21 — degraded rows excluded, window extended

**Why:** the audit found that `scripts/calibrate_judge.py` had no filter for
fallback verdicts. When the judge is unreachable the code emits
`{"verdict": "approve", "scale": 1.0}` — and *always* `1.0`, because a fallback
is not a judgement. Those rows entered the haircut and could only ever pull it
toward "the judge cuts nothing."

**How much of the sample was fallback: 36 of 164 rows, 22%.**

### The move, decomposed

Two things changed at once, so they are separated here rather than reported as
one number. Arm A reproduces the previously committed file exactly, which is
what validates the decomposition.

| arm | window | filter | n | mean_scale |
|---|---|---|---|---|
| A | to 2026-07-28 | none | 164 | **0.7294** ← previously committed |
| B | to 2026-07-28 | degraded excluded | 128 | 0.6508 |
| C | to 2026-08-19 | none | 301 | 0.6664 |
| D | to 2026-08-19 | degraded excluded | 250 | **0.5952** ← now committed |

- **filter effect alone (A→B): −0.0786**
- **more data alone (A→C): −0.0630**
- **combined (A→D): −0.1342**

Both push the same way, and the filter is the larger of the two. The previously
committed file was also stale on its own terms: `observed_to` read 2026-07-28
against a ledger that then ran to 2026-08-19.

### Other fields

| field | before | after |
|---|---|---|
| `mean_scale` | 0.729375 | 0.595188 |
| `downsize_rate` | 0.58125 | 0.857741 |
| `veto_rate` | 0.02439 | 0.044 |
| `n_judged_buys` | 164 | 250 |
| `observed_span_days` | 12 | 34 |
| `source_ledger_sha256_16` | f7cc2b35b81ec117 | 9a23fcfb85071061 |
| `n_degraded_excluded` | *(field did not exist)* | 51 |

`n_degraded_excluded` is new and is emitted deliberately: an exclusion nobody
can see is indistinguishable from a filter that never fired.

### What this invalidates

**The 55 specs declaring `judge_model: true` will not reproduce their recorded
verdicts against this calibration.** Their verdict rows in
`research/verdicts.jsonl` stand as a record of what was measured *at the time*,
with the calibration then in force — they are not withdrawn, and no pass
becomes a fail by fiat. But re-running any of them now scores a different,
larger haircut, and a re-run that disagrees with its own history is expected
rather than alarming.

Affected: `s43a`–`d`, `s44a`–`d`, `s48a`–`d`, `s50a`–`d`, `s51a`–`c`,
`s53a`–`d`, `s54a`–`d`, `s57a`–`d`, `s58a`–`h`, `s59a`–`h`, `s62a`–`h`.

**§72 is NOT affected** — all four of `s72a`–`d` are `judge_model: false`, so
the project's only EDGE pass on survivorship-certified data never consumed this
file at all. That is what makes the planned §75 re-measurement clean: it is the
first judge-on measurement of that claim, so it has no judge-on history to stay
comparable with, and it runs on the corrected number.

**The direction matters for §75.** A mean scale of 0.5952 with a 4.4% veto rate
is roughly a **40% position haircut**, not the ~27% the pre-audit file implied.
Against §72's thinnest margins over SPY (+3.78pp and +8.51pp), that is a
materially bigger lever than expected — which raises, not lowers, the value of
running the re-test with a reading rule frozen in advance.

### Decision record

Regenerating immediately was the owner's explicit call (2026-08-21), taken over
the more conservative option of landing the code fix and deferring the
regeneration to its own registered METHOD claim. This document is the
mitigation that keeps it auditable without one.

---

## Before 2026-08-21

The file was written twice: `af3c906` (§29/§31) and `9864c5d` (Wave 2,
divergences #8/#11/#12, 2026-07-29). No record was kept of what either
regeneration changed, which is the gap this file now closes going forward.
