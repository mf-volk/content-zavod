# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Content Zavod (Контент-завод) — a Telegram bot for automated content creation and publishing. Users manage multiple Telegram channels, generate ideas from donor channels via AI, create/edit drafts, attach AI-generated images, and schedule publications.

**Language:** Russian (UI, prompts, user messages). Code comments and variable names in English.

## Commands

```bash
# Run the bot
python -m app.main

# Run tests
pytest tests/ -v

# Run a single test
pytest tests/test_core.py::test_parse_count_basic -v

# Database migrations (manual scripts)
python scripts/migrate_ideas_v2.py
python scripts/migrate_media_position.py
```

No build step. No linter configured. Dependencies installed via `pip install -r requirements.txt`.

## Architecture

### Entry Point & Middleware

`app/main.py` creates the aiogram `Dispatcher`, registers all routers, and starts polling. A middleware injects `AsyncSession` into every handler automatically:

```python
async def my_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    # session is auto-injected, auto-commits on return, rolls back on exception
```

Three background tasks run via `asyncio.create_task()` from `app/scheduler.py`:
- **Post scheduler** — publishes due `ScheduledPost` records (interval from `SCHEDULER_INTERVAL` env var)
- **Donor parser** — scrapes public Telegram channels via `t.me/s/{username}`
- **Channel stats collector** — daily subscriber snapshots for analytics

### Handler Pattern

Each handler module in `app/handlers/` defines a `Router`. Routers are registered in order in `main.py`. Handler functions use:
- `@router.callback_query(F.data.startswith("prefix:"))` for inline button callbacks
- `@router.message(SomeState.waiting_for_input)` for FSM text input
- FSM states defined in `app/handlers/states.py` as `StatesGroup` classes
- Keyboard factories in `app/handlers/keyboards.py` return `InlineKeyboardMarkup`

Callback data format: `"module:action:id"` (e.g., `"drafts:view:42"`, `"media:ai_gen_size:1024x1024:7"`).

### Database

Async SQLAlchemy 2.x with SQLite (WAL mode) or PostgreSQL. Models in `app/db/models.py`, session factory in `app/db/session.py`. `Base` and `TimestampMixin` (auto `created_at`/`updated_at`) in `app/db/base.py`.

Key model relationships:
- `User` → `ManagedChannel` (owner) → `DonorChannel` → `DonorPost`
- `ManagedChannel` → `Draft` → `DraftMedia`, `ScheduledPost`
- `ManagedChannel` → `Idea` (generated from donors)
- `User` → `Space` → `SpaceMaterial` (document/audio/link collection)

Tables auto-create on first run via `Base.metadata.create_all()`. Schema changes to existing tables require manual migration scripts in `scripts/`.

### AI Services

- **`app/llm_client.py`** — OpenAI wrapper. `LLMClient` class with methods: `generate_ideas()`, `write_post()`, `rewrite_post()`, `generate_tone_of_voice()`. Returns DTOs (`IdeaDTO`, `DraftTextDTO`).
- **`app/image_generation.py`** — Image generation with a selectable provider (`settings.ai_provider`): OpenAI (`gpt-image-1`/`dall-e-3`) or Kie.ai (task-based `createTask` → `recordInfo` polling). `ImageGenerator.generate_bytes(prompt, size)` returns PNG bytes. Supports 1024x1024, 1792x1024, 1024x1792.
- **`app/donor_parser.py`** — Scrapes `t.me/s/{channel}` with BeautifulSoup. Extracts post text, views, reactions. No MTProto required.

### Services Layer

- `app/services/publisher.py` — `publish_content(bot, chat_id, draft)`. Handles media positioning (top/bottom/text_top), caption length limits, HTML fallback.
- `app/services/document_processor.py` — Extracts text from DOCX, XLSX, PDF, YouTube transcripts (free `youtube-transcript-api`), web links.
- `app/services/telegraph_uploader.py` — Uploads images to Telegraph for hosting.

### Configuration

`app/config.py` uses Pydantic `BaseSettings` loading from `.env`. Key vars: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `AI_PROVIDER` (`openai`|`kie`), `KIE_API_KEY`, `LLM_MODEL`, `DATABASE_URL`, `SCHEDULER_INTERVAL`.

### Navigation Helper

`answer_nav()` from `app/utils.py` edits the current message with breadcrumb-aware navigation. Used throughout handlers for consistent UX.

### HTML Safety

`sanitize_html()` in `app/utils.py` fixes unclosed tags and strips unsupported ones (keeps b, i, u, code, pre, a, blockquote). Publishing always tries HTML parse mode first, falls back to plain text on `TelegramBadRequest`.
