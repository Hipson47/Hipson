"""Local JSONL memory store for Hipson decisions and handoffs."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hipson.redaction import is_sensitive_path, redact_metadata, redact_text

NOTES_FILE = "notes.jsonl"
SOURCES_FILE = "sources.jsonl"


@dataclass
class MemorySource:
    id: str
    note_id: str
    path: str
    detail: str = ""
    created_at: str = field(default_factory=lambda: timestamp())


@dataclass
class MemoryNote:
    id: str
    scope: str
    repo: str
    kind: str
    summary: str
    source_refs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    created_at: str = field(default_factory=lambda: timestamp())
    updated_at: str = field(default_factory=lambda: timestamp())


@dataclass
class MemorySearchResult:
    note: MemoryNote
    score: int


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def memory_dir(path: str | None = None) -> Path:
    return Path(path).expanduser().resolve() if path else (Path.cwd() / "memory").resolve()


def notes_path(root: Path) -> Path:
    return root / NOTES_FILE


def sources_path(root: Path) -> Path:
    return root / SOURCES_FILE


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_notes(root: Path) -> list[MemoryNote]:
    notes = []
    for row in read_jsonl(notes_path(root)):
        notes.append(
            MemoryNote(
                id=str(row.get("id", "")),
                scope=str(row.get("scope", "")),
                repo=str(row.get("repo", "")),
                kind=str(row.get("kind", "")),
                summary=str(row.get("summary", "")),
                source_refs=[str(item) for item in row.get("source_refs", [])],
                tags=[str(item) for item in row.get("tags", [])],
                confidence=float(row.get("confidence", 1.0)),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
            )
        )
    return notes


def add_note(
    *,
    root: Path,
    scope: str,
    repo: str,
    kind: str,
    summary: str,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    confidence: float = 1.0,
) -> MemoryNote:
    sources = sources or []
    for source in sources:
        if is_sensitive_path(source) or source != redact_text(source):
            raise SystemExit(f"Refusing to store sensitive source path: {source}")

    note_id = uuid.uuid4().hex
    source_records = [
        MemorySource(id=uuid.uuid4().hex, note_id=note_id, path=redact_metadata(source))
        for source in sources
    ]
    note = MemoryNote(
        id=note_id,
        scope=redact_metadata(scope),
        repo=redact_metadata(repo),
        kind=redact_metadata(kind),
        summary=redact_text(summary),
        source_refs=[source.id for source in source_records],
        tags=[redact_metadata(tag) for tag in (tags or [])],
        confidence=confidence,
    )
    append_jsonl(notes_path(root), asdict(note))
    for source in source_records:
        append_jsonl(sources_path(root), asdict(source))
    return note


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9_.:/-]+", text) if len(token) > 1}


def search_notes(
    *,
    root: Path,
    query: str,
    repo: str | None = None,
    scope: str | None = None,
    limit: int = 5,
) -> list[MemorySearchResult]:
    query_tokens = tokenize(query)
    results = []
    for note in load_notes(root):
        if repo and note.repo != repo:
            continue
        if scope and note.scope != scope:
            continue
        haystack = " ".join([note.scope, note.repo, note.kind, note.summary, " ".join(note.tags)])
        score = len(query_tokens.intersection(tokenize(haystack)))
        if query.lower() and query.lower() in haystack.lower():
            score += 3
        if score:
            results.append(MemorySearchResult(note=note, score=score))
    return sorted(results, key=lambda result: (-result.score, result.note.updated_at))[:limit]


def format_note(note: MemoryNote) -> str:
    tags = ", ".join(note.tags) or "none"
    sources = ", ".join(note.source_refs) or "none"
    return "\n".join(
        [
            f"- id: `{note.id}`",
            f"  scope: `{note.scope}`",
            f"  repo: `{note.repo}`",
            f"  kind: `{note.kind}`",
            f"  tags: `{tags}`",
            f"  confidence: `{note.confidence}`",
            f"  sources: `{sources}`",
            f"  summary: {note.summary}",
        ]
    )


def command_add(args: argparse.Namespace) -> None:
    note = add_note(
        root=memory_dir(args.memory_dir),
        scope=args.scope,
        repo=args.repo,
        kind=args.kind,
        summary=args.summary,
        tags=parse_csv(args.tags),
        sources=args.source or [],
        confidence=args.confidence,
    )
    print(f"Added memory note {note.id}")


def command_search(args: argparse.Namespace) -> None:
    results = search_notes(
        root=memory_dir(args.memory_dir),
        query=args.query,
        repo=args.repo,
        scope=args.scope,
        limit=args.limit,
    )
    if not results:
        print("No memory notes found.")
        return
    for result in results:
        print(f"score: {result.score}")
        print(format_note(result.note))


def command_list(args: argparse.Namespace) -> None:
    notes = load_notes(memory_dir(args.memory_dir))
    if args.repo:
        notes = [note for note in notes if note.repo == args.repo]
    if args.scope:
        notes = [note for note in notes if note.scope == args.scope]
    if not notes:
        print("No memory notes found.")
        return
    for note in notes[-args.limit :]:
        print(format_note(note))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local Hipson memory")
    parser.add_argument("--memory-dir", help="Memory directory; defaults to repo-local memory/")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add a durable memory note")
    add.add_argument("--scope", default="global", help="Memory scope")
    add.add_argument("--repo", default="", help="Repository path or name")
    add.add_argument("--kind", default="note", help="Memory kind, e.g. decision, risk, handoff")
    add.add_argument("--summary", required=True, help="Memory summary")
    add.add_argument("--tags", help="Comma-separated tags")
    add.add_argument("--source", action="append", help="Source path or reference; repeatable")
    add.add_argument("--confidence", type=float, default=1.0)
    add.set_defaults(func=command_add)

    search = subparsers.add_parser("search", help="Search memory notes")
    search.add_argument("query")
    search.add_argument("--repo")
    search.add_argument("--scope")
    search.add_argument("--limit", type=int, default=5)
    search.set_defaults(func=command_search)

    list_cmd = subparsers.add_parser("list", help="List recent memory notes")
    list_cmd.add_argument("--repo")
    list_cmd.add_argument("--scope")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.set_defaults(func=command_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
