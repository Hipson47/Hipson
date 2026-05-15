# AI Engineering Skills Package — April 2026

A production-ready library of 7 Claude Skills covering modern prompting, context engineering, agentic orchestration, multimodal generation, AI-assisted coding, fullstack development, and evaluation/security.

## What This Is

A self-contained reference package synthesized from 18+ source documents, research papers, and official documentation. Every skill is designed to be loaded directly into a Claude session to guide real work — not to be read as an essay.

## How to Use

1. **Pick the skill** that matches your task (see `_INDEX.md` for routing).
2. **Read `SKILL.md`** for the core guidance — it's the only required file.
3. **Use `examples.md`** when you need prompt templates or patterns to adapt.
4. **Run `checklist.md`** before, during, and after execution as a quality gate.

## Skill Map

| Skill | Use When You Need To… |
|-------|----------------------|
| `system-prompt-architect` | Design or audit a system prompt for any LLM |
| `reasoning-decomposition` | Choose and apply a reasoning strategy (CoT, GoT, PS+, etc.) |
| `agentic-rag-orchestration` | Build agent loops, retrieval pipelines, tool chains, or multi-agent systems |
| `multimodal-gen-prompting` | Prompt image or video generation models (Nano Banana, Veo, Kling, Sora, Seedance) |
| `ai-coding-workflows` | Run AI-assisted development: spec-driven, TDD-AI, agent coding |
| `fullstack-2026` | Build production Next.js + FastAPI applications |
| `eval-security-guardrails` | Verify outputs, harden prompts, test AI-written code, defend against attacks |

## Suggested Reading Order

1. `system-prompt-architect` — foundational; everything else builds on it
2. `reasoning-decomposition` — core thinking strategies
3. `eval-security-guardrails` — verification applies to every skill
4. Then whichever domain skill matches your work

## Keeping This Package Updated

- Check `_LEGACY_NOTES.md` for material that may need refresh
- When new models or API changes ship, update the relevant `SKILL.md` sections marked `[FRESHNESS]`
- New terminology goes in `_GLOSSARY.md`
- New source documents get mapped in `_SOURCE_MAP.md`

## Language

All shipped skills and public documentation are in English. Historical source material has been synthesized into the public skill files.

## Last Updated

April 12, 2026
---

# Skills Index

## Quick Routing

| You want to… | Go to… |
|--------------|--------|
| Write or audit a system prompt | `system-prompt-architect/` |
| Choose CoT vs GoT vs PS+ vs no-CoT | `reasoning-decomposition/` |
| Build an agent, RAG pipeline, or tool chain | `agentic-rag-orchestration/` |
| Prompt Nano Banana, Veo, Kling, Sora, Seedance | `multimodal-gen-prompting/` |
| Run AI-assisted coding workflows | `ai-coding-workflows/` |
| Build Next.js + FastAPI production apps | `fullstack-2026/` |
| Verify outputs, harden prompts, test code, threat model | `eval-security-guardrails/` |

---

## Skill Summaries

### 1. system-prompt-architect
Design high-performance system prompts for 2026-class LLMs. Covers XML/Markdown structure, modular sectioning, role-behavioral contracts, semantic density thresholds, context rot prevention, KV-cache stability, S2A filtering, and Claude 4.x-specific guidance. The foundation skill — cross-referenced by every other skill.

### 2. reasoning-decomposition
Select and apply the right reasoning framework for your task. Covers Chain-of-Thought and its variants (Thread-of-Thought, Graph-of-Thought, Skeleton-of-Thought), Plan-and-Solve, Critical-Questions-of-Thought (CQoT), cognitive verification triggers, and the critical distinction between prompting reasoning-native models (o1, R1) vs standard LLMs.

### 3. agentic-rag-orchestration
Build reliable agent systems with retrieval, tool use, and multi-agent coordination. Covers Agentic RAG, Self-RAG, HyDE, Instructed Retriever, Reflexion, MCP production patterns, ACE/MCE context optimization, context folding, the Skills-vs-CLI-vs-MCP decision, and the Supervisor/Worker pattern.

