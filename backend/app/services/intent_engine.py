def calculate_intent_score(events):

    score = 0

    for event in events:

        if event.event_type == "product_view":
            score += 1

        if event.event_type == "wishlist_add":
            score += 5

        if event.event_type == "add_to_cart":
            score += 10

        if event.event_type == "purchase":
            score += 50

    return score

from app.services.opportunity_engine import update_product_opportunity
from datetime import datetime
from app.models.visitor_product_state import VisitorProductState
from app.core.url_utils import normalize_product_url


def calculate_intent_score_v2(state):

    score = 0

    # views
    score += state.total_views * 5

    # dwell
    if state.total_dwell_seconds >= 10:
        score += 10
    if state.total_dwell_seconds >= 30:
        score += 15
    if state.total_dwell_seconds >= 60:
        score += 20

    # scroll
    if state.max_scroll_depth >= 50:
        score += 10
    if state.max_scroll_depth >= 80:
        score += 15
    if state.max_scroll_depth >= 100:
        score += 20

    # wishlist
    if state.wishlist_added:
        score += 40

    return score


def classify_intent_level(score):

    if score >= 80:
        return "HOT"

    if score >= 40:
        return "WARM"

    return "COLD"

def recommend_action(state):

    if state.wishlist_added and state.intent_score >= 80:
        return "PRICE_DROP_ALERT"

    if state.intent_score >= 80:
        return "WISHLIST_REMINDER"

    if state.intent_score >= 40:
        return "ENGAGE_LATER"

    return "NO_ACTION"


def build_intent_explanation(state):

    reasons = []

    if state.total_views > 0:
        reasons.append(f"{state.total_views} product views")

    if state.total_dwell_seconds > 0:
        reasons.append(f"{state.total_dwell_seconds}s dwell time")

    if state.max_scroll_depth > 0:
        reasons.append(f"{state.max_scroll_depth}% max scroll")

    if state.wishlist_added:
        reasons.append("wishlist added")

    if not reasons:
        return "No significant signals yet"

    return ", ".join(reasons)

def update_visitor_product_state(db, event):

    # Normalize to /products/{handle} — reject full URLs, non-product pages,
    # and any garbage that would create an unresolvable product key.
    product_url = normalize_product_url(event.page_url)

    if not product_url:
        return

    state = db.query(VisitorProductState).filter(
        VisitorProductState.visitor_id == event.visitor_id,
        VisitorProductState.product_url == product_url
    ).first()

    now = event.occurred_at or datetime.utcnow()

    if not state:
        state = VisitorProductState(
            visitor_id=event.visitor_id,
            product_url=product_url,
            first_seen=now,
            last_seen=now
        )
        db.add(state)
        db.flush()

    state.last_seen = now

    if event.event_type == "product_view":
        state.total_views += 1

    if event.dwell_seconds:
        state.total_dwell_seconds += event.dwell_seconds

    if event.scroll_depth and event.scroll_depth > state.max_scroll_depth:
        state.max_scroll_depth = event.scroll_depth

    if event.event_type == "wishlist_add":
        state.wishlist_added = True

    state.intent_score = calculate_intent_score_v2(state)
    state.intent_level = classify_intent_level(state.intent_score)
    state.recommended_action = recommend_action(state)
    state.intent_explanation = build_intent_explanation(state)

    db.commit()

    update_product_opportunity(db, state.product_url)
