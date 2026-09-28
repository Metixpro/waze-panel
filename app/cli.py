"""Command-line helper used by install.sh / uninstall.sh and for local
maintenance:

    python -m app.cli init-db
    python -m app.cli create-admin --username admin --password 'secret'
    python -m app.cli reset-password --username admin --password 'newsecret'
    python -m app.cli list-admins
"""
import argparse
import sys

from app.database import SessionLocal, init_db
from app.models import AdminUser
from app.security import hash_password


def cmd_init_db(_args) -> None:
    init_db()
    print("Database initialized.")


def cmd_create_admin(args) -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(AdminUser).filter(AdminUser.username == args.username).first()
        if existing:
            print(f"Admin '{args.username}' already exists.", file=sys.stderr)
            sys.exit(1)
        admin = AdminUser(username=args.username, password_hash=hash_password(args.password))
        db.add(admin)
        db.commit()
        print(f"Admin '{args.username}' created.")
    finally:
        db.close()


def cmd_reset_password(args) -> None:
    db = SessionLocal()
    try:
        admin = db.query(AdminUser).filter(AdminUser.username == args.username).first()
        if not admin:
            print(f"Admin '{args.username}' not found.", file=sys.stderr)
            sys.exit(1)
        admin.password_hash = hash_password(args.password)
        db.commit()
        print(f"Password updated for '{args.username}'.")
    finally:
        db.close()


def cmd_list_admins(_args) -> None:
    db = SessionLocal()
    try:
        for admin in db.query(AdminUser).all():
            print(admin.username)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="waze-panel-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    p = sub.add_parser("create-admin")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.set_defaults(func=cmd_reset_password)

    sub.add_parser("list-admins").set_defaults(func=cmd_list_admins)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
