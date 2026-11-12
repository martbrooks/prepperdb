# Contributing

Suggestions, bug reports and patches are welcome, including AI-assisted and entirely AI-generated contributions. This project was developed using AI coding tools under its owner's direction. No AI service or API key is required to run it.

You are responsible for reviewing your submission. Explain what problem it solves, the approach taken, any use of AI, and exactly which checks you ran. Unrun tests and known limitations should be stated plainly. AI use is not a reason to reject a contribution; unexplained changes, fabricated test results, secrets and incompatible copied code are not acceptable.

Use GitHub issues for bugs and suggestions. Include the version, browser/OS, deployment method, steps to reproduce, expected behaviour and sanitised logs. Never attach a real stock database, passwords, tokens or private photos. Open a pull request against `main`; small focused patches are easiest to review. Keep interface text and documentation in British English. Update documentation and tests when behaviour changes.

## Development checks

Use Python 3.13 and follow [getting started](docs/getting-started.md), then:

```sh
python -m pip install --require-hashes -r requirements-dev.lock
ruff check .
ruff format --check .
PREPPERDB_DEVELOPMENT=1 python manage.py makemigrations --check --dry-run
PREPPERDB_DEVELOPMENT=1 python manage.py test --noinput
python ci/production_check.py
python ci/migration_check.py
python ci/wsgi_check.py
python ci/http_check.py
python -m playwright install --with-deps chromium webkit
python ci/browser.py
PREPPERDB_BROWSER=webkit python ci/browser.py
```

Browser tests use disposable synthetic data and block external requests. Runtime and development locks currently target Python 3.13 on Linux x86-64. Other platforms may need freshly resolved compatible dependencies; do not remove hash checking silently. Preserve database migrations and test populated upgrades. Avoid mandatory external services or frontend build dependencies.

## Licence

Code contributions use GPL-3.0-only; contributions to project artwork use CC BY 4.0. Only submit code and assets you are entitled to contribute, and retain third-party attribution. See `THIRD_PARTY_NOTICES.md` for separately licensed dependencies and data.
