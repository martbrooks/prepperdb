"""Exercise explicit production HTTP through the real Waitress Unix socket."""

from http.cookies import SimpleCookie
import http.client
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlencode


def main():
    with tempfile.TemporaryDirectory(prefix="prepperdb-http-") as state:
        sock = str(Path(state) / "app.sock")
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("PREPPERDB_")},
            "PREPPERDB_SECRET_KEY": secrets.token_urlsafe(64),
            "PREPPERDB_ALLOWED_HOSTS": "stock.example.test",
            "PREPPERDB_STATE_DIR": state,
            "PREPPERDB_SOCKET": sock,
            "PREPPERDB_HTTPS": "0",
        }
        subprocess.run(
            [os.sys.executable, "manage.py", "migrate", "--noinput"],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                os.sys.executable,
                "manage.py",
                "shell",
                "-c",
                "from stock.models import User; User.objects.create_user('http-fixture','test-http-password-42!')",
            ],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [os.sys.executable, "manage.py", "collectstatic", "--noinput"],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        server = subprocess.Popen([os.sys.executable, "-m", "prepperdb.server"], env=env)

        def request(path, method="GET", headers=None, body=None):
            conn = http.client.HTTPConnection("stock.example.test:8080", timeout=5)
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.connect(sock)
            try:
                conn.request(method, path, headers=headers or {}, body=body)
                response = conn.getresponse()
                return response.status, response.getheaders(), response.read().decode()
            finally:
                conn.close()

        try:
            for _ in range(100):
                if Path(sock).exists():
                    break
                if server.poll() is not None:
                    raise RuntimeError("HTTP server exited.")
                time.sleep(0.05)
            status, headers, html = request("/login/")
            assert status == 200
            cookies = SimpleCookie()
            for key, value in headers:
                if key.lower() == "set-cookie":
                    cookies.load(value)
            assert not cookies["csrftoken"]["secure"]
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
            headers = {
                "Cookie": "csrftoken=" + cookies["csrftoken"].value,
                "Origin": "http://stock.example.test:8080",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            status, response_headers, _ = request(
                "/login/",
                "POST",
                headers,
                urlencode(
                    {
                        "username": "http-fixture",
                        "password": "test-http-password-42!",
                        "csrfmiddlewaretoken": token,
                    }
                ),
            )
            assert status == 302
            for key, value in response_headers:
                if key.lower() == "set-cookie":
                    cookies.load(value)
            session = cookies["prepperdb_session"]
            assert not session["secure"] and session["httponly"] and session["samesite"] == "Lax"
            status, response_headers, html = request(
                "/", headers={"Cookie": "prepperdb_session=" + session.value}
            )
            assert status == 200 and "Stock status" in html
            assert not any(
                key.lower() == "strict-transport-security" for key, value in response_headers
            )
            assert (
                request(
                    "/login/",
                    "POST",
                    {"Content-Type": "application/x-www-form-urlencoded"},
                    "username=x&password=x",
                )[0]
                == 403
            )
            assert request("/", headers={"Host": "untrusted.example"})[0] == 400
            print(
                "PASS: production HTTP login/session works on a nonstandard port, retains CSRF/host checks and omits HSTS."
            )
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
