"""
utils/vesqor_authdb.py — VESQOR shared auth database client for Open WebUI.

The `auth` database on the Fly Postgres cluster is the single source of truth
for VESQOR user credentials (bcrypt $2b$12$ hashes, compatible with passlib).
Open WebUI's local `user` table is a projection; sign-in/sign-up validate
against authdb first, then mirror the row locally.

Connection string comes from AUTH_DATABASE_URL (set as a Fly secret on
vesqor-chat). Falls back to DATABASE_URL when unset (dev convenience).
"""

import logging
import os
import time
import uuid

import bcrypt
import psycopg

log = logging.getLogger(__name__)

AUTH_DATABASE_URL = os.getenv("AUTH_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _connect():
    if not AUTH_DATABASE_URL:
        raise RuntimeError("AUTH_DATABASE_URL is not set")
    return psycopg.connect(AUTH_DATABASE_URL, connect_timeout=5)


def vesqor_authdb_get_user(email: str) -> dict | None:
    """Return the authdb user row (or None)."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, name, email_verified, role "
                    "FROM users WHERE email = %s",
                    (email.lower(),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "email": row[1],
                    "password_hash": row[2],
                    "name": row[3],
                    "email_verified": row[4],
                    "role": row[5],
                }
    except Exception as e:
        log.error("vesqor_authdb_get_user failed: %s", e)
        return None


def vesqor_authdb_verify(email: str, password: str) -> dict | None:
    """Verify credentials against authdb. Returns the user row or None."""
    user = vesqor_authdb_get_user(email)
    if not user:
        return None
    try:
        password_bytes = password.encode("utf-8")[:72]
        if not bcrypt.checkpw(password_bytes, user["password_hash"].encode("utf-8")):
            return None
    except Exception as e:
        log.error("vesqor_authdb_verify bcrypt failed: %s", e)
        return None
    return user


def vesqor_authdb_create_user(email: str, password: str, name: str) -> dict:
    """Create a user in authdb. Returns the created row."""
    user_id = str(uuid.uuid4())
    now = int(time.time())
    password_hash = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode("utf-8")
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, email, password_hash, name, email_verified, role, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, FALSE, 'pending', %s, %s) "
                    "ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at "
                    "RETURNING id, email, name, email_verified, role",
                    (user_id, email.lower(), password_hash, name, now, now),
                )
                row = cur.fetchone()
                conn.commit()
                return {
                    "id": row[0],
                    "email": row[1],
                    "name": row[2],
                    "email_verified": row[3],
                    "role": row[4],
                }
    except Exception as e:
        log.error("vesqor_authdb_create_user failed: %s", e)
        raise


def vesqor_authdb_mark_verified(email: str) -> bool:
    """Mark a user verified in authdb. Returns True on success."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET email_verified = TRUE, role = 'user', updated_at = %s WHERE email = %s",
                    (int(time.time()), email.lower()),
                )
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        log.error("vesqor_authdb_mark_verified failed: %s", e)
        return False
