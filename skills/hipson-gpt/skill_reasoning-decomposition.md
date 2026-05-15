---
name: reasoning-decomposition
description: >
  Select and apply the right reasoning framework for any task. Covers CoT variants
  (ThoT, GoT, SoT, PS+), CQoT, cognitive verification triggers, and the critical
  distinction between reasoning-native models (o1, DeepSeek-R1) and standard LLMs.
  Use when choosing a thinking strategy, debugging reasoning failures, or calibrating
  reasoning effort.
---

# Reasoning Decomposition

## 1. Purpose
Choose the right reasoning framework for each task. Avoid both under-thinking (shallow answers) and over-thinking (verbose noise).

## 2. When to Use
- Complex multi-step problems (math, logic, architecture)
- Tasks requiring synthesis from multiple sources
- Debugging why a model gives wrong answers despite correct context
- Calibrating reasoning effort (low/medium/high)

## 3. When NOT to Use
- Simple factual lookups
- Creative writing where structure kills flow
- Reasoning-native model on a task in its training distribution — just state the problem clearly

## 4. Core Concepts

### The Reasoning Model Bifurcation [2026]

**Standard LLMs** (GPT-4o, Claude Sonnet, Gemini Flash): Need explicit reasoning guidance.

**Reasoning-native models** (o1, DeepSeek-R1, Claude extended thinking): CoT is internalized via RL. Adding "think step-by-step" is redundant or harmful.

### Framework Selection

| Framework | Structure | Best For | Skip When |
|-----------|-----------|----------|-----------|
| **Direct** | None | Simple queries; reasoning models on familiar tasks | — |
| **CoT** | Linear | Math, logic, standard LLMs | Reasoning-native models |
| **PS+** | Plan → Execute | Multi-step math, code architecture | Simple tasks |
| **ThoT** | Linear thread | Long-context, fragmented RAG data | Short, clean contexts |
| **GoT** | DAG | Deep research, multi-source synthesis | Single-source tasks |
| **SoT** | Parallel | Long-form content generation | Short answers |
| **CQoT** | Adversarial | Critical reasoning, legal, medical | Creative tasks |

### CQoT (Critical-Questions-of-Thought)
Forces each reasoning step through Toulmin components: Data → Warrant → Backing → Rebuttal. Prevents the "plausibility trap" where fluent chains are factually wrong.

### Cognitive Verification Triggers
Short cues ("let me verify", "hold on") that activate verification attention heads. More effective than "think step-by-step" for reasoning-native models.

## 5. Decision Rules

```
IF reasoning-native model:
  → State problem + constraints clearly. Set reasoning effort.
  → DO NOT add "think step-by-step"

IF standard LLM AND task is:
  simple math/logic    → CoT
  multi-step planning  → PS+
  long messy context   → ThoT
  deep research        → GoT
  long-form content    → SoT
  critical decisions   → CQoT
```

## 6. Prompt Patterns

**PS+**: "First understand the problem and devise a plan. Then execute step by step. Extract all variables before computing."

**ThoT**: "Walk me through this in manageable parts, summarizing and analyzing as we go."

**GoT**: "Generate 3 approaches → Evaluate each → Combine the best aspects into a final solution."

**CQoT**: "For each step: state DATA, WARRANT, and REBUTTAL. Only proceed if the rebuttal is addressed."

**SoT**: Stage 1: "Write a concise outline." Stage 2 (parallel): "Expand point N." Stage 3: Concatenate.

## 7. Failure Modes
1. **Over-thinking simple tasks** — reasoning model wastes tokens. Fix: set effort to low.
2. **Skipping steps** — standard LLM with no decomposition. Fix: add PS+ framing.
3. **Plausibility trap** — fluent but wrong chain. Fix: CQoT with forced rebuttals.
4. **Long-context failure** — attention dilution. Fix: ThoT to thread fragments.
5. **SoT incoherence** — vague skeleton. Fix: more detailed outline in Stage 1.

## 8. Cross-Links
- Prompt structure → `system-prompt-architect/`
- Verification of reasoning output → `eval-security-guardrails/`
- Reasoning in agent loops → `agentic-rag-orchestration/`

## 9. Source Basis
PDF §2, Standards.md §1-3, Research.md Ch.1 (CREST/CQoT), Semantic Anchors, Delta §2.

## 10. Freshness Notes
`[FRESHNESS: April 2026]` Reasoning effort parameters are model-specific. Check latest API docs. DSPy GEPA can automate strategy selection.
---

# Reasoning Decomposition — Examples

## Example 1: Bad vs Better — CoT for Reasoning Model

**Bad (for o1/R1):**
```
Think step by step. Break this into sub-problems.
First analyze the requirements, then design the solution,
then implement it carefully, checking each step.

What is the optimal data structure for a real-time leaderboard
supporting 10M concurrent users?
```

