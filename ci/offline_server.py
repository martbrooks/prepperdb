"""Test-only server: accept local browser requests, forbid all outbound connections."""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def deny_outbound(*args, **kwargs):
    raise AssertionError(
        "Application attempted an outbound network connection during offline acceptance."
    )


socket.socket.connect = deny_outbound
socket.socket.connect_ex = deny_outbound
socket.create_connection = deny_outbound

from django.core.management import execute_from_command_line

execute_from_command_line(["manage.py", *sys.argv[1:]])
