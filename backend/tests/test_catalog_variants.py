import unittest
from decimal import Decimal

from app.catalog import (
    clean_variant_descriptions,
    extract_product_variants,
    resolve_variant_price,
)
from app.inventory import is_low_stock, weighted_average_cost


class ProductVariantTests(unittest.TestCase):
    def test_extracts_only_explicit_bouquet_price_options(self):
        description = (
            "Букет из 5 роз в упаковке - 2150 "
            "Букет из 7 роз в упаковке - 2870 "
            "Упаковка +550"
        )

        self.assertEqual(
            extract_product_variants(description),
            [
                {"id": "1", "label": "Букет из 5 роз в упаковке", "price_rub": 2150},
                {"id": "2", "label": "Букет из 7 роз в упаковке", "price_rub": 2870},
            ],
        )

    def test_requires_multiple_explicit_options(self):
        self.assertEqual(extract_product_variants("Букет из 5 роз - 2150"), [])
        self.assertEqual(extract_product_variants("Упаковка +550"), [])

    def test_resolves_order_price_from_catalog_not_base_price(self):
        description = "Букет из 5 роз - 2150 Букет из 7 роз - 2870"

        self.assertEqual(
            resolve_variant_price(description, 350, "2"),
            (2870, "Букет из 7 роз"),
        )

    def test_rejects_missing_or_unknown_variant(self):
        description = "Букет из 5 роз - 2150 Букет из 7 роз - 2870"

        with self.assertRaisesRegex(ValueError, "Выберите вариант"):
            resolve_variant_price(description, 350, None)
        with self.assertRaisesRegex(ValueError, "Выберите вариант"):
            resolve_variant_price(description, 350, "999")

    def test_removes_variant_prices_but_keeps_other_description(self):
        description = (
            "Букет из 5 роз - 2150 Букет из 7 роз - 2870 "
            "К букету можно добавить открытку"
        )

        self.assertEqual(
            clean_variant_descriptions(description),
            "К букету можно добавить открытку",
        )

    def test_weighted_average_recalculates_stock_cost(self):
        self.assertEqual(
            weighted_average_cost(
                Decimal("10"),
                Decimal("100"),
                Decimal("5"),
                Decimal("160"),
            ),
            Decimal("120.00"),
        )

    def test_weighted_average_rounds_to_kopecks(self):
        self.assertEqual(
            weighted_average_cost(
                Decimal("1"),
                Decimal("10"),
                Decimal("2"),
                Decimal("11"),
            ),
            Decimal("10.67"),
        )

    def test_zero_minimum_disables_low_stock_warning(self):
        self.assertFalse(
            is_low_stock(Decimal("0"), Decimal("0"), Decimal("0"))
        )

    def test_low_stock_uses_available_not_physical_quantity(self):
        self.assertTrue(
            is_low_stock(Decimal("10"), Decimal("8"), Decimal("3"))
        )
        self.assertFalse(
            is_low_stock(Decimal("10"), Decimal("2"), Decimal("3"))
        )


if __name__ == "__main__":
    unittest.main()
