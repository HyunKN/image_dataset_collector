from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def verify_class(class_dir: Path) -> dict:
    manifest_path = class_dir / "manifest.csv"
    rows = read_manifest(manifest_path)
    image_files = sorted(
        path for path in class_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    file_by_name = {path.name: path for path in image_files}

    manifest_files = [row.get("filename", "") for row in rows if row.get("filename")]
    manifest_hashes = [row.get("sha256", "") for row in rows if row.get("sha256")]
    missing_files = [name for name in manifest_files if name not in file_by_name]
    extra_files = [path.name for path in image_files if path.name not in set(manifest_files)]

    duplicate_manifest_hashes = [
        sha for sha, count in Counter(manifest_hashes).items() if sha and count > 1
    ]
    duplicate_manifest_files = [
        name for name, count in Counter(manifest_files).items() if name and count > 1
    ]

    actual_hash_to_files: dict[str, list[str]] = defaultdict(list)
    hash_mismatches = []
    for path in image_files:
        actual = sha256_file(path)
        actual_hash_to_files[actual].append(path.name)
        expected = next((row.get("sha256", "") for row in rows if row.get("filename") == path.name), "")
        if expected and actual != expected:
            hash_mismatches.append({"filename": path.name, "expected": expected, "actual": actual})

    duplicate_actual_files = {
        sha: names for sha, names in actual_hash_to_files.items() if len(names) > 1
    }

    return {
        "class_id": class_dir.name,
        "manifest_rows": len(rows),
        "image_files": len(image_files),
        "missing_files": missing_files,
        "extra_files": extra_files,
        "duplicate_manifest_hashes": duplicate_manifest_hashes,
        "duplicate_manifest_files": duplicate_manifest_files,
        "duplicate_actual_files": duplicate_actual_files,
        "hash_mismatches": hash_mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify dataset manifests and duplicate files.")
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    args = parser.parse_args()

    if not args.dataset_dir.exists():
        print(json.dumps({"error": "dataset_dir_not_found", "path": str(args.dataset_dir)}, ensure_ascii=False))
        return

    results = [
        verify_class(path)
        for path in sorted(args.dataset_dir.iterdir())
        if path.is_dir()
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
