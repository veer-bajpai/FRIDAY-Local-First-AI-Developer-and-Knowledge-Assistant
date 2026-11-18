"""Compact repository overview for agent prompts (shared by both agent implementations).

Uses ``git ls-files`` when available so ignored and generated files are skipped, groups files
by directory and caps the output, so very large repositories still fit in the context window.
"""
from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".next", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "data",
    ".pytest_cache", ".mypy_cache", ".friday", ".turbo", "coverage", "target",
}


def list_files(root: Path, limit: int = 20000) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        files = [line for line in result.stdout.splitlines() if line.strip()]
        if files:
            return sorted(f for f in files if not (set(Path(f).parts) & IGNORE_DIRS))[:limit]
    except (OSError, subprocess.SubprocessError):
        pass
    rows: list[str] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in IGNORE_DIRS)
        for name in sorted(names):
            rows.append((Path(current) / name).relative_to(root).as_posix())
            if len(rows) >= limit:
                return rows
    return rows


def repo_map(root: Path, max_files: int = 100) -> str:
    files = list_files(root)
    if not files:
        return "(empty project)"
    if len(files) <= max_files:
        return "\n".join(files)
    by_dir: dict[str, list[str]] = defaultdict(list)
    for file in files:
        by_dir[str(Path(file).parent)].append(Path(file).name)
    lines = [f"{len(files)} files in {len(by_dir)} directories (showing a summary)"]
    budget = max_files
    for directory in sorted(by_dir, key=lambda d: (d.count("/"), d)):
        names = by_dir[directory]
        shown = names[: max(1, min(len(names), budget // 4 or 1))]
        extra = f" (+{len(names) - len(shown)} more)" if len(names) > len(shown) else ""
        lines.append(f"{directory}/: {', '.join(shown)}{extra}")
        budget -= len(shown)
        if budget <= 0:
            lines.append("... use list_dir / glob / grep to explore further")
            break
    return "\n".join(lines)
