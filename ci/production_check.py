"""Exercise fail-closed settings and Django's deployment checks with ephemeral state."""

import os
import json
from pathlib import Path
import secrets
import subprocess
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="prepperdb-production-check-") as state:
        env = {key: value for key, value in os.environ.items() if not key.startswith("PREPPERDB_")}
        result = subprocess.run(
            [os.sys.executable, "manage.py", "check"], env=env, capture_output=True
        )
        assert result.returncode != 0 and b"PREPPERDB_SECRET_KEY" in result.stderr
        env.update(
            PREPPERDB_SECRET_KEY=secrets.token_urlsafe(64),
            PREPPERDB_ALLOWED_HOSTS="stock.example.test,stock.example.org",
            PREPPERDB_STATE_DIR=state,
        )
        subprocess.run(
            [os.sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                os.sys.executable,
                "-c",
                "import os; os.umask(0o077); "
                "from django.core.management import execute_from_command_line; "
                "execute_from_command_line(['manage.py','collectstatic','--noinput'])",
            ],
            env={**env, "DJANGO_SETTINGS_MODULE": "prepperdb.settings"},
            check=True,
        )
        vendor = Path(state) / "static/vendor"
        assert vendor.stat().st_mode & 0o777 == 0o755
        assert (vendor / "zxing-browser-0.2.1.min.js").stat().st_mode & 0o777 == 0o644
        manifest = json.loads((Path(state) / "static/staticfiles.json").read_text())
        for asset in ("app.css", "theme.js", "scanner.js", "vendor/zxing-browser-0.2.1.min.js"):
            hashed = manifest["paths"][asset]
            assert hashed != asset and (Path(state) / "static" / hashed).is_file()
        print("PASS: production asset URLs change with content, including scanner and theme files.")
        print("PASS: nested scanner assets remain nginx-readable under a private deployment umask.")
        # HTTP is an explicit production choice; strong secrets and host checks remain.
        probe = (
            "import json; from django.conf import settings as s; "
            "print(json.dumps([s.DEBUG,s.HTTPS,s.SECURE_SSL_REDIRECT,"
            "s.SESSION_COOKIE_SECURE,s.CSRF_COOKIE_SECURE,s.SECURE_HSTS_SECONDS]))"
        )
        result = subprocess.check_output(
            [os.sys.executable, "manage.py", "shell", "-c", probe],
            env={**env, "PREPPERDB_HTTPS": "0"},
            text=True,
        )
        assert json.loads(result.strip().splitlines()[-1]) == [False, False, False, False, False, 0]
        result = subprocess.run(
            [os.sys.executable, "manage.py", "check"],
            env={**env, "PREPPERDB_HTTPS": "yes"},
            capture_output=True,
        )
        assert result.returncode != 0 and b"PREPPERDB_HTTPS must be" in result.stderr
        print("PASS: explicit production HTTP keeps DEBUG off; malformed HTTPS choices fail.")
        env["PREPPERDB_STATIC_ROOT"] = state
        result = subprocess.run(
            [os.sys.executable, "manage.py", "check"], env=env, capture_output=True
        )
        assert result.returncode != 0 and b"Static root must not expose" in result.stderr
        print("PASS: production settings fail closed and deployment checks pass.")


if __name__ == "__main__":
    main()
