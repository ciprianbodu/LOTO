"""Reproduce the 12 certified La Jolla covering-design imports from 2026-10-15.

The source is the immutable Zenodo record 19735294, licensed CC BY 4.0.
Only 60,848 bytes in pinned HTTP ranges are needed from its 4.19 GB archive.
No downloaded code is executed. Every section and normalized design has a
pinned SHA-256; every block and every guaranteed subset is also checked.

Validate downloaded sections, without writing any design (default dry run):
    python scripts/analysis/import_ljcr_designs.py --download
Reproduce into a new directory, retaining raw sections for an offline replay:
    python scripts/analysis/import_ljcr_designs.py --download \
        --save-sections scratch/ljcr-sections --output scratch/ljcr-designs
Offline replay:
    python scripts/analysis/import_ljcr_designs.py \
        --sections-dir scratch/ljcr-sections --output scratch/ljcr-replayed

Existing different output files are refused. This is an offline maintenance
utility, never invoked by application startup or ticket generation.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import urllib.request

MANIFEST = Path(__file__).with_name("ljcr_designs_2026_10_15.json")
ARCHIVE_URL = "https://zenodo.org/api/records/19735294/files/covers.json/content"
ARCHIVE_BYTES = 4_191_522_340


def verify_section(payload: bytes, record: dict) -> bytes:
    """Validate a pinned JSON section, return the normalized design as bytes.

    Raises ValueError for an altered source, invalid block, duplicate ticket,
    incomplete guarantee, mismatched geometry/count, or changed normalized
    content. It performs no I/O and uses only Python's standard library.
    """
    if hashlib.sha256(payload).hexdigest() != record["section_sha256"]:
        raise ValueError(f"{record['file']}: source SHA-256 mismatch")
    v, pick, guarantee = (record[k] for k in ("v", "pick", "guarantee"))
    if not all(type(x) is int for x in (v, pick, guarantee)) or not (
        1 <= guarantee < pick <= v <= 16
    ):
        raise ValueError("Unsupported design geometry")
    filename = f"C_{v}_{pick}_{guarantee}.txt"
    if record["file"] != filename:
        raise ValueError("Design filename and geometry disagree")
    key = f"C({v},{pick},{guarantee})"
    try:
        parsed = json.loads(b"{" + payload + b"}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{filename}: invalid JSON section") from exc
    if list(parsed) != [key] or not isinstance(parsed[key], list):
        raise ValueError(f"{filename}: section key and geometry disagree")
    blocks = []
    for row in parsed[key]:
        if (
            not isinstance(row, list)
            or len(row) != pick
            or any(type(x) is not int or not 1 <= x <= v for x in row)
            or len(set(row)) != pick
        ):
            raise ValueError(f"{filename}: invalid block")
        blocks.append(tuple(sorted(row)))
    if len(blocks) != record["blocks"] or not blocks:
        raise ValueError(f"{filename}: unexpected number of blocks")
    if len(set(blocks)) != len(blocks):
        raise ValueError(f"{filename}: duplicate block")
    covered = {sub for row in blocks for sub in itertools.combinations(row, guarantee)}
    total = math.comb(v, guarantee)
    if len(covered) != total or record["coverage_subsets"] != total:
        raise ValueError(f"{filename}: incomplete guarantee ({len(covered)}/{total})")
    normalized = ("\n".join(" ".join(map(str, row)) for row in blocks) + "\n").encode("ascii")
    if hashlib.sha256(normalized).hexdigest() != record["design_sha256"]:
        raise ValueError(f"{filename}: normalized SHA-256 mismatch")
    return normalized


def download_section(record: dict) -> bytes:
    """Read exactly one pinned numeric section; reject ignored HTTP ranges."""
    start, end = record["start"], record["end"]
    if not (type(start) is int and type(end) is int and 0 <= start <= end < ARCHIVE_BYTES):
        raise ValueError("Invalid archive byte range")
    size = end - start + 1
    request = urllib.request.Request(
        ARCHIVE_URL,
        headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        expected = f"bytes {start}-{end}/{ARCHIVE_BYTES}"
        if response.status != 206 or response.headers.get("Content-Range") != expected:
            raise ValueError("Server did not honor the pinned archive byte range")
        payload = response.read(size + 1)
    if len(payload) != size:
        raise ValueError("Unexpected archive section length")
    return payload


def write_verified_files(directory: Path, files: list[tuple[str, bytes]]) -> None:
    """Refuse conflicting files before writing the verified collection."""
    for filename, content in files:
        target = directory / filename
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            raise ValueError(f"Refusing to overwrite different content: {target}")
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in files:
        target = directory / filename
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--download", action="store_true", help="fetch only pinned Zenodo sections")
    source.add_argument("--sections-dir", type=Path, help="read previously saved raw JSON sections")
    parser.add_argument("--save-sections", type=Path, help="retain verified raw sections for offline replay")
    parser.add_argument("--output", type=Path, help="write verified designs; absent means validation only")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["archive_url"] != ARCHIVE_URL or manifest["archive_bytes"] != ARCHIVE_BYTES:
        raise ValueError("Manifest does not identify the pinned archive")
    designs, sections = [], []
    for record in manifest["records"]:
        name = record["file"]
        payload = (
            download_section(record)
            if args.download
            else (args.sections_dir / (name + ".json")).read_bytes()
        )
        normalized = verify_section(payload, record)
        designs.append((name, normalized))
        sections.append((name + ".json", payload))
        print(f"{name}: {record['blocks']} blocks, {record['coverage_subsets']} subsets, 100%")
    if args.save_sections is not None:
        write_verified_files(args.save_sections, sections)
    if args.output is not None:
        write_verified_files(args.output, designs)
        print(f"Wrote {len(designs)} verified designs to {args.output.resolve()}")
    else:
        print(f"Validated {len(designs)} designs; no design files written.")


if __name__ == "__main__":
    main()
