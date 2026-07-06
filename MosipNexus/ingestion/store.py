"""
Ingestion — Stage 2.

Reads crawled JSON files, chunks the content, embeds with multilingual-e5-base,
and upserts into pgvector collections in PostgreSQL:
  - mosip_docs       — documentation chunks
  - mosip_community  — community Q&A chunks
  - mosip_github     — GitHub issue chunks
  - mosip_code       — source code chunks
  - mosip_confluence — Confluence page chunks
  - mosip_jira       — Jira ticket chunks

Run after the crawlers have completed. Re-running drops and rebuilds each
collection (pre_delete_collection=True on the first batch per collection).
"""

import json
import math
import sys
from pathlib import Path

from langchain_postgres import PGVector
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import create_engine, text as sql_text

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    PG_CONNECTION, CHUNK_OVERLAP, CHUNK_SIZE, CODE_COLLECTION, CODE_FILE,
    COMMUNITY_COLLECTION, COMMUNITY_FILE, CONFLUENCE_COLLECTION, CONFLUENCE_FILE,
    DOCS_COLLECTION, DOCS_FILE, WEBSITE_COLLECTION, WEBSITE_FILE, EMBED_MODEL,
    GITHUB_COLLECTION, GITHUB_FILE, JIRA_COLLECTION, JIRA_FILE,
)

BATCH_SIZE = 100

# ── Shared embedding model ─────────────────────────────────────────────────────

def build_embeddings() -> HuggingFaceEmbeddings:
    """Load the multilingual embedding model (shared across both collections)."""
    return HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        encode_kwargs={"batch_size": 64},
        show_progress=True,
    )


# ── Document preparation ───────────────────────────────────────────────────────

