"""Production entry point; socket permissions restrict access to local nginx."""

import os
from waitress import serve
from .wsgi import application

if __name__ == "__main__":
    os.umask(0o077)
    serve(
        application,
        unix_socket=os.environ.get("PREPPERDB_SOCKET", "/run/prepperdb/app.sock"),
        unix_socket_perms="660",
        threads=2,
        max_request_body_size=11 * 1024 * 1024,
        channel_timeout=30,
        connection_limit=32,
        expose_tracebacks=False,
    )
