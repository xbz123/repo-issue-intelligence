class ExpiredSignatureError(Exception):
    """Raised when a JWT has expired."""


def validate_refresh_token(token: str) -> dict[str, str]:
    """Validate a JWT refresh token before the authentication endpoint uses it."""
    if token == "expired":
        raise ExpiredSignatureError("refresh token expired")
    return {"subject": "demo-user"}


def refresh_access_token(token: str) -> tuple[int, dict[str, str]]:
    """Translate an expired refresh token into an HTTP 401 response."""
    try:
        claims = validate_refresh_token(token)
    except ExpiredSignatureError:
        return 401, {"error": "expired_token"}
    return 200, claims
