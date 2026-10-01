import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Callable

from fastapi import Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.database import get_db
from server.models import LoginSession, Role, User

logger = logging.getLogger(__name__)
password_hasher = PasswordHash.recommended()
SESSION_COOKIE = "ai_coach_session"
CSRF_COOKIE = "ai_coach_csrf"


def session_ttl() -> timedelta:
    return timedelta(hours=max(1, int(os.getenv("SESSION_TTL_HOURS", "12"))))


def cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hasher.verify(password, encoded_hash)
    except Exception:
        logger.exception("Password verification failed")
        return False


def issue_login_session(db: Session, user: User) -> tuple[str, str, LoginSession]:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(48)
    session = LoginSession(
        token_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=now + session_ttl(),
        last_seen_at=now,
    )
    db.add(session)
    db.commit()
    return token, secrets.token_urlsafe(32), session


def set_auth_cookies(response: Response, token: str, csrf_token: str) -> None:
    max_age = int(session_ttl().total_seconds())
    secure = cookie_secure()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=cookie_secure(), httponly=False, samesite="strict")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    now = datetime.now(UTC)
    login_session = db.scalar(
        select(LoginSession)
        .where(
            LoginSession.token_hash == hash_session_token(token),
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > now,
        )
        .options(joinedload(LoginSession.user).joinedload(User.role))
    )
    if not login_session or not login_session.user.is_active:
        raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.")

    login_session.last_seen_at = now
    db.commit()
    request.state.login_session = login_session
    return login_session.user


def require_permission(permission: str) -> Callable:
    def permission_check(user: User = Depends(get_current_user)) -> User:
        if permission not in user.role.permissions:
            raise HTTPException(status_code=403, detail="Your account does not have permission for this action.")
        return user

    return permission_check


def seed_roles_and_bootstrap_admin(db: Session) -> None:
    role_permissions = {
        "admin": [
            "documents:read",
            "documents:write",
            "documents:delete",
            "collections:read",
            "users:manage",
            "conversations:read",
            "conversations:write",
            "conversations:delete",
        ],
        "member": ["documents:read", "collections:read", "conversations:read", "conversations:write", "conversations:delete"],
    }
    roles = {role.name: role for role in db.scalars(select(Role)).all()}
    for role_name, permissions in role_permissions.items():
        if role_name not in roles:
            db.add(Role(name=role_name, permissions=permissions))
    db.commit()

    is_development = os.getenv("APP_ENV", "production").strip().lower() == "development"
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "raja" if is_development else "").strip().lower()
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "raja@gmail.com" if is_development else "").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "12354" if is_development else "")
    if not username or not email or not password:
        logger.warning(
            "Bootstrap admin is not configured; set BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_EMAIL, and BOOTSTRAP_ADMIN_PASSWORD"
        )
        return
    valid_username = username.replace("_", "").replace("-", "").replace(".", "").isalnum()
    if not valid_username or len(username) < 3 or len(username) > 64:
        raise RuntimeError("BOOTSTRAP_ADMIN_USERNAME must be 3-64 letters, digits, dots, underscores, or hyphens.")
    minimum_password_length = 5 if is_development else 12
    if len(password) < minimum_password_length:
        if is_development:
            raise RuntimeError("Development bootstrap passwords must contain at least 5 characters.")
        raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters.")
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    existing = db.scalar(select(User).where((User.email == email) | (User.username == username)))
    if not existing:
        db.add(User(username=username, email=email, password_hash=hash_password(password), role_id=admin_role.id))
        db.commit()
        logger.info("Bootstrap administrator created", extra={"username": username})
    elif existing.username != username or existing.email != email:
        raise RuntimeError("Bootstrap admin username/email conflicts with an existing account.")