"""
legal_pages.py — Privacy Policy + Cookie Policy API endpoints.

Serves structured JSON so both the landing page and the dashboard can
render the policies. Also serves pre-rendered HTML for direct browser
access at /privacy and /cookies.

These endpoints are public — no auth required.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

_LAST_UPDATED = "April 2026"
_CONTACT_EMAIL = "privacy@hedgesparkhq.com"
_DPO_EMAIL = "privacy@hedgesparkhq.com"

# -----------------------------------------------------------------------
# Privacy Policy — structured
# -----------------------------------------------------------------------

_PRIVACY_SECTIONS = [
    {
        "id": "data-controller",
        "title": "Data Controller",
        "body": (
            "HedgeSpark operates as a data processor under GDPR Article 28. "
            "The merchant who installs HedgeSpark on their Shopify store is the "
            "data controller. HedgeSpark processes data exclusively on behalf of "
            "and under the instructions of the merchant."
        ),
    },
    {
        "id": "what-we-collect",
        "title": "What we collect",
        "body": (
            "HedgeSpark collects pseudonymous behavioral data from storefront "
            "visitors: page views, scroll depth, dwell time, click events, and "
            "cart interactions. We assign an anonymous visitor identifier via a "
            "first-party cookie. We do NOT collect personal information such as "
            "names, email addresses, phone numbers, or payment details of "
            "storefront customers."
        ),
    },
    {
        "id": "legal-basis",
        "title": "Legal basis for processing",
        "body": (
            "Processing is based on the merchant's legitimate interest in "
            "understanding storefront engagement (GDPR Article 6(1)(f)). Where "
            "applicable law requires visitor consent (e.g. ePrivacy Directive, "
            "CCPA/CPRA), the merchant must obtain consent before loading the "
            "HedgeSpark tracker script. Our tracker respects the Global Privacy "
            "Control (GPC) signal, the Do Not Track (DNT) header, and an "
            "explicit consent API (window.hsSetConsent)."
        ),
    },
    {
        "id": "how-we-use-it",
        "title": "How we use data",
        "body": (
            "All data is used exclusively to generate product intelligence "
            "signals, measure conversion impact, score revenue at risk, and "
            "improve recommendations for the merchant's store. We do not sell, "
            "share, or transfer visitor data to third parties for advertising "
            "or marketing purposes."
        ),
    },
    {
        "id": "sub-processors",
        "title": "Sub-processors",
        "body": (
            "HedgeSpark uses the following sub-processors: Shopify (merchant "
            "platform integration), Resend (transactional email delivery), "
            "Anthropic and OpenAI (AI analysis — no raw PII is sent to LLMs, "
            "enforced by a runtime PII guard), Sentry (error tracking with "
            "send_default_pii=false). A full processor register is maintained "
            "at docs/processors.md. All sub-processors have signed Data "
            "Processing Agreements (DPAs)."
        ),
    },
    {
        "id": "data-storage",
        "title": "Data storage and security",
        "body": (
            "All data is encrypted at rest and in transit (TLS 1.2+). "
            "Merchant access tokens are encrypted with AES-256. Data is stored "
            "on secure servers within the EU. Behavioral event data is retained "
            "for a maximum of 395 days; visitor purchase sessions for 730 days. "
            "Automated retention sweeps run daily. An audit log with hash-chain "
            "integrity verification ensures tamper evidence."
        ),
    },
    {
        "id": "data-subject-rights",
        "title": "Your rights",
        "body": (
            "Under GDPR, UK DPA 2018, CCPA/CPRA, LGPD, and other applicable "
            "laws, data subjects have the right to: access their data (Art. 15), "
            "rectify inaccurate data (Art. 16), request erasure (Art. 17), "
            "data portability (Art. 20), object to processing (Art. 21), and "
            "not be subject to solely automated decision-making (Art. 22). "
            "Merchants can exercise these rights via their dashboard (Settings > "
            "Privacy) or by contacting us. Storefront visitors should contact "
            "the merchant (data controller) directly."
        ),
    },
    {
        "id": "international-transfers",
        "title": "International data transfers",
        "body": (
            "When data is transferred outside the EEA, we rely on Standard "
            "Contractual Clauses (SCCs) as approved by the European Commission. "
            "Sub-processors receiving data outside the EEA have signed SCCs. "
            "We honor the Global Privacy Control signal for California residents "
            "(CCPA/CPRA) and support opt-out requests under applicable US state "
            "privacy laws."
        ),
    },
    {
        "id": "breach-notification",
        "title": "Breach notification",
        "body": (
            "In the event of a personal data breach, HedgeSpark will notify "
            "the relevant supervisory authority within 72 hours (GDPR Art. 33) "
            "and affected data subjects without undue delay when required "
            "(Art. 34). An automated breach classifier monitors for security "
            "incidents continuously."
        ),
    },
    {
        "id": "children",
        "title": "Children's privacy",
        "body": (
            "HedgeSpark does not knowingly collect data from children under 16 "
            "(or the applicable age of consent in the relevant jurisdiction). "
            "Merchants are responsible for complying with COPPA, EU age of "
            "consent requirements, and other child protection laws on their "
            "storefronts."
        ),
    },
    {
        "id": "changes",
        "title": "Changes to this policy",
        "body": (
            "We may update this policy to reflect changes in our practices or "
            "applicable laws. Material changes will be communicated via the "
            "merchant dashboard and email notification at least 30 days before "
            "they take effect."
        ),
    },
    {
        "id": "contact",
        "title": "Contact",
        "body": (
            f"For privacy inquiries: {_CONTACT_EMAIL}. "
            f"Data Protection contact: {_DPO_EMAIL}."
        ),
    },
]

# -----------------------------------------------------------------------
# Cookie Policy — structured
# -----------------------------------------------------------------------

_COOKIE_SECTIONS = [
    {
        "id": "what-cookies",
        "title": "What cookies we use",
        "body": (
            "HedgeSpark uses a single first-party cookie on the merchant's "
            "storefront to maintain visitor session continuity. This cookie "
            "contains only a pseudonymous identifier (visitor_id). No "
            "cross-site tracking is performed. No third-party cookies are set."
        ),
    },
    {
        "id": "cookie-details",
        "title": "Cookie details",
        "cookies": [
            {
                "name": "hs_vid",
                "purpose": "Anonymous visitor identification for behavioral analytics",
                "type": "First-party, persistent",
                "duration": "90 days",
                "data": "Pseudonymous visitor ID (UUID)",
            },
            {
                "name": "hs_session",
                "purpose": "Merchant dashboard authentication",
                "type": "First-party, HttpOnly, Secure, SameSite=None",
                "duration": "Session (24h)",
                "data": "Encrypted session token (no PII)",
            },
        ],
    },
    {
        "id": "local-storage",
        "title": "Local storage",
        "body": (
            "The tracker script may read localStorage key 'hs_consent' as a "
            "legacy consent signal ('1' = consented, '0' = denied). This is "
            "a fallback mechanism; the preferred integration is via "
            "window.hsSetConsent(given, region)."
        ),
    },
    {
        "id": "consent-mechanism",
        "title": "How to control cookies",
        "body": (
            "Merchants can integrate their cookie consent banner with "
            "HedgeSpark by calling window.hsSetConsent(true/false, 'EU'/'US'/...) "
            "from their consent management platform. When consent is denied, "
            "the tracker stops collecting data immediately. The backend also "
            "respects the browser's Global Privacy Control (GPC) and Do Not "
            "Track (DNT) signals. Visitors can clear cookies via their browser "
            "settings at any time."
        ),
    },
    {
        "id": "contact",
        "title": "Contact",
        "body": f"For cookie-related inquiries: {_CONTACT_EMAIL}.",
    },
]


# -----------------------------------------------------------------------
# JSON endpoints
# -----------------------------------------------------------------------

@router.get("/legal/privacy")
def privacy_policy_json():
    """Structured privacy policy for programmatic consumption."""
    return {
        "title": "Privacy Policy",
        "last_updated": _LAST_UPDATED,
        "contact_email": _CONTACT_EMAIL,
        "sections": _PRIVACY_SECTIONS,
    }


@router.get("/legal/cookies")
def cookie_policy_json():
    """Structured cookie policy for programmatic consumption."""
    return {
        "title": "Cookie Policy",
        "last_updated": _LAST_UPDATED,
        "contact_email": _CONTACT_EMAIL,
        "sections": _COOKIE_SECTIONS,
    }


# -----------------------------------------------------------------------
# HTML endpoints — direct browser access
# -----------------------------------------------------------------------

def _render_html(title: str, sections: list[dict]) -> str:
    body_parts = []
    for s in sections:
        body_parts.append(f'<h2>{s["title"]}</h2>')
        if "body" in s:
            body_parts.append(f'<p>{s["body"]}</p>')
        if "cookies" in s:
            for c in s["cookies"]:
                body_parts.append(
                    f'<div class="cookie">'
                    f'<strong>{c["name"]}</strong> — {c["purpose"]}<br>'
                    f'Type: {c["type"]} · Duration: {c["duration"]} · '
                    f'Data: {c["data"]}'
                    f'</div>'
                )
    body_html = "\n".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — HedgeSpark</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 42rem; margin: 3rem auto; padding: 0 1.5rem;
         color: #c8c8d0; background: #080811; line-height: 1.75; }}
  h1 {{ color: #fff; font-size: 1.75rem; margin-bottom: 0.25rem; }}
  h2 {{ color: #e8a04e; font-size: 1rem; margin-top: 2rem; }}
  p {{ font-size: 0.875rem; }}
  .meta {{ font-size: 0.75rem; color: #64748b; }}
  a {{ color: #a78bfa; }}
  .cookie {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07);
             border-radius: 0.75rem; padding: 1rem; margin: 0.75rem 0; font-size: 0.8rem; }}
  .back {{ font-size: 0.8rem; color: #64748b; text-decoration: none; }}
  .back:hover {{ color: #94a3b8; }}
</style>
</head>
<body>
<a href="/" class="back">&larr; Back to home</a>
<h1>{title}</h1>
<p class="meta">Last updated: {_LAST_UPDATED}</p>
{body_html}
</body>
</html>"""


@router.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy_html():
    return HTMLResponse(_render_html("Privacy Policy", _PRIVACY_SECTIONS))


@router.get("/cookie-policy", response_class=HTMLResponse)
def cookie_policy_html():
    return HTMLResponse(_render_html("Cookie Policy", _COOKIE_SECTIONS))
