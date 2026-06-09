# -*- coding: utf-8 -*-
"""
Strategy-readiness engine for manual situation descriptions.

Design principle: slots (problem/context/goal) are an extraction artifact for
normalization — not a form to fill. The gate asks users only when missing
information would produce a wrong or unfocused Agent1 strategy.

What Agent1 actually needs from the situation field (see PROMPT_A):
  - one concrete business problem that a mechanism can address.

What we infer silently (never ask):
  - context (business domain)
  - goal (usually inverse of the problem)

Clarify tiers:
  A — proceed (strategy can be built)
  B — one targeted question (ambiguous problem OR multiple problems)
  C — rewrite (empty, spam, too vague, too long)
"""
from __future__ import annotations

import re
from typing import Any

READINESS_TIER_PROCEED = "A"
READINESS_TIER_CLARIFY = "B"
READINESS_TIER_REJECT = "C"

CLARIFY_FOCUS = "focus"
CLARIFY_PROBLEM = "problem"
CLARIFY_REWRITE = "rewrite"

SITUATION_SOFT_LENGTH = 1000
SITUATION_HARD_LENGTH = 2000

SITUATION_SLOT_LABELS = {
    "problem": "конкретная проблема или симптом (problem)",
    "context": "где проявляется проблема (context)",
    "goal": "цель или желаемый результат (goal)",
    "scale": "масштаб или динамика (scale)",
    "cause": "причина или гипотеза (cause)",
    "constraints": "ограничения (constraints)",
}

# Archetypes: known symptom → inferred normalization fields.
PROBLEM_ARCHETYPES = (
    {
        "id": "customer_churn",
        "pattern": r"отток\s+(?:клиент|покупател|абонент|подписчик)|\bchurn\b|уход\s+клиент",
        "problem": "отток клиентов",
        "context": "клиентская база",
        "goal": "остановить отток клиентов",
        "mechanism_family": "loss_reduction",
    },
    {
        "id": "staff_turnover",
        "pattern": r"текучк|увольнен|не\s+хватает\s+сотрудник|дефицит\s+кадр|подбор\s+персонал",
        "problem": "текучка кадров",
        "context": "HR / персонал",
        "goal": "снизить текучку кадров",
        "mechanism_family": "operational_recovery",
    },
    {
        "id": "sales_decline",
        "pattern": r"падают\s+продаж|снижени[ея]\s+продаж|продажи\s+падают|выручк[аи]\s+падает|падени[ея]\s+выручк",
        "problem": "снижение продаж",
        "context": "продажи",
        "goal": "восстановить продажи",
        "mechanism_family": "revenue_model",
    },
    {
        "id": "customer_complaints",
        "pattern": r"жалоб|недовольн|плохой\s+сервис|качеств[оа]\s+обслуживан",
        "problem": "жалобы клиентов",
        "context": "клиентский сервис",
        "goal": "снизить жалобы и повысить удовлетворённость",
        "mechanism_family": "operational_recovery",
    },
    {
        "id": "cash_pressure",
        "pattern": r"кассовый\s+разрыв|не\s+хватает\s+денег|ликвидност",
        "problem": "нехватка ликвидности",
        "context": "финансы",
        "goal": "стабилизировать денежный поток",
        "mechanism_family": "operational_recovery",
    },
    {
        "id": "tax_arrears",
        "pattern": r"налогов\w*\s+задолженн|долг\s+по\s+налог",
        "problem": "налоговая задолженность",
        "context": "налоги / финансы",
        "goal": "погасить налоговую задолженность",
        "mechanism_family": "loss_reduction",
    },
    {
        "id": "debt_burden",
        "pattern": r"долг|задолженност|просрочк[аи]\s+по",
        "problem": "долговая нагрузка",
        "context": "финансы",
        "goal": "снизить долговую нагрузку",
        "mechanism_family": "loss_reduction",
    },
    {
        "id": "competition",
        "pattern": r"конкурент|доля\s+рынка|теряем\s+клиент",
        "problem": "давление конкурентов",
        "context": "рынок / продажи",
        "goal": "укрепить конкурентную позицию",
        "mechanism_family": "revenue_model",
    },
    {
        "id": "supply_chain",
        "pattern": r"задержк[аи]\s+поставок|логистик|склад|дефицит\s+товар",
        "problem": "проблемы с поставками",
        "context": "логистика / склад",
        "goal": "наладить поставки и наличие товара",
        "mechanism_family": "operational_recovery",
    },
    {
        "id": "no_acquiring",
        "pattern": r"нет\s+эквайринг|без\s+эквайринг|не\s+принимаем\s+карт",
        "problem": "отсутствует эквайринг",
        "context": "платежи / продажи",
        "goal": "организовать приём безналичных платежей",
        "mechanism_family": "operational_recovery",
    },
)

