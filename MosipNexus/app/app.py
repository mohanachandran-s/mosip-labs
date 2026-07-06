"""
MOSIP Nexus — Streamlit Chat UI.

Displays a conversational interface backed by the RAG query engine.
Session state holds:
  - memory:    SessionMemory (LangChain history + detected language)
  - messages:  display history (list of dicts for rendering)

Language detection runs on every user message; updates only when a non-English
language is detected with >= 95% confidence and at least 20 characters.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import base64
import datetime
import html as _html
import re

import streamlit as st
from langdetect import DetectorFactory, LangDetectException, detect_langs

from chain.query_engine import ask
from memory.session import SessionMemory
from retrieval.dedup import find_similar_question

DetectorFactory.seed = 0


def _safe_url(url: str) -> str:
    """Allow only http/https URLs; replace anything else with '#'."""
    return url if url.lower().startswith(("http://", "https://")) else "#"


def _md_to_html(text: str) -> str:
    """Convert markdown answer text to safe HTML for the export report."""
    import html, re
    t = html.escape(text)
    # Bold: **text** → <strong>
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    # Inline code: `code` → <code>
    t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
    # Numbered list items starting a line
    t = re.sub(r'(?m)^(\d+)\.\s+', r'<br><strong>\1.</strong> ', t)
    # Bullet list items starting a line (-, *, •)
    t = re.sub(r'(?m)^[-*•]\s+', r'<br>&nbsp;&nbsp;• ', t)
    # Remaining newlines → <br>
    t = t.replace('\n', '<br>')
    # Clean up leading <br> artifacts
    t = re.sub(r'^(<br>)+', '', t)
    return t


def _export_chat_html(messages: list, language: str) -> str:
    """Generate a self-contained HTML report of the current chat session."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    logo_html = ""
    if _logo_data:
        logo_html = (
            f'<img src="data:image/png;base64,{_logo_data}" '
            f'style="width:64px; display:block; margin:0 auto 10px auto;">'
        )
    rows = ""
    for msg in messages:
        if msg["role"] == "user":
            import html as _html
            rows += f"""
            <div class="turn">
              <div class="question">
                <span class="label">Question</span>
                <p>{_html.escape(msg["content"])}</p>
              </div>"""
        else:
            badge = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}.get(
                msg.get("confidence", ""), ""
            )
            sources_html = ""
            if msg.get("sources"):
                links = ""
                seen: set = set()
                icons = {"docs": "📄", "community": "💬", "github": "🐙",
                         "code": "🧑‍💻", "confluence": "📘"}
                for src in msg["sources"]:
                    url = src.get("source", "")
                    title = src.get("title") or url
                    icon = icons.get(src.get("source_type", ""), "📄")
                    if url and url not in seen:
                        seen.add(url)
                        links += f'<li>{icon} <a href="{_html.escape(_safe_url(url))}" target="_blank">{_html.escape(title)}</a></li>'
                if links:
                    sources_html = f'<div class="sources"><strong>Sources:</strong><ul>{links}</ul></div>'

            rows += f"""
              <div class="answer">
                <span class="label">Answer</span>
                {"<span class='badge'>" + badge + "</span>" if badge else ""}
                <div class="answer-body">{_md_to_html(msg["content"])}</div>
                {sources_html}
              </div>
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MOSIP Nexus — Test Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; color: #222; }}
    .meta {{ color: #666; font-size: 0.9em; margin-bottom: 30px; text-align: center; }}
    .turn {{ border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 20px; overflow: hidden; }}
    .question {{ background: #f0f4ff; padding: 16px; }}
    .answer {{ background: #fff; padding: 16px; }}
    .label {{ font-size: 0.75em; font-weight: bold; text-transform: uppercase;
              color: #666; display: block; margin-bottom: 6px; }}
    .badge {{ font-size: 0.8em; background: #f0fdf4; padding: 2px 8px;
              border-radius: 12px; margin-bottom: 10px; display: inline-block; }}
    .answer-body {{ line-height: 1.65; color: #1a1a1a; }}
    .answer-body code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 4px;
                         font-family: monospace; font-size: 0.9em; }}
    .sources {{ margin-top: 14px; font-size: 0.85em; background: #f8fafc;
                padding: 10px; border-radius: 6px; }}
    .sources ul {{ margin: 6px 0 0 0; padding-left: 18px; }}
    .sources li {{ margin-bottom: 4px; }}
    a {{ color: #1a56db; }}
  </style>
</head>
<body>
  <div style="text-align:center; margin-bottom:24px;">
    {logo_html}
    <h1 style="margin:0; color:#1a56db;">MOSIP Nexus — Test Report</h1>
  </div>
  <div class="meta">
    Generated: {now} &nbsp;|&nbsp;
    Language: {language} &nbsp;|&nbsp;
    Total turns: {sum(1 for m in messages if m["role"] == "user")}
  </div>
  {rows}
</body>
</html>"""

