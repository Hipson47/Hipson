# Boundary for Hipson Studio, a future conversational client (design, not implemented)

Status: proposal for the next phase. Nothing here is built yet. Hipson Studio is a separate, optional client of
the Hipson core, not part of it. It does not revive `hipson chat` or the gateway; those are deprecated for the
failure modes in [DEPRECATIONS.md](DEPRECATIONS.md) (lost tool output and history, success reported after a
provider failure). Phase 2 (programmable harness hardening) used no model API: every live run went through the
runtimes' own subscriptions and CLIs. Whether Studio talks to a model through a paid API, or drives the same
native runtimes, is an owner decision for its first task (see below).

## Goal and constraint

A standalone chat application talks to a model through the Anthropic API and lets a user manage projects and
tasks, start authorized workflows, watch progress and read evidence. The Hipson core stays provider-independent:
**the chat app is one more client of Hipson, never part of it.** It must be possible to delete the chat app
without changing any core module, and the core must not import an API SDK.

## The boundary: the existing MCP server, plus one supervised launcher

The smallest stable boundary already exists: `hipson mcp serve` (stdio JSON-RPC) with the ledger tools
(`task.open`, `task.show`, `task.board`, `task.list`, `task.verify`, `task.close`, `task.findings`,
`task.events`, `task.activity`, `lead.resume`, ...; `hipson mcp serve --help` lists them all). Every write tool already requires
`approved: true`, and MCP launches no workers. The chat app should be an MCP client of that server and add only:

| Proposed tool | Contract | Why it is separate |
| --- | --- | --- |
| `flow.check` | `{spec}` -> `{valid, spec_sha256, spec}`; read-only (`hipson task flow --check`) | lets the model draft a spec and get exact validation errors before asking the user |
| `flow.start` | `{spec, approved}` -> `{task, pid}`; starts `hipson task flow --spec` as a detached, supervised process | workers run outside MCP, as today; the tool only starts the same CLI a lead would |
| `flow.resume` | `{task, spec, retry?, approved}` -> `{task, pid}` | same, with `--resume` / `--retry` |
| `flow.status` | `{task}` -> `hipson.task_flow_result`-shaped view derived from the ledger (`task.show` + `flow` events) | read-only progress; never inferred from the chat transcript |
| `agent.runtimes` | `{}` -> `hipson.agent_runtimes` | lets the model explain what is installed and verified, with evidence levels |

Everything else (findings, verification records, events, briefs) is already readable through `task.*`.

## Trust boundaries

1. **User -> chat model:** the model proposes; consequential operations (`flow.start`, `task.close`, anything
   with `approved`) need an explicit user confirmation in the chat UI. The UI, not the model, sets
   `approved: true`.
2. **Model -> Hipson:** tool arguments are untrusted input; the core already validates them (spec schema,
   check argv rules, redaction). The review policy is fixed at open. Declared checks can be replaced by the lead
   (`task checks --reason`, audited, refused in worker contexts), so the app must treat `task.checks` as a
   consequential write needing user approval; a running or resumed flow stops with `conflict` when they change.
3. **Hipson -> workers:** unchanged: `task run` supervision, claims, observation, `--env minimal` where chosen.
   The chat app never talks to worker CLIs directly.
4. **Evidence -> model:** worker summaries, verification output and repository text come back as data. The app
   must keep the `<untrusted_data>` wrapping the core already applies and must not place that text in the
   system prompt.

## Session model

- One chat conversation maps to zero or more ledger tasks; the mapping is stored by the app as
  `{conversation_id, task_ids}`. The ledger stays the source of truth for task state; the transcript is never
  used to reconstruct it (`lead.resume` / `task.show` are).
- Native worker sessions (`hipson agent exec`) are bound to `task/assignment` and are not visible to the chat
  model; it reads their outcome through the ledger and the flow result.

## Cost and usage controls

- Chat API spend is the app's: a per-conversation and per-day budget in the app config, checked before each
  request; the model's own usage (`usage` in API responses) is logged per turn.
- Workflow spend is the workers': `hipson agent exec` already records runtime usage (tokens, Claude's list-price
  estimate, which is not a subscription charge) in the flow's private `*.meta.json`; `flow.status` should sum it.
- `flow.start` requires the spec's `max_repairs`, so the worst-case number of worker runs is known before the
  user approves: `1 + max_repairs` builders plus reviews.

## Context strategy

Send the model the task view (`task.show`, bounded), the latest flow result and the open findings, not raw logs.
`lead.resume` already produces a deterministic, size-capped recovery text (4,096 bytes) for exactly this.

## What Phase 2 changed for this boundary

- Reviews in a flow read an immutable snapshot, so `flow.status` can report *which* candidate was reviewed
  (the `reviewed` events carry its revision and fingerprint) instead of trusting the transcript.
- `hipson agent runtimes` already separates documented, configured, authorized and verified capabilities; the
  proposed `agent.runtimes` tool should pass that through unchanged, including the documented gaps (e.g.
  Antigravity's plan mode does not prevent writes).
- Protected paths make a builder's change to the acceptance tests or runtime configuration a `needs_lead`
  stop: the client must surface it as a decision for the user, never resolve it itself.

## What a first Studio task needs

1. An Anthropic API key in the app's own environment (never in Hipson config, briefs or the ledger).
2. A thin client: Messages API with tool use, the five proposed tools plus the read-only `task.*` tools, and the
   MCP stdio client to `hipson mcp serve --project <repo>`.
3. Implement `flow.*` and `agent.runtimes` as MCP tools in `mcp_server.py` (small: they call the existing
   functions), with tests that `flow.start` refuses without `approved` and in a worker context.
4. Offline tests with recorded API responses before any paid call; then a small, budget-capped live run.

Out of scope until then: a UI, multi-user auth, remote execution.
