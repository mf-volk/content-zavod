# Contributing

Thanks for your interest in improving Content Zavod!

## Development setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install pytest              # for running tests
cp .env.example .env            # then fill in your tokens
```

## Running

```bash
python -m app.main
```

## Tests

```bash
pytest tests/ -v
```

Please run the tests before opening a pull request.

## Conventions

- **UI, prompts and user-facing messages are in Russian**; code, comments and
  identifiers are in English.
- Handlers live in `app/handlers/`, each defining an aiogram `Router`.
  Callback data uses the `"module:action:id"` format.
- Keep secrets out of the repo — everything sensitive goes in `.env`
  (which is gitignored). Never commit real API keys or personal data.

## Reporting issues

Please open a GitHub issue with steps to reproduce, expected vs. actual
behaviour, and relevant log output (with secrets redacted).
