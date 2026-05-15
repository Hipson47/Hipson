---
name: agentic-rag-orchestration
description: >
  Build reliable agent systems with retrieval, tool use, and multi-agent coordination.
  Covers Agentic RAG, Self-RAG, HyDE, Instructed Retriever, Reflexion, MCP production
  patterns, ACE/MCE context optimization, context folding, and the Skills-vs-CLI-vs-MCP
  decision. Use when designing any system where an LLM calls tools, retrieves information,
  or coordinates with other agents. Also use when debugging agent failures or optimizing
  agent cost/latency.
---

# Agentic RAG & Orchestration

## 1. Purpose
Design agent architectures that retrieve, reason, act, and verify reliably.

## 2. When to Use
- Building RAG pipelines (basic or agentic)
- Designing tool-using agents
- Multi-agent orchestration (supervisor/worker, swarm)
- Debugging agent loops (stuck, hallucinating, wrong tool)
- Optimizing agent cost via context management

## 3. When NOT to Use
- Simple Q&A with no retrieval needed
- Pure reasoning tasks with no external data → `reasoning-decomposition/`
- System prompt design without tool use → `system-prompt-architect/`

## 4. Core Concepts

### Agentic RAG
Replaces static Query→Retrieve→Answer with a feedback loop:
1. Generate plan and issue queries
2. Retrieve and analyze results
3. **Decide** if results are sufficient
4. If not: rewrite query, search again (multi-hop)
5. Synthesize final answer

### Self-RAG
Model outputs "reflection tokens" that critique its own retrieval and generation:
1. **Retrieval Necessity**: "Does this query need external data?"
2. **Relevance Grading**: "Is this document relevant?"
3. **Support Grading**: "Is my answer supported by the document?"
If support is low → trigger fallback (broader search, different source).

### HyDE (Hypothetical Document Embeddings)
Generate a hypothetical answer document → embed it → use as search query. Matches "document to document" instead of "question to document." Improves recall in domain-specific vocabularies.

### Instructed Retriever [2026]
Pass both query AND instruction to the retriever: "Retrieve financial documents describing risk factors, specifically mentioning 'inflation'. Prioritize fiscal year 2025." Aligns retrieval with the generation goal.

### Context Folding [2026]
Agent branches for subtask → folds results back → preserves: outcomes, failed approaches, created artifacts, open questions. Discards intermediate reasoning.

### ACE/MCE [2026]
- **ACE**: Treats contexts as evolving playbooks (generation → reflection → curation). Prevents context collapse.
- **MCE**: Meta-agent evolves CE skills; base-agent executes them. 16.9% mean improvement.

## 5. Tool Integration — The 2026 Stack

| Layer | When to Use | Cost | Latency |
|-------|-------------|------|---------|
| **Skills** (static docs in context) | Known patterns, SDK docs, domain knowledge | Zero runtime | Zero |
| **CLI tools** | Local operations, known tool sets, fast execution | Low | Low |
| **MCP servers** | Dynamic tool discovery, external services, multi-agent | Medium | Medium |
| **Direct APIs** | High-throughput, predictable costs, managed runtimes | Variable | Variable |

**Rule of thumb**: "A CLI works when you know what tools you need. MCP works when the agent needs to figure that out at runtime."

**Max 10-15 active tools per agent turn.** Namespace overlapping tools.

### MCP Production Patterns [2026]
- Domain-specific servers (not monolithic) — limits context bloat
- Central registry for server discovery
- CABP for identity-scoped routing
- SERF for structured error recovery
- 10K+ active servers, 97M monthly SDK downloads

## 6. Agent Patterns

### Supervisor/Worker ("Talk Less, Call Right")
```xml
<supervisor_contract>
  You are an API Router. You have NO ability to converse.
  Your ONLY output is JSON tool calls.
  If no tool fits the query, output null.
  DO NOT explain. DO NOT introduce yourself.
</supervisor_contract>
```

### Error Handling Sub-Routine
```xml
<error_handling>
  IF tool_output == ERROR:
    1. READ the error message
    2. REFLECT: bad parameters? missing access? timeout?
    3. FIX: retry with corrected params (once)
    4. If retry fails: report with analysis, do not apologize
</error_handling>
```

### Reflexion Loop
```
Actor performs task → Evaluator tests output → If fail:
  Self-Reflect: "Why did this fail? What edge case was missed?"
  Store reflection in Memory Buffer
  Retry with memory: "Previous attempt failed because of X. Avoid this."
```

### Context Folding Template
```xml
<fold_summary task="[subtask_name]">
  <outcome>[what was accomplished]</outcome>
  <failed_approaches>[what didn't work and why]</failed_approaches>
  <artifacts>[files created, data saved]</artifacts>
  <open_questions>[what remains unresolved]</open_questions>
</fold_summary>
```

## 7. Failure Modes
1. **One-shot retrieval failure** — first query misses. Fix: Agentic RAG with rewrite loop.
2. **Over-speaking** — agent explains instead of calling tool. Fix: RRP with binary strictness.
3. **Hallucination cascade in multi-agent** — workers propagate errors. Fix: all data flows through orchestrator; worker isolation.
4. **Context collapse** — iterative rewriting erodes detail. Fix: ACE structured incremental updates.
5. **Tool ambiguity** — agent picks wrong tool. Fix: WHEN-to-use triggers in tool descriptions.

## 8. Cross-Links
- Tool definition patterns → `system-prompt-architect/`
- Verification of agent outputs → `eval-security-guardrails/`
- Agent coding workflows → `ai-coding-workflows/`

