from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "new",
    "best",
    "premium",
    "official",
    "shop",
    "store",
    "product",
    "item",
    "collection",
    "edition",
    "model",
    "style",
    "design",
    "classic",
}

GENERIC_HINTS = {
    "t-shirt",
    "shirt",
    "hoodie",
    "cap",
    "hat",
    "mug",
    "poster",
    "ring",
    "necklace",
    "bracelet",
    "lamp",
    "chair",
    "table",
    "sofa",
    "wallet",
    "bag",
    "shoes",
    "sneakers",
    "watch",
    "bottle",
    "cream",
    "serum",
    "candle",
    "hooded",
    "cotton",
    "black",
    "white",
    "blue",
    "red",
    "basic",
    "standard",
}

UNIQUE_HINTS = {
    "handmade",
    "artisan",
    "artisanal",
    "limited",
    "limited edition",
    "custom",
    "personalized",
    "bespoke",
    "one of a kind",
    "small batch",
    "made to order",
    "exclusive",
    "signature",
    "atelier",
    "numbered",
    "crafted",
    "individually made",
    "custom finish",
}

CATEGORY_WORDS = {
    "ring",
    "shirt",
    "tshirt",
    "t-shirt",
    "hoodie",
    "lamp",
    "chair",
    "table",
    "bag",
    "wallet",
    "watch",
    "mug",
    "poster",
    "necklace",
    "bracelet",
    "cream",
    "serum",
    "candle",
    "bottle",
    "sofa",
    "sneakers",
    "shoes",
}

BRANDLIKE_PATTERNS = [
    r"\b[A-Z]{3,}\b",
    r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b",
]


