---
name: system-prompt-architect
description: >
  Design, audit, and optimize system prompts for 2026-class LLMs (Claude 4.x, GPT-5.x, Gemini 3).
  Covers XML/Markdown structure, modular sectioning, role-behavioral contracts, semantic density,
  context rot prevention, KV-cache optimization, S2A filtering, and model-specific guidance.
  Use this skill whenever designing a system prompt, auditing an existing one, or debugging
  poor instruction adherence. Also use when setting up agent system instructions, tool definitions,
  or multi-turn conversation scaffolds.
---

# System Prompt Architect

## 1. Purpose
Design system prompts that maximize instruction adherence, minimize context rot, and optimize for cost/latency via KV-cache stability.

## 2. When to Use
- Designing a new system prompt for any LLM application
- Auditing why a model isn't following instructions
- Setting up agent system instructions with tool definitions
- Optimizing prompt cost via cache-friendly structure
- Migrating prompts between models (Claude ↔ GPT ↔ Gemini)

## 3. When NOT to Use
- Single-turn casual queries (no system prompt needed)
- Tasks where the reasoning strategy matters more than the frame → `reasoning-decomposition/`
- Tool chain design → `agentic-rag-orchestration/`

## 4. Inputs Required
- Task description and success criteria
- Target model (Claude 4.x, GPT-5.x, Gemini 3, or multi-model)
- Available tools/functions (if any)
- Expected input types (text, images, code, documents)
- Constraints (safety, compliance, tone, format)

## 5. Outputs Produced
- Complete system prompt with modular sections
- KV-cache-stable prefix structure
- Role and behavioral contract
- Output format specification

## 6. Core Concepts

### Semantic Density Threshold
Every LLM has a limit on prompt verbosity beyond which additional details *dilute* compliance. Primacy and recency effects cause middle instructions to be ignored. Keep system prompts compact; test for adherence at every section.

### Context Rot
As context fills with noise (redundant tool outputs, verbose reasoning traces), the model's attention on critical instructions fades. Combat with: JIT context loading, compaction, and S2A filtering.

### KV-Cache Stability [2026]
Cached input tokens are 10x cheaper than uncached (e.g., Claude Sonnet: $0.30 vs $3.00/MTok). A single-token change in the prompt prefix invalidates the entire cache. Design prompts with a **stable prefix** and **variable suffix**.

### S2A (System 2 Attention)
Pre-processing step where the LLM rewrites context to remove irrelevant information before reasoning. Use in RAG systems to sanitize retrieved documents.

## 7. Decision Rules

| Situation | Do This |
|-----------|---------|
| Claude 4.x | Use XML tags for structure. Request "above-and-beyond" explicitly. Provide motivation for instructions. |
| GPT-5.x | Use Markdown headers (H1, H2). Include explicit "Response Format" section. YAML for nested data. |
| Gemini 3 | Concise role + concrete rules. XML or structured JSON for tool definitions. |
| Long context (20k+ tokens) | Place documents at TOP, instructions and query at BOTTOM. Up to 30% quality improvement. |
| Agent with tools | Define tools with WHEN-to-use descriptions, not just WHAT they do. Max 10-15 active tools. |
| Multi-turn conversation | Keep system prompt prefix stable across turns for KV-cache reuse. Append dynamic content after. |

## 8. Recommended Workflow

```
1. DEFINE    → Role, task, constraints, output format
2. STRUCTURE → Modular sections with XML/Markdown tags
3. STABILIZE → Ensure prefix is cache-friendly (no timestamps, no variable content before instructions)
4. EXAMPLES  → Add 2-3 canonical examples showing desired behavior
5. NEGATIVE  → Add anti-patterns ("Do NOT...")
6. VERIFY    → Test with edge cases. Check middle instructions are followed.
7. COMPRESS  → Remove redundancy. Every token must earn its place.
```

## 9. Prompt Architecture Template

```xml
<!-- STABLE PREFIX — never changes between turns -->
<system_identity>
  You are [ROLE]. Your purpose is [PURPOSE].
</system_identity>

<behavioral_contract>
  <always>[list of required behaviors]</always>
  <never>[list of prohibited behaviors]</never>
</behavioral_contract>

<output_specification>
  Format: [JSON/Markdown/prose]
  Length: [constraint]
  Schema: [if structured output]
</output_specification>

<tool_definitions>
  <!-- Only if agent has tools -->
  <tool name="..." description="Use when [TRIGGER]. Returns [OUTPUT]. Limitations: [LIMITS]">
    <parameters>...</parameters>
  </tool>
</tool_definitions>

<examples>
  <example type="good">...</example>
  <example type="bad">...</example>
</examples>

<!-- VARIABLE SUFFIX — changes per turn -->
<context>
  [Retrieved documents, conversation history, user input]
</context>

<query>
  [Current user request — placed LAST for best performance]
</query>
```

## 10. Failure Modes

