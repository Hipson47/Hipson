---
name: ai-coding-workflows
description: >
  Run AI-assisted development from spec to production. Covers spec-driven development,
  TDD-AI protocol, vibe coding guardrails, verification loops, Cursor/Claude Code
  workflows, long-horizon coding, mutation testing, and the Delegate-Review-Own
  operating model. Use when writing code with AI agents, setting up coding workflows,
  debugging AI-generated code quality, or designing CI gates for AI-produced PRs.
---

# AI Coding Workflows

## 1. Purpose
Structure AI-assisted development so agents produce correct, secure, testable code — not just code that compiles.

## 2. When to Use
- Writing code with AI agents (Claude Code, Cursor, Copilot, Devin)
- Designing spec-driven development workflows
- Setting up TDD-AI protocols
- Building CI gates for AI-generated code
- Long-horizon coding tasks (multi-file, multi-session)
- Reviewing AI-generated PRs

## 3. When NOT to Use
- Pure prompting with no code output → `system-prompt-architect/`
- Testing strategy design (the testing anti-patterns and frameworks) → `eval-security-guardrails/`

## 4. Core Concepts

### The Delegate-Review-Own Model [2026]
1. **Delegate**: AI agents handle first-pass — scaffolding, implementation, tests, docs
2. **Review**: Engineers verify correctness, security, and alignment
3. **Own**: Humans own architecture, tradeoffs, and outcomes

### The Verification Gap
Most developers don't fully trust AI code but only ~50% consistently verify before committing. AI agents optimize for "tests pass" not "risk reduced" — producing self-confirming suites (high coverage, low fault detection).

### Spec-Driven Development
Write specs BEFORE code. The spec is the contract AI must satisfy, not something derived from implementation.

### TDD-AI Protocol (from testy.md — canonical)
```
Phase 1: Human writes acceptance criteria (not code, not tests)
Phase 2: AI writes tests FROM CRITERIA (never from implementation)
Phase 3: AI writes implementation WITHOUT modifying tests
```

Tests are a **security boundary** — designed to resist collusion between implementation and test generation.

## 5. The AI Coding Workflow

```
1. SPEC      → Human writes acceptance criteria / requirements
2. PLAN      → AI generates implementation plan (review before proceeding)
3. TEST      → AI writes tests from spec (Phase 2 of TDD-AI)
4. IMPLEMENT → AI writes code to pass tests (Phase 3 — tests are frozen)
5. VERIFY    → Run tests, linter, type checker. AI fixes failures.
6. REVIEW    → Human reviews: architecture, security, edge cases
7. MUTATE    → Run mutation testing to verify test quality
8. MERGE     → Only if mutation score meets threshold
```

## 6. Long-Horizon Coding Patterns [Claude 4.x]

### Context Awareness
Claude 4.5+ tracks remaining token budget. Use this:
```
This is a very long task, so plan your work clearly.
Spend your entire output context working on the task —
just make sure you don't run out of context with significant
uncommitted work. Continue systematically until complete.
```

### Fresh Start > Compaction
Claude 4.5+ discovers state from filesystem effectively. When context window fills:
```
Start a new context. Begin by:
1. Call pwd — only read/write files in this directory
2. Review progress.txt, tests.json, and git logs
3. Run the fundamental integration test before implementing new features
```

### Incremental Progress
```
After each major milestone:
- Commit work with a descriptive message
- Update progress.txt with what's done and what remains
- Run tests to verify nothing regressed
```

## 7. Testing Anti-Patterns (from testy.md)

| Anti-Pattern | What Happens | Fix |
|-------------|-------------|-----|
| **Tautological test** | Test re-implements same logic as code | Assert requirements, not formulas |
| **Mock theater** | Test proves mocks work, not system | Use real DB/services where feasible |
| **Assertion-free test** | Test runs code but checks nothing | Every test needs explicit assertions |
| **Happy path only** | No edge cases, no error paths | Require error/boundary test cases in spec |
| **Coverage theater** | High line coverage, low fault detection | Use mutation testing as the real metric |
| **Synchronized mutation** | AI updates tests when fixing code | Freeze tests — implementation must not touch test files |

## 8. Tool Ecosystem [April 2026]

| Tool | Strength | Best For |
|------|----------|----------|
| **Claude Code** | 4% of GitHub commits. Terminal-first, long-horizon, 10+ file changes | Complex refactors, architecture work |
| **Cursor** | Deep context awareness via codebase indexing | Day-to-day development, exploration |
| **GitHub Copilot** | Agent mode with self-healing. Claude 4.5 + Gemini on Enterprise | Teams already in GitHub ecosystem |
| **Augment Code** | Semantic indexing + dependency graphs across services | Large monorepos, polyglot codebases |
| **Devin 2.0** | End-to-end: issue → merged PR | Fully autonomous tasks |

## 9. Security Considerations

### Vibe Hacking Risks (from Top Findings)
- AI-generated code creates "illusion of security" — looks correct, misses subtle flaws
- Example: AI builds 2FA app without rate limiting; even when prompted to fix, introduces implementation gap
- Remote AI coding environments (Cursor + Remote-SSH) can be pivot points for attackers
- Treat AI agents as untrusted collaborators: verify, don't trust

### CI Gates for AI Code
```
Required gates before merge:
1. All existing tests pass (not just new tests)
2. Type checker passes (strict mode)
3. Linter passes (no warnings suppressed)
4. Mutation testing score ≥ threshold
5. Security scan (SAST) passes
6. Human approval on architectural changes
```

## 10. Failure Modes
1. **Self-confirming tests** — AI writes test + impl, both agree, both wrong. Fix: TDD-AI protocol.
2. **Test modification during fix** — AI changes tests to make them pass. Fix: freeze test files.
3. **Context loss on long tasks** — agent forgets earlier decisions. Fix: progress.txt + git log + fresh start.
4. **Security blind spots** — AI misses rate limiting, auth bypass. Fix: security scan in CI + human review.
5. **Vibe coding without verification** — "it works" without tests. Fix: require spec + tests before implementation.

## 11. Cross-Links
- Testing frameworks and anti-patterns → `eval-security-guardrails/`
- Agent architecture for coding → `agentic-rag-orchestration/`
- System prompt for coding agent → `system-prompt-architect/` (Example 5)
- Backend/frontend patterns → `fullstack-2026/`

## 12. Source Basis
New Prompting Techniques (Technique Cards), testy.md (TDD-AI protocol, anti-patterns), Top Findings (security), Delta §7 (coding agents, Delegate-Review-Own), Delta §2 (Claude 4.x long-horizon patterns).

## 13. Freshness Notes
`[FRESHNESS: April 2026]` Claude Code at 4% of GitHub commits. Devin 2.0 at $73M ARR. Monitor for: Claude Code teams GA, new agent modes in Cursor/Copilot, mutation testing tool integrations.
---

# AI Coding Workflows — Examples

## Example 1: Bad vs Better — TDD-AI

**Bad (self-confirming):**
```
"Write a discount function and its tests."

# AI produces both — tests mirror implementation, both miss cap rule
def discountTotal(subtotal, pct):
    return subtotal - subtotal * (pct / 100)

def test_discount():
    assert discountTotal(200, 10) == 200 - 200 * (10/100)  # tautological!
```

**Better (TDD-AI protocol):**
```
Phase 1 — Human acceptance criteria:
  "Discount is capped at $50. Result rounded to cents."

Phase 2 — AI writes tests FROM criteria (no implementation yet):
  def test_caps_discount_at_50():
      assert discountTotal(1000, 10) == 950.00  # 10% = $100, cap → $50
  
  def test_rounds_to_cents():
      assert discountTotal(0.03, 10) == 0.03  # $0.003 rounds away

Phase 3 — AI writes implementation. Tests are FROZEN:
  def discountTotal(subtotal, pct):
      discount = min(subtotal * (pct / 100), 50.00)
      return round(subtotal - discount, 2)
```

---

## Example 2: Bad vs Better — Long-Horizon Coding

**Bad:**
```
"Refactor the entire authentication system."
# Agent starts, loses context at turn 15, redoes work, contradicts earlier decisions
```

**Better:**
```
"Refactor the authentication system.

Before starting:
1. Read progress.txt for any prior work
2. Review the git log for recent changes
3. Run the existing test suite

Plan your work in progress.txt:
- Phase 1: Extract auth middleware into separate module
- Phase 2: Replace JWT with session-based auth
- Phase 3: Add rate limiting to login endpoint
- Phase 4: Update all tests

After each phase: commit, update progress.txt, run tests.
If context window fills, commit and we'll continue in a new session."
```

---

