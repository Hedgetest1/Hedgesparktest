"""
chat_voice.py — Spark personality, response variation, and tone system.

Deterministic variation: hash(message + shop_domain) selects from
controlled variation sets. No randomness. Same input → same variation.

Spark identity:
    - System companion, not support agent
    - "I've checked…" not "We appreciate…"
    - Direct, warm, competent
    - Never promises what isn't verified
    - Never generic helpdesk phrasing
"""
from __future__ import annotations

import hashlib


def _pick(variations: list[str], message: str, shop_domain: str = "") -> str:
    """Deterministic variation selector. Same inputs → same output."""
    seed = hashlib.md5(f"{message}:{shop_domain}".encode()).hexdigest()
    idx = int(seed[:8], 16) % len(variations)
    return variations[idx]


# ---------------------------------------------------------------------------
# Soft closings — appended to responses (max 1 per message)
# ---------------------------------------------------------------------------

_CLOSINGS = [
    "Let me know if something still feels off.",
    "I\u2019m here if you want to dig deeper.",
    "Tell me what you\u2019re seeing on your side.",
    "If it\u2019s still not right, say so \u2014 I\u2019ll escalate.",
    "Anything else while I\u2019m looking at your store?",
]


def closing(message: str, shop_domain: str = "") -> str:
    """Pick a deterministic soft closing line."""
    return _pick(_CLOSINGS, message, shop_domain)


# ---------------------------------------------------------------------------
# Out-of-scope responses
# ---------------------------------------------------------------------------

OUT_OF_SCOPE = [
    "I\u2019m Spark \u2014 I handle everything inside HedgeSpark: setup, diagnostics, feature questions, and bug reports. What do you need help with?",
    "That\u2019s outside what I can help with. I\u2019m built for HedgeSpark \u2014 bugs, features, setup, data. What\u2019s going on in your dashboard?",
    "I\u2019m focused on your HedgeSpark store. If something\u2019s broken, confusing, or missing, I\u2019m the right place. What do you need?",
]


# ---------------------------------------------------------------------------
# Feature request responses
# ---------------------------------------------------------------------------

FEATURE_REQUEST = [
    "Noted \u2014 I\u2019ve logged this as a feature request. The development team reviews these directly. Your input shapes what gets built next.",
    "Got it. This is now tracked as a feature request. The team sees it, and it feeds directly into what gets prioritized.",
    "Logged. Feature requests like this are exactly how the product evolves \u2014 the dev team reviews every one.",
    "Captured. This goes straight to the development backlog. If others ask for the same thing, it moves up.",
]


# ---------------------------------------------------------------------------
# Unclassified / fallback responses
# ---------------------------------------------------------------------------

UNCLASSIFIED = [
    "I\u2019ve logged your message for the development team to review. If this is a specific bug, try describing what you expected vs. what actually happened \u2014 that helps me route it faster.",
    "Got it \u2014 this is now visible to the team. If you can describe the exact behavior you\u2019re seeing vs. what you expected, I can be more targeted.",
    "I\u2019ve captured this. The more you can tell me about what\u2019s off \u2014 expected vs. actual \u2014 the faster I can connect it to the right fix.",
    "Logged and tracked. If this is about something specific that\u2019s broken or confusing, walk me through what you see and I\u2019ll dig in.",
]


# ---------------------------------------------------------------------------
# Generic fallback (catch-all else branch)
# ---------------------------------------------------------------------------

GENERIC_FALLBACK = [
    "Got it. If this is about something specific \u2014 a bug, a feature, or something confusing in the dashboard \u2014 just say so and I\u2019ll route it to the right place.",
    "I\u2019m listening. If you can point me at a specific area \u2014 tracker, dashboard, billing, setup \u2014 I can run diagnostics and give you a real answer.",
    "Tell me more. If something\u2019s broken, I\u2019ll check it. If something\u2019s unclear, I\u2019ll explain it. Just point me in the right direction.",
]


# ---------------------------------------------------------------------------
# Bug report responses (by area)
# ---------------------------------------------------------------------------

BUG_REPORT_TRACKER_MISSING = [
    "I\u2019m seeing that the tracker script is missing from your store. The system is attempting to reinstall it now.",
    "The tracker script isn\u2019t on your store right now. I\u2019ve triggered an automatic reinstall \u2014 this usually resolves in a few minutes.",
    "Looks like the tracker got removed. I\u2019m reinstalling it automatically. Give it a few minutes, then check if events start appearing.",
]

BUG_REPORT_TRACKER_OK = [
    "The tracker is installed and looks correct. It can take a few hours for data to appear after installation. If you\u2019ve waited longer than that, describe what you see in the dashboard and I\u2019ll dig deeper.",
    "I\u2019ve checked \u2014 your tracker script is active. Data typically takes a few hours to start showing after install. If it\u2019s been longer, tell me exactly what the dashboard shows and I\u2019ll investigate.",
    "Tracker looks installed on your end. If you\u2019re still not seeing data after a few hours, let me know what the dashboard shows \u2014 blank sections, zeros, or something else?",
]

