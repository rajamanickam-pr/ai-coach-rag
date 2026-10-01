import hmac

from fastapi import Header, HTTPException, Request

from server.security import CSRF_COOKIE


def require_csrf(request: Request, x_csrf_token: str | None = Header(default=None)) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token or not x_csrf_token or not hmac.compare_digest(cookie_token, x_csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed. Refresh the page and try again.")