### 4. multimodal-gen-prompting
Prompt image and video generation models effectively. Covers Nano Banana/Pro/2 (Gemini Image), Veo 3.1, Kling 3.0, Sora 2, Seedance 2.0 with model-specific guidance, multi-model routing, camera-first video prompting, Draft-to-Master workflows, and native audio direction. Refreshed to Q1 2026 state.

### 5. ai-coding-workflows
Run AI-assisted development from spec to production. Covers spec-driven development, TDD-AI protocol, vibe coding guardrails, verification loops, Cursor/Claude Code patterns, long-horizon coding, mutation testing, and the Delegate-Review-Own operating model.

### 6. fullstack-2026
Build production Next.js App Router + FastAPI applications. Covers server-first React architecture, Server Components/Actions, BFF/proxy integration shapes, async FastAPI patterns, OpenAPI contracts, agent-friendly repo layouts, and testing strategies. Canonical source: backend.md and frontend.md.

### 7. eval-security-guardrails
Verify, test, harden, and defend AI systems. Covers Chain of Verification (CoVe) and extensions (ConVerTest, CoV-RAG, VeriCoT), LLM-as-Judge, prompt hardening, red teaming, Constitutional AI, vibe hacking threat model, tool poisoning, TDD-AI testing anti-patterns, and CI gates for AI-generated code.

---

## Dependency Map

```
system-prompt-architect ──────────────────────────────┐
        │                                              │
        ├── reasoning-decomposition                    │
        │        │                                     │
        │        └── agentic-rag-orchestration         │
        │                 │                            │
        │                 ├── ai-coding-workflows      │
        │                 │                            │
        │                 └── multimodal-gen-prompting  │
        │                                              │
        └── fullstack-2026                             │
                                                       │
eval-security-guardrails ─────── applies to all ───────┘
```
---

# Glossary

Merged vocabulary from Meta-Analysis Semantic Anchors + Q1 2026 Delta. Terms marked `[2026]` are new additions.

---

**ACE (Agentic Context Engineering)** `[2026]` — Framework treating contexts as evolving playbooks: generation → reflection → curation. Prevents brevity bias and context collapse. +10.6% on agent benchmarks. [arXiv 2510.04618]

**Anchor-Summary Pattern** — Context-bridging strategy where key information is summarized before the next query. Counteracts "lost-in-the-middle" blind spot.

**CABP (Context-Aware Broker Protocol)** `[2026]` — Extends MCP's JSON-RPC with identity-scoped request routing for production agent deployments. [arXiv 2603.13417]

**Cognitive Verification Trigger** — Short cues embedded in reasoning (e.g., "let me verify", "hold on") that activate high-verification attention heads in reasoning-native models.

**Compiled AI** `[2026]` — Paradigm where LLMs generate executable code during compilation; workflows then execute deterministically without further model invocation. Trades flexibility for predictability. [arXiv 2604.05150]

**Context Collapse** `[2026]` — Degradation of context detail through iterative rewriting. ACE and MCE are designed to prevent this.

**Context Folding** `[2026]` — Agent branches for a subtask, then folds results back by collapsing intermediate steps into a summary preserving constraints, failed approaches, and open questions.

**Context Isolation** `[2026]` — Ensuring one subtask's context doesn't contaminate another's in multi-agent systems.

**Context Rot** — Degradation of reasoning quality as signal-to-noise ratio drops in long contexts. Addressed by S2A, JIT loading, and compaction.

**ConVerTest** `[2026]` — CoVe applied to code generation. Iteratively refines code using verification questions until testable agreement across self-generated test cases. [Taherkhani et al., Feb 2026]

**CoV-RAG** `[2026]` — CoVe integrated into RAG pipelines. Scores retrieved context and generated answers; triggers query rewriting on verification failure.

