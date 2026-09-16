# 🎓 AI-Методист

Telegram-бот, который генерирует интерактивные уроки по загруженным PDF-учебникам.

Загружаешь учебник — бот разбирает его на смысловые фрагменты, строит векторную базу знаний и по запросу составляет структурированный урок: цель, объяснение, аналогия, пример и вопросы для проверки. Всё работает **локально**, без внешних API.

---

## ✨ Что умеет

- 📥 **Загрузка учебников** — принимает PDF (текстовые и сканы, через OCR), сохраняет метаданные: название, класс, авторы.
- 🧠 **Генерация уроков** — по теме и выбранному учебнику составляет урок с опорой **только на текст учебника**, а не на выдумки модели.
- 📖 **Источники** — в конце урока указывает страницы, из которых взята информация.
- 👤 **Профили пользователей** — у каждого свои учебники, чужие не смешиваются.

## 🏗️ Архитектура

```
┌─────────────┐     ┌──────────┐     ┌─────────────┐
│  Telegram   │────▶│  Redis   │────▶│   Worker    │
│    Bot      │     │ (queue)  │     │   (arq)     │
└──────┬──────┘     └──────────┘     └──────┬──────┘
       │                                     │
       │              ┌──────────────┐       │
       └─────────────▶│  PostgreSQL  │◀──────┘
                      │  + pgvector  │
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │ Ollama (LLM) │
                      │ Qwen + Gemma │
                      └──────────────┘
```

1. **Bot** — принимает PDF, ставит задачу в очередь, отвечает пользователю.
2. **Worker** — в фоне парсит PDF, считает эмбеддинги, пишет в БД.
3. **PostgreSQL + pgvector** — хранит чанки текста и их векторы.
4. **Ollama** — локальная LLM для генерации урока и эмбеддинг-модель для поиска.

## 🛠️ Стек

| Компонент | Технология |
|-----------|-----------|
| Бот | aiogram 3 |
| БД | PostgreSQL 16 + pgvector |
| Очередь задач | Redis + arq |
| LLM | Ollama (Qwen 2.5) |
| Эмбеддинги | Ollama (EmbeddingGemma, 768d) |
| Парсинг PDF | PyMuPDF, pymupdf4llm |
| OCR для сканов | Tesseract + pdf2image |
| Миграции | Alembic |
| ORM | SQLAlchemy 2.0 (async) |

## 🚀 Запуск

### 1. Требования

- Python 3.11+
- PostgreSQL 16 с расширением `pgvector`
- Redis
- [Ollama](https://ollama.com)
- Tesseract OCR с русским языком (для сканов)

```bash
sudo apt install postgresql-16-pgvector redis-server \
                 tesseract-ocr tesseract-ocr-rus poppler-utils
```

### 2. Клонирование

```bash
git clone https://github.com/YOUR_USERNAME/PopitusSchool.git
cd PopitusSchool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Модели в Ollama

```bash
ollama pull qwen2.5:3b-instruct
ollama pull embeddinggemma
```

### 4. Настройка окружения

Создай `.env` в папке `bot/` по образцу `.env.example`:

```env
BOT_TOKEN=your_telegram_bot_token
PROXY_URL=

DB_HOST=localhost
DB_PORT=5432
DB_USER=popit
DB_PASS=your_db_password
DB_NAME=PopitusSchool

REDIS_URL=redis://localhost:6379

LLM_BASE_URL=http://localhost:11434
LLM_MODEL_CHAT=qwen2.5:3b-instruct

EMBEDDING_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=embeddinggemma
EMBEDDING_DIM=768
```

### 5. База данных

```bash
# создай пользователя и БД (один раз)
sudo -u postgres psql -c "CREATE USER popit WITH PASSWORD 'your_db_password' SUPERUSER;"
sudo -u postgres psql -c 'CREATE DATABASE "PopitusSchool" OWNER popit;'

# примени миграции
alembic upgrade head
```

### 6. Запуск

Нужно **два процесса** в разных терминалах:

```bash
# Терминал 1 — бот
cd bot
python3 main.py

# Терминал 2 — воркер
cd bot
arq worker.WorkerSettings
```

## 📱 Использование

1. `/start` — регистрация.
2. **➕ Добавить учебник** — ввести данные (название, класс, авторы) и загрузить PDF.
3. Дождаться сообщения об успешной обработке.
4. **🧠 Сгенерировать урок** — выбрать учебник, ввести тему, получить урок.

## 🧩 Структура проекта

```
PopitusSchool/
├── bot/
│   ├── app/
│   │   └── handlers.py       # обработчики команд
│   ├── database/
│   │   ├── database.py       # engine, session, Base
│   │   └── models.py         # User, Document, Chunk
│   ├── services/
│   │   ├── pdf.py            # парсинг PDF + OCR
│   │   ├── embeddings.py     # получение векторов
│   │   ├── retriever.py      # поиск по pgvector
│   │   ├── generator.py      # генерация урока
│   │   ├── ingest.py         # загрузка PDF в БД
│   │   ├── llm_client.py     # клиент Ollama
│   │   └── queue.py          # постановка задач в Redis
│   ├── config.py             # настройки (pydantic-settings)
│   ├── keyboards.py          # клавиатуры
│   ├── worker.py             # arq-воркер
│   └── main.py               # точка входа бота
├── migration/                # Alembic-миграции
├── alembic.ini
├── requirements.txt
└── README.md
```

## ⚠️ Ограничения

- Telegram Bot API скачивает файлы до **20 МБ**. Для больших PDF нужен [локальный Bot API server](https://github.com/tdlib/telegram-bot-api).
- Качество уроков зависит от размера LLM. На 3B-модели возможны неточности; для лучшего результата — 14B и выше.
- OCR сканов работает, но качество зависит от исходного изображения.

## 📄 Лицензия

Учебный проект. Свободно используй и адаптируй.