# -*- coding: utf-8 -*-
"""
Situation validation facade.

The decision layer lives in strategy_readiness.py (code is the judge).
LLM output is enrichment only — never the sole gate for context/goal questions.
"""
import json
import re

from strategy_readiness import (
    SITUATION_HARD_LENGTH,
    SITUATION_SLOT_LABELS,
    SITUATION_SOFT_LENGTH,
    assess_strategy_readiness,
    run_guards,
    sanitize_situation_for_check,
)

SITUATION_REQUIRED_SLOTS = ("problem", "context", "goal")
SITUATION_OPTIONAL_SLOTS = ("scale", "cause", "constraints")
SITUATION_BLOCKING_SLOTS = ("problem",)
SITUATION_INFERABLE_SLOTS = ("context", "goal")

PROMPT_SITUATION_SLOT_FILL = """
Ты — бизнес-аналитик. Извлеки структуру из описания ситуации для построения ОДНОЙ стратегии.

Твоя роль — ОБОГАЩЕНИЕ, не блокировка. Финальное решение принимает система.

Извлеки:
- problem — симптом или проблема;
- context — область бизнеса (выведи сам);
- goal — желаемый результат (выведи сам, часто обратное problem);
- problem_count — число НЕЗАВИСИМЫХ проблем (не симптомов одной);
- detected_problems — краткие формулировки каждой отдельной проблемы.

Правила извлечения:
1. Короткие фразы («отток клиентов», «текучка кадров») — problem_count=1, заполни все слоты.
2. missing_blockers всегда [] — система сама решает, нужно ли уточнение.
3. agent1_ready — твоя оценка, но система может переопределить.
4. normalized_text — связное описание (problem + context + goal), без выдуманных фактов.
5. slot_options — только если problem_count=1, но problem неясен (общие фразы типа «нужна помощь»).

Верни строго JSON:
{
  "agent1_ready": true или false,
  "problem_count": число,
  "detected_problems": ["...", "..."],
  "score": число от 0 до 100,
  "missing_blockers": [],
  "slots": {
    "problem": {"present": true, "value": "...", "confidence": 0.9},
    "context": {"present": true, "value": "...", "confidence": 0.85},
    "goal": {"present": true, "value": "...", "confidence": 0.8}
  },
  "reason": "коротко",
  "normalized_text": "...",
  "slot_options": {
    "problem": {"message": "...", "options": ["...", "Другое (уточню сам)"]}
  }
}
"""


def to_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    value_str = str(value).strip().lower()
    return value_str in {"1", "true", "yes", "y", "да"}


def format_missing_slots_message(missing):
    labels = [SITUATION_SLOT_LABELS.get(key, key) for key in missing]
    return "Для полного понимания проблемы не хватает: " + ", ".join(labels) + "."


