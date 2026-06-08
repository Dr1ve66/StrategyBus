# -*- coding: utf-8 -*-
import json
import re

SITUATION_REQUIRED_SLOTS = ("problem", "context", "goal")
SITUATION_OPTIONAL_SLOTS = ("scale", "cause", "constraints")
SITUATION_BLOCKING_SLOTS = ("problem",)
SITUATION_INFERABLE_SLOTS = ("context", "goal")
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

PROMPT_SITUATION_SLOT_FILL = """
Ты — бизнес-аналитик. Разбери описание ситуации компании и оцени, достаточно ли его для построения ОДНОЙ бизнес-стратегии (Agent1).

Слоты методологии:
- problem — что происходит, симптом или проблема (обязателен для уточнения, если неясен);
- context — область бизнеса (продажи, HR, финансы, склад и т.д.);
- goal — желаемый результат;
- scale, cause, constraints — дополнительно, не блокируют.

Принцип достаточности:
1. agent1_ready=true, если из текста можно построить одну конкретную стратегию, даже если goal или context не названы явно, но однозначно выводятся из problem.
2. Короткая фраза «текучка кадров», «падают продажи на маркетплейсе» — достаточна: извлеки problem, context, goal; missing_blockers пустой.
3. missing_blockers — только слоты, без которых стратегия будет ошибочной или слишком общей. НЕ включай context/goal, если их можно вывести с уверенностью ≥ 0.8.
4. problem_count — число различных независимых проблем (не симптомов одной проблемы). Если ≥ 2 — agent1_ready=false.
5. detected_problems — краткие формулировки каждой отдельной проблемы (1 строка каждая).
6. normalized_text — связное описание одной сфокусированной ситуации (problem + context + goal), без выдуманных фактов. Заполняй, если agent1_ready=true.
7. slot_options — только для ключей из missing_blockers: message и 3–4 варианта + «Другое (уточню сам)».

Верни строго JSON:
{
  "agent1_ready": true или false,
  "problem_count": число,
  "detected_problems": ["...", "..."],
  "score": число от 0 до 100,
  "missing_blockers": ["problem"],
  "slots": {
    "problem": {"present": true, "value": "...", "confidence": 0.9},
    "context": {"present": true, "value": "...", "confidence": 0.85},
    "goal": {"present": true, "value": "...", "confidence": 0.8},
    "scale": {"present": false, "value": "", "confidence": 0},
    "cause": {"present": false, "value": "", "confidence": 0},
    "constraints": {"present": false, "value": "", "confidence": 0}
  },
  "reason": "коротко: почему достаточно или что мешает",
  "normalized_text": "если agent1_ready=true — итог; иначе пустая строка",
  "slot_options": {
    "problem": {
      "message": "...",
      "options": ["вариант 1", "вариант 2", "Другое (уточню сам)"]
    }
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


def sanitize_situation_for_check(text):
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^у\s+клиент[аеу]?\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


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


def missing_required_slots(slots):
    missing = []
    for key in SITUATION_REQUIRED_SLOTS:
        slot = slots.get(key) or {}
        if not slot.get("present") or not str(slot.get("value") or "").strip():
            missing.append(key)
    return missing


def inferable_slot_missing(slots, key, min_confidence=0.8):
    slot = slots.get(key) or {}
    value = str(slot.get("value") or "").strip()
    if not value:
        return True
    if not slot.get("present"):
        return True
    return slot_confidence(slot) < min_confidence


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
            message = f"Для полного понимания проблемы не хватает: {SITUATION_SLOT_LABELS.get(key, key)}."
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


def count_problems_heuristic(text):
    normalized = sanitize_situation_for_check(text)
    if not normalized:
        return 0
    parts = re.split(r"[;\n]|(?:\s+и\s+)|(?:\s+а\s+также\s+)|(?:\s+кроме\s+того\s+)|(?:\s+ещё\s+)|(?:\s+еще\s+)", normalized, flags=re.IGNORECASE)
    markers = 0
    for part in parts:
        chunk = part.strip()
        if len(chunk) < 12:
            continue
        if re.search(
            r"проблем|пада|сниж|рост|текуч|дефицит|не хватает|нужен|нужно|отсутств|задерж|жалоб|убыт|долг|конкурент",
            chunk,
            re.IGNORECASE,
        ):
            markers += 1
    numbered = len(re.findall(r"(?:^|\s)\d+[\).\:-]\s*\S", normalized))
    return max(markers, numbered, 1 if normalized else 0)


def run_basic_situation_guards(text):
    normalized = sanitize_situation_for_check(text)
    if not normalized:
        return {
            "ok": False,
            "score": 0,
            "missing": list(SITUATION_BLOCKING_SLOTS),
            "reason": "Описание ситуации пустое.",
            "rewrite_hint": "Опишите одну ситуацию: что происходит и чего хотите добиться.",
            "example": "",
            "normalized_text": "",
            "source": "rules",
            "clarify_mode": None,
            "clarify_payload": {},
            "slots_filled": {},
            "problem_count": 0,
            "detected_problems": [],
        }
    if len(normalized) > SITUATION_HARD_LENGTH:
        return {
            "ok": False,
            "score": 25,
            "missing": ["focus"],
            "reason": "Описание слишком длинное — в нём сложно выделить одну проблему для стратегии.",
            "rewrite_hint": (
                f"Сократите до {SITUATION_SOFT_LENGTH} символов: одна проблема, где проявляется, желаемый результат."
            ),
            "example": "",
            "normalized_text": "",
            "source": "rules",
            "clarify_mode": None,
            "clarify_payload": {},
            "slots_filled": {},
            "problem_count": count_problems_heuristic(normalized),
            "detected_problems": [],
        }
    if not re.search(r"[A-Za-zА-Яа-яЁё]", normalized):
        return {
            "ok": False,
            "score": 5,
            "missing": list(SITUATION_BLOCKING_SLOTS),
            "reason": "Описание не содержит осмысленного текста.",
            "rewrite_hint": "Опишите ситуацию словами: что происходит в компании.",
            "example": "",
            "normalized_text": "",
            "source": "rules",
            "clarify_mode": None,
            "clarify_payload": {},
            "slots_filled": {},
            "problem_count": 0,
            "detected_problems": [],
        }
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9%+-]+", normalized.lower())
    unique_words = set(words)
    if len(words) >= 20 and len(unique_words) / max(len(words), 1) < 0.35:
        return {
            "ok": False,
            "score": 20,
            "missing": ["problem"],
            "reason": "Текст похож на повторяющийся набор фраз, а не на описание ситуации.",
            "rewrite_hint": "Переформулируйте одним связным описанием одной проблемы.",
            "example": "",
            "normalized_text": "",
            "source": "rules",
            "clarify_mode": None,
            "clarify_payload": {},
            "slots_filled": {},
            "problem_count": 0,
            "detected_problems": [],
        }
    vague_phrases = {
        "улучшить бизнес", "развить бизнес", "оптимизировать бизнес",
        "нужна помощь", "помогите", "нужна стратегия", "хочу кредит",
        "нужен кредит", "нужно финансирование", "увеличить прибыль",
    }
    if normalized.lower() in vague_phrases or (len(words) <= 6 and any(phrase in normalized.lower() for phrase in vague_phrases)):
        return {
            "ok": False,
            "score": 15,
            "missing": ["problem", "context"],
            "reason": "Описание содержит только общий запрос, но не объясняет ситуацию компании.",
            "rewrite_hint": "Укажите конкретную проблему и где она проявляется.",
            "example": "",
            "normalized_text": "",
            "source": "rules",
            "clarify_mode": None,
            "clarify_payload": {},
            "slots_filled": {},
            "problem_count": 0,
            "detected_problems": [],
        }
    return None


def resolve_missing_blockers(slots, llm_blockers):
    blockers = []
    for key in llm_blockers or []:
        if key in SITUATION_REQUIRED_SLOTS and key not in blockers:
            blockers.append(key)
    if not blockers:
        for key in SITUATION_BLOCKING_SLOTS:
            if inferable_slot_missing(slots, key, min_confidence=0.75):
                blockers.append(key)
        for key in SITUATION_INFERABLE_SLOTS:
            if inferable_slot_missing(slots, key, min_confidence=0.8) and key in (llm_blockers or missing_required_slots(slots)):
                blockers.append(key)
    return blockers


def normalize_situation_slot_check(data, source="llm"):
    slots = parse_situation_slots(data.get("slots") or {})
    try:
        score = int(data.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(score, 100))

    detected_problems = data.get("detected_problems") or []
    if isinstance(detected_problems, str):
        detected_problems = [item.strip() for item in re.split(r"[,;\n]", detected_problems) if item.strip()]
    detected_problems = [str(item).strip() for item in detected_problems if str(item).strip()]

    try:
        problem_count = int(data.get("problem_count") or 0)
    except (TypeError, ValueError):
        problem_count = 0
    if problem_count < len(detected_problems):
        problem_count = len(detected_problems)
    if problem_count <= 0 and detected_problems:
        problem_count = len(detected_problems)

    agent1_ready = to_bool(data.get("agent1_ready"))
    missing_blockers = resolve_missing_blockers(slots, data.get("missing_blockers") or data.get("missing_required") or data.get("missing") or [])

    reason = str(data.get("reason") or "").strip()
    normalized_text = str(data.get("normalized_text") or "").strip()
    clarify_mode = None
    clarify_payload = {}

    if problem_count >= 2 or (detected_problems and len(detected_problems) >= 2):
        ok = False
        score = min(score, 50)
        clarify_mode = "focus"
        focus_question = normalize_focus_question(detected_problems, data.get("focus_question") or {})
        clarify_payload = pack_clarify_payload("focus", focus_question=focus_question)
        if not reason:
            reason = focus_question["message"]
        missing = ["focus"]
    elif agent1_ready and not missing_blockers:
        ok = True
        score = max(score, 70)
        if not normalized_text:
            normalized_text = build_situation_from_slots("", {}, slots)
        missing = []
        if not reason:
            reason = "Описания достаточно для построения стратегии."
    else:
        ok = False
        score = min(score, 55) if score else 45
        missing = missing_blockers or missing_required_slots(slots)
        missing = [key for key in missing if key in SITUATION_REQUIRED_SLOTS]
        if missing:
            slot_questions = normalize_slot_questions(data.get("slot_options") or {}, missing)
            clarify_mode = "slots"
            clarify_payload = pack_clarify_payload("slots", slot_questions=slot_questions)
            if not reason:
                reason = format_missing_slots_message(missing)
            elif "не хватает" not in reason.lower():
                reason = format_missing_slots_message(missing)
        else:
            missing = ["problem"]
            reason = reason or "Не удалось выделить одну конкретную проблему."
            slot_questions = normalize_slot_questions(data.get("slot_options") or {}, missing)
            clarify_mode = "slots"
            clarify_payload = pack_clarify_payload("slots", slot_questions=slot_questions)

    return {
        "ok": ok,
        "score": score if ok else max(score, 40),
        "missing": missing,
        "reason": reason,
        "rewrite_hint": str(data.get("rewrite_hint") or "").strip(),
        "example": str(data.get("example") or "").strip(),
        "normalized_text": normalized_text if ok else "",
        "source": source,
        "clarify_mode": clarify_mode,
        "clarify_payload": clarify_payload,
        "slots_filled": slots,
        "problem_count": problem_count,
        "detected_problems": detected_problems,
        "agent1_ready": agent1_ready,
    }


def finalize_check_result(result):
    payload = result.get("clarify_payload") or {}
    if not payload and result.get("slot_questions"):
        payload = result["slot_questions"]
    result["clarify_payload"] = payload
    result["slot_questions"] = payload
    return result


def call_situation_check(text, call_openai_raw):
    guard = run_basic_situation_guards(text)
    if guard:
        return finalize_check_result(guard)
    cleaned = sanitize_situation_for_check(text)
    heuristic_count = count_problems_heuristic(cleaned)
    data = call_openai_raw(
        PROMPT_SITUATION_SLOT_FILL,
        json.dumps(
            {
                "situation_description": cleaned,
                "heuristic_problem_count": heuristic_count,
                "length_hint": (
                    "long"
                    if len(cleaned) > SITUATION_SOFT_LENGTH
                    else "ok"
                ),
            },
            ensure_ascii=False,
        ),
    )
    result = normalize_situation_slot_check(data, source="llm")
    if (
        not result["ok"]
        and result.get("clarify_mode") != "focus"
        and heuristic_count >= 2
        and len(result.get("detected_problems") or []) < 2
    ):
        result["problem_count"] = heuristic_count
        focus_question = normalize_focus_question(
            result.get("detected_problems") or re.split(r"[;\n]", cleaned)[:heuristic_count],
        )
        result.update({
            "ok": False,
            "missing": ["focus"],
            "clarify_mode": "focus",
            "clarify_payload": pack_clarify_payload("focus", focus_question=focus_question),
            "reason": focus_question["message"],
        })
    return finalize_check_result(result)