| Failure | Cause | Fix |
|---------|-------|-----|
| Model ignores middle instructions | Semantic density exceeded; primacy/recency effect | Shorten prompt. Repeat critical constraints at end. |
| Model "hallucinates" tool use | Vague tool descriptions | Add WHEN-to-use triggers and explicit limitations |
| Cache miss on every turn | Variable content in prefix (timestamps, user names) | Move all variable content to suffix |
| Model over-explains instead of acting | No behavioral contract suppressing verbosity | Add "DO NOT explain reasoning. ONLY output [artifact]" |
| Model ignores role | Role is abstract ("You are helpful") | Add concrete behavioral specifications + examples |
| Sycophancy / user bias | Standard attention follows user framing | Add S2A pre-processing step |

## 11. Verification Checklist
→ See `checklist.md`

## 12. Cross-Links
- Reasoning strategy selection → `reasoning-decomposition/`
- Tool definition patterns → `agentic-rag-orchestration/`
- Prompt hardening → `eval-security-guardrails/`
- Multimodal prompt structure → `multimodal-gen-prompting/`

## 13. Source Basis
- Prompting Methods Internet Map.pdf §1 (Context Engineering)
- Advanced System Prompt Engineering (2026 Standards).md §5 (Structured Prompts)
- Advanced System Prompt Engineering Research.md Ch.1, Ch.4, Ch.5
- Meta-Analysis Semantic Anchors (Semantic Density, Anchor-Summary, RAL-Writer)
- Delta §1 (KV-cache, context folding), §2 (Claude 4.x)

## 14. Freshness Notes
`[FRESHNESS: April 2026]` Claude 4.6 is current. KV-cache guidance is from Manus production blog (March 2026). Monitor for new model releases that may change optimal structure (e.g., if Claude introduces native structured output modes).
---

# System Prompt Architect — Checklist

## Pre-Flight
- [ ] Target model identified (Claude 4.x / GPT-5.x / Gemini 3 / multi)
- [ ] Task and success criteria defined
- [ ] Available tools listed with descriptions
- [ ] Output format specified (JSON, Markdown, prose, structured)
- [ ] Constraints documented (safety, compliance, tone, length)
- [ ] Expected input types noted (text, images, code, documents)

## In-Flight (While Writing)
- [ ] Stable prefix contains NO variable content (timestamps, session IDs, user names)
- [ ] Sections use appropriate structure for target model (XML for Claude, Markdown for GPT, concise rules for Gemini)
- [ ] Role definition includes behavioral contract, not just persona
- [ ] Tool definitions include WHEN-to-use triggers and limitations
- [ ] Examples included (at least 1 good, 1 bad)
- [ ] Critical constraints appear at BOTH start and end of prompt (recency anchoring)
- [ ] Long documents placed at TOP, query at BOTTOM
- [ ] Semantic density check: no section exceeds ~500 tokens without clear sub-structure
- [ ] No conflicting instructions between sections

## Final Review
- [ ] Read the prompt as if you're the model. Is every instruction unambiguous?
- [ ] Test: does the middle 30% of the prompt get followed? (semantic density check)
- [ ] Test: does the model handle edge cases mentioned in constraints?
- [ ] KV-cache: will the prefix remain identical across turns?
- [ ] Tool count: ≤15 active tools? Namespaced if overlapping?
- [ ] No orphan references (every term, acronym, or tool mentioned is defined)

## Top 5 Failure Modes
1. **Middle instruction dropout** — prompt too long; model ignores middle sections. Fix: compress or repeat critical constraints at end.
2. **Cache invalidation** — variable content in prefix. Fix: move timestamps/IDs to suffix.
3. **Tool hallucination** — vague tool descriptions. Fix: add WHEN/WHEN-NOT triggers.
4. **Over-verbosity** — model explains instead of acting. Fix: add explicit "DO NOT explain" in behavioral contract.
5. **Role drift** — model abandons persona over long conversations. Fix: reinject role anchor at compaction boundaries.
---

# System Prompt Architect — Examples

## Example 1: Bad vs Better — Vague Role

**Bad:**
```
You are a helpful assistant. Answer questions accurately.
```

**Better:**
```xml
<system_identity>
  You are a Senior Financial Analyst specializing in SaaS metrics.
</system_identity>

<behavioral_contract>
  <always>
    - Cite specific metrics (ARR, NRR, CAC/LTV) with their definitions on first use
    - Flag when data is insufficient for a conclusion
    - Use tables for comparisons of 3+ items
  </always>
  <never>
    - Provide investment advice or price targets
    - Assume data not present in the provided context
    - Use marketing language ("revolutionary", "game-changing")
  </never>
</behavioral_contract>

<output_specification>
  Format: Structured analysis with headers
  Length: 200-500 words unless asked for more
  Always end with: "Key risks:" section
</output_specification>
```

**Why it's better:** Concrete role + behavioral contract + output spec. The model knows what to do, what not to do, and how to format it.

---