**CoVe (Chain of Verification)** — Four-step hallucination reduction: draft → plan verification questions → answer independently → refine. Reduces hallucinations 50–70%.

**CQoT (Critical-Question-of-Thought)** — Forcing self-scrutiny via argumentative querying mid-CoT. Based on Toulmin's argumentation model: Data, Warrant, Backing, Rebuttal.

**CREST (Cognitive REasoning Steering at Test-time)** — Techniques for steering model reasoning at inference time by activating specific attention heads (verification, backtracking).

**Delegate-Review-Own** `[2026]` — Operating model: AI agents handle first-pass execution → engineers review for correctness → humans own architecture and outcomes.

**Draft-to-Master** `[2026]` — Video production workflow: generate low-res previews to test prompts → master only best clips to high-fidelity 4K.

**GEPA (Generic-Pareto)** `[2026]` — Evolutionary prompt optimizer in DSPy. Uses reflective natural language reasoning on execution feedback with Pareto optimization.

**GoT (Graph of Thoughts)** — Reasoning as a DAG: allows aggregation, backtracking, and circulation of thoughts. Highest expressivity among decomposition frameworks.

**HyDE (Hypothetical Document Embeddings)** — Generates a hypothetical answer document to use as an embedding query, bridging the vocabulary gap between short queries and long documents.

**KV-Cache Hit Rate** `[2026]` — Ratio of cached to uncached input tokens. Primary cost/latency metric for production agents. 10x cost difference on Claude Sonnet ($0.30 vs $3.00/MTok).

**MCE (Meta Context Engineering)** `[2026]` — Bi-level framework: meta-agent evolves CE skills via agentic crossover; base-agent executes them. 5.6–53.8% improvement over SOTA CE. [arXiv 2601.21557]

**MCP (Model Context Protocol)** `[2026]` — Open standard for AI-to-tool integration. 10K+ servers, 97M monthly SDK downloads. Donated to Linux Foundation's Agentic AI Foundation.

**Multi-Shot Storyboard** `[2026]` — Generating 3-12 video cuts in a single batch with per-shot prompts, camera directions, and transitions while maintaining visual consistency. (Kling 3.0)

**PS+ (Plan-and-Solve Plus)** — Decouples planning from execution. "First understand the problem and devise a plan." PS+ adds "pay attention to calculation" and "extract variables."

**RAL-Writer** — Retrieval-Augmented Long-Text Writer. Periodically re-introduces earlier content into the prompt to counter lost-in-the-middle effects.

**RRP (Rule-Based Role Prompting)** — Concise, rule-enforced role definitions ("talk less, call right") with explicit character profiles and strict function-call rules. +5.2% task success.

**S2A (System 2 Attention)** — Pre-processing step where LLM rewrites query/context to remove irrelevant info and bias before answering. Named after Kahneman's System 2 thinking.

**Semantic Density Threshold** — Limit on prompt verbosity beyond which additional details dilute instruction compliance. Primacy/recency effects cause middle instructions to be ignored.

**SERF (Structured Error Recovery Framework)** `[2026]` — Machine-readable failure semantics for MCP, enabling deterministic agent self-correction. [arXiv 2603.13417]

**Skillbook** `[2026]` — Persistent collection of learned strategies that evolves with agent tasks. Maintained by a Recursive Reflector that programmatically searches execution traces.

**SoT (Skeleton-of-Thought)** — Latency optimization: generate outline first, then expand each point in parallel API calls. Dramatically reduces time for long-form generation.

**ThoT (Thread of Thought)** — Maintains coherence in chaotic contexts. Trigger: "Walk me through this in manageable parts, summarizing as we go."

**VeriCoT** `[2026]` — Formalizes each CoT step as FOL formula, subjects to SMT solver (Z3) consistency checks. 3–7x verification pass rate improvement.
---

# Source Map

Public source references shipped with this package.

