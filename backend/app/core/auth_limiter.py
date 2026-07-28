"""Login rate-limiter + lockout constants (architecture review finding 1.2).

The limiter itself is owned by FastAPI app (`app.main:app.state.limiter`)
and mounted via `slowapi` middleware. This module is the single source
of truth for the policy numbers so they can be tuned in one place.

Two tiers of defense:
  1. **Per-IP rate cap** (`slowapi`): no more than `LOGIN_RATE_LIMIT`
     POSTs to `/api/auth/login` per minute per source IP. Stops
     spray-and-pray before the bcrypt column even touches them.
  2. **Per-account lockout** (`User.failed_attempts` + `locked_until`):
     after `MAX_FAILED_ATTEMPTS` proven-wrong passwords, the row gets
     `locked_until = now + LOCKOUT_MINUTES`. Subsequent logins for
     that name return 429 + a Retry-After hint until the window
     expires. Survives backend restart (DB-persisted).

The two layers compose: rate-limit kicks in by IP (catches one
attacker hammering many usernames); lockout kicks in per username
(catches one username being reliably targeted from many IPs).
"""
from __future__ import annotations

# Per-IP rate limit on POST /api/auth/login.
# 5/min is ~3s between attempts — painless for a human typo-retry,
# crippling for a script. Tuned from architecture review 1.2.
LOGIN_RATE_LIMIT = "5/minute"

# Hard cap on consecutive failed attempts before the account is
# locked out for `LOCKOUT_MINUTES`. 5 traces the standard Unix/PAM
# default that operators already know — anything smaller false-
# positives too easily on legitimate typo-retry windows.
MAX_FAILED_ATTEMPTS = 5

# Account lockout window. 1 hour is long enough to make scripted
# brute force painfully slow (~5 attempts/hour after lock), short
# enough that an operator who fat-fingers their own password
# doesn't have to come back tomorrow.
LOCKOUT_MINUTES = 60