## 9. Source Basis
PDF §3 (Agentic RAG, Self-RAG, HyDE, Reflexion), Research.md Ch.3 (Supervisor, RRP, error handling), Delta §1 (KV-cache, context folding), §3 (MCP), §4 (ACE/MCE).

## 10. Freshness Notes
`[FRESHNESS: April 2026]` MCP v2.1 is current. ACE/MCE papers updated March 2026. Monitor MCP roadmap for Tasks primitive GA and new transport layers.
---

# Agentic RAG & Orchestration — Examples

## Example 1: Bad vs Better — Static RAG

**Bad (static RAG):**
```python
docs = retriever.search(user_query)
answer = llm.generate(context=docs, query=user_query)
return answer  # If docs are bad, answer is bad. No recovery.
```

**Better (Agentic RAG):**
```python
plan = llm.generate("Assess what information is needed for: " + user_query)
docs = retriever.search(plan.initial_query)
assessment = llm.generate(f"Are these docs sufficient? {docs}")
if assessment.insufficient:
    refined_query = llm.generate(f"Rewrite query: {assessment.missing_info}")
    more_docs = retriever.search(refined_query)
    docs += more_docs
answer = llm.generate(context=docs, query=user_query)
```

---

## Example 2: Bad vs Better — Tool Descriptions

**Bad:**
```json
{"name": "search", "description": "Searches documents"}
```

**Better:**
```json
{
  "name": "search_product_docs",
  "description": "Search product documentation for feature specs, API references, and troubleshooting guides. USE WHEN: user asks about product capabilities, API endpoints, or error codes. DO NOT USE: for account-specific questions (use get_account instead). Returns top 5 matches. Only includes docs from 2025+.",
  "parameters": {
    "query": "Natural language search query, max 50 words",
    "category": "Optional: features | api | troubleshooting"
  }
}
```

---

## Example 3: HyDE Implementation

```
Step 1 — Generate hypothetical answer:
"Write a comprehensive answer to: 'How does photosynthesis work?'
 Include main arguments and supporting details.
 This will be used for embedding-based retrieval, not as the final answer."

Step 2 — Embed the hypothetical document

Step 3 — Search vector DB with the hypothetical embedding

Step 4 — Generate final answer from REAL retrieved documents
```

---

## Example 4: Reflexion for Coding Agent

```
Attempt 1:
  Agent writes function → Tests fail (IndexError on empty list)
  
Self-Reflection prompt:
  "The tests failed with IndexError. Analyze: the function doesn't
   handle empty input. This is an edge case I missed in the initial
   implementation."

Memory update:
  "LESSON: Always add guard clause for empty collections before indexing."

Attempt 2:
  Agent writes function with guard clause → Tests pass
  Memory includes: "Previous attempt failed on empty input. Added guard."
```

---

## Example 5: Multi-Agent Orchestrator with Query Expansion

```xml
<orchestrator_instruction>
  Analyze the user query: "Impact of AI on 2026 healthcare markets"
  
  Decompose into 4 sub-queries:
  1. "AI diagnostics market size 2026" → assign to Market Researcher
  2. "Healthcare AI regulatory changes 2025-2026" → assign to Legal Analyst
  3. "AI drug discovery investment trends" → assign to Finance Analyst
  4. "Hospital automation adoption rates" → assign to Tech Specialist
  
  Workers operate in isolation. All results flow through you.
  Synthesize worker outputs into a coherent analysis.
  Flag contradictions between worker findings.
</orchestrator_instruction>
```

---

## Example 6: Context Folding in Practice

```
Main task: "Redesign the authentication system"

Agent branches to subtask: "Research competitor auth implementations"

[subtask executes: searches web, reads docs, analyzes 3 competitors]

Fold result back:
<fold_summary task="competitor_auth_research">
  <outcome>Auth0 uses PKCE+opaque tokens, Clerk uses session-based
  with edge worker validation, Supabase uses JWT with RLS</outcome>
  <failed_approaches>Tried to access Clerk's internal docs — 403.
  Used their public changelog instead.</failed_approaches>
  <artifacts>competitor_auth_comparison.md saved</artifacts>
  <open_questions>Clerk's edge worker latency numbers not found</open_questions>
</fold_summary>

Agent continues main task with compressed competitor context.
```

---

# Agentic RAG & Orchestration — Checklist

## Pre-Flight
- [ ] Retrieval strategy selected (basic RAG / Agentic RAG / Self-RAG)
- [ ] Tools defined with WHEN-to-use triggers
- [ ] Tool count ≤ 15 per agent turn
- [ ] Agent architecture chosen (single agent / supervisor-worker / swarm)
- [ ] Error handling protocol defined
- [ ] Memory strategy determined (in-context / external notes / context folding)

## In-Flight
- [ ] Agent assesses retrieval sufficiency before answering
- [ ] Failed retrievals trigger query rewriting, not hallucination
- [ ] Tool errors handled with retry-once-then-report pattern
- [ ] Multi-agent: workers isolated, all data through orchestrator
- [ ] Context folding preserves failed approaches and open questions
- [ ] KV-cache prefix remains stable across agent turns

## Final Review
- [ ] Answer is grounded in retrieved evidence (not hallucinated)
- [ ] All tool calls used correct parameters
- [ ] No hallucinated tool calls ("I have checked..." without actual check)
- [ ] Context budget not exceeded
- [ ] Reflexion memory captured lessons from failures

## Top 5 Failure Modes
1. **One-shot retrieval miss** — no rewrite loop → wrong answer
2. **Over-speaking agent** — explains instead of calling tools
3. **Hallucination cascade** — bad data propagates through workers
4. **Context collapse** — detail lost through iterative rewrites
5. **Wrong tool selection** — ambiguous tool descriptions