| Source File | Status | Informs Skills | Notes |
|-------------|--------|----------------|-------|
| `backend.md` | **Canonical** | fullstack-2026 | Production backend patterns for Next.js integrations, FastAPI, async services, and OpenAPI contracts. |
| `frontend.md` | **Canonical** | fullstack-2026 | Server-first React, App Router, UI security, accessibility, and frontend testing guidance. |
| `testy.md` | **Canonical** | eval-security-guardrails, ai-coding-workflows | TDD-AI protocol, testing anti-patterns, mutation testing, and CI gate recommendations. |

Historical research inputs were consolidated into the skill files and are not required at runtime.

## Files Referenced in Task Brief but Not in Uploads

| File | Resolution |
|------|-----------|
| `Gemini 3 Pro System Instruction Upgrade.md` | Content covered by Advanced_System_Prompt_Engineering files and delta. No gap. |
| `Cursor AI Development Research.pdf` | Content covered by New_Prompting_Techniques and delta's coding agent section. No gap. |
| `Cursor_Best_Practices_2026.md` | Content covered by New_Prompting_Techniques and delta. No gap. |
| `React 16 Modern Web Development.md` | Treated as **legacy**. frontend.md (March 2026) supersedes React 16-specific advice. Server-first React architecture is canonical. |

## Conflict Resolution Log

| Topic | Conflict | Resolution |
|-------|----------|------------|
| Kling version | Playbook says 2.5 Turbo; delta says 3.0 | **Kling 3.0** (Feb 2026) is canonical |
| Nano Banana naming | Multiple naming conventions | Canonical: Nano Banana = Gemini 2.5 Flash Image; Nano Banana Pro = Gemini 3 Pro Image; Nano Banana 2 = Gemini 3.1 Flash Image |
| CoT for reasoning models | Standards file says useful; PDF says counterproductive for o1/R1 | **PDF + delta is canonical**: CoT is redundant/harmful for reasoning-native models (o1, R1), useful for standard LLMs |
| YAML vs XML vs Markdown for prompts | Standards file prefers YAML; Research file + Claude docs prefer XML | **Model-dependent**: XML for Claude, YAML/Markdown for GPT-5/Gemini. Both documented. |
| Context compaction vs fresh start | PDF recommends compaction; Claude 4.x docs recommend fresh start | **Both valid, context-dependent**: Claude 4.5+ prefers fresh start with filesystem state discovery. Compaction for non-filesystem contexts. |
---

# Canonical Decisions

Synthesis decisions made when merging the 18-file corpus into this skills package.

## Taxonomy

The 7-skill taxonomy from the audit document was adopted without changes. No merge or split was needed — each skill covers a distinct domain with clear boundaries and minimal overlap.

## What Became Canonical

| Topic | Canonical Source | Rationale |
|-------|-----------------|-----------|
| System prompt architecture | PDF (§1) + Research.md + Delta (§2) | PDF provides theory; Research provides CREST/CQoT; Delta provides Claude 4.x specifics |
| Reasoning frameworks | PDF (§2) + Standards.md (§1-3) | Comprehensive coverage of all variants. Delta adds reasoning-native model nuance |
| Agentic/RAG patterns | PDF (§3) + Research.md (Ch.3) + Delta (§1,3,4) | PDF covers Self-RAG/HyDE/Agentic RAG; Research covers Supervisor pattern; Delta adds ACE/MCE/MCP production |
| Multimodal prompting | **Delta (§6)** as primary + DevOps Playbook for pipeline architecture | Delta has current model versions (Kling 3.0, Seedance 2.0, NB2). Playbook preserved only for DevOps pipeline patterns |
| AI coding workflows | New Prompting Techniques + testy.md + Delta (§7) | Technique Cards still valid. TDD-AI protocol from testy.md is canonical. Delta adds Claude Code/agent ecosystem |
| Fullstack patterns | **backend.md + frontend.md** | March 2026 vintage, high-confidence, directly operational |
| Eval/security | PDF (§4,6) + Top Findings + testy.md + Delta (§5) | PDF covers CoVe/Reflexion theory; Top Findings covers threat model; testy.md covers testing anti-patterns; Delta adds CoVe extensions |

