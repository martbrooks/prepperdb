# Getting started

Start with a local preview. When you want to keep using PrepperDB from other devices, follow [deployment](deployment.md).

## Prerequisites

The reference platform is Debian 13 with Python 3.13, SQLite and a virtual environment. The supplied runtime and development locks contain hashes for tested Python 3.13/Linux x86-64 wheels, including Pillow's platform-specific wheel. Other operating systems/architectures are not verified. If your installer cannot find a matching wheel, do not bypass hash checking: use a matching Linux VM or deliberately resolve and verify a platform-specific lock. `python3.13 --version` should report Python 3.13.

On Debian 13:

```sh
sudo apt-get update
sudo apt-get install python3-venv sqlite3 git
```

Download the v0.1 source archive from this repository's Releases page, extract it, and enter the extracted directory. A Git checkout works too; check out the `v0.1` tag for this release. No JavaScript build or external account is required.

## Install and run

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
export PREPPERDB_DEVELOPMENT=1
python manage.py migrate --noinput
python manage.py user_account alex
python manage.py runserver 127.0.0.1:8000 --noreload --insecure
```

Choose your own username instead of `alex`. The command prompts twice for a password of at least 12 characters; it stores a salted hash. Open **http://127.0.0.1:8000/** on the same computer. No stock is preloaded. Add stock with a name and quantity, then explore the stock views. See the [user guide](user-guide.md).

Keep this preview bound to `127.0.0.1`: development mode uses a known test secret. `--insecure` here tells Django to serve local static assets even though DEBUG remains off; it is not an HTTPS certificate bypass. For a permanent installation, use Waitress/nginx and a unique secret as described in [deployment](deployment.md). Configuration is read from environment variables; the app does **not** automatically read `.env` files.

## Where your data lives

By default the preview creates `var/stock.sqlite3`, with SQLite WAL/journal files beside it. It contains users, stock and the authoritative product photos. Keep it private and back it up consistently. Do not upload it with bug reports or commit it to Git. Preview users and data survive stopping/restarting the app. Use a fresh `PREPPERDB_STATE_DIR` to try a separate, empty instance; do not delete a database you want to keep.

## Common problems

- **DisallowedHost / HTTP 400:** add the exact hostname or IP (without scheme/port) to `PREPPERDB_ALLOWED_HOSTS`; no wildcard. In production this is required.
- **Redirects to HTTPS:** production defaults to HTTPS. Use a correctly configured HTTPS proxy, or explicitly set `PREPPERDB_HTTPS=0` for HTTP. Do not enable development mode as a workaround for production.
- **Login CSRF error:** use one consistent browser URL; ensure the proxy preserves Host and overwrites the documented scheme header. For a nonstandard port or deliberately different origin, set an exact scheme/host/port in `PREPPERDB_CSRF_TRUSTED_ORIGINS`.
- **Missing styling/images:** the preview command needs `--insecure`; production needs `collectstatic` and the nginx static location. Never serve the whole checkout or state directory.
- **Camera unavailable:** grant camera permission and use trusted HTTPS or localhost. HTTP on another computer/phone normally cannot access the camera. Manual typing and selecting a barcode photo are alternatives.
- **Forgotten password:** stop the preview, activate the same virtual environment and environment, then run `python manage.py user_account alex` to replace it. Production uses `sudo prepperdb-manage user_account alex`. No email recovery is provided.
