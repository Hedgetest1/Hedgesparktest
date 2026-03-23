from __future__ import annotations

from pydantic import BaseModel


class ProductTrendRow(BaseModel):
    product_url: str
    last_7_days_views: list[int]
    total_views: int


class ProductTrendResponse(BaseModel):
    shop_domain: str
    count: int
    products: list[ProductTrendRow]
