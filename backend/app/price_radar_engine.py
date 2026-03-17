import random
from datetime import datetime

def simulate_competitor_price(product_name: str):

    base_price = random.uniform(20,120)

    drop = random.choice([True, False, False])

    if drop:
        new_price = base_price * random.uniform(0.6,0.85)
    else:
        new_price = base_price

    return round(new_price,2), drop


def evaluate_price(product_name):

    price, drop = simulate_competitor_price(product_name)

    if drop:
        return {
            "price_drop": True,
            "competitor_price": price,
            "recommended_action": "PRICE_DROP_ALERT",
            "reason": "Competitor price dropped"
        }

    return {
        "price_drop": False,
        "competitor_price": price
    }
