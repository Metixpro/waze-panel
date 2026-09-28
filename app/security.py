import bcrypt

# bcrypt has a hard 72-byte input limit; longer passwords are truncated
# rather than raising, same behavior users expect from every bcrypt-based
# auth system.
_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")[:_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = plain.encode("utf-8")[:_MAX_BYTES]
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        return False
