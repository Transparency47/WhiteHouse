#!/usr/bin/env python3
"""Generate listing.json for the Transparency47 White House archive."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LISTING_PATH = ROOT_DIR / "listing.json"


def stable_id(source: str, path: str) -> str:
    digest = hashlib.sha1(f"{source}:{path}".encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def first_heading(markdown: str) -> str | None:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else None


def html_comment(markdown: str, label: str) -> str | None:
    match = re.search(rf"<!--\s*{re.escape(label)}:\s*(.*?)\s*-->", markdown, re.IGNORECASE)
    return match.group(1).strip() if match else None


def metadata_line(markdown: str, title: str, label: str) -> str | None:
    section_pattern = re.compile(rf"^##\s+{re.escape(title)}\s*$([\s\S]*?)(?:^##\s+|\Z)", re.MULTILINE)
    section = section_pattern.search(markdown)
    if not section:
        return None
    line_pattern = re.compile(rf"^-\s+{re.escape(label)}:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    match = line_pattern.search(section.group(1))
    return match.group(1).strip() if match else None


def category_from_path(relative_path: str) -> str:
    parts = relative_path.split("/")
    if parts[0] == "Actions" and len(parts) > 1 and not parts[1].isdigit():
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def summary_from(markdown: str) -> str | None:
    body = re.sub(r"<!--[\s\S]*?-->", "", markdown)
    body = re.sub(r"^#\s+.+?$", "", body, count=1, flags=re.MULTILINE)
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", body)]
    for paragraph in paragraphs:
        if paragraph and not paragraph.startswith("#"):
            return paragraph[:280]
    return None


def metadata_for(path: Path, title: str, label: str) -> str | None:
    metadata_path = path.parent / "metadata.md"
    if not metadata_path.exists():
        return None
    return metadata_line(read_text(metadata_path), title, label)


def build_record(path: Path) -> dict:
    relative_path = path.relative_to(ROOT_DIR).as_posix()
    markdown = read_text(path)
    title = first_heading(markdown) or path.stem.replace("_", " ")
    category = metadata_for(path, title, "Category") or category_from_path(relative_path)
    date = html_comment(markdown, "date_published") or metadata_for(path, title, "Date published")
    if date and len(date) > 10:
        date = date[:10]
    return {
        "id": stable_id("whitehouse", relative_path),
        "title": title,
        "path": relative_path,
        "category": category,
        "kind": "whitehouse_record",
        "date": date,
        "sourceUrl": html_comment(markdown, "source") or metadata_for(path, title, "URL"),
        "summary": summary_from(markdown),
        "metadata": {
            "dateAccessed": html_comment(markdown, "date_accessed") or metadata_for(path, title, "Date accessed"),
        },
    }


def discover_records() -> list[Path]:
    records = []
    for path in ROOT_DIR.rglob("*.md"):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative == "README.md" or relative.startswith("Scrapers/") or relative.endswith("/metadata.md"):
            continue
        records.append(path)
    return sorted(records, key=lambda p: p.relative_to(ROOT_DIR).as_posix())


def build_listing() -> dict:
    records = [build_record(path) for path in discover_records()]
    records.sort(key=lambda row: (row.get("date") or "", row.get("title") or ""), reverse=True)
    return {
        "version": 1,
        "source": "whitehouse",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "records": records,
    }


def write_listing(path: Path = LISTING_PATH) -> None:
    listing = build_listing()
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(listing, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)
    print(f"Wrote {path.relative_to(ROOT_DIR)} with {len(listing['records'])} records.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate White House listing.json.")
    parser.parse_args()
    write_listing()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