LANG_NAMES: dict[str, str] = {
    "en": "English", "ta": "Tamil", "hi": "Hindi", "fr": "French",
    "de": "German", "es": "Spanish", "ar": "Arabic", "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)", "ja": "Japanese", "ko": "Korean",
    "pt": "Portuguese", "ru": "Russian", "it": "Italian", "nl": "Dutch",
    "tr": "Turkish", "pl": "Polish", "uk": "Ukrainian", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "ml": "Malayalam",
    "te": "Telugu", "kn": "Kannada", "bn": "Bengali", "ur": "Urdu",
}

# ── Explicit language instruction detection ────────────────────────────────────
_LANG_INSTRUCTION_RE = re.compile(
    r'\b(?:reply|respond|answer|speak|write|communicate|talk)\s+in\s+([a-zA-Z]+)'
    r'|\buse\s+([a-zA-Z]+)\s*(?:language|lang)?\b'
    r'|\bswitch\s+to\s+([a-zA-Z]+)\b'
    r'|\btranslate\s+(?:to|in)\s+([a-zA-Z]+)\b',
    re.IGNORECASE,
)
_INSTRUCTION_LANG_MAP: dict[str, tuple[str, str]] = {
    "english": ("en", "English"), "tamil": ("ta", "Tamil"),
    "hindi": ("hi", "Hindi"), "french": ("fr", "French"),
    "german": ("de", "German"), "spanish": ("es", "Spanish"),
    "arabic": ("ar", "Arabic"), "chinese": ("zh-cn", "Chinese (Simplified)"),
    "japanese": ("ja", "Japanese"), "korean": ("ko", "Korean"),
    "portuguese": ("pt", "Portuguese"), "russian": ("ru", "Russian"),
    "italian": ("it", "Italian"), "dutch": ("nl", "Dutch"),
    "turkish": ("tr", "Turkish"), "polish": ("pl", "Polish"),
    "malayalam": ("ml", "Malayalam"), "telugu": ("te", "Telugu"),
    "kannada": ("kn", "Kannada"), "bengali": ("bn", "Bengali"),
    "urdu": ("ur", "Urdu"),
}


def _detect_lang_instruction(text: str) -> tuple[str, str] | None:
    """Return (lang_code, lang_name) if the query explicitly requests a language switch."""
    m = _LANG_INSTRUCTION_RE.search(text)
    if not m:
        return None
    word = next((g for g in m.groups() if g), "").lower()
    return _INSTRUCTION_LANG_MAP.get(word)


_CONFIDENCE_BADGE = {
    "high":   ("🟢", "High confidence"),
    "medium": ("🟡", "Medium confidence"),
    "low":    ("🔴", "Low confidence — answer may be incomplete"),
    "n/a":    ("⚪", ""),
}

_SOURCE_LABEL = {
    "mosip_docs":  "MOSIP Documentation",
    "community":   "Community Forum",
    "github":      "GitHub Issues",
    "code":        "Source Code",
    "confluence":  "Confluence",
    "jira":        "Jira Tickets",
    "website": "MOSIP Website",
    "mixed":       "Docs · Community · GitHub · Code",
    "web":         "Web Sources",
    "none":        "",
    "chat":        "",
}

def _svg(path_d: str, *, filled: bool = False) -> str:
    fill = "#4b5563" if filled else "none"
    stroke = "" if filled else 'stroke="#4b5563" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" '
        f'fill="{fill}" {stroke} style="vertical-align:-2px;margin-right:5px">'
        f'{path_d}</svg>'
    )

