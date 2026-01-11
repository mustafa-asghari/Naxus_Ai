"""
Nexus Helper Functions

Extracted from orchestrator.py for cleaner code organization.
Contains utility functions for text processing, URL detection, and validation.
"""
from __future__ import annotations

import re
from typing import Optional

from core.models import ActionStep
from core.intent import Intent
from macos.running_apps import get_running_apps


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

WRITE_CONFIDENCE_AUTO = 0.65
WRITE_CONFIDENCE_ASK = 0.60

_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    r"password\s*[:=]\s*\S+",
]

_TLD_PATTERN = r"(com|net|org|io|ai|app|dev|edu|gov|co|uk|au)"

# Common app aliases for fuzzy matching
APP_ALIASES = {
    "google chrome": ["chrome"],
    "visual studio code": ["vscode", "code", "vs code"],
    "messages": ["message"],
    "system settings": ["settings"],
    "notes": ["note"],
    "music": ["itunes"],
    "itunes": ["music"],
    "microsoft teams": ["teams"],
    "microsoft word": ["word"],
    "microsoft excel": ["excel"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def redact(text: str) -> str:
    """Redact sensitive patterns from text."""
    out = text
    for p in _SECRET_PATTERNS:
        out = re.sub(p, "[REDACTED]", out, flags=re.IGNORECASE)
    return out


def looks_like_phone_or_email(s: str) -> bool:
    """Check if string looks like a phone number or email."""
    ss = (s or "").strip()
    if not ss:
        return False
    return bool(
        re.fullmatch(r"[+\d][\d\s().-]{6,}", ss) or 
        re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", ss)
    )


def parse_contacts_tool_text(text: str) -> tuple[str, list[str]]:
    """Parse apple-mcp contacts tool response."""
    t = (text or "").strip()
    if not t:
        return "", []
    if ":" not in t:
        return t.strip(), []
    name, rest = t.split(":", 1)
    handles = [h.strip() for h in rest.split(",") if h.strip()]
    return name.strip(), handles


# ═══════════════════════════════════════════════════════════════════════════════
# URL DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_url(text: str) -> Optional[str]:
    """
    Best-effort URL detector for voice input.
    Handles patterns like "open google.com" or "go to youtube dot com".
    Returns a normalized URL with https:// prefix.
    """
    if not text:
        return None
    t = text.strip().lower()

    # Convert spoken "dot com" patterns
    for tld in ["com", "net", "org", "io", "ai", "app", "dev", "edu", "gov", "co", "uk", "au"]:
        t = re.sub(rf"\bdot\s+{tld}\b", f".{tld}", t)

    t = re.sub(r"\s*\.\s*", ".", t)
    t = re.sub(r"\s*/\s*", "/", t)

    m = re.search(
        rf"((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.{_TLD_PATTERN}(?:/[^\s]*)?)", 
        t
    )
    if not m:
        return None
    url = m.group(1).strip().rstrip(".,)")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


# ═══════════════════════════════════════════════════════════════════════════════
# APP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_close_targets(raw: str, running_apps: list[str]) -> list[str]:
    """
    Match mentioned app names against running apps list.
    Supports multiple apps and common aliases.
    """
    if not raw:
        return []
    t = raw.lower()
    
    # Check for close/quit keywords
    if not any(kw in t for kw in ["close", "quit", "exit"]):
        return []
    if any(phrase in t for phrase in ["close all", "quit everything", "close everything"]):
        return []

    matches: list[tuple[int, str]] = []
    
    for app in running_apps:
        al = app.lower()
        aliases = {al}
        
        # Add known aliases
        for key, alias_list in APP_ALIASES.items():
            if key in al:
                aliases.update(alias_list)
        
        # Also add the app name itself as potential alias
        aliases.add(al.split()[0] if " " in al else al)

        for a in aliases:
            if not a or len(a) < 3:
                continue
            idx = t.find(a)
            if idx != -1:
                matches.append((idx, app))
                break

    # Sort by position and dedupe
    matches.sort(key=lambda x: x[0])
    seen = set()
    return [app for _, app in matches if not (app in seen or seen.add(app))]


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIRMATION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

POSITIVE_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright",
    "do it", "confirm", "confirmed", "go ahead", "proceed", "affirmative",
    "absolutely", "definitely", "of course", "for sure", "go for it",
    "please", "please do", "yes please", "that's right", "correct",
    "right", "uh huh", "mm hmm", "yea", "ya", "yas", "yah",
    "confer", "conf", "confir", "confirme", "go for",
    "yep yep", "yes yes", "uh-huh", "mhm"
})

NEGATIVE_WORDS = frozenset({
    "no", "nah", "nope", "cancel", "stop", "don't", "abort",
    "never", "negative", "no way", "not now", "wait", "hold on",
    "actually no", "no thanks", "nope nope"
})

POSITIVE_PREFIXES = ("ye", "su", "ok", "al", "go", "conf", "uh", "mm")


def is_confirmation_positive(user_text: str) -> bool:
    """Determine if user's response means YES or NO."""
    if not user_text:
        return False

    clean = user_text.lower().strip()
    
    # Direct match
    if clean in POSITIVE_WORDS:
        return True
    if clean in NEGATIVE_WORDS:
        return False

    # Partial match (longer phrases first)
    for phrase in POSITIVE_WORDS:
        if len(phrase) > 2 and phrase in clean:
            return True

    for phrase in NEGATIVE_WORDS:
        if len(phrase) > 2 and phrase in clean:
            return False

    # Prefix match
    if clean.startswith(POSITIVE_PREFIXES):
        return True

    return False  # Default to no for safety


# ═══════════════════════════════════════════════════════════════════════════════
# ACTION EXPANSION
# ═══════════════════════════════════════════════════════════════════════════════

def expand_steps(actions: list[ActionStep]) -> list[ActionStep]:
    """Expand meta-actions like CLOSE_ALL_APPS into individual steps."""
    expanded: list[ActionStep] = []
    
    for step in actions:
        if step.intent == Intent.CLOSE_ALL_APPS:
            apps = get_running_apps()
            print(f"[CLOSE_ALL] Found {len(apps)} apps to close")
            for app in apps:
                expanded.append(ActionStep(Intent.CLOSE_APP, {"app_name": app}))
        else:
            expanded.append(step)
    
    return expanded
