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

## Shipped

- **1.4: lead visibility and recovery.** Assignment activity and failure journal, the `task now` view, and
  `hipson lead resume` with explicit result acknowledgement.
- **1.5: acceptance bound to evidence.** `review` and `done` reference the candidate revision and the
  verification actually run; stale verification is flagged; false-completion e2e cases.
- **1.8: agent-facing reads and whole-task usage.** Compact reads with ranked next actions, the `lead` MCP
  profile, and recorded tokens and elapsed time per task. See the [1.8 release notes](release-1.8.md).

Details for each are in the release notes ([1.4](release-1.4.md), [1.5](release-1.5.md), [1.8](release-1.8.md)).

## Next

- **Known 1.8 limitations** ([release-1.8.md](release-1.8.md#known-limitations)): ledger writers fingerprint the
  project while holding the write lock, so a slow filesystem can make other writers time out; Git-ignored and
  excluded paths are neither observed as findings nor fingerprinted.
- **1.9:** make compact the general default read detail, as planned in the
  [task ledger docs](TASK_LEDGER.md#compact-projection-view_schema-1). The MCP tool profile is a separate
  setting; changing its default is reserved for a later release.
- **2.0:** remove the commands deprecated in 1.7: `autopilot`, `release claim`, `scheduler`, `chat`, `learn` and
  `hermes` ([DEPRECATIONS.md](DEPRECATIONS.md)).

## Later

- Delegation contracts for shared interfaces and the runtime under test (what path claims cannot see).
- Whole-task budget limits and enforcement.
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
