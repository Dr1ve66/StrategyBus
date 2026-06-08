# -*- coding: utf-8 -*-
import json
import os
import re

from capability_rules import (
    capabilities_from_text,
    forbidden_product_capabilities,
)

STEP_ARCHETYPE_PATTERNS = {
    "competitor_research": r"конкурент|конкурентн",
    "market_research": r"исслед.{0,16}рынк|анализ рынк|рыночн.{0,12}сред|изуч.{0,16}рынк",
    "business_analytics": r"аналитик|дашборд|отчетност|отчётност|kpi|метрик",
    "launch_advertising": r"запуст.{0,16}реклам|настро.{0,12}реклам|таргетир|геоконтекст|медиаплан",
    "improve_ad_performance": r"эффективност.{0,12}реклам|стоимость привлечен|ctr|охват аудитор",
    "employee_hiring": r"подбор сотрудник|найм|ваканс|рекрут|привлеч.{0,16}кандидат",
    "partner_matching": r"подбор партнер|поиск партнер|поиск подрядчик",
    "customer_loyalty_program": r"лояльност.{0,20}(клиент|покупател)|бонус.{0,16}клиент|удержан.{0,16}клиент",
    "employee_retention": r"удержан.{0,12}сотрудник|текуч|сниз.{0,12}увольн",
    "employee_training": r"обуч|повыш.{0,12}квалиф|тренинг",
    "payment_acceptance": r"эквайринг|прием платеж|приём платеж|безнал|pos|терминал",
    "business_financing": r"кредит|финансир|кассов|овердрафт|гарант|факторинг|лизинг",
    "treasury": r"депозит|размещен.{0,16}средств|свободн.{0,12}средств|вексел|облигаци|репо|ценн.*бумаг",
    "bank_comparison": r"банк.{0,24}(депозит|средств|размещ)|депозит.{0,24}банк|потенциальн.{0,16}банк|сравн.{0,12}банк",
    "business_banking": r"рко|расчетн|расчётн|открыт.{0,12}счет|открыт.{0,12}счёт",
    "marketplace_operations": r"маркетплейс|селлер|ozon|wildberries|яндекс.?маркет|мегамаркет",
    "asset_realization": r"реализ.*актив|продаж.*имуществ|продаж.*актив|залогов.*имуществ|изъят.*имуществ|имуществ.*банкрот|портал da",
    "staff_tips": r"чаев|чаевые|gratuit",
    "tax_free_operations": r"tax\s*free|taxfree|возврат ндс иностран",
    "corporate_health_insurance": r"дмс|медстрах|корпоративн.*здоров|страхован.*сотрудник|добровольн.*медицин",
    "background_music": r"фонов.*музык|корпоративн.*музык|музык.*в точк|звук бизнес|музыкальн.*оформлен",
    "accounting_tax": r"эдо|документооборот|бухгалтер|налог|отчетност|отчётност|электронн.*подпис",
}

ARCHETYPE_REQUIRED_CAPABILITIES = {
    "competitor_research": {
        "competitor_analysis",
        "marketplace_analytics",
        "business_analytics",
        "consumer_analytics",
        "geo_analytics",
    },
    "market_research": {
        "business_analytics",
        "consumer_analytics",
        "geo_analytics",
        "marketplace_analytics",
        "real_estate_analytics",
    },
    "business_analytics": {"business_analytics", "consumer_analytics", "predictive_analytics", "speech_analytics"},
    "launch_advertising": {"advertising", "audience_targeting", "lead_generation"},
    "improve_ad_performance": {"advertising", "audience_targeting"},
    "employee_hiring": {"employee_hiring"},
    "partner_matching": {"partner_matching"},
    "customer_loyalty_program": {"customer_loyalty_program"},
    "employee_retention": {"employee_retention", "hr_operations", "payroll"},
    "employee_training": {"employee_training", "corporate_training", "digital_learning"},
    "payment_acceptance": {"payment_acceptance"},
    "business_financing": {"business_financing", "bank_guarantee", "factoring", "leasing_financing", "currency_hedging"},
    "business_banking": {"business_banking", "premium_banking"},
    "marketplace_operations": {"marketplace_analytics", "marketplace_onboarding"},
    "accounting_tax": {"accounting", "tax_reporting", "electronic_document_flow", "electronic_signature"},
    "legal_support": {"legal_support"},
    "sales_management": {"crm_sales", "sales_management", "lead_generation"},
    "construction_management": {"construction_supervision", "construction_digital", "real_estate_services"},
    "restaurant_operations": {"restaurant_automation"},
    "hotel_operations": {"hotel_management"},
    "international_trade": {"international_payments", "currency_control", "ved_consulting"},
    "tender_participation": {"tender_platform", "tender_guarantee", "bank_guarantee"},
    "risk_management": {"risk_management", "counterparty_check", "insurance"},
    "it_infrastructure": {"cloud_infrastructure", "it_outsourcing"},
    "it_security": {"cybersecurity"},
    "business_automation": {"ai_assistant", "business_automation", "chatbots", "voice_robot"},
    "customer_service_automation": {"online_booking", "chatbots", "voice_robot"},
    "website_creation": {"website_creation"},
    "procurement": {"procurement_platform"},
    "real_estate": {"real_estate_services", "real_estate_analytics"},
    "logistics": {"logistics"},
    "cash_operations": {"cash_collection", "self_collection"},
    "treasury": {"deposits", "securities", "treasury", "business_financing"},
    "bank_comparison": {"deposits", "treasury", "business_banking", "business_financing"},
    "employee_wellness": {"medical_services"},
    "compliance": {"compliance"},
    "self_employed_management": {"self_employed_management"},
    "business_registration": {"business_registration"},
    "operational_efficiency": {"operational_efficiency"},
    "strategic_consulting": {"operational_efficiency"},
    "custom_solutions": {"custom_projects", "catch_all_partner", "catch_all_banking"},
    "asset_realization": {"asset_realization", "real_estate_services"},
    "staff_tips": {"tips_service", "payment_acceptance"},
    "tax_free_operations": {"tax_refund_service", "retail_services"},
    "corporate_health_insurance": {"insurance", "medical_services"},
    "background_music": {"background_music", "retail_services"},
}