**Better (for o1/R1):**
```
Design a data structure for a real-time leaderboard.
Constraints:
- 10M concurrent users
- Read latency < 5ms at p99
- Write latency < 20ms at p99
- Support: get_rank(user_id), get_top_k(k), update_score(user_id, delta)
- Memory budget: 64GB
Compare at least 2 approaches with complexity analysis.
```

**Why:** Reasoning-native models have internalized step-by-step thinking. Adding explicit CoT instructions wastes tokens and can interfere with their learned patterns. Instead, specify constraints tightly.

---

## Example 2: Bad vs Better — PS+ for Standard LLM

**Bad:**
```
Calculate the total cost of a 3-day conference for 150 attendees
with catering, venue, AV equipment, and speaker travel.
```

**Better:**
```
Calculate the total cost of a 3-day conference for 150 attendees.

First, understand the problem and extract all variables:
- Venue: $5,000/day
- Catering: $45/person/day (breakfast + lunch)
- AV equipment: $2,000/day
- Speaker travel: 5 speakers × $1,200 average

Then devise a plan: calculate each category, sum subtotals,
add 15% contingency. Show your work for each step.
Pay attention to multiplication — state each calculation explicitly.
```

---

## Example 3: ThoT for Fragmented RAG Context

```
I've retrieved 8 document chunks about our company's Q3 performance.
The chunks come from different reports and may be disjointed.

Walk me through this context in manageable parts step by step,
summarizing and analyzing as we go. Thread together the narrative
about revenue trends, connecting data points across chunks
even when they appear in different sections.

After threading through all chunks, provide a synthesis of
the key Q3 trends with supporting data points.
```

---

## Example 4: GoT for Deep Research

```
I need to design an authentication system for a multi-tenant SaaS app.

Step 1: Generate 3 distinct approaches:
  A) Traditional JWT with refresh tokens
  B) Session-based with Redis store
  C) OAuth 2.0 with PKCE + opaque tokens

Step 2: Evaluate each approach against these criteria:
  - Security (resistance to token theft, replay, CSRF)
  - Scalability (horizontal scaling, stateless vs stateful)
  - Developer experience (implementation complexity, debugging)
  - Multi-tenancy support (tenant isolation, cross-tenant risks)

Step 3: Combine the best aspects into a recommended hybrid approach.
  Justify each design choice with specific tradeoffs.
```

---

## Example 5: CQoT for Medical Reasoning

```
Analyze whether this patient's symptoms suggest condition X or Y.

For each reasoning step, explicitly state:
- DATA: What specific symptom or test result supports this step?
- WARRANT: What medical principle connects this evidence to the conclusion?
- REBUTTAL: What alternative explanation could invalidate this step?

Only proceed to the next step if the rebuttal is addressed or
acknowledged as a limitation.

Present your final assessment with confidence level and
remaining uncertainties.
```

---

## Example 6: SoT for Report Generation

```
Stage 1 — Generate outline:
Write a concise outline for a market analysis of the European
EV charging infrastructure market in 2026. Include 6-8 major sections.

Stage 2 — Expand (run in parallel):
"Write section 3: 'Competitive Landscape' from the outline.
 Include specific company names, market shares, and strategic moves."

Stage 3 — Assemble:
Combine all sections. Ensure consistent terminology and smooth transitions.
Add an executive summary based on the assembled content.
```

---

# Reasoning Decomposition — Checklist

## Pre-Flight
- [ ] Task complexity assessed (simple/medium/hard/novel)
- [ ] Model type identified (reasoning-native vs standard)
- [ ] Latency budget established
- [ ] Accuracy requirement clear (best-effort vs mission-critical)

## In-Flight
- [ ] Framework selected matches task type (see decision rules in SKILL.md)
- [ ] For reasoning models: constraints specified, no redundant CoT instructions
- [ ] For standard LLMs: decomposition prompt included
- [ ] For CQoT: rebuttal requirement is explicit
- [ ] For SoT: skeleton is detailed enough for coherent parallel expansion

## Final Review
- [ ] Reasoning chain is logically sound (no unsupported leaps)
- [ ] For critical tasks: rebuttals addressed or flagged as limitations
- [ ] Output matches the requested format and depth
- [ ] No over-thinking artifacts (excessive hedging, circular reasoning)

## Top 5 Failure Modes
1. **CoT on reasoning model** — wastes tokens, may degrade quality
2. **No decomposition on standard LLM** — skipped steps, missing logic
3. **Plausibility trap** — fluent but wrong; needs CQoT or verification
4. **Attention dilution in long context** — needs ThoT threading
5. **Wrong framework for task** — e.g., SoT for a 2-sentence answer
