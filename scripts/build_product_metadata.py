#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генерация product_metadata.json из каталога + веб-подтверждённые overrides."""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from capability_rules import CAPABILITY_RULES

PRODUCTS_PATH = os.path.join(ROOT, "data", "products.txt")
OUTPUT_PATH = os.path.join(ROOT, "data", "product_metadata.json")
REPORT_PATH = os.path.join(ROOT, "data", "product_metadata_report.json")
MANUAL_PATH = os.path.join(ROOT, "data", "product_metadata_manual.json")

# Подтверждено веб-поиском (официальные сайты / СберБизнес / вендор)
WEB_OVERRIDES = {
    "Сбер Таргет": {
        "capabilities": ["advertising", "audience_targeting", "consumer_analytics"],
        "web_refs": [
            "https://sbertarget.ru/",
            "https://www.sberbank.com/ru/s_m_business/nbs/sberlead",
        ],
        "web_note": "Запуск рекламных кампаний, таргетинг аудитории; аналитика ЦА, не исследование конкурентов.",
    },
    "MPSTATS — платформа для аналитики маркетплейсов": {
        "capabilities": ["marketplace_analytics", "competitor_analysis", "business_analytics"],
        "web_refs": ["https://mpstats.io/", "https://mpstats.io/instruments/analytics"],
        "web_note": "Аналитика WB/Ozon/ЯМ, конкуренты, ниши, цены, спрос.",
    },
    "СберАналитика": {
        "capabilities": ["business_analytics", "competitor_analysis", "geo_analytics", "consumer_analytics", "predictive_analytics"],
        "web_refs": [
            "https://www.sberbank.ru/ru/s_m_business/businessapps/analitika-dlya-biznesa",
            "https://sberanalytics.ru/products/panel_retail",
        ],
        "web_note": "Сравнение с конкурентами, геоаналитика, панели ритейла/ассортимента.",
    },
    "Аналитика бизнеса": {
        "capabilities": ["business_analytics"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/businessapps/analitika-dlya-biznesa"],
        "web_note": "Внутренняя бизнес-аналитика в СберБизнес.",
    },
    "HR-платформа Saby": {
        "capabilities": ["hr_operations", "employee_hiring", "employee_retention", "payroll"],
        "web_refs": ["https://saby.ru/staff", "https://saby.ru/staff/recruitment"],
        "web_note": "Кадры, зарплата, подбор, публикация вакансий, ЭДО.",
    },
    "HR-платформа": {
        "capabilities": ["hr_operations", "employee_hiring", "employee_retention", "payroll"],
        "web_refs": ["https://saby.ru/staff"],
        "web_note": "Saby HRM — кадры и подбор.",
    },
    "BenefittY – платформа управления программой лояльности": {
        "capabilities": ["customer_loyalty_program", "consumer_analytics"],
        "web_refs": ["https://benefitty.ru/about/"],
        "web_note": "SaaS программы лояльности, бонусы, акции, CRM клиентов.",
    },
    "SberCRM": {
        "capabilities": ["crm_sales", "business_analytics"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "CRM продаж и клиентов в экосистеме Сбера.",
    },
    "Работа.ру в составе пакета услуг": {
        "capabilities": ["employee_hiring"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Размещение вакансий и подбор через Работа.ру.",
    },
    "hh.ru – размещение вакансий": {
        "capabilities": ["employee_hiring"],
        "web_refs": ["https://hh.ru/"],
        "web_note": "Публикация вакансий на hh.ru.",
    },
    "Авито Работа – поиск сотрудников": {
        "capabilities": ["employee_hiring"],
        "web_refs": ["https://www.avito.ru/rabota"],
        "web_note": "Поиск сотрудников на Авито Работа.",
    },
    "СберПодбор": {
        "capabilities": ["employee_hiring"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Сервис подбора персонала Сбера.",
    },
    "Подбор партнёров": {
        "capabilities": ["partner_matching"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Поиск партнёров и подрядчиков, не найм сотрудников.",
    },
    "Услуги торгового эквайринга": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/bankingservice/acquiring"],
        "web_note": "Приём платежей через терминалы.",
    },
    "Услуги Интернет-эквайринга": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/bankingservice/internet-acquiring"],
        "web_note": "Онлайн-эквайринг.",
    },
    "Интернет-эквайринг": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/bankingservice/internet-acquiring"],
        "web_note": "Приём онлайн-платежей.",
    },
    "ЮKassa": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://yookassa.ru/"],
        "web_note": "Платёжный сервис для бизнеса.",
    },
    "SberPay QR": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business"],
        "web_note": "QR-платежи СберPay.",
    },
    "Прием оплаты по СБП": {
        "capabilities": ["payment_acceptance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business"],
        "web_note": "Приём платежей через СБП.",
    },
    "Геоаналитика Bestplace": {
        "capabilities": ["geo_analytics", "business_analytics"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Геоаналитика локаций (часть СберАналитики).",
    },
    "Сегментация аудитории и таргетинг": {
        "capabilities": ["advertising", "audience_targeting"],
        "web_refs": ["https://sbertarget.ru/"],
        "web_note": "Таргетинг и сегментация для рекламы.",
    },
    "Геоконтекстная реклама": {
        "capabilities": ["advertising", "audience_targeting"],
        "web_refs": ["https://sbertarget.ru/"],
        "web_note": "Геоконтекстная реклама.",
    },
    "Рекламные услуги": {
        "capabilities": ["advertising"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Рекламные услуги Сбера.",
    },
    "Рекламная платформа": {
        "capabilities": ["advertising", "audience_targeting"],
        "web_refs": ["https://sbertarget.ru/"],
        "web_note": "Рекламная платформа.",
    },
    "Программа лояльности \"СберБизнес Спасибо\"": {
        "capabilities": ["customer_loyalty_program"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Лояльность для бизнес-клиентов.",
    },
    "СберСпасибо – программа лояльности для бизнеса": {
        "capabilities": ["customer_loyalty_program"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Программа лояльности СберСпасибо для бизнеса.",
    },
    "TravelLine - Платформа управления гостиничным бизнесом": {
        "capabilities": ["hotel_management", "business_analytics"],
        "web_refs": ["https://www.travelline.ru/"],
        "web_note": "PMS для отелей: бронирование, аналитика.",
    },
    "Yclients - Онлайн-запись и автоматизация": {
        "capabilities": ["online_booking", "crm_sales"],
        "web_refs": ["https://www.yclients.com/"],
        "web_note": "Онлайн-запись и автоматизация для сферы услуг.",
    },
    "Process Mining от Сбера": {
        "capabilities": ["business_analytics", "operational_efficiency"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Анализ и оптимизация бизнес-процессов.",
    },
    "СберБизнес.Giga-assistant": {
        "capabilities": ["ai_assistant", "business_automation"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "ИИ-ассистент для бизнес-задач.",
    },
    "Регистрация Бизнеса": {
        "capabilities": ["business_registration"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Регистрация ИП/ООО через СберБизнес.",
    },
    "Юрист для бизнеса": {
        "capabilities": ["legal_support"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Юридические консультации для МСБ.",
    },
    "Юрист для бизнеса в составе пакета услуг": {
        "capabilities": ["legal_support"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Юрист для бизнеса в пакете.",
    },
    "Портал бухгалтера": {
        "capabilities": ["accounting", "tax_reporting"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Портал для бухгалтеров.",
    },
    "Моя бухгалтерия Онлайн": {
        "capabilities": ["accounting", "tax_reporting"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Онлайн-бухгалтерия.",
    },
    "Бухгалтерия для ИП": {
        "capabilities": ["accounting", "tax_reporting"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Бухгалтерия для ИП.",
    },
    "Сайт-визитка от нейросети": {
        "capabilities": ["website_creation"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Генерация сайта-визитки нейросетью.",
    },
    "Купер B2B": {
        "capabilities": ["procurement_platform"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "B2B-закупки (Купер для бизнеса).",
    },
    "Контрагент-профи": {
        "capabilities": ["counterparty_check", "risk_management"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Проверка контрагентов.",
    },
    "Комплаенс-помощник": {
        "capabilities": ["compliance"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Помощник по комплаенсу.",
    },
    "Электронный документооборот": {
        "capabilities": ["electronic_document_flow"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "ЭДО и отчётность.",
    },
    "Электронная подпись": {
        "capabilities": ["electronic_signature"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Электронная подпись для бизнеса.",
    },
    "Расчетные счета ЮЛ/ИП": {
        "capabilities": ["business_banking"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business"],
        "web_note": "РКО для ЮЛ и ИП.",
    },
    "Расчетное обслуживание": {
        "capabilities": ["business_banking"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business"],
        "web_note": "Расчётное обслуживание.",
    },
    "Консоль - работа с самозанятыми": {
        "capabilities": ["self_employed_management", "payroll"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Выплаты и учёт самозанятых.",
    },
    "Зачисление выплат самозанятым": {
        "capabilities": ["self_employed_management", "payroll"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Зачисление выплат самозанятым.",
    },
    "SaluteJazz": {
        "capabilities": ["video_conferencing", "business_automation"],
        "web_refs": ["https://developers.sber.ru/portal/products/jazz-by-sber"],
        "web_note": "ВКС, вебинары, корпоративные коммуникации.",
    },
    "GigaCode": {
        "capabilities": ["ai_assistant", "it_outsourcing"],
        "web_refs": ["https://platformv.sbertech.ru/products/instrumenty-razrabotchika/giga-code"],
        "web_note": "AI-ассистент разработчика.",
    },
    "Лизинг": {
        "capabilities": ["leasing_financing", "business_financing"],
        "web_refs": ["https://www.sberleasing.ru/"],
        "web_note": "Финансовая аренда техники и оборудования.",
    },
    "21. Финансирование операций корпоративного лизинга": {
        "capabilities": ["leasing_financing", "business_financing"],
        "web_refs": ["https://www.sberleasing.ru/"],
        "web_note": "Финансирование корпоративного лизинга.",
    },
    "VS Robotics: голосовой робот и речевая аналитика для бизнеса": {
        "capabilities": ["voice_robot", "speech_analytics", "business_automation"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Голосовой робот и речевая аналитика.",
    },
    "Робот-оператор": {
        "capabilities": ["voice_robot", "customer_service_automation"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Голосовой робот-оператор.",
    },
    "Виртуальный оператор": {
        "capabilities": ["voice_robot", "customer_service_automation"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Виртуальный оператор.",
    },
    "Речевая аналитика": {
        "capabilities": ["speech_analytics", "business_analytics"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/nbs"],
        "web_note": "Анализ телефонных разговоров.",
    },
    "Toka – сервис для управления рестораном": {
        "capabilities": ["restaurant_automation", "business_analytics"],
        "web_refs": ["https://toka.rest/"],
        "web_note": "Автоматизация ресторана.",
    },
    "Saby Presto": {
        "capabilities": ["restaurant_automation"],
        "web_refs": ["https://saby.ru/presto"],
        "web_note": "Автоматизация HoReCa.",
    },
    "Doma.ai": {
        "capabilities": ["real_estate_services", "construction_digital"],
        "web_refs": ["https://doma.ai/"],
        "web_note": "Платформа ЖКХ и недвижимости.",
    },
    "Аналитика потребителя": {
        "capabilities": ["consumer_analytics", "business_analytics"],
        "web_refs": ["https://www.sberbank.ru/ru/s_m_business/businessapps/analitika-dlya-biznesa"],
        "web_note": "Аналитика поведения потребителей.",
    },
    "Аналитика ассортимента": {
        "capabilities": ["business_analytics", "consumer_analytics"],
        "web_refs": ["https://sberanalytics.ru/products/panel_retail"],
        "web_note": "Панель ассортимента.",
    },
    "Панель Туризм": {
        "capabilities": ["business_analytics"],
        "web_refs": ["https://sberanalytics.ru/"],
        "web_note": "Аналитика туристического рынка.",
    },
}

CAPABILITY_ARCHETYPES = {
    "advertising": ["launch_advertising", "improve_ad_performance"],
    "audience_targeting": ["launch_advertising", "improve_ad_performance"],
    "competitor_analysis": ["competitor_research", "market_research"],
    "marketplace_analytics": ["competitor_research", "market_research", "marketplace_operations"],
    "business_analytics": ["business_analytics", "market_research", "competitor_research"],
    "consumer_analytics": ["market_research", "business_analytics"],
    "predictive_analytics": ["business_analytics", "market_research"],
    "geo_analytics": ["market_research", "competitor_research"],
    "employee_hiring": ["employee_hiring"],
    "partner_matching": ["partner_matching"],
    "hr_operations": ["employee_retention", "employee_hiring"],
    "employee_retention": ["employee_retention"],
    "payroll": ["employee_retention"],
    "employee_training": ["employee_training"],
    "customer_loyalty_program": ["customer_loyalty_program"],
    "payment_acceptance": ["payment_acceptance"],
    "business_financing": ["business_financing"],
    "business_banking": ["business_banking"],
    "bank_guarantee": ["business_financing", "tender_participation"],
    "factoring": ["business_financing"],
    "leasing_financing": ["business_financing"],
    "currency_hedging": ["business_financing"],
    "insurance": ["risk_management"],
    "legal_support": ["legal_support"],
    "accounting": ["accounting_tax"],
    "tax_reporting": ["accounting_tax"],
    "electronic_document_flow": ["accounting_tax"],
    "electronic_signature": ["accounting_tax"],
    "website_creation": ["website_creation"],
    "crm_sales": ["sales_management"],
    "sales_management": ["sales_management"],
    "online_booking": ["customer_service_automation"],
    "hotel_management": ["hotel_operations"],
    "restaurant_automation": ["restaurant_operations"],
    "marketplace_onboarding": ["marketplace_operations"],
    "marketplace_operations": ["marketplace_operations"],
    "construction_supervision": ["construction_management"],
    "construction_digital": ["construction_management"],
    "developer_financing": ["business_financing"],
    "cash_collection": ["cash_operations"],
    "self_collection": ["cash_operations"],
    "international_payments": ["international_trade"],
    "currency_control": ["international_trade"],
    "ved_consulting": ["international_trade"],
    "procurement_platform": ["procurement"],
    "tender_platform": ["tender_participation"],
    "tender_guarantee": ["tender_participation", "business_financing"],
    "ai_assistant": ["business_automation"],
    "business_automation": ["business_automation"],
    "chatbots": ["customer_service_automation"],
    "voice_robot": ["customer_service_automation"],
    "speech_analytics": ["business_analytics"],
    "cloud_infrastructure": ["it_infrastructure"],
    "it_outsourcing": ["it_infrastructure"],
    "cybersecurity": ["it_security"],
    "compliance": ["compliance"],
    "risk_management": ["risk_management"],
    "counterparty_check": ["risk_management"],
    "business_registration": ["business_registration"],
    "self_employed_management": ["self_employed_management"],
    "lead_generation": ["sales_management", "launch_advertising"],
    "operational_efficiency": ["operational_efficiency"],
    "corporate_training": ["employee_training"],
    "digital_learning": ["employee_training"],
    "medical_services": ["employee_wellness"],
    "real_estate_services": ["real_estate"],
    "real_estate_analytics": ["market_research", "real_estate"],
    "logistics": ["logistics"],
    "video_conferencing": ["business_automation"],
    "premium_banking": ["business_banking"],
    "deposits": ["treasury"],
    "securities": ["treasury"],
    "custom_projects": ["custom_solutions"],
    "catch_all_banking": ["business_financing", "business_banking"],
    "catch_all_partner": ["business_automation"],
    "asset_realization": ["asset_realization", "real_estate"],
    "tips_service": ["staff_tips", "payment_acceptance"],
    "tax_refund_service": ["tax_free_operations", "retail_services"],
    "background_music": ["background_music", "retail_services"],
    "retail_services": ["retail_services"],
}

NOT_FOR_BY_CAPABILITY = {
    "advertising": ["competitor_research", "employee_hiring", "payment_acceptance", "business_financing"],
    "audience_targeting": ["competitor_research", "employee_hiring", "payment_acceptance"],
    "payment_acceptance": ["competitor_research", "employee_hiring", "launch_advertising", "business_financing"],
    "employee_hiring": ["competitor_research", "launch_advertising", "payment_acceptance", "partner_matching"],
    "partner_matching": ["employee_hiring", "launch_advertising", "payment_acceptance"],
    "customer_loyalty_program": ["employee_hiring", "competitor_research", "business_financing"],
    "business_financing": ["competitor_research", "launch_advertising", "employee_hiring"],
    "bank_guarantee": ["competitor_research", "launch_advertising", "employee_hiring"],
    "business_banking": ["competitor_research", "launch_advertising", "employee_hiring"],
    "competitor_analysis": ["launch_advertising", "employee_hiring", "payment_acceptance"],
    "marketplace_analytics": ["employee_hiring", "legal_support"],
    "insurance": ["competitor_research", "launch_advertising"],
    "legal_support": ["competitor_research", "launch_advertising", "payment_acceptance"],
    "accounting": ["competitor_research", "launch_advertising", "employee_hiring"],
    "construction_supervision": ["launch_advertising", "customer_loyalty_program"],
}

CLASSIFICATION_RULES = CAPABILITY_RULES

VAGUE_NAME_PATTERNS = [
    r"^ку ",
    r"^прочее",
    r"^все остальные",
    r"^индивидуальные проекты$",
    r"^listim$",
    r"^ладошки$",
    r"^визирь$",
    r"^нестор\.",
    r"^smart logger$",
    r"^портал da$",
    r"^sberunity$",
    r"^amelia",
    r"^gigacode$",
    r"^корпоративное развитие",
    r"альбом №",
]


def load_products():
    with open(PRODUCTS_PATH, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        items = []
        for row in reader:
            name = (row.get("Название продукта") or "").strip()
            if not name:
                continue
            items.append({
                "name": name,
                "description": (row.get("Что делает продукт/сервис") or "").strip(),
                "problems": (row.get("Какие проблемы помогает решить") or "").strip(),
            })
        return items


def normalize_blob(product):
    return " ".join([product["name"], product["description"], product["problems"]]).lower().replace("ё", "е")


def classify_from_catalog(product):
    blob = normalize_blob(product)
    caps = set()
    for pattern, capabilities in CLASSIFICATION_RULES:
        if re.search(pattern, blob, re.IGNORECASE):
            caps.update(capabilities)
    if not caps:
        if re.search(r"консультац", blob):
            caps.add("operational_efficiency")
        elif re.search(r"пакет услуг|активац", blob):
            caps.add("catch_all_partner")
        elif re.search(r"брокер", blob):
            caps.add("insurance") if re.search(r"страх|осаго|каско|дмс", blob) else caps.add("business_financing")
        else:
            caps.add("custom_projects")
    return sorted(caps)


def archetypes_for_capabilities(caps):
    result = set()
    for cap in caps:
        result.update(CAPABILITY_ARCHETYPES.get(cap, []))
    return sorted(result)


def not_for_capabilities(caps):
    result = set()
    for cap in caps:
        result.update(NOT_FOR_BY_CAPABILITY.get(cap, []))
    archetypes = set(archetypes_for_capabilities(caps))
    result -= archetypes
    return sorted(result)


def is_vague_for_web(product):
    blob = f"{product['name']} {product['description']}".lower()
    if len(product["description"]) < 12:
        return True
    return any(re.search(p, blob, re.IGNORECASE) for p in VAGUE_NAME_PATTERNS)


def load_manual_decisions():
    if not os.path.exists(MANUAL_PATH):
        return {"excluded": {}, "overrides": {}}
    with open(MANUAL_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        "excluded": data.get("excluded") or {},
        "overrides": data.get("overrides") or {},
    }


def build_excluded_entry(reason):
    return {
        "capabilities": [],
        "not_for": [],
        "step_archetypes": [],
        "source": "manual_excluded",
        "web_status": "excluded",
        "web_note": reason,
        "excluded": True,
    }


def build_entry(product, existing_manual=None, excluded_reason=None):
    name = product["name"]
    if excluded_reason:
        return build_excluded_entry(excluded_reason)

    web = WEB_OVERRIDES.get(name)
    catalog_caps = classify_from_catalog(product)

    if existing_manual:
        caps = existing_manual.get("capabilities") or catalog_caps
        source = existing_manual.get("source", "manual")
        web_refs = existing_manual.get("web_refs", [])
        web_status = existing_manual.get("web_status", "manual")
        web_note = existing_manual.get("web_note", "")
    elif web:
        caps = web["capabilities"]
        source = "web+catalog" if set(catalog_caps) & set(web["capabilities"]) else "web"
        web_refs = web.get("web_refs", [])
        web_status = "found"
        web_note = web.get("web_note", "")
    else:
        caps = catalog_caps
        source = "catalog"
        web_refs = []
        web_status = "not_found" if is_vague_for_web(product) else "catalog_inferred"
        web_note = ""

    not_for = (
        existing_manual.get("not_for")
        if existing_manual and existing_manual.get("not_for") is not None
        else not_for_capabilities(caps)
    )
    step_archetypes = (
        existing_manual.get("step_archetypes")
        if existing_manual and existing_manual.get("step_archetypes") is not None
        else archetypes_for_capabilities(caps)
    )

    entry = {
        "capabilities": caps,
        "not_for": not_for,
        "step_archetypes": step_archetypes,
        "source": source,
        "web_status": web_status,
    }
    if web_refs:
        entry["web_refs"] = web_refs
    if web_note:
        entry["web_note"] = web_note
    return entry


def main():
    manual_decisions = load_manual_decisions()
    manual_excluded = manual_decisions["excluded"]
    manual_overrides = manual_decisions["overrides"]

    products = load_products()
    built = {}
    report = {
        "web_found": [],
        "catalog_inferred": [],
        "not_found_online": [],
        "manual_overrides": [],
        "manual_excluded": [],
    }

    for product in products:
        name = product["name"]
        if name in manual_excluded:
            entry = build_entry(product, excluded_reason=manual_excluded[name])
            built[name] = entry
            report["manual_excluded"].append({"name": name, "reason": manual_excluded[name]})
            continue

        manual = manual_overrides.get(name)
        entry = build_entry(product, existing_manual=manual)
        built[name] = entry

        status = entry["web_status"]
        if manual:
            report["manual_overrides"].append(name)
        elif status == "found":
            report["web_found"].append(name)
        elif status == "not_found":
            report["not_found_online"].append({
                "name": name,
                "description": product["description"],
                "inferred_capabilities": entry["capabilities"],
            })
        else:
            report["catalog_inferred"].append(name)

    default_glossary_caps = {
            "advertising": "Размещение и ведение рекламных кампаний",
            "audience_targeting": "Сегментация аудитории и таргетированное продвижение",
            "business_analytics": "Бизнес-аналитика, отчёты, дашборды",
            "consumer_analytics": "Аналитика поведения и сегментов клиентов",
            "competitor_analysis": "Анализ конкурентов и конкурентной среды",
            "marketplace_analytics": "Аналитика маркетплейсов и конкурентов на площадках",
            "marketplace_onboarding": "Вывод и сопровождение продаж на маркетплейсах",
            "predictive_analytics": "Прогнозирование и предиктивная аналитика",
            "geo_analytics": "Геоаналитика и анализ локаций",
            "employee_hiring": "Подбор и найм сотрудников",
            "partner_matching": "Поиск партнёров и подрядчиков",
            "hr_operations": "Кадровый учёт, HR-процессы, зарплата",
            "employee_retention": "Удержание и вовлечённость сотрудников",
            "payroll": "Расчёт зарплаты и выплат",
            "employee_training": "Обучение и развитие сотрудников",
            "customer_loyalty_program": "Программы лояльности для клиентов",
            "payment_acceptance": "Приём безналичных и онлайн-платежей",
            "business_financing": "Кредиты, гарантии, факторинг, лизинг",
            "business_banking": "РКО и расчётные операции",
            "bank_guarantee": "Банковские гарантии",
            "factoring": "Факторинг дебиторской задолженности",
            "leasing_financing": "Лизинговое финансирование",
            "currency_hedging": "Валютное хеджирование",
            "insurance": "Страхование и защита",
            "legal_support": "Юридическая поддержка",
            "accounting": "Бухгалтерский учёт",
            "tax_reporting": "Налоговая отчётность",
            "electronic_document_flow": "Электронный документооборот",
            "electronic_signature": "Электронная подпись",
            "website_creation": "Создание сайтов",
            "crm_sales": "CRM и управление продажами",
            "sales_management": "Управление продажами",
            "online_booking": "Онлайн-запись клиентов",
            "hotel_management": "Управление отелем",
            "restaurant_automation": "Автоматизация ресторанов",
            "construction_supervision": "Строительный инжиниринг и сопровождение",
            "real_estate_services": "Услуги по недвижимости",
            "real_estate_analytics": "Аналитика недвижимости",
            "logistics": "Логистика и транспорт",
            "ai_assistant": "ИИ-ассистенты и автоматизация",
            "business_automation": "Автоматизация бизнес-процессов",
            "cloud_infrastructure": "Облачная инфраструктура",
            "it_outsourcing": "ИТ-аутсорсинг",
            "cybersecurity": "Кибербезопасность",
            "compliance": "Комплаенс",
            "risk_management": "Управление рисками",
            "counterparty_check": "Проверка контрагентов",
            "business_registration": "Регистрация бизнеса",
            "self_employed_management": "Работа с самозанятыми",
            "procurement_platform": "B2B-закупки",
            "tender_platform": "Электронные торги и тендеры",
            "tender_participation": "Участие в тендерах",
            "operational_efficiency": "Повышение операционной эффективности",
            "international_payments": "Международные расчёты",
            "currency_control": "Валютный контроль",
            "ved_consulting": "Консалтинг по ВЭД",
            "cash_collection": "Инкассация",
            "self_collection": "Самоинкассация",
            "securities": "Операции с ценными бумагами",
            "treasury": "Казначейство и размещение средств",
            "premium_banking": "Премиальное обслуживание",
            "medical_services": "Медицина и здоровье сотрудников",
            "video_conferencing": "Видеоконференции",
            "lead_generation": "Генерация лидов",
            "speech_analytics": "Речевая аналитика",
            "custom_projects": "Индивидуальные/нестандартные решения",
            "catch_all_banking": "Обобщённый банковский продукт",
            "catch_all_partner": "Партнёрский сервис общего назначения",
            "asset_realization": "Реализация залогового имущества",
            "tips_service": "Сервис безналичных чаевых",
            "tax_refund_service": "Tax Free / возврат НДС иностранцам",
            "background_music": "Фоновая музыка для бизнеса",
            "retail_services": "Сервисы для розничной точки",
            "developer_financing": "Проектное финансирование девелоперов",
        }
    glossary_caps = dict(default_glossary_caps)
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as handle:
            glossary_caps.update(json.load(handle).get("capabilities_glossary") or {})
    glossary_caps.update(default_glossary_caps)

    default_archetypes_glossary = {
            "competitor_research": "Исследование конкурентов",
            "market_research": "Исследование рынка",
            "launch_advertising": "Запуск рекламы",
            "improve_ad_performance": "Повышение эффективности рекламы",
            "employee_hiring": "Подбор персонала",
            "partner_matching": "Поиск партнёров",
            "customer_loyalty_program": "Лояльность клиентов",
            "employee_retention": "Удержание сотрудников",
            "payment_acceptance": "Приём платежей",
            "business_financing": "Финансирование",
            "business_banking": "РКО и расчёты",
            "business_analytics": "Бизнес-аналитика",
            "marketplace_operations": "Работа на маркетплейсах",
            "accounting_tax": "Бухгалтерия и налоги",
            "legal_support": "Юридическая поддержка",
            "construction_management": "Управление строительством",
            "restaurant_operations": "Ресторанный бизнес",
            "hotel_operations": "Гостиничный бизнес",
            "sales_management": "Управление продажами",
            "employee_training": "Обучение сотрудников",
            "international_trade": "ВЭД и международная торговля",
            "tender_participation": "Тендеры и госзакупки",
            "risk_management": "Управление рисками",
            "it_infrastructure": "ИТ-инфраструктура",
            "it_security": "ИБ",
            "business_automation": "Автоматизация",
            "customer_service_automation": "Автоматизация обслуживания клиентов",
            "website_creation": "Создание сайта",
            "procurement": "Закупки",
            "real_estate": "Недвижимость",
            "logistics": "Логистика",
            "cash_operations": "Работа с наличными",
            "treasury": "Казначейство",
            "employee_wellness": "Здоровье сотрудников",
            "compliance": "Комплаенс",
            "self_employed_management": "Самозанятые",
            "business_registration": "Регистрация бизнеса",
            "operational_efficiency": "Операционная эффективность",
            "strategic_consulting": "Стратегический консалтинг",
            "custom_solutions": "Кастомные решения",
            "asset_realization": "Реализация активов",
            "staff_tips": "Чаевые персоналу",
            "tax_free_operations": "Tax Free для иностранных покупателей",
            "background_music": "Фоновая музыка",
            "retail_services": "Сервисы для розницы",
        }
    archetypes_glossary = dict(default_archetypes_glossary)
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as handle:
            archetypes_glossary.update(json.load(handle).get("step_archetypes_glossary") or {})
    archetypes_glossary.update(default_archetypes_glossary)

    output = {
        "version": 2,
        "capabilities_glossary": glossary_caps,
        "step_archetypes_glossary": archetypes_glossary,
        "products": built,
        "stats": {
            "total": len(built),
            "web_found": len(report["web_found"]),
            "catalog_inferred": len(report["catalog_inferred"]),
            "not_found_online": len(report["not_found_online"]),
            "manual_overrides": len(report["manual_overrides"]),
            "manual_excluded": len(report["manual_excluded"]),
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(f"Wrote {len(built)} products to {OUTPUT_PATH}")
    print(
        "web_found={web_found}, catalog_inferred={catalog_inferred}, "
        "not_found_online={not_found_online}, manual_overrides={manual_overrides}, "
        "manual_excluded={manual_excluded}".format(**output["stats"])
    )


if __name__ == "__main__":
    main()
