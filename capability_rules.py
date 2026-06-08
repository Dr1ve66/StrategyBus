# -*- coding: utf-8 -*-
"""Единые правила capability для классификации продуктов и смысла шагов."""
import re

# Паттерн текста шага/продукта → набор capabilities.
# Используется и при генерации product_metadata.json, и при подборе продукта к шагу.
CAPABILITY_RULES = [
    (r"таргет|реклам|продвижен|геоконтекст", ["advertising", "audience_targeting"]),
    (r"конкурент|маркетплейс|mpstats|wildberries|ozon|селлер|яндекс.?маркет|мегамаркет", ["marketplace_analytics", "competitor_analysis"]),
    (r"аналитик|дашборд|мониторинг|прогноз|process mining|speech|речев", ["business_analytics"]),
    (r"потребител|клиент.*поведен", ["consumer_analytics"]),
    (r"геоаналитик|геоинформ|bestplace|локац", ["geo_analytics"]),
    (r"подбор партнер|поиск партнер|подбор подрядчик|поиск подрядчик|подбор вендор|поиск поставщик", ["partner_matching"]),
    (r"работа\.?ру|hh\.?ru|авито работа|сберподбор|ваканс|рекрут|найм|подбор персонал|подбор сотрудник", ["employee_hiring"]),
    (r"\bhr\b|кадр|зарплат|(?<![а-я])персонал(?!из)|saby|work.?life|текуч|удержан.*сотрудник", ["hr_operations", "employee_retention"]),
    (r"лояльност|спасибо|benefitty|бонус.*клиент", ["customer_loyalty_program"]),
    (r"эквайринг|pos|терминал|юkassa|sberpay|сбп|прием платеж|приём платеж|онлайн.?платеж", ["payment_acceptance"]),
    (r"факторинг", ["factoring", "business_financing"]),
    (r"лизинг", ["leasing_financing", "business_financing"]),
    (r"гарант", ["bank_guarantee", "business_financing"]),
    (r"кредит|овердрафт|финансир|заем|заём|эскроу|ипотек|залог.*недвижим", ["business_financing"]),
    (r"депозит|вексел|облигаци|репо|ценн.*бумаг|размещен.{0,16}средств|свободн.{0,12}средств", ["deposits", "securities", "treasury", "business_financing"]),
    (r"банк.{0,24}(депозит|средств|размещ)|депозит.{0,24}банк|потенциальн.{0,16}банк|сравн.{0,12}банк", ["deposits", "treasury", "business_banking"]),
    (r"валютн|форвард|своп|хедж", ["currency_hedging", "business_financing"]),
    (r"аккредитив", ["business_financing", "international_payments"]),
    (r"рко|расчетн|расчётн|расчетн.*счет|расчётн.*счёт|дбо|неснижаем", ["business_banking"]),
    (r"инкассац|самоинкассац", ["cash_collection", "self_collection"]),
    (r"страхован|каско|осаго|дмс|защита\+|личн.*защит", ["insurance"]),
    (r"юрист|правосуд|медиац|банкрот", ["legal_support"]),
    (r"бухгалтер|усн|отчетност|отчётност|налог", ["accounting", "tax_reporting"]),
    (r"эдо|документооборот|электронн.*подпис", ["electronic_document_flow", "electronic_signature"]),
    (r"сайт|визитк", ["website_creation"]),
    (r"crm|управлени.*продаж|лид", ["crm_sales", "sales_management"]),
    (r"travelline|отел|гостиниц", ["hotel_management"]),
    (r"toka|presto|ресторан|horeca", ["restaurant_automation"]),
    (r"yclients|онлайн.?запис", ["online_booking"]),
    (r"вывод на|ecom\.|маркетплейс", ["marketplace_onboarding", "marketplace_operations"]),
    (r"строител|инжиниринг|капремонт|жкх|девелоп|doma\.ai|недвижим", ["construction_supervision", "real_estate_services"]),
    (r"логист|транспорт|доставк|склад", ["logistics"]),
    (r"обучен|образован|сберуниверситет|алгоритмик|интенсив", ["employee_training", "corporate_training"]),
    (r"ии|ai|нейросет|giga|чат.?бот|робот.?оператор|виртуальн.*оператор|голосов", ["ai_assistant", "business_automation"]),
    (r"облачн|cloud\.ru|platformv|ит.?аутсорс|кибербезопас", ["cloud_infrastructure", "it_outsourcing", "cybersecurity"]),
    (r"комплаенс|контрагент|риск", ["compliance", "counterparty_check", "risk_management"]),
    (r"регистрац.*бизнес|легкий старт|бизнес.?старт", ["business_registration"]),
    (r"самозанят", ["self_employed_management"]),
    (r"тендер|торг|госзакуп|электронн.*площадк|сбер а", ["tender_platform", "tender_participation"]),
    (r"консалтинг|диагностик|эффективност", ["operational_efficiency", "strategic_consulting"]),
    (r"видеоконференц|salutejazz", ["video_conferencing"]),
    (r"премиум|прайм|премьер|первый", ["premium_banking"]),
    (r"вэд|валютн.*контрол|таможен|международн", ["international_payments", "currency_control", "ved_consulting"]),
    (r"закуп|купер|procurement", ["procurement_platform"]),
    (r"медицин|здоров|телемед|врач", ["medical_services"]),
    (r"все остальные гарантии|все остальные партнер|индивидуальные проекты.*прочее", ["catch_all_banking"]),
]