def prepare_doc_documents() -> list[Document]:
    """Load docs JSON and convert to LangChain Documents with URL-prefixed content."""
    with open(DOCS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for item in data:
        if not item.get("content", "").strip():
            continue
        # Prepend URL so the chunk embedding encodes the page it came from.
        content = f"Page: {item['url']}\n\n{item['content']}"
        docs.append(Document(
            page_content=content,
            metadata={
                "source":      item["url"],
                "source_type": "docs",
                "title":       item.get("title", ""),
            },
        ))
    return docs


def prepare_community_documents() -> list[Document]:
    """Load community JSON and convert each Q&A thread to LangChain Documents.

    Each topic produces two document types:
      - question chunk  (tagged question=True)
      - answer chunks   (one per answer, accepted_answer flagged)
    Accepted answers are prepended with [ACCEPTED ANSWER] so the embedding
    encodes their higher relevance.
    """
    with open(COMMUNITY_FILE, encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for item in data:
        base_meta = {
            "source":      item["url"],
            "source_type": "community",
            "title":       item.get("title", ""),
            "tags":        ", ".join(
                t["name"] if isinstance(t, dict) else str(t)
                for t in item.get("tags", [])
            ),
        }

        # Question document
        if item.get("question", "").strip():
            docs.append(Document(
                page_content=f"Question: {item['title']}\n\n{item['question']}",
                metadata={**base_meta, "post_type": "question", "accepted": False, "votes": 0},
            ))

        # Answer documents
        for ans in item.get("answers", []):
            text = ans.get("text", "").strip()
            if not text:
                continue
            prefix = "[ACCEPTED ANSWER] " if ans.get("accepted") else ""
            docs.append(Document(
                page_content=f"{prefix}Thread: {item['title']}\n\n{text}",
                metadata={
                    **base_meta,
                    "post_type": "answer",
                    "accepted":  ans.get("accepted", False),
                    "votes":     ans.get("vote_count", 0),
                },
            ))
    return docs


# ── Ingestion pipeline ─────────────────────────────────────────────────────────

def ingest(documents: list[Document], collection_name: str, embeddings: HuggingFaceEmbeddings) -> None:
    """Chunk and embed a list of documents into a named pgvector collection."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    print(f"  -> {len(chunks)} chunks to embed")

    total_batches = math.ceil(len(chunks) / BATCH_SIZE)
    vectorstore = None
    for i in range(total_batches):
        batch = chunks[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        print(f"  Batch {i + 1}/{total_batches} ({len(batch)} chunks)...")
        if vectorstore is None:
            # pre_delete_collection=True on first batch — clears any existing data
            # for this collection (equivalent to deleting the old vector store).
            vectorstore = PGVector.from_documents(
                documents=batch,
                embedding=embeddings,
                connection=PG_CONNECTION,
                collection_name=collection_name,
                pre_delete_collection=True,
            )
        else:
            vectorstore.add_documents(batch)


def _open_collection(collection_name: str, embeddings: HuggingFaceEmbeddings) -> PGVector:
    """Open an existing pgvector collection."""
    return PGVector(
        embeddings=embeddings,
        connection=PG_CONNECTION,
        collection_name=collection_name,
    )


def delete_chunks_by_url(collection_name: str, url: str) -> None:
    """Delete all chunks for a source URL from the given pgvector collection."""
    engine = create_engine(PG_CONNECTION)
    with engine.connect() as conn:
        conn.execute(
            sql_text(
                "DELETE FROM langchain_pg_embedding "
                "WHERE cmetadata->>'source' = :url "
                "AND collection_id = ("
                "  SELECT uuid FROM langchain_pg_collection WHERE name = :cname"
                ")"
            ),
            {"url": url, "cname": collection_name},
        )
        conn.commit()


def ingest_incremental(
    new_pages: list[dict],
    changed_pages: list[dict],
    collection_name: str,
    embeddings: HuggingFaceEmbeddings,
    source_type: str = "docs",
) -> None:
    """Embed and upsert only new and changed content into an existing collection.

    For changed pages, old chunks are deleted first so stale content is
    replaced cleanly.  For new pages, chunks are simply appended.
    Community topics are always treated as new (append-only).
    """
    if not new_pages and not changed_pages:
        print("  Nothing to update.")
        return

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    vectorstore = _open_collection(collection_name, embeddings)

    # ── Delete stale chunks for changed pages ──────────────────────────────────
    if changed_pages:
        print(f"  Removing stale chunks for {len(changed_pages)} changed page(s)...")
        for page in changed_pages:
            delete_chunks_by_url(collection_name, page["url"])

    # ── Build LangChain Documents ──────────────────────────────────────────────
    all_pages = new_pages + changed_pages
    if source_type == "docs":
        lc_docs = [
            Document(
                page_content=f"Page: {p['url']}\n\n{p['content']}",
                metadata={"source": p["url"], "source_type": "docs", "title": p.get("title", "")},
            )
            for p in all_pages
            if p.get("content", "").strip()
        ]
    elif source_type == "community":
        lc_docs = _community_items_to_documents(all_pages)
    else:
        # Generic path for github / code / confluence / jira incremental updates
        lc_docs = [
            Document(
                page_content=f"{p.get('title', '')}\n\n{p.get('content', '')}".strip(),
                metadata={
                    "source": p.get("url", ""),
                    "source_type": source_type,
                    "title": p.get("title", ""),
                },
            )
            for p in all_pages
            if p.get("content", "").strip()
        ]

    if not lc_docs:
        print("  No embeddable content found.")
        return

    # ── Chunk and embed in batches ─────────────────────────────────────────────
    chunks = splitter.split_documents(lc_docs)
    print(f"  -> {len(chunks)} chunks to embed")
    total_batches = math.ceil(len(chunks) / BATCH_SIZE)
    for i in range(total_batches):
        batch = chunks[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        print(f"  Batch {i + 1}/{total_batches} ({len(batch)} chunks)...")
        vectorstore.add_documents(batch)


def _community_items_to_documents(items: list[dict]) -> list[Document]:
    """Convert raw community topic dicts to LangChain Documents."""
    docs = []
    for item in items:
        tags_raw = item.get("tags", [])
        tags_str = ", ".join(t["name"] if isinstance(t, dict) else str(t) for t in tags_raw)
        base_meta = {
            "source":      item["url"],
            "source_type": "community",
            "title":       item.get("title", ""),
            "tags":        tags_str,
        }
        if item.get("question", "").strip():
            docs.append(Document(
                page_content=f"Question: {item['title']}\n\n{item['question']}",
                metadata={**base_meta, "post_type": "question", "accepted": False, "votes": 0},
            ))
        for ans in item.get("answers", []):
            text = ans.get("text", "").strip()
            if not text:
                continue
            prefix = "[ACCEPTED ANSWER] " if ans.get("accepted") else ""
            docs.append(Document(
                page_content=f"{prefix}Thread: {item['title']}\n\n{text}",
                metadata={**base_meta, "post_type": "answer",
                          "accepted": ans.get("accepted", False), "votes": ans.get("vote_count", 0)},
            ))
    return docs


def prepare_github_documents() -> list[Document]:
    """Load GitHub issues JSON and convert to LangChain Documents.

    Each issue becomes one document with:
      - title prefixed so the embedding encodes the issue subject
      - full content (body + comments) where the resolution lives
      - metadata: repo, state, labels for filtering and display
    """
    if not GITHUB_FILE.exists():
        print(f"  {GITHUB_FILE} not found — skipping GitHub ingestion.")
        return []
    with open(GITHUB_FILE, encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for item in data:
        content = item.get("content", "").strip()
        if not content:
            continue
        full_text = f"GitHub Issue: {item.get('title', '')}\nRepo: {item.get('repo', '')}\n\n{content}"
        docs.append(Document(
            page_content=full_text,
            metadata={
                "source":      item["url"],
                "source_type": "github",
                "title":       item.get("title", ""),
                "repo":        item.get("repo", ""),
                "state":       item.get("state", ""),
                "labels":      item.get("labels", ""),
            },
        ))
    return docs


def prepare_code_documents() -> list[Document]:
    """Load MOSIP source code JSON and convert to LangChain Documents.

    Each file becomes one document. The language and file path are embedded in
    the text so the model understands what kind of file it is reading.
    Code files use smaller chunks (set via CHUNK_SIZE) so method-level
    context is preserved rather than mixing unrelated classes.
    """
    if not CODE_FILE.exists():
        print(f"  {CODE_FILE} not found — skipping code ingestion.")
        return []
    with open(CODE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for item in data:
        content = item.get("content", "").strip()
        if not content:
            continue
        lang      = item.get("language", "")
        file_path = item.get("file_path", "")
        repo      = item.get("repo", "")
        # Prefix gives the embedding model strong context about what this file is
        full_text = (
            f"Repository: {repo}\n"
            f"File: {file_path}\n"
            f"Language: {lang}\n\n"
            f"{content}"
        )
        docs.append(Document(
            page_content=full_text,
            metadata={
                "source":      item["url"],
                "source_type": "code",
                "title":       item.get("title", ""),
                "repo":        repo,
                "file_path":   file_path,
                "language":    lang,
            },
        ))
    return docs


def prepare_generic_documents(file_path, source_type_label: str) -> list[Document]:
    """Generic loader for Confluence / Jira JSON files."""
    from pathlib import Path as _Path
    fp = _Path(file_path)
    if not fp.exists():
        print(f"  {fp} not found — skipping {source_type_label} ingestion.")
        return []
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)
    docs = []
    for item in data:
        content = item.get("content", "").strip()
        if not content:
            continue
        full_text = f"{item.get('title', '')}\n\n{content}"
        docs.append(Document(
            page_content=full_text,
            metadata={
                "source":      item.get("url", ""),
                "source_type": source_type_label,
                "title":       item.get("title", ""),
            },
        ))
    return docs


if __name__ == "__main__":
    from crawler.state import content_hash, now_iso, save as save_state
    from config.settings import PIPELINE_VERSION

    print("Loading embedding model...")
    embeddings = build_embeddings()

    print(f"\n-- Ingesting DOCS -> '{DOCS_COLLECTION}' --")
    with open(DOCS_FILE, encoding="utf-8") as f:
        raw_docs = json.load(f)
    doc_documents = prepare_doc_documents()
    print(f"  Loaded {len(doc_documents)} doc pages")
    ingest(doc_documents, DOCS_COLLECTION, embeddings)

    print(f"\n-- Ingesting WEBSITE -> '{WEBSITE_COLLECTION}' --")

    website_documents = prepare_generic_documents(
        WEBSITE_FILE,
        "website",
    )

    if website_documents:
        print(f"  Loaded {len(website_documents)} website pages")
        ingest(
            website_documents,
            WEBSITE_COLLECTION,
            embeddings,
        )

    print(f"\n-- Ingesting COMMUNITY -> '{COMMUNITY_COLLECTION}' --")
    with open(COMMUNITY_FILE, encoding="utf-8") as f:
        raw_community = json.load(f)
    community_documents = prepare_community_documents()
    print(f"  Loaded {len(community_documents)} community posts")
    ingest(community_documents, COMMUNITY_COLLECTION, embeddings)

    print(f"\n-- Ingesting GITHUB -> '{GITHUB_COLLECTION}' --")
    github_documents = prepare_github_documents()
    if github_documents:
        print(f"  Loaded {len(github_documents)} GitHub issues")
        ingest(github_documents, GITHUB_COLLECTION, embeddings)

    print(f"\n-- Ingesting CODE -> '{CODE_COLLECTION}' --")
    code_documents = prepare_code_documents()
    if code_documents:
        print(f"  Loaded {len(code_documents)} source files")
        ingest(code_documents, CODE_COLLECTION, embeddings)

    print(f"\n-- Ingesting CONFLUENCE -> '{CONFLUENCE_COLLECTION}' --")
    confluence_documents = prepare_generic_documents(CONFLUENCE_FILE, "confluence")
    if confluence_documents:
        print(f"  Loaded {len(confluence_documents)} Confluence pages")
        ingest(confluence_documents, CONFLUENCE_COLLECTION, embeddings)

    print(f"\n-- Ingesting JIRA -> '{JIRA_COLLECTION}' --")
    jira_documents = prepare_generic_documents(JIRA_FILE, "jira")
    if jira_documents:
        print(f"  Loaded {len(jira_documents)} Jira tickets")
        ingest(jira_documents, JIRA_COLLECTION, embeddings)

    # ── Seed crawl state so run_update.py knows what's already ingested ────────
    print("\nSeeding crawl state for future incremental updates...")
    url_hashes = {
        doc["url"]: content_hash(doc.get("content", ""))
        for doc in raw_docs
        if doc.get("content", "").strip()
    }
    max_topic_id = 0
    for topic in raw_community:
        try:
            tid = int(topic["url"].rstrip("/").split("/")[-1])
            max_topic_id = max(max_topic_id, tid)
        except (ValueError, IndexError):
            pass

    github_state: dict = {}
    if GITHUB_FILE.exists():
        with open(GITHUB_FILE, encoding="utf-8") as f:
            raw_github = json.load(f)
        for item in raw_github:
            repo   = item.get("repo", "")
            number = item.get("number", 0)
            if repo:
                github_state[repo] = {"max_issue_number": max(
                    github_state.get(repo, {}).get("max_issue_number", 0), number
                ), "last_run": now_iso()}

    # Confluence: seed known page versions so run_update.py treats already-
    # ingested pages as unchanged instead of re-embedding them as "new".
    confluence_state: dict = {"page_versions": {}, "last_run": now_iso()}
    if CONFLUENCE_FILE.exists():
        with open(CONFLUENCE_FILE, encoding="utf-8") as f:
            raw_confluence = json.load(f)
        for item in raw_confluence:
            page_id = item.get("_page_id", "")
            if page_id:
                confluence_state["page_versions"][page_id] = item.get("_version", 0)

    # Jira: seed known issue keys per project (parsed from the "PROJECT-123"
    # key format) so run_update.py's incremental JQL query starts from "now".
    jira_state: dict = {}
    if JIRA_FILE.exists():
        with open(JIRA_FILE, encoding="utf-8") as f:
            raw_jira = json.load(f)
        for item in raw_jira:
            key = item.get("key", "")
            project = key.split("-")[0] if "-" in key else ""
            if project:
                project_state = jira_state.setdefault(project, {"seen_keys": [], "last_run": now_iso()})
                project_state["seen_keys"].append(key)

    website_url_hashes = {}

    if WEBSITE_FILE.exists():
        with open(WEBSITE_FILE, encoding="utf-8") as f:
            raw_website = json.load(f)

        website_url_hashes = {
            item["url"]: content_hash(item.get("content", ""))
            for item in raw_website
            if item.get("content", "").strip()
        }

    save_state({
        "pipeline_version": PIPELINE_VERSION,
        "docs":             {"url_hashes": url_hashes, "last_run": now_iso()},
        "website":          {"url_hashes": website_url_hashes,"last_run": now_iso()},
        "community":        {"max_topic_id": max_topic_id, "last_run": now_iso()},
        "github":           github_state,
        "confluence":       confluence_state,
        "jira":             jira_state,
    })
    print(f"  Tracked {len(url_hashes)} doc pages, "
          f"{len(website_url_hashes)} website pages."
          f"max community topic ID: {max_topic_id}, "
          f"{len(github_state)} GitHub repos, "
          f"{len(confluence_state['page_versions'])} Confluence pages, "
          f"{len(jira_state)} Jira projects")
    print(f"\nDone — all collections stored in pgvector ({PG_CONNECTION.split('@')[-1]})")
