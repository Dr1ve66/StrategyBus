# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from situation_validation import (
    build_situation_from_focus,
    call_situation_check,
    count_problems_heuristic,
    normalize_situation_slot_check,
    run_basic_situation_guards,
    unpack_clarify_payload,
)


class SituationValidationTests(unittest.TestCase):
    def test_short_hr_phrase_sufficient(self):
        data = {
            "agent1_ready": True,
            "problem_count": 1,
            "detected_problems": ["текучка кадров"],
            "score": 82,
            "missing_blockers": [],
            "slots": {
                "problem": {"present": True, "value": "текучка кадров", "confidence": 0.95},
                "context": {"present": True, "value": "HR", "confidence": 0.9},
                "goal": {"present": True, "value": "снизить текучку", "confidence": 0.85},
            },
            "reason": "достаточно",
            "normalized_text": "В компании высокая текучка кадров (HR). Цель — снизить текучку.",
        }
        result = normalize_situation_slot_check(data)
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["clarify_mode"], None)

    def test_multiple_problems_trigger_focus(self):
        data = {
            "agent1_ready": False,
            "problem_count": 3,
            "detected_problems": [
                "падают продажи",
                "высокая текучка",
                "нет эквайринга",
            ],
            "score": 40,
            "missing_blockers": [],
            "slots": {
                "problem": {"present": True, "value": "несколько проблем", "confidence": 0.7},
            },
            "reason": "много проблем",
        }
        result = normalize_situation_slot_check(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["clarify_mode"], "focus")
        self.assertIn("focus", result["missing"])

    def test_only_true_blocker_asks_one_slot(self):
        data = {
            "agent1_ready": False,
            "problem_count": 1,
            "detected_problems": ["что-то не так"],
            "score": 45,
            "missing_blockers": ["problem"],
            "slots": {
                "context": {"present": True, "value": "продажи", "confidence": 0.9},
                "goal": {"present": True, "value": "стабилизировать", "confidence": 0.85},
            },
            "slot_options": {
                "problem": {
                    "message": "Уточните проблему",
                    "options": ["Падают продажи", "Другое (уточню сам)"],
                }
            },
        }
        result = normalize_situation_slot_check(data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["clarify_mode"], "slots")
        self.assertEqual(result["missing"], ["problem"])

    def test_hard_length_guard(self):
        text = "проблема " * 500
        guard = run_basic_situation_guards(text)
        self.assertIsNotNone(guard)
        self.assertFalse(guard["ok"])
        self.assertIn("длинн", guard["reason"].lower())

    def test_heuristic_problem_count(self):
        text = "Падают продажи; высокая текучка кадров; нет эквайринга"
        self.assertGreaterEqual(count_problems_heuristic(text), 2)

    def test_build_focus_situation(self):
        built = build_situation_from_focus(
            "Падают продажи и текучка",
            "Падают продажи",
            {"context": {"value": "розница"}, "goal": {"value": "стабилизировать выручку"}},
        )
        self.assertIn("Главная проблема: Падают продажи", built)
        self.assertIn("розница", built)

    def test_unpack_focus_payload(self):
        payload = {
            "_mode": "focus",
            "_message": "Выберите проблему",
            "_options": ["A", "B"],
        }
        state = unpack_clarify_payload(payload)
        self.assertEqual(state["mode"], "focus")
        self.assertEqual(len(state["focus_question"]["options"]), 2)

    def test_call_without_llm_uses_guard(self):
        result = call_situation_check("", lambda *_args, **_kwargs: {})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
