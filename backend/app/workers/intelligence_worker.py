import sys
sys.path.append("/opt/wishspark/backend")

import time
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.models.visitor_product_state import VisitorProductState
from app.services.opportunity_engine import update_product_opportunity

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def log(msg):
    print(f"[WORKER] {datetime.utcnow().isoformat()} | {msg}")


def run_cycle():
    log("starting intelligence cycle")
    db = SessionLocal()

    try:
        product_urls = [
            row[0]
            for row in db.query(VisitorProductState.product_url)
            .filter(VisitorProductState.product_url.isnot(None))
            .distinct()
            .all()
        ]

        log(f"found {len(product_urls)} product urls")

        for product_url in product_urls:
            try:
                update_product_opportunity(db, product_url)
                log(f"updated opportunity for {product_url}")
            except Exception as e:
                log(f"error on {product_url}: {e}")

        log("cycle finished")
    finally:
        db.close()


def main():
    log("worker started")

    while True:
        run_cycle()
        log("sleeping 10 minutes")
        time.sleep(600)


if __name__ == "__main__":
    main()