## Example 2: Bad vs Better — KV-Cache Instability

**Bad (cache-breaking):**
```
System prompt generated at 2026-04-12T14:32:05Z
Session ID: abc-123-def
User: John Smith (Premium tier)

You are an assistant...
```

**Better (cache-stable):**
```xml
<!-- STABLE PREFIX — identical across all sessions -->
<system_identity>
  You are a customer support agent for Acme Corp.
</system_identity>

<instructions>
  [all instructions here — never changes]
</instructions>

<!-- VARIABLE SUFFIX — changes per session -->
<session_context>
  <user_tier>Premium</user_tier>
  <timestamp>2026-04-12T14:32:05Z</timestamp>
</session_context>
```

**Why it's better:** Timestamps, session IDs, and user names in the prefix invalidate KV-cache on every turn. Move all variable content to the suffix.

---

## Example 3: Agent Tool Definitions

```xml
<tool name="search_knowledge_base">
  <description>
    Search the product documentation knowledge base.
    USE WHEN: User asks about features, pricing, technical specs, or troubleshooting.
    DO NOT USE WHEN: User asks about their own account (use get_account_info instead).
    RETURNS: Top 5 matching documents with relevance scores.
    LIMITATIONS: Only includes docs from 2025 onwards. Does not search community forums.
  </description>
  <parameters>
    <param name="query" type="string" required="true">
      Natural language search query. Keep under 50 words.
    </param>
    <param name="category" type="enum" values="features,pricing,troubleshooting,api" required="false">
      Optional category filter to narrow results.
    </param>
  </parameters>
</tool>
```

---

## Example 4: S2A Pre-Processing for RAG

```xml
<s2a_preprocessing>
  Before answering the user's question, perform this step:
  
  Given the retrieved documents below, extract ONLY the sentences
  that are factually relevant to the user's query. Remove:
  - Biographical details of people not asked about
  - Marketing copy and promotional language
  - Dates and events unrelated to the query
  
  Present the filtered context as "Relevant Context:" before reasoning.
</s2a_preprocessing>
```

---

## Example 5: Long-Form System Prompt — Coding Agent

```xml
<system_identity>
  You are a Senior Python Engineer working on a FastAPI backend.
  Your code must adhere to PEP 8, include type hints for all function
  signatures, and use async patterns for I/O operations.
</system_identity>

<behavioral_contract>
  <always>
    - Run existing tests before modifying code
    - Write pytest tests for every new function
    - Use Pydantic v2 models for request/response schemas
    - Handle errors with Problem Details (RFC 9457)
    - Commit work incrementally with descriptive messages
  </always>
  <never>
    - Modify test files to make failing tests pass
    - Use synchronous database calls in async endpoints
    - Introduce new dependencies without explaining why
    - Skip type hints to save time
  </never>
</behavioral_contract>

<process>
  1. Read the task description and existing code
  2. Plan: outline what files need to change and why
  3. Implement changes incrementally
  4. Run tests after each change
  5. If tests fail, debug and fix before continuing
  6. Summarize what was done when complete
</process>

<output_specification>
  When writing code, use fenced code blocks with language identifiers.
  When explaining, be concise — max 2 sentences per explanation.
  After completing tool use, provide a brief summary of work done.
</output_specification>

<error_handling_protocol>
  IF tool_output == ERROR:
    1. Read the error message carefully
    2. Reflect: was it bad parameters, missing file, or logic error?
    3. Fix and retry once automatically
    4. If retry fails, report the issue with your analysis
</error_handling_protocol>
```

---

## Example 6: Multi-Model Prompt Adaptation

**For Claude 4.x:**
```xml
<instructions>
  Analyze the contract for termination clauses.
  This analysis will be reviewed by legal counsel, so accuracy
  is more important than speed. Go beyond surface-level reading.
</instructions>
```

**Same task for GPT-5.x:**
```markdown
## Task
Analyze the contract for termination clauses.

## Response Format
Use CommonMark Markdown. Include:
- A summary table of all termination clauses found
- Risk assessment for each (Low/Medium/High)
- Direct quotes (with section references) supporting each finding

## Context
This analysis will be reviewed by legal counsel.
Accuracy is more important than speed.
```

**Same task for Gemini 3:**
```
Role: Contract Analyst
Rules:
1. Extract all termination clauses from the provided document
2. Rate each clause's risk: Low, Medium, or High
3. Cite section numbers for every finding
4. Output as a structured table followed by a 3-sentence summary
5. Do not speculate beyond what the document states
```

---

## Example 7: Anchor-Summary Pattern for Long Documents

```xml
<instructions>
  You will receive a 50-page technical specification split into chunks.
  
  After processing each chunk, output a brief summary anchored to
  the key architectural decisions found:
  
  "Based on the above: [2-3 sentence summary of key points].
   Continuing to next section."
  
  At the end, synthesize all anchor summaries into a final analysis.
  This prevents losing critical mid-document information.
</instructions>
```
