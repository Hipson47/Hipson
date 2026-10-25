#!/usr/bin/env python3
"""Fetch the optional external skill library from its upstream repositories.

The skills listed in `skills/external/manifest.json` are third-party content under their own
licenses, so Hipson does not redistribute copies. This script downloads exactly the listed
skill directories at the commit pinned per source, and checks each one against the tree hash
recorded in `docs/vendored-skills-provenance.json`.

Usage:
    python scripts/fetch_external_skills.py                 # fetch all into skills/external/
    python scripts/fetch_external_skills.py --only anthropic/frontend-design
    python scripts/fetch_external_skills.py --list
    python scripts/fetch_external_skills.py --update-pins   # maintainers: move pins to upstream HEAD

Requires `git`. Fetched skills are git-ignored; each keeps its upstream LICENSE file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "external" / "manifest.json"
PROVENANCE = ROOT / "docs" / "vendored-skills-provenance.json"


def tree_hash(directory: Path) -> tuple[str, int, int]:
    """sha256 over (relative POSIX path, NUL, content, NUL) for every file, sorted by path."""
    digest = hashlib.sha256()
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    total = 0
    for path in files:
        data = path.read_bytes()
        total += len(data)
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0" + data + b"\0")
    return digest.hexdigest(), len(files), total


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603,S607
    return result.stdout.strip()


def checkout(url: str, ref: str, paths: list[str], into: Path) -> None:
    git("clone", "--quiet", "--filter=blob:none", "--no-checkout", "--sparse", url, str(into))
    git("sparse-checkout", "set", "--no-cone", *[f"/{p}/" for p in paths], cwd=into)
    git("checkout", "--quiet", ref, cwd=into)


def selected(manifest: dict, only: list[str]) -> list[tuple[dict, list[str]]]:
    wanted = {item.strip("/") for item in only}
    plan = []
    for source in manifest["sources"]:
        names = [n for n in source["installed"] if not wanted or f"{source['id']}/{n}" in wanted or source["id"] in wanted]
        if names:
            plan.append((source, names))
    unknown = wanted - {f"{s['id']}/{n}" for s, ns in plan for n in ns} - {s["id"] for s, _ in plan}
    if unknown:
        sys.exit(f"Unknown skills: {', '.join(sorted(unknown))}")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=ROOT / "skills" / "external", help="Target directory")
    parser.add_argument("--only", action="append", default=[], help="provider or provider/skill; repeatable")
    parser.add_argument("--list", action="store_true", help="List the available skills and exit")
    parser.add_argument("--update-pins", action="store_true",
                        help="Pin every source to its upstream HEAD and rewrite the recorded tree hashes")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    recorded = {(s["provider"], s["name"]): s for s in provenance["skills"]}

    if args.list:
        for source in manifest["sources"]:
            for name in source["installed"]:
                print(f"{source['id']}/{name}  ({source['url']} @ {source['ref'][:12]})")
        return 0

    plan = selected(manifest, args.only)
    failures = 0
    for source, names in plan:
        ref = source.get("ref", "")
        if not ref and not args.update_pins:
            sys.exit(f"Source {source['id']} has no pinned ref; run with --update-pins first")
        if args.update_pins:
            ref = git("ls-remote", source["url"], "HEAD").split()[0]
            source["ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            print(f"{source['id']}: {source['url']} @ {ref[:12]} ({len(names)} skills)")
            checkout(source["url"], ref, [f"{source['path']}/{n}" for n in names], repo)
            for name in names:
                src = repo / source["path"] / name
                if not src.is_dir():
                    print(f"  MISSING {name} at {ref[:12]}")
                    failures += 1
                    continue
                digest, count, size = tree_hash(src)
                key = (source["id"], name)
                if args.update_pins:
                    recorded[key] = {
                        "name": name, "provider": source["id"],
                        "path": f"skills/external/{source['id']}/{name}",
                        "source": f"{source['url']}/tree/{ref}/{source['path']}/{name}",
                        "file_count": count, "bytes": size, "sha256_tree": digest,
                    }
                elif key not in recorded or recorded[key]["sha256_tree"] != digest:
                    print(f"  HASH MISMATCH {name}: upstream content differs from the recorded tree hash")
                    failures += 1
                    continue
                target = args.dest / source["id"] / name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(src, target, ignore=shutil.ignore_patterns(".git"))
                license_file = next((p.name for p in target.iterdir() if p.name.upper().startswith("LICENSE")), None)
                print(f"  ok {name} ({count} files{', ' + license_file if license_file else ''})")

    if args.update_pins:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        provenance["note"] = (
            "sha256_tree = sha256 over (relative POSIX path, NUL, content, NUL) for every file, "
            "sorted by path; computed by scripts/fetch_external_skills.py at the pinned upstream ref."
        )
        provenance["skills"] = sorted(recorded.values(), key=lambda s: (s["provider"], s["name"]))
        PROVENANCE.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        print(f"Pins and tree hashes updated in {MANIFEST.relative_to(ROOT)} and {PROVENANCE.relative_to(ROOT)}")
    if failures:
        print(f"{failures} skill(s) failed; nothing was copied for them.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