ARCHETYPE_FORBIDDEN_CAPABILITIES = {
    "competitor_research": {
        "advertising",
        "audience_targeting",
        "payment_acceptance",
        "employee_hiring",
        "customer_loyalty_program",
        "business_financing",
        "business_banking",
    },
    "market_research": {"advertising", "audience_targeting", "payment_acceptance", "employee_hiring"},
    "employee_hiring": {"advertising", "payment_acceptance", "customer_loyalty_program", "partner_matching"},
    "partner_matching": {"employee_hiring", "advertising", "payment_acceptance"},
    "payment_acceptance": {"advertising", "employee_hiring", "business_financing"},
    "launch_advertising": {"employee_hiring", "payment_acceptance", "business_financing"},
    "asset_realization": {
        "advertising",
        "audience_targeting",
        "employee_hiring",
        "payment_acceptance",
        "customer_loyalty_program",
        "competitor_analysis",
        "marketplace_analytics",
    },
    "staff_tips": {
        "advertising",
        "employee_hiring",
        "competitor_analysis",
        "business_financing",
        "business_analytics",
        "launch_advertising",
    },
    "tax_free_operations": {
        "advertising",
        "employee_hiring",
        "competitor_analysis",
        "business_financing",
        "employee_training",
        "launch_advertising",
    },
}

LEGACY_TAG_CAPABILITIES = {
    "hiring": {"employee_hiring"},
    "partner_search": {"partner_matching"},
    "customer_loyalty": {"customer_loyalty_program"},
    "employee_retention": {"employee_retention"},
    "employee_hr": {"hr_operations"},
    "training": {"employee_training"},
    "payments": {"payment_acceptance"},
    "finance": {"business_financing"},
    "marketing": {"advertising", "audience_targeting"},
    "analytics": {"business_analytics"},
    "banking": {"business_banking"},
}

_METADATA_CACHE = {"path": None, "mtime": None, "data": {}}


def metadata_paths():
    base = os.path.dirname(__file__)
    return [
        os.path.join(base, "data", "product_metadata.json"),
        "/app/data/product_metadata.json",
    ]


def load_product_metadata():
    path = next((item for item in metadata_paths() if os.path.exists(item)), None)
    if not path:
        return {}
    mtime = os.path.getmtime(path)
    cache = _METADATA_CACHE
    if cache["path"] == path and cache["mtime"] == mtime:
        return cache["data"]
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    cache["path"] = path
    cache["mtime"] = mtime
    cache["data"] = data
    return data


def clear_product_metadata_cache():
    _METADATA_CACHE["path"] = None
    _METADATA_CACHE["mtime"] = None
    _METADATA_CACHE["data"] = {}


def legacy_tags_to_capabilities(tags):
    capabilities = set()
    for tag in tags or []:
        capabilities.update(LEGACY_TAG_CAPABILITIES.get(tag, set()))
    return capabilities