DOMAIN_KEYWORDS = (
    (r"сотрудник|кадр|персонал|hr|зарплат|увольнен|текучк", "HR / персонал"),
    (r"клиент|покупател|абонент|churn|отток|продаж|выручк|маркетплейс", "продажи / клиенты"),
    (r"поставк|логистик|склад|товар|импорт", "логистика / склад"),
    (r"деньг|ликвидност|касс|кредит|долг|налог|финанс", "финансы"),
    (r"жалоб|сервис|обслуживан|поддержк", "клиентский сервис"),
    (r"производств|цех|оборудован", "производство"),
)

GOAL_TEMPLATES = (
    (r"отток|уход\s+клиент|churn", "остановить отток"),
    (r"текучк|увольнен", "снизить текучку"),
    (r"пада|сниж|падени", "восстановить показатели"),
    (r"рост|увелич", "снизить негативную динамику"),
    (r"жалоб|недовольн", "снизить жалобы"),
    (r"дефицит|не\s+хватает|отсутств", "устранить дефицит"),
    (r"долг|задолженност", "снизить задолженность"),
    (r"задержк", "устранить задержки"),
)

SYMPTOM_MARKERS = re.compile(
    r"проблем|пада|сниж|рост|текуч|дефицит|не\s+хватает|нужен|нужно|отсутств|"
    r"задерж|жалоб|убыт|долг|конкурент|отток|churn|эквайринг|налог",
    re.IGNORECASE,
)

VAGUE_PHRASES = {
    "улучшить бизнес",
    "развить бизнес",
    "оптимизировать бизнес",
    "нужна помощь",
    "помогите",
    "нужна стратегия",
    "хочу кредит",
    "нужен кредит",
    "нужно финансирование",
    "увеличить прибыль",
}

# Descriptions that describe the bank's problem, not the client's business problem.
BANK_CENTRIC_PATTERNS = (
    # "клиент перестал пользоваться продуктом банка" / услугами банка
    r"клиент\w*\s+(?:перестал|не\s+пользуетс|отказалс|прекратил).*(?:продукт|услуг|сервис)\w*\s+(?:банк|наш|ваш)",
    r"(?:продукт|услуг|сервис)\w*\s+(?:банк|наш|ваш).*(?:перестал|не\s+пользуетс|отказалс)",
    # "клиент уходит из банка" / "клиенты уходят из банка"
    r"(?:клиент|компан)\w*\s+уход\w+\s+из\s+банк",
    r"отток\s+клиент\w*\s+из\s+банк",
    # "перестал пользоваться банком" / "не пользуется услугами банка"
    r"переста\w+\s+пользоваться\s+(?:банк|услуг)",
    r"не\s+пользу\w+\s+(?:услуг|продукт|сервис)\w*\s+(?:банк|наш)",
    # "клиент не платит по кредиту" / "клиент перестал платить"
    r"клиент\w*\s+(?:не\s+платит|перестал\s+платить|просроч\w+)\s+(?:по\s+)?(?:кредит|займ|платеж)",
    # "уход клиента в другой банк"
    r"уход\w*\s+(?:клиент|компан)\w*\s+в\s+другой\s+банк",
)


def is_bank_centric(text: str) -> bool:
    """Detect descriptions of bank's problem, not client's business problem."""
    normalized = sanitize_situation_for_check(text).lower()
    if not normalized:
        return False
    for pattern in BANK_CENTRIC_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True
    return False


BANK_CENTRIC_REWRITE_HINT = (
    "Опишите проблему бизнеса клиента, а не банка. "
    "Например: «у клиента падают продажи», «у клиента высокая текучка кадров», "
    "«клиент теряет своих покупателей»."
)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "да"}


