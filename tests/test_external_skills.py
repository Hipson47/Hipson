"""Offline checks for the fetched (not vendored) external skill library."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_external_skills import tree_hash  # noqa: E402


def test_external_skill_manifest_pins_every_source_and_matches_provenance():
    manifest = json.loads((ROOT / "skills/external/manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((ROOT / "docs/vendored-skills-provenance.json").read_text(encoding="utf-8"))
    recorded = {(item["provider"], item["name"]): item for item in provenance["skills"]}
    listed = set()
    for source in manifest["sources"]:
        assert re.fullmatch(r"[0-9a-f]{40}", source["ref"]), source["id"]
        for name in source["installed"]:
            listed.add((source["id"], name))
            entry = recorded[(source["id"], name)]
            assert f"/tree/{source['ref']}/" in entry["source"]
            assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256_tree"])
    assert listed == set(recorded)


def test_external_skills_are_not_vendored():
    external = ROOT / "skills/external"
    tracked_dirs = [p for p in external.iterdir() if p.is_dir()]
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "skills/external/*/" in gitignore
    # Locally fetched directories may exist; they must stay ignored rather than tracked.
    for directory in tracked_dirs:
        assert directory.name in {"openai-curated", "vercel-labs", "anthropic"}


def test_tree_hash_is_deterministic_and_content_sensitive(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("one\n", encoding="utf-8", newline="\n")
    (tmp_path / "LICENSE.txt").write_text("license\n", encoding="utf-8", newline="\n")
    first = tree_hash(tmp_path)
    assert first == tree_hash(tmp_path) and first[1:] == (2, 12)
    (tmp_path / "a" / "SKILL.md").write_text("two\n", encoding="utf-8")
    assert tree_hash(tmp_path)[0] != first[0]