_SVG_ICON = {
    "docs": _svg(
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'
    ),
    "community": _svg(
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
    ),
    "github": _svg(
        '<path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387'
        '.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416'
        '-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729'
        ' 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997'
        '.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931'
        ' 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0'
        ' 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404'
        ' 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23'
        '.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221'
        ' 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293'
        'c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386'
        ' 0-6.627-5.373-12-12-12z"/>',
        filled=True,
    ),
    "code": _svg(
        '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>'
    ),
    "confluence": _svg(
        '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
        '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
    ),
    "jira": _svg(
        '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2'
        'a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/>'
    ),
    "web": _svg(
        '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
        '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10'
        ' 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
    ),

    "website": _svg(
    '<circle cx="12" cy="12" r="10"/>'
    '<line x1="2" y1="12" x2="22" y2="12"/>'
    '<path d="M12 2a15.3 15.3 0 0 1 4 10 '
    '15.3 15.3 0 0 1-4 10 '
    '15.3 15.3 0 0 1-4-10 '
    '15.3 15.3 0 0 1 4-10z"/>'
    ),
}

_SIDEBAR_SOURCES_HTML = (
    '<div style="font-size:0.88em; line-height:2.1; color:#374151">'
    + "".join(
        f'<div>{_SVG_ICON[k]} {label}</div>'
        for k, label in [
            ("docs",        "MOSIP Documentation"),
            ("website", "MOSIP Website"),
            ("community",   "Community Forum"),
            ("github",      "GitHub Issues"),
            ("confluence",  "Confluence"),
            ("code",        "Source Code"),
        ]
    )
    + "</div>"
)

# ── Page config ────────────────────────────────────────────────────────────────
_LOGO = Path(__file__).parent / "assets" / "logo.png"
try:
    from PIL import Image as _PILImage
    _page_icon = _PILImage.open(_LOGO) if _LOGO.exists() else "🔷"
except Exception:
    _page_icon = "🔷"

st.set_page_config(
    page_title="MOSIP Nexus",
    page_icon=_page_icon,
    layout="wide",
)

# ── Session state ──────────────────────────────────────────────────────────────
if "memory" not in st.session_state:
    st.session_state.memory = SessionMemory()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "lang_locked" not in st.session_state:
    st.session_state.lang_locked = False
if "pending_lang" not in st.session_state:
    st.session_state.pending_lang = None   # (lang_code, lang_name) awaiting user confirmation

memory: SessionMemory = st.session_state.memory


def _detect_language(text: str, current_lang_code: str = "en") -> tuple[str, str] | None:
    """Detect language and return (lang_code, lang_name) if it should change.

    Sticky logic:
    - Non-English detected with high confidence → switch to that language
    - English detected on a long message (40+ chars) while session is non-English
      → switch back to English (user explicitly switched back)
    - Short messages and ambiguous detections → keep current language
    """
    from config.settings import LANG_DETECT_CONFIDENCE, MIN_LANG_DETECT_CHARS
    # Non-ASCII text (Hindi, Tamil, Arabic, etc.) is unambiguous — use a lower threshold
    has_non_ascii = any(ord(c) > 127 for c in text)
    min_chars = 5 if has_non_ascii else MIN_LANG_DETECT_CHARS
    if len(text.strip()) < min_chars:
        return None
    try:
        langs = detect_langs(text)
        top = langs[0]
        if top.prob < LANG_DETECT_CONFIDENCE:
            return None
        if top.lang != "en" and top.lang in LANG_NAMES:
            return top.lang, LANG_NAMES[top.lang]
        # Switch back to English only when user writes a long English message explicitly
        if top.lang == "en" and current_lang_code != "en" and len(text.strip()) >= 40:
            return "en", "English"
    except LangDetectException:
        pass
    return None


# ── Read input + detect language BEFORE sidebar so sidebar shows current language
query = st.chat_input("Ask anything about MOSIP...")
if query:
    # Explicit instruction ("reply in Tamil") takes priority over auto-detect
    lang_instruction = _detect_lang_instruction(query)
    if lang_instruction and lang_instruction[0] != memory.lang_code:
        st.session_state.pending_lang = lang_instruction
    elif not st.session_state.lang_locked:
        lang_result = _detect_language(query, memory.lang_code)
        if lang_result:
            memory.set_language(*lang_result)

