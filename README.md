<p align="center">
  <img src="assets/teleceh-hero.png" width="340" alt="ТелеЦех">
</p>

<h1 align="center">ТелеЦех</h1>

<p align="center">
  <strong>Русский</strong> | <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="license MIT" src="https://img.shields.io/badge/license-MIT-2ea44f"></a>
  <img alt="platform Telegram" src="https://img.shields.io/badge/platform-Telegram-2CA5E0">
  <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="aiogram 3" src="https://img.shields.io/badge/aiogram-3.x-26A5E4">
  <img alt="database SQLite PostgreSQL" src="https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-5865F2">
</p>

<p align="center">
  Telegram-бот для AI-идей, черновиков, изображений и отложенных публикаций в каналы.
</p>

---

> **Open-source note.** ТелеЦех рассчитан на личный или self-hosted запуск. Не коммитьте реальные токены, API-ключи, базы данных, логи и Telegram session-файлы.

## 🔎 AI SEO / смысловой поиск

ТелеЦех помогает автоматизировать создание контента для Telegram-каналов: искать идеи по донорским каналам, писать посты с помощью AI, учитывать tone of voice, генерировать изображения, собирать материалы, вести контент-план и публиковать посты по расписанию.

Проект может быть полезен, если вы ищете: open-source Telegram bot для контент-маркетинга, AI content generator для Telegram, бот для автопостинга, генератор идей из донорских каналов, планировщик публикаций, Telegram channel manager, self-hosted AI writing assistant, OpenAI bot на aiogram, инструмент для контент-плана и автоматизации SMM.

## ✨ Возможности

- 📢 **Мультиканальность**: управление несколькими Telegram-каналами из одного бота.
- 🧲 **Мониторинг доноров**: парсинг публичных Telegram-каналов через `t.me/s/`.
- 💡 **AI-идеи**: анализ донорских постов и генерация идей для ваших каналов.
- ✍️ **Черновики**: написание и переписывание постов с учетом tone of voice.
- 🎙️ **Tone of voice**: генерация стиля из последних постов или ручная настройка.
- 🎨 **AI-изображения**: генерация медиа через OpenAI или Kie.ai.
- 📁 **Пространства**: сбор DOCX, XLSX, PDF, ссылок, изображений и аудио как материалов для идей.
- ▶️ **YouTube-идеи**: получение публичных субтитров через `youtube-transcript-api`.
- 🗓️ **Контент-план**: планирование публикаций и автоматический постинг.

## 🧰 Стек

| Зона | Технологии |
|---|---|
| Bot framework | aiogram 3.x |
| База данных | SQLAlchemy 2.x, SQLite по умолчанию, PostgreSQL опционально |
| Text AI | OpenAI-compatible Chat Completions API |
| Изображения | OpenAI Images API или Kie.ai |
| Документы | python-docx, openpyxl, PyPDF2 |
| Парсинг | aiohttp, BeautifulSoup, `t.me/s/` |

## 🚀 Быстрый старт

```bash
git clone https://github.com/mf-volk/content-zavod.git
cd content-zavod

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Заполните минимум две переменные:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
OPENAI_API_KEY=sk-...
```

Запустите бота:

```bash
python -m app.main
```

## ⚙️ Конфигурация

ТелеЦех читает настройки из `.env` через `pydantic-settings`.

| Переменная | Обязательна | Описание |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | да | Токен бота от BotFather |
| `OPENAI_API_KEY` | да | Текст, голос, vision и OpenAI images |
| `DATABASE_URL` | нет | По умолчанию `sqlite+aiosqlite:///./content_zavod.db` |
| `AI_PROVIDER` | нет | `openai` или `kie`; провайдер генерации изображений |
| `KIE_API_KEY` | только для Kie.ai | Нужен при `AI_PROVIDER=kie` |
| `LLM_MODEL` | нет | По умолчанию `gpt-4o` |
| `DEFAULT_TIMEZONE` | нет | По умолчанию `Europe/Moscow` |
| `SCHEDULER_INTERVAL` | нет | Интервал проверки расписания в секундах |

Полный список переменных: [.env.example](.env.example).

## 🤖 AI-провайдеры

| `AI_PROVIDER` | Текст | Изображения | Голос / Vision |
|---|---|---|---|
| `openai` | OpenAI-compatible API | OpenAI image model | OpenAI |
| `kie` | OpenAI-compatible API | Kie.ai image model | OpenAI |

Текст всегда идет через OpenAI-compatible клиент. Голосовые сообщения и распознавание изображений тоже используют OpenAI-модели, поэтому `OPENAI_API_KEY` нужен в обоих режимах.

## 🧭 Как пользоваться

1. Отправьте боту `/start`.
2. Добавьте канал. Бот должен быть администратором с правом публикации.
3. Настройте tone of voice вручную или сгенерируйте его из последних постов.
4. Добавьте донорские каналы и сгенерируйте идеи.
5. Превратите идеи в черновики, добавьте медиа и запланируйте публикации.

## 🗂️ Структура проекта

```text
content-zavod/
|-- app/
|   |-- db/                 # SQLAlchemy models and async sessions
|   |-- handlers/           # aiogram routers and FSM flows
|   |-- services/           # publishing, document processing, Telegraph upload
|   |-- config.py           # pydantic-settings configuration
|   |-- donor_parser.py     # public Telegram channel parser
|   |-- image_generation.py # OpenAI / Kie.ai image generation
|   |-- llm_client.py       # OpenAI-compatible LLM client
|   |-- main.py             # bot entry point
|   `-- scheduler.py        # background publishing and parsing tasks
|-- scripts/                # manual database migration helpers
|-- tests/
|-- .env.example
|-- requirements.txt
`-- README.md
```

## 🗄️ База данных

Таблицы создаются автоматически при первом запуске через `Base.metadata.create_all`. По умолчанию используется SQLite. Для production можно использовать PostgreSQL:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/content_zavod
```

Для существующих установок применяйте изменения схемы вручную через скрипты в `scripts/`.

## 🛠️ Разработка

```bash
pip install -r requirements.txt
pip install pytest
pytest tests/ -v
```

Соглашения:

- пользовательский интерфейс, промпты и сообщения бота написаны на русском;
- имена переменных, кодовые комментарии и callback data пишутся на английском;
- секреты хранятся только в `.env`, никогда в git.

См. [CONTRIBUTING.md](CONTRIBUTING.md).

## 🔐 Безопасность

Перед публикацией форка или публичным деплоем прочитайте [SECURITY.md](SECURITY.md). Бот может хранить черновики, метаданные каналов, донорские посты, пользовательские материалы и опциональные API-ключи пользователей в настроенной базе данных.

## 📄 Лицензия

[MIT](LICENSE)
