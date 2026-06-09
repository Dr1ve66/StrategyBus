# -*- coding: utf-8 -*-
"""
Comprehensive tests for the strategy-readiness engine.

Corpus covers:
  - Tier A: clear single-problem phrases (must proceed without clarify)
  - Tier B focus: structurally multiple problems
  - Tier B problem: genuinely vague input
  - Tier C: guards (empty, spam, too long)
  - Regression: context/goal must never block
  - Inference: domain and goal derived silently
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_readiness import (
    CLARIFY_FOCUS,
    CLARIFY_PROBLEM,
    READINESS_TIER_CLARIFY,
    READINESS_TIER_PROCEED,
    READINESS_TIER_REJECT,
    assess_strategy_readiness,
    build_normalized_text,
    build_slots_from_text,
    explicit_multi_problem_count,
    infer_domain,
    infer_goal_from_problem,
    is_bank_centric,
    is_manager_task,
    is_vague_request,
    match_archetype,
    problem_is_actionable,
    should_request_focus,
)
from situation_validation import call_situation_check, normalize_situation_slot_check


# --- Tier A corpus: must pass without user clarification ---
TIER_A_PHRASES = (
    ("у клиента отток клиентов", "customer_churn"),
    ("отток клиентов", "customer_churn"),
    ("текучка кадров", "staff_turnover"),
    ("падают продажи на маркетплейсе", "sales_decline"),
    ("жалобы клиентов на сервис", "customer_complaints"),
    ("кассовый разрыв", "cash_pressure"),
    ("нет эквайринга", "no_acquiring"),
    ("налоговая задолженность", "tax_arrears"),
    ("задержки поставок на склад", "supply_chain"),
    ("давление конкурентов на рынке", "competition"),
)


# --- Tier B focus: multiple independent problems ---
TIER_B_FOCUS_PHRASES = (
    "Падают продажи; высокая текучка кадров; нет эквайринга",
    "1. Падают продажи 2. Высокая текучка 3. Нет эквайринга",
)


# --- Tier C / vague: must NOT proceed silently ---
TIER_C_PHRASES = (
    "",
    "нужна помощь",
    "улучшить бизнес",
    "хочу кредит",
)


class StrategyReadinessTierTests(unittest.TestCase):
    def test_tier_a_corpus_proceeds_without_clarify(self):
        for phrase, archetype_id in TIER_A_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertTrue(
                    result["ok"],
                    msg=f"Expected proceed for {phrase!r}, got {result}",
                )
                self.assertEqual(result["tier"], READINESS_TIER_PROCEED)
                self.assertIsNone(result["clarify_mode"])
                self.assertEqual(result["missing"], [])
                self.assertTrue(result["normalized_text"])
                readiness = result.get("readiness") or {}
                self.assertEqual(readiness.get("focus"), "single")
                self.assertEqual(readiness.get("specificity"), "actionable")
                if archetype_id:
                    self.assertEqual(
                        (readiness.get("inference") or {}).get("archetype_id"),
                        archetype_id,
                    )

    def test_tier_b_focus_corpus(self):
        for phrase in TIER_B_FOCUS_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertFalse(result["ok"])
                self.assertEqual(result["tier"], READINESS_TIER_CLARIFY)
                self.assertEqual(result["clarify_mode"], "focus")
                self.assertIn(CLARIFY_FOCUS, result["missing"])

    def test_tier_c_vague_corpus_rejected(self):
        for phrase in TIER_C_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertFalse(result["ok"])
                self.assertIn(result["tier"], {READINESS_TIER_REJECT, READINESS_TIER_CLARIFY})

    def test_weak_conjunction_does_not_force_focus(self):
        """«продажи и текучка» в одном предложении — не повод для focus без явного разделения."""
        phrase = "падают продажи и растёт текучка кадров"
        self.assertLess(explicit_multi_problem_count(phrase), 2)
        enrichment = {
            "problem_count": 2,
            "detected_problems": ["падают продажи"],
            "slots": {},
        }
        self.assertFalse(should_request_focus(phrase, 2, ["падают продажи"]))


class StrategyReadinessInferenceTests(unittest.TestCase):
    def test_archetype_match_churn(self):
        archetype = match_archetype("у клиента отток клиентов")
        self.assertEqual(archetype["id"], "customer_churn")
        self.assertEqual(archetype["goal"], "остановить отток клиентов")

    def test_domain_inference_sales(self):
        self.assertIn("продаж", infer_domain("снижение выручки в рознице").lower())

    def test_goal_inference_from_symptom(self):
        self.assertIn("отток", infer_goal_from_problem("отток клиентов").lower())

    def test_slots_always_include_inferred_context_goal(self):
        slots = build_slots_from_text("отток клиентов")
        self.assertTrue((slots.get("context") or {}).get("value"))
        self.assertTrue((slots.get("goal") or {}).get("value"))
        self.assertEqual((slots.get("context") or {}).get("source"), "archetype")

    def test_normalized_text_contains_three_fields(self):
        slots = build_slots_from_text("текучка кадров")
        text = build_normalized_text(slots)
        self.assertIn("Проблема:", text)
        self.assertIn("Контекст:", text)
        self.assertIn("Цель:", text)


class StrategyReadinessLLMIntegrationTests(unittest.TestCase):
    def test_llm_context_goal_blockers_never_block(self):
        """LLM может ошибочно вернуть context/goal как blockers — система не спрашивает."""
        enrichment = {
            "agent1_ready": False,
            "problem_count": 1,
            "detected_problems": ["отток клиентов"],
            "missing_blockers": ["context", "goal"],
            "slots": {
                "problem": {"present": True, "value": "отток клиентов", "confidence": 0.95},
            },
        }
        result = assess_strategy_readiness("у клиента отток клиентов", enrichment)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["clarify_mode"])

    def test_llm_multi_problem_with_detected_list_triggers_focus(self):
        enrichment = {
            "problem_count": 3,
            "detected_problems": ["падают продажи", "текучка", "нет эквайринга"],
            "slots": {},
        }
        result = assess_strategy_readiness(
            "Падают продажи; текучка; нет эквайринга",
            enrichment,
        )
        self.assertEqual(result["clarify_mode"], "focus")

    def test_call_situation_check_without_llm_for_archetype(self):
        result = call_situation_check(
            "у клиента отток клиентов",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "readiness")

    def test_call_situation_check_vague_without_llm(self):
        result = call_situation_check("нужна помощь", lambda *_a, **_k: {})
        self.assertFalse(result["ok"])


class StrategyReadinessSpecificityTests(unittest.TestCase):
    def test_actionable_short_symptom(self):
        self.assertTrue(problem_is_actionable(build_slots_from_text("отток клиентов"), "отток клиентов"))

    def test_vague_request_detected(self):
        self.assertTrue(is_vague_request("улучшить бизнес"))
        self.assertFalse(is_vague_request("отток клиентов"))

    def test_ambiguous_text_triggers_problem_clarify(self):
        enrichment = {
            "problem_count": 1,
            "detected_problems": [],
            "slots": {"problem": {"present": False, "value": "", "confidence": 0}},
            "slot_options": {
                "problem": {
                    "message": "Что именно происходит?",
                    "options": ["Падают продажи", "Другое (уточню сам)"],
                }
            },
        }
        result = assess_strategy_readiness("ситуация сложная", enrichment)
        self.assertFalse(result["ok"])
        self.assertEqual(result["clarify_mode"], "slots")
        self.assertIn(CLARIFY_PROBLEM, result["missing"])


class StrategyReadinessBankCentricTests(unittest.TestCase):
    """Descriptions must be about client's business, not bank's relationship."""

    BANK_CENTRIC_PHRASES = (
        "клиент перестал пользоваться продуктом банка",
        "клиент перестал пользоваться услугами банка",
        "клиент уходит из банка",
        "клиенты уходят из банка",
        "отток клиентов из банка",
        "перестал пользоваться банком",
        "клиент не платит по кредиту",
        "клиент перестал платить по кредиту",
        "уход клиента в другой банк",
        "клиент не пользуется услугами банка",
    )

    def test_bank_centric_phrases_are_detected(self):
        for phrase in self.BANK_CENTRIC_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    is_bank_centric(phrase),
                    msg=f"Expected bank-centric for {phrase!r}",
                )

    def test_bank_centric_phrases_are_rejected(self):
        for phrase in self.BANK_CENTRIC_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertFalse(result["ok"])
                self.assertEqual(result["tier"], READINESS_TIER_REJECT)

    def test_client_business_phrases_are_not_bank_centric(self):
        client_phrases = (
            "отток клиентов",                          # клиент теряет СВОИХ покупателей
            "падают продажи на маркетплейсе",
            "текучка кадров",
            "жалобы клиентов на сервис",
            "кассовый разрыв",
            "налоговая задолженность",
            "нет эквайринга",
            "снижение выручки в рознице",
            "клиент теряет своих покупателей",         # явно про бизнес клиента
        )
        for phrase in client_phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(is_bank_centric(phrase))

    def test_churn_with_prefix_still_client_side(self):
        """«у клиента отток клиентов» — клиент теряет СВОИХ, не банковских."""
        self.assertFalse(is_bank_centric("у клиента отток клиентов"))
        result = assess_strategy_readiness("у клиента отток клиентов")
        self.assertTrue(result["ok"])

    def test_churn_clearly_bank_side_is_rejected(self):
        result = assess_strategy_readiness("отток клиентов из банка")
        self.assertFalse(result["ok"])
        self.assertIn("банк", result["reason"].lower())


class ManagerTaskGuardTests(unittest.TestCase):
    """Situations that describe the bank manager's task — must be rejected."""

    MANAGER_TASK_PHRASES = (
        "У меня есть список кому рассчитан предодобренный кредит, мне нужно составить их портрет",
        "Мне нужно составить аналитику по портфелю клиентов",
        "Мне нужно подготовить КП для клиента",
        "Нужно составить портрет клиентов малого бизнеса",
        "Нужно выполнить план по кредитованию",
        "Нужно подготовить коммерческое предложение по РКО",
        "Нужна стратегия для работы с сегментом малого бизнеса",
        "Хочу предложить клиентам из портфеля факторинг",
        "Нужно найти клиентов для зарплатного проекта",
    )

    CLIENT_BUSINESS_PHRASES = (
        "У клиента падают продажи из-за конкурентов",
        "Клиент испытывает кассовый разрыв",
        "Клиент хочет выйти на маркетплейсы",
        "Клиент хочет масштабироваться — открыть ещё 3 точки",
        "Клиент ищет инвестора для расширения производства",
        "У клиента высокая налоговая нагрузка",
        "Клиент теряет сотрудников — конкуренты платят больше",
        "Клиент хочет поговорить о возможностях",
        "Нужно найти новых клиентов для нашего нового продукта",  # "наш" = клиентский бизнес
    )

    def test_manager_task_phrases_detected(self):
        for phrase in self.MANAGER_TASK_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    is_manager_task(phrase),
                    msg=f"Expected manager-task for {phrase!r}",
                )

    def test_manager_task_phrases_rejected(self):
        for phrase in self.MANAGER_TASK_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertFalse(result["ok"], msg=f"Should be rejected: {phrase!r}")
                self.assertEqual(result["tier"], READINESS_TIER_REJECT)

    def test_client_business_phrases_not_manager_task(self):
        for phrase in self.CLIENT_BUSINESS_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(
                    is_manager_task(phrase),
                    msg=f"False positive: {phrase!r} detected as manager task",
                )


