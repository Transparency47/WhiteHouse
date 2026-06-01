#!/usr/bin/env python3
"""Archive White House news posts as Markdown files.

Default mode performs a full backfill. Use --incremental from cron to scan
recent pages and stop once enough already-seen posts are encountered.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from dateutil import parser as date_parser


BASE_URL = "https://www.whitehouse.gov"
NEWS_URL = f"{BASE_URL}/news/"
ROOT_DIR = Path(__file__).resolve().parents[1]
SCRAPER_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRAPER_DIR / "state.json"
LOCK_PATH = SCRAPER_DIR / ".whitehouse_news_scraper.lock"
LISTING_GENERATOR_PATH = SCRAPER_DIR / "generate_listing.py"
REQUEST_TIMEOUT = 30
USER_AGENT = "WhiteHouseNewsScraper/1.0 (+local archive; respects whitehouse.gov robots.txt)"
INCREMENTAL_SEEN_LIMIT = 25
REQUEST_DELAY_SECONDS = 0.35

CATEGORY_TO_FOLDER = {
    "Releases": Path("Releases"),
    "Briefings & Statements": Path("Briefings"),
    "Fact Sheets": Path("Fact Sheets"),
    "Remarks": Path("Remarks"),
    "Research": Path("Research"),
    "Executive Orders": Path("Actions") / "Executive Orders",
    "Nominations & Appointments": Path("Actions") / "Nominations",
    "Presidential Memoranda": Path("Actions") / "Memoranda",
    "Proclamations": Path("Actions") / "Proclamations",
}

PRESIDENTIAL_SUBCATEGORIES = {
    "Executive Orders",
    "Nominations & Appointments",
    "Presidential Memoranda",
    "Proclamations",
}


@dataclass(frozen=True)
class ListingItem:
    url: str
    title: str
    categories: tuple[str, ...]
    published: dt.datetime


class ScrapeError(Exception):
    pass


def fetch(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def page_url(page: int) -> str:
    if page == 1:
        return NEWS_URL
    return f"{NEWS_URL}page/{page}/"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_listing_page(soup: BeautifulSoup) -> list[ListingItem]:
    items: list[ListingItem] = []
    for post in soup.select("ul.wp-block-post-template > li.wp-block-post"):
        title_link = post.select_one("h2.wp-block-post-title a, h2 a")
        timestamp = post.select_one("time[datetime]")
        if not title_link or not timestamp:
            continue

        categories = tuple(
            clean_text(anchor.get_text(" ", strip=True))
            for anchor in post.select(".taxonomy-category a")
            if clean_text(anchor.get_text(" ", strip=True))
        )
        published = date_parser.parse(timestamp["datetime"])
        items.append(
            ListingItem(
                url=urljoin(BASE_URL, title_link["href"]),
                title=clean_text(title_link.get_text(" ", strip=True)),
                categories=categories,
                published=published,
            )
        )
    return items


def has_next_page(soup: BeautifulSoup) -> bool:
    next_link = soup.find("a", class_="next")
    if next_link:
        return True
    return any(clean_text(a.get_text(" ", strip=True)).lower() == "next" for a in soup.select("a"))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen_urls": {}, "last_successful_run": None}
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("seen_urls", {})
    state.setdefault("last_successful_run", None)
    return state


def save_state(state: dict) -> None:
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(STATE_PATH)


def refresh_listing() -> None:
    if not LISTING_GENERATOR_PATH.exists():
        return
    import subprocess

    subprocess.run([sys.executable, str(LISTING_GENERATOR_PATH)], check=True)


@contextlib.contextmanager
def lock_or_exit() -> Iterable[None]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another scraper run is already active; exiting.", file=sys.stderr, flush=True)
            raise SystemExit(0)
        yield


def slugify(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized)
    normalized = normalized.strip("_")
    return normalized or "Untitled"


def categories_to_folders(categories: tuple[str, ...]) -> list[Path]:
    folders: list[Path] = []
    for category in categories:
        if category == "Presidential Actions":
            continue
        folder = CATEGORY_TO_FOLDER.get(category)
        if folder and folder not in folders:
            folders.append(folder)

    if not folders and "Presidential Actions" in categories:
        folders.append(Path("Actions"))
    return folders


def remove_unwanted_post_nodes(content: Tag) -> None:
    for selector in [
        ".wp-block-whitehouse-topper",
        ".wp-block-post-template",
        ".wp-block-query",
        ".sharedaddy",
        ".wp-block-social-links",
        "script",
        "style",
        "noscript",
    ]:
        for node in content.select(selector):
            node.decompose()

    for heading in list(content.find_all(re.compile("^h[1-6]$"))):
        if clean_text(heading.get_text(" ", strip=True)).lower() == "related":
            for sibling in list(heading.find_next_siblings()):
                sibling.decompose()
            heading.decompose()


def markdown_escape(text: str) -> str:
    return text.replace("\\", "\\\\")


def safe_join_url(href: str) -> str | None:
    try:
        url = urljoin(BASE_URL, href)
    except ValueError:
        return None
    if re.search(r"/Users/[^/?#]+/", url, flags=re.IGNORECASE):
        return None
    return url


def join_inline_parts(parts: list[str]) -> str:
    output = ""
    for part in parts:
        if not part:
            continue
        if (
            output
            and not output[-1].isspace()
            and not part[0].isspace()
            and output[-1] not in "([{/`"
            and part[0] not in ".,;:!?)]}/`"
        ):
            output += " "
        output += part
    return output


def inline_markdown(node) -> str:
    if isinstance(node, NavigableString):
        return markdown_escape(str(node))
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name in {"script", "style", "noscript"}:
        return ""
    if name == "br":
        return "\n"

    text = join_inline_parts([inline_markdown(child) for child in node.children])
    text = re.sub(r"[ \t\r\f\v]+", " ", text)

    if name == "a":
        label = clean_text(text)
        href = node.get("href")
        url = safe_join_url(href) if href else None
        if label and url:
            return f"[{label}]({url})"
        return label
    if name in {"strong", "b"}:
        label = clean_text(text)
        return f"**{label}**" if label else ""
    if name in {"em", "i"}:
        label = clean_text(text)
        return f"*{label}*" if label else ""
    return text


def block_markdown(node: Tag, list_depth: int = 0) -> list[str]:
    name = node.name.lower()

    if name in {"script", "style", "noscript"}:
        return []
    if name in {"p", "blockquote"}:
        text = clean_text(inline_markdown(node))
        if not text:
            return []
        if name == "blockquote":
            return [f"> {line}" for line in text.splitlines()]
        return [text]
    if re.match(r"h[1-6]", name):
        level = int(name[1])
        text = clean_text(inline_markdown(node))
        return [f"{'#' * level} {text}"] if text else []
    if name in {"ul", "ol"}:
        lines: list[str] = []
        ordered = name == "ol"
        for index, li in enumerate([child for child in node.children if isinstance(child, Tag) and child.name == "li"], 1):
            prefix = f"{index}. " if ordered else "- "
            li_copy = BeautifulSoup(str(li), "html.parser").find("li")
            nested_lists = list(li.find_all(["ul", "ol"], recursive=False))
            if li_copy:
                for nested_copy in li_copy.find_all(["ul", "ol"]):
                    nested_copy.decompose()
            first_line = clean_text(inline_markdown(li_copy)) if li_copy else ""
            if first_line:
                lines.append("  " * list_depth + prefix + first_line)
            for nested in nested_lists:
                lines.extend(block_markdown(nested, list_depth + 1))
        return lines
    if name == "hr":
        return ["---"]

    lines: list[str] = []
    for child in node.children:
        if isinstance(child, Tag):
            lines.extend(block_markdown(child, list_depth))
        elif isinstance(child, NavigableString):
            text = clean_text(str(child))
            if text:
                lines.append(text)
    return lines


def page_to_markdown(soup: BeautifulSoup, item: ListingItem) -> str:
    content = soup.select_one(".entry-content.wp-block-post-content")
    if not content:
        content = soup.select_one("main")
    if not content:
        raise ScrapeError(f"Could not locate main content for {item.url}")

    content = BeautifulSoup(str(content), "html.parser").select_one(".entry-content, main")
    if not content:
        raise ScrapeError(f"Could not clone main content for {item.url}")
    remove_unwanted_post_nodes(content)

    blocks: list[str] = [f"# {item.title}"]
    for child in content.children:
        if isinstance(child, Tag):
            for block in block_markdown(child):
                if block and block != blocks[-1]:
                    blocks.append(block)

    return "\n\n".join(blocks).strip() + "\n"


def metadata_entry(item: ListingItem, category: str, filename: str, accessed: dt.datetime) -> str:
    return (
        f"## {item.title}\n\n"
        f"- URL: {item.url}\n"
        f"- Title: {item.title}\n"
        f"- Category: {category}\n"
        f"- Date published: {item.published.isoformat()}\n"
        f"- Date accessed: {accessed.isoformat()}\n"
        f"- Markdown filename: {filename}\n\n"
    )


def append_metadata_once(day_folder: Path, item: ListingItem, category: str, filename: str, accessed: dt.datetime) -> None:
    metadata_path = day_folder / "metadata.md"
    marker = f"- URL: {item.url}"
    if metadata_path.exists() and marker in metadata_path.read_text(encoding="utf-8"):
        return
    with metadata_path.open("a", encoding="utf-8") as handle:
        if metadata_path.stat().st_size == 0:
            handle.write("# Metadata\n\n")
        handle.write(metadata_entry(item, category, filename, accessed))


def output_path_for(item: ListingItem, folder: Path, force: bool) -> Path:
    published = item.published
    day_folder = ROOT_DIR / folder / f"{published.year:04d}" / f"{published.month:02d}" / f"{published.day:02d}"
    base = slugify(item.title)
    path = day_folder / f"{base}.md"
    if force or not path.exists():
        return path

    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        if item.url in existing:
            return path

    digest = hashlib.sha1(item.url.encode("utf-8")).hexdigest()[:8]
    return day_folder / f"{base}_{digest}.md"


def archive_item(session: requests.Session, item: ListingItem, state: dict, force: bool) -> bool:
    if not force and item.url in state["seen_urls"]:
        return False

    folders = categories_to_folders(item.categories)
    if not folders:
        print(f"Skipping unsupported categories {item.categories}: {item.url}", file=sys.stderr, flush=True)
        return False

    soup = fetch(session, item.url)
    markdown = page_to_markdown(soup, item)
    accessed = dt.datetime.now(dt.timezone.utc)
    wrote_any = False
    written_paths: list[str] = []

    for folder in folders:
        category = next(
            (category for category, mapped in CATEGORY_TO_FOLDER.items() if mapped == folder),
            folder.name,
        )
        output_path = output_path_for(item, folder, force)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        body = (
            f"<!-- source: {item.url} -->\n"
            f"<!-- date_published: {item.published.isoformat()} -->\n"
            f"<!-- date_accessed: {accessed.isoformat()} -->\n\n"
            f"{markdown}"
        )

        if force or not output_path.exists() or output_path.read_text(encoding="utf-8", errors="ignore") != body:
            output_path.write_text(body, encoding="utf-8")
            wrote_any = True
        append_metadata_once(output_path.parent, item, category, output_path.name, accessed)
        written_paths.append(str(output_path.relative_to(ROOT_DIR)))

    state["seen_urls"][item.url] = {
        "title": item.title,
        "categories": list(item.categories),
        "published": item.published.isoformat(),
        "paths": written_paths,
        "last_accessed": accessed.isoformat(),
    }
    return wrote_any


def iter_listing_items(session: requests.Session, max_pages: int | None = None):
    page = 1
    while True:
        soup = fetch(session, page_url(page))
        items = parse_listing_page(soup)
        if not items:
            break
        yield page, items

        if max_pages is not None and page >= max_pages:
            break
        if not has_next_page(soup):
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)


def run(args: argparse.Namespace) -> int:
    state = load_state()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    total_seen = 0
    total_archived = 0
    consecutive_seen = 0
    max_pages = args.max_pages

    for page, items in iter_listing_items(session, max_pages=max_pages):
        print(f"Scanning page {page}: {len(items)} posts", flush=True)
        for item in items:
            total_seen += 1
            already_seen = item.url in state["seen_urls"]
            if already_seen and args.incremental and not args.force:
                consecutive_seen += 1
                if consecutive_seen >= args.seen_limit:
                    state["last_successful_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    save_state(state)
                    print(f"Stopping incremental run after {consecutive_seen} already-seen posts.", flush=True)
                    print(f"Archived {total_archived} new/updated posts from {total_seen} listings.", flush=True)
                    return 0
                continue

            consecutive_seen = 0
            try:
                if archive_item(session, item, state, force=args.force):
                    total_archived += 1
                    print(f"Archived: {item.title}", flush=True)
            except Exception as exc:
                print(f"ERROR {item.url}: {exc}", file=sys.stderr, flush=True)
            time.sleep(REQUEST_DELAY_SECONDS)

        save_state(state)

    state["last_successful_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_state(state)
    print(f"Archived {total_archived} new/updated posts from {total_seen} listings.", flush=True)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive whitehouse.gov/news posts as Markdown.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true", help="Scan every available news page (default).")
    mode.add_argument("--incremental", action="store_true", help="Scan recent pages and stop after enough seen posts.")
    parser.add_argument("--force", action="store_true", help="Re-fetch and overwrite posts that are already in state.")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit listing pages scanned, useful for tests.")
    parser.add_argument("--seen-limit", type=int, default=INCREMENTAL_SEEN_LIMIT, help="Seen-post stop threshold for --incremental.")
    args = parser.parse_args(argv)
    if not args.incremental:
        args.backfill = True
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    with lock_or_exit():
        result = run(args)
        if result == 0:
            refresh_listing()
        return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
