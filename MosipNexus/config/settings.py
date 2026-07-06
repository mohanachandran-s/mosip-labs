"""
Central configuration for MOSIP Nexus.
All tuneable constants and environment bindings live here.
Import this module everywhere instead of reading os.getenv() directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR      = Path(__file__).parent.parent
DATA_DIR      = ROOT_DIR / "data"
DOCS_FILE     = DATA_DIR / "mosip_docs.json"
COMMUNITY_FILE = DATA_DIR / "mosip_community.json"

# ── pgvector connection ────────────────────────────────────────────────────────
# Format: postgresql+psycopg://user:password@host:port/dbname
# On Rancher: set PG_CONNECTION in the nexus-env Secret.
PG_CONNECTION = os.getenv(
    "PG_CONNECTION",
    "postgresql+psycopg://mosip:mosip@localhost:5432/mosipnexus",
)

DATA_DIR.mkdir(exist_ok=True)

# ── Crawl targets ──────────────────────────────────────────────────────────────
DOCS_BASE_URL    = "https://docs.mosip.io/1.2.0"
DOCS_SITEMAP_URL = f"{DOCS_BASE_URL}/sitemap.xml"
COMMUNITY_BASE_URL = "https://community.mosip.io"

# ──MOSIP Website ──────────────────────────────────────────────────────────────
WEBSITE_BASE_URL = "https://www.mosip.io"
WEBSITE_FILE = DATA_DIR / "mosip_website.json"
WEBSITE_COLLECTION = "mosip_website"

# ── Embeddings ─────────────────────────────────────────────────────────────────
EMBED_MODEL = "intfloat/multilingual-e5-base"   # 768-dim, 100+ languages

# ── pgvector collection names ──────────────────────────────────────────────────
DOCS_COLLECTION      = "mosip_docs"
COMMUNITY_COLLECTION = "mosip_community"

# ── Chunking ───────────────────────────────────────────────────────────────────
CHUNK_SIZE    = 900
CHUNK_OVERLAP = 150

# ── Pipeline version ───────────────────────────────────────────────────────────
# A hash of parameters that, if changed, require a full re-embed from scratch.
# run_update.py compares this against the value saved in crawl_state.json and
# triggers a full rebuild automatically when they differ.
# You never need to edit this manually — it is computed at import time.
import hashlib as _hashlib
PIPELINE_VERSION = _hashlib.md5(
    f"{EMBED_MODEL}|{CHUNK_SIZE}|{CHUNK_OVERLAP}".encode()
).hexdigest()[:8]

# ── Retrieval ──────────────────────────────────────────────────────────────────
RETRIEVAL_K       = 8    # final chunks returned per collection
RETRIEVAL_FETCH_K = 30   # MMR candidate pool

# Cosine similarity (1 = identical).  Above this → treat as duplicate question.
DEDUP_THRESHOLD = 0.88

# Similarity thresholds for confidence scoring (higher = better, range [0, 1]).
# Uses LangChain relevance scores so values are metric-agnostic (L2 or cosine).
# similarity >= 0.75 → high confidence
# similarity >= 0.55 → medium confidence
# else              → low confidence (answer may be unreliable)
CONFIDENCE_HIGH   = 0.75
CONFIDENCE_MEDIUM = 0.55

# ── LLM ───────────────────────────────────────────────────────────────────────
GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ── Email notifications (optional) ────────────────────────────────────────────
# Used to alert the MOSIP team when a question cannot be answered.
# Gmail: use an App Password (not your account password).
#   Generate at: Google Account → Security → App passwords
# Port 465 = SMTP_SSL  |  Port 587 = STARTTLS (most providers)
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAIL  = os.getenv("NOTIFY_EMAIL", "")   # recipient: MOSIP team address

# ── LangSmith (optional observability) ────────────────────────────────────────
# LangChain reads these env vars automatically when set — no code changes needed.
LANGSMITH_TRACING = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "mosip-nexus")

# ── Language detection ─────────────────────────────────────────────────────────
MIN_LANG_DETECT_CHARS    = 20
LANG_DETECT_CONFIDENCE   = 0.95

# ── Community crawler ──────────────────────────────────────────────────────────
COMMUNITY_MAX_PAGES  = 50    # pages of /latest.json  (100 topics each)
COMMUNITY_MIN_POSTS  = 1     # skip topics with no replies
CRAWL_DELAY_SECS     = 0.4   # polite delay between requests

# ── GitHub crawler (optional — add GITHUB_TOKEN to .env for 5k req/hr) ────────
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
GITHUB_FILE     = DATA_DIR / "mosip_github.json"
GITHUB_COLLECTION = "mosip_github"
GITHUB_RETRIEVAL_K = 6          # chunks returned from GitHub collection
GITHUB_MAX_ISSUES   = 500       # max closed issues to fetch per repo on first run
GITHUB_ORG          = "mosip"   # GitHub organisation to auto-discover repos from

# Repos to always skip regardless of filter rules (noise / already covered elsewhere)
GITHUB_REPO_EXCLUDE = {
    "documentation",            # already crawled by docs_crawler
    "mosip-acceptance-tests",   # test harness — not useful for support answers
    "mosip-functional-tests",   # test harness
    "performance-test-scripts", # benchmarks only
    "mosip-ref-impl",           # reference implementation demo — misleading for prod support
    "mosip-data",               # seed data files
    ".github",                  # org-level GitHub config
}

# Languages we care about. Repos in other languages (HTML, CSS, Jupyter) are skipped.
GITHUB_USEFUL_LANGUAGES = {"Java", "Python", "Shell", "TypeScript", "JavaScript", "Kotlin", "Scala"}

# Fallback list — used when GITHUB_TOKEN is absent (can't call org API without auth)
GITHUB_REPOS = [
    "mosip/id-authentication",
    "mosip/registration",
    "mosip/registration-client",
    "mosip/commons",
    "mosip/partner-management-services",
    "mosip/resident-services",
    "mosip/keymanager",
    "mosip/admin-ui",
    "mosip/mosip-config",
    "mosip/mosip-infra",
    "mosip/mosip-helm",
    "mosip/bio-utils",
    "mosip/admin-services",
    "mosip/print",
    "mosip/mosip-openid-bridge",
]

# ── Code crawler — MOSIP source code for deep technical Q&A ───────────────────
CODE_FILE       = DATA_DIR / "mosip_code.json"
CODE_COLLECTION = "mosip_code"
CODE_RETRIEVAL_K = 6            # chunks returned from code collection per query

# File extensions to crawl from each repo (ordered by value for debugging)
CODE_INCLUDE_EXTENSIONS = {".java", ".yaml", ".yml", ".properties", ".sql", ".xml"}

# Directory/file patterns to skip — test code and generated files add noise
CODE_EXCLUDE_PATTERNS = {
    "test", "Test", "IT.java", "generated", "target/", "node_modules/",
    "__pycache__", ".mvn", "vendor",
}

# Filenames that are highest priority (error constants, service interfaces)
# These are fetched first and always included regardless of size limits
CODE_PRIORITY_PATTERNS = {
    "ErrorConstants", "ExceptionConstants", "StatusCode", "ErrorCode",
    "ServiceImpl", "Controller", "Handler", "Exception",
}

# Max file size in bytes — skip huge generated/minified files
CODE_MAX_FILE_BYTES = 80_000   # ~2,000 lines of Java

# ── Confluence crawler (optional — fill credentials in .env) ──────────────────
CONFLUENCE_URL        = os.getenv("CONFLUENCE_URL", "")        # https://org.atlassian.net/wiki
CONFLUENCE_USER       = os.getenv("CONFLUENCE_USER", "")       # email used to log in
CONFLUENCE_TOKEN      = os.getenv("CONFLUENCE_TOKEN", "")      # Atlassian API token
CONFLUENCE_SPACE_KEYS = [k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "MOSIP").split(",") if k.strip()]
CONFLUENCE_FILE       = DATA_DIR / "mosip_confluence.json"
CONFLUENCE_COLLECTION = "mosip_confluence"

# ── Jira crawler (optional — fill credentials in .env) ────────────────────────
JIRA_URL          = os.getenv("JIRA_URL", "")                 # https://org.atlassian.net
JIRA_USER         = os.getenv("JIRA_USER", "")
JIRA_TOKEN        = os.getenv("JIRA_TOKEN", "")
JIRA_PROJECT_KEYS = [k.strip() for k in os.getenv("JIRA_PROJECT_KEYS", "MOSIP").split(",") if k.strip()]
JIRA_FILE         = DATA_DIR / "mosip_jira.json"
JIRA_COLLECTION   = "mosip_jira"

# ── HTTP headers ───────────────────────────────────────────────────────────────
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MosipNexusBot/1.0)"}
