# Roadmap

Hipson's main user is the lead agent. The roadmap is ordered by two inputs: the friction the lead
hits while running real work through Hipson, and published evidence about where lead/worker coding
agents fail. Every item ships with an agent e2e scenario or metric that shows whether it helps.

Research basis (October 2026): two independent literature and practitioner scans plus an adversarial
synthesis that re-opened the 15 most consequential sources; the lead re-checked the key ones. The
strongest, most repeated failure is **false completion**: work accepted without independent,
current verification ([MAST](https://arxiv.org/abs/2503.13657),
[Anthropic, long-running harnesses](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)).
Next come **lost state across continuations** and **results that arrive but are not consumed**
([anthropics/claude-code#102262](https://github.com/anthropics/claude-code/issues/102262)).
Popular ideas with weak or mixed evidence are pilots, not defaults: repository context files did not
improve success and raised cost by over 20% ([Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)),
and output counts or perceived speed are poor productivity measures
([METR RCT](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)).

## 1.4: lead visibility and recovery

- **Assignment activity and failure journal.** The runner records heartbeats, last output and last
  completed action as bounded, redacted, untrusted observations; when a worker ends without a
  result it records what exists (exit status, changed files, `git diff --stat`, log tail) and the
  uncertainty, never an invented outcome. Stale progress never releases claims.
- **One "now" view.** Active assignments with last activity, pending worker results, tasks that
  need a lead decision or cleanup, project filter, commit subjects next to full SHAs.
- **`hipson lead resume`.** Deterministic, read-only state for a lead after context loss: open
  tasks, active owners, results not yet acknowledged, open blocking/major findings, failed
  attempts, decisions, verification freshness. Worker results need an explicit acknowledgement, so a
  delivered-but-unread result stays visible.
- Measured by new agent e2e scenarios: a lead resuming mid-task, a worker that dies without a
  result, a result that arrives while the lead is busy.

## 1.5: acceptance bound to evidence

- `review` and `done` reference the candidate revision and the verification actually run
  (command, exit, time, revision); stale verification is flagged.
- Agent e2e adds false-completion cases: zero-exit incomplete work, altered tests, verification run
  on an older revision; the reference solution must pass and the broken one must fail.

## Later

- Delegation contracts for shared interfaces and the runtime under test (what path claims cannot see).
- Whole-task budgets: attempts, review and repair rounds, elapsed time, usage per accepted task.
- Finding adjudication: reproduction evidence and recorded reasons before a finding blocks work.
- Pilot: lead-approved lessons from findings injected into briefs, with provenance, expiry and
  recurrence tracking; shipped only if recurrence drops without extra cost.
- Pilot: explainable worker recommendations from verified ledger outcomes, abstaining when history
  is sparse; compared against fixed roles on held-out tasks.

## Not planned

- A replacement for the transactional ledger with free-form agent swarms.
- Automatic promotion of retrospectives into shared instructions or skills.
- Treating another model's review as a correctness gate on its own.
- Worker selection from raw pass rates, PR counts or model reputation.
- A learned summarizer before the deterministic resume baseline exists.
