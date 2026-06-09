# -*- coding: utf-8 -*-
import csv
import json
import math
import re
import os
import uuid
from io import BytesIO
from datetime import datetime
from functools import wraps
from types import SimpleNamespace
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, LongTable

load_dotenv()
from situation_validation import (
    SITUATION_REQUIRED_SLOTS,
    SITUATION_SLOT_LABELS,
    SITUATION_SOFT_LENGTH,
    SITUATION_HARD_LENGTH,
    build_situation_from_focus,
    build_situation_from_slots,
    call_situation_check as analyze_situation_slots,
    load_situation_clarify_state,
    load_situation_focus_question,
    load_situation_slot_questions,
    load_situation_slots_filled,
)
from capability_rules import is_hiring_context
from product_metadata import (
    clear_product_metadata_cache,
    extract_step_archetypes,
    extract_step_capabilities,
    is_product_excluded,
    score_product_by_metadata,
)

db = SQLAlchemy()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET") or "missing-secret-key-change-me"
    
    os.makedirs(os.path.join(app.root_path, "data"), exist_ok=True)
    default_db_path = os.path.join(app.root_path, "data", "app.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("APP_DATABASE_URL", f"sqlite:///{default_db_path}")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_PARTITIONED"] = True

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_situation_check_schema()
        ensure_agent2_product_column()
        ensure_industry_check_schema()
        # Блокируем все legacy-учётки "admin" (созданные до системы регистрации).
        # Обнуляем password_hash → login_required не пропустит (проверка if user.password_hash).
        # Данные (UserInput и всё связанное) остаются нетронутыми.
        from sqlalchemy import text
        db.session.execute(
            text("UPDATE users SET password_hash = NULL WHERE username = 'admin' AND (email IS NULL OR email = '')")
        )
        db.session.commit()

    register_routes(app)
    register_template_helpers(app)
    return app

# === MODELS ===
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=True, unique=True)
    password_hash = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True, default="unknown")
    registered_at = db.Column(db.DateTime, nullable=True)
    first_login_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    login_count = db.Column(db.Integer, nullable=False, default=1)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False

class UserInput(db.Model):
    __tablename__ = "user_inputs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    input_text = db.Column(db.Text, nullable=False)
    session_token = db.Column(db.String(36), unique=True, nullable=False)
    user = db.relationship("User", backref="inputs")

class Agent1Response(db.Model):
    __tablename__ = "agent1_responses"
    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    item_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    logic = db.Column(db.Text, nullable=False)
    criteria = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    implemented = db.Column(db.Boolean, default=False)
    rejection_reason = db.Column(db.Text, nullable=True)
    user_input = db.relationship("UserInput", backref="agent1_responses")

class Agent1Edit(db.Model):
    __tablename__ = "agent1_edits"
    id = db.Column(db.Integer, primary_key=True)
    agent1_response_id = db.Column(db.Integer, db.ForeignKey("agent1_responses.id"), unique=True, nullable=False)
    edited_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    original_title = db.Column(db.Text, nullable=False)
    original_description = db.Column(db.Text, nullable=False)
    original_logic = db.Column(db.Text, nullable=False)
    original_criteria = db.Column(db.Text, nullable=False)
    edited_title = db.Column(db.Text, nullable=False)
    edited_description = db.Column(db.Text, nullable=False)
    edited_logic = db.Column(db.Text, nullable=False)
    edited_criteria = db.Column(db.Text, nullable=False)
    response = db.relationship("Agent1Response", backref=db.backref("edit", uselist=False))

class Agent1Selected(db.Model):
    __tablename__ = "agent1_selected"
    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"), nullable=False)
    agent1_response_id = db.Column(db.Integer, db.ForeignKey("agent1_responses.id"), nullable=False)
    selected_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    final_title = db.Column(db.Text, nullable=False)
    final_description = db.Column(db.Text, nullable=False)
    final_logic = db.Column(db.Text, nullable=False)
    final_criteria = db.Column(db.Text, nullable=False)
    was_edited = db.Column(db.Boolean, nullable=False, default=False)
    user_input = db.relationship("UserInput", backref="selected_items")
    response = db.relationship("Agent1Response")

