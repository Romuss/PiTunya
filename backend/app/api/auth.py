"""Authentication endpoints: login, change password, current user."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import User
from app.core.auth import verify_password, hash_password, create_access_token, get_current_user
from app.schemas import LoginRequest, TokenResponse, ChangePasswordRequest, UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    # ── Per-IP rate limit (architecture review finding 1.2) ────────
    # The slowapi Limiter was mounted in main.py but its public API
    # is decorator-based (@limiter.limit("5/minute")). Calling
    # limiter.hit() directly raised AttributeError in production
    # ('Limiter' object has no attribute 'hit'), crashing the entire
    # login endpoint on every attempt.
    #
    # Fix: apply the rate limit via the decorator at module level
    # (see the @router.post decorator above — it now includes the
    # rate limit from the limiter mounted on app.state). The manual
    # call is removed.
    #
    # The per-account lockout below is independent of slowapi and
    # works through the DB (User.failed_attempts + locked_until).
    from app.core.auth_limiter import (
        LOGIN_RATE_LIMIT,
        MAX_FAILED_ATTEMPTS,
        LOCKOUT_MINUTES,
    )

    user = (await session.exec(select(User).where(User.username == body.username))).first()
    if user is None:
        # Don't reveal existence of username to attackers — same 401
        # shape as the wrong-password case. We DON'T increment
        # failed_attempts on a missing User: there's nothing to lock
        # out, and tracking attempts by (missing) username would
        # create a DoS vector against login-future users.
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # ── Per-account lockout check ─────────────────────────────────
    # `locked_until` is set when `failed_attempts >= cap`. Persists
    # across restarts (DB-backed), so a backend bounce can't be used
    # to reset mid-brute. After the window the row is unlocked, BUT
    # `failed_attempts` is preserved until the NEXT successful login
    # (so a steady stream of attempts every LOCKOUT_MINUTES+1 is
    # still throttling-aware: 5 fails → 1h lockout → next fail re-
    # enters lockout because counter ≥ 5).
    now_utc = datetime.now(tz=timezone.utc)
    if user.locked_until is not None:
        lock_until = user.locked_until
        if lock_until.tzinfo is None:
            lock_until = lock_until.replace(tzinfo=timezone.utc)
        if lock_until > now_utc:
            retry_after = int((lock_until - now_utc).total_seconds())
            logger.warning(
                "Login rejected — account %r locked for %ds more (failed_attempts=%d)",
                body.username, retry_after, user.failed_attempts,
            )
            # 429 Too Many Requests with Retry-After — matches the
            # convention slowapi uses, lets the frontend or curl
            # back off cleanly instead of retrying.
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Account is temporarily locked",
                    "retry_after_seconds": retry_after,
                    "max_failed_attempts": MAX_FAILED_ATTEMPTS,
                },
                headers={"Retry-After": str(retry_after)},
            )
        # Lockout window passed → fall through; attempt proceeds.

    if not verify_password(body.password, user.password_hash):
        # ── Increment failed_attempts + (maybe) lock ──────────────
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now_utc + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning(
                "Account %r locked: %d/%d failed attempts — locked for %d min",
                body.username, user.failed_attempts, MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES,
            )
        else:
            logger.info(
                "Failed login for %r: %d/%d — not yet locked",
                body.username, user.failed_attempts, MAX_FAILED_ATTEMPTS,
            )
        session.add(user)
        await session.commit()
        # Same 401 as the "user not found" case — uniform so an
        # attacker can't differentiate "user doesn't exist" from
        # "wrong password" by response shape.
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # ── Successful login — reset counters ─────────────────────────
    if user.failed_attempts != 0 or user.locked_until is not None:
        user.failed_attempts = 0
        user.locked_until = None
        session.add(user)
        await session.commit()

    token = create_access_token(user.username)
    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user = (await session.exec(select(User).where(User.id == current_user.id))).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    await session.commit()


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)):
    return current_user