# ── Sidebar ────────────────────────────────────────────────────────────────────
# Collapse default Streamlit sidebar top padding
st.markdown("""
<style>
[data-testid="stSidebarContent"] { padding-top: 1rem !important; }
/* Section spacing */
[data-testid="stSidebarContent"] hr { margin: 1.4rem 0 !important; border-color: #e5e7eb !important; }
[data-testid="stSidebarContent"] .stMarkdown p { margin-bottom: 0.3rem !important; }
/* Kill auto-link blue on tagline */
[data-testid="stSidebarContent"] .stMarkdown a { color: #6b7280 !important; text-decoration: none !important; pointer-events: none; }
/* Hide sidebar scrollbar */
section[data-testid="stSidebar"]::-webkit-scrollbar { display: none; }
section[data-testid="stSidebar"] { scrollbar-width: none; }
/* Main title logo — hidden when sidebar is open, visible when collapsed */
.mosip-title-logo { display: none !important; }
:root:has(section[data-testid="stSidebar"][aria-expanded="false"]) .mosip-title-logo { display: block !important; }
/* Center title area when sidebar is collapsed */
:root:has(section[data-testid="stSidebar"][aria-expanded="false"]) .mosip-title-block { text-align: center !important; }
:root:has(section[data-testid="stSidebar"][aria-expanded="false"]) .mosip-subtitle { text-align: center !important; }
[data-testid="stChatInput"] {
    border: 1.5px solid #d1d5db !important;
    border-radius: 12px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.07) !important;
    transition: box-shadow 0.2s ease, border-color 0.2s ease !important;
}
[data-testid="stChatInput"]:hover {
    border-color: #9ca3af !important;
    box-shadow: 0 3px 12px rgba(0,0,0,0.11) !important;
}
[data-testid="stChatInput"]:focus-within {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 3px rgba(99,102,241,0.15), 0 2px 8px rgba(0,0,0,0.07) !important;
}
.mosip-footer {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    text-align: center;
    padding: 7px 0;
    font-size: 0.75em;
    color: #9ca3af;
    background: rgba(255,255,255,0.92);
    backdrop-filter: blur(4px);
    border-top: 1px solid #f0f0f0;
    z-index: 999;
    transition: left 0.3s ease;
}
:root:has(section[data-testid="stSidebar"][aria-expanded="true"]) .mosip-footer {
    left: 19rem;
}
</style>
<div class="mosip-footer">
  Powered by LangChain &nbsp;·&nbsp; Groq Llama 3.3 &nbsp;·&nbsp; pgvector &nbsp;·&nbsp; HuggingFace
</div>
""", unsafe_allow_html=True)

_logo_data: str | None = base64.b64encode(_LOGO.read_bytes()).decode() if _LOGO.exists() else None

