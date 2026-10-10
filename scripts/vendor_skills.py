"""
Vendor third-party Claude skills and commands, with a lock file and per-file checksums.

Usage:
    python scripts/vendor_skills.py sync <source-id>   download, copy, rewrite the lock (needs network)
    python scripts/vendor_skills.py check              verify the tree against the lock (offline)

The lock is `.claude/skills/VENDORED.json`. It records, per source, where the files came from
(repo and pinned ref, or a local path), which skills and commands it provides, and the sha256 of
every vendored file. Vendored files are never edited by hand: change the lock's `ref` and run `sync`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ".claude/skills/VENDORED.json"
SKILLS_DIR = ".claude/skills"
COMMANDS_DIR = ".claude/commands"
THIRD_PARTY_DIR = "THIRD_PARTY"
_VARIANT = re.compile(r"\{\{(.*?)\|(.*?)\}\}", re.DOTALL)


def sha256(path: Path) -> str:
    """Return the hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_command(text: str) -> str:
    """
    Resolve upstream's `{{plugin|standalone}}` variants for a project-level Claude command.

    The first variant is Claude's own (frontmatter, `$1` arguments); the second is for other hosts.
    The exception is a plugin-namespaced command reference such as `/databricks:setup`: a project
    command is invoked as `/databricks-setup`, so the standalone form is used there.
    """

    def pick(match: re.Match) -> str:
        first, second = match.group(1), match.group(2)
        return second if first.strip().startswith("/databricks:") else first

    return _VARIANT.sub(pick, text)


def load_lock(root: Path = ROOT) -> dict[str, Any]:
    """Read the lock file."""
    return json.loads((root / LOCK_PATH).read_text(encoding="utf-8"))


def save_lock(lock: dict[str, Any], root: Path = ROOT) -> None:
    """Write the lock file with stable key order."""
    text = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    (root / LOCK_PATH).write_text(text, encoding="utf-8")


def _copy_tree(src: Path, dest: Path, exclude: set[str]) -> list[Path]:
    copied = []
    for file in sorted(p for p in src.rglob("*") if p.is_file()):
        if file.name in exclude or ".git" in file.relative_to(src).parts:
            continue
        target = dest / file.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        copied.append(target)
    return copied


def _remove_previous(source: dict[str, Any], root: Path) -> None:
    for relative in source.get("files", {}):
        (root / relative).unlink(missing_ok=True)
    for dest in source["skills"]:
        shutil.rmtree(root / SKILLS_DIR / dest, ignore_errors=True)


def sync_from_root(lock: dict[str, Any], source_id: str, source_root: Path, root: Path = ROOT) -> None:
    """Copy a source's skills, commands and licence files from `source_root` into the repo and record checksums."""
    source = lock["sources"][source_id]
    _remove_previous(source, root)
    written: list[Path] = []
    exclude = set(source.get("exclude", []))
    for dest, relative in source["skills"].items():
        written += _copy_tree(source_root / relative, root / SKILLS_DIR / dest, exclude)
    for name, relative in source.get("commands", {}).items():
        target = root / COMMANDS_DIR / f"{name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_command((source_root / relative).read_text(encoding="utf-8")), encoding="utf-8")
        written.append(target)
    for notice in source.get("notice_files", []):
        target = root / THIRD_PARTY_DIR / source_id / Path(notice).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / notice, target)
        written.append(target)
    source["files"] = {str(path.relative_to(root)): sha256(path) for path in sorted(written)}
    save_lock(lock, root)


def _download(source: dict[str, Any], into: Path) -> Path:
    url = f"{source['repo']}/archive/{source['ref']}.tar.gz"
    archive = into / "source.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive.write_bytes(response.read())
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError("safe tar extraction needs Python >= 3.11.4")
    extracted = into / "src"
    with tarfile.open(archive) as tar:
        tar.extractall(extracted, filter="data")
    (top,) = list(extracted.iterdir())
    return top


def sync(source_id: str, root: Path = ROOT) -> None:
    """Fetch a source at its pinned ref and sync it into the repo."""
    lock = load_lock(root)
    source = lock["sources"][source_id]
    if source["kind"] == "local":
        sync_from_root(lock, source_id, root / source["path"], root)
        return
    with tempfile.TemporaryDirectory() as tmp:
        sync_from_root(lock, source_id, _download(source, Path(tmp)), root)


def _upstream_commit(root: Path) -> str | None:
    path = root / "framework/UPSTREAM.lock"
    if not path.exists():
        return None
    match = re.search(r"^commit=(\w+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _tree_files(source: dict[str, Any], root: Path) -> set[str]:
    found = set()
    for dest in source["skills"]:
        base = root / SKILLS_DIR / dest
        found |= {str(p.relative_to(root)) for p in base.rglob("*") if p.is_file()}
    return found


def check_source(source_id: str, source: dict[str, Any], root: Path) -> list[str]:
    """Return the problems found in one vendored source; an empty list means it matches the lock."""
    problems = []
    for relative, expected in source.get("files", {}).items():
        path = root / relative
        if not path.is_file():
            problems.append(f"{source_id}: missing {relative}")
        elif sha256(path) != expected:
            problems.append(f"{source_id}: {relative} differs from the lock (edited by hand?)")
    locked = set(source.get("files", {}))
    problems += [f"{source_id}: unlocked file {name}" for name in sorted(_tree_files(source, root) - locked)]
    if source["kind"] == "local" and _upstream_commit(root) != source["ref"]:
        problems.append(f"{source_id}: submodule moved to {_upstream_commit(root)}; run `sync {source_id}`")
    return problems


def check(root: Path = ROOT) -> list[str]:
    """Return every problem found across all sources."""
    lock = load_lock(root)
    problems: list[str] = []
    for source_id, source in sorted(lock["sources"].items()):
        problems += check_source(source_id, source, root)
    return problems


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    """Run the CLI against the repo at `root` and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("source_id")
    args = parser.parse_args(argv)
    if args.command == "sync":
        sync(args.source_id, root)
        print(f"synced {args.source_id}")
        return 0
    problems = check(root)
    print("\n".join(problems) if problems else "vendored skills match the lock")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