class BankCentricExtendedTests(unittest.TestCase):
    """Bank retention / reactivation patterns added in the second pass."""

    BANK_RETENTION_PHRASES = (
        "Клиент давно не пользуется расчётным счётом, нужно его реактивировать",
        "У клиента истекает депозит через месяц, нужно его удержать",
        "Нужно удержать вклад клиента",
        "Почему клиенты не берут кредиты в нашем банке",
        "Истекает депозит клиента",
    )

    def test_bank_retention_detected(self):
        for phrase in self.BANK_RETENTION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    is_bank_centric(phrase),
                    msg=f"Expected bank-centric for {phrase!r}",
                )

    def test_bank_retention_rejected(self):
        for phrase in self.BANK_RETENTION_PHRASES:
            with self.subTest(phrase=phrase):
                result = assess_strategy_readiness(phrase)
                self.assertFalse(result["ok"], msg=f"Should be rejected: {phrase!r}")
                self.assertEqual(result["tier"], READINESS_TIER_REJECT)


class StrategyReadinessBackwardCompatTests(unittest.TestCase):
    def test_normalize_situation_slot_check_short_hr(self):
        data = {
            "agent1_ready": True,
            "problem_count": 1,
            "detected_problems": ["текучка кадров"],
            "missing_blockers": [],
            "slots": {
                "problem": {"present": True, "value": "текучка кадров", "confidence": 0.95},
            },
            "normalized_text": "В компании высокая текучка кадров.",
        }
        result = normalize_situation_slot_check(data, original_text="текучка кадров")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["clarify_mode"])


if __name__ == "__main__":
    unittest.main()