with st.sidebar:
    if _logo_data:
        st.markdown(
            f'<div style="text-align:center; padding:4px 0 8px 0;">'
            f'<img src="data:image/png;base64,{_logo_data}" width="120" style="display:block; margin:0 auto 10px auto;">'
            f'<div style="font-size:1.15em; font-weight:700; margin-bottom:4px;">MOSIP Nexus</div>'
            f'<div style="font-size:0.78em; color:#6b7280; letter-spacing:0.03em; line-height:1.4;">'
            f'AI-powered MOSIP Knowledge Assistant</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🔷 MOSIP Nexus")
        st.caption("AI-powered MOSIP Knowledge Assistant")
    st.divider()

    if st.button("🔄 New Chat", use_container_width=True):
        memory.clear()
        st.session_state.messages = []
        st.rerun()

    if st.session_state.messages:
        filename = f"mosip_nexus_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.html"
        st.download_button(
            label="📥 Export Chat",
            data=_export_chat_html(st.session_state.messages, memory.language).encode("utf-8"),
            file_name=filename,
            mime="text/html; charset=utf-8",
            use_container_width=True,
        )

    st.divider()
    st.markdown("**Session info**")
    _lock_icon = "🔒" if st.session_state.lang_locked else "🌐"
    st.markdown(f"Language: **{memory.language}** {_lock_icon}")
    st.markdown(f"Turns: **{len(memory.messages) // 2}**")

    if not st.session_state.lang_locked:
        # Lock to current non-English language
        if memory.lang_code != "en":
            if st.button(f"🔒 Lock to {memory.language}", use_container_width=True):
                st.session_state.lang_locked = True
                st.session_state.pending_lang = None
                st.rerun()
        # Switch to English (sets + locks)
        if st.button("🔒 Switch to English", use_container_width=True):
            memory.set_language("en", "English")
            st.session_state.lang_locked = True
            st.session_state.pending_lang = None
            st.rerun()
    else:
        # Locked — offer unlock and optionally switch to English
        if st.button("🌐 Switch to Default", use_container_width=True):
            st.session_state.lang_locked = False
            st.rerun()
        if memory.lang_code != "en":
            if st.button("🔒 Switch to English", use_container_width=True):
                memory.set_language("en", "English")
                st.session_state.lang_locked = True
                st.session_state.pending_lang = None
                st.rerun()

    st.divider()
    st.markdown("**Sources searched**")
    st.markdown(_SIDEBAR_SOURCES_HTML, unsafe_allow_html=True)

# ── Main title ─────────────────────────────────────────────────────────────────
if _logo_data:
    st.markdown(
        f'<div class="mosip-title-block" style="margin-bottom:4px;">'
        f'<img class="mosip-title-logo" src="data:image/png;base64,{_logo_data}" width="56" style="display:block; margin:0 auto 10px auto;">'
        f'<h1 style="margin:0; padding:0; font-size:2.25rem; font-weight:700; line-height:1.2;">MOSIP Nexus</h1>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    st.title("MOSIP Nexus")
st.markdown(
    '<p class="mosip-subtitle" style="color:#6b7280; font-size:0.875rem; margin-top:2px;">'
    'Ask anything about MOSIP — answers from docs, community forum, GitHub issues, Confluence, and source code'
    '</p>',
    unsafe_allow_html=True,
)

# ── Scroll-to-bottom button + auto-scroll ─────────────────────────────────────
import streamlit.components.v1 as _components
_components.html("""
<script>
(function() {
    const doc  = window.parent.document;
    const scrl = doc.querySelector('[data-testid="stAppViewContainer"]');
    if (!scrl) return;

    // ── Auto-scroll: wait for Streamlit to finish rendering, then scroll ───
    setTimeout(() => {
        scrl.scrollTo({ top: scrl.scrollHeight, behavior: 'smooth' });
    }, 120);

    // ── Floating "Latest message" button ───────────────────────────────────
    let btn = doc.getElementById('_mosip_goto_bottom');
    if (!btn) {
        btn = doc.createElement('button');
        btn.id = '_mosip_goto_bottom';
        btn.innerHTML = '&#8595;&nbsp;Latest message';
        Object.assign(btn.style, {
            position:     'fixed',
            bottom:       '72px',
            right:        '24px',
            zIndex:       '9999',
            display:      'none',
            alignItems:   'center',
            gap:          '6px',
            background:   '#6366f1',
            color:        '#fff',
            border:       'none',
            borderRadius: '50px',
            padding:      '9px 18px',
            fontSize:     '0.82em',
            fontWeight:   '600',
            cursor:       'pointer',
            boxShadow:    '0 3px 14px rgba(99,102,241,0.40)',
            transition:   'opacity 0.2s ease, transform 0.15s ease',
        });
        doc.body.appendChild(btn);
        btn.addEventListener('mouseenter', () => { btn.style.transform = 'translateY(-2px)'; });
        btn.addEventListener('mouseleave', () => { btn.style.transform = 'translateY(0)'; });
        btn.addEventListener('click', () => {
            scrl.scrollTo({ top: scrl.scrollHeight, behavior: 'smooth' });
        });
    }

    // Re-attach scroll listener on every Streamlit rerun (removes stale one first)
    if (scrl._mosipScrollFn) scrl.removeEventListener('scroll', scrl._mosipScrollFn);
    scrl._mosipScrollFn = () => {
        const fromBottom = scrl.scrollHeight - scrl.scrollTop - scrl.clientHeight;
        btn.style.display = fromBottom > 300 ? 'flex' : 'none';
    };
    scrl.addEventListener('scroll', scrl._mosipScrollFn);
})();
</script>
""", height=0)

# ── Render existing chat ───────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        if msg.get("confidence") and msg["confidence"] not in ("n/a", "low"):
            badge, label = _CONFIDENCE_BADGE.get(msg["confidence"], ("", ""))
            if label:
                st.caption(f"{badge} {label}")

        if msg.get("similar_questions"):
            with st.expander("🔍 Related community threads", expanded=False):
                for q in msg["similar_questions"]:
                    st.markdown(f"- {q}")

        if msg.get("sources"):
            label = _SOURCE_LABEL.get(msg.get("source_type", ""), "📄 Sources")
            with st.expander(label, expanded=False):
                seen: set[str] = set()
                for src in msg["sources"]:
                    url   = src.get("source", "")
                    title = src.get("title") or url
                    stype = src.get("source_type", "")
                    icon  = _SVG_ICON.get(stype, _SVG_ICON["docs"])
                    if url and url not in seen:
                        seen.add(url)
                        tags = src.get("tags", "")
                        tag_str = f" `{tags}`" if tags else ""
                        st.markdown(f"{icon}<a href='{_html.escape(_safe_url(url))}' target='_blank'>{_html.escape(title)}</a>{tag_str}", unsafe_allow_html=True)

# ── Language-lock confirmation popup ──────────────────────────────────────────
if st.session_state.pending_lang:
    _plc, _pln = st.session_state.pending_lang
    st.info(f"🌐 Stay in **{_pln}** for the rest of this conversation?")
    _pcol1, _pcol2 = st.columns(2)
    with _pcol1:
        if st.button(f"✅ Yes, lock to {_pln}", use_container_width=True, key="_lang_yes"):
            memory.set_language(_plc, _pln)
            st.session_state.lang_locked = True
            st.session_state.pending_lang = None
            st.rerun()
    with _pcol2:
        if st.button("❌ No, just this reply", use_container_width=True, key="_lang_no"):
            st.session_state.pending_lang = None
            st.rerun()

if query:
    # ── Render user message ────────────────────────────────────────────────────
    with st.chat_message("user"):
        st.markdown(query)
    st.session_state.messages.append({"role": "user", "content": query})

    # ── Duplicate detection ────────────────────────────────────────────────────
    similar = None
    try:
        similar = find_similar_question(query)
    except Exception:
        pass

    if similar:
        with st.chat_message("assistant"):
            st.info(
                f"**Similar thread found** (similarity: {similar['similarity_score']:.0%})\n\n"
                f"**[{similar['title']}]({similar['source']})**\n\n"
                f"This community thread may already answer your question. "
                f"Generating a summarised answer below..."
            )

    # ── Generate answer ────────────────────────────────────────────────────────
    with st.chat_message("assistant"):
        with st.spinner("Searching docs and community forum..."):
            result = ask(query, memory.messages, memory.language)

        answer       = result["answer"]
        sources      = result["sources"]
        source_type  = result["source_type"]
        confidence   = result["confidence"]
        similar_qs   = result["similar_questions"]

        st.markdown(answer)

        # ── Disclaimer 1: web-sourced answer ───────────────────────────────────
        if source_type == "web":
            st.warning(
                "⚠️ **Disclaimer:** This answer is from external web sources, "
                "not from official MOSIP documentation or community. "
                "Verify with MOSIP experts before using in production."
            )

        # ── Disclaimer 2: low-confidence MOSIP answer (auto + auto-notify) ────
        _auto_notify_key = f"_auto_notified_{hash(query)}"
        if confidence == "low" and source_type not in ("none", "chat", "web", "n/a"):
            st.warning(
                "⚠️ **Disclaimer:** The AI confidence for this answer is low — "
                "the MOSIP knowledge base may not fully cover this topic. "
                "A MOSIP expert can provide a more accurate and complete response. "
                "The MOSIP team has been notified automatically."
            )
            # Auto-send once per unique question (session-scoped to prevent duplicate sends)
            if not st.session_state.get(_auto_notify_key):
                from notifications.email_notifier import send_low_confidence_notification
                send_low_confidence_notification(
                    question=query,
                    language=memory.language,
                    source_type=source_type,
                )
                st.session_state[_auto_notify_key] = True

        # Confidence badge
        badge, conf_label = _CONFIDENCE_BADGE.get(confidence, ("", ""))
        if conf_label:
            st.caption(f"{badge} {conf_label}")

        # Similar questions
        if similar_qs:
            with st.expander("🔍 Related community threads", expanded=True):
                for q in similar_qs:
                    st.markdown(f"- {q}")

        # Sources
        if sources:
            src_label = _SOURCE_LABEL.get(source_type, "📄 Sources")
            with st.expander(src_label, expanded=True):
                seen_now: set[str] = set()
                for src in sources:
                    url   = src.get("source", "")
                    title = src.get("title") or url
                    stype = src.get("source_type", "")
                    icon  = _SVG_ICON.get(stype, _SVG_ICON["docs"])
                    if url and url not in seen_now:
                        seen_now.add(url)
                        tags = src.get("tags", "")
                        tag_str = f" `{tags}`" if tags else ""
                        accepted = " ✅" if src.get("accepted") else ""
                        st.markdown(f"{icon}<a href='{url}' target='_blank'>{title}</a>{tag_str}{accepted}", unsafe_allow_html=True)

        # ── "Ask MOSIP Expert" button (web + low-confidence + none) ───────────
        _show_expert = source_type in ("web", "none") or confidence == "low"
        if _show_expert and source_type not in ("chat",):
            st.divider()
            if source_type == "none":
                st.warning(
                    "This question is not covered in MOSIP documentation, "
                    "community forum, or web search."
                )
            community_url = (
                f"https://community.mosip.io/new-topic"
                f"?title={query[:100].replace(' ', '+')}"
            )
            col1, col2 = st.columns(2)
            with col1:
                st.link_button(
                    "💬 Post to MOSIP Community",
                    community_url,
                    use_container_width=True,
                )
            with col2:
                _btn_key = f"expert_{len(st.session_state.messages)}"
                if st.button("🙋 Ask MOSIP Expert", use_container_width=True, key=_btn_key):
                    st.session_state["_pending_expert"] = {
                        "query": query, "context": answer[:300],
                    }

    # ── Expert request form (rendered outside chat_message) ───────────────────
    _pending = st.session_state.get("_pending_expert", {})
    if _pending.get("query") == query:
        with st.form("expert_form"):
            st.markdown("**Request a MOSIP Expert Response**")
            st.caption(
                "A MOSIP team member will review your question and reply directly. "
                "Leave your email so they can reach you."
            )
            user_email = st.text_input(
                "Your email (required for expert to reply)",
                placeholder="you@example.com",
            )
            submitted = st.form_submit_button("Send Request")
            if submitted:
                if not user_email.strip():
                    st.error("Please enter your email so the expert can reply.")
                else:
                    from notifications.email_notifier import (
                        send_expert_request_notification,
                        send_unanswered_notification,
                    )
                    if source_type == "none":
                        ok, msg = send_unanswered_notification(
                            question=query,
                            language=memory.language,
                            user_email=user_email.strip(),
                        )
                    else:
                        ok, msg = send_expert_request_notification(
                            question=query,
                            language=memory.language,
                            user_email=user_email.strip(),
                            context=_pending.get("context", ""),
                        )
                    if ok:
                        st.success(f"✅ {msg} A MOSIP expert will be in touch.")
                    else:
                        st.error(f"❌ {msg}")
                    del st.session_state["_pending_expert"]

    # ── Update session state ───────────────────────────────────────────────────
    st.session_state.messages.append({
        "role":             "assistant",
        "content":          answer,
        "sources":          sources,
        "source_type":      source_type,
        "confidence":       confidence,
        "similar_questions": similar_qs,
    })
    memory.add_turn(query, answer)
    # Rerun so the sidebar export button picks up the just-appended messages.
    st.rerun()