class Agent2Response(db.Model):
    __tablename__ = "agent2_responses"
    id = db.Column(db.Integer, primary_key=True)
    selected_id = db.Column(db.Integer, db.ForeignKey("agent1_selected.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    item_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    logic = db.Column(db.Text, nullable=False)
    criteria = db.Column(db.Text, nullable=False)
    product = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    was_edited = db.Column(db.Boolean, nullable=False, default=False)
    implemented = db.Column(db.Boolean, default=False)
    rejection_reason = db.Column(db.Text, nullable=True)
    selected = db.relationship("Agent1Selected", backref="agent2_responses")

class Agent2Final(db.Model):
    __tablename__ = "agent2_final"
    id = db.Column(db.Integer, primary_key=True)
    selected_id = db.Column(db.Integer, db.ForeignKey("agent1_selected.id"), nullable=False)
    agent2_response_id = db.Column(db.Integer, db.ForeignKey("agent2_responses.id"), nullable=False)
    saved_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    final_title = db.Column(db.Text, nullable=False)
    final_description = db.Column(db.Text, nullable=False)
    final_logic = db.Column(db.Text, nullable=False)
    final_criteria = db.Column(db.Text, nullable=False)
    was_edited = db.Column(db.Boolean, nullable=False, default=False)
    pdf_locked = db.Column(db.Boolean, nullable=False, default=False)
    selected = db.relationship("Agent1Selected", backref=db.backref("agent2_final", uselist=False))
    response = db.relationship("Agent2Response")

class ProductGuide(db.Model):
    __tablename__ = "product_guides"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    problems = db.Column(db.Text, nullable=False)

class Clarification(db.Model):
    __tablename__ = "clarifications"
    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"))
    questions = db.Column(db.Text)
    answers = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")

class IndustryCheck(db.Model):
    __tablename__ = "industry_checks"
    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"), nullable=False)
    initial_industry = db.Column(db.Text, nullable=False)
    extracted_industry_context = db.Column(db.Text, nullable=True)
    industry_detail_required = db.Column(db.Boolean, nullable=False, default=False)
    industry_context_sufficient = db.Column(db.Boolean, nullable=False, default=False)
    industry_context_found_in_description = db.Column(db.Boolean, nullable=False, default=False)
    trigger = db.Column(db.String(64), nullable=False, default="none")
    reason = db.Column(db.Text, nullable=True)
    problem_nature = db.Column(db.String(30), nullable=True)
    is_universal_problem = db.Column(db.Boolean, nullable=False, default=False)
    dependency_test_passed = db.Column(db.Boolean, nullable=False, default=False)
    question = db.Column(db.Text, nullable=True)
    answer = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="checked")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_input = db.relationship("UserInput", backref=db.backref("industry_checks", lazy=True))

class SituationCheck(db.Model):
    __tablename__ = "situation_checks"
    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"), nullable=False)
    original_text = db.Column(db.Text, nullable=False)
    normalized_text = db.Column(db.Text, nullable=True)
    ok = db.Column(db.Boolean, nullable=False, default=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    missing = db.Column(db.Text, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    rewrite_hint = db.Column(db.Text, nullable=True)
    example = db.Column(db.Text, nullable=True)
    slot_questions = db.Column(db.Text, nullable=True)
    slots_filled = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="llm")
    status = db.Column(db.String(20), nullable=False, default="failed")
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_input = db.relationship("UserInput", backref=db.backref("situation_checks", lazy=True))

def product_catalog_paths():
    base = os.path.dirname(__file__)
    return [
        os.path.join(base, "data", "Products.xlsx"),
        os.path.join(base, "data", "products.txt"),
        "/app/data/Products.xlsx",
        "/app/data/products.txt",
    ]


def _safe_cell(val):
    if val is None:
        return ""
    try:
        import math as _math
        if isinstance(val, float) and _math.isnan(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    return "" if s in ("None", "nan") else s


def _load_product_catalog_xlsx(path):
    try:
        df = pd.read_excel(path, sheet_name="Лист3", header=None, engine="openpyxl")
    except Exception as e:
        print(f"Warning: cannot load {path}: {e}", flush=True)
        return []
    products = []
    for _, row in df.iterrows():
        if len(row) < 4:
            continue
        name_raw = _safe_cell(row.iloc[2])
        description = _safe_cell(row.iloc[3])
        if not name_raw or name_raw == "Сервис (код)":
            continue
        # Strip internal product codes, e.g. "(0000015882)"
        name = re.sub(r"\s*\(\d{7,}\)\s*$", "", name_raw).strip()
        if not name:
            continue
        products.append({"name": name, "description": description, "problems": ""})
    return products

_PRODUCT_CATALOG_CACHE = {"path": None, "mtime": None, "products": []}
_PRODUCT_CAPABILITIES_CACHE = {}
_KNOWN_STEPS_CACHE = {"mtime": None, "steps": []}

def _catalog_cache_path():
    return next((path for path in product_catalog_paths() if os.path.exists(path)), None)

def load_product_catalog():
    catalog_path = _catalog_cache_path()
    if not catalog_path:
        return []
    mtime = os.path.getmtime(catalog_path)
    cache = _PRODUCT_CATALOG_CACHE
    if cache["path"] == catalog_path and cache["mtime"] == mtime:
        return cache["products"]
    if catalog_path.endswith(".xlsx"):
        products = _load_product_catalog_xlsx(catalog_path)
    else:
        products = []
        with open(catalog_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                name = (row.get("Название продукта") or "").strip()
                description = (row.get("Что делает продукт/сервис") or "").strip()
                problems = (row.get("Какие проблемы помогает решить") or "").strip()
                if name and description:
                    products.append({"name": name, "description": description, "problems": problems})
    cache["path"] = catalog_path
    cache["mtime"] = mtime
    cache["products"] = products
    _PRODUCT_CAPABILITIES_CACHE.clear()
    clear_product_metadata_cache()
    return products

def build_product_catalog_prompt(compact=False):
    products = load_product_catalog()
    if compact:
        return "\n".join(item["name"] for item in products)
    return "\n".join([f"{item['name']} - {item['description']}" for item in products])

def load_signals_by_segment():
    path = os.path.join(os.path.dirname(__file__), "data", "signals.tsv")
    signals_by_segment = {"micro_small": [], "medium_large": []}
    if not os.path.exists(path):
        return signals_by_segment
    seen = {key: set() for key in signals_by_segment}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            segment_group = (row.get("segment_group") or "").strip()
            signal = (row.get("signal") or "").strip()
            signal_id = (row.get("signal_id") or "").strip()
            if segment_group not in signals_by_segment or not signal or signal_id in seen[segment_group]:
                continue
            seen[segment_group].add(signal_id)
            label = f"{signal_id} — {signal}" if signal_id else signal
            signals_by_segment[segment_group].append({"id": signal_id, "signal": signal, "label": label})
    return signals_by_segment

def load_all_signals():
    seen = set()
    signals = []
    for group_signals in load_signals_by_segment().values():
        for item in group_signals:
            key = item["id"] or item["signal"]
            if key in seen:
                continue
            seen.add(key)
            signals.append(item)
    return signals

def signal_allowed_for_segment(segment_group, signal):
    if not signal:
        return True
    signals = load_signals_by_segment().get(segment_group, [])
    return any(signal in {item["signal"], item["id"], item["label"]} for item in signals)

def signal_exists(signal):
    if not signal:
        return True
    return any(signal in {item["signal"], item["id"], item["label"]} for item in load_all_signals())

def normalize_signals_list(signals=None, signal=None):
    if signals is not None:
        if isinstance(signals, str):
            return [signals.strip()] if signals.strip() else []
        return [str(item).strip() for item in signals if str(item).strip()]
    if signal:
        return normalize_signals_list(signals=signal)
    return []

def signals_all_exist(signals):
    return all(signal_exists(item) for item in normalize_signals_list(signals=signals))

def signals_joined(signals):
    normalized = normalize_signals_list(signals=signals)
    return "; ".join(normalized)

def parse_signals_from_text(text):
    signals = []
    block_match = re.search(r"^Сигналы:\s*\n((?:- .+\n?)*)", text or "", flags=re.MULTILINE)
    if block_match:
        for line in block_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("- "):
                signals.append(line[2:].strip())
    if not signals:
        single_match = re.search(r"^Сигнал:\s*(.*)$", text or "", flags=re.MULTILINE)
        if single_match and single_match.group(1).strip():
            signals.append(single_match.group(1).strip())
    return signals

def format_signal_lines(signals):
    normalized = normalize_signals_list(signals=signals)
    if not normalized:
        return []
    if len(normalized) == 1:
        return [f"Сигнал: {normalized[0]}"]
    lines = ["Сигналы:"]
    lines.extend(f"- {item}" for item in normalized)
    return lines

def segment_group_for_company_size(company_size):
    value = (company_size or "").lower()
    if "микро" in value or "мал" in value:
        return "micro_small"
    if "сред" in value or "круп" in value:
        return "medium_large"
    return ""

# === PROMPTS ===
PROMPT_METHODOLOGY_CHECK = """
Ты — строгий методист. Текст вводит клиентский менеджер банка. Он должен описывать ПРОБЛЕМУ или ВОЗМОЖНОСТЬ БИЗНЕСА КЛИЕНТА — не задачу самого менеджера и не задачу банка.

Верни JSON: {"violation": true/false, "violation_type": "manager_task | bank_problem | product_request | general_complaint | abstract | none", "reason": "...", "rewrite_hint": "...", "example": "..."}

Категории нарушений:

1. manager_task — менеджер банка описывает СВОЮ задачу или задачу банка, а не проблему/возможность клиентского бизнеса. Субъект первого лица («я», «мне», «у меня») — это менеджер, а не клиент.
   Признаки: аналитическая задача менеджера, задача продажи продукта, банковская сегментация.
   Примеры нарушений:
   — «У меня есть список предодобренных кредитов, нужно составить портрет этих клиентов»
   — «Мне нужно выполнить план по продажам кредитных продуктов в этом квартале»
   — «Хочу предложить клиентам из портфеля факторинг — как лучше это сделать»
   — «Нужно подготовить КП для клиента по расчётно-кассовому обслуживанию»
   — «Мне нужно найти клиентов для продукта "зарплатный проект"»
   — «Нужна стратегия для работы с сегментом малого бизнеса»

2. bank_problem — проблема банка или банковских отношений, а НЕ бизнес-ситуация клиента. Клиент упоминается как объект банковского сервиса, а не как бизнес с проблемой.
   Признаки: реактивация/удержание клиента для банка, причины отказа от банковских продуктов.
   Примеры нарушений:
   — «Клиент давно не пользуется расчётным счётом, нужно его реактивировать»
   — «У клиента истекает депозит через месяц, нужно его удержать»
   — «Клиент уходит из банка» / «Клиент перестал пользоваться нашими продуктами»
   — «Почему клиенты не берут кредиты в нашем банке»

3. product_request — запрос на подбор или рекомендацию банковского продукта без описания бизнес-ситуации.
   Примеры: «нужен кредит», «хочу открыть счёт», «клиент спрашивает какой продукт ему подойдёт», «дайте страховку», «рассчитайте лизинг».

4. general_complaint — жалоба без формулировки конкретной решаемой проблемы.
   Примеры: «тяжёлая жизнь», «всё плохо», «не знаю что делать», «бизнес не идёт».

5. abstract — абстрактное описание без привязки к конкретной ситуации клиентского бизнеса.
   Примеры: «улучшить бизнес», «развитие компании», «хочу больше денег».

Нарушений НЕТ (violation=false) если текст описывает:
— проблему в бизнесе клиента: падение продаж, кассовый разрыв, текучка кадров, налоговая нагрузка, проблемы с поставщиками, давление конкурентов
— возможность для развития бизнеса клиента: выход на маркетплейсы, масштабирование, поиск инвестора, новые рынки
— даже если клиент пока нечётко сформулировал проблему («клиент хочет обсудить возможности», «хочет расти»)
— даже если в тексте упоминаются банковские инструменты как ЧАСТЬ бизнес-ситуации клиента

НЕ путай: «клиент хочет выйти на маркетплейсы» — бизнес-ситуация клиента (violation=false). «Мне нужно найти клиентов для нашего продукта» — задача менеджера (violation=true).

Если нарушение есть:
- violation = true, violation_type — одна из категорий выше
- reason — объяснение пользователю, почему текст не подходит
- rewrite_hint — что конкретно нужно описать (на русском)
- example — один конкретный пример правильного описания похожей бизнес-ситуации клиента

Если нарушений нет:
- violation = false, violation_type = "none", reason = "", rewrite_hint = "", example = ""
"""

PROMPT_DEPENDENCY_TEST = """
Ты — бизнес-аналитик. Определи природу проблемы через тест на зависимость от отрасли.

Верни строго JSON:
{
  "problem_nature": "FINANCIAL | OPERATIONAL | STRATEGIC | REGULATORY | MARKET",
  "is_universal_problem": true/false,
  "dependency_test_passed": true/false,
  "reason": "..."
}

Правила:
1. Определи problem_nature (природу проблемы):
   - FINANCIAL — проблема с деньгами: кассовый разрыв, нехватка оборотных средств, убытки, рост затрат
   - OPERATIONAL — проблема с процессами: сбои в производстве, логистика, найм, удержание персонала
   - STRATEGIC — проблема с рынком/конкурентами: падение продаж, потеря доли рынка, смена модели
   - REGULATORY — проблема с регулированием: лицензии, налоги, проверки, compliance
   - MARKET — проблема с клиентами/спросом: отток клиентов, низкая конверсия, сезонность

2. Проведи Dependency Test:
   Представь две компании из РАЗНЫХ отраслей с одинаковой формулировкой проблемы.
   Нужны ли им ПРИНЦИПИАЛЬНО разные стратегии?
   Если стратегии одинаковые → is_universal_problem = true → dependency_test_passed = true.
   Если стратегии сильно зависят от отрасли → is_universal_problem = false → dependency_test_passed = false.

   Пример универсальной проблемы:
   «Кассовый разрыв из-за отсрочек клиентов на 60 дней» — и мебельщику, и транспортной компании нужен один подход (факторинг, управление дебиторкой, пересмотр условий оплаты). Стратегии не зависят от отрасли.

   Пример отрасле-зависимой проблемы:
   «Поставщики сырья подняли цены на 20%» — для пекарни (мука), для стройки (металл) и для IT (серверы) стратегии принципиально разные: замена поставщика, долгосрочные контракты, смена технологии.

3. Правило: FINANCIAL и OPERATIONAL проблемы ЧАЩЕ универсальны.
   REGULATORY и MARKET проблемы ЧАЩЕ отрасле-зависимы.
   Но всегда анализируй конкретную формулировку, а не только категорию.

4. reason — короткое обоснование (1-2 предложения на русском).
"""

PROMPT_CLARIFY_ADDON = """

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА — учёт универсальности проблемы:

1. Если проблема универсальная (FINANCIAL или OPERATIONAL, не зависит от отрасли):
   - НЕ задавай вопрос про отрасль
   - Если отрасль уже указана размыто — не уточняй, оставь как есть
   - Фокусируйся на уточнении РЕСУРСОВ и МАСШТАБА, а не отрасли

2. Если проблема отрасле-зависимая (REGULATORY, MARKET, или STRATEGIC с отраслевой спецификой):
   - Проверь, указана ли отрасль с достаточной детализацией
   - Если размыто и триггер сработал — задай ОДИН уточняющий вопрос

3. При формировании вопроса про отрасль учитывай problem_nature:
   - FINANCIAL → НЕ задавай вопрос про отрасль (она не влияет на стратегию)
   - OPERATIONAL → НЕ задавай вопрос про отрасль (процессные решения универсальны)
   - STRATEGIC → задавай только если нужна специфика рынка
   - REGULATORY → задавай (регуляторика отрасле-специфична)
   - MARKET → задавай если B2B/B2C различается
"""

PROMPT_A_DESC = """Ты — бизнес-консультант с 20-летним опытом работы с корпоративными клиентами.
Твоя задача: сформировать 1 стратегию для клиента на основе входных данных.
Определение стратегии
Стратегия — это самостоятельное направление решения ситуации, содержащее:
конкретную цель;
чёткий механизм достижения результата (что именно делаем);
используемые инструменты (включая продукт, если он задан);
ожидаемый измеримый бизнес-эффект;
критерии (кому стратегия НЕ подходит).
Входные данные
Ты получаешь анкету из 3 полей:
Размер компании
Отрасль компании (чем именно занимается)
Описание ситуации (что конкретно происходит)
Используй ВСЕ поля. Нельзя игнорировать ни одно.
Правила работы с продуктом
Если во входных данных указан продукт:
Используй его как инструмент внутри стратегии, если он релевантен.
Не делай продукт названием стратегии.
Не подменяй продукт абстрактными словами.
Не придумывай использование продукта, если оно не логично в данной ситуации.
Если продукт не указан как уже используемый — считай его предлагаемым инструментом.
Продукт может использоваться только в двух сценариях:
Как новый инструмент (например: оформить кредит, купить страховку)
Как уже действующий инструмент, только если это прямо указано во входных данных
Запрещено:
использовать продукт как источник выплат/ресурсов, если это не указано явно;
строить стратегию на уже полученных деньгах, если это не подтверждено.
Запрещено:
использовать формулировки типа «финансирование», «банковский инструмент» вместо конкретного продукта;
предлагать сценарии, требующие уже существующего контракта (например, страхового), если это не указано.
Анализ перед формированием стратегий
Перед ответом обязательно учитывай:
размер компании (ресурсы и ограничения);
отрасль и её текущий тренд (рост / спад);
макроэкономическую ситуацию в России;
конкретную проблему из описания.
Не придумывай факты, которых нет во входных данных.Механизм должен быть реализуем без предположений о скрытых ресурсах.
Требования к стратегии
Нужно предложить 1 стратегию.
Стратегия должна относиться к одному из типов механики:
Снижение потерь / защита
Восстановление операционной деятельности
Изменение или расширение модели дохода
Выбери тот тип, который наиболее релевантен ситуации клиента.
Запрещено использовать факты, которых нет во входных данных;
Нельзя предполагать наличие: действующих договоров (страхование, кредит, лизинг и т.д.);уже полученных или ожидаемых выплат;ресурсов, которые не указаны явно.
Недопустимые примеры (запрещено):
Предположение фактов, которых нет:
«Получение страховой выплаты для компенсации убытков» (если страховка не указана);
«Использование уже одобренного кредита» (если кредит не указан);
«Привлечение инвестиций от текущих партнёров» (если партнёры не описаны);
«Использование накопленных резервов» (если резервы не упомянуты).
Общее правило:
Нельзя использовать ресурсы или условия, которых нет во входных данных.
Если в описании НЕ сказано, что у компании есть страховка —
считай, что её нет.
Требования к названию стратегии
Название:
максимум 5 слов;
начинается с существительного;
описывает действие и бизнес-результат;
НЕ содержит название продукта;
НЕ является абстрактным.
Запрещены слова без конкретики:
финансирование
поддержка
развитие
оптимизация
улучшение
сопровождение
страхование (если это просто название продукта)
Название должно отвечать на вопрос: что именно делаем для изменения ситуации.
Требования к содержанию стратегии
Каждая стратегия должна содержать:
Цель
Конкретный измеримый результат.
Механизм
Пошаговое описание действий (что именно делаем).
Инструменты
Какие инструменты используются (включая продукт, если применимо).
Бизнес-эффект
Оцифрованный результат (выручка, издержки, маржа и т.д.).
Критерии (кому НЕ подходит)
Критерии должны:
быть проверяемыми по данным (банк или открытые источники);
содержать числовые значения;
не требовать внутренней информации компании.
Примеры допустимых критериев:
выручка < 50 млн руб.
ОКВЭД не входит в 01–03
срок деятельности < 12 месяцев
Финальная самопроверка (обязательная)
Перед тем как выдать ответ, проверь:
название не содержит продукт;
нет абстрактных формулировок;
продукт (если задан) используется корректно;
стратегия реализуема и имеет измеримый эффект.
Если есть нарушения — исправь до вывода.
"""
PROMPT_A = os.environ.get("PROMPT_A", PROMPT_A_DESC)

PROMPT_B_DESC = """Ты — бизнес консультант с 20-летним опытом работы с корпоративными клиентами.
К выбранной стратегии подбери ровно 3 шага.
Шаги должны быть выстроены в логическом и временном порядке.
Шаг — это простое действие, которое можно выполнить на практике и которое не требует дальнейшего разбиения для исполнителя.
Название Шага должно начинаться с глагола.
Каждый шаг должен быть: однозначным, реализуемым, ограниченным по сроку, привязанным к цели стратегии.
Пример: изучить информацию о торгах, провести аудит соответствия компании, сделать ремонт помещения, проверить документы УКЭП, проанализировать бизнес, подобрать тендер.
Избегай общих и абстрактных формулировок, не используй слова без конкретизации: «улучшить», «оптимизировать», «усилить», «развить», «проработать».
Каждый шаг должен быть реализуем в срок до 1 месяца.
Принадлежность Шага к Стратегии очевидна.
Критерии — это критерии кому данный Шаг не подходит.
Критерии должны: относиться к открытым данным (отрасль, ОКВЭД, регион, лицензии) или к данным банка (выручка, количество сотрудников, срок деятельности, назначения платежей). Не предлагай критерии, которые можно узнать только внутри компании. Иметь числовое значение — не «не подходит по ОКВЭД», а «не подходит ОКВЭД 01, 02».
Поле "product" всегда оставляй пустым ("") — сервис банка к шагу добавит менеджер вручную.
"""
PROMPT_B_BASE = os.environ.get("PROMPT_B", PROMPT_B_DESC)

PROMPT_PRODUCT_STEP = """Ты — бизнес-консультант. Сгенерируй ОДИН конкретный шаг для реализации стратегии.
При выполнении этого шага клиенту поможет указанный сервис банка.
Требования:
- Шаг описывает реальное бизнес-действие клиента, а не «подключить продукт» или «оформить сервис»
- Сервис банка — инструмент внутри шага, а не цель самого шага
- Название начинается с глагола
- Однозначное, реализуемое действие, срок до 1 месяца
- Явная связь с целью стратегии
- Поле "product" должно содержать ТОЧНОЕ название сервиса банка, переданного в запросе
"""

def get_prompt_b():
    return PROMPT_B_BASE

JSON_INSTRUCTIONS = """
ВАЖНО: Отвечай строго в формате JSON — от 1 до 10 вариантов:
{ "items": [
{
 "id": 1,
 "title": "Краткое название варианта",
 "description": "Описание варианта (2-3 предложения)",
 "logic": "Логика и обоснование",
 "criteria": "Критерии оценки / применения",
 "implemented": "Флаг реализации (ОБЯЗАТЕЛЬНО ЗАПОЛНИТЬ значением Реализована или Не реализована)"
},
{ "id": 2, "title": "", "description": "", "logic": "", "criteria": "", "implemented": ""},
{ "id": 3, "title": "", "description": "", "logic": "", "criteria": "", "implemented": ""}
]}
Никакого текста вне JSON.
"""

JSON_INSTRUCTIONS_AGENT2 = """
ВАЖНО: Отвечай строго в формате JSON — ровно 3 шага:
{ "items": [
{
 "id": 1,
 "title": "Краткое название шага (начинается с глагола)",
 "description": "Описание шага (2-3 предложения)",
 "logic": "Логика и обоснование",
 "criteria": "Критерии — кому шаг не подходит (с числовыми значениями)",
 "product": "",
 "implemented": "Флаг реализации (ОБЯЗАТЕЛЬНО ЗАПОЛНИТЬ значением Реализована или Не реализована)"
},
{ "id": 2, "title": "", "description": "", "logic": "", "criteria": "", "product": "", "implemented": ""},
{ "id": 3, "title": "", "description": "", "logic": "", "criteria": "", "product": "", "implemented": ""}
]}
Никакого текста вне JSON.
"""

JSON_INSTRUCTIONS_SINGLE_STEP = """
ВАЖНО: Отвечай строго в формате JSON — ровно 1 шаг:
{ "items": [
{
 "id": 1,
 "title": "Краткое название шага (начинается с глагола)",
 "description": "Описание шага (2-3 предложения) — объясни, как сервис банка помогает в этом действии",
 "logic": "Логика — почему именно этот сервис банка уместен здесь",
 "criteria": "Критерии — кому шаг не подходит (с числовыми значениями)",
 "product": "ТОЧНОЕ название сервиса банка из запроса",
 "implemented": "Не реализована"
}
]}
Никакого текста вне JSON.
"""

PROMPT_CLARIFY = """
Ты — бизнес-аналитик.
Твоя задача:
Проверить входные данные клиента
Определить, достаточно ли информации для разработки стратегий

Если информации достаточно:
верни:
{"status": "ok"}

Если информации недостаточно:
Верни JSON строго формата:
{
 "status": "need_clarification",
 "questions": [
{
 "key": "company_age",
 "question": "Сколько лет компании?",
 "options": ["<1 года", "1–3 года", "3–5 лет", "5+ лет"],
 "explanation": "Возраст компании влияет на доступность кредитных продуктов и господдержки — для молодых компаний стратегии принципиально другие"
}
]
}

Правила:
максимум 5 вопросов
максимум 3 варианта ответа на каждый вопрос
вопросы должны критически влиять на стратегию
варианты ответа должны быть конкретными
не задавай очевидных или повторяющихся вопросов
key должен быть понятным (snake_case)
никаких q1, q2, q3

ДОПОЛНИТЕЛЬНОЕ ПРАВИЛО — проверка отрасли на детализацию:

Проверь, нужно ли уточнять отрасль.
Отрасль считается размытой, если это 1-2 общих слова без специфики:
"торговля", "производство", "услуги", "строительство", "транспорт", "сельское хозяйство", "IT"

Детализация отрасли ОБЯЗАТЕЛЬНА, если описанная проблема попадает под один из 6 триггеров:

1. Регуляторика — если проблема касается лицензирования, сертификации, разрешений, compliance
   → нужна конкретная под-отрасль (медицина, фарма, алкоголь, авиация и т.д.)
2. Физика продукта — если проблема касается издержек, логистики, хранения, порчи товара
   → нужна информация о типе товара (скоропорт, опасный груз, хрупкое, сыпучее и т.д.)
3. Рыночная структура — если проблема касается продаж, выручки, клиентов, конкуренции
   → нужно знать B2B или B2C, длинный или короткий цикл сделки
4. Цепочка поставок — если проблема касается поставщиков, импорта, дефицита, сроков
   → нужна информация об источнике (локальные/импорт, сезонность)
5. Экономика под-отрасли — если проблема касается рентабельности, затрат, ценообразования
   → нужна специфика (капиталоёмкость, маржинальность, доля постоянных издержек зависит от под-отрасли)
6. Госучастие — если проблема касается госзакупок, субсидий, регулируемых цен
   → нужно знать, работает ли отрасль с госзаказом

Если отрасль размытая И проблема попадает под триггер — задай ОДИН уточняющий вопрос про отрасль.
В поле explanation объясни КОНКРЕТНО для этой ситуации, почему детализация отрасли важна.
Пиши explanation на русском, простым языком, 2-3 предложения.

Пример explanation для ситуации "рост издержек" и отрасли "торговля":
"Для снижения издержек важно знать физику товара. Скоропортящиеся продукты требуют холодильников и имеют списание — а стройматериалы нет. Стратегия для этих случаев будет принципиально разной."

Не задавай вопрос про отрасль, если она уже указана детально или если проблема НЕ требует конкретики отрасли.

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА — учёт универсальности проблемы:

1. Если проблема универсальная (FINANCIAL или OPERATIONAL, не зависит от отрасли):
   - НЕ задавай вопрос про отрасль
   - Если отрасль уже указана размыто — не уточняй, оставь как есть
   - Фокусируйся на уточнении РЕСУРСОВ и МАСШТАБА, а не отрасли

2. Если проблема отрасле-зависимая (REGULATORY, MARKET, или STRATEGIC с отраслевой спецификой):
   - Проверь, указана ли отрасль с достаточной детализацией
   - Если размыто и триггер сработал — задай ОДИН уточняющий вопрос

3. При формировании вопроса про отрасль учитывай problem_nature:
   - FINANCIAL → НЕ задавай вопрос про отрасль (она не влияет на стратегию)
   - OPERATIONAL → НЕ задавай вопрос про отрасль (процессные решения универсальны)
   - STRATEGIC → задавай только если нужна специфика рынка
   - REGULATORY → задавай (регуляторика отрасле-специфична)
   - MARKET → задавай если B2B/B2C различается
"""

PROMPT_INDUSTRY_CHECK = """
Ты — бизнес-аналитик. Реши, нужно ли уточнять отрасль ПЕРЕД генерацией стратегии.

Главное правило: по умолчанию industry_detail_required = false.
Спрашивай отрасль только если без неё ты не можешь предложить релевантную стратегию
или подобрать уместные продукты — то есть отрасль меняет саму логику решения.

Алгоритм:
1. Проверь, указана ли отрасль уже в company_industry или в описании ситуации.
   Если да — industry_detail_required = false, извлеки контекст в extracted_industry_context.
2. Задай себе тест: «Две компании разного масштаба, но с одной и той же формулировкой проблемы —
   нужны ли им принципиально разные стратегии ТОЛЬКО из-за отрасли?»
   Если нет — industry_detail_required = false.
3. Не спрашивай отрасль из-за второстепенных слов (расходы, клиенты, платежи),
   если корневая проблема решается одинаково в большинстве отраслей.
4. Спрашивай отрасль только если она влияет на: регуляторику, цепочку поставок,
   сезонность спроса, тип клиентов B2B/B2C, лицензирование, отраслевые меры поддержки,
   сравнение с отраслевым бенчмарком.
5. Если industry_detail_required = true, обязательно заполни strategy_impact_if_unknown:
   одно конкретное предложение, ЧТО именно в стратегии/шагах/продуктах изменится после ответа.
   Без этого поля нельзя ставить industry_detail_required = true.

КРИТИЧЕСКОЕ ПРАВИЛО ДЛЯ УНИВЕРСАЛЬНЫХ ПРОБЛЕМ:
Если problem_nature = "FINANCIAL" или problem_nature = "OPERATIONAL",
то industry_detail_required ВСЕГДА = false.
Для этих типов проблем отрасль НЕ влияет на стратегию —
решение одно и то же для любой компании (факторинг, управление дебиторкой,
оптимизация затрат, найм, удержание).

Не задавай общих вопросов вроде «уточните отрасль» или «в какой отрасли работает компания».
Вопрос должен быть привязан к ситуации и к решению, которое зависит от отрасли.

Ответь строго JSON:
{
  "industry_detail_required": true или false,
  "industry_context_sufficient": true или false,
  "industry_context_found_in_description": true или false,
  "trigger": "sales_demand | costs_margin | logistics_inventory | supply_chain | regulation | government | benchmark | none",
  "initial_industry": "...",
  "extracted_industry_context": "...",
  "strategy_impact_if_unknown": "...",
  "reason": "...",
  "question": "..."
}
"""


# === HELPERS ===
def to_bool(value):
    if isinstance(value, bool): return value
    if value is None: return False
    value_str = str(value).strip().lower()
    return value_str in {"1", "true", "yes", "y", "да", "реализована", "реализовано", "реализован", "выполнено", "сделано", "done"}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

# Ownership-checking helpers — abort(403) if the resource belongs to another user
def get_input_or_403(input_id):
    user_input = UserInput.query.get_or_404(input_id)
    if user_input.user_id != current_user_id():
        abort(403)
    return user_input

def get_agent1_response_or_403(response_id):
    response = Agent1Response.query.get_or_404(response_id)
    if response.user_input.user_id != current_user_id():
        abort(403)
    return response

def get_selected_or_403(selected_id):
    selected = Agent1Selected.query.get_or_404(selected_id)
    if selected.user_input.user_id != current_user_id():
        abort(403)
    return selected

def get_agent2_response_or_403(response_id):
    response = Agent2Response.query.get_or_404(response_id)
    if response.selected.user_input.user_id != current_user_id():
        abort(403)
    return response

def get_auth_serializer():
    return URLSafeSerializer(current_app_secret(), salt="iframe-auth")

def current_app_secret():
    from flask import current_app
    return current_app.config["SECRET_KEY"]

def current_user_id():
    return session.get("user_id") or getattr(g, "token_user_id", None)

def auth_url(endpoint, **values):
    token = getattr(g, "auth_token", None)
    if token and endpoint != "static" and "_auth" not in values:
        values["_auth"] = token
    return url_for(endpoint, **values)

def register_template_helpers(app):
    app.jinja_env.globals["url_for"] = auth_url

    @app.context_processor
    def inject_current_user():
        uid = session.get("user_id") or getattr(g, "token_user_id", None)
        user = db.session.get(User, uid) if uid else None
        return {"current_user": user}

def normalize_items(content):
    print("RAW AI CONTENT:", repr(content))
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.startswith("json"): text = text[4:]
    if text.endswith("```"): text = text.rstrip("`")
    text = text.strip()
    try:
        data = json.loads(text)
    except Exception:
        return []
    items = None
    if isinstance(data, list): items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("results") or data.get("recommendations") or data.get("cards")
    if isinstance(items, dict): items = list(items.values())
    if not isinstance(items, list): return []
    if len(items) == 0: return []
    normalized = []
    for index, item in enumerate(items[:10], start=1):
        if not isinstance(item, dict):
            item = {"title": str(item), "description": "", "logic": "", "criteria": "", "product": ""}
        implemented = to_bool(item.get("implemented"))
        normalized.append({
            "item_number": int(item.get("id") or index),
            "title": str(item.get("title") or f"Пункт {index}"),
            "description": str(item.get("description") or ""),
            "logic": str(item.get("logic") or ""),
            "criteria": str(item.get("criteria") or ""),
            "product": str(item.get("product") or "").strip(),
            "implemented": implemented,
        })
    return normalized

def call_openai(system_prompt, user_message, json_instructions=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    instructions = json_instructions if json_instructions is not None else JSON_INSTRUCTIONS
    client = OpenAI(api_key=api_key, timeout=120)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{instructions.strip()}"},
            {"role": "user", "content": user_message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")

def call_openai_raw(system_prompt, user_message):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=120)
    enhanced_prompt = f"{system_prompt.strip()}\nВАЖНО: Ответь строго в формате JSON. Никакого текста вне JSON."
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return json.loads(response.choices[0].message.content)

def normalize_product_text(value):
    text = str(value or "").lower().replace("ё", "е")
    text = text.replace("sber", "сбер")
    text = re.sub(r"\(\s*\d{6,}\s*\)", " ", text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def product_brand_variants(name):
    variants = set()
    raw = str(name or "").strip()
    normalized = normalize_product_text(raw)
    if not normalized:
        return variants
    variants.add(normalized)
    for sep in (" – ", " - ", "—", "–"):
        if sep in raw:
            head = normalize_product_text(raw.split(sep, 1)[0])
            if len(head) >= 4:
                variants.add(head)
    first_token = normalized.split(" ", 1)[0]
    if len(first_token) >= 4 and first_token not in {"услуги", "сервис", "продукт", "платформа"}:
        variants.add(first_token)
    expanded = set(variants)
    for variant in variants:
        if "sber" in variant:
            expanded.add(variant.replace("sber", "сбер"))
        if "сбер" in variant:
            expanded.add(variant.replace("сбер", "sber"))
    return expanded

def product_match_variants(name):
    normalized = normalize_product_text(name)
    brand_variants = product_brand_variants(name)
    variants = set(brand_variants)
    without_number = re.sub(r"^\d+\s+", "", normalized).strip()
    if without_number:
        variants.add(without_number)
    without_album = re.sub(r"\bальбом\b.*$", "", without_number or normalized).strip()
    if len(without_album) >= 12:
        variants.add(without_album)
    return [
        variant for variant in variants
        if len(variant) >= 8 or variant in brand_variants
    ]

def step_text_for_product_match(item):
    return normalize_product_text(" ".join([
        item.get("title", ""),
        item.get("description", ""),
        item.get("logic", ""),
        item.get("criteria", ""),
    ]))

MIN_PRODUCT_MATCH_SCORE = 14
PRODUCT_STEP_RATIO = 0.7

STEP_ACTION_PATTERNS = {
    "research": r"изуч|анализ|исслед|обзор|сравн|собра|проанализ|оцен|бенчмарк|практик|кейс|лучш.{0,12}опыт|монитор",
    "implement": r"внедр|запуст|настро|разверн|созда|подключ|оформ|активир|внедри",
    "hire": r"подбор|найм|нанят|ваканс|рекрут|привлеч.{0,16}кандидат|закрыть.{0,12}ваканс",
    "retain": r"удерж|сниз.{0,12}текуч|мотивац|вовлеч|лояльност.{0,20}(сотрудник|персонал|кадр|команд)",
    "payroll": r"зарплат|фот|выплат.{0,12}сотрудник",
    "finance": r"кредит|финанс|заем|заём|кассов|разрыв|лимит|оборотн|депозит|вексел|облигаци",
    "market": r"реклам|акци|продвижен|маркет|таргет|трафик|промо",
    "sell": r"продаж|выручк|сделк|конверс|чек",
    "train": r"обуч|повыш.{0,12}квалиф|тренинг|развит.{0,12}компетенц",
    "survey": r"опрос|интервью|обратн.{0,12}связ",
    "loyalty_setup": r"запуст.{0,16}лояльност|созда.{0,16}лояльност|программ.{0,16}лояльност",
}

STEP_DOMAIN_PATTERNS = {
    "employee_hr": r"сотрудник|персонал|кадр|текуч|увольн|hr|штат|работник|команд",
    "customer": r"клиент|покупател|потребител|посетител",
    "customer_loyalty": r"лояльност.{0,24}(клиент|покупател)|удержан.{0,16}клиент|бонус.{0,16}клиент",
    "payments": r"эквайринг|эквайр|оплат|платеж|платёж|терминал|безнал|pos",
    "banking": r"рко|расчет|расчёт|счет|счёт|депозит|банк",
    "construction": r"строител|девелоп|капремонт|жкх",
    "logistics": r"логист|склад|достав|остат|запас",
}

PRODUCT_TAG_PATTERNS = {
    "hiring": (r"работа\.?ру", r"\bhh\.?ru\b", r"авито работа", r"сберподбор", r"подбор сотрудник", r"поиск сотрудник", r"размещен.{0,12}ваканс"),
    "partner_search": (r"подбор партнер",),
    "customer_loyalty": (r"benefitty", r"лояльност.{0,20}(клиент|бизнес|спасибо)", r"спасибо", r"удержан.{0,12}клиент", r"бонус"),
    "employee_retention": (r"work.?life", r"удержан.{0,12}сотрудник", r"вовлечен.{0,12}сотрудник"),
    "employee_hr": (r"\bhr\b", r"saby", r"кадр", r"зарплат", r"(?<![а-я])персонал(?!из)", r"фот", r"кадров"),
    "training": (r"обучен", r"образовательн", r"квалификац", r"сберуниверситет", r"алгоритмик"),
    "payments": (r"эквайринг", r"эквайр", r"pos", r"терминал", r"платеж", r"платёж"),
    "finance": (r"кредит", r"овердрафт", r"гарант", r"факторинг", r"лизинг", r"финанс", r"депозит", r"вексел", r"облигаци", r"репо"),
    "marketing": (r"таргет", r"реклам", r"геоконтекст", r"продвижен", r"маркет"),
    "analytics": (r"аналитик", r"монитор", r"прогноз"),
    "banking": (r"рко", r"расчетн", r"расчётн", r"счет", r"депозит"),
}

ACTION_CAPABILITY_MAP = {
    "research": {"analytics", "training", "customer_loyalty", "employee_retention", "employee_hr", "marketing"},
    "implement": {"customer_loyalty", "employee_hr", "payments", "marketing", "training", "finance", "banking", "employee_retention"},
    "hire": {"hiring"},
    "retain": {"employee_retention", "employee_hr", "training"},
    "payroll": {"employee_hr"},
    "finance": {"finance"},
    "market": {"marketing", "customer_loyalty"},
    "sell": {"marketing", "payments", "analytics"},
    "train": {"training"},
    "survey": {"employee_hr", "analytics"},
    "loyalty_setup": {"customer_loyalty", "employee_retention"},
}

DOMAIN_CAPABILITY_MAP = {
    "employee_hr": {"employee_hr", "employee_retention", "training", "hiring"},
    "customer": {"customer_loyalty", "marketing", "payments", "analytics"},
    "customer_loyalty": {"customer_loyalty", "marketing"},
    "payments": {"payments"},
    "banking": {"banking", "finance"},
    "construction": {"finance", "analytics"},
    "logistics": {"finance", "analytics"},
}

def stem_token(token):
    token = (token or "").lower().replace("ё", "е")
    if len(token) < 4:
        return token
    suffixes = (
        "ения", "ению", "ением", "ована", "овано", "ованы", "овани", "овать",
        "ами", "ями", "ного", "ному", "ной", "ную", "ная", "ные", "ных", "ное",
        "иями", "иях", "иям", "ов", "ам", "ах", "ой", "ей", "ий", "ие", "ия", "ию",
        "ть", "ти", "ка", "ки", "ку", "ком", "ем", "ом", "ы", "и", "у", "а", "е",
    )
    for suffix in suffixes:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token

def match_tokens(text):
    return {
        stem_token(token)
        for token in normalize_product_text(text).split()
        if len(stem_token(token)) >= 3
    }

def extract_step_intent(step):
    text = step_text_for_product_match(step)
    actions = {name for name, pattern in STEP_ACTION_PATTERNS.items() if re.search(pattern, text)}
    domains = {name for name, pattern in STEP_DOMAIN_PATTERNS.items() if re.search(pattern, text)}
    if re.search(r"лояльн", text) and "customer_loyalty" not in domains:
        if domains & {"employee_hr"} or re.search(r"текуч|удерж|сотрудник|персонал|кадр", text):
            domains.add("employee_hr")
        elif domains & {"customer"}:
            domains.add("customer_loyalty")
    if not actions:
        if re.search(r"изуч|анализ|практик|кейс", text):
            actions.add("research")
        elif is_hiring_context(text):
            actions.add("hire")
        else:
            actions.add("implement")
    elif "hire" in actions and not is_hiring_context(text):
        actions.discard("hire")
    if domains & {"banking"} or re.search(r"депозит|размещен.{0,16}средств", text):
        actions.discard("loyalty_setup")
    return {"text": text, "actions": actions, "domains": domains}

def product_capabilities(product):
    product_id = product["name"]
    if product_id in _PRODUCT_CAPABILITIES_CACHE:
        return _PRODUCT_CAPABILITIES_CACHE[product_id]
    blob = normalize_product_text(
        f"{product['name']} {product.get('description', '')} {product.get('problems', '')}"
    )
    tags = set()
    for tag, patterns in PRODUCT_TAG_PATTERNS.items():
        if any(re.search(pattern, blob) for pattern in patterns):
            tags.add(tag)
    if "hiring" in tags and "partner_search" in tags:
        tags.discard("partner_search")
    _PRODUCT_CAPABILITIES_CACHE[product_id] = tags
    return tags

def legacy_product_score(step, product, intent=None, caps=None):
    intent = intent or extract_step_intent(step)
    caps = caps if caps is not None else product_capabilities(product)
    if not caps:
        return 0
    actions = intent["actions"]
    domains = intent["domains"]
    score = 0

    for action in actions:
        allowed = ACTION_CAPABILITY_MAP.get(action, set())
        if caps & allowed:
            score += 12
        elif allowed:
            score -= 10

    for domain in domains:
        aligned = DOMAIN_CAPABILITY_MAP.get(domain, set())
        if caps & aligned:
            score += 10

    name_norm = normalize_product_text(product["name"])
    step_tokens = match_tokens(intent["text"])
    product_tokens = match_tokens(f"{product['name']} {product.get('description', '')}")
    overlap = len(step_tokens & product_tokens)
    score += min(overlap, 4)

    if "hiring" in caps and "hire" not in actions:
        score -= 30
    if "partner_search" in caps and (domains & {"employee_hr"} or "hire" in actions):
        score -= 35
    if "payments" in caps and "payments" not in domains and "sell" not in actions:
        score -= 30
    step_capabilities = extract_step_capabilities(intent)
    loyalty_relevant = (
        "customer_loyalty" in domains
        or "customer" in domains
        or "customer_loyalty_program" in step_capabilities
        or "loyalty_setup" in actions
        or "market" in actions
    )
    if "customer_loyalty" in caps and "research" in actions and not loyalty_relevant:
        score -= 20
    if "customer_loyalty" in caps and domains & {"employee_hr"} and not loyalty_relevant:
        score -= 25
    if "employee_retention" in caps and domains & {"employee_hr"}:
        score += 8
    if "training" in caps and ("train" in actions or ("research" in actions and domains & {"employee_hr"})):
        score += 6
    if "таргет" in name_norm and actions & {"market", "sell"}:
        score += 6
    if name_norm in {
        normalize_product_text("Рекламные услуги"),
        normalize_product_text("Рекламная платформа"),
    }:
        score -= 4
    return score

def score_product_for_step(step, product, intent=None):
    intent = intent or extract_step_intent(step)
    caps = product_capabilities(product)
    structured_score = score_product_by_metadata(step, product, intent, legacy_tags=caps)
    if structured_score is not None:
        if structured_score <= 0:
            return 0
        legacy_cap = 0 if extract_step_capabilities(intent) else 6
        return structured_score + min(legacy_product_score(step, product, intent=intent, caps=caps), legacy_cap)
    return legacy_product_score(step, product, intent=intent, caps=caps)

def rank_products_for_step(step, products=None, limit=3, min_score=MIN_PRODUCT_MATCH_SCORE):
    products = matchable_products(products)
    intent = extract_step_intent(step)
    scored = []
    for product in products:
        score = score_product_for_step(step, product, intent)
        if score >= min_score:
            scored.append((score, product))
    scored.sort(key=lambda row: (-row[0], row[1]["name"]))
    return [product for _, product in scored[:limit]]

def detect_product_match_ambiguity(intent):
    text = intent["text"]
    questions = []
    if re.search(r"лояльн", text) and "customer_loyalty" not in intent["domains"]:
        if intent["domains"] & {"employee_hr"} or not (intent["domains"] & {"customer"}):
            if "research" in intent["actions"] or "loyalty_setup" in intent["actions"]:
                questions.append(
                    "Программа лояльности в этом шаге относится к сотрудникам (удержание персонала) или к клиентам (бонусы покупателям)?"
                )
    if re.search(r"подбор", text) and intent["domains"] & {"employee_hr"} and "hire" not in intent["actions"]:
        questions.append(
            "Шаг про подбор сотрудников или подбор партнёров/подрядчиков?"
        )
    if not intent["domains"] and intent["actions"] & {"implement", "research"}:
        questions.append(
            "Для какого направления бизнеса выполняется шаг — это поможет точнее подобрать продукт банка."
        )
    return questions[:1]

def matchable_products(products=None):
    products = products if products is not None else load_product_catalog()
    return [product for product in products if not is_product_excluded(product)]

def match_products_for_step(step, products=None):
    products = matchable_products(products)
    intent = extract_step_intent(step)
    explicit_names = detect_explicit_step_products(step, products, intent=intent)
    if explicit_names:
        by_name = {product["name"]: product for product in products}
        product = by_name.get(explicit_names[0])
        if product:
            return {
                "status": "matched",
                "products": [build_step_product_payload(step, product, intent)],
                "question": "",
            }

    scored = [
        (score_product_for_step(step, product, intent), product)
        for product in products
    ]
    scored = [(score, product) for score, product in scored if score >= MIN_PRODUCT_MATCH_SCORE]
    scored.sort(key=lambda row: (-row[0], row[1]["name"]))

    if len(scored) >= 2 and scored[0][0] - scored[1][0] < 4:
        question = detect_product_match_ambiguity(intent)
        if question:
            return {"status": "needs_clarification", "products": [], "question": question[0]}

    if not scored:
        question = detect_product_match_ambiguity(intent)
        return {
            "status": "needs_clarification",
            "products": [],
            "question": question[0] if question else (
                "Уточните цель шага: что именно нужно сделать и для кого (сотрудники, клиенты, финансы, продажи)?"
            ),
        }

    return {
        "status": "matched",
        "products": [build_step_product_payload(step, scored[0][1], intent)],
        "question": "",
    }

def build_step_product_payload(step, product, intent=None):
    return {
        "name": product["name"],
        "description": product.get("description", ""),
        "usage_hint": build_step_product_usage_hint(step, product, intent=intent),
    }

def detect_explicit_step_products(item, products=None, intent=None):
    products = products if products is not None else load_product_catalog()

    # LLM-assigned product (Variant E): if the step has a non-empty "product" field
    # matching a catalog entry, trust the LLM and return it.
    if isinstance(item, dict):
        product_field = str(item.get("product") or "").strip()
    else:
        product_field = str(getattr(item, "product", "") or "").strip()
    if product_field:
        by_name = {p["name"]: p for p in products}
        if product_field in by_name:
            return [product_field]

    intent = intent or extract_step_intent(item)
    step_text = intent["text"]
    matched = []
    for product in products:
        name = product["name"]
        for variant in product_match_variants(name):
            if not variant or len(variant) < 8 or variant not in step_text:
                continue
            if score_product_for_step(item, product, intent) >= MIN_PRODUCT_MATCH_SCORE:
                matched.append(name)
                break
    return matched

def detect_step_products(item, products=None):
    match = match_products_for_step(agent2_step_dict(item), products)
    return [product["name"] for product in match["products"]]

def agent2_step_dict(item):
    if isinstance(item, dict):
        return item
    return {
        "title": item.title,
        "description": item.description,
        "logic": item.logic,
        "criteria": item.criteria,
        "product": getattr(item, "product", "") or "",
    }

def recommend_step_products(item, products=None, limit=1):
    return rank_products_for_step(agent2_step_dict(item), products, limit=limit)

CAPABILITY_USAGE_HINTS = {
    "hiring": "Закрывает задачу шага по поиску и привлечению персонала.",
    "customer_loyalty": "Помогает реализовать шаг через программу лояльности для клиентов.",
    "employee_retention": "Поддерживает удержание сотрудников — ключевую цель этого шага.",
    "employee_hr": "Автоматизирует HR-процессы, необходимые для выполнения шага.",
    "training": "Даёт инструменты обучения и развития, которые нужны на этом шаге.",
    "payments": "Обеспечивает приём платежей, если шаг связан с оплатами.",
    "finance": "Закрывает финансовую часть реализации шага.",
    "marketing": "Поддерживает маркетинговую составляющую шага.",
    "analytics": "Даёт аналитику и данные для принятия решений на этом шаге.",
    "banking": "Упрощает расчётные операции, нужные для шага.",
}

def build_step_product_usage_hint(step, product, intent=None):
    step = agent2_step_dict(step)
    intent = intent or extract_step_intent(step)
    caps = product_capabilities(product)
    actions = intent["actions"]
    domains = intent["domains"]
    description = (product.get("description") or "").strip()

    if "hire" in actions and "hiring" in caps:
        return CAPABILITY_USAGE_HINTS["hiring"]
    if "research" in actions and domains & {"employee_hr"} and "training" in caps:
        return "Поможет изучить и внедрить практики развития и удержания персонала."
    if "research" in actions and domains & {"employee_hr"} and "employee_retention" in caps:
        return "Даст ориентиры по программам удержания сотрудников для этого шага."
    if domains & {"employee_hr"} and "customer_loyalty" in caps:
        return CAPABILITY_USAGE_HINTS["employee_retention"]
    for cap in ("employee_retention", "employee_hr", "training", "hiring", "customer_loyalty", "marketing", "payments", "finance", "analytics", "banking"):
        if cap in caps and cap in CAPABILITY_USAGE_HINTS:
            if caps & ACTION_CAPABILITY_MAP.get(next(iter(actions), "implement"), set()) or caps & set().union(*(DOMAIN_CAPABILITY_MAP.get(d, set()) for d in domains)):
                return CAPABILITY_USAGE_HINTS[cap]

    problems = (product.get("problems") or "").strip()
    if problems:
        return f"По возможностям продукта: {problems}."
    if description:
        return f"Продукт закрывает задачу шага через: {description}."
    title = (step.get("title") or "").strip()
    if title:
        return f"Подобран под задачу шага «{title}»."
    return "Рекомендован для реализации этого шага."

def get_step_bank_products(item, products=None):
    return match_products_for_step(agent2_step_dict(item), products)["products"]

def attach_bank_products_to_agent2_responses(responses, products=None):
    products = products if products is not None else load_product_catalog()
    for response in responses:
        match = match_products_for_step(agent2_step_dict(response), products)
        response.bank_products = match["products"]
        response.product_match_status = match["status"]
        response.product_clarification_question = match.get("question") or ""
    return responses

def product_usage_report(items, products=None):
    products = products if products is not None else load_product_catalog()
    total = len(items)
    required = math.ceil(total * PRODUCT_STEP_RATIO) if total else 0
    usage = []
    for item in items:
        usage.append(detect_step_products(item, products))
    used_count = sum(1 for matches in usage if matches)
    return {
        "total": total,
        "required": required,
        "used_count": used_count,
        "ok": used_count >= required,
        "usage": usage,
    }

def load_known_steps():
    path = os.path.join(os.path.dirname(__file__), "steps.xlsx")
    if not os.path.exists(path):
        path = "steps.xlsx"
    if not os.path.exists(path):
        return []
    mtime = os.path.getmtime(path)
    cache = _KNOWN_STEPS_CACHE
    if cache["mtime"] == mtime:
        return cache["steps"]
    try:
        df = pd.read_excel(path)
        steps = [normalize_product_text(row) for row in df["steps"] if pd.notna(row)]
    except Exception:
        steps = []
    cache["mtime"] = mtime
    cache["steps"] = steps
    return steps

def mark_implemented_steps_local(items):
    known_steps = load_known_steps()
    updated = []
    for item in items:
        row = dict(item)
        title = normalize_product_text(row.get("title", ""))
        if known_steps and title:
            row["implemented"] = any(
                title in step or step in title
                for step in known_steps
                if len(step) >= 8
            )
        else:
            row["implemented"] = False
        updated.append(row)
    return updated

def ensure_agent2_product_usage_local(items, products, expected_count):
    """
    Validate LLM-assigned products (Variant E).
    - Steps with a valid product field → count as product-used
    - Steps without → try semantic matching with min_score=14 as fallback
    - If below 70% threshold → accept as-is (no force-injection)
    """
    expected_count = expected_count or len(items)
    current = [dict(item) for item in items[:expected_count]]
    products = products if products is not None else load_product_catalog()
    by_name = {p["name"]: p for p in products}

    # Count steps that already have a valid product from LLM
    for item in current:
        product_field = str(item.get("product") or "").strip()
        if product_field and product_field in by_name:
            item["_product_valid"] = True
        else:
            item["_product_valid"] = False

    product_count = sum(1 for item in current if item["_product_valid"])
    required = math.ceil(expected_count * PRODUCT_STEP_RATIO)

    if product_count < required:
        # LLM didn't assign enough products. Try semantic fallback with strict threshold.
        for item in current:
            if item["_product_valid"]:
                continue
            matched = recommend_step_products(item, products, limit=1)
            if matched:
                item["product"] = matched[0]["name"]
                item["_product_valid"] = True

    if len(current) < expected_count:
        raise RuntimeError(f"AI вернул недостаточно шагов: {len(current)} из {expected_count}")
    return current[:expected_count]


def annotate_steps_with_products(items, products, required):
    """
    DEPRECATED — kept for backward compatibility, no longer called.
    Variant E: LLM assigns products; this function is never invoked.
    """
    return [dict(item) for item in items]

def generate_agent2_items(message, expected_count):
    items = call_openai(get_prompt_b(), message, json_instructions=JSON_INSTRUCTIONS_AGENT2)[:expected_count]
    if len(items) < expected_count:
        raise RuntimeError(f"AI вернул недостаточно шагов: {len(items)} из {expected_count}")
    return mark_implemented_steps_local(items)[:expected_count]


def ensure_agent2_product_column():
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if "agent2_responses" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent2_responses")}
    if "product" not in columns:
        db.session.execute(text("ALTER TABLE agent2_responses ADD COLUMN product VARCHAR(500)"))
        db.session.commit()

def ensure_situation_check_schema():
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if "situation_checks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("situation_checks")}
    statements = []
    if "slot_questions" not in columns:
        statements.append("ALTER TABLE situation_checks ADD COLUMN slot_questions TEXT")
    if "slots_filled" not in columns:
        statements.append("ALTER TABLE situation_checks ADD COLUMN slots_filled TEXT")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()

def ensure_industry_check_schema():
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if "industry_checks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("industry_checks")}
    statements = []
    if "problem_nature" not in columns:
        statements.append("ALTER TABLE industry_checks ADD COLUMN problem_nature VARCHAR(30)")
    if "is_universal_problem" not in columns:
        statements.append("ALTER TABLE industry_checks ADD COLUMN is_universal_problem BOOLEAN NOT NULL DEFAULT 0")
    if "dependency_test_passed" not in columns:
        statements.append("ALTER TABLE industry_checks ADD COLUMN dependency_test_passed BOOLEAN NOT NULL DEFAULT 0")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()

def call_situation_check(text):
    return analyze_situation_slots(text, call_openai_raw)

def save_situation_check(input_id, text, result):
    attempt_number = SituationCheck.query.filter_by(input_id=input_id).count() + 1
    clarify_payload = result.get("clarify_payload") or result.get("slot_questions") or {}
    check = SituationCheck(
        input_id=input_id,
        original_text=text,
        normalized_text=result.get("normalized_text") or None,
        ok=result["ok"],
        score=result["score"],
        missing=json.dumps(result.get("missing") or [], ensure_ascii=False),
        reason=result.get("reason") or None,
        rewrite_hint=result.get("rewrite_hint") or None,
        example=result.get("example") or None,
        slot_questions=json.dumps(clarify_payload, ensure_ascii=False) if clarify_payload else None,
        slots_filled=json.dumps(result.get("slots_filled") or {}, ensure_ascii=False) or None,
        source=result.get("source") or "llm",
        status="passed" if result["ok"] else "failed",
        attempt_number=attempt_number,
    )
    db.session.add(check)
    return check


def apply_situation_to_input(user_input, situation_description, situation_result=None):
    fields = extract_user_input_fields(user_input.input_text)
    final_description = situation_description
    if situation_result and situation_result.get("ok") and situation_result.get("normalized_text"):
        final_description = situation_result["normalized_text"]
    user_input.input_text = build_input_text(
        company_size=fields["company_size"],
        company_industry=fields["company_industry"],
        situation_description=final_description,
        product_name=fields["product_name"],
        signals=fields["signals"],
    )
    return final_description


def extract_user_input_fields(input_text):
    text = input_text or ""
    company_size = ""
    company_industry = ""
    product_name = ""
    situation_description = ""
    size_match = re.search(r"^Размер компании:\s*(.*)$", text, flags=re.MULTILINE)
    industry_match = re.search(r"^Отрасль компании:\s*(.*)$", text, flags=re.MULTILINE)
    product_match = re.search(r"^Продукт:\s*(.*)$", text, flags=re.MULTILINE)
    situation_match = re.search(
        r"Описание ситуации у компании:\s*(.*?)(?:\nСигнал:|\nСигналы:|\nПродукт:|\nДополнительное условие:|\Z)",
        text,
        flags=re.DOTALL
    )
    if size_match:
        company_size = size_match.group(1).strip()
    if industry_match:
        company_industry = industry_match.group(1).strip()
    if product_match:
        product_name = product_match.group(1).strip()
    signals = parse_signals_from_text(text)
    if situation_match:
        situation_description = situation_match.group(1).strip()
    return {
        "company_size": company_size,
        "company_industry": company_industry,
        "product_name": product_name,
        "signals": signals,
        "signal": signals_joined(signals),
        "situation_description": situation_description,
    }

def build_input_text(company_size=None, company_industry=None, situation_description=None, product_name=None, signals=None, signal=None):
    lines = []
    if company_size:
        lines.append(f"Размер компании: {company_size}")
    if company_industry:
        lines.append(f"Отрасль компании: {company_industry}")
    if situation_description:
        lines.append(f"Описание ситуации у компании: {situation_description}")
    lines.extend(format_signal_lines(normalize_signals_list(signals=signals, signal=signal)))
    if product_name:
        lines.append(f"Продукт: {product_name}")
        lines.append("Дополнительное условие: если указанный продукт релевантен ситуации клиента, обязательно явно используй именно этот продукт в предлагаемых стратегиях. Не игнорируй продукт и не заменяй его абстрактными формулировками.")
    return "\n".join(lines).strip()

GENERIC_INDUSTRY_QUESTION_PATTERNS = (
    r"уточните\s+отрасл",
    r"какова\s+отрасл",
    r"какую\s+отрасл",
    r"какой\s+отрасл",
    r"в\s+какой\s+отрасл",
    r"какая\s+отрасл",
    r"укажите\s+отрасл",
    r"назовите\s+отрасл",
    r"представляете.{0,20}отрасл",
)

def industry_context_text(company_industry="", situation_description="", extracted_industry_context=""):
    parts = [
        (company_industry or "").strip(),
        (extracted_industry_context or "").strip(),
        extract_industry_from_description(situation_description),
    ]
    return next((part for part in parts if part), "")

def extract_industry_from_description(situation_description):
    text = (situation_description or "").strip()
    if not text:
        return ""
    patterns = (
        r"(?:отрасл[ьи]\s+(?:компани[ияе]|бизнеса?)\s*[-—:]\s*)([^.;,\n]{3,80})",
        r"(?:работаем\s+в\s+)([^.;,\n]{3,80})",
        r"(?:компания\s+в\s+сфере\s+)([^.;,\n]{3,80})",
        r"((?:производственн|торгов\w+|строительн\w+|розничн\w+|оптов\w+|сервисн\w+|логистическ\w+)\w*\s+компани\w+)",
        r"(?:\bв\s+)(ритейл[еу]?|розниц[еу]|производств[еу]|строительств[еу]|логистик[еу]|it|айти|медицин[еу]|общепит[еу]|horeca|хорек[ае]|оптов[ойе]\s+торговл[еи]|e-?commerce|ecommerce)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""

def industry_benchmark_required(situation_description, signal=None):
    text = f"{situation_description or ''} {signal or ''}".lower()
    benchmark_patterns = (
        r"выше.{0,30}отрасл",
        r"ниже.{0,30}отрасл",
        r"по\s+отрасл",
        r"отраслев.{0,20}норм",
        r"отраслев.{0,20}средн",
        r"характерн.{0,20}для\s+отрасл",
    )
    return any(re.search(pattern, text) for pattern in benchmark_patterns)

def is_generic_industry_question(question):
    question = (question or "").strip().lower()
    if not question:
        return True
    return any(re.search(pattern, question) for pattern in GENERIC_INDUSTRY_QUESTION_PATTERNS)

def is_specific_strategy_impact(strategy_impact):
    text = re.sub(r"\s+", " ", (strategy_impact or "").strip())
    if len(text) < 40:
        return False
    generic_markers = (
        "стратегия будет более релевантной",
        "нужна отрасль",
        "уточнить отрасль",
        "зависит от отрасли",
        "важно знать отрасль",
        "без отрасли сложно",
    )
    lowered = text.lower()
    return not any(marker in lowered for marker in generic_markers)

INDUSTRY_CHECK_DB_FIELDS = {
    "initial_industry",
    "extracted_industry_context",
    "industry_detail_required",
    "industry_context_sufficient",
    "industry_context_found_in_description",
    "trigger",
    "reason",
    "question",
}

def industry_check_db_payload(check_data):
    return {key: check_data[key] for key in INDUSTRY_CHECK_DB_FIELDS if key in check_data}

def industry_check_not_required(reason, company_industry="", extracted_industry_context="", found_in_description=False):
    return {
        "industry_detail_required": False,
        "industry_context_sufficient": True,
        "industry_context_found_in_description": found_in_description or bool((company_industry or "").strip() or (extracted_industry_context or "").strip()),
        "trigger": "none",
        "initial_industry": company_industry or "",
        "extracted_industry_context": extracted_industry_context or "",
        "strategy_impact_if_unknown": "",
        "reason": reason,
        "question": "",
    }

def finalize_industry_check(result, situation_description, signal=None, company_industry=""):
    result = dict(result)
    known_industry = industry_context_text(
        company_industry,
        situation_description,
        result.get("extracted_industry_context", ""),
    )
    if known_industry:
        result["industry_detail_required"] = False
        result["industry_context_sufficient"] = True
        result["industry_context_found_in_description"] = True
        result["extracted_industry_context"] = known_industry
        result["question"] = ""
        result["strategy_impact_if_unknown"] = ""
        if not result.get("reason"):
            result["reason"] = "Отраслевой контекст уже есть во входных данных."
        return result

    if industry_benchmark_required(situation_description, signal):
        result["industry_detail_required"] = True
        result["industry_context_sufficient"] = False
        result["trigger"] = "benchmark"
        if not result.get("strategy_impact_if_unknown"):
            result["strategy_impact_if_unknown"] = (
                "Нужен отраслевой бенчмарк, чтобы сравнить показатель клиента с нормой по отрасли и выбрать стратегию."
            )
        if is_generic_industry_question(result.get("question")):
            result["question"] = (
                "В какой отрасли нужно сравнить показатель с рыночной нормой, чтобы оценить масштаб проблемы?"
            )
        if not result.get("reason"):
            result["reason"] = "В ситуации есть сравнение с отраслью — без неё нельзя оценить масштаб проблемы."
        return result

    if not result.get("industry_detail_required"):
        result["question"] = ""
        result["strategy_impact_if_unknown"] = ""
        return result

    strategy_impact = result.get("strategy_impact_if_unknown", "")
    question = result.get("question", "")
    if not is_specific_strategy_impact(strategy_impact) or is_generic_industry_question(question):
        fallback_reason = (
            result.get("reason")
            or "Отрасль не меняет выбор стратегии: в описании достаточно контекста для релевантного решения."
        )
        return industry_check_not_required(
            fallback_reason,
            company_industry=company_industry,
            extracted_industry_context=result.get("extracted_industry_context", ""),
        )

    return result

def normalize_industry_check(data, fallback_industry):
    trigger = str(data.get("trigger") or "none").strip()
    allowed_triggers = {
        "sales_demand", "costs_margin", "logistics_inventory", "supply_chain",
        "regulation", "government", "benchmark", "none",
    }
    if trigger not in allowed_triggers:
        trigger = "none"
    required = to_bool(data.get("industry_detail_required"))
    question = str(data.get("question") or "").strip()
    strategy_impact = str(data.get("strategy_impact_if_unknown") or "").strip()
    if not required:
        question = ""
        strategy_impact = ""
    return {
        "industry_detail_required": required,
        "industry_context_sufficient": to_bool(data.get("industry_context_sufficient")),
        "industry_context_found_in_description": to_bool(data.get("industry_context_found_in_description")),
        "trigger": trigger,
        "initial_industry": str(data.get("initial_industry") or fallback_industry or "").strip(),
        "extracted_industry_context": str(data.get("extracted_industry_context") or "").strip(),
        "strategy_impact_if_unknown": strategy_impact,
        "reason": str(data.get("reason") or "").strip(),
        "question": question,
    }

def call_industry_check(company_size, company_industry, situation_description, product_name, signal=None):
    known_industry = industry_context_text(company_industry, situation_description)
    if known_industry:
        return normalize_industry_check(
            industry_check_not_required(
                "Отраслевой контекст уже указан во входных данных.",
                company_industry=company_industry or known_industry,
                extracted_industry_context=known_industry,
                found_in_description=True,
            ),
            company_industry,
        )

    message = json.dumps({
        "company_size": company_size,
        "company_industry": company_industry,
        "situation_description": situation_description,
        "product_name": product_name,
        "signal": signal,
    }, ensure_ascii=False)
    data = call_openai_raw(PROMPT_INDUSTRY_CHECK, message)
    result = normalize_industry_check(data, company_industry)
    return finalize_industry_check(result, situation_description, signal, company_industry)

def call_methodology_check(situation_description):
    message = json.dumps({"situation_description": situation_description}, ensure_ascii=False)
    data = call_openai_raw(PROMPT_METHODOLOGY_CHECK, message)
    return {
        "violation": to_bool(data.get("violation", False)),
        "violation_type": str(data.get("violation_type", "none")),
        "reason": str(data.get("reason", "")),
        "rewrite_hint": str(data.get("rewrite_hint", "")),
        "example": str(data.get("example", "")),
    }

def call_dependency_test(situation_description):
    message = json.dumps({"situation_description": situation_description}, ensure_ascii=False)
    data = call_openai_raw(PROMPT_DEPENDENCY_TEST, message)
    return {
        "problem_nature": str(data.get("problem_nature", "")),
        "is_universal_problem": to_bool(data.get("is_universal_problem", False)),
        "dependency_test_passed": to_bool(data.get("dependency_test_passed", False)),
        "reason": str(data.get("reason", "")),
    }

def create_clarification_if_needed(input_id):
    user_input = get_input_or_403(input_id)
    final_input = build_final_input(user_input, None)
    clarify = call_openai_raw(PROMPT_CLARIFY, final_input)
    if clarify.get("status") == "need_clarification":
        questions = clarify.get("questions") or []
        if questions:
            existing = Clarification.query.filter_by(input_id=input_id).first()
            if existing:
                existing.questions = json.dumps(questions, ensure_ascii=False)
                existing.answers = None
                existing.status = "pending"
            else:
                db.session.add(Clarification(input_id=input_id, questions=json.dumps(questions, ensure_ascii=False)))
            return True
    return False

def run_context_checks_after_situation(input_id):
    user_input = get_input_or_403(input_id)
    fields = extract_user_input_fields(user_input.input_text)

    # --- Problem Nature Router: Dependency Test ---
    dep_test = call_dependency_test(fields["situation_description"])

    # --- Industry Check с учётом природы проблемы ---
    check_data = call_industry_check(
        fields["company_size"],
        fields["company_industry"],
        fields["situation_description"],
        fields["product_name"],
        signals_joined(fields["signals"]),
    )

    # Если проблема универсальная — принудительно снимаем флаг
    if dep_test["is_universal_problem"]:
        check_data["industry_detail_required"] = False

    payload = industry_check_db_payload(check_data)
    payload["problem_nature"] = dep_test["problem_nature"]
    payload["is_universal_problem"] = dep_test["is_universal_problem"]
    payload["dependency_test_passed"] = dep_test["dependency_test_passed"]

    industry_check = IndustryCheck(
        input_id=user_input.id,
        status="needs_answer" if check_data["industry_detail_required"] and check_data["question"] else "checked",
        **payload
    )
    db.session.add(industry_check)
    db.session.commit()
    if industry_check.status == "needs_answer":
        return redirect(auth_url("context_clarify", input_id=user_input.id))
    if create_clarification_if_needed(user_input.id):
        db.session.commit()
        return redirect(auth_url("clarify", input_id=user_input.id))
    db.session.commit()
    return redirect(auth_url("process_after_clarify", input_id=user_input.id))

def call_openai_check_str(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=120)
    system_prompt = "Ты на вход получишь одну или несколько стратегий.\nСравни стратегии, которые тебе передали, с уже существующими из списка по названию и логике -\n"
    try:
        df = pd.read_excel('strategies.xlsx')
        results = [{"title": str(row.get("strateg_nm", "")), "description": str(row.get("logic", ""))} for _, row in df.iterrows()]
        for el in results:
            system_prompt += f"Название стратегии: {el['title']}; Логика: {el['description']}\n"
    except Exception:
        pass
    system_prompt += "Если стратегия есть в этом списке или что-то похожее, то пометь как Реализована.\nВАЖНО верни те же самые данные, которые ты получил с изменением только одного поля implemented"
    message = json.dumps(item, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")

def call_openai_check_stp(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=120)
    system_prompt = "Ты на вход получишь один или несколько шагов\nСравни стратегию или шаг, которую тебе передали, с уже существующими из списка по названию -\n"
    try:
        df = pd.read_excel('steps.xlsx')
        results = [{"title": row} for row in df["steps"] if pd.notna(row)]
        for el in results:
            system_prompt += el['title'] + "\n"
    except Exception:
        pass
    system_prompt += "Если стратегия или шаг есть в этом списке или что-то похожее, то пометь как Реализована.\nВАЖНО верни те же самые данные, которые ты получил с изменением только одного поля implemented"
    message = json.dumps(item, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")

def final_agent1_payload(response):
    if response.edit:
        return {"title": response.edit.edited_title, "description": response.edit.edited_description, "logic": response.edit.edited_logic, "criteria": response.edit.edited_criteria, "was_edited": True}
    return {"title": response.title, "description": response.description, "logic": response.logic, "criteria": response.criteria, "was_edited": False}

def combine_payloads(payloads, title_prefix):
    count = len(payloads)
    if count == 1: return payloads[0]
    return {
        "title": f"{title_prefix}: {count} Шага(ов)",
        "description": "\n\n".join([f"{i}. {it['title']}\n{it['description']}" for i, it in enumerate(payloads, start=1)]),
        "logic": "\n\n".join([f"{i}. {it['title']}\n{it['logic']}" for i, it in enumerate(payloads, start=1)]),
        "criteria": "\n\n".join([f"{i}. {it['title']}\n{it['criteria']}" for i, it in enumerate(payloads, start=1)]),
        "was_edited": any(it.get("was_edited") for it in payloads),
    }

def final_agent2_payload(response):
    return {"title": response.title, "description": response.description, "logic": response.logic, "criteria": response.criteria, "was_edited": response.was_edited}

def validate_payload(payload):
    fields = {}
    for key in ["title", "description", "logic", "criteria"]:
        value = (payload.get(key) or "").strip()
        if not value: raise ValueError("empty field")
        fields[key] = value
    return fields

def flash_ai_error():
    flash("Ошибка обработки ответа AI. Попробуйте ещё раз.", "danger")

def validate_custom_item(fields, prompt_type):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: return True, ""
    base_prompt = PROMPT_A if prompt_type == "agent1" else get_prompt_b()
    system_prompt = f"Ты — строгий валидатор. Тебе дан промпт с правилами для генерации вариантов:\n{base_prompt}\nПользователь добавил свой вариант вручную. Проверь, соответствует ли он правилам промпта выше.\nВерни JSON строго в формате:\n{{\"ok\": true}} — если вариант соответствует правилам,\n{{\"ok\": false, \"reason\": \"краткое объяснение на русском, почему не соответствует\"}} — если не соответствует.\nПроверяй только смысловое соответствие правилам, не придирайся к формулировкам."
    item_text = json.dumps(fields, ensure_ascii=False)
    try:
        client = OpenAI(api_key=api_key, timeout=30)
        response = client.chat.completions.create(
            model="gpt-4.1-mini", response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": item_text}],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        if data.get("ok") is False: return False, data.get("reason", "Вариант не соответствует правилам.")
        return True, ""
    except Exception as e:
        print("VALIDATE ERROR:", repr(e), flush=True)
        # Fail closed: if validation call fails, block the item rather than silently bypass
        return False, "Не удалось проверить вариант, попробуйте позже."

def create_more_agent1_responses(input_id):
    user_input = UserInput.query.get_or_404(input_id)
    clarification = Clarification.query.filter_by(input_id=input_id).first()
    final_input = build_final_input(user_input, clarification)
    previous = Agent1Response.query.filter_by(input_id=input_id).order_by(Agent1Response.round_number.asc()).all()
    next_round = max([item.round_number for item in previous], default=0) + 1
    rejected_items = [item for item in previous if item.status == "rejected"]
    rejection_parts = []
    for i, item in enumerate(rejected_items, 1):
        part = f"Стратегия {i}: «{item.title}»\nОписание: {item.description}"
        if item.rejection_reason:
            part += f"\nПричина отказа пользователя: {item.rejection_reason}"
        rejection_parts.append(part)
    rejection_history = "\n\n".join(rejection_parts)
    message = (
        f"Запрос пользователя:\n{final_input}\n\n"
        f"Пользователь уже рассмотрел и отклонил следующие стратегии:\n{rejection_history}\n\n"
        f"Важно: обязательно учти причины отказа и предложи стратегию принципиально другого типа механики.\n"
        f"Сформируй ровно ОДНУ новую стратегию в формате JSON с одним вариантом."
    )
    items = call_openai(PROMPT_A, message)
    final_items = call_openai_check_str(items)
    if not final_items:
        raise RuntimeError("AI вернул пустой список стратегий")
    for item in final_items:
        db.session.add(Agent1Response(input_id=input_id, round_number=next_round, status="pending",
                                      **{k: v for k, v in item.items() if k != "product"}))

def create_more_agent2_responses(selected_id):
    selected = Agent1Selected.query.get_or_404(selected_id)
    previous_responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()

    accepted_items  = [r for r in previous_responses if r.status == 'accepted']
    rejected_items  = [r for r in previous_responses if r.status == 'rejected']
    pending_items   = [r for r in previous_responses if r.status == 'pending']

    context_parts = []

    if accepted_items:
        lines = "\n".join([f"- «{r.title}»: {r.description}" for r in accepted_items])
        context_parts.append(
            f"Пользователь ВЫБРАЛ эти шаги (новый шаг должен логически продолжать их, не повторять):\n{lines}"
        )

    if rejected_items:
        lines = []
        for r in rejected_items:
            line = f"- «{r.title}»: {r.description}"
            if r.rejection_reason:
                line += f"\n  Причина отказа: {r.rejection_reason}"
            lines.append(line)
        context_parts.append(
            f"Пользователь ОТКЛОНИЛ эти шаги (не предлагай похожие, обязательно учти причины отказа):\n" + "\n".join(lines)
        )

    if pending_items:
        titles = ", ".join([f"«{r.title}»" for r in pending_items])
        context_parts.append(f"Уже предложены, но ещё не оценены (не повторяй): {titles}")

    context = "\n\n".join(context_parts) if context_parts else "Это первая дополнительная генерация."

    message = (
        f"Стратегия: {selected.final_title}\n"
        f"Описание: {selected.final_description}\n"
        f"Логика: {selected.final_logic}\n"
        f"Критерии: {selected.final_criteria}\n\n"
        f"{context}\n\n"
        f"Задача: сгенерируй ровно ОДИН новый шаг, который:\n"
        f"- логически продолжает уже выбранные шаги;\n"
        f"- не повторяет ни один из ранее предложенных (ни по смыслу, ни по названию);\n"
        f"- учитывает причины отказа от отклонённых шагов.\n"
        f"Верни ответ в формате JSON с одним шагом."
    )

    final_items = generate_agent2_items(message, 1)
    start_number = max([r.item_number for r in previous_responses], default=0)
    for index, item in enumerate(final_items, start=1):
        item["item_number"] = start_number + index
        db.session.add(Agent2Response(selected_id=selected_id, status="pending",
                                      **{k: v for k, v in item.items() if not k.startswith("_")}))

def next_agent1_item_number(input_id):
    return max([item.item_number for item in Agent1Response.query.filter_by(input_id=input_id).all()], default=0) + 1

def next_agent2_item_number(selected_id):
    return max([item.item_number for item in Agent2Response.query.filter_by(selected_id=selected_id).all()], default=0) + 1

def build_final_input(user_input, clarification):
    base = user_input.input_text
    industry_check = IndustryCheck.query.filter_by(input_id=user_input.id).order_by(IndustryCheck.created_at.desc()).first()
    industry_parts = []
    if industry_check:
        if industry_check.extracted_industry_context:
            industry_parts.append(f"Отраслевая специфика из описания: {industry_check.extracted_industry_context}")
        if industry_check.answer:
            industry_parts.append(f"Ответ пользователя по отраслевой специфике: {industry_check.answer}")
        if industry_check.reason:
            industry_parts.append(f"Причина проверки отрасли: {industry_check.reason}")
    if industry_parts:
        base += "\n\nУточнение отрасли:\n" + "\n".join(industry_parts)
    if clarification and clarification.answers:
        answers = json.loads(clarification.answers)
        extra = "\n".join([f"{key}: {value if value else 'не указано'}" for key, value in answers.items()])
        return base + "\n\nУточнения:\n" + extra
    return base

# === PDF GENERATOR ===
def build_results_pdf(selected, final, clarification=None, steps=None):
    buffer = BytesIO()
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
    if "DejaVuSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_font_path))

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=15*mm, leftMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm, title="Итоговый результат"
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='DocTitle', fontName='DejaVuSans-Bold', fontSize=16, leading=20, spaceAfter=12, alignment=1))
    styles.add(ParagraphStyle(name='BlockHeader', fontName='DejaVuSans-Bold', fontSize=12, leading=15, spaceBefore=0, spaceAfter=6, textColor=colors.HexColor('#1e3a8a')))
    styles.add(ParagraphStyle(name='BlockBody', fontName='DejaVuSans', fontSize=10, leading=14, spaceBefore=2, spaceAfter=6))

    story = []
    story.append(Paragraph("Итоговый результат", styles['DocTitle']))

    def make_block(title, html_content):
        chunks = [c.strip() for c in html_content.split('<br/><br/>') if c.strip()]
        data = [[Paragraph(title, styles['BlockHeader'])]]
        for chunk in chunks:
            data.append([Paragraph(chunk, styles['BlockBody'])])

        t = LongTable(data, colWidths=[465], splitByRow=1)
        t.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#334155')),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#334155')),
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#f1f5f9')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12), 
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, -1), 'DejaVuSans'),
        ]))
        return t

    def fmt_criteria(text):
        if not text: return "<b>Критерии (кому не подходит):</b> —"
        parts = [p.strip().lstrip('0123456789.-• )') for p in re.split(r'\n|;|•', text) if p.strip()]
        if not parts: return f"<b>Критерии (кому не подходит):</b> {text.replace(chr(10), '<br/>')}"
        numbered = [f"{i+1}. {p}" for i, p in enumerate(parts)]
        return f"<b>Критерии (кому не подходит):</b><br/>" + "<br/>".join(numbered)

    # BLOCK 1
    b1_html = ""
    input_text = selected.user_input.input_text if selected.user_input else ""
    for line in input_text.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith(('Размер компании:', 'Сегмент клиента:')):
            val = line.split(':', 1)[1].strip()
            b1_html += f"<b>Сегмент клиента:</b> {val}<br/><br/>"
        elif line.startswith('Отрасль компании:'):
            val = line.split(':', 1)[1].strip()
            b1_html += f"<b>Отрасль компании:</b> {val}<br/><br/>"
        elif line.startswith('Описание ситуации у компании:'):
            val = line.split(':', 1)[1].strip()
            b1_html += f"<b>Описание ситуации у компании:</b> {val}<br/><br/>"
        elif line.startswith('Портрет:'):
            val = line.split(':', 1)[1].strip()
            b1_html += f"<b>Портрет:</b> {val}<br/><br/>"
        else:
            b1_html += f"{line.replace(chr(10), '<br/>')}<br/><br/>"

    if clarification and clarification.status == "done" and clarification.answers:
        try:
            questions = json.loads(clarification.questions)
            answers = json.loads(clarification.answers)
            qa_text = "<br/><br/>".join([f"<b>{q.get('question', q.get('key'))}:</b> {answers.get(q.get('key'), '—')}" for q in questions])
            b1_html += f"<br/><b>Ответы на уточняющие вопросы:</b><br/>{qa_text}<br/><br/>"
        except Exception:
            pass
    industry_check = IndustryCheck.query.filter_by(input_id=selected.input_id).order_by(IndustryCheck.created_at.desc()).first()
    if industry_check:
        industry_lines = []
        if industry_check.extracted_industry_context:
            industry_lines.append(f"<b>Из описания:</b> {industry_check.extracted_industry_context}")
        if industry_check.answer:
            industry_lines.append(f"<b>Ответ пользователя:</b> {industry_check.answer}")
        if industry_check.reason:
            industry_lines.append(f"<b>Причина проверки:</b> {industry_check.reason}")
        if industry_lines:
            b1_html += "<br/><b>Отраслевая специфика:</b><br/>" + "<br/><br/>".join(industry_lines) + "<br/><br/>"
    story.append(make_block("Блок 1: Вводные данные и уточнения", b1_html))
    story.append(Spacer(1, 10))

    # BLOCK 2
    b2_html = f"<b>{selected.final_title}</b><br/><br/>"
    b2_html += f"<b>Описание:</b> {selected.final_description.replace(chr(10), '<br/>')}<br/><br/>"
    b2_html += f"<b>Логика:</b> {selected.final_logic.replace(chr(10), '<br/>')}<br/><br/>"
    b2_html += fmt_criteria(selected.final_criteria)
    story.append(make_block("Блок 2: Выбранная стратегия", b2_html))
    story.append(Spacer(1, 10))

    # BLOCK 3
    b3_html = ""
    if steps:
        for idx, step in enumerate(steps, start=1):
            full_step = f"Шаг {idx}: {step.title}: {step.description}"
            first_colon = full_step.find(':')
            second_colon = full_step.find(':', first_colon + 1) if first_colon != -1 else -1
            if second_colon != -1:
                header = f"<b>{full_step[:second_colon+1]}</b> {full_step[second_colon+1:].strip()}"
            else:
                header = f"<b>{full_step}</b>"
            b3_html += header.replace(chr(10), '<br/>') + "<br/><br/>"
            b3_html += f"<b>Логика шага:</b> {step.logic.replace(chr(10), '<br/>')}<br/><br/>"
            b3_html += fmt_criteria(step.criteria) + "<br/><br/>"
    if not b3_html:
        b3_html = "План действий не выбран."
    story.append(make_block("Блок 3: Пошаговый план реализации", b3_html))

    doc.build(story)
    buffer.seek(0)
    return buffer

# === ROUTES ===
def register_routes(app):
    @app.before_request
    def restore_auth_from_token():
        g.auth_token = request.values.get("_auth") or request.args.get("_auth")
        g.token_user_id = None
        if not g.auth_token: return
        try:
            user_id = int(get_auth_serializer().loads(g.auth_token))
        except (BadSignature, TypeError, ValueError):
            g.auth_token = None; return
        if db.session.get(User, user_id):
            g.token_user_id = user_id
            session["user_id"] = user_id

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user_id():
            return redirect(url_for("index"))
        if request.method == "POST":
            username  = request.form.get("username", "").strip()
            email     = request.form.get("email", "").strip().lower()
            password  = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            errors = []
            if len(username) < 3:
                errors.append("Имя пользователя — не менее 3 символов.")
            if not re.match(r'^[a-zA-Z0-9_а-яА-ЯёЁ]+$', username):
                errors.append("Имя пользователя: только буквы, цифры и знак «_».")
            if not email or "@" not in email:
                errors.append("Введите корректный email.")
            if len(password) < 6:
                errors.append("Пароль — не менее 6 символов.")
            if password != password2:
                errors.append("Пароли не совпадают.")
            if not errors:
                if User.query.filter_by(username=username).first():
                    errors.append("Это имя пользователя уже занято.")
                if User.query.filter_by(email=email).first():
                    errors.append("Этот email уже зарегистрирован.")
            if errors:
                for err in errors:
                    flash(err, "danger")
                return render_template("register.html", f_username=username, f_email=email)
            ip_address = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
            now = datetime.utcnow()
            user = User(username=username, email=email, ip_address=ip_address,
                        registered_at=now, first_login_at=now, last_login_at=now, login_count=1)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session["user_id"] = user.id
            g.auth_token = get_auth_serializer().dumps(user.id)
            flash(f"Добро пожаловать, {username}! Регистрация прошла успешно.", "success")
            return redirect(auth_url("index"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user_id():
            return redirect(url_for("index"))
        if request.method == "POST":
            login_input = request.form.get("username", "").strip()
            password    = request.form.get("password", "")
            # Ищем по username или email
            user = User.query.filter_by(username=login_input).first()
            if not user:
                user = User.query.filter_by(email=login_input.lower()).first()
            ip_address = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
            now = datetime.utcnow()
            authenticated = False
            if user and user.password_hash:
                authenticated = user.check_password(password)
            if not authenticated:
                flash("Неверный логин / email или пароль.", "danger")
                return render_template("login.html", f_username=login_input)
            user.last_login_at = now
            user.login_count += 1
            user.ip_address = ip_address
            db.session.commit()
            session["user_id"] = user.id
            g.auth_token = get_auth_serializer().dumps(user.id)
            return redirect(auth_url("index"))
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.pop("user_id", None)
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index(): return render_template("index.html", signals=load_all_signals())

    @app.route("/history")
    @login_required
    def history():
        user_inputs = UserInput.query.filter_by(user_id=current_user_id()).order_by(UserInput.created_at.desc()).all()
        rows = []
        for inp in user_inputs:
            max_round_a = db.session.query(db.func.max(Agent1Response.round_number)).filter_by(input_id=inp.id).scalar()
            rounds_a = max_round_a if max_round_a else 0
            selected = Agent1Selected.query.filter_by(input_id=inp.id).first()
            rounds_b = 0
            completed = False
            fields = extract_user_input_fields(inp.input_text)
            latest_situation = SituationCheck.query.filter_by(input_id=inp.id).order_by(SituationCheck.created_at.desc()).first()
            pending_industry = IndustryCheck.query.filter_by(input_id=inp.id, status="needs_answer").first()
            if latest_situation and not latest_situation.ok:
                open_url = auth_url("situation_clarify", input_id=inp.id)
            elif not fields["company_size"] or pending_industry:
                open_url = auth_url("context_clarify", input_id=inp.id)
            else:
                open_url = auth_url("review", input_id=inp.id)
            if selected:
                count_b = Agent2Response.query.filter_by(selected_id=selected.id).count()
                rounds_b = count_b
                final = Agent2Final.query.filter_by(selected_id=selected.id).first()
                if final:
                    completed = True
                    open_url = auth_url("result", selected_id=selected.id)
                else:
                    open_url = auth_url("agent2", selected_id=selected.id)
            row = SimpleNamespace(
                input=inp,
                rounds_a=rounds_a,
                rounds_b=rounds_b,
                completed=completed,
                open_url=open_url
            )
            rows.append(row)
        return render_template("history.html", rows=rows)

    @app.route("/process", methods=["POST"])
    @login_required
    def process():
        product_name = request.form.get("product_name", "").strip()
        signals = [item.strip() for item in request.form.getlist("signal") if item.strip()]
        situation_description = request.form.get("situation_description", "").strip()
        if not situation_description and not signals:
            flash("Опишите ситуацию компании или выберите хотя бы один сигнал.", "warning")
            return redirect(url_for("index"))
        if signals and not signals_all_exist(signals):
            flash("Выберите сигналы только из списка.", "warning")
            return redirect(url_for("index"))
        input_text = build_input_text(
            situation_description=situation_description,
            product_name=product_name,
            signals=signals,
        )
        user_input = UserInput(user_id=current_user_id(), input_text=input_text, session_token=str(uuid.uuid4()))
        db.session.add(user_input); db.session.commit()
        try:
            if situation_description:
                # --- Methodology Check: фильтр мусорных вводов ---
                methodology = call_methodology_check(situation_description)
                if methodology["violation"]:
                    situation_result = {
                        "ok": False,
                        "score": 0,
                        "reason": methodology["reason"],
                        "rewrite_hint": methodology["rewrite_hint"],
                        "example": methodology["example"],
                        "missing": [],
                        "normalized_text": situation_description,
                        "clarify_payload": {},
                    }
                    situation_check = save_situation_check(user_input.id, situation_description, situation_result)
                    db.session.commit()
                    return redirect(auth_url("situation_clarify", input_id=user_input.id))

                situation_result = call_situation_check(situation_description)
                situation_check = save_situation_check(user_input.id, situation_description, situation_result)
                if situation_check.ok:
                    apply_situation_to_input(user_input, situation_description, situation_result)
                db.session.commit()
                if not situation_check.ok:
                    return redirect(auth_url("situation_clarify", input_id=user_input.id))
            return redirect(auth_url("context_clarify", input_id=user_input.id))
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
            return redirect(url_for("index"))

    @app.route("/situation_clarify/<int:input_id>")
    @login_required
    def situation_clarify(input_id):
        user_input = get_input_or_403(input_id)
        situation_check = SituationCheck.query.filter_by(input_id=input_id).order_by(SituationCheck.created_at.desc()).first()
        if not situation_check or situation_check.ok:
            return redirect(auth_url("context_clarify", input_id=input_id))
        fields = extract_user_input_fields(user_input.input_text)
        missing = json.loads(situation_check.missing or "[]")
        clarify_state = load_situation_clarify_state(situation_check)
        return render_template(
            "situation_clarify.html",
            situation_check=situation_check,
            situation_text=fields["situation_description"],
            missing=missing,
            slot_questions=clarify_state.get("slot_questions") or {},
            focus_question=clarify_state.get("focus_question"),
            clarify_mode=clarify_state.get("mode"),
            slot_labels=SITUATION_SLOT_LABELS,
            situation_soft_length=SITUATION_SOFT_LENGTH,
            situation_hard_length=SITUATION_HARD_LENGTH,
        )

    @app.route("/situation_clarify/<int:input_id>", methods=["POST"])
    @login_required
    def situation_clarify_submit(input_id):
        user_input = get_input_or_403(input_id)
        fields = extract_user_input_fields(user_input.input_text)
        latest_check = SituationCheck.query.filter_by(input_id=input_id).order_by(SituationCheck.created_at.desc()).first()
        clarify_state = load_situation_clarify_state(latest_check)
        clarify_mode = clarify_state.get("mode")
        slot_questions = clarify_state.get("slot_questions") or {}
        focus_question = clarify_state.get("focus_question")

        if clarify_mode == "focus" and focus_question:
            selected = request.form.get("focus_problem", "").strip()
            if not selected:
                flash("Выберите главную проблему, которую решаем в первую очередь.", "warning")
                return redirect(auth_url("situation_clarify", input_id=input_id))
            if selected == "Другое (уточню сам)":
                selected = request.form.get("focus_problem_custom", "").strip()
                if not selected:
                    flash("Уточните главную проблему или выберите вариант из списка.", "warning")
                    return redirect(auth_url("situation_clarify", input_id=input_id))
            extracted_slots = load_situation_slots_filled(latest_check)
            situation_description = build_situation_from_focus(
                fields["situation_description"],
                selected,
                extracted_slots,
            )
        elif slot_questions:
            slot_answers = {}
            for key in slot_questions:
                selected = request.form.get(f"slot_{key}", "").strip()
                if not selected:
                    flash(
                        f"Выберите вариант: {SITUATION_SLOT_LABELS.get(key, key)}.",
                        "warning",
                    )
                    return redirect(auth_url("situation_clarify", input_id=input_id))
                if selected == "Другое (уточню сам)":
                    custom = request.form.get(f"slot_{key}_custom", "").strip()
                    if not custom:
                        flash("Уточните свой вариант или выберите другой пункт из списка.", "warning")
                        return redirect(auth_url("situation_clarify", input_id=input_id))
                    slot_answers[f"{key}_custom"] = custom
                slot_answers[key] = selected
            extracted_slots = load_situation_slots_filled(latest_check)
            situation_description = build_situation_from_slots(
                fields["situation_description"],
                slot_answers,
                extracted_slots,
            )
        else:
            situation_description = request.form.get("situation_description", "").strip()
            if not situation_description:
                flash("Опишите ситуацию компании, чтобы продолжить.", "warning")
                return redirect(auth_url("situation_clarify", input_id=input_id))
        try:
            # --- Methodology Check: фильтр мусорных вводов ---
            methodology = call_methodology_check(situation_description)
            if methodology["violation"]:
                situation_result = {
                    "ok": False,
                    "score": 0,
                    "reason": methodology["reason"],
                    "rewrite_hint": methodology["rewrite_hint"],
                    "example": methodology["example"],
                    "missing": [],
                    "normalized_text": situation_description,
                    "clarify_payload": {},
                }
                situation_check = save_situation_check(input_id, situation_description, situation_result)
                apply_situation_to_input(user_input, situation_description, situation_result)
                db.session.commit()
                return redirect(auth_url("situation_clarify", input_id=input_id))

            situation_result = call_situation_check(situation_description)
            situation_check = save_situation_check(input_id, situation_description, situation_result)
            apply_situation_to_input(user_input, situation_description, situation_result)
            db.session.commit()
            if not situation_check.ok:
                return redirect(auth_url("situation_clarify", input_id=input_id))
            return redirect(auth_url("context_clarify", input_id=input_id))
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
            return redirect(auth_url("situation_clarify", input_id=input_id))

    @app.route("/context_clarify/<int:input_id>")
    @login_required
    def context_clarify(input_id):
        user_input = get_input_or_403(input_id)
        fields = extract_user_input_fields(user_input.input_text)
        latest_situation = SituationCheck.query.filter_by(input_id=input_id).order_by(SituationCheck.created_at.desc()).first()
        if latest_situation and not latest_situation.ok:
            return redirect(auth_url("situation_clarify", input_id=input_id))
        industry_check = IndustryCheck.query.filter_by(input_id=input_id, status="needs_answer").order_by(IndustryCheck.created_at.desc()).first()
        if fields["company_size"] and not industry_check:
            return redirect(auth_url("process_after_clarify", input_id=input_id))
        return render_template(
            "context_clarify.html",
            fields=fields,
            industry_check=industry_check,
        )

    @app.route("/context_clarify/<int:input_id>", methods=["POST"])
    @login_required
    def context_clarify_submit(input_id):
        user_input = get_input_or_403(input_id)
        fields = extract_user_input_fields(user_input.input_text)
        company_size = request.form.get("company_size", "").strip()
        industry_answer = request.form.get("industry_answer", "").strip()
        industry_mode = request.form.get("industry_mode", "specify")
        if not company_size:
            flash("Выберите сегмент клиента, чтобы продолжить.", "warning")
            return redirect(auth_url("context_clarify", input_id=input_id))
        pending_industry = IndustryCheck.query.filter_by(input_id=input_id, status="needs_answer").order_by(IndustryCheck.created_at.desc()).first()
        if pending_industry:
            if industry_mode == "skip":
                pending_industry.answer = None
                pending_industry.status = "skipped"
                pending_industry.industry_context_sufficient = False
            else:
                if not industry_answer:
                    flash("Уточните отраслевую специфику, чтобы продолжить.", "warning")
                    return redirect(auth_url("context_clarify", input_id=input_id))
                pending_industry.answer = industry_answer
                pending_industry.status = "done"
                pending_industry.industry_context_sufficient = True
            company_industry = industry_answer if industry_mode != "skip" else ""
            user_input.input_text = build_input_text(
                company_size=company_size,
                company_industry=company_industry,
                situation_description=fields["situation_description"],
                product_name=fields["product_name"],
                signals=fields["signals"],
            )
            try:
                if create_clarification_if_needed(input_id):
                    db.session.commit()
                    return redirect(auth_url("clarify", input_id=input_id))
                db.session.commit()
                return redirect(auth_url("process_after_clarify", input_id=input_id))
            except Exception as e:
                db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
                return redirect(auth_url("context_clarify", input_id=input_id))

        user_input.input_text = build_input_text(
            company_size=company_size,
            company_industry=fields["company_industry"],
            situation_description=fields["situation_description"],
            product_name=fields["product_name"],
            signals=fields["signals"],
        )
        db.session.commit()
        try:
            return run_context_checks_after_situation(input_id)
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
            return redirect(auth_url("context_clarify", input_id=input_id))

    @app.route("/industry_clarify/<int:input_id>")
    @login_required
    def industry_clarify(input_id):
        get_input_or_403(input_id)
        industry_check = IndustryCheck.query.filter_by(input_id=input_id).order_by(IndustryCheck.created_at.desc()).first()
        if not industry_check or industry_check.status != "needs_answer":
            return redirect(auth_url("process_after_clarify", input_id=input_id))
        return render_template("industry_clarify.html", industry_check=industry_check)

    @app.route("/industry_clarify/<int:input_id>", methods=["POST"])
    @login_required
    def industry_clarify_submit(input_id):
        get_input_or_403(input_id)
        industry_check = IndustryCheck.query.filter_by(input_id=input_id).order_by(IndustryCheck.created_at.desc()).first_or_404()
        answer = request.form.get("industry_answer", "").strip()
        industry_mode = request.form.get("industry_mode", "specify")
        if industry_mode == "skip":
            industry_check.answer = None
            industry_check.status = "skipped"
            industry_check.industry_context_sufficient = False
        else:
            if not answer:
                flash("Уточните отраслевую специфику, чтобы продолжить.", "warning")
                return redirect(auth_url("industry_clarify", input_id=input_id))
            industry_check.answer = answer
            industry_check.status = "done"
            industry_check.industry_context_sufficient = True
        db.session.commit()
        try:
            if create_clarification_if_needed(input_id):
                db.session.commit()
                return redirect(auth_url("clarify", input_id=input_id))
            db.session.commit()
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
            return redirect(auth_url("industry_clarify", input_id=input_id))
        return redirect(auth_url("process_after_clarify", input_id=input_id))

    @app.route("/clarify/<int:input_id>")
    @login_required
    def clarify(input_id):
        get_input_or_403(input_id)
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        if not clarification: return redirect(url_for("process_after_clarify", input_id=input_id))
        questions = json.loads(clarification.questions)
        return render_template("clarify.html", questions=questions)

    @app.route("/clarify/<int:input_id>", methods=["POST"])
    @login_required
    def clarify_submit(input_id):
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        answers = {}
        for q in json.loads(clarification.questions):
            key = q["key"]
            selected_value = request.form.get(key)
            custom_value = request.form.get(f"{key}_custom")
            answers[key] = custom_value if selected_value == "custom" else selected_value
        if "skip" in request.form:
            clarification.status = "skipped"; clarification.answers = None
        else:
            clarification.status = "done"; clarification.answers = json.dumps(answers, ensure_ascii=False)
        db.session.commit()
        return redirect(url_for("process_after_clarify", input_id=input_id))

    @app.route("/process_after_clarify/<int:input_id>")
    @login_required
    def process_after_clarify(input_id):
        user_input = get_input_or_403(input_id)
        latest_situation = SituationCheck.query.filter_by(input_id=input_id).order_by(SituationCheck.created_at.desc()).first()
        if latest_situation and not latest_situation.ok:
            return redirect(auth_url("situation_clarify", input_id=input_id))
        pending_industry = IndustryCheck.query.filter_by(input_id=input_id, status="needs_answer").first()
        if pending_industry:
            return redirect(auth_url("industry_clarify", input_id=input_id))
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        final_input = build_final_input(user_input, clarification)
        message = f"{final_input}\n\nВАЖНО: сформируй ровно ОДНУ наилучшую стратегию для данной ситуации. Формат ответа JSON с одним вариантом."
        try:
            items = call_openai(PROMPT_A, message)
            final_items = call_openai_check_str(items)
            for item in final_items:
                db.session.add(Agent1Response(input_id=input_id, round_number=1, status="pending",
                                              **{k: v for k, v in item.items() if k != "product"}))
            db.session.commit()
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        return redirect(url_for("review", input_id=input_id))

    @app.route("/review/<int:input_id>")
    @login_required
    def review(input_id):
        user_input = get_input_or_403(input_id)
        responses = Agent1Response.query.filter_by(input_id=input_id).order_by(Agent1Response.round_number.asc(), Agent1Response.item_number.asc()).all()
        # Latest non-rejected strategy is the current one
        current_strategy = next((r for r in reversed(responses) if r.status != "rejected"), None)
        # All rejected strategies, oldest first
        rejected_strategies = [r for r in responses if r.status == "rejected"]
        accepted = any(item.status == "accepted" for item in responses)
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        parsed_clarification = None
        if clarification and clarification.questions and clarification.answers:
            try:
                questions = json.loads(clarification.questions)
                answers = json.loads(clarification.answers)
                text_lines = [f"{q.get('question')}: {answers.get(q.get('key')) or 'не указано'}" for q in questions]
                parsed_clarification = "\n".join(text_lines)
            except Exception: pass
        industry_check = IndustryCheck.query.filter_by(input_id=input_id).order_by(IndustryCheck.created_at.desc()).first()
        parsed_industry_check = None
        if industry_check:
            industry_lines = []
            if industry_check.extracted_industry_context:
                industry_lines.append(f"Из описания: {industry_check.extracted_industry_context}")
            if industry_check.answer:
                industry_lines.append(f"Ответ пользователя: {industry_check.answer}")
            if industry_check.reason:
                industry_lines.append(f"Причина проверки: {industry_check.reason}")
            parsed_industry_check = "\n".join(industry_lines) if industry_lines else None
        return render_template("review.html", user_input=user_input,
                               current_strategy=current_strategy,
                               rejected_strategies=rejected_strategies,
                               accepted=accepted, clarification=clarification,
                               parsed_clarification=parsed_clarification,
                               parsed_industry_check=parsed_industry_check)

    @app.route("/more/<int:input_id>", methods=["POST"])
    @login_required
    def more(input_id):
        get_input_or_403(input_id)
        try: create_more_agent1_responses(input_id); db.session.commit()
        except Exception as e: db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        return redirect(auth_url("review", input_id=input_id))

    @app.route("/item1/accept/<int:response_id>", methods=["POST"])
    @login_required
    def item1_accept(response_id):
        response = get_agent1_response_or_403(response_id)
        Agent1Response.query.filter(Agent1Response.input_id == response.input_id, Agent1Response.status == "accepted", Agent1Response.id != response_id).update({"status": "pending"})
        response.status = "accepted"; db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item1/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item1_reject(response_id):
        response = get_agent1_response_or_403(response_id)
        input_id = response.input_id
        data = request.get_json(silent=True) or {}
        rejection_reason = (data.get("reason") or "").strip() or None
        # Check for another pending strategy before marking this one rejected
        existing_pending = Agent1Response.query.filter(
            Agent1Response.input_id == input_id,
            Agent1Response.status == "pending",
            Agent1Response.id != response_id
        ).first()
        try:
            # Mark rejected and (if needed) generate a new strategy in one transaction
            response.status = "rejected"
            response.rejection_reason = rejection_reason
            if existing_pending is None:
                create_more_agent1_responses(input_id)
            db.session.commit()
        except Exception as e:
            db.session.rollback(); print("AI ERROR", repr(e), flush=True); flash_ai_error()
            return jsonify(ok=False, reload=False), 500
        return jsonify(ok=True, reload=True)

    @app.route("/item1/save/<int:response_id>", methods=["POST"])
    @login_required
    def item1_save(response_id):
        response = get_agent1_response_or_403(response_id)
        try: fields = validate_payload(request.get_json(force=True) or {})
        except ValueError: return jsonify({"ok": False}), 400
        edit = response.edit
        if edit:
            edit.edited_at = datetime.utcnow(); edit.edited_title = fields["title"]; edit.edited_description = fields["description"]; edit.edited_logic = fields["logic"]; edit.edited_criteria = fields["criteria"]
        else:
            edit = Agent1Edit(agent1_response_id=response.id, original_title=response.title, original_description=response.description, original_logic=response.logic, original_criteria=response.criteria, edited_title=fields["title"], edited_description=fields["description"], edited_logic=fields["logic"], edited_criteria=fields["criteria"])
            db.session.add(edit)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item1/custom/<int:input_id>", methods=["POST"])
    @login_required
    def item1_custom(input_id):
        get_input_or_403(input_id)
        try: payload = request.get_json(silent=True) or request.form; fields = validate_payload(payload)
        except ValueError:
            if request.is_json: return jsonify({"ok": False}), 400
            flash("Заполните все поля.", "warning"); return redirect(auth_url("review", input_id=input_id))
        ok, reason = validate_custom_item(fields, "agent1")
        if not ok:
            if request.is_json: return jsonify({"ok": False, "reason": reason}), 422
            flash(f"Вариант не прошёл проверку: {reason}", "warning"); return redirect(auth_url("review", input_id=input_id))
        db.session.add(Agent1Response(input_id=input_id, round_number=max((item.round_number for item in Agent1Response.query.filter_by(input_id=input_id).all()), default=1), item_number=next_agent1_item_number(input_id), title=fields["title"], description=fields["description"], logic=fields["logic"], criteria=fields["criteria"], status="accepted", implemented=False))
        db.session.commit()
        if request.is_json: return jsonify({"ok": True, "reload": True})
        return redirect(auth_url("review", input_id=input_id))

    @app.route("/continue/<int:input_id>", methods=["POST"])
    @login_required
    def continue_agent1(input_id):
        get_input_or_403(input_id)
        response = Agent1Response.query.filter_by(input_id=input_id, status="accepted").first()
        if not response: flash("Сначала выберите один вариант.", "warning"); return redirect(auth_url("review", input_id=input_id))
        payload = final_agent1_payload(response)
        existing = Agent1Selected.query.filter_by(input_id=input_id).first()
        if existing:
            existing.agent1_response_id = response.id; existing.final_title = payload["title"]; existing.final_description = payload["description"]; existing.final_logic = payload["logic"]; existing.final_criteria = payload["criteria"]; existing.was_edited = payload["was_edited"]
            selected = existing
            Agent2Response.query.filter_by(selected_id=existing.id).delete()
            Agent2Final.query.filter_by(selected_id=existing.id).delete()
        else:
            selected = Agent1Selected(input_id=input_id, agent1_response_id=response.id, final_title=payload["title"], final_description=payload["description"], final_logic=payload["logic"], final_criteria=payload["criteria"], was_edited=payload["was_edited"])
            db.session.add(selected)
        db.session.commit()
        return redirect(auth_url("agent2", selected_id=selected.id))

    @app.route("/agent2/<int:selected_id>")
    @login_required
    def agent2(selected_id):
        selected = get_selected_or_403(selected_id)
        responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
        if not responses:
            message = (
                f"Стратегия: {selected.final_title}\n"
                f"Описание: {selected.final_description}\n"
                f"Логика: {selected.final_logic}\n"
                f"Критерии: {selected.final_criteria}\n\n"
                f"ВАЖНО: сгенерируй ровно 3 шага без продуктов банка. Формат ответа JSON."
            )
            try:
                final_items = generate_agent2_items(message, 3)
                for item in final_items:
                    db.session.add(Agent2Response(selected_id=selected_id, status="pending",
                                                  **{k: v for k, v in item.items() if not k.startswith("_")}))
                db.session.commit()
                responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
            except Exception as e:
                db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        accepted = any(item.status == "accepted" for item in responses)
        products = load_product_catalog()
        return render_template("agent2.html", selected=selected, responses=responses, accepted=accepted, products=products)

    @app.route("/agent2/more/<int:selected_id>", methods=["POST"])
    @login_required
    def more_agent2(selected_id):
        get_selected_or_403(selected_id)
        try:
            create_more_agent2_responses(selected_id)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("AI ERROR:", repr(e), flush=True)
            flash_ai_error()
        return redirect(auth_url("agent2", selected_id=selected_id))

    @app.route("/item2/accept/<int:response_id>", methods=["POST"])
    @login_required
    def item2_accept(response_id):
        response = get_agent2_response_or_403(response_id)
        response.status = "accepted"; db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item2_reject(response_id):
        response = get_agent2_response_or_403(response_id)
        data = request.get_json(silent=True) or {}
        rejection_reason = (data.get("reason") or "").strip() or None
        response.status = "rejected"
        response.rejection_reason = rejection_reason
        db.session.commit()
        return jsonify({"ok": True, "reload": False})

    @app.route("/item2/save/<int:response_id>", methods=["POST"])
    @login_required
    def item2_save(response_id):
        response = get_agent2_response_or_403(response_id)
        try: fields = validate_payload(request.get_json(force=True) or {})
        except ValueError: return jsonify({"ok": False}), 400
        response.title = fields["title"]; response.description = fields["description"]; response.logic = fields["logic"]; response.criteria = fields["criteria"]; response.was_edited = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/custom/<int:selected_id>", methods=["POST"])
    @login_required
    def item2_custom(selected_id):
        get_selected_or_403(selected_id)
        try: payload = request.get_json(silent=True) or request.form; fields = validate_payload(payload)
        except ValueError:
            if request.is_json: return jsonify({"ok": False}), 400
            flash("Заполните все поля.", "warning"); return redirect(auth_url("agent2", selected_id=selected_id))
        ok, reason = validate_custom_item(fields, "agent2")
        if not ok:
            if request.is_json: return jsonify({"ok": False, "reason": reason}), 422
            flash(f"Шаг не прошёл проверку: {reason}", "warning"); return redirect(auth_url("agent2", selected_id=selected_id))
        db.session.add(Agent2Response(selected_id=selected_id, item_number=next_agent2_item_number(selected_id), title=fields["title"], description=fields["description"], logic=fields["logic"], criteria=fields["criteria"], status="accepted", was_edited=True, implemented=False))
        db.session.commit()
        if request.is_json: return jsonify({"ok": True, "reload": True})
        return redirect(auth_url("agent2", selected_id=selected_id))

    @app.route("/item2/set_product/<int:response_id>", methods=["POST"])
    @login_required
    def item2_set_product(response_id):
        response = get_agent2_response_or_403(response_id)
        data = request.get_json(silent=True) or {}
        product_name = (data.get("product") or "").strip()
        response.product = product_name or None
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/agent2/generate_product_step/<int:selected_id>", methods=["POST"])
    @login_required
    def generate_product_step(selected_id):
        selected = get_selected_or_403(selected_id)
        product_name = request.form.get("product_name", "").strip()
        if not product_name:
            flash("Выберите сервис банка.", "warning")
            return redirect(auth_url("agent2", selected_id=selected_id))
        products = load_product_catalog()
        product = next((p for p in products if p["name"] == product_name), None)
        product_desc = (product["description"] if product else "").strip()
        message = (
            f"Стратегия: {selected.final_title}\n"
            f"Описание стратегии: {selected.final_description}\n"
            f"Логика: {selected.final_logic}\n\n"
            f"Сервис банка: {product_name}\n"
            f"Описание сервиса: {product_desc}\n"
        )
        try:
            items = call_openai(PROMPT_PRODUCT_STEP, message, json_instructions=JSON_INSTRUCTIONS_SINGLE_STEP)
            items = mark_implemented_steps_local(items)
            for item in items[:1]:
                item["product"] = product_name
                new_fields = {k: v for k, v in item.items() if not k.startswith("_")}
                new_fields["item_number"] = next_agent2_item_number(selected_id)
                db.session.add(Agent2Response(selected_id=selected_id, status="pending", **new_fields))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("AI ERROR:", repr(e), flush=True)
            flash_ai_error()
        return redirect(auth_url("agent2", selected_id=selected_id))

    @app.route("/agent2/finish/<int:selected_id>", methods=["POST"])
    @login_required
    def agent2_finish(selected_id):
        get_selected_or_403(selected_id)
        responses = Agent2Response.query.filter_by(selected_id=selected_id, status="accepted").order_by(Agent2Response.item_number.asc()).all()
        if not responses: flash("Сначала выберите хотя бы один вариант Агента 2.", "warning"); return redirect(auth_url("agent2", selected_id=selected_id))
        payload = combine_payloads([final_agent2_payload(response) for response in responses], "Финальный выбор")
        first_response = responses[0]
        final = Agent2Final.query.filter_by(selected_id=selected_id).first()
        if final:
            final.agent2_response_id = first_response.id; final.saved_at = datetime.utcnow(); final.final_title = payload["title"]; final.final_description = payload["description"]; final.final_logic = payload["logic"]; final.final_criteria = payload["criteria"]; final.was_edited = payload["was_edited"]; final.pdf_locked = False
        else:
            final = Agent2Final(selected_id=selected_id, agent2_response_id=first_response.id, final_title=payload["title"], final_description=payload["description"], final_logic=payload["logic"], final_criteria=payload["criteria"], was_edited=payload["was_edited"], pdf_locked=False)
            db.session.add(final)
        db.session.commit()
        return redirect(auth_url("result", selected_id=selected_id))

    @app.route("/result/<int:selected_id>")
    @login_required
    def result(selected_id):
        selected = get_selected_or_403(selected_id)
        final = Agent2Final.query.filter_by(selected_id=selected_id).first_or_404()
        steps = Agent2Response.query.filter_by(selected_id=selected_id, status="accepted").order_by(Agent2Response.item_number.asc()).all()
        clarification = Clarification.query.filter_by(input_id=selected.input_id).first()
        return render_template("result.html", selected=selected, final=final, steps=steps, clarification=clarification)

    @app.route("/result_pdf/<int:selected_id>")
    @login_required
    def result_pdf(selected_id):
        selected = get_selected_or_403(selected_id)
        final = Agent2Final.query.filter_by(selected_id=selected_id).first_or_404()
        steps = Agent2Response.query.filter_by(selected_id=selected_id, status="accepted").order_by(Agent2Response.item_number.asc()).all()
        clarification = Clarification.query.filter_by(input_id=selected.input_id).first()
        # Фиксируем результат — после этого навигация назад недоступна
        if not final.pdf_locked:
            final.pdf_locked = True
            db.session.commit()
        buffer = build_results_pdf(selected, final, clarification, steps)
        return Response(buffer.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename=result_{selected_id}.pdf"})


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)