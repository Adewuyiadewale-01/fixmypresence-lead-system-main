"""Deterministic discovery and state management for local location trees."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


_LOCATIONS_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?LOCATIONS\s*=\s*(\[[\s\S]*\])\s*;?\s*$"
)


def resolve_tree_root(root_value: str | Path, project_root: Path) -> Path:
    """Resolve a configured tree root relative to the repository root."""
    root = Path(str(root_value or "").strip()).expanduser()
    if not root.is_absolute():
        root = project_root / root
    return root.resolve()


def discover_location_files(
    tree_root: Path,
    terminal_filename: str = "LOCATIONS.js",
    country_order: list[str] | None = None,
) -> list[Path]:
    """Return terminal files in deterministic country and path order."""
    if not tree_root.is_dir():
        return []

    countries = [path for path in tree_root.iterdir() if path.is_dir()]
    countries_by_name = {path.name.casefold(): path for path in countries}
    ordered_countries: list[Path] = []
    seen: set[str] = set()

    for name in country_order or []:
        key = str(name).strip().casefold()
        country = countries_by_name.get(key)
        if country is not None and key not in seen:
            ordered_countries.append(country)
            seen.add(key)

    ordered_countries.extend(
        sorted(
            (path for path in countries if path.name.casefold() not in seen),
            key=lambda path: path.name.casefold(),
        )
    )

    files: list[Path] = []
    for country in ordered_countries:
        country_files = [
            path
            for path in country.rglob(terminal_filename)
            if path.is_file()
        ]
        files.extend(
            sorted(
                country_files,
                key=lambda path: path.relative_to(country).as_posix().casefold(),
            )
        )
    return files


def relative_file_id(path: Path, tree_root: Path) -> str:
    """Return the stable, portable ID used in the location-tree state."""
    return path.resolve().relative_to(tree_root.resolve()).as_posix()


def parse_locations_file(path: Path) -> list[str]:
    """Parse a JSON-compatible LOCATIONS assignment without evaluating JS."""
    text = path.read_text(encoding="utf-8")
    match = _LOCATIONS_ASSIGNMENT_RE.match(text)
    if not match:
        raise ValueError(f"{path} must contain a single LOCATIONS = [...] assignment.")

    try:
        values = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} contains an invalid JSON-style location array: {exc}") from exc

    if not isinstance(values, list):
        raise ValueError(f"{path} LOCATIONS value must be an array.")

    locations: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{path} contains a non-string location.")
        location = value.strip()
        if location and location not in seen:
            locations.append(location)
            seen.add(location)

    if not locations:
        raise ValueError(f"{path} does not contain any usable locations.")
    return locations


def load_tree_state(state_file: Path) -> dict:
    """Load persisted tree state, returning an empty state when unavailable."""
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_tree_state(state_file: Path, state: dict) -> None:
    """Atomically persist location-tree state."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["version"] = 1
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = state_file.with_suffix(f"{state_file.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def choose_initial_file(
    file_ids: list[str],
    configured_initial_file: str = "",
) -> str | None:
    """Choose a valid configured initial file or the first discovered file."""
    configured = str(configured_initial_file or "").strip().replace("\\", "/")
    if configured in file_ids:
        return configured
    return file_ids[0] if file_ids else None


def choose_next_file(
    file_ids: list[str],
    current_file: str | None,
    completed_files: set[str],
) -> str | None:
    """Choose the next unfinished file, wrapping once at the end."""
    if not file_ids:
        return None

    start_index = 0
    if current_file in file_ids:
        start_index = (file_ids.index(current_file) + 1) % len(file_ids)

    for offset in range(len(file_ids)):
        candidate = file_ids[(start_index + offset) % len(file_ids)]
        if candidate != current_file and candidate not in completed_files:
            return candidate
    return None


def build_tree_status(
    *,
    tree_root: Path,
    terminal_filename: str,
    country_order: list[str],
    state_file: Path,
    configured_initial_file: str = "",
    include_discovered_files: bool = False,
) -> dict:
    """Build a dashboard/controller-friendly snapshot of the tree."""
    files = discover_location_files(tree_root, terminal_filename, country_order)
    file_ids = [relative_file_id(path, tree_root) for path in files]
    state = load_tree_state(state_file)
    current_file = str(state.get("current_file") or "").strip()
    if current_file not in file_ids:
        current_file = choose_initial_file(file_ids, configured_initial_file) or ""

    completed = {
        str(value).strip()
        for value in state.get("completed_files", [])
        if str(value).strip() in file_ids
    }
    next_file = choose_next_file(file_ids, current_file or None, completed)
    current_index = file_ids.index(current_file) if current_file in file_ids else -1
    parts = Path(current_file).parts if current_file else ()

    status = {
        "enabled": True,
        "tree_root": str(tree_root),
        "terminal_filename": terminal_filename,
        "state_file": str(state_file),
        "state_exists": state_file.exists(),
        "files_total": len(file_ids),
        "files_completed": len(completed),
        "files_remaining_after_current": max(0, len(file_ids) - len(completed) - (1 if current_file and current_file not in completed else 0)),
        "current_file": current_file or None,
        "current_file_index": current_index,
        "current_file_completed": bool(current_file and current_file in completed),
        "current_country": parts[0] if parts else None,
        "next_file": next_file,
        "has_next_file": next_file is not None,
        "tree_exhausted": bool(file_ids) and next_file is None,
        "completed_files": sorted(completed, key=str.casefold),
    }
    if include_discovered_files:
        status["discovered_files"] = file_ids
    return status