@dataclass
class ExternalLookupResult:
    product_id: str | None
    product_name: str
    normalized_name: str
    lookup_status: str
    comparable_presence: str
    uniqueness_hint: str
    lookup_confidence: int
    market_summary: str
    recommended_next_step: str
    plan_required: str = "pro"
    locked_for_lite: bool = True
    external_match_score: int = 0
    category_confidence: int = 0
    uniqueness_score: int = 0
    comparability_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "normalized_name": self.normalized_name,
            "lookup_status": self.lookup_status,
            "comparable_presence": self.comparable_presence,
            "uniqueness_hint": self.uniqueness_hint,
            "lookup_confidence": self.lookup_confidence,
            "market_summary": self.market_summary,
            "recommended_next_step": self.recommended_next_step,
            "plan_required": self.plan_required,
            "locked_for_lite": self.locked_for_lite,
            "external_match_score": self.external_match_score,
            "category_confidence": self.category_confidence,
            "uniqueness_score": self.uniqueness_score,
            "comparability_score": self.comparability_score,
        }


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    value = re.sub(r"[^\w\s\-]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _tokenize(value: str) -> list[str]:
    tokens = re.split(r"[\s\-_]+", value.lower())
    return [token for token in tokens if token and token not in STOPWORDS]


def _extract_brandlike_score(name: str) -> int:
    score = 0
    for pattern in BRANDLIKE_PATTERNS:
        if re.search(pattern, name):
            score += 1
    return score


def _contains_hint(text: str, hints: set[str]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in hints)


def _detect_category(tokens: list[str]) -> tuple[str | None, int]:
    overlap = [token for token in tokens if token in CATEGORY_WORDS]
    if not overlap:
        return None, 20
    # prende il primo match come categoria principale
    return overlap[0], min(95, 45 + len(overlap) * 15)


def _size_color_material_score(name: str) -> int:
    lowered = name.lower()
    score = 0

    if re.search(r"\b(xs|s|m|l|xl|xxl)\b", lowered):
        score += 2

    if re.search(r"\b(black|white|red|blue|green|grey|gray|beige|navy)\b", lowered):
        score += 2

    if re.search(r"\b(cotton|leather|steel|gold|silver|wood|ceramic|linen)\b", lowered):
        score += 2

    if re.search(r"\b\d+(ml|cm|mm|kg|g|oz|l)\b", lowered):
        score += 2

    return score


def _generic_score(name: str) -> int:
    lowered = name.lower()
    score = 0

    for hint in GENERIC_HINTS:
        if hint in lowered:
            score += 2

    tokens = _tokenize(name)
    if len(tokens) <= 2:
        score += 2
    elif len(tokens) <= 4:
        score += 1

    score += _size_color_material_score(name)

    return score


def _unique_score(name: str, description: str | None = None) -> int:
    combined = f"{name} {description or ''}".strip().lower()
    score = 0

    for hint in UNIQUE_HINTS:
        if hint in combined:
            score += 3

    brandlike = _extract_brandlike_score(name)
    score += brandlike * 2

    tokens = _tokenize(name)
    long_tokens = [t for t in tokens if len(t) >= 7]
    if len(long_tokens) >= 2:
        score += 2

    if len(tokens) >= 5:
        score += 1

    # se compaiono taglie/colori/materiali standard, si abbassa un po' il senso di unicità
    score -= max(0, _size_color_material_score(name) - 2)

    return max(0, score)


def _token_overlap_ratio(tokens_a: list[str], tokens_b: list[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def _build_internal_market_candidates(category: str | None) -> list[str]:
    generic_pool = {
        "ring": [
            "silver artisan ring",
            "minimal stone ring",
            "black lava ring",
            "handmade gemstone ring",
        ],
        "shirt": [
            "black cotton t-shirt",
            "premium basic t-shirt",
            "oversized cotton shirt",
            "unisex black tee",
        ],
        "t-shirt": [
            "black cotton t-shirt",
            "premium basic t-shirt",
            "oversized cotton shirt",
            "unisex black tee",
        ],
        "hoodie": [
            "basic cotton hoodie",
            "oversized streetwear hoodie",
            "minimal logo hoodie",
        ],
        "lamp": [
            "ceramic designer lamp",
            "minimal bloom lamp",
            "artisan table lamp",
            "modern ceramic light",
        ],
        "bag": [
            "leather crossbody bag",
            "minimal tote bag",
            "artisan shoulder bag",
        ],
    }

    if category and category in generic_pool:
        return generic_pool[category]

    return [
        "premium artisan object",
        "minimal home decor item",
        "classic apparel basic",
        "signature crafted piece",
    ]


def _estimate_external_match_score(
    normalized_name: str,
    tokens: list[str],
    category: str | None,
    generic_score: int,
    unique_score: int,
) -> int:
    candidates = _build_internal_market_candidates(category)
    best_overlap = 0.0

    for candidate in candidates:
        overlap = _token_overlap_ratio(tokens, _tokenize(candidate))
        if overlap > best_overlap:
            best_overlap = overlap

    base = int(best_overlap * 100)

    if category:
        base += 10

    base += min(20, generic_score * 2)
    base -= min(18, unique_score * 2)

    return max(5, min(95, base))


def infer_external_lookup(
    product_id: str | None,
    product_name: str | None,
    description: str | None = None,
) -> dict[str, Any]:
    clean_name = _normalize_text(product_name or "")
    if not clean_name:
        clean_name = product_id or "Unknown Product"

    normalized_name = clean_name.lower()
    tokens = _tokenize(clean_name)

    category, category_confidence = _detect_category(tokens)
    comparability_score = _generic_score(clean_name)
    uniqueness_score = _unique_score(clean_name, description)
    external_match_score = _estimate_external_match_score(
        normalized_name=normalized_name,
        tokens=tokens,
        category=category,
        generic_score=comparability_score,
        unique_score=uniqueness_score,
    )

    score_gap = uniqueness_score - comparability_score

    if uniqueness_score >= comparability_score + 4 and external_match_score <= 45:
        uniqueness_hint = "LIKELY_UNIQUE"
        comparable_presence = "NOT_FOUND_YET"
        lookup_status = "INFERRED_INTERNAL"
        lookup_confidence = min(
            94,
            60 + uniqueness_score * 3 + max(0, 12 - external_match_score // 4),
        )
        recommended_next_step = "CHECK_EXTERNAL_MATCHES_AND_STORYTELLING"
        market_summary = (
            "Naming, wording, and match heuristics suggest differentiated positioning. "
            "The product appears less easily comparable than standard catalog items, "
            "so uniqueness storytelling and premium framing are likely stronger levers "
            "than immediate price competition."
        )

    elif comparability_score >= uniqueness_score + 3 or external_match_score >= 65:
        uniqueness_hint = "LIKELY_COMPARABLE"
        comparable_presence = "LIKELY_FOUND_EXTERNALLY"
        lookup_status = "INFERRED_INTERNAL"
        lookup_confidence = min(
            93,
            58 + comparability_score * 3 + external_match_score // 3,
        )
        recommended_next_step = "COMPARE_PRICE_AND_POSITIONING"
        market_summary = (
            "The product looks category-standard or easily matchable. "
            "Comparable offers are likely to exist elsewhere, so pricing strategy, "
            "bundling, urgency, and differentiation copy should be evaluated before "
            "assuming product uniqueness."
        )

    else:
        uniqueness_hint = "UNCLEAR"
        comparable_presence = "REQUIRES_EXTERNAL_CHECK"
        lookup_status = "NEEDS_EXTERNAL_VALIDATION"
        lookup_confidence = max(
            52,
            min(
                78,
                56 + abs(score_gap) + (category_confidence // 10),
            ),
        )
        recommended_next_step = "RUN_EXTERNAL_SEARCH"
        market_summary = (
            "Signals are mixed. The product shows some traits of a differentiated item "
            "but also enough catalog similarity to justify a real external comparison "
            "step before deciding between premium positioning and price competition."
        )

    return ExternalLookupResult(
        product_id=product_id,
        product_name=clean_name,
        normalized_name=normalized_name,
        lookup_status=lookup_status,
        comparable_presence=comparable_presence,
        uniqueness_hint=uniqueness_hint,
        lookup_confidence=int(lookup_confidence),
        market_summary=market_summary,
        recommended_next_step=recommended_next_step,
        external_match_score=int(external_match_score),
        category_confidence=int(category_confidence),
        uniqueness_score=int(uniqueness_score),
        comparability_score=int(comparability_score),
    ).to_dict()


def infer_many(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for product in products:
        results.append(
            infer_external_lookup(
                product_id=product.get("product_id"),
                product_name=product.get("product_name") or product.get("name"),
                description=product.get("description"),
            )
        )
    return results
