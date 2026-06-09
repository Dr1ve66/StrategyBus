# -*- coding: utf-8 -*-
import csv
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capability_rules import capabilities_from_text, is_hiring_context
from product_metadata import (
    extract_step_archetypes,
    extract_step_capabilities,
    get_product_profile,
    score_product_by_metadata,
)


def load_products():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "products.txt")
    products = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            name = (row.get("Название продукта") or "").strip()
            if not name:
                continue
            products.append({
                "name": name,
                "description": (row.get("Что делает продукт/сервис") or "").strip(),
                "problems": (row.get("Какие проблемы помогает решить") or "").strip(),
            })
    return products


def step_intent(step):
    text = " ".join(
        step.get(key, "") or ""
        for key in ("title", "description", "logic", "criteria")
    ).lower().replace("ё", "е")
    return {"text": text, "actions": set(), "domains": set()}


class ProductMatchingTests(unittest.TestCase):
    def setUp(self):
        self.products = {item["name"]: item for item in load_products()}

    def test_deposit_step_detects_treasury_capabilities(self):
        text = "определить потенциальные банки для депозитов сравнить условия размещения свободных средств"
        caps = capabilities_from_text(text)
        self.assertIn("treasury", caps)
        self.assertIn("deposits", caps)
        self.assertIn("business_financing", caps)

    def test_bank_selection_is_not_hiring(self):
        self.assertFalse(is_hiring_context("провести подбор потенциальных банков для размещения депозитов"))
        self.assertTrue(is_hiring_context("провести подбор сотрудников на склад"))

    def test_deposit_step_prefers_deposits_over_benefitty(self):
        step = {
            "title": "Определить потенциальные банки для депозитов",
            "description": "Сравнить условия размещения свободных средств",
        }
        intent = step_intent(step)
        self.assertTrue(extract_step_capabilities(intent))
        self.assertIn("treasury", extract_step_archetypes(intent))

        deposits = self.products["Депозиты"]
        benefitty = self.products["BenefittY – платформа управления программой лояльности"]

        deposits_score = score_product_by_metadata(step, deposits, intent, legacy_tags=set())
        benefitty_score = score_product_by_metadata(step, benefitty, intent, legacy_tags=set())

        self.assertGreater(deposits_score, 0)
        self.assertEqual(benefitty_score, 0)
        self.assertGreater(deposits_score, benefitty_score)

    def test_loyalty_step_still_matches_benefitty(self):
        step = {
            "title": "Изучить программы лояльности для клиентов",
            "description": "Сравнить бонусные механики удержания покупателей",
        }
        intent = step_intent(step)
        benefitty = self.products["BenefittY – платформа управления программой лояльности"]
        score = score_product_by_metadata(step, benefitty, intent, legacy_tags=set())
        self.assertGreater(score, 0)

    def test_benefitty_profile_does_not_get_false_hr_tag_from_personalization(self):
        benefitty = self.products["BenefittY – платформа управления программой лояльности"]
        profile = get_product_profile(benefitty, legacy_tags=set())
        self.assertNotIn("hr_operations", profile["capabilities"])


class ProductVariantETests(unittest.TestCase):
    """Variant E: LLM assigns product in `product` field; system validates, never force-injects."""

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app import detect_explicit_step_products, ensure_agent2_product_usage_local
        self.detect_explicit_step_products = detect_explicit_step_products
        self.ensure_agent2_product_usage_local = ensure_agent2_product_usage_local
        self.products = load_products()

    def test_product_field_returns_exact_match(self):
        step = {
            "title": "Разработать новые комбинации товаров",
            "description": "Анализ и создание новых товарных наборов",
            "product": "BenefittY – платформа управления программой лояльности",
        }
        matched = self.detect_explicit_step_products(step, self.products)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0], "BenefittY – платформа управления программой лояльности")

    def test_product_field_invalid_name_skips(self):
        step = {
            "title": "Разработать новые комбинации товаров",
            "description": "...",
            "product": "Несуществующий продукт банка",
        }
        matched = self.detect_explicit_step_products(step, self.products)
        self.assertEqual(matched, [])

    def test_product_field_empty_string_falls_through(self):
        step = {
            "title": "Разработать новые комбинации товаров",
            "description": "Использовать продукт: BenefittY – платформа управления программой лояльности",
            "product": "",
        }
        matched = self.detect_explicit_step_products(step, self.products)
        # Falls through to text search — BenefittY name appears in description
        self.assertTrue(any("BenefittY" in m for m in matched))

    def test_ensure_agent2_product_usage_validates_not_forces(self):
        step = {
            "title": "Найти новых поставщиков",
            "description": "Анализ рынка поставщиков сырья",
            "logic": "Сравнить цены у разных поставщиков",
            "criteria": "Выручка > 10 млн",
            "product": "BenefittY – платформа управления программой лояльности",
        }
        result = self.ensure_agent2_product_usage_local([step], self.products, expected_count=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["product"], "BenefittY – платформа управления программой лояльности")

    def test_ensure_agent2_product_usage_semantic_fallback_strict_threshold(self):
        """Step with no product field and truly irrelevant text — no force-injection."""
        # Use text that produces exactly one action (implement) and no domain overlaps.
        # implement → any HR product scores 12, which is < MIN_PRODUCT_MATCH_SCORE (14).
        step = {
            "title": "XXX",
            "description": "XXX",
            "logic": "XXX",
            "criteria": "XXX",
            "product": "",
        }
        result = self.ensure_agent2_product_usage_local([step], self.products, expected_count=1)
        # No product should be force-injected — semantic fallback with min_score=14 rejects it.
        self.assertEqual(result[0].get("product", ""), "")

    def test_ensure_agent2_product_usage_respects_llm_choice(self):
        """LLM correctly assigned HR-платформа to a hiring step — system must keep it."""
        step = {
            "title": "Разместить вакансии на hh.ru",
            "description": "Поиск сотрудников через платформы",
            "logic": "Опубликовать вакансии",
            "criteria": "Количество сотрудников > 5",
            "product": "HR-платформа Saby",
        }
        result = self.ensure_agent2_product_usage_local([step], self.products, expected_count=1)
        self.assertEqual(result[0]["product"], "HR-платформа Saby")


if __name__ == "__main__":
    unittest.main()
