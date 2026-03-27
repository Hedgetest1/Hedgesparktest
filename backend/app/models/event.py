from sqlalchemy import BigInteger, Column, Integer, String
from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    visitor_id = Column(String)
    event_type = Column(String)

    # url stores the raw page URL (window.location.href or pathname).
    # product_url stores the product path only when the event fired on a
    # product page; NULL for all other pages.
    # Keeping them separate allows analytics to scope queries per product
    # without pattern-matching on url.
    url = Column(String, nullable=True)
    product_url = Column(String, nullable=True)

    timestamp = Column(BigInteger, nullable=True)   # epoch milliseconds
    dwell_seconds = Column(Integer, nullable=True)
    max_scroll_depth = Column(Integer, nullable=True)
    shop_domain = Column(String, nullable=False)

    # Source attribution — populated by spark-tracker.js since migration j7e0a4b8c3d6.
    # Nullable: rows ingested before that migration have no source data.
    source_type = Column(String, nullable=True)   # direct | google | facebook | …
    referrer = Column(String, nullable=True)       # raw document.referrer value
    utm_medium = Column(String(128), nullable=True)  # raw utm_medium for paid/organic classification

    # Device type — "mobile" or "desktop", sent by tracker since v3.
    # Nullable: rows before this addition have no device data.
    device_type = Column(String(16), nullable=True)

    # Shopify numeric product ID — captured from window.ShopifyAnalytics.meta.product.id
    # on product pages since migration o1a2b3c4d5e6.  Used to bridge product_id → product_url
    # at order ingestion time so get_real_product_conversion_map() returns real data.
    # Nullable: NULL for all non-product pages and rows before this migration.
    product_id = Column(String(64), nullable=True)
