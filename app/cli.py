"""Command-line helper used by install.sh / uninstall.sh and for local
maintenance:

    python -m app.cli init-db
    python -m app.cli create-admin --username admin --password 'secret'
    python -m app.cli reset-password --username admin --password 'newsecret'
    python -m app.cli list-admins
    python -m app.cli backup --out /root
    python -m app.cli version
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


def cmd_delete_admin(args) -> None:
    db = SessionLocal()
    try:
        admin = db.query(AdminUser).filter(AdminUser.username == args.username).first()
        if not admin:
            print(f"Admin '{args.username}' not found.", file=sys.stderr)
            sys.exit(1)
        if db.query(AdminUser).count() <= 1:
            print("Refusing to delete the last admin.", file=sys.stderr)
            sys.exit(1)
        db.delete(admin)
        db.commit()
        print(f"Admin '{args.username}' deleted.")
    finally:
        db.close()


def cmd_backup(args) -> None:
    import os
    from pathlib import Path

    from app.backup import create_backup

    filename, data = create_backup()
    out = Path(args.out) if args.out else Path.cwd() / filename
    if out.is_dir():
        out = out / filename
    old_umask = os.umask(0o077)
    try:
        out.write_bytes(data)
    finally:
        os.umask(old_umask)
    print(out)


def cmd_list_admins(_args) -> None:
    db = SessionLocal()
    try:
        for admin in db.query(AdminUser).all():
            print(admin.username)
    finally:
        db.close()


def cmd_version(_args) -> None:
    from app.version import __version__, build_info

    build = build_info()
    extra = []
    if build["commit"]:
        extra.append(f"commit {build['commit'][:7]}")
    if build["date"]:
        extra.append(build["date"][:10])
    print(f"Waze Panel {__version__}" + (f" ({', '.join(extra)})" if extra else ""))


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

    p = sub.add_parser("delete-admin")
    p.add_argument("--username", required=True)
    p.set_defaults(func=cmd_delete_admin)

    p = sub.add_parser("backup")
    p.add_argument("--out", help="output file or directory (default: current directory)")
    p.set_defaults(func=cmd_backup)

    sub.add_parser("version").set_defaults(func=cmd_version)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
