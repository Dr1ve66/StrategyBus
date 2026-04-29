# -*- coding: utf-8 -*-
import json
import os
import uuid
from fileinput import filename
from io import BytesIO
from collections import defaultdict
from datetime import datetime
from functools import wraps
import pandas as pd

from dotenv import load_dotenv
from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, URLSafeSerializer
from openai import OpenAI
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
import textwrap

load_dotenv()

db = SQLAlchemy()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET")
    os.makedirs(os.path.join(app.root_path, "data"), exist_ok=True)
    default_db_path = os.path.join(app.root_path, "data", "app.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "APP_DATABASE_URL", f"sqlite:///{default_db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_PARTITIONED"] = True

    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = "missing-secret-key-change-me"

    db.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    register_template_helpers(app)
    return app


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, default="admin")
    ip_address = db.Column(db.String(64), nullable=False)
    first_login_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    login_count = db.Column(db.Integer, nullable=False, default=1)


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

    user_input = db.relationship("UserInput", backref="agent1_responses")


class Agent1Edit(db.Model):
    __tablename__ = "agent1_edits"

    id = db.Column(db.Integer, primary_key=True)
    agent1_response_id = db.Column(
        db.Integer, db.ForeignKey("agent1_responses.id"), unique=True, nullable=False
    )
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

    selected = db.relationship("Agent1Selected", backref=db.backref("agent2_final", uselist=False))
    response = db.relationship("Agent2Response")

class ProductGuide(db.Model):
    __tablename__ = "product_guides"

    id = db.Column(db.Integer, primary_key=True)  # №
    name = db.Column(db.String(255), nullable=False)  # Название продукта
    description = db.Column(db.Text, nullable=False)  # Что делает продукт/сервис
    problems = db.Column(db.Text, nullable=False)  # Какие проблемы помогает решить


class Clarification(db.Model):
    __tablename__ = "clarifications"

    id = db.Column(db.Integer, primary_key=True)
    input_id = db.Column(db.Integer, db.ForeignKey("user_inputs.id"))
    questions = db.Column(db.Text)  # JSON
    answers = db.Column(db.Text)    # JSON
    status = db.Column(db.String(20), default="pending")

PROMPT_A_DESC = """Ты — бизнес-консультант с 20-летним опытом работы с корпоративными клиентами.

Твоя задача: сформировать 3 стратегии для клиента на основе входных данных.

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

Требования к стратегиям

Нужно предложить 3 принципиально разные стратегии.

Каждая стратегия должна относиться к разному типу механики:
Снижение потерь / защита
Восстановление операционной деятельности
Изменение или расширение модели дохода

Запрещено:
делать две стратегии с одинаковой логикой;
использовать один и тот же инструмент одинаковым способом в нескольких стратегиях;
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

1. Цель

Конкретный измеримый результат.

2. Механизм

Пошаговое описание действий (что именно делаем).

3. Инструменты

Какие инструменты используются (включая продукт, если применимо).

4. Бизнес-эффект

Оцифрованный результат (выручка, издержки, маржа и т.д.).

5. Критерии (кому НЕ подходит)

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

названия не содержат продукт;
нет абстрактных формулировок;
стратегии не дублируют друг друга;
механики различаются;
продукт (если задан) используется корректно;
каждая стратегия реализуема и имеет измеримый эффект.

Если есть нарушения — исправь до вывода.
"""

PROMPT_A = os.environ.get(
    "PROMPT_A",
    PROMPT_A_DESC
)

PROMPT_B_DESC  = """Ты — бизнес консультант с 20-летним опытом работы с корпоративными клиентами. 

К выбранной стратегии подбери от 4 до 10 шагов.
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
- относиться или к открытым данным, т.е. их можно найти в интернете (например:отраль, оквэд, регион работы,наличие товарного знака, лицензии) и/или к данным которые могут есть в банке (например:количество покупателей и поставщиков, 
количество сотрудников, размер выручки, срок деятельности, назначения платежей в транзакциях). Не предлагай Критерии которые можно узнать только работая в самой компании (например: есть ли в штате должность юриста, 
у компании есть pipeline по продажам и подобное) 
- иметь числовое значение или это значение можно получить с помощью вычислений. То есть не указывай "не подходит по ОКВЭД", пиши - "не подходит ОКВЭД 01, 02"

Не менее 70% предложенных Шагов должны использовать продукты и/или сервисы из списка, где сначала идет название продукта, а потом что делает данные продукт/сервис -
"""

filename = "/app/data/products.txt"

