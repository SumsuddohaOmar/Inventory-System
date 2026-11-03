"""Local accounts, server-side permissions and revocable opaque sessions.

Passwords use salted PBKDF2-HMAC-SHA256; only a token HASH is stored for sessions.
No default password exists. The first launch creates an administrator through
the setup form. Session permissions are read from the account on every request.
"""

import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

from app.database import connect
from app import audit

ITERATIONS = 600_000
PERMISSIONS = ("read", "products", "receive", "issue", "correct", "reports", "audit", "backups", "settings", "users")


class AccountError(ValueError):
    """A safe account error for display; never includes credentials."""


def password_hash(password: str) -> str:
    """Store algorithm, work factor, random salt and derived hash; never plaintext."""
    if not isinstance(password, str) or not 12 <= len(password) <= 200:
        raise AccountError("Use a password between 12 and 200 characters.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2-sha256${ITERATIONS}${salt}${digest}"


def verify(password: str, encoded: str) -> bool:
    """Compare in constant time after deriving with the saved work factor."""
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2-sha256": return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def public_user(row) -> dict:
    """Allowlist public fields so password hashes never enter API responses."""
    return dict(id=row["id"], username=row["username"], admin=bool(row["admin"]),
                active=bool(row["active"]), permissions=list(PERMISSIONS) if row["admin"] else json.loads(row["permissions_json"]))


def configured(path) -> bool:
    """An empty users table means first-time administrator setup is required."""
    with connect(path) as connection:
        return bool(connection.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def create_user(path, username, password, permissions=(), admin=False, first=False):
    """Create an account atomically; only one racing caller can claim first setup."""
    username = username.strip().casefold()
    if not 1 <= len(username) <= 80 or any(character.isspace() for character in username):
        raise AccountError("Use a username of 1–80 characters without spaces.")
    if not set(permissions).issubset(PERMISSIONS): raise AccountError("Unknown permission.")
    encoded = password_hash(password)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if first and connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise AccountError("Administrator setup is already complete. Sign in instead.")
        if connection.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AccountError("That username already exists.")
        identity = str(uuid4())
        connection.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, 1)",
                           (identity, username, encoded, json.dumps(list(permissions)), int(admin or first)))
        result = public_user(connection.execute("SELECT * FROM users WHERE id=?", (identity,)).fetchone())
        audit.record(connection, "account.created", identity, after=result)
        return result


def login(path, username, password, remember=False) -> tuple[str, dict]:
    """Authenticate with bounded retries; return an opaque token only on success.

    Five failures lock the username for five minutes, including nonexistent names.
    Commit failure counters before raising; otherwise a rollback defeats lockout.
    """
    username = username.strip().casefold()
    now = int(time.time())
    result = None
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempt = connection.execute("SELECT * FROM login_attempts WHERE username=?", (username,)).fetchone()
        if attempt and attempt["blocked_until"] > now: raise AccountError("Too many attempts. Try again in five minutes.")
        row = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        # Nonexistent users still perform the expensive derivation to reduce
        # username timing disclosure; this is not a substitute for lockout.
        dummy = f"pbkdf2-sha256${ITERATIONS}$" + "00" * 16 + "$" + "00" * 32
        valid = verify(password, row["password_hash"] if row else dummy)
        if row and row["active"] and valid:
            connection.execute("DELETE FROM login_attempts WHERE username=?", (username,))
            token = secrets.token_urlsafe(32)
            lifetime = 30 * 86400 if remember else 12 * 3600
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], now + lifetime))
            result = token, public_user(row)
        else:
            failures = (attempt["failures"] if attempt and attempt["blocked_until"] == 0 else 0) + 1
            connection.execute("INSERT INTO login_attempts VALUES (?, ?, ?) ON CONFLICT (username) DO UPDATE SET failures=excluded.failures, blocked_until=excluded.blocked_until",
                               (username, failures, now + 300 if failures >= 5 else 0))
    if result is None: raise AccountError("Invalid username or password.")
    return result


def session_user(path, token):
    """Resolve a live session and CURRENT permissions; disabled users lose access."""
    if not token: return None
    with connect(path) as connection:
        row = connection.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
        ).fetchone()
        return public_user(row) if row else None


def logout(path, token):
    """Invalidate the server token; deleting only the browser cookie is insufficient."""
    with connect(path) as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256((token or "").encode()).hexdigest(),))


def update_user(path, identity, permissions, active):
    """Change employee rights/active status, preserving at least one active admin."""
    if not set(permissions).issubset(PERMISSIONS): raise AccountError("Unknown permission.")
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM users WHERE id=?", (identity,)).fetchone()
        if not row: raise AccountError("User not found.")
        if row["admin"] and not active and connection.execute("SELECT COUNT(*) FROM users WHERE admin=1 AND active=1").fetchone()[0] <= 1:
            raise AccountError("The last active administrator cannot be disabled.")
        before = public_user(row)
        connection.execute("UPDATE users SET permissions_json=?,active=? WHERE id=?", (json.dumps(permissions), int(active), identity))
        after = public_user(connection.execute("SELECT * FROM users WHERE id=?", (identity,)).fetchone())
        audit.record(connection, "account.changed", identity, before, after)
        return after
