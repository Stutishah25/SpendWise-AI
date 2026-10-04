"""Comprehensive authentication tests: registration, login, lockout, tokens, rotation, reuse, logout."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token, hash_password, hash_refresh_token
from app.db.dependencies import clear_session_rls_user, get_db
from app.main import app
from app.models import RefreshToken, User


@pytest.fixture()
def client(session):
    def override_get_db():
        try:
            yield session
        finally:
            clear_session_rls_user(session)

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# -----------------------------------------------------------------------------
# 1. Registration
# -----------------------------------------------------------------------------
def test_registration_success(client, session):
    res = client.post(
        "/auth/register",
        json={"email": "newuser@spendwise.test", "password": "SecurePassword123!"},
    )
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert data["email"] == "newuser@spendwise.test"
    assert data["is_active"] is True
    assert "password_hash" not in data
    assert "failed_login_count" not in data

    # Verify DB persistence and Argon2id hash
    user = session.scalar(select(User).where(User.email == "newuser@spendwise.test"))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != "SecurePassword123!"


def test_registration_duplicate_email(client):
    res1 = client.post(
        "/auth/register",
        json={"email": "duplicate@spendwise.test", "password": "Password123!"},
    )
    assert res1.status_code == 201

    # Exact duplicate
    res2 = client.post(
        "/auth/register",
        json={"email": "duplicate@spendwise.test", "password": "AnotherPassword123!"},
    )
    assert res2.status_code == 400
    assert "already registered" in res2.json()["detail"].lower()

    # Case-insensitive duplicate (Postgres CITEXT)
    res3 = client.post(
        "/auth/register",
        json={"email": "DUPLICATE@spendwise.test", "password": "Password123!"},
    )
    assert res3.status_code == 400


@pytest.mark.parametrize(
    "invalid_password",
    [
        "short",         # < 8 chars
        "1234567",       # < 8 chars
        "a" * 129,       # > 128 chars
        "onlyletters",   # no digits or special chars
        "12345678",      # no letters
    ],
)
def test_registration_invalid_password(client, invalid_password):
    res = client.post(
        "/auth/register",
        json={"email": "pwdtest@spendwise.test", "password": invalid_password},
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    "invalid_email",
    [
        "notanemail",
        "missingatsign.com",
        "@domain.com",
        "user@nodomain",
        "  ",
    ],
)
def test_registration_invalid_email(client, invalid_email):
    res = client.post(
        "/auth/register",
        json={"email": invalid_email, "password": "ValidPassword123!"},
    )
    assert res.status_code == 422


# -----------------------------------------------------------------------------
# 2. Login
# -----------------------------------------------------------------------------
def test_login_success(client, session):
    client.post(
        "/auth/register",
        json={"email": "login_ok@spendwise.test", "password": "MyLoginPassword123!"},
    )

    res = client.post(
        "/auth/login",
        json={"email": "login_ok@spendwise.test", "password": "MyLoginPassword123!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    # DB verification: last_login_at set, failed_login_count 0
    user = session.scalar(select(User).where(User.email == "login_ok@spendwise.test"))
    assert user.last_login_at is not None
    assert user.failed_login_count == 0

    # Refresh token DB check: raw token is NEVER stored
    raw_rt = data["refresh_token"]
    expected_hash = hash_refresh_token(raw_rt)
    rt_record = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == expected_hash))
    assert rt_record is not None
    assert rt_record.token_hash != raw_rt
    assert rt_record.revoked_at is None
    assert rt_record.expires_at > rt_record.issued_at


def test_login_wrong_password(client, session):
    client.post(
        "/auth/register",
        json={"email": "wrongpwd@spendwise.test", "password": "CorrectPassword123!"},
    )

    res = client.post(
        "/auth/login",
        json={"email": "wrongpwd@spendwise.test", "password": "IncorrectPassword123!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]

    # Non-existent email returns same 401 to prevent enumeration
    res_nonexistent = client.post(
        "/auth/login",
        json={"email": "nobody@spendwise.test", "password": "IncorrectPassword123!"},
    )
    assert res_nonexistent.status_code == 401
    assert "Invalid email or password" in res_nonexistent.json()["detail"]


def test_login_failed_login_counting(client, session):
    client.post(
        "/auth/register",
        json={"email": "counting@spendwise.test", "password": "CorrectPassword123!"},
    )

    for i in range(1, 4):
        res = client.post(
            "/auth/login",
            json={"email": "counting@spendwise.test", "password": f"WrongAttempt{i}!"},
        )
        assert res.status_code == 401

    user = session.scalar(select(User).where(User.email == "counting@spendwise.test"))
    assert user.failed_login_count == 3
    assert user.locked_until is None

    # Successful login resets the counter
    ok_res = client.post(
        "/auth/login",
        json={"email": "counting@spendwise.test", "password": "CorrectPassword123!"},
    )
    assert ok_res.status_code == 200
    session.refresh(user)
    assert user.failed_login_count == 0


def test_login_account_lockout(client, session):
    client.post(
        "/auth/register",
        json={"email": "lockout@spendwise.test", "password": "CorrectPassword123!"},
    )

    # Trigger threshold failures (settings.MAX_FAILED_LOGIN_ATTEMPTS = 5)
    for _ in range(settings.MAX_FAILED_LOGIN_ATTEMPTS):
        res = client.post(
            "/auth/login",
            json={"email": "lockout@spendwise.test", "password": "WrongPassword123!"},
        )
        assert res.status_code == 401

    user = session.scalar(select(User).where(User.email == "lockout@spendwise.test"))
    assert user.failed_login_count == settings.MAX_FAILED_LOGIN_ATTEMPTS
    assert user.locked_until is not None
    assert user.locked_until > dt.datetime.now(dt.timezone.utc)

    # Next attempt (even with right password) is rejected with 403 Forbidden
    res_locked = client.post(
        "/auth/login",
        json={"email": "lockout@spendwise.test", "password": "CorrectPassword123!"},
    )
    assert res_locked.status_code == 403
    assert "temporarily locked" in res_locked.json()["detail"].lower()

    # Simulate lockout period expiration
    user.locked_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    session.commit()

    # Now login succeeds and resets lockout
    res_unlocked = client.post(
        "/auth/login",
        json={"email": "lockout@spendwise.test", "password": "CorrectPassword123!"},
    )
    assert res_unlocked.status_code == 200
    session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


# -----------------------------------------------------------------------------
# 3. Access Token & /auth/me
# -----------------------------------------------------------------------------
def test_access_token_authentication_and_auth_me(client):
    reg = client.post(
        "/auth/register",
        json={"email": "me@spendwise.test", "password": "Password123!"},
    )
    assert reg.status_code == 201

    login = client.post(
        "/auth/login",
        json={"email": "me@spendwise.test", "password": "Password123!"},
    )
    token = login.json()["access_token"]

    # Access /auth/me with valid Bearer token
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "me@spendwise.test"
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data

    # Verify sensitive fields are NOT exposed
    assert "password_hash" not in data
    assert "token_hash" not in data
    assert "failed_login_count" not in data
    assert "locked_until" not in data


def test_access_token_rejected_when_missing_or_invalid(client):
    # No auth header
    res1 = client.get("/auth/me")
    assert res1.status_code == 401

    # Malformed token
    res2 = client.get("/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt"})
    assert res2.status_code == 401

    # Token signed with a different key
    wrong_key_token = create_access_token(uuid.uuid4())
    # Tamper with the token
    tampered = wrong_key_token[:-5] + "XXXXX"
    res3 = client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert res3.status_code == 401


def test_access_token_expired(client, session):
    # Issue a token that expired 10 minutes ago
    expired_token = create_access_token(uuid.uuid4(), expires_delta=dt.timedelta(minutes=-10))
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 4. Refresh Token Rotation & Reuse Detection
# -----------------------------------------------------------------------------
def test_refresh_token_rotation(client, session):
    client.post(
        "/auth/register",
        json={"email": "rotation@spendwise.test", "password": "Password123!"},
    )
    login = client.post(
        "/auth/login",
        json={"email": "rotation@spendwise.test", "password": "Password123!"},
    )
    rt1 = login.json()["refresh_token"]
    h1 = hash_refresh_token(rt1)

    # Perform first rotation
    refresh_res = client.post("/auth/refresh", json={"refresh_token": rt1})
    assert refresh_res.status_code == 200
    data = refresh_res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    rt2 = data["refresh_token"]
    assert rt2 != rt1
    h2 = hash_refresh_token(rt2)

    # Verify DB state of rotated token
    token1_rec = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == h1))
    token2_rec = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == h2))

    assert token1_rec.revoked_at is not None
    assert token1_rec.last_used_at is not None
    assert token1_rec.replaced_by_id == token2_rec.id

    assert token2_rec.revoked_at is None
    assert token2_rec.family_id == token1_rec.family_id

    # New token can be used to rotate again
    refresh_res2 = client.post("/auth/refresh", json={"refresh_token": rt2})
    assert refresh_res2.status_code == 200
    rt3 = refresh_res2.json()["refresh_token"]
    assert rt3 != rt2


def test_refresh_token_reuse_detection(client, session):
    client.post(
        "/auth/register",
        json={"email": "reuse@spendwise.test", "password": "Password123!"},
    )
    login = client.post(
        "/auth/login",
        json={"email": "reuse@spendwise.test", "password": "Password123!"},
    )
    rt1 = login.json()["refresh_token"]

    # Rotate rt1 -> rt2
    rot1 = client.post("/auth/refresh", json={"refresh_token": rt1})
    assert rot1.status_code == 200
    rt2 = rot1.json()["refresh_token"]

    # Attacker or malicious client tries to reuse already-revoked rt1!
    reuse_attempt = client.post("/auth/refresh", json={"refresh_token": rt1})
    assert reuse_attempt.status_code == 401
    assert "reuse detected" in reuse_attempt.json()["detail"].lower()

    # Entire family must now be revoked! Legitimate rt2 should now also fail.
    rt2_attempt = client.post("/auth/refresh", json={"refresh_token": rt2})
    assert rt2_attempt.status_code == 401

    # Verify in DB that all tokens in this family are revoked
    h2 = hash_refresh_token(rt2)
    token2_rec = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == h2))
    assert token2_rec.revoked_at is not None


# -----------------------------------------------------------------------------
# 5. Logout
# -----------------------------------------------------------------------------
def test_logout(client, session):
    client.post(
        "/auth/register",
        json={"email": "logout@spendwise.test", "password": "Password123!"},
    )
    login = client.post(
        "/auth/login",
        json={"email": "logout@spendwise.test", "password": "Password123!"},
    )
    rt = login.json()["refresh_token"]
    h = hash_refresh_token(rt)

    # Logout with refresh token
    res = client.post("/auth/logout", json={"refresh_token": rt})
    assert res.status_code == 200
    assert "logged out" in res.json()["message"].lower()

    # DB check: token revoked
    rec = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == h))
    assert rec.revoked_at is not None

    # Refresh with logged-out token fails
    ref = client.post("/auth/refresh", json={"refresh_token": rt})
    assert ref.status_code == 401

    # Repeating logout is safe (idempotent)
    res_repeat = client.post("/auth/logout", json={"refresh_token": rt})
    assert res_repeat.status_code == 200

    # Logout without body is also safe
    res_empty = client.post("/auth/logout")
    assert res_empty.status_code == 200


# -----------------------------------------------------------------------------
# 6. Inactive / Deleted User Rejection
# -----------------------------------------------------------------------------
def test_inactive_and_deleted_user_rejected(client, session):
    client.post(
        "/auth/register",
        json={"email": "status_check@spendwise.test", "password": "Password123!"},
    )
    user = session.scalar(select(User).where(User.email == "status_check@spendwise.test"))

    login = client.post(
        "/auth/login",
        json={"email": "status_check@spendwise.test", "password": "Password123!"},
    )
    access_token = login.json()["access_token"]
    refresh_token = login.json()["refresh_token"]

    # Deactivate user
    user.is_active = False
    session.commit()

    # Login rejected
    assert client.post(
        "/auth/login",
        json={"email": "status_check@spendwise.test", "password": "Password123!"},
    ).status_code == 401

    # /auth/me rejected
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"}).status_code == 401

    # Refresh rejected
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401

    # Reactivate user, then soft-delete
    user.is_active = True
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    session.commit()

    # Login rejected
    assert client.post(
        "/auth/login",
        json={"email": "status_check@spendwise.test", "password": "Password123!"},
    ).status_code == 401

    # /auth/me rejected
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"}).status_code == 401

    # Refresh rejected
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401


# -----------------------------------------------------------------------------
# 7. Seed Accounts Dev Login
# -----------------------------------------------------------------------------
def test_seed_account_development_login(client, session):
    from scripts.seed_dev import PLACEHOLDER_HASH, set_seed_user_password

    # Create a user with the seed placeholder hash
    seed_email = "seeded_demo@spendwise.test"
    u = User(email=seed_email, password_hash=PLACEHOLDER_HASH, is_active=True)
    session.add(u)
    session.commit()

    # Seed accounts cannot log in by default (placeholder is non-verifiable)
    res_placeholder = client.post(
        "/auth/login",
        json={"email": seed_email, "password": "AnyPassword123!"},
    )
    assert res_placeholder.status_code == 401

    # Dev-only mechanism sets an Argon2id password
    ok = set_seed_user_password(session, seed_email, "DevSecret123!")
    assert ok is True
    session.commit()

    # Now login succeeds with the newly set dev password
    res_dev = client.post(
        "/auth/login",
        json={"email": seed_email, "password": "DevSecret123!"},
    )
    assert res_dev.status_code == 200
    assert "access_token" in res_dev.json()
