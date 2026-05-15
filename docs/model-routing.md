# Hipson Model Routing

## Default Rule
Use cheap paid models only. Do not use free models automatically.

## Recommended Tiers

| Tier | Agent | Model | Use for |
|---|---|---|---|
| Cheap sanity review | `reviewer_lite` | `deepseek/deepseek-v3.2` | quick second opinion, checklist review |
| Cheap reliable review | `reviewer_cheap` | `deepseek/deepseek-v3.2` | normal repo delta review |
| Cheap code review | `coder_review_cheap` | `deepseek/deepseek-v3.2` | code-specific review |
| Premium UI/UX review | `premium_ui_ux` | `deepseek/deepseek-v3.2` | visual quality, UX, accessibility and premium polish |
| Cheap critic | `critic_lite` | `deepseek/deepseek-v3.2` | assumptions and architecture critique |
| Cheap memory | `memory_summarizer_cheap` | `google/gemini-3-flash-lite` | progress summaries, changelog compression |
| Cheap long context | `long_context_cheap` | `x-ai/grok-4.1-fast` | large packets, long docs, broad scans |
| Strong architecture | `architect_strong` | `openai/gpt-5.5-mini` | important architecture/review decisions |
| Max review | `architect_max` | `openai/gpt-5.5-mini` | hard design/security review only |

## Cost Snapshot
Snapshot from OpenRouter model API on 2026-05-10:

| Model | Input / 1M | Output / 1M | Context |
|---|---:|---:|---:|
| `google/gemini-2.5-flash-lite` | $0.10 | $0.40 | 1,048,576 |
| `xiaomi/mimo-v2-flash` | $0.10 | $0.30 | 262,144 |
| `z-ai/glm-4.7-flash` | $0.06 | $0.40 | 202,752 |
| `qwen/qwen3.6-35b-a3b` | $0.15 | $1.00 | 262,144 |
| `x-ai/grok-4.1-fast` | $0.20 | $0.50 | 2,000,000 |
| `deepseek/deepseek-v3.2` | $0.252 | $0.378 | 131,072 |
| `qwen/qwen3.6-plus` | $0.325 | $1.95 | 1,000,000 |
| `minimax/minimax-m2.5` | $0.15 | $1.15 | 196,608 |
| `minimax/minimax-m2.7` | $0.299 | $1.20 | 196,608 |
| `openai/gpt-5.4-mini` | $0.75 | $4.50 | 400,000 |

## Practical Routing

Use `hipson sidecar route --task "..." --risk ...` for deterministic suggestions
from `config/agents.json`. The router reads `expertise`, `use_when`,
`avoid_when`, `context_budget`, and `can_handle_sensitive_context`; it is a
gate, not an autonomous decision maker.

Use `reviewer_lite` for:
- simple sanity checks;
- prompt critique;
- test gap brainstorming.

Use `reviewer_cheap` for:
- real code review packets;
- security/test review;
- stable output needed.

Use `coder_review_cheap` for:
- implementation-focused review;
- type/API misuse;
- test-quality review.

Use `premium_ui_ux` for:
- landing pages and public-facing UI;
- screenshot-driven visual critique;
- typography, spacing, image crop/quality, controls, accessibility, and responsive polish;
- catching anything that feels default, cheap, visually mismatched, or below premium level.

Note: `qwen/qwen3.6-35b-a3b` is a promising cheap coding model, but returned empty content in one Hipson smoke test, so `deepseek/deepseek-v3.2` remains the default.

Use `critic_lite` for:
- weak assumptions;
- architecture critique;
- cheap second opinion.

Note: `z-ai/glm-4.7-flash` is very cheap, but returned empty content in one Hipson smoke test, so it is not a default agent.

Use `memory_summarizer_cheap` for:
- `docs/hipson-progress.md` updates;
- handoff summaries;
- changelog compression.

Use `long_context_cheap` for:
- large docs;
- multi-repo scan synthesis;
- long PR review summaries.

Use `architect_strong` or `architect_max` for:
- cross-module architecture;
- risky implementation plans;
- deciding between competing approaches.

## Free Model Policy
Do not use free models unless the user explicitly asks for a free-only run.