df = pd.read_csv(filename, sep = ';')
new_df = df['Название продукта'] + ' - ' + df['Что делает продукт/сервис']
new_df = new_df.values.tolist()

for el in new_df:
    PROMPT_B_DESC += el + "\n"


PROMPT_B = os.environ.get(
    "PROMPT_B",
        PROMPT_B_DESC
)

JSON_INSTRUCTIONS = """
ВАЖНО: Отвечай строго в формате JSON — от 1 до 10 вариантов:
{"items": [
  {
    "id": 1,
    "title": "Краткое название варианта",
    "description": "Описание варианта (2-3 предложения)",
    "logic": "Логика и обоснование",
    "criteria": "Критерии оценки / применения",
    "implemented": "Флаг реализации (ОБЯЗАТЕЛЬНО ЗАПОЛНИТЬ значением Реализована или Не реализована)"
  },
  {"id": 2, "title": "", "description": "", "logic": "", "criteria": "", "implemented": ""},
  {"id": 3, "title": "", "description": "", "logic": "", "criteria": "", "implemented": ""}
]}
Никакого текста вне JSON.
"""


PROMPT_CLARIFY = """
Ты — бизнес-аналитик.

Твоя задача:
1. Проверить входные данные клиента
2. Определить, достаточно ли информации для разработки стратегий

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
- максимум 5 вопросов
- максимум 3 варианта ответа на каждый вопрос
- вопросы должны критически влиять на стратегию
- варианты ответа должны быть конкретными
- не задавай очевидные или повторяющиеся вопросы
- key должен быть понятным (snake_case)
- никаких q1, q2, q3
"""



def to_bool(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    value_str = str(value).strip().lower()

    return value_str in {
        "1", "true", "yes", "y",
        "да",
        "реализована", "реализовано", "реализован",
        "выполнено", "сделано", "done"
    }

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


def normalize_items(content):
    # Лог сырого ответа модели
    print("RAW AI CONTENT:", repr(content))

    # content из OpenAI всегда строка (json_object в content)
    text = (content or "").strip()

    # Убираем возможные обёртки ```json ```
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.startswith("json"):
            text = text[4:]
    if text.endswith("```"):
        text = text.rstrip("`")
    text = text.strip()

    # Парсим JSON
    data = json.loads(text)

    # Поддерживаем несколько форматов:
    # 1) {"items": []}
    # 2) {"results": []}
    # 3) список напрямую: []
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("items")
            or data.get("results")
            or data.get("recommendations")
            or data.get("cards")
        )

    # Если items словарь — берём значения
    if isinstance(items, dict):
        items = list(items.values())

    if not isinstance(items, list):
        print("AI returned invalid item structure:", repr(data))
        return []

    # Если список есть, но он пустой — считаем, что AI не нашёл вариантов,
    # и просто возвращаем пустой список, НЕ бросая исключение
    if len(items) == 0:
        return []

    normalized = []
    for index, item in enumerate(items[:10], start=1):
        if not isinstance(item, dict):
            item = {
                "title": str(item),
                "description": "",
                "logic": "",
                "criteria": "",
            }

        implemented = to_bool(item.get("implemented"))

        normalized.append(
            {
                "item_number": int(item.get("id") or index),
                "title": str(item.get("title") or f"Пункт {index}"),
                "description": str(item.get("description") or ""),
                "logic": str(item.get("logic") or ""),
                "criteria": str(item.get("criteria") or ""),
                "implemented": implemented,
            }
        )

    return normalized

def call_openai(system_prompt, user_message):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": user_message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")


