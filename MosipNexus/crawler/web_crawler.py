"""
Website Crawler

Crawls the MOSIP website from a base URL, follows internal links,
extracts the main content from each page, converts it to Markdown,
and stores the results in JSON.

"""

import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    WEBSITE_FILE,
    WEBSITE_BASE_URL,
    HTTP_HEADERS, 
    CRAWL_DELAY_SECS,
)




def fetch_page_as_markdown(url: str) -> str:
    """Fetch a single docs page and return its main content as ATX Markdown."""
    res = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    content = (
        soup.find("main")
        or soup.find("article")
        or soup.find("section")
        or soup.find("div", class_="container")
        or soup.body
    )
    return markdownify(
        str(content),
        heading_style="ATX",
        strip=["script", "style", "nav", "footer", "head"],
    )


def crawl_website(depth: int = 3):
    visited = set()
    docs = []

    base = urlparse(WEBSITE_BASE_URL)

    def _crawl(url, d):
        if d == 0 or url in visited:
            return

        visited.add(url)

        try:
            response = requests.get(
                url,
                headers=HTTP_HEADERS,
                timeout=30,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            content = fetch_page_as_markdown(url)

            if len(content.strip()) > 100:
                docs.append(
                    {
                        "url": url,
                        "content": content,
                        "source_type": "website",
                    }
                )

            for a in soup.find_all("a", href=True):
                next_url = urljoin(url, a["href"]).split("#")[0]

                parsed = urlparse(next_url)

                if (
                    parsed.scheme in ("http", "https")
                    and parsed.netloc == base.netloc
                ):
                    _crawl(next_url, d - 1)

        except Exception as e:
            print(e)

        time.sleep(CRAWL_DELAY_SECS)

    _crawl(WEBSITE_BASE_URL, depth)

    return docs

def crawl_incremental(state: dict) -> tuple[list[dict], list[dict], int]:
    """
    Crawl only new and changed website pages.

    Returns:
        new_pages
        changed_pages
        unchanged_count
    """
    from crawler.state import content_hash

    url_hashes = state.get("website", {}).get("url_hashes", {})

    print(f"Crawling website from {WEBSITE_BASE_URL} ...")

    all_pages = crawl_website(depth=3)

    print(f"Found {len(all_pages)} website pages")

    new_pages = []
    changed_pages = []
    unchanged = 0

    for page in all_pages:
        url = page["url"]
        content = page["content"]

        if not content.strip():
            continue

        chash = content_hash(content)
        page["_hash"] = chash

        old_hash = url_hashes.get(url)

        if old_hash is None:
            new_pages.append(page)
            print(f"NEW      {url}")

        elif old_hash != chash:
            changed_pages.append(page)
            print(f"CHANGED  {url}")

        else:
            unchanged += 1

    return new_pages, changed_pages, unchanged

if __name__ == "__main__":

    print(f"Crawling {WEBSITE_BASE_URL}")

    docs = crawl_website(depth=3)

    with open(WEBSITE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            docs,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"\nCollected {len(docs)} website pages -> {WEBSITE_FILE}"
    )