def extract_step_capabilities(intent):
    return capabilities_from_text(intent.get("text") or "")


def extract_step_archetypes(intent):
    text = intent.get("text") or ""
    archetypes = set()
    for name, pattern in STEP_ARCHETYPE_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            archetypes.add(name)

    if re.search(r"исслед|изуч|анализ|сравн|обзор|собра.{0,12}дан", text) and re.search(r"конкурент", text):
        archetypes.add("competitor_research")
    if re.search(r"исслед|изуч|анализ", text) and re.search(r"рынок|рыночн", text):
        archetypes.add("market_research")

    if "competitor_research" in archetypes or "market_research" in archetypes:
        archetypes.discard("launch_advertising")
        archetypes.discard("improve_ad_performance")

    if re.search(r"маркетплейс|селлер", text):
        archetypes.add("marketplace_operations")

    return archetypes


def manual_metadata_paths():
    base = os.path.dirname(__file__)
    return [
        os.path.join(base, "data", "product_metadata_manual.json"),
        "/app/data/product_metadata_manual.json",
    ]


def load_manual_product_metadata():
    path = next((item for item in manual_metadata_paths() if os.path.exists(item)), None)
    if not path:
        return {"excluded": {}, "overrides": {}}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        "excluded": data.get("excluded") or {},
        "overrides": data.get("overrides") or {},
    }


def is_product_excluded(product):
    name = product.get("name") if isinstance(product, dict) else product
    return name in load_manual_product_metadata()["excluded"]


def get_product_profile(product, legacy_tags=None):
    if is_product_excluded(product):
        return {
            "capabilities": set(),
            "not_for": set(),
            "step_archetypes": set(),
            "source": "excluded",
            "excluded": True,
        }
    metadata = load_product_metadata()
    manual = load_manual_product_metadata()
    override = dict((metadata.get("products") or {}).get(product["name"]) or {})
    override.update(manual["overrides"].get(product["name"]) or {})
    inferred = legacy_tags_to_capabilities(legacy_tags or [])
    capabilities = set(override.get("capabilities") or []) or inferred
    return {
        "capabilities": capabilities,
        "not_for": set(override.get("not_for") or []),
        "step_archetypes": set(override.get("step_archetypes") or []),
        "source": "metadata" if override else "inferred",
        "excluded": bool(override.get("excluded")),
    }


def score_product_by_metadata(step, product, intent, legacy_tags):
    profile = get_product_profile(product, legacy_tags=legacy_tags)
    if profile.get("excluded"):
        return 0
    if not profile["capabilities"]:
        return None

    step_capabilities = extract_step_capabilities(intent)
    archetypes = extract_step_archetypes(intent)
    capabilities = profile["capabilities"]

    if not step_capabilities and not archetypes:
        return None

    if step_capabilities:
        if archetypes & profile["not_for"]:
            return 0

        overlap = capabilities & step_capabilities
        blocked = forbidden_product_capabilities(step_capabilities)
        if not overlap:
            if capabilities & blocked:
                return 0
            return 0

        if capabilities & blocked and len(overlap) <= 1 and "consumer_analytics" in capabilities:
            # Аналитика клиентов не закрывает казначейские/финансовые шаги сами по себе.
            if not (capabilities & step_capabilities - {"consumer_analytics"}):
                return 0

        score = len(overlap) * 18
        matched_archetypes = archetypes & profile["step_archetypes"]
        score += len(matched_archetypes) * 12

        required = set()
        for archetype in archetypes:
            required.update(ARCHETYPE_REQUIRED_CAPABILITIES.get(archetype, set()))
        if required:
            required_overlap = capabilities & required
            if archetypes and not required_overlap:
                return 0
            score += len(required_overlap) * 10
        return score

    score = 0

    if archetypes & profile["not_for"]:
        return 0

    forbidden = set()
    for archetype in archetypes:
        forbidden.update(ARCHETYPE_FORBIDDEN_CAPABILITIES.get(archetype, set()))
    if capabilities & forbidden and not (archetypes & profile["step_archetypes"]):
        return 0

    matched_archetypes = archetypes & profile["step_archetypes"]
    score += len(matched_archetypes) * 28

    required = set()
    for archetype in archetypes:
        required.update(ARCHETYPE_REQUIRED_CAPABILITIES.get(archetype, set()))

    if required:
        overlap = capabilities & required
        if archetypes and not overlap:
            return 0
        score += len(overlap) * 16
    elif profile["step_archetypes"]:
        score += 6

    if archetypes and profile["source"] == "metadata" and not matched_archetypes and not (capabilities & required):
        return max(score - 8, 0)

    return score
