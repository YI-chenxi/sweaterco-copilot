"""Native HTTP endpoints: same-origin, POST-only, no session tokens in JSON."""

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.routing import Route

from app.accounts.session.browser_transport import COOKIE_NAME, TransportError

ENDPOINT = "sweaterco-auth/session"
_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class BrowserSettings:
    origin: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str):
            raise ValueError("ACCOUNT_PUBLIC_URL is required")
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("ACCOUNT_PUBLIC_URL must be the HTTPS application origin")
        return cls(f"{parsed.scheme}://{parsed.netloc}")


def browser_routes(runtime_provider):
    async def session(request):
        try:
            runtime = runtime_provider()
        except Exception:
            return JSONResponse({"error": "configuration"}, status_code=503, headers=_HEADERS)
        if (
            request.headers.get("origin") != runtime.settings.origin
            or request.headers.get("sec-fetch-site") not in {None, "same-origin"}
            or request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
        ):
            return JSONResponse({"error": "request_rejected"}, status_code=403, headers=_HEADERS)
        try:
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > 2048:
                    raise ValueError()
            payload = json.loads(raw)
            if type(payload) is not dict or len(payload) != 1:
                raise ValueError()
            value = next(iter(payload.values()))
            if type(value) is not str or len(value) > 128:
                raise ValueError()
            handle = request.cookies.get(COOKIE_NAME)
            if set(payload) == {"command"}:
                verification, cookie = runtime.handoffs.exchange(payload["command"], handle)
                response = JSONResponse({"verification": verification}, headers=_HEADERS)
                if cookie is not None:
                    response.set_cookie(
                        key=cookie.name,
                        value=cookie.value,
                        max_age=cookie.max_age,
                        path=cookie.path,
                        secure=True,
                        httponly=True,
                        samesite="lax",
                    )
                return response
            if set(payload) == {"verification"}:
                receipt = runtime.handoffs.verify(payload["verification"], handle)
                return JSONResponse({"receipt": receipt}, headers=_HEADERS)
            raise ValueError()
        except (ValueError, TransportError):
            return JSONResponse(
                {"error": "browser_session_failed"}, status_code=400, headers=_HEADERS
            )

    return [Route("/" + ENDPOINT, session, methods=["POST"])]
