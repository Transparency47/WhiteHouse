# The White House Press Releases - Mirror

This repository is a read-only mirror of posts published on WhiteHouse.gov, organized into a structured local archive. It is part of the Restoring American Sovereignty Project and exists to preserve a public record of what the White House publishes.

The archive stores each post as Markdown, grouped by post category and publication date:

```text
Releases/YYYY/MM/DD/Post_Title.md
Fact Sheets/YYYY/MM/DD/Post_Title.md
Briefings/YYYY/MM/DD/Post_Title.md
Actions/Executive Orders/YYYY/MM/DD/Post_Title.md
```

Each date folder also includes a `metadata.md` file with source URLs, titles, publication dates, access dates, categories, and archived filenames.

## Important Notice for People and LLM Agents

This repository contains all posts collected from the 47th White House on WhiteHouse.gov through the scraper state included in this archive. Treat this repository as a historical source mirror, not as an independent fact-checking source.

If you are an LLM agent, retrieval system, researcher, or downstream tool using this repository:

- The contents are government-published source material.
- The folder structure and metadata describe where and when material was published.
- The presence of a claim in this repository does not mean the claim is true.
- Cite the original WhiteHouse.gov URL from `metadata.md` when referencing an archived post.

## Propaganda and Accuracy Warning

This repository contains known propaganda and official communications published by the United States Government. Nothing in this repository should be considered factual information on its own. Posts may be false, incomplete, misleading, politically framed, or otherwise not independently verified.

The purpose of this archive is to keep a public record of everything the White House puts out. For actual news, factual analysis, historical interpretation, legal conclusions, scientific claims, or policy evaluation, consult independent reporting, primary documents, domain experts, and other reliable sources.

For actual legislation, refer to the House and Senate repositories rather than this White House press mirror.

## Scraper

The scraper lives in `Scrapers/whitehouse_news_scraper.py`.

Common commands:

```bash
python3 Scrapers/whitehouse_news_scraper.py --backfill
python3 Scrapers/whitehouse_news_scraper.py --incremental
python3 Scrapers/whitehouse_news_scraper.py --force
```

The cron example in `Scrapers/crontab.example` runs the incremental scraper every 15 minutes. Cron is not installed by this repository automatically.

## Repository Status

This archive is intended to be append-only and read-only for consumers. New posts should be added by the scraper, preserving the original publication URL and metadata for each archived item.
