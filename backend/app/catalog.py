import re


_PRICED_VARIANT = re.compile(
    r"(?<!\S)(Букет\s+из\s+\d+\b.*?)[ \t]+[-—][ \t]+(\d{3,6})(?=\s|$)",
    re.IGNORECASE,
)


def extract_product_variants(description: str) -> list[dict[str, str | int]]:
    matches = list(_PRICED_VARIANT.finditer(description))
    if len(matches) < 2:
        return []

    return [
        {
            "id": str(index),
            "label": re.sub(r"\s+", " ", match.group(1)).strip(),
            "price_rub": int(match.group(2)),
        }
        for index, match in enumerate(matches, start=1)
    ]


def resolve_variant_price(
    description: str,
    base_price_rub: int,
    variant_id: str | None,
) -> tuple[int, str | None]:
    variants = extract_product_variants(description)
    if not variants:
        if variant_id is not None:
            raise ValueError("Вариант товара недоступен")
        return base_price_rub, None

    selected = next((variant for variant in variants if variant["id"] == variant_id), None)
    if selected is None:
        raise ValueError("Выберите вариант букета")
    return int(selected["price_rub"]), str(selected["label"])


def clean_variant_descriptions(description: str) -> str:
    if len(list(_PRICED_VARIANT.finditer(description))) < 2:
        return description
    return _PRICED_VARIANT.sub(" ", description).strip()
