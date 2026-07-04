<p align="center">
  <img src="assets/teleceh-hero.png" width="340" alt="TeleCeh">
</p>

<h1 align="center">TeleCeh</h1>

<p align="center">
  <a href="README.md">Русский</a> | <strong>English</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="license MIT" src="https://img.shields.io/badge/license-MIT-2ea44f"></a>
  <img alt="platform Telegram" src="https://img.shields.io/badge/platform-Telegram-2CA5E0">
  <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="aiogram 3" src="https://img.shields.io/badge/aiogram-3.x-26A5E4">
  <img alt="database SQLite PostgreSQL" src="https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-5865F2">
</p>

<p align="center">
  Telegram content factory for AI-assisted ideas, drafts, images, and scheduled publishing.
</p>

---

> **Open-source note.** TeleCeh is designed for personal or self-hosted use. Keep real bot tokens, API keys, databases, logs, and Telegram session files out of the repository.

## Features

- **Multi-channel workspace**: manage several Telegram channels from one bot.
- **Donor monitoring**: parse public Telegram channels through `t.me/s/`.
- **AI idea generation**: turn donor posts into content ideas for your channels.
- **Draft writing and rewriting**: generate posts with channel tone of voice.
- **Tone of voice**: derive style from recent posts or set it manually.
- **AI images**: generate media with OpenAI or Kie.ai.
- **Spaces**: collect DOCX, XLSX, PDF, links, images, and audio as source material.
- **YouTube ideas**: extract public transcripts with `youtube-transcript-api`.
- **Content plan**: plan posts and publish them automatically.

## Stack

| Area | Technology |
|---|---|
| Bot framework | aiogram 3.x |
| Database | SQLAlchemy 2.x, SQLite by default, PostgreSQL optional |
| Text AI | OpenAI-compatible Chat Completions API |
| Images | OpenAI Images API or Kie.ai |
| Documents | python-docx, openpyxl, PyPDF2 |
| Parsing | aiohttp, BeautifulSoup, `t.me/s/` |

## Quick Start

```bash
git clone https://github.com/mf-volk/content-zavod.git
cd content-zavod

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Fill in at least these two variables:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
OPENAI_API_KEY=sk-...
```

Run the bot:

```bash
python -m app.main
```

## Configuration

TeleCeh reads settings from `.env` via `pydantic-settings`.

| Variable | Required | Description |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from BotFather |
| `OPENAI_API_KEY` | yes | Text generation, voice transcription, vision, and OpenAI images |
| `DATABASE_URL` | no | Defaults to `sqlite+aiosqlite:///./content_zavod.db` |
| `AI_PROVIDER` | no | `openai` or `kie`; image generation provider |
| `KIE_API_KEY` | only for Kie.ai | Required when `AI_PROVIDER=kie` |
| `LLM_MODEL` | no | Defaults to `gpt-4o` |
| `DEFAULT_TIMEZONE` | no | Defaults to `Europe/Moscow` |
| `SCHEDULER_INTERVAL` | no | Scheduler polling interval in seconds |

Full environment reference: [.env.example](.env.example).

## AI Provider Modes

| `AI_PROVIDER` | Text | Images | Voice / Vision |
|---|---|---|---|
| `openai` | OpenAI-compatible API | OpenAI image model | OpenAI |
| `kie` | OpenAI-compatible API | Kie.ai image model | OpenAI |

Text generation always uses the OpenAI-compatible client. Voice transcription and image understanding also use OpenAI models, so `OPENAI_API_KEY` is required in both modes.

## Usage

1. Send `/start` to your bot.
2. Add a managed channel. The bot must be an admin with publishing rights.
3. Configure tone of voice manually or generate it from recent posts.
4. Add donor channels and generate ideas.
5. Turn ideas into drafts, attach media, and schedule publication.

## Project Structure

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

## Database

Tables are created automatically on first run with `Base.metadata.create_all`. SQLite is the default local database. PostgreSQL can be used for production:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/content_zavod
```

Existing installations should apply schema changes manually with scripts in `scripts/`.

## Development

```bash
pip install -r requirements.txt
pip install pytest
pytest tests/ -v
```

Conventions:

- user-facing text, prompts, and bot messages are in Russian;
- code identifiers, comments, and callback data are in English;
- secrets belong in `.env`, never in git.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Read [SECURITY.md](SECURITY.md) before publishing a fork or deploying a public instance. The bot may store drafts, channel metadata, donor posts, user materials, and optional user-provided API keys in the configured database.

## License

[MIT](LICENSE)
