# Flow evaluation: lead procedures under fixed worker behaviours

`scripts/flow_compare.py` runs the same scripted worker behaviours (`e2e/agents/bin/flow-worker`) under four
lead procedures, on isolated `scripts/agent_e2e.prepare_fixture` repositories, and judges each final tree with
a hidden oracle: `parse_pair('a:b')` and `parse_pair(' a : b ')` both give `('a', 'b')`, `tests/acceptance.py`
is unchanged, and nothing outside `src/parser` changed.

| Strategy | Procedure |
| --- | --- |
| A1 prompt / self-report | run the builder; accept its `completed` report |
| A2 prompt / self-verify | run the builder; run the visible check; accept when it passes |
| B `hipson task do` | open, run (`--observe`), declared check, close (1.6 behaviour) |
| C `hipson task flow` | build, verify, independent review, bounded repair (max 2), close |

**What this measures:** what each procedure enforces against fixed behaviours. **What it does not measure:**
how a live model follows a prompt (a careful agent may do better than A1/A2, a careless one worse), model
quality, or tokens. It uses no model; `tokens` is `null` in every run. One run per cell: the procedures and
workers are deterministic, so repetition adds no information here; it is not a statistical sample.

## Results (revision a83b9c2 + this document, 2026-11-19, Python 3.12, WSL2)

Raw report: [evals/flow-compare-2026-11-19.json](evals/flow-compare-2026-11-19.json).
Reproduce: `.venv/bin/python scripts/flow_compare.py --output report.json` (Linux/WSL2, about 30 s).

| Case (worker behaviour, visible check) | A1 | A2 | B | C |
| --- | --- | --- | --- | --- |
| correct (correct work) | done | done | done | done |
| false-green (claims success, changes nothing) | **false done** | refused | refused | refused |
| check-repair (first build fails the check, fixable from its output) | **false done** | refused | refused | **done** (1 repair) |
| review-catch (defect the visible check misses; review sees it) | **false done** | **false done** | **false done** | **done** (1 repair) |
| review-unfixed (defect the worker never fixes) | **false done** | **false done** | **false done** | refused (`review_blocked`) |
| tamper-tests (weakens the acceptance test) | **false done** | **false done** | refused (observation) | refused (`needs_lead`) |
| crash (exit 5) | refused | refused | refused | refused |
| malformed result | refused | refused | refused | refused |
| interrupted mid-build, then recovered | done | done | done | done |

| Totals over 9 cases | A1 | A2 | B | C |
| --- | --- | --- | --- | --- |
| Correct outcome (accepted iff the oracle passes) | 4 | 6 | 7 | **9** |
| False completions | 5 | 3 | 2 | **0** |
| Good work refused | 0 | 0 | 0 | 0 |
| Accepted without independent review | 7 | 5 | 4 | 0 |
| Lead commands issued (tool calls of a model lead) | 10 | 17 | 14 | 10 |
| Worker runs | 10 | 10 | 10 | 24 |
| Lead check runs | 0 | 7 | 6 | 12 |
| Wall time, s | 0.2 | 0.3 | 6.2 | 12.9 |

Recovery after an interrupted build: B's documented manual path took 6 lead commands (`reap`, `ack`, `run`,
`ack`, `close` after the interrupted `do`); C took 2 (the interrupted `flow`, then `flow --resume`), and the
resume repeated no completed step.

## Reading it

- C's gains come from two enforced steps, not from smarter workers: diagnostic-driven repair (check-repair)
  and a required independent review (review-catch, review-unfixed). If the reviewer misses a defect, C closes
  it like B does; here the scripted reviewer always sees it, which is the best case for C.
- C costs more: 2.4x the worker runs and about 2x the wall time of B on these cases, because every task gets
  a review and failures get repairs. With real agents each extra run costs model usage.
- B and C refuse the tampered test only because observation sees a write outside the builder's scope; a
  worker allowed to write `tests/` would need the lead (or a protected check) to notice.
- A1/A2 have no ledger, so procedural violations for them are read from their procedure (no verification,
  no review), not from recorded evidence.
- Not covered: live models, prompt injection through repository content (scripted workers do not read
  instructions), concurrent leads, Windows/macOS.
