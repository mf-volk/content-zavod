# Security Policy

## Supported Use

Content Zavod is published for personal or self-hosted use. Do not commit real
tokens, API keys, databases, logs, Telegram session files, downloaded user
content, or generated media.

## Secrets

Configuration belongs in `.env`, which is ignored by git. If a secret is ever
committed or pushed, rotate it immediately in the provider dashboard and remove
it from git history before publishing the repository.

Common secrets for this project include:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `KIE_API_KEY`
- `OPENROUTER_API_KEY`
- payment provider credentials
- MTProto API credentials and `.session` files

## Reporting Vulnerabilities

Please open a private security advisory or contact the repository maintainer
privately before publishing exploit details.

## Data Handling

The bot may store channel metadata, drafts, scheduled posts, donor posts, user
materials, and optional user-provided API keys in the configured database.
Operators are responsible for protecting the database, backups, and logs.
