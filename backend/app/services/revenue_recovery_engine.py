from typing import List, Dict

def generate_revenue_actions(
    visitor_scores: List[Dict],
    opportunities: List[Dict],
    price_intel: List[Dict]
):
    actions = []

    for opp in opportunities:

        url = opp.get("url")
        visitors = opp.get("visitors",0)
        clicks = opp.get("clicks",0)
        avg_dwell = opp.get("avg_dwell",0)

        if not url:
            continue

        conversion_gap = visitors - clicks

        if conversion_gap > 10 and avg_dwell > 15:

            action = {
                "product_url": url,
                "problem": "high_traffic_low_conversion",
                "suggested_action": "improve_cta_or_price",
                "expected_conversion_lift": "+10% to +25%",
                "confidence": 0.75
            }

            actions.append(action)

    for p in price_intel:

        url = p.get("product_url")
        opportunity = p.get("price_opportunity")

        if opportunity == "HIGH_INTENT_PRICE_OPPORTUNITY":

            actions.append({
                "product_url": url,
                "problem": "price_friction",
                "suggested_action": "test_price_drop_or_scarcity",
                "expected_conversion_lift": "+15% to +30%",
                "confidence": p.get("confidence_score",70)/100
            })

    return actions