def call_openai_raw(system_prompt, user_message):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=api_key, timeout=60)

    # Добавляем инструкцию о JSON в системный промпт
    enhanced_prompt = f"""{system_prompt.strip()}

ВАЖНО: Ответь строго в формате JSON. Никакого текста вне JSON.
"""

    response = client.chat.completions.create(
        model="gpt-5.4-mini",  # ← Исправлено
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    return json.loads(response.choices[0].message.content)


def call_openai_check_str(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
    system_prompt = """Ты на вход получишь одну или несколько стратегий. 
    Сравни стратегии, которые тебе передали, с уже существующими из списка по названию и логике - 
    """
    df = pd.read_excel('strategies.xlsx')

    results = [
        {
            "title": str(row["strateg_nm"]) if pd.notna(row["strateg_nm"]) else "",
            "description": str(row["logic"]) if pd.notna(row["logic"]) else ""
        }
        for _, row in df.iterrows()
    ]


    for el in results:
        system_prompt += "Название стратегии: " + el['title'] + "; Логика: " + el["description"] + "\n"
    system_prompt += """Если стратегия есть в этом списке или что-то похожее, то пометь как Реализована.
    ВАЖНО верни те же самые данные, которые ты получил с изменением только одного поля implemented"""
    message = json.dumps(item, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")


def call_openai_check_stp(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key, timeout=60)
    system_prompt = """Ты на вход получишь один или несколько шагов
    Сравни стратегию или шаг, которую тебе передали, с уже существующими из списка по названию - 
    """

    df = pd.read_excel('steps.xlsx')

    results = [{"title": row if pd.notna(row) else ""} for row in df["steps"]]

    for el in results:
        system_prompt += el['title'] + "\n"
    system_prompt += """Если стратегия или шаг есть в этом списке или что-то похожее, то пометь как Реализована.
    ВАЖНО верни те же самые данные, которые ты получил с изменением только одного поля implemented"""
    message = json.dumps(item, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"{system_prompt.strip()}\n\n{JSON_INSTRUCTIONS.strip()}"},
            {"role": "user", "content": message},
        ],
    )
    return normalize_items(response.choices[0].message.content or "")


def final_agent1_payload(response):
    if response.edit:
        return {
            "title": response.edit.edited_title,
            "description": response.edit.edited_description,
            "logic": response.edit.edited_logic,
            "criteria": response.edit.edited_criteria,
            "was_edited": True,
        }
    return {
        "title": response.title,
        "description": response.description,
        "logic": response.logic,
        "criteria": response.criteria,
        "was_edited": False,
    }


def combine_payloads(payloads, title_prefix):
    count = len(payloads)
    if count == 1:
        return payloads[0]
    return {
        "title": f"{title_prefix}: {count} Шага(ов)",
        "description": "\n\n".join(
            [f"{index}. {item['title']}\n{item['description']}" for index, item in enumerate(payloads, start=1)]
        ),
        "logic": "\n\n".join(
            [f"{index}. {item['title']}\n{item['logic']}" for index, item in enumerate(payloads, start=1)]
        ),
        "criteria": "\n\n".join(
            [f"{index}. {item['title']}\n{item['criteria']}" for index, item in enumerate(payloads, start=1)]
        ),
        "was_edited": any(item.get("was_edited") for item in payloads),
    }



def final_agent2_payload(response):
    return {
        "title": response.title,
        "description": response.description,
        "logic": response.logic,
        "criteria": response.criteria,
        "was_edited": response.was_edited,
    }


def validate_payload(payload):
    fields = {}
    for key in ["title", "description", "logic", "criteria"]:
        value = (payload.get(key) or "").strip()
        if not value:
            raise ValueError("empty field")
        fields[key] = value
    return fields


def flash_ai_error():
    flash("Ошибка обработки ответа AI. Попробуйте ещё раз.", "danger")

def validate_custom_item(fields, prompt_type):
    """
    Проверяет пользовательский вариант на соответствие правилам промпта.
    prompt_type: 'agent1' или 'agent2'
    Возвращает (True, '') если прошёл, (False, reason) если нет.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return True, ""  # если ключа нет — пропускаем проверку

    base_prompt = PROMPT_A if prompt_type == "agent1" else PROMPT_B

    system_prompt = f"""Ты — строгий валидатор. Тебе дан промпт с правилами для генерации вариантов:

---
{base_prompt}
---

Пользователь добавил свой вариант вручную. Проверь, соответствует ли он правилам промпта выше.
Верни JSON строго в формате:
{{"ok": true}} — если вариант соответствует правилам,
{{"ok": false, "reason": "краткое объяснение на русском, почему не соответствует"}} — если не соответствует.
Проверяй только смысловое соответствие правилам, не придирайся к формулировкам."""

    item_text = json.dumps(fields, ensure_ascii=False)

    try:
        client = OpenAI(api_key=api_key, timeout=30)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": item_text},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        if data.get("ok") is False:
            return False, data.get("reason", "Вариант не соответствует правилам.")
        return True, ""
    except Exception as e:
        print("VALIDATE ERROR:", repr(e), flush=True)
        return True, ""  # при ошибке валидации — пропускаем, не блокируем


def create_more_agent1_responses(input_id):
    user_input = UserInput.query.get_or_404(input_id)
    previous = Agent1Response.query.filter_by(input_id=input_id).all()

    next_round = (max([item.round_number for item in previous], default=0) + 1)
    previous_titles = ", ".join([item.title for item in previous]) or "нет"
    message = f"""Исходный запрос пользователя: {user_input.input_text}

Пользователь уже видел следующие варианты (они его не устроили):
{previous_titles}

Предложи еще один вариант, который отличается от предыдущих.Формат ответа такой же JSON."""

    items = call_openai(PROMPT_A, message)
    final_items = call_openai_check_str(items)
    for item in final_items:
        db.session.add(Agent1Response(input_id=input_id, round_number=next_round, status="pending", **item))


def create_more_agent2_responses(selected_id):
    selected = Agent1Selected.query.get_or_404(selected_id)
    previous = Agent2Response.query.filter_by(selected_id=selected_id).all()
    previous_titles = ", ".join([item.title for item in previous]) or "нет"
    message = f"""Название: {selected.final_title}
Описание: {selected.final_description}
Логика: {selected.final_logic}
Критерии: {selected.final_criteria}

Пользователь уже видел следующие варианты Агента 2:
{previous_titles}

Предложи до 10 новых, принципиально других вариантов. Не повторяй предыдущие.
Формат ответа такой же JSON."""

    items = call_openai(PROMPT_B, message)
    final_items = call_openai_check_stp(items)
    start_number = max([item.item_number for item in previous], default=0)
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

        extra = "\n".join([
            f"{key}: {value if value else 'не указано'}"
            for key, value in answers.items()
        ])

        return base + "\n\nУточнения:\n" + extra

    return base

def build_results_pdf(selected, final):
    buffer = BytesIO()
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
    if "DejaVuSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_font_path))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Итоговый результат",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="RuTitle",
            fontName="DejaVuSans-Bold",
            fontSize=18,
            leading=22,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="RuHeading",
            fontName="DejaVuSans-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="RuBody",
            fontName="DejaVuSans",
            fontSize=10,
            leading=14,
        )
    )

    story = [Paragraph("Итоговый результат", styles["RuTitle"])]

    # 1. Запрос пользователя
    story.append(Paragraph("Запрос пользователя", styles["RuHeading"]))
    user_text = ""
    if selected.user_input and selected.user_input.input_text:
        user_text = selected.user_input.input_text.replace("\n", "<br/>")
    story.append(Paragraph(user_text, styles["RuBody"]))
    story.append(Spacer(1, 8))

    # 2. Итог Агента 1
    story.append(Paragraph("Стратегии (Агент 1, после правок)", styles["RuHeading"]))
    agent1_text_parts = []

    if selected.final_title:
        agent1_text_parts.append(f"<b>Название:</b> {selected.final_title}")

    if selected.final_description:
        final_description = selected.final_description.replace("\n", "<br/>")
        agent1_text_parts.append(f"<b>Описание:</b> {final_description}")

    if selected.final_logic:
        final_logic = selected.final_logic.replace("\n", "<br/>")
        agent1_text_parts.append(f"<b>Логика:</b> {final_logic}")

    if selected.final_criteria:
        final_criteria = selected.final_criteria.replace("\n", "<br/>")
        agent1_text_parts.append(f"<b>Критерии:</b> {final_criteria}")

    agent1_text = "<br/><br/>".join(agent1_text_parts) or ""
    story.append(Paragraph(agent1_text, styles["RuBody"]))
    story.append(Spacer(1, 12))


def add_block(title, item):
    story.append(Paragraph(title, styles["RuHeading"]))
    data = [
        [Paragraph("Название", styles["RuBody"]), Paragraph(item.final_title, styles["RuBody"])],
        [Paragraph("Описание", styles["RuBody"]), Paragraph(item.final_description.replace("\\n", "<br/>"), styles["RuBody"])],
        [Paragraph("Логика", styles["RuBody"]), Paragraph(item.final_logic.replace("\\n", "<br/>"), styles["RuBody"])],
        [Paragraph("Критерии", styles["RuBody"]), Paragraph(item.final_criteria.replace("\\n", "<br/>"), styles["RuBody"])],
    ]

    table = Table(data, colWidths=[32 * mm, 128 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 8))

    # 3. Выбор Агента 1 (структурировано)
    add_block("Выбор Агента 1", selected)
    # 4. Результат Агента 2 (с учетом выбора пользователя)
    add_block("Результат Агента 2", final)
    doc.build(story)
    buffer.seek(0)
    return buffer


def register_routes(app):
    @app.before_request
    def restore_auth_from_token():
        g.auth_token = request.values.get("_auth") or request.args.get("_auth")
        g.token_user_id = None
        if not g.auth_token:
            return
        try:
            user_id = int(get_auth_serializer().loads(g.auth_token))
        except (BadSignature, TypeError, ValueError):
            g.auth_token = None
            return
        if User.query.get(user_id):
            g.token_user_id = user_id
            session["user_id"] = user_id

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = os.environ.get("APP_PASSWORD")
            if not password:
                flash("APP_PASSWORD не задан. Добавьте пароль в переменные окружения.", "danger")
                return render_template("login.html")
            if request.form.get("username") != "admin" or request.form.get("password") != password:
                flash("Неверный логин или пароль.", "danger")
                return render_template("login.html")

            ip_address = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
            username = request.form.get("username")
            user = User.query.filter_by(username=username).first()
            now = datetime.utcnow()
            if user:
                user.last_login_at = now
                user.login_count += 1
            else:
                user = User(username="admin", ip_address=ip_address, first_login_at=now, last_login_at=now, login_count=1)
                db.session.add(user)
            db.session.commit()
            session["user_id"] = user.id
            g.auth_token = get_auth_serializer().dumps(user.id)
            return redirect(auth_url("index"))
        return render_template("login.html")

    @app.route("/")
    @login_required
    def index():
        return render_template("index.html")

    @app.route("/process", methods=["POST"])
    @login_required
    def process():
        company_size          = request.form.get("company_size", "").strip()
        company_industry      = request.form.get("company_industry", "").strip()
        product_name = request.form.get("product_name", "").strip()
        situation_description = request.form.get("situation_description", "").strip()

        inputtext = (
            f"Размер компании: {company_size}\n"
            f"Отрасль: {company_industry}\n"
            f"Описание ситуации: {situation_description}"
        ).strip()
       
        required_fields = [
            company_size,
            company_industry,
            situation_description,
        ]
        if not all(required_fields):
            flash("Заполните все 3 обязательных поля", "warning")
            return redirect(url_for("index"))

        input_text = input_text = (
    f"Размер компании: {company_size}\n"
    f"Отрасль компании: {company_industry}\n"
    f"Описание ситуации у компании: {situation_description}"
).strip()

        if product_name:
            input_text += f"""

Продукт: {product_name}
Дополнительное условие: если указанный продукт релевантен ситуации клиента, обязательно явно используй именно этот продукт в предлагаемых стратегиях. Не игнорируй продукт и не заменяй его абстрактными формулировками.
        """.strip()


        user_input = UserInput(
            user_id=current_user_id(),
            input_text=input_text,
            session_token=str(uuid.uuid4()),
        )
        db.session.add(user_input)
        db.session.commit()

        try:
            clarify = call_openai_raw(PROMPT_CLARIFY, input_text)
            if clarify.get("status") == "need_clarification":
                # сохраняем вопросы
                db.session.add(Clarification(
                    input_id=user_input.id,
                    questions=json.dumps(clarify["questions"])
                ))
                db.session.commit()

                return redirect(url_for("clarify", input_id=user_input.id))

        except Exception as e:
            db.session.rollback()
            print("AI ERROR:", repr(e), flush=True)
            flash_ai_error()
            return redirect(url_for("index"))

        return redirect(url_for("process_after_clarify", input_id=user_input.id))


    @app.route("/clarify/<int:input_id>")

    def clarify(input_id):
        clarification = Clarification.query.filter_by(input_id=input_id).first()
        if not clarification:
            return redirect(url_for("process_after_clarify", input_id=input_id))
        questions = json.loads(clarification.questions)
        return render_template("clarify.html", questions=questions)

    @app.route("/clarify/<int:input_id>", methods=["POST"])
    @login_required
    def clarify_submit(input_id):
        clarification = Clarification.query.filter_by(input_id=input_id).first()

        raw = request.form.to_dict()

        answers = {}

        for q in json.loads(clarification.questions):
            key = q["key"]

            selected_value = request.form.get(key)
            custom_value = request.form.get(f"{key}_custom")

            if selected_value == "custom":
                answers[key] = custom_value
            else:
                answers[key] = selected_value

        if "skip" in request.form:
            clarification.status = "skipped"
            clarification.answers = None
        else:
            clarification.status = "done"
            clarification.answers = json.dumps(answers, ensure_ascii=False)

        db.session.commit()
        return redirect(url_for("process_after_clarify", input_id=input_id))

    @app.route("/process_after_clarify/<int:input_id>")
    @login_required
    def process_after_clarify(input_id):
        user_input = UserInput.query.get_or_404(input_id)
        clarification = Clarification.query.filter_by(input_id=input_id).first()

        final_input = build_final_input(user_input, clarification)

        try:
            items = call_openai(PROMPT_A, final_input)
            final_items = call_openai_check_str(items)

            for item in final_items:
                db.session.add(Agent1Response(
                    input_id=input_id,
                    round_number=1,
                    status="pending",
                    **item
                ))

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            print("AI ERROR:", repr(e), flush=True)
            flash_ai_error()

        return redirect(url_for("review", input_id=input_id))

    @app.route("/review/<int:input_id>")
    @login_required
    def review(input_id):
        user_input = UserInput.query.get_or_404(input_id)

        responses = Agent1Response.query.filter_by(input_id=input_id).order_by(
            Agent1Response.round_number.desc(),
            Agent1Response.item_number.asc()
        ).all()

        rounds = defaultdict(list)
        for item in responses:
            rounds[item.round_number].append(item)

        accepted = any(item.status == "accepted" for item in responses)

        clarification = Clarification.query.filter_by(input_id=input_id).first()

        parsed_clarification = None

        if clarification and clarification.questions and clarification.answers:
            try:
                questions = json.loads(clarification.questions)
                answers = json.loads(clarification.answers)

                merged = []

                for q in questions:
                    key = q.get("key")
                    merged.append({
                        "question": q.get("question"),
                        "answer": answers.get(key)
                    })

                if clarification and clarification.questions and clarification.answers:
                    questions = json.loads(clarification.questions)
                    answers = json.loads(clarification.answers)

                    text_lines = []
                    for q in questions:
                        key = q.get("key")
                        text_lines.append(
                            f"{q.get('question')}: {answers.get(key) or 'не указано'}"
                        )

                    parsed_clarification = "\n".join(text_lines)

            except Exception:
                parsed_clarification = None

        return render_template(
            "review.html",
            user_input=user_input,
            rounds=rounds,
            accepted=accepted,
            clarification=clarification,
            parsed_clarification=parsed_clarification
        )

    @app.route("/more/<int:input_id>", methods=["POST"])
    @login_required
    def more(input_id):
        try:
            create_more_agent1_responses(input_id)
            db.session.commit()
        except Exception as e:
                db.session.rollback()
                print("AI ERROR:", repr(e), flush=True)
                flash_ai_error()
        return redirect(auth_url("review", input_id=input_id))

    @app.route("/item1/accept/<int:response_id>", methods=["POST"])
    @login_required
    def item1_accept(response_id):
        response = Agent1Response.query.get_or_404(response_id)

        Agent1Response.query.filter(
            Agent1Response.input_id == response.input_id,
            Agent1Response.status == "accepted",
            Agent1Response.id != response_id
        ).update({"status": "pending"})

        response.status = "accepted"
        db.session.commit()
        return jsonify({"ok": True})  # без reload — JS сам обновляет карточки

    @app.route("/item1/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item1_reject(response_id):
        response = Agent1Response.query.get_or_404(response_id)
        input_id = response.input_id
        current_round = response.round_number

        response.status = "rejected"
        db.session.commit()

        remaining_active = Agent1Response.query.filter(
            Agent1Response.input_id == input_id,
            Agent1Response.round_number == current_round,
            Agent1Response.status.in_(["pending", "accepted"])
        ).count()


        if remaining_active == 0:
            try:
                create_more_agent1_responses(input_id)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print("AI ERROR", repr(e), flush=True)
                flash_ai_error()
                return jsonify(ok=False, reload=False), 500

            return jsonify(ok=True, reload=True)

        return jsonify(ok=True, reload=False)

    @app.route("/item1/save/<int:response_id>", methods=["POST"])
    @login_required
    def item1_save(response_id):
        response = Agent1Response.query.get_or_404(response_id)
        try:
            fields = validate_payload(request.get_json(force=True) or {})
        except ValueError:
            return jsonify({"ok": False}), 400
        edit = response.edit
        if edit:
            edit.edited_at = datetime.utcnow()
            edit.edited_title = fields["title"]
            edit.edited_description = fields["description"]
            edit.edited_logic = fields["logic"]
            edit.edited_criteria = fields["criteria"]
        else:
            edit = Agent1Edit(
                agent1_response_id=response.id,
                original_title=response.title,
                original_description=response.description,
                original_logic=response.logic,
                original_criteria=response.criteria,
                edited_title=fields["title"],
                edited_description=fields["description"],
                edited_logic=fields["logic"],
                edited_criteria=fields["criteria"],
            )
            db.session.add(edit)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item1/custom/<int:input_id>", methods=["POST"])
    @login_required
    def item1_custom(input_id):
        UserInput.query.get_or_404(input_id)
        try:
            payload = request.get_json(silent=True) or request.form
            fields = validate_payload(payload)
        except ValueError:
            if request.is_json:
                return jsonify({"ok": False}), 400
            flash("Заполните все поля.", "warning")
            return redirect(auth_url("review", input_id=input_id))

        ok, reason = validate_custom_item(fields, "agent1")
        if not ok:
            if request.is_json:
                return jsonify({"ok": False, "reason": reason}), 422
            flash(f"Вариант не прошёл проверку: {reason}", "warning")
            return redirect(auth_url("review", input_id=input_id))

        db.session.add(Agent1Response(
            input_id=input_id,
            round_number=max(
                (item.round_number for item in Agent1Response.query.filter_by(input_id=input_id).all()),
                default=1
            ),
            item_number=next_agent1_item_number(input_id),
            title=fields["title"],
            description=fields["description"],
            logic=fields["logic"],
            criteria=fields["criteria"],
            status="accepted",
            implemented=False,
        ))
        db.session.commit()

        if request.is_json:
            return jsonify({"ok": True, "reload": True})
        return redirect(auth_url("review", input_id=input_id))

    @app.route("/continue/<int:input_id>", methods=["POST"])
    @login_required
    def continue_agent1(input_id):
        response = Agent1Response.query.filter_by(
            input_id=input_id, status="accepted"
        ).first()

        if not response:
            flash("Сначала выберите один вариант.", "warning")
            return redirect(auth_url("review", input_id=input_id))

        payload = final_agent1_payload(response)
        existing = Agent1Selected.query.filter_by(input_id=input_id).first()

        if existing:
            existing.agent1_response_id = response.id
            existing.final_title = payload["title"]
            existing.final_description = payload["description"]
            existing.final_logic = payload["logic"]
            existing.final_criteria = payload["criteria"]
            existing.was_edited = payload["was_edited"]
            selected = existing

            Agent2Response.query.filter_by(selected_id=existing.id).delete()
            Agent2Final.query.filter_by(selected_id=existing.id).delete()
        else:
            selected = Agent1Selected(
                input_id=input_id,
                agent1_response_id=response.id,
                final_title=payload["title"],
                final_description=payload["description"],
                final_logic=payload["logic"],
                final_criteria=payload["criteria"],
                was_edited=payload["was_edited"],
            )
            db.session.add(selected)

        db.session.commit()
        return redirect(auth_url("agent2", selected_id=selected.id))
    @app.route("/agent2/<int:selected_id>")
    @login_required
    def agent2(selected_id):
        selected = Agent1Selected.query.get_or_404(selected_id)
        responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
        if not responses:
            message = f"""Название: {selected.final_title}
Описание: {selected.final_description}
Логика: {selected.final_logic}
Критерии: {selected.final_criteria}"""
            steps = Agent2Final.query.all()
            result = [
                {
                    "title": s.final_title,
                    "description": s.final_description
                }
                for s in steps
            ]

            try:
                items = call_openai(PROMPT_B, message)
                final_items = call_openai_check_stp(items)
                for item in final_items:
                    db.session.add(Agent2Response(selected_id=selected_id, status="pending", **item))
                db.session.commit()
                responses = Agent2Response.query.filter_by(selected_id=selected_id).order_by(Agent2Response.item_number.asc()).all()
            except Exception as e:
                db.session.rollback()
                print("AI ERROR:", repr(e), flush=True)
                flash_ai_error()
        accepted = any(item.status == "accepted" for item in responses)
        return render_template("agent2.html", selected=selected, responses=responses, accepted=accepted)

    @app.route("/item2/accept/<int:response_id>", methods=["POST"])
    @login_required
    def item2_accept(response_id):
        response = Agent2Response.query.get_or_404(response_id)
        response.status = "accepted"
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/reject/<int:response_id>", methods=["POST"])
    @login_required
    def item2_reject(response_id):
        response = Agent2Response.query.get_or_404(response_id)
        selected_id = response.selected_id
        response.status = "rejected"
        db.session.commit()
        remaining = Agent2Response.query.filter(
            Agent2Response.selected_id == selected_id,
            Agent2Response.status.in_(["pending", "accepted"]),
        ).count()
        if remaining == 0:
            try:
                create_more_agent2_responses(selected_id)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print("AI ERROR:", repr(e), flush=True)
                flash_ai_error()
            return jsonify({"ok": True, "reload": True})
        return jsonify({"ok": True, "reload": False})

    @app.route("/item2/save/<int:response_id>", methods=["POST"])
    @login_required
    def item2_save(response_id):
        response = Agent2Response.query.get_or_404(response_id)
        try:
            fields = validate_payload(request.get_json(force=True) or {})
        except ValueError:
            return jsonify({"ok": False}), 400
        response.title = fields["title"]
        response.description = fields["description"]
        response.logic = fields["logic"]
        response.criteria = fields["criteria"]
        response.was_edited = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/item2/custom/<int:selected_id>", methods=["POST"])
    @login_required
    def item2_custom(selected_id):
        Agent1Selected.query.get_or_404(selected_id)
        try:
            payload = request.get_json(silent=True) or request.form
            fields = validate_payload(payload)
        except ValueError:
            if request.is_json:
                return jsonify({"ok": False}), 400
            flash("Заполните все поля.", "warning")
            return redirect(auth_url("agent2", selected_id=selected_id))

    # Проверка соответствия правилам Агента 2
        ok, reason = validate_custom_item(fields, "agent2")
        if not ok:
            if request.is_json:
                return jsonify({"ok": False, "reason": reason}), 422
            flash(f"Шаг не прошёл проверку: {reason}", "warning")
            return redirect(auth_url("agent2", selected_id=selected_id))

        db.session.add(Agent2Response(
            selected_id=selected_id,
            item_number=next_agent2_item_number(selected_id),
            title=fields["title"],
            description=fields["description"],
            logic=fields["logic"],
            criteria=fields["criteria"],
            status="accepted",
            was_edited=True,
            implemented=False,
        ))
        db.session.commit()
        if request.is_json:
            return jsonify({"ok": True, "reload": True})
        return redirect(auth_url("agent2", selected_id=selected_id))

    @app.route("/agent2/finish/<int:selected_id>", methods=["POST"])
    @login_required
    def agent2_finish(selected_id):
        responses = Agent2Response.query.filter_by(selected_id=selected_id, status="accepted").order_by(
            Agent2Response.item_number.asc()
        ).all()
        if not responses:
            flash("Сначала выберите хотя бы один вариант Агента 2.", "warning")
            return redirect(auth_url("agent2", selected_id=selected_id))
        payload = combine_payloads([final_agent2_payload(response) for response in responses], "Финальный выбор")
        first_response = responses[0]
        final = Agent2Final.query.filter_by(selected_id=selected_id).first()
        if final:
            final.agent2_response_id = first_response.id
            final.final_title = payload["title"]
            final.final_description = payload["description"]
            final.final_logic = payload["logic"]
            final.final_criteria = payload["criteria"]
            final.was_edited = payload["was_edited"]
            final.saved_at = datetime.utcnow()
        else:
            final = Agent2Final(
                selected_id=selected_id,
                agent2_response_id=first_response.id,
                final_title=payload["title"],
                final_description=payload["description"],
                final_logic=payload["logic"],
                final_criteria=payload["criteria"],
                was_edited=payload["was_edited"],
            )
            db.session.add(final)
        db.session.commit()
        return redirect(auth_url("result", selected_id=selected_id))

    @app.route("/result/<int:selected_id>")
    @login_required
    def result(selected_id):
        selected = Agent1Selected.query.get_or_404(selected_id)
        final = Agent2Final.query.filter_by(selected_id=selected_id).first()
        if not final:
            flash("Финальный результат Агента 2 ещё не сохранён.", "warning")
            return redirect(auth_url("agent2", selected_id=selected_id))
        return render_template("result.html", selected=selected, final=final)

    @app.route("/result/<int:selected_id>/pdf")
    @login_required
    def result_pdf(selected_id):
        selected = Agent1Selected.query.get_or_404(selected_id)
        final = Agent2Final.query.filter_by(selected_id=selected_id).first_or_404()
        buffer = build_results_pdf(selected, final)
        return Response(
            buffer.getvalue(),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=result-{selected_id}.pdf"},
        )

    @app.route("/history")
    @login_required
    def history():
        inputs = UserInput.query.filter_by(user_id=current_user_id()).order_by(UserInput.created_at.desc()).all()
        rows = []
        for item in inputs:
            rounds_a = db.session.query(Agent1Response.round_number).filter_by(input_id=item.id).distinct().count()
            selected = Agent1Selected.query.filter_by(input_id=item.id).first()
            rounds_b = 0
            completed = False
            open_url = auth_url("review", input_id=item.id)
            if selected:
                rounds_b = 1 if Agent2Response.query.filter_by(selected_id=selected.id).count() else 0
                completed = Agent2Final.query.filter_by(selected_id=selected.id).first() is not None
                open_url = auth_url("result", selected_id=selected.id) if completed else auth_url("agent2", selected_id=selected.id)
            rows.append({"input": item, "rounds_a": rounds_a, "rounds_b": rounds_b, "completed": completed, "open_url": open_url})
        return render_template("history.html", rows=rows)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)