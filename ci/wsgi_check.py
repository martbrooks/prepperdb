"""Production WSGI/socket/session smoke test without enabling a public listener."""

import http.client
from io import BytesIO
from http.cookies import SimpleCookie
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlencode
from PIL import Image


def main():
    with tempfile.TemporaryDirectory(prefix="prepperdb-wsgi-") as state:
        sockpath = str(Path(state) / "app.sock")
        env = {
            **os.environ,
            "PREPPERDB_DEVELOPMENT": "0",
            "PREPPERDB_SECRET_KEY": secrets.token_urlsafe(64),
            "PREPPERDB_STATE_DIR": state,
            "PREPPERDB_ALLOWED_HOSTS": "stock.example.test",
            "PREPPERDB_TRUSTED_PROXIES": "localhost",
            "PREPPERDB_SOCKET": sockpath,
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
                "import uuid; from stock.models import User,Product; User.objects.create_user('socket-fixture','synthetic-socket-password-9187'); Product.objects.create(family=uuid.uuid4(),name='Socket photo fixture')",
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
            conn = http.client.HTTPConnection("stock.example.test", timeout=5)
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.connect(sockpath)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                result = conn.getresponse()
                return result.status, result.getheaders(), result.read().decode()
            finally:
                conn.close()

        try:
            for _ in range(100):
                if Path(sockpath).exists():
                    break
                if server.poll() is not None:
                    raise RuntimeError("WSGI process exited before creating socket.")
                time.sleep(0.05)
            assert stat.S_IMODE(Path(sockpath).stat().st_mode) == 0o660
            assert request("/login/")[0] == 301
            assert request("/login/", headers={"X-Forwarded-Proto": "https"})[0] == 301
            trusted = {"X-Prepperdb-Proto": "https", "X-Prepperdb-Client-IP": "192.0.2.25"}
            status, headers, html = request("/login/", headers=trusted)
            assert status == 200
            csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
            cookies = SimpleCookie()
            for key, value in headers:
                if key.lower() == "set-cookie":
                    cookies.load(value)
            body = urlencode(
                {
                    "username": "socket-fixture",
                    "password": "synthetic-socket-password-9187",
                    "csrfmiddlewaretoken": csrf,
                }
            )
            status, headers, _ = request(
                "/login/",
                method="POST",
                body=body,
                headers={
                    **trusted,
                    "Cookie": "csrftoken=" + cookies["csrftoken"].value,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://stock.example.test",
                },
            )
            assert status == 302
            for key, value in headers:
                if key.lower() == "set-cookie":
                    cookies.load(value)
            session = cookies["prepperdb_session"]
            assert session["secure"] and session["httponly"] and session["samesite"] == "Lax"
            status, _, html = request(
                "/", headers={**trusted, "Cookie": "prepperdb_session=" + session.value}
            )
            assert status == 200 and "Stock status" in html
            auth = {
                **trusted,
                "Cookie": "prepperdb_session="
                + session.value
                + "; csrftoken="
                + cookies["csrftoken"].value,
                "Origin": "https://stock.example.test",
            }
            status, _, html = request("/products/1/edit/", headers=auth)
            assert status == 200
            csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
            picture = BytesIO()
            Image.effect_noise((1024, 1024), 128).convert("RGB").save(picture, "JPEG", quality=90)
            photo = picture.getvalue()
            assert len(photo) > 262144
            boundary = "prepperdb-" + uuid.uuid4().hex
            fields = {
                "csrfmiddlewaretoken": csrf,
                "token": str(uuid.uuid4()),
                "revision": "0",
                "name": "Socket photo fixture",
                "size_value": "",
                "size_unit": "",
                "packaging": "",
                "category": "",
                "barcode": "",
            }
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
                for name, value in fields.items()
            ]
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="synthetic.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
                + photo
                + f"\r\n--{boundary}--\r\n".encode()
            )
            status, _, _ = request(
                "/products/1/edit/",
                method="POST",
                headers={**auth, "Content-Type": "multipart/form-data; boundary=" + boundary},
                body=b"".join(parts),
            )
            assert status == 302, status
            status, _, html = request("/products/1/", headers=auth)
            assert status == 200 and "Custom product photo." in html
            print(
                "PASS: production Waitress accepts an authenticated multipart photo above the former 256 KiB limit."
            )
            print(
                "PASS: production Waitress Unix socket, explicit proxy scheme, CSRF login and secure authenticated session."
            )
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
