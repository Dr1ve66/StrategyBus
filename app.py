# -*- coding: utf-8 -*-
import json
import re
import os
import uuid
from io import BytesIO
from datetime import datetime
from functools import wraps
from types import SimpleNamespace
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, session, url_for
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

# === PROMPTS ===
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
К выбранной стратегии подбери ровно 5 шагов.
Шаги должны быть выстроены в логическом и временном порядке.
Шаг — это простое действие, которое можно выполнить на практике и которое не требует дальнейшего разбиения для исполнителя.
Название Шага должно начинаться с глагола.
Каждый шаг должен быть:однозначным, реализуемым, ограниченным по сроку, привязанным к цели стратегии.
Пример: изучить информацию о торгах, провести аудит соответствия компании, сделать ремонт помещения, проверить документы УКЭП, проанализируй бизнес, подобрать тендер.
Избегай общих и абстрактных формулировок, не используй слова без конкретизации: «улучшить», «оптимизировать», «усилить», «развить», «проработать».
Каждый шаг должен быть реализуем в срок до 1 месяца.
Принадлежность Шага к Стратегии очевидна.
Критерии — это критерии кому данный Шаг не подходит.
Критерии должны:
относиться или к открытым данным, т.е. их можно найти в интернете (например:отраль, оквэд, регион работы,наличие товарного знака, лицензии) и/или к данным которые могут есть в банке (например:количество покупателей и поставщиков,
количество сотрудников, размер выручки, срок деятельности, назначения платежей в транзакциях). Не предлагай Критерии которые можно узнать только работая в самой компании (например: есть ли в штате должность юриста,
у компании есть pipeline по продажам и подобное)
иметь числовое значение или это значение можно получить с помощью вычислений. То есть не указывай "не подходит по ОКВЭД", пиши - "не подходит ОКВЭД 01, 02"
Не менее 70% предложенных Шагов должны использовать продукты и/или сервисы из списка, где сначала идет название продукта, а потом что делает данные продукт/сервис -
"""
filename = "/app/data/products.txt"
if os.path.exists(filename):
    df = pd.read_csv(filename, sep=';')
    if 'Название продукта' in df.columns and 'Что делает продукт/сервис' in df.columns:
        new_df = df['Название продукта'] + ' - ' + df['Что делает продукт/сервис']
        new_df = new_df.values.tolist()
        for el in new_df:
            PROMPT_B_DESC += el + "\n"

PROMPT_B = os.environ.get("PROMPT_B", PROMPT_B_DESC)

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
 "options": ["<1 года", "1–3 года", "3–5 лет", "5+ лет"]
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
        user = User.query.get(uid) if uid else None
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
            item = {"title": str(item), "description": "", "logic": "", "criteria": ""}
        implemented = to_bool(item.get("implemented"))
        normalized.append({
            "item_number": int(item.get("id") or index),
            "title": str(item.get("title") or f"Пункт {index}"),
            "description": str(item.get("description") or ""),
            "logic": str(item.get("logic") or ""),
            "criteria": str(item.get("criteria") or ""),
            "implemented": implemented,
        })
    return normalized

def call_openai(system_prompt, user_message):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": user_message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")

def call_openai_raw(system_prompt, user_message):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
    enhanced_prompt = f"{system_prompt.strip()}\nВАЖНО: Ответь строго в формате JSON. Никакого текста вне JSON."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return json.loads(response.choices[0].message.content)

def call_openai_check_str(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
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
        model="gpt-4o-mini",
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
    client = OpenAI(api_key=api_key, timeout=60)
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
        model="gpt-4o-mini",
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
    base_prompt = PROMPT_A if prompt_type == "agent1" else PROMPT_B
    system_prompt = f"Ты — строгий валидатор. Тебе дан промпт с правилами для генерации вариантов:\n{base_prompt}\nПользователь добавил свой вариант вручную. Проверь, соответствует ли он правилам промпта выше.\nВерни JSON строго в формате:\n{{\"ok\": true}} — если вариант соответствует правилам,\n{{\"ok\": false, \"reason\": \"краткое объяснение на русском, почему не соответствует\"}} — если не соответствует.\nПроверяй только смысловое соответствие правилам, не придирайся к формулировкам."
    item_text = json.dumps(fields, ensure_ascii=False)
    try:
        client = OpenAI(api_key=api_key, timeout=30)
        response = client.chat.completions.create(
            model="gpt-4o-mini", response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": item_text}],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        if data.get("ok") is False: return False, data.get("reason", "Вариант не соответствует правилам.")
        return True, ""
    except Exception as e:
        print("VALIDATE ERROR:", repr(e), flush=True)
        return True, ""

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
    for item in final_items:
        db.session.add(Agent1Response(input_id=input_id, round_number=next_round, status="pending", **item))

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

    items = call_openai(PROMPT_B, message)
    final_items = call_openai_check_stp(items)
    start_number = max([r.item_number for r in previous_responses], default=0)
    for index, item in enumerate(final_items, start=1):
        item["item_number"] = start_number + index
        db.session.add(Agent2Response(selected_id=selected_id, status="pending", **item))

def next_agent1_item_number(input_id):
    return max([item.item_number for item in Agent1Response.query.filter_by(input_id=input_id).all()], default=0) + 1

def next_agent2_item_number(selected_id):
    return max([item.item_number for item in Agent2Response.query.filter_by(selected_id=selected_id).all()], default=0) + 1

def build_final_input(user_input, clarification):
    base = user_input.input_text
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
        if User.query.get(user_id):
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
            if user:
                if user.password_hash:
                    authenticated = user.check_password(password)
                else:
                    # Обратная совместимость: старый admin без хэша
                    env_password = os.environ.get("APP_PASSWORD")
                    if env_password and password == env_password and user.username == "admin":
                        authenticated = True
                        # Сразу хэшируем пароль для будущих входов
                        user.set_password(password)
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
    def index(): return render_template("index.html")

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
        company_size = request.form.get("company_size", "").strip()
        company_industry = request.form.get("company_industry", "").strip()
        product_name = request.form.get("product_name", "").strip()
        situation_description = request.form.get("situation_description", "").strip()
        required_fields = [company_size, company_industry, situation_description]
        if not all(required_fields):
            flash("Заполните все 3 обязательных поля", "warning")
            return redirect(url_for("index"))
        input_text = f"Размер компании: {company_size}\nОтрасль компании: {company_industry}\nОписание ситуации у компании: {situation_description}".strip()
        if product_name:
            input_text += f"\nПродукт: {product_name}\nДополнительное условие: если указанный продукт релевантен ситуации клиента, обязательно явно используй именно этот продукт в предлагаемых стратегиях. Не игнорируй продукт и не заменяй его абстрактными формулировками.".strip()
        user_input = UserInput(user_id=current_user_id(), input_text=input_text, session_token=str(uuid.uuid4()))
        db.session.add(user_input); db.session.commit()
        try:
            clarify = call_openai_raw(PROMPT_CLARIFY, input_text)
            if clarify.get("status") == "need_clarification":
                db.session.add(Clarification(input_id=user_input.id, questions=json.dumps(clarify["questions"])))
                db.session.commit()
                return redirect(url_for("clarify", input_id=user_input.id))
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
            return redirect(url_for("index"))
        return redirect(url_for("process_after_clarify", input_id=user_input.id))

    @app.route("/clarify/<int:input_id>")
    def clarify(input_id):
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
        user_input = UserInput.query.get_or_404(input_id)
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        final_input = build_final_input(user_input, clarification)
        message = f"{final_input}\n\nВАЖНО: сформируй ровно ОДНУ наилучшую стратегию для данной ситуации. Формат ответа JSON с одним вариантом."
        try:
            items = call_openai(PROMPT_A, message)
            final_items = call_openai_check_str(items)
            for item in final_items:
                db.session.add(Agent1Response(input_id=input_id, round_number=1, status="pending", **item))
            db.session.commit()
        except Exception as e:
            db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        return redirect(url_for("review", input_id=input_id))

    @app.route("/review/<int:input_id>")
    @login_required
    def review(input_id):
        user_input = UserInput.query.get_or_404(input_id)
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
        return render_template("review.html", user_input=user_input,
                               current_strategy=current_strategy,
                               rejected_strategies=rejected_strategies,
                               accepted=accepted, clarification=clarification,
                               parsed_clarification=parsed_clarification)

    @app.route("/more/<int:input_id>", methods=["POST"])
    @login_required
    def more(input_id):
        try: create_more_agent1_responses(input_id); db.session.commit()
        except Exception as e: db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        return redirect(auth_url("review", input_id=input_id))

    @app.route("/item1/accept/<int:response_id>", methods=["POST"])
    @login_required
    def item1_accept(response_id):
        response = Agent1Response.query.get_or_404(response_id)
        Agent1Response.query.filter(Agent1Response.input_id == response.input_id, Agent1Response.status == "accepted", Agent1Response.id != response_id).update({"status": "pending"})
        response.status = "accepted"; db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item1/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item1_reject(response_id):
        response = Agent1Response.query.get_or_404(response_id)
        input_id = response.input_id
        data = request.get_json(silent=True) or {}
        rejection_reason = (data.get("reason") or "").strip() or None
        response.status = "rejected"
        response.rejection_reason = rejection_reason
        db.session.commit()
        try:
            create_more_agent1_responses(input_id)
            db.session.commit()
        except Exception as e:
            db.session.rollback(); print("AI ERROR", repr(e), flush=True); flash_ai_error()
            return jsonify(ok=False, reload=False), 500
        return jsonify(ok=True, reload=True)

    @app.route("/item1/save/<int:response_id>", methods=["POST"])
    @login_required
    def item1_save(response_id):
        response = Agent1Response.query.get_or_404(response_id)
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
        UserInput.query.get_or_404(input_id)
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
        selected = Agent1Selected.query.get_or_404(selected_id)
        responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
        if not responses:
            message = (
                f"Стратегия: {selected.final_title}\n"
                f"Описание: {selected.final_description}\n"
                f"Логика: {selected.final_logic}\n"
                f"Критерии: {selected.final_criteria}\n\n"
                f"ВАЖНО: сгенерируй ровно 5 шагов. Формат ответа JSON."
            )
            try:
                items = call_openai(PROMPT_B, message)
                final_items = call_openai_check_stp(items)
                for item in final_items: db.session.add(Agent2Response(selected_id=selected_id, status="pending", **item))
                db.session.commit()
                responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
            except Exception as e: db.session.rollback(); print("AI ERROR:", repr(e), flush=True); flash_ai_error()
        accepted = any(item.status == "accepted" for item in responses)
        return render_template("agent2.html", selected=selected, responses=responses, accepted=accepted)

    @app.route("/agent2/more/<int:selected_id>", methods=["POST"])
    @login_required
    def more_agent2(selected_id):
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
        response = Agent2Response.query.get_or_404(response_id)
        response.status = "accepted"; db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item2_reject(response_id):
        response = Agent2Response.query.get_or_404(response_id)
        data = request.get_json(silent=True) or {}
        rejection_reason = (data.get("reason") or "").strip() or None
        response.status = "rejected"
        response.rejection_reason = rejection_reason
        db.session.commit()
        return jsonify({"ok": True, "reload": False})

    @app.route("/item2/save/<int:response_id>", methods=["POST"])
    @login_required
    def item2_save(response_id):
        response = Agent2Response.query.get_or_404(response_id)
        try: fields = validate_payload(request.get_json(force=True) or {})
        except ValueError: return jsonify({"ok": False}), 400
        response.title = fields["title"]; response.description = fields["description"]; response.logic = fields["logic"]; response.criteria = fields["criteria"]; response.was_edited = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/custom/<int:selected_id>", methods=["POST"])
    @login_required
    def item2_custom(selected_id):
        Agent1Selected.query.get_or_404(selected_id)
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

    @app.route("/agent2/finish/<int:selected_id>", methods=["POST"])
    @login_required
    def agent2_finish(selected_id):
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
        selected = Agent1Selected.query.get_or_404(selected_id)
        final = Agent2Final.query.filter_by(selected_id=selected_id).first_or_404()
        steps = Agent2Response.query.filter_by(selected_id=selected_id, status="accepted").order_by(Agent2Response.item_number.asc()).all()
        clarification = Clarification.query.filter_by(input_id=selected.input_id).first()
        return render_template("result.html", selected=selected, final=final, steps=steps, clarification=clarification)

    @app.route("/result_pdf/<int:selected_id>")
    @login_required
    def result_pdf(selected_id):
        selected = Agent1Selected.query.get_or_404(selected_id)
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