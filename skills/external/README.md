# External skill library (fetched, not vendored)

Hipson can use a curated set of public agent skills from three upstream catalogs. They are
third-party content under their own licenses (some are not open source), so this repository
does **not** redistribute copies. `manifest.json` lists the selected skills and pins each source
to an upstream commit; `docs/vendored-skills-provenance.json` records a tree hash per skill.

Fetch them locally when you want them:

```bash
python scripts/fetch_external_skills.py            # all 44 skills into skills/external/
python scripts/fetch_external_skills.py --list     # what is available
python scripts/fetch_external_skills.py --only anthropic/frontend-design
```

The script downloads only the listed directories (sparse checkout) at the pinned commit, refuses
anything whose tree hash differs from the recorded one, and keeps each skill's upstream `LICENSE`
file. Fetched directories are git-ignored. Check each skill's license before redistributing it;
for example, Anthropic's `docx`, `pptx` and `xlsx` skills are "all rights reserved" and governed
by your agreement with Anthropic.

Maintainers move the pins with `--update-pins`, which re-pins every source to its upstream HEAD
and rewrites the recorded tree hashes; review the resulting diff before committing it.

## Sources

| Source | Upstream | Skills |
|---|---|---|
| `openai-curated/` | <https://github.com/openai/skills> (`skills/.curated`) | 28: Figma, browser/screenshot testing, GitHub, security, docs, deployment |
| `vercel-labs/` | <https://github.com/vercel-labs/agent-skills> (`skills`) | 5: React, composition, transitions, web design, Vercel deployment |
| `anthropic/` | <https://github.com/anthropics/skills> (`skills`) | 11: frontend design, brand, themes, web-app testing, artifacts, MCP, documents |

Codex and Claude Code load *active* skills from their own skill directories (for example
`~/.codex/skills`); this folder is a project-local reference for Hipson packets and sidecars.