## Example 3: Spec-Driven Development

```
# SPEC (human-written, before any code)

## Feature: User Search API
### Requirements:
1. GET /api/users/search?q={query}&page={n}&limit={n}
2. Searches by name (partial, case-insensitive) and email (exact)
3. Returns paginated results with total count
4. Empty query returns 400 Bad Request
5. Limit capped at 100; default 20
6. Results sorted by relevance, then alphabetically
7. Response time < 200ms at p95 for 1M users

### Edge cases to handle:
- SQL injection in query parameter
- Unicode characters in names
- Deleted/deactivated users excluded from results
- Rate limit: 30 requests/min per API key

# INSTRUCTION TO AI:
Write tests for this spec first. Do not write any implementation.
Tests must cover all 7 requirements and all 4 edge cases.
```

---

## Example 4: Mutation Testing Gate

```
# CI pipeline configuration

steps:
  - name: "Run tests"
    run: pytest --tb=short
    
  - name: "Mutation testing"
    run: |
      mutmut run --paths-to-mutate=src/
      mutmut results
      # Gate: mutation score must be ≥ 80%
      SCORE=$(mutmut results | grep "Killed" | awk '{print $2}')
      if [ "$SCORE" -lt 80 ]; then
        echo "Mutation score $SCORE% < 80% threshold"
        exit 1
      fi
    
  - name: "Security scan"
    run: bandit -r src/ -ll
    
  - name: "Type check"
    run: mypy src/ --strict
```

---

## Example 5: Claude Code Long-Horizon Setup

```xml
<system_prompt_for_coding_agent>
  You are a Senior Python Engineer working on a FastAPI backend.

  <process>
    1. Read progress.txt and git log before starting
    2. Plan changes in progress.txt before implementing
    3. Write tests before implementation (TDD)
    4. Implement incrementally — commit after each logical unit
    5. Run tests after every change
    6. If tests fail, fix without modifying test files
    7. Update progress.txt after each milestone
  </process>

  <constraints>
    - Never modify test files to make failing tests pass
    - Always use async for I/O operations
    - Type hints required on all function signatures
    - Handle errors with Problem Details (RFC 9457)
    - Commit messages: conventional commits format
  </constraints>

  <context_management>
    This is a long task. Plan your work to avoid running out
    of context with uncommitted changes. When approaching
    context limits, commit work and summarize state in progress.txt.
  </context_management>
</system_prompt_for_coding_agent>
```

---

## Example 6: Security Review of AI-Generated Code

```
Review this AI-generated authentication endpoint for security issues.

Check specifically for:
1. Rate limiting on login attempts (missing = critical)
2. Timing-safe comparison for passwords/tokens
3. Session fixation prevention
4. CSRF protection on state-changing endpoints
5. Input validation (SQL injection, XSS in error messages)
6. Secrets not hardcoded or logged

For each issue found:
- Severity: Critical / High / Medium / Low
- Location: file and line
- Fix: concrete code change
```

---

# AI Coding Workflows — Checklist

## Pre-Flight
- [ ] Acceptance criteria / spec written by human (not AI)
- [ ] Target architecture decided before coding starts
- [ ] Tool selected (Claude Code / Cursor / Copilot) based on task scope
- [ ] progress.txt initialized with plan
- [ ] Existing tests pass before any changes

## In-Flight
- [ ] Tests written FROM spec, not from implementation
- [ ] Test files frozen after Phase 2 — implementation must not modify them
- [ ] Commits after each logical unit (not one giant commit)
- [ ] Tests run after every change
- [ ] progress.txt updated at each milestone
- [ ] No new dependencies introduced without justification

## Final Review
- [ ] All tests pass (existing + new)
- [ ] Type checker passes (strict mode)
- [ ] Linter passes (no suppressed warnings)
- [ ] Mutation testing score ≥ 80%
- [ ] Security scan passes (SAST)
- [ ] Human reviewed: architecture, auth, error handling, edge cases
- [ ] No self-confirming test patterns (tautological, mock theater, assertion-free)

## Top 5 Failure Modes
1. **Self-confirming tests** — AI writes tests that mirror implementation logic
2. **Test modification** — AI changes tests during bug fixes
3. **Context loss** — long task, agent forgets earlier decisions
4. **Security blind spots** — missing rate limiting, auth bypass, injection
5. **Vibe coding** — shipping "it works" without spec or tests
