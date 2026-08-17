from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os


LEGACY_ROOTS = [
    Path("/Users/luke808/AI/asl-shared"),
    Path("/Users/luke808/AI/asl-r8-5m-lake"),
    Path("/Users/luke808/AI/V flash/data"),
]


@dataclass(frozen=True)
class RootInventory:
    path: str
    exists: bool
    file_count: int
    total_bytes: int
    top_level_entries: tuple[str, ...]


def inventory_root(root: Path) -> RootInventory:
    if not root.exists():
        return RootInventory(str(root), False, 0, 0, ())

    file_count = 0
    total_bytes = 0
    top_level_entries = tuple(sorted(path.name for path in root.iterdir()))
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            file_count += 1
            total_bytes += stat.st_size
    return RootInventory(
        str(root), True, file_count, total_bytes, top_level_entries
    )


def main() -> None:
    payload = {"roots": [asdict(inventory_root(path)) for path in LEGACY_ROOTS]}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