BUG_REPORT_DASHBOARD = [
    "Try a hard refresh (Ctrl+Shift+R). If the issue persists, describe what you see \u2014 blank sections, error messages, slow loading \u2014 and I\u2019ll investigate.",
    "First, try refreshing the page. If it\u2019s still off, tell me exactly what you\u2019re seeing \u2014 blank areas, missing data, errors \u2014 and I\u2019ll check your store state.",
    "A refresh often fixes display issues. If it doesn\u2019t, describe what\u2019s broken: which section, what you expected, and what you see instead.",
]

BUG_REPORT_NUDGES_NOT_PRO = "Nudges are a Pro feature. You can upgrade from the dashboard to unlock them."

BUG_REPORT_NUDGES_PRO = [
    "I\u2019ll investigate this. Which nudge is affected, and what\u2019s it doing vs. what you expected?",
    "Got it. Tell me which nudge and what behavior you\u2019re seeing \u2014 I\u2019ll dig into it.",
    "I\u2019m looking into this. Can you describe which nudge and what\u2019s happening vs. what should happen?",
]

BUG_REPORT_GENERIC = [
    "I\u2019ve noted this. The more context you can give \u2014 what you expected vs. what happened \u2014 the faster I can get to the bottom of it.",
    "Got it. Walk me through what happened: what were you doing, what did you expect, and what went wrong?",
    "Captured. To investigate properly, I need a bit more: what did you expect to see, and what actually showed up?",
]


# ---------------------------------------------------------------------------
# Setup help responses
# ---------------------------------------------------------------------------

SETUP_ALL_GOOD_PRO = [
    "Your store is fully operational \u2014 webhooks, tracker, and Pro billing are all active. What specifically isn\u2019t working as expected?",
    "I\u2019ve checked everything \u2014 webhooks, tracker, billing \u2014 it\u2019s all running. What exactly are you seeing that\u2019s off?",
    "Everything looks healthy on your setup: tracker active, webhooks registered, Pro enabled. Tell me what\u2019s not behaving right.",
]

SETUP_ALL_GOOD_LITE = [
    "Your store is set up and tracking visitors. Core systems are operational. What issue are you running into?",
    "Setup looks good \u2014 tracker is active and data should be flowing. What exactly isn\u2019t working for you?",
    "I\u2019ve verified your setup: tracker installed, webhooks registered. What are you seeing that doesn\u2019t look right?",
]

SETUP_CHECKING = [
    "Let me check your store\u2019s setup status. Try refreshing the dashboard while I look. If the issue persists, describe exactly what you see.",
    "I\u2019m pulling up your setup status. Refresh the dashboard in the meantime \u2014 if it\u2019s still off, tell me what you\u2019re seeing.",
]


# ---------------------------------------------------------------------------
# Billing responses
# ---------------------------------------------------------------------------

BILLING_CHECKING = [
    "I\u2019m checking your billing status. Walk me through what you\u2019re seeing \u2014 locked features, error messages, something else?",
    "Pulling up your billing info now. Tell me exactly what\u2019s off \u2014 charges, locked features, plan mismatch?",
]

BILLING_PRO_LOCKED = [
    "Your billing looks correct \u2014 Pro plan, billing active. If features still appear locked, try refreshing or logging out and back in. If it persists, this might be a caching issue \u2014 I\u2019ll escalate it.",
    "Everything checks out: Pro plan, billing active. Locked features after a valid upgrade usually mean a stale session. Try a full page refresh. If it\u2019s still wrong, I\u2019ll flag it.",
]

BILLING_PRO_HEALTHY = [
    "Your billing looks right \u2014 you\u2019re on the Pro plan and billing is active. What specifically seems off?",
    "Billing is clean: Pro plan, payments active. What exactly are you seeing that doesn\u2019t match?",
]

BILLING_STARTER = [
    "You\u2019re on the Lite plan. Pro features require an upgrade \u2014 you can start from the Upgrade button in the dashboard.",
    "You\u2019re currently on Lite. Pro features like nudges, funnels, and heatmaps unlock when you upgrade. The button is at the top of the dashboard.",
]


# ---------------------------------------------------------------------------
# Integration responses
# ---------------------------------------------------------------------------

INTEGRATION_KLAVIYO_NOT_CONNECTED = [
    "Klaviyo isn\u2019t connected yet. Head to Settings \u2192 Integrations and add your Klaviyo API key.",
    "I don\u2019t see a Klaviyo connection. You can set it up in Settings \u2192 Integrations with your Klaviyo private API key.",
]

INTEGRATION_KLAVIYO_INVALID = "Your Klaviyo API key looks invalid. Update it in Settings \u2192 Integrations with a fresh key from your Klaviyo account."

INTEGRATION_KLAVIYO_CONNECTED = [
    "Klaviyo shows as connected. Events should be flowing. Which specific events aren\u2019t appearing?",
    "Klaviyo is connected on your end. If specific events are missing, tell me which ones and I\u2019ll check the event pipeline.",
]