def sanitize_situation_for_check(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^у\s+клиент[аеу]?\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def slot_confidence(slot: dict) -> float:
    if not isinstance(slot, dict):
        return 0.0
    try:
        return float(slot.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.8 if slot.get("present") else 0.0


def parse_slots(slots_data: dict | None) -> dict:
    keys = ("problem", "context", "goal", "scale", "cause", "constraints")
    if not isinstance(slots_data, dict):
        return {}
    parsed = {}
    for key in keys:
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


def match_archetype(text: str) -> dict | None:
    normalized = sanitize_situation_for_check(text)
    if not normalized:
        return None
    for archetype in PROBLEM_ARCHETYPES:
        if re.search(archetype["pattern"], normalized, re.IGNORECASE):
            return dict(archetype)
    return None


def infer_domain(text: str) -> str:
    normalized = sanitize_situation_for_check(text)
    for pattern, domain in DOMAIN_KEYWORDS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return domain
    return "операционная деятельность"


def infer_goal_from_problem(problem: str, text: str = "") -> str:
    source = f"{problem} {text}".strip()
    for pattern, goal in GOAL_TEMPLATES:
        if re.search(pattern, source, re.IGNORECASE):
            return goal
    if problem:
        return f"устранить проблему: {problem}"
    return "стабилизировать ситуацию"


def extract_problem_phrase(text: str) -> str:
    normalized = sanitize_situation_for_check(text)
    archetype = match_archetype(normalized)
    if archetype:
        return archetype["problem"]
    return normalized


def build_slots_from_text(text: str, enrichment_slots: dict | None = None) -> dict:
    normalized = sanitize_situation_for_check(text)
    slots = parse_slots(enrichment_slots)
    archetype = match_archetype(normalized)

    if archetype:
        for key in ("problem", "context", "goal"):
            slots[key] = {
                "present": True,
                "value": archetype[key],
                "confidence": 0.95,
                "source": "archetype",
                "archetype_id": archetype["id"],
            }
        slots["_mechanism_family"] = archetype.get("mechanism_family")
        return slots

    problem_value = (
        str((slots.get("problem") or {}).get("value") or "").strip() or normalized
    )
    if problem_value and not (slots.get("problem") or {}).get("present"):
        slots["problem"] = {
            "present": True,
            "value": problem_value,
            "confidence": 0.7,
            "source": "text",
        }

    if not (slots.get("context") or {}).get("value"):
        slots["context"] = {
            "present": True,
            "value": infer_domain(normalized),
            "confidence": 0.85,
            "source": "inferred",
        }

    if not (slots.get("goal") or {}).get("value"):
        slots["goal"] = {
            "present": True,
            "value": infer_goal_from_problem(problem_value, normalized),
            "confidence": 0.85,
            "source": "inferred",
        }

    return slots


def problem_is_actionable(slots: dict, text: str) -> bool:
    normalized = sanitize_situation_for_check(text)
    if is_vague_request(normalized):
        return False
    if match_archetype(normalized):
        return True
    problem = slots.get("problem") or {}
    value = str(problem.get("value") or "").strip()
    if not value:
        return False
    if SYMPTOM_MARKERS.search(normalized):
        return True
    if slot_confidence(problem) >= 0.8 and SYMPTOM_MARKERS.search(value):
        return True
    return False


def is_vague_request(text: str) -> bool:
    normalized = sanitize_situation_for_check(text).lower()
    if not normalized:
        return True
    if normalized in VAGUE_PHRASES:
        return True
    words = re.findall(r"[а-яёa-z0-9%+-]+", normalized)
    if len(words) <= 6 and any(phrase in normalized for phrase in VAGUE_PHRASES):
        return True
    return False


def explicit_multi_problem_count(text: str) -> int:
    """Count independent problems only with strong structural signals."""
    normalized = sanitize_situation_for_check(text)
    if not normalized:
        return 0

    numbered = re.findall(r"(?:^|\s)\d+[\).\:-]\s*\S+", normalized)
    if len(numbered) >= 2:
        return len(numbered)

    parts = re.split(r"[;\n]+", normalized)
    if len(parts) >= 2:
        distinct = 0
        for part in parts:
            chunk = part.strip()
            if len(chunk) >= 12 and SYMPTOM_MARKERS.search(chunk):
                distinct += 1
        if distinct >= 2:
            return distinct

    return 0


def should_request_focus(
    text: str,
    llm_problem_count: int,
    detected_problems: list[str],
) -> bool:
    explicit_count = explicit_multi_problem_count(text)
    if explicit_count >= 2:
        return True
    if llm_problem_count >= 2 and len(detected_problems) >= 2:
        return True
    return False


def build_normalized_text(slots: dict) -> str:
    problem = str((slots.get("problem") or {}).get("value") or "").strip()
    context = str((slots.get("context") or {}).get("value") or "").strip()
    goal = str((slots.get("goal") or {}).get("value") or "").strip()
    parts = []
    if problem:
        parts.append(f"Проблема: {problem}.")
    if context:
        parts.append(f"Контекст: {context}.")
    if goal:
        parts.append(f"Цель: {goal}.")
    return " ".join(parts).strip()


def run_guards(text: str) -> dict | None:
    normalized = sanitize_situation_for_check(text)
    if not normalized:
        return _reject(
            reason="Описание ситуации пустое.",
            rewrite_hint="Опишите одну ситуацию: что происходит и чего хотите добиться.",
            missing=[CLARIFY_REWRITE],
        )
    if len(normalized) > SITUATION_HARD_LENGTH:
        return _reject(
            reason="Описание слишком длинное — в нём сложно выделить одну проблему для стратегии.",
            rewrite_hint=(
                f"Сократите до {SITUATION_SOFT_LENGTH} символов: одна проблема, где проявляется, желаемый результат."
            ),
            missing=[CLARIFY_REWRITE],
            problem_count=explicit_multi_problem_count(normalized) or 1,
        )
    if not re.search(r"[A-Za-zА-Яа-яЁё]", normalized):
        return _reject(
            reason="Описание не содержит осмысленного текста.",
            rewrite_hint="Опишите ситуацию словами: что происходит в компании.",
            missing=[CLARIFY_REWRITE],
        )
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9%+-]+", normalized.lower())
    unique_words = set(words)
    if len(words) >= 20 and len(unique_words) / max(len(words), 1) < 0.35:
        return _reject(
            reason="Текст похож на повторяющийся набор фраз, а не на описание ситуации.",
            rewrite_hint="Переформулируйте одним связным описанием одной проблемы.",
            missing=[CLARIFY_REWRITE],
        )
    if is_vague_request(normalized):
        return _reject(
            reason="Описание содержит только общий запрос, но не объясняет ситуацию компании.",
            rewrite_hint="Укажите конкретную проблему: что именно происходит в компании.",
            missing=[CLARIFY_PROBLEM],
            score=15,
        )
    if is_bank_centric(normalized):
        return _reject(
            reason="Описание похоже на проблему банка, а не бизнеса клиента.",
            rewrite_hint=BANK_CENTRIC_REWRITE_HINT,
            missing=[CLARIFY_REWRITE],
            score=10,
        )
    return None


def _reject(**kwargs) -> dict:
    base = {
        "ok": False,
        "tier": READINESS_TIER_REJECT,
        "score": kwargs.get("score", 20),
        "missing": kwargs.get("missing", [CLARIFY_REWRITE]),
        "reason": kwargs.get("reason", ""),
        "rewrite_hint": kwargs.get("rewrite_hint", ""),
        "example": "",
        "normalized_text": "",
        "source": "rules",
        "clarify_mode": None,
        "clarify_payload": {},
        "slots_filled": {},
        "problem_count": kwargs.get("problem_count", 0),
        "detected_problems": [],
        "agent1_ready": False,
        "readiness": {},
    }
    base.update(kwargs)
    return base


def assess_strategy_readiness(text: str, enrichment: dict | None = None) -> dict:
    """
    Deterministic readiness decision. Optional enrichment from LLM is advisory only.
    """
    enrichment = enrichment or {}
    guard = run_guards(text)
    if guard:
        return guard

    normalized = sanitize_situation_for_check(text)
    slots = build_slots_from_text(normalized, enrichment.get("slots"))
    detected_problems = enrichment.get("detected_problems") or []
    if isinstance(detected_problems, str):
        detected_problems = [
            item.strip()
            for item in re.split(r"[,;\n]", detected_problems)
            if item.strip()
        ]
    detected_problems = [str(item).strip() for item in detected_problems if str(item).strip()]

    try:
        llm_problem_count = int(enrichment.get("problem_count") or 0)
    except (TypeError, ValueError):
        llm_problem_count = 0
    if llm_problem_count < len(detected_problems):
        llm_problem_count = len(detected_problems)

    readiness_meta = {
        "tier": READINESS_TIER_PROCEED,
        "focus": "single",
        "specificity": "actionable" if problem_is_actionable(slots, normalized) else "vague",
        "inference": {
            "context": (slots.get("context") or {}).get("source", "inferred"),
            "goal": (slots.get("goal") or {}).get("source", "inferred"),
            "archetype_id": (slots.get("problem") or {}).get("archetype_id"),
        },
        "mechanism_family": slots.get("_mechanism_family"),
    }

    if should_request_focus(normalized, llm_problem_count, detected_problems):
        readiness_meta["tier"] = READINESS_TIER_CLARIFY
        readiness_meta["focus"] = "multiple"
        options = detected_problems or re.split(r"[;\n]", normalized)
        options = [item.strip() for item in options if item.strip()][:6]
        return _clarify_focus(
            slots=slots,
            detected_problems=options,
            reason=enrichment.get("reason")
            or "В описании несколько разных проблем. Выберите, что решаем в первую очередь.",
            readiness=readiness_meta,
            source=enrichment.get("source") or "readiness",
        )

    if not problem_is_actionable(slots, normalized):
        readiness_meta["tier"] = READINESS_TIER_CLARIFY
        readiness_meta["specificity"] = "vague"
        return _clarify_problem(
            slots=slots,
            enrichment=enrichment,
            reason=enrichment.get("reason")
            or "Не удалось выделить одну конкретную проблему для стратегии.",
            readiness=readiness_meta,
            source=enrichment.get("source") or "readiness",
        )

    normalized_text = str(enrichment.get("normalized_text") or "").strip() or build_normalized_text(slots)
    readiness_meta["tier"] = READINESS_TIER_PROCEED
    return {
        "ok": True,
        "tier": READINESS_TIER_PROCEED,
        "score": max(int(enrichment.get("score") or 0), 75),
        "missing": [],
        "reason": enrichment.get("reason") or "Описания достаточно для построения стратегии.",
        "rewrite_hint": "",
        "example": "",
        "normalized_text": normalized_text,
        "source": enrichment.get("source") or "readiness",
        "clarify_mode": None,
        "clarify_payload": {},
        "slots_filled": {k: v for k, v in slots.items() if not str(k).startswith("_")},
        "problem_count": 1,
        "detected_problems": detected_problems or [extract_problem_phrase(normalized)],
        "agent1_ready": True,
        "readiness": readiness_meta,
    }


def _clarify_focus(slots, detected_problems, reason, readiness, source):
    from situation_validation import normalize_focus_question, pack_clarify_payload

    focus_question = normalize_focus_question(detected_problems)
    payload = pack_clarify_payload("focus", focus_question=focus_question)
    return {
        "ok": False,
        "tier": READINESS_TIER_CLARIFY,
        "score": 45,
        "missing": [CLARIFY_FOCUS],
        "reason": reason or focus_question["message"],
        "rewrite_hint": "",
        "example": "",
        "normalized_text": "",
        "source": source,
        "clarify_mode": "focus",
        "clarify_payload": payload,
        "slots_filled": {k: v for k, v in slots.items() if not str(k).startswith("_")},
        "problem_count": len(detected_problems) or 2,
        "detected_problems": detected_problems,
        "agent1_ready": False,
        "readiness": readiness,
    }


def _clarify_problem(slots, enrichment, reason, readiness, source):
    from situation_validation import normalize_slot_questions, pack_clarify_payload

    slot_options = enrichment.get("slot_options") or {}
    slot_questions = normalize_slot_questions(slot_options, ["problem"])
    payload = pack_clarify_payload("slots", slot_questions=slot_questions)
    return {
        "ok": False,
        "tier": READINESS_TIER_CLARIFY,
        "score": 45,
        "missing": [CLARIFY_PROBLEM],
        "reason": reason,
        "rewrite_hint": enrichment.get("rewrite_hint") or "",
        "example": enrichment.get("example") or "",
        "normalized_text": "",
        "source": source,
        "clarify_mode": "slots",
        "clarify_payload": payload,
        "slots_filled": {k: v for k, v in slots.items() if not str(k).startswith("_")},
        "problem_count": 1,
        "detected_problems": enrichment.get("detected_problems") or [],
        "agent1_ready": False,
        "readiness": readiness,
    }