## What Was Downgraded to Legacy

| Material | Reason | What Survived |
|----------|--------|---------------|
| `Multimodal_DevOps_Playbook_2025.md` model-specific sections | Model versions stale (Kling 2.5, no Seedance 2.0, no native audio, no 4K) | Pipeline architecture recommendations, evaluation metrics (CLIP, FVD), safety/watermarking guidance |
| Historical multimodal model tables | Names and capabilities outdated | Some prompt skeleton structures were preserved where model-agnostic |
| Historical planning manifest | Organizational manifest, not content | Used to verify coverage completeness |
| React 16-specific advice (referenced in task brief) | Superseded by frontend.md's server-first React architecture | None — frontend.md is canonical |

## Major Updates from April 2026 Delta

1. **KV-cache stability** became a first-class system prompt design concern (Section 1.1 of Delta)
2. **Context folding** added as a new context management pattern (Delta §1.2)
3. **Claude 4.x behavioral shifts** — conciseness, context awareness, explicit "above-and-beyond" requests (Delta §2)
4. **MCP** elevated from "future direction" to production infrastructure with 10K+ servers (Delta §3)
5. **ACE and MCE** added as entirely new context optimization frameworks (Delta §4)
6. **Kling 3.0** replaces Kling 2.5 as the primary video generation model (Delta §6.1)
7. **Seedance 2.0** added as new model for directed motion/lip-sync (Delta §6.2)
8. **Nano Banana 2** added as the default Gemini image model (Delta §6.3)
9. **Multi-model routing** established as the 2026 production standard for video (Delta §6.4)
10. **Claude Code at 4% of GitHub commits** — reshapes coding workflow guidance (Delta §7.1)

## Conflict Resolutions

See `_SOURCE_MAP.md` → Conflict Resolution Log for the full list.
---

# Legacy Notes

Material that should not drive future guidance without review. Preserved for historical context only.

## Stale Model References

- **Kling 2.5 Turbo** — replaced by Kling 3.0 (February 2026). Native 4K, multi-shot storyboard, and audio are not available in 2.5.
- **Kling O1** — community alias from December 2025. Official name is now Kling 3.0.
- **Sora 2 (initial)** — consumer access is now primarily through third-party APIs, not direct OpenAI subscription.
- **Nano Banana (original)** — now three distinct models: Nano Banana (Gemini 2.5 Flash), Nano Banana Pro (Gemini 3 Pro Image), Nano Banana 2 (Gemini 3.1 Flash Image).

## Stale Architecture Assumptions

- **Static RAG loop** (Query → Retrieve → Answer) — replaced by Agentic RAG with multi-hop retrieval and self-evaluation.
- **Single-model video workflows** — replaced by multi-model routing (2026 standard).
- **Context stuffing** — replaced by JIT context loading, context folding, and compaction strategies.
- **"Prompt whisperer" paradigm** — replaced by Context Engineering and System Interaction Design.

## Files Requiring Refresh Before Reuse

| File | What's Stale | Refresh Priority |
|------|-------------|-----------------|
| `Multimodal_DevOps_Playbook_2025.md` | Model versions, capabilities tables, pricing | High — models changed significantly in Q1 2026 |
| Historical multimodal model notes | Model names, feature tables, some prompt skeletons | High |
| `New_Prompting_Techniques___Workflows_for_AI-Assisted_Coding__2025_Update___1_.md` | Agent tool ecosystem (pre-Claude Code teams, pre-Augment Code) | Medium — core technique cards still valid |

## React 16 Material

Any material specifically referencing React 16 class components, lifecycle methods, or pre-Server Components architecture is **fully legacy**. The canonical React guidance is server-first (React 19+, Next.js App Router) as documented in `frontend.md`.