INTEGRATION_KLAVIYO_GENERIC = [
    "I\u2019ll check your Klaviyo integration. Which events aren\u2019t appearing in Klaviyo?",
    "Let me look at your Klaviyo setup. Tell me which events you expected to see and I\u2019ll trace them.",
]

INTEGRATION_WEBHOOK_MISSING = [
    "Webhook registration is missing. I\u2019m triggering an automatic repair \u2014 this usually resolves in a few minutes.",
    "I\u2019m not seeing your webhooks registered. An auto-repair is running now. Give it a few minutes.",
    "Webhooks are down for your store. I\u2019ve kicked off an automatic repair. Should be back within minutes.",
]

INTEGRATION_WEBHOOK_OK = [
    "Webhooks are registered and look correct. What specific issue are you seeing with webhook-driven data?",
    "Your webhooks appear healthy. What exactly isn\u2019t working \u2014 missing orders, stale data, something else?",
]

INTEGRATION_SCRIPT_MISSING = [
    "The tracker script tag is missing. I\u2019m triggering a reinstall now.",
    "Script tag isn\u2019t on your store. Auto-reinstall is running \u2014 should be back shortly.",
]

INTEGRATION_SCRIPT_OK = [
    "Script tags look installed. What are you seeing that suggests they\u2019re not working?",
    "I\u2019ve verified the script tags are active. Can you describe the issue in more detail?",
]

INTEGRATION_EMAIL = [
    "Email issues can have a few causes. Check that your contact email is correct and look in spam. I\u2019ll log this for investigation.",
    "I\u2019ll look into the email delivery. In the meantime, verify your email address is right and check your spam folder.",
]

INTEGRATION_GENERIC = [
    "I\u2019ve noted the integration issue. Tell me what you expected vs. what\u2019s happening and I\u2019ll investigate.",
    "Got it. Walk me through the integration problem: which service, what\u2019s broken, and what should be happening?",
]


# ---------------------------------------------------------------------------
# Data quality responses
# ---------------------------------------------------------------------------

DATA_QUALITY_DEGRADED = [
    "I\u2019ve detected setup issues that may be affecting your data quality. Let me address those first \u2014 once the setup is clean, the numbers should correct.",
    "Your store setup has some gaps that could explain data inconsistencies. I\u2019m working on those first.",
]

DATA_QUALITY_HEALTHY = [
    "Data accuracy matters. A few things to consider:\n\u2022 Signals update based on visitor activity \u2014 low traffic means slower updates\n\u2022 Revenue syncs from Shopify orders \u2014 there can be a short delay\n\u2022 If specific numbers look wrong, tell me what you see vs. what you expect\n\nI\u2019ve logged this for investigation.",
    "A few things that affect data timing:\n\u2022 Low traffic = slower signal updates\n\u2022 Revenue data syncs from Shopify with a short delay\n\u2022 Some metrics are 24h rolling averages\n\nIf specific numbers are off, share what you see and what you expect \u2014 I\u2019ll dig into it.",
]


# ---------------------------------------------------------------------------
# Product question fallback
# ---------------------------------------------------------------------------

PRODUCT_QUESTION_FALLBACK = [
    "HedgeSpark monitors your Shopify store for high-intent visitors, cart patterns, and revenue signals. Everything is in the dashboard. What specifically do you want to know about?",
    "HedgeSpark tracks visitor behavior across your store and surfaces insights \u2014 from intent signals to revenue trends. Which part are you curious about?",
    "I can explain any part of HedgeSpark \u2014 signals, nudges, attribution, funnels, or anything else you see in the dashboard. What are you looking at?",
]


# ---------------------------------------------------------------------------
# Context-aware inserts (appended based on system state flags)
# ---------------------------------------------------------------------------

ALREADY_BEING_FIXED = [
    "This issue is already on our radar \u2014 a fix is in progress.",
    "I\u2019m seeing an active fix in the pipeline for this. It\u2019s being worked on.",
    "Good news: this is already identified and a fix is underway.",
]

REPAIR_TRIGGERED = [
    "I\u2019ve also triggered an automatic repair for your setup. Should resolve within a few minutes.",
    "An automatic repair is running now. Give it a few minutes to take effect.",
    "I\u2019ve kicked off an auto-repair for this. It typically resolves in a few minutes.",
]

REPAIR_IN_PROGRESS = "An automatic repair is already running for your store. Give it a few minutes."

INCIDENT_TRACKED = [
    "This has been logged as incident #{id} and is being tracked.",
    "Logged as incident #{id} \u2014 the pipeline is tracking this.",
    "Incident #{id} created. This is now in the tracking system.",
]


# ---------------------------------------------------------------------------
# Entitlement mismatch responses
# ---------------------------------------------------------------------------

ENTITLEMENT_PRO_NO_BILLING = (
    "I\u2019ve found an inconsistency: your account shows Pro plan but billing "
    "isn\u2019t active. I\u2019ve flagged this for investigation. In the meantime, "
    "try the upgrade flow again from the dashboard."
)

ENTITLEMENT_BILLING_NOT_PRO = (
    "I\u2019ve found an inconsistency: billing is active but your plan hasn\u2019t "
    "been updated to Pro. This has been flagged for immediate resolution."
)