def slot_confidence(slot):
    if not isinstance(slot, dict):
        return 0.0
    try:
        return float(slot.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.8 if slot.get("present") else 0.0


def parse_situation_slots(slots_data):
    if not isinstance(slots_data, dict):
        return {}
    parsed = {}
    for key in SITUATION_REQUIRED_SLOTS + SITUATION_OPTIONAL_SLOTS:
        slot = slots_data.get(key) or {}
        if not isinstance(slot, dict):
            slot = {"present": bool(str(slot).strip()), "value": str(slot).strip()}
        value = str(slot.get("value") or "").strip()
        present = to_bool(slot.get("present")) or bool(value)
        parsed[key] = {
            "present": present,
            "value": value,
            "confidence": slot_confidence(slot) if present else 0.0,
        }
    return parsed


def normalize_slot_questions(slot_options, missing):
    normalized = {}
    for key in missing:
        payload = (slot_options or {}).get(key) or {}
        options = payload.get("options") or []
        if isinstance(options, str):
            options = [item.strip() for item in re.split(r"[,;\n]", options) if item.strip()]
        options = [str(item).strip() for item in options if str(item).strip()]
        if not options:
            options = [
                "Снизить негативное влияние на бизнес",
                "Стабилизировать ситуацию в ближайшие 1–3 месяца",
                "Другое (уточню сам)",
            ]
        elif not any("другое" in item.lower() for item in options):
            options.append("Другое (уточню сам)")
        message = str(payload.get("message") or "").strip()
        if not message:
            message = f"Уточните конкретную проблему: {SITUATION_SLOT_LABELS.get(key, key)}."
        normalized[key] = {"message": message, "options": options[:5]}
    return normalized


def normalize_focus_question(detected_problems, data=None):
    data = data or {}
    options = data.get("options") or detected_problems or []
    if isinstance(options, str):
        options = [item.strip() for item in re.split(r"[,;\n]", options) if item.strip()]
    options = [str(item).strip() for item in options if str(item).strip()]
    options = list(dict.fromkeys(options))
    if len(options) < 2 and detected_problems:
        options = list(dict.fromkeys(detected_problems))
    if not any("другое" in item.lower() for item in options):
        options.append("Другое (уточню сам)")
    message = str(data.get("message") or "").strip()
    if not message:
        message = (
            "В описании несколько разных проблем. Мы предлагаем одну стратегию — "
            "выберите, что решаем в первую очередь."
        )
    return {"message": message, "options": options[:6]}


def pack_clarify_payload(mode, slot_questions=None, focus_question=None):
    payload = {"_mode": mode}
    if mode == "focus" and focus_question:
        payload["_message"] = focus_question.get("message", "")
        payload["_options"] = focus_question.get("options", [])
    elif mode == "slots" and slot_questions:
        payload.update(slot_questions)
    return payload


def unpack_clarify_payload(raw):
    if not isinstance(raw, dict):
        return {"mode": None, "slot_questions": {}, "focus_question": None}
    mode = raw.get("_mode")
    if mode == "focus":
        return {
            "mode": "focus",
            "slot_questions": {},
            "focus_question": {
                "message": raw.get("_message", ""),
                "options": raw.get("_options") or [],
            },
        }
    if mode == "slots" or any(key in raw for key in SITUATION_REQUIRED_SLOTS):
        slot_questions = {
            key: value
            for key, value in raw.items()
            if not str(key).startswith("_")
        }
        return {"mode": "slots", "slot_questions": slot_questions, "focus_question": None}
    return {"mode": None, "slot_questions": {}, "focus_question": None}


def build_situation_from_slots(original, slot_answers, extracted_slots=None):
    extracted_slots = extracted_slots or {}
    lines = [sanitize_situation_for_check(original)]
    for key in SITUATION_REQUIRED_SLOTS + SITUATION_OPTIONAL_SLOTS:
        answer = (slot_answers.get(key) or "").strip()
        if answer == "Другое (уточню сам)":
            answer = (slot_answers.get(f"{key}_custom") or "").strip()
        if not answer:
            answer = str((extracted_slots.get(key) or {}).get("value") or "").strip()
        if answer:
            lines.append(f"{SITUATION_SLOT_LABELS[key]}: {answer}")
    return "\n".join(line for line in lines if line).strip()


def build_situation_from_focus(original, primary_problem, extracted_slots=None):
    extracted_slots = extracted_slots or {}
    primary = (primary_problem or "").strip()
    if primary == "Другое (уточню сам)":
        primary = ""
    lines = []
    if primary:
        lines.append(f"Главная проблема: {primary}")
    for key in ("context", "goal"):
        value = str((extracted_slots.get(key) or {}).get("value") or "").strip()
        if value:
            lines.append(f"{SITUATION_SLOT_LABELS[key]}: {value}")
    original_clean = sanitize_situation_for_check(original)
    if original_clean and (not primary or primary not in original_clean):
        lines.append(f"Контекст: {original_clean}")
    return "\n".join(lines).strip()


def load_situation_clarify_state(situation_check):
    if not situation_check or not getattr(situation_check, "slot_questions", None):
        return {"mode": None, "slot_questions": {}, "focus_question": None}
    try:
        data = json.loads(situation_check.slot_questions)
        return unpack_clarify_payload(data if isinstance(data, dict) else {})
    except Exception:
        return {"mode": None, "slot_questions": {}, "focus_question": None}


def load_situation_slot_questions(situation_check):
    return load_situation_clarify_state(situation_check).get("slot_questions") or {}


def load_situation_focus_question(situation_check):
    return load_situation_clarify_state(situation_check).get("focus_question")


def load_situation_slots_filled(situation_check):
    if not situation_check or not getattr(situation_check, "slots_filled", None):
        return {}
    try:
        data = json.loads(situation_check.slots_filled)
        return parse_situation_slots(data)
    except Exception:
        return {}


def run_basic_situation_guards(text):
    return run_guards(text)


def finalize_check_result(result):
    payload = result.get("clarify_payload") or {}
    if not payload and result.get("slot_questions"):
        payload = result["slot_questions"]
    result["clarify_payload"] = payload
    result["slot_questions"] = payload
    return result


def call_situation_check(text, call_openai_raw):
    cleaned = sanitize_situation_for_check(text)

    rules_result = assess_strategy_readiness(cleaned)
    if rules_result.get("ok"):
        return finalize_check_result({**rules_result, "source": "readiness"})

    guard = run_guards(cleaned)
    if guard and guard.get("tier") == "C":
        return finalize_check_result(guard)

    enrichment = call_openai_raw(
        PROMPT_SITUATION_SLOT_FILL,
        json.dumps(
            {
                "situation_description": cleaned,
                "note": "Верни только извлечённые данные. missing_blockers всегда [].",
            },
            ensure_ascii=False,
        ),
    )
    enrichment["source"] = "llm+readiness"
    result = assess_strategy_readiness(cleaned, enrichment)
    return finalize_check_result(result)


# Backward-compatible exports used in tests
def normalize_situation_slot_check(data, source="llm", original_text=""):
    enrichment = dict(data)
    enrichment["source"] = source
    text = (original_text or "").strip()
    if not text:
        text = str(enrichment.get("situation_description") or "").strip()
    if not text:
        detected = enrichment.get("detected_problems") or []
        if detected:
            text = "; ".join(str(item).strip() for item in detected if str(item).strip())
    if not text:
        problem_value = str((enrichment.get("slots") or {}).get("problem", {}).get("value") or "").strip()
        text = problem_value
    return assess_strategy_readiness(text, enrichment)


def infer_slots_from_text(text):
    from strategy_readiness import match_archetype

    archetype = match_archetype(text)
    if not archetype:
        return None
    return {
        "problem": archetype["problem"],
        "context": archetype["context"],
        "goal": archetype["goal"],
    }


def try_rules_based_sufficiency(text):
    result = assess_strategy_readiness(text)
    if result.get("ok"):
        return result
    return None


def resolve_missing_blockers(slots, llm_blockers, agent1_ready=False):
    if agent1_ready:
        return []
    return [key for key in (llm_blockers or []) if key in SITUATION_BLOCKING_SLOTS]


def count_problems_heuristic(text):
    from strategy_readiness import explicit_multi_problem_count

    count = explicit_multi_problem_count(text)
    return count if count else (1 if sanitize_situation_for_check(text) else 0)
