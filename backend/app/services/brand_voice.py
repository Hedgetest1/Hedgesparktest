"""
brand_voice.py — HedgeSpark brand voice system.

Extracted from the actual emails that define the brand:
  - beta_welcome (the foundational email — longest, most complete voice)
  - followup_opened / followup_clicked / followup_noopen
  - welcome (short form)

This is NOT invented. Every rule was reverse-engineered from real text
that was written, reviewed, and approved. This file is the single source
of truth for how HedgeSpark communicates with merchants.

Public interface:
    validate_email_text(text: str) -> BrandCheckResult
    get_brand_rules() -> dict

Usage:
    Called by email_orchestrator before flush to catch drift.
    Called by email_templates to guide template writing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — EXTRACTED PATTERNS (deep analysis of real emails)
# ═══════════════════════════════════════════════════════════════════════════
#
# TONE CHARACTERISTICS (how it speaks):
#   - First person singular from Andrea, not "The HedgeSpark Team"
#   - Direct, declarative sentences. No hedging ("we think", "perhaps").
#   - Confident but not boastful. States what the system does, not how great it is.
#   - Warm without being casual. No emojis, no exclamation marks in body text.
#   - Technical specificity as proof of credibility ("behavioral scoring",
#     "real-time event processing", "SHA256 cache key").
#   - Acknowledges reality: "We're an early-stage company. Trust is something
#     we earn." Never pretends to be bigger than it is.
#
# SENTENCE STRUCTURE:
#   - Short declarative opener. Then one longer sentence with detail.
#     Then a short closer. Rhythm: short–long–short.
#   - Em dashes for asides, not parentheses.
#   - "This is X — not Y" construction for clarity.
#   - Paragraphs are 1-3 sentences max. Never walls of text.
#
# EMOTIONAL PATTERN:
#   - Opens with concrete fact (what happened, what exists)
#   - Builds understanding (how it works, what it means)
#   - Resolves with clear action (one CTA, never two)
#   - Never creates anxiety. Never implies merchant is failing.
#   - Tension is about opportunity, not about threat.
#
# CTA PSYCHOLOGY:
#   - Single CTA per email. Never "click here AND also do this."
#   - CTA is an invitation, not a command: "Open your dashboard" not "ACT NOW"
#   - CTA comes after understanding is built, never at the top.
#   - Fallback action is always "reply to this email" — human backstop.
#
# FORMATTING:
#   - Section titles in UPPERCASE (plain text) or styled heading (HTML)
#   - Bullet points for parallel items (3-4 max per list)
#   - Numbered steps only for sequential processes
#   - Visual breathing: separator lines between major sections
#   - Signature: "Talk soon," or "Looking forward to building this together,"
#
# WHAT IS NEVER DONE:
#   - No exclamation marks in body copy (subject lines may have one)
#   - No "Hey!" or "Hey there!" — always "Hi," or "Hi {name},"
#   - No emoji
#   - No "just checking in" or "following up"
#   - No "limited time" or artificial urgency
#   - No superlatives ("best", "amazing", "incredible", "game-changing")
#   - No rhetorical questions ("Tired of losing sales?")
#   - No third-person team reference ("The team at HedgeSpark")
#   - No "Click here" as link text
#   - No fake familiarity ("As a valued customer")
#   - No discount/promotional language
#   - Never blames the merchant for inaction


# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — BRAND DNA (strict rules as code)
# ═══════════════════════════════════════════════════════════════════════════

# --- FORBIDDEN PATTERNS ---
# Regex patterns that must NEVER appear in any HedgeSpark email.
# Each tuple: (pattern, reason)

FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # Hype language
    (r"\bamazing\b", "superlative_hype"),
    (r"\bincredible\b", "superlative_hype"),
    (r"\bgame[- ]chang", "superlative_hype"),
    (r"\bground[- ]?breaking\b", "superlative_hype"),
    (r"\brevolution", "superlative_hype"),
    (r"\bunbelievable\b", "superlative_hype"),
    (r"\bmassive\b", "superlative_hype"),
    (r"\bhuge\b", "superlative_hype"),
    (r"\binsane\b", "superlative_hype"),

    # Urgency / scarcity manipulation
    (r"\blimited time\b", "artificial_urgency"),
    (r"\bact now\b", "artificial_urgency"),
    (r"\bdon'?t miss\b", "artificial_urgency"),
    (r"\bhurry\b", "artificial_urgency"),
    (r"\blast chance\b", "artificial_urgency"),
    (r"\bbefore it'?s too late\b", "artificial_urgency"),
    (r"\bexpires? soon\b", "artificial_urgency"),
    (r"\bonly \d+ (?:spots?|seats?|slots?) (?:left|remaining)\b", "artificial_scarcity"),

    # Marketing clichés
    (r"\bjust checking in\b", "cliche_followup"),
    (r"\btouching base\b", "cliche_followup"),
    (r"\bcircling back\b", "cliche_followup"),
    (r"\bfollowing up\b", "cliche_followup"),
    (r"\bvalued customer\b", "fake_familiarity"),
    (r"\bdear valued\b", "fake_familiarity"),
    (r"\bclick here\b", "generic_cta"),
    (r"\bthe team at\b", "third_person_team"),

    # Rhetorical questions as hooks
    (r"\btired of\b", "rhetorical_hook"),
    (r"\bstruggling with\b", "rhetorical_hook"),
    (r"\bwhat if (?:you|I told)\b", "rhetorical_hook"),
    (r"\bwant to (?:boost|increase|skyrocket|explode)\b", "rhetorical_hook"),

    # Blame language
    (r"\byou(?:'re| are) (?:missing out|falling behind|losing)\b", "blame_language"),
    (r"\byour competitors\b", "competitive_fear"),

    # Excessive punctuation
    (r"!{2,}", "excessive_punctuation"),
    (r"\?\?+", "excessive_punctuation"),
]

# Compiled for performance
_FORBIDDEN_COMPILED = [
    (re.compile(pat, re.IGNORECASE), reason)
    for pat, reason in FORBIDDEN_PATTERNS
]

# --- REQUIRED TRAITS ---
# Structural requirements for email body text.

REQUIRED_TRAITS = {
    "max_exclamation_marks": 1,     # max 1 in entire body (subject can have 1)
    "max_paragraph_sentences": 3,   # paragraphs should be 1-3 sentences
    "max_cta_buttons": 1,           # single CTA per email
    "max_bullet_items": 6,          # no more than 6 bullets in one list
    "min_body_words": 20,           # must have substance
    "max_body_words": 800,          # for standalone emails (digest is exempt)
}

# --- VOICE SIGNATURE ---
# The patterns that make it recognizably HedgeSpark.

VOICE_SIGNATURE = {
    "greeting_style": "Hi, / Hi {name},",           # never Hey, Dear, Hello
    "sign_off_style": "Talk soon, / Looking forward to building this together,",
    "sender_name": "Andrea",                          # first person singular
    "brand_name": "HedgeSpark",                       # always one word, capital H capital S
    "cta_verbs": [                                    # invitation verbs, not commands
        "Open", "See", "Start", "Continue", "View", "Check", "Explore",
    ],
    "forbidden_cta_verbs": [                          # never use
        "Buy", "Act", "Hurry", "Grab", "Claim", "Unlock",
        "Get started now", "Don't wait",
    ],
    "fallback_action": "reply to this email",         # always present as escape hatch
    "tone_anchors": [
        "concrete over abstract",
        "specific over general",
        "invitation over command",
        "explanation over assertion",
        "acknowledgment over assumption",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# PART 3 — EMAIL STRUCTURE TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════
#
# Every HedgeSpark email follows this emotional arc:
#
# 1. GROUND      — Start with a concrete fact. What happened. What exists.
#                   No greeting-then-filler. The first sentence carries weight.
#                   Example: "HedgeSpark found 3 insights on your store."
#
# 2. CONTEXTUALIZE — Explain what it means. Why it matters. How it works.
#                    This is the understanding phase. 1-2 paragraphs max.
#                    Use specifics, not generalities.
#
# 3. EVIDENCE     — Show proof. Numbers, product names, before/after.
#                   Bullet points or steps. Never just claims.
#
# 4. ACTION       — Single CTA. Invitation, not command.
#                   "Open your dashboard" / "See your insights"
#                   Always after understanding is built, never at the top.
#
# 5. SAFETY NET   — "Reply to this email" / "the chatbot can help"
#                   Always present. The merchant is never alone.
#
# Visual structure (HTML):
#   [Heading]
#   [Ground paragraph]
#   [Context paragraph]
#   [Section title]
#   [Evidence: bullets or steps]
#   [CTA button]
#   [Safety note in smaller text]
#   [Signature if personal email]


# ═══════════════════════════════════════════════════════════════════════════
# PART 4 — VALIDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BrandCheckResult:
    """Result of brand voice validation."""
    passed: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_violation(self, msg: str) -> None:
        self.violations.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_email_text(
    text: str,
    *,
    is_digest: bool = False,
    check_structure: bool = True,
) -> BrandCheckResult:
    """
    Validate email text against HedgeSpark brand rules.

    Args:
        text: The plain text (or stripped HTML) of the email body.
        is_digest: If True, relaxes max_body_words (digests are data-heavy).
        check_structure: If True, checks structural traits (word count, etc).

    Returns:
        BrandCheckResult with violations and warnings.
    """
    result = BrandCheckResult()

    if not text or not text.strip():
        result.add_violation("empty_body")
        return result

    # --- Forbidden patterns ---
    text_lower = text.lower()
    for regex, reason in _FORBIDDEN_COMPILED:
        match = regex.search(text)
        if match:
            result.add_violation(f"forbidden:{reason}:{match.group()}")

    if not check_structure:
        return result

    # --- Exclamation marks ---
    excl_count = text.count("!")
    max_excl = REQUIRED_TRAITS["max_exclamation_marks"]
    if excl_count > max_excl:
        result.add_warning(f"exclamation_marks:{excl_count}>{max_excl}")

    # --- Word count ---
    words = text.split()
    word_count = len(words)
    if word_count < REQUIRED_TRAITS["min_body_words"]:
        result.add_warning(f"too_short:{word_count}_words")

    if not is_digest and word_count > REQUIRED_TRAITS["max_body_words"]:
        result.add_warning(f"too_long:{word_count}_words")

    # --- Brand name consistency ---
    # "HedgeSpark" (space) appears in some older code. Should be "HedgeSpark".
    if "hedge spark" in text_lower and "hedgespark" not in text_lower:
        result.add_warning("brand_name_spacing:use_HedgeSpark_not_Hedge_Spark")

    # --- Greeting check ---
    first_line = text.strip().split("\n")[0].strip().lower()
    if first_line.startswith("hey") or first_line.startswith("dear"):
        result.add_violation("wrong_greeting:use_Hi_not_Hey_or_Dear")

    return result


def validate_subject_line(subject: str) -> BrandCheckResult:
    """Validate an email subject line against brand rules."""
    result = BrandCheckResult()

    if not subject:
        result.add_violation("empty_subject")
        return result

    # Forbidden patterns in subject
    for regex, reason in _FORBIDDEN_COMPILED:
        match = regex.search(subject)
        if match:
            result.add_violation(f"forbidden_in_subject:{reason}:{match.group()}")

    # All caps words (more than 1 consecutive caps word = shouting)
    caps_words = re.findall(r"\b[A-Z]{2,}\b", subject)
    # Filter out brand names and acronyms
    real_caps = [w for w in caps_words if w not in {"AI", "CRO", "AOV", "CVR"}]
    if len(real_caps) > 1:
        result.add_warning(f"caps_shouting:{real_caps}")

    # Length check
    if len(subject) > 80:
        result.add_warning(f"subject_too_long:{len(subject)}_chars")

    return result


def get_brand_rules() -> dict:
    """Return brand rules as a dict for operator visibility / debugging."""
    return {
        "forbidden_pattern_count": len(FORBIDDEN_PATTERNS),
        "required_traits": REQUIRED_TRAITS,
        "voice_signature": VOICE_SIGNATURE,
        "structure": [
            "1. GROUND — concrete fact",
            "2. CONTEXTUALIZE — what it means",
            "3. EVIDENCE — proof (bullets/steps/numbers)",
            "4. ACTION — single CTA (invitation, not command)",
            "5. SAFETY NET — reply/chatbot escape hatch",
        ],
    }
