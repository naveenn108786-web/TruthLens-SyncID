"""Public-deployment hardening: security headers, per-client rate limits and an admin token.

Everything here is a no-op for local use unless the matching environment variable is set,
so `./run.sh` behaves exactly as before. The Docker/Render deployment sets them.

  TRUTHLENS_ADMIN_TOKEN   required (X-Admin-Token header) for administrative endpoints;
                          when TRUTHLENS_PUBLIC=1 and no token is set, they are disabled
  TRUTHLENS_PUBLIC=1      public mode: admin endpoints locked, rate limits on
  TRUTHLENS_RATE_LIMIT    POST requests per client IP per minute (default 20 in public mode)
"""
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

PUBLIC = os.environ.get("TRUTHLENS_PUBLIC") == "1"


def require_admin(x_admin_token: str | None = Header(None)):
    """FastAPI dependency for administrative endpoints."""
    token = os.environ.get("TRUTHLENS_ADMIN_TOKEN", "")
    if token:
        if not secrets.compare_digest(x_admin_token or "", token):
            raise HTTPException(403, "Administrative endpoint: a valid X-Admin-Token header is required")
    elif PUBLIC:
        raise HTTPException(403, "Administrative endpoint is disabled on the public deployment")


def client_ip(request):
    # Render terminates TLS and adds X-Forwarded-For; the left-most entry is the original client.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        default = "20" if PUBLIC else "0"
        self.limit = int(os.environ.get("TRUTHLENS_RATE_LIMIT", default) or 0)
        self.global_limit = self.limit * 10
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def _allowed(self, key, limit, now):
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    async def dispatch(self, request, call_next):
        if self.limit and request.method == "POST":
            now = time.monotonic()
            with self.lock:
                ok = self._allowed(client_ip(request), self.limit, now) and self._allowed("*", self.global_limit, now)
                if len(self.hits) > 5000:      # bound memory: forget idle clients
                    for k in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
                        del self.hits[k]
            if not ok:
                return JSONResponse({"detail": "Too many requests from this client. Please wait a minute and try again."},
                                    status_code=429, headers={"Retry-After": "60"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if PUBLIC:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
