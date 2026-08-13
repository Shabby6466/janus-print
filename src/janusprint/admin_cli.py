"""User management CLI.

Without this the only account that ever exists is the one `dev_mode` seeds — which means
turning dev_mode off (as production must) locks you out of your own console. Creating and
resetting accounts has to work without it.

    janus-print-admin list
    janus-print-admin create <username> [--role admin|approver|analyst]
    janus-print-admin passwd <username>
    janus-print-admin disable <username>

Passwords are read from the JANUS_PRINT_NEW_PASSWORD environment variable, or prompted for
interactively. They are never taken as an argument — argv is visible to every process on
the box and lands in shell history.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from .api.auth import ROLE_RANK, hash_password
from .db import init_db, session_scope
from .models import User


def _read_password(confirm: bool = True) -> str:
    supplied = os.environ.get("JANUS_PRINT_NEW_PASSWORD")
    if supplied:
        return supplied

    if not sys.stdin.isatty():
        raise SystemExit(
            "no password supplied: set JANUS_PRINT_NEW_PASSWORD or run interactively"
        )

    password = getpass.getpass("New password: ")
    if confirm and password != getpass.getpass("Confirm password: "):
        raise SystemExit("passwords do not match")
    if len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    return password


def cmd_list() -> int:
    with session_scope() as session:
        users = session.scalars(select(User).order_by(User.username)).all()
        if not users:
            print("no users; create one with: janus-print-admin create <username>")
            return 0
        print(f"{'USERNAME':<24} {'ROLE':<10} {'ACTIVE':<7} CREATED")
        for user in users:
            print(
                f"{user.username:<24} {user.role:<10} "
                f"{'yes' if user.active else 'no':<7} {user.created_at:%Y-%m-%d}"
            )
    return 0


def cmd_create(username: str, role: str) -> int:
    if role not in ROLE_RANK:
        raise SystemExit(f"role must be one of: {', '.join(ROLE_RANK)}")
    password = _read_password()
    with session_scope() as session:
        if session.scalar(select(User).where(User.username == username)):
            raise SystemExit(f"user {username!r} already exists; use passwd to reset it")
        session.add(
            User(
                username=username,
                display_name=username,
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"created {username} ({role})")
    return 0


def cmd_passwd(username: str) -> int:
    password = _read_password()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"no such user: {username}")
        user.password_hash = hash_password(password)
        user.active = True
    print(f"password reset for {username}")
    return 0


def cmd_disable(username: str) -> int:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"no such user: {username}")
        user.active = False
    print(f"disabled {username}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="janus-print-admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list console accounts")

    create = sub.add_parser("create", help="create a console account")
    create.add_argument("username")
    create.add_argument("--role", default="analyst", choices=sorted(ROLE_RANK))

    passwd = sub.add_parser("passwd", help="reset an account password")
    passwd.add_argument("username")

    disable = sub.add_parser("disable", help="deactivate an account")
    disable.add_argument("username")

    args = parser.parse_args()
    init_db()

    if args.command == "list":
        return cmd_list()
    if args.command == "create":
        return cmd_create(args.username, args.role)
    if args.command == "passwd":
        return cmd_passwd(args.username)
    if args.command == "disable":
        return cmd_disable(args.username)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