# Если шаг явно про capability слева, продукты только с capability справа — отклоняются
# (если нет пересечения с требуемыми capabilities шага).
FORBIDDEN_PRODUCT_CAPS_WHEN_STEP_HAS = {
    "treasury": {
        "customer_loyalty_program", "employee_hiring", "advertising", "audience_targeting",
        "restaurant_automation", "hotel_management", "online_booking", "employee_training",
    },
    "deposits": {
        "customer_loyalty_program", "employee_hiring", "advertising", "audience_targeting",
        "restaurant_automation", "hotel_management",
    },
    "securities": {
        "customer_loyalty_program", "employee_hiring", "advertising", "audience_targeting",
    },
    "business_banking": {
        "customer_loyalty_program", "employee_hiring", "advertising", "restaurant_automation",
    },
    "employee_hiring": {
        "customer_loyalty_program", "treasury", "deposits", "payment_acceptance", "advertising",
    },
    "customer_loyalty_program": {
        "employee_hiring", "treasury", "deposits", "bank_guarantee", "factoring",
    },
    "payment_acceptance": {
        "employee_hiring", "treasury", "deposits", "employee_training",
    },
    "competitor_analysis": {
        "customer_loyalty_program", "employee_hiring", "payment_acceptance", "treasury",
    },
    "advertising": {
        "treasury", "deposits", "employee_hiring", "bank_guarantee",
    },
    "insurance": {
        "customer_loyalty_program", "advertising", "marketplace_analytics",
    },
    "legal_support": {
        "customer_loyalty_program", "advertising", "payment_acceptance",
    },
}

# «Подбор» в контексте банков/депозитов/партнёров — не HR-найм.
NON_HIRING_CONTEXT_PATTERN = re.compile(
    r"банк|депозит|средств|партнер|подрядчик|вендор|поставщик|контрагент",
    re.IGNORECASE,
)


def normalize_match_text(text):
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def capabilities_from_text(text):
    normalized = normalize_match_text(text)
    capabilities = set()
    for pattern, items in CAPABILITY_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            capabilities.update(items)
    return capabilities


def forbidden_product_capabilities(step_capabilities):
    forbidden = set()
    for capability in step_capabilities:
        forbidden.update(FORBIDDEN_PRODUCT_CAPS_WHEN_STEP_HAS.get(capability, set()))
    return forbidden


def is_hiring_context(text):
    normalized = normalize_match_text(text)
    if not re.search(r"подбор|найм|ваканс|рекрут", normalized):
        return False
    if NON_HIRING_CONTEXT_PATTERN.search(normalized):
        return False
    return True
