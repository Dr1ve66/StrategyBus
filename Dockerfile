FROM python:3.11-slim

WORKDIR /app

# Системные зависимости + шрифты для ReportLab
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Python-библиотеки
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект внутрь контейнера
COPY . .

# Папка для базы SQLite
RUN mkdir -p /app/data

# Переменная для БД (если код её использует)
ENV APP_DATABASE_URL=sqlite:////app/data/app.db

EXPOSE 5000

# Запускаем именно app.py, а не main.py
CMD ["python", "app.py"]
