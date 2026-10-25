import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from fastapi import Header, HTTPException
from .config import settings

def validate_telegram_init_data(init_data: str, max_age_seconds: int = 3600) -> dict:
    if not settings.bot_token:
        raise HTTPException(503, "Telegram bot is not configured")
    if len(init_data) > 8192:
        raise HTTPException(401, "Invalid initData")
    try:
        parsed_pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
        )
    except ValueError as exc:
        raise HTTPException(401, "Invalid initData") from exc
    if len({key for key, _ in parsed_pairs}) != len(parsed_pairs):
        raise HTTPException(401, "Invalid initData")
    pairs = dict(parsed_pairs)
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Missing initData hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, "Invalid Telegram signature")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise HTTPException(401, "Invalid initData date") from exc
    now = time.time()
    if not auth_date or now - auth_date > max_age_seconds or auth_date > now + 60:
        raise HTTPException(401, "Expired initData")
    try: user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError: raise HTTPException(401, "Invalid user payload")
    if not user.get("id"): raise HTTPException(401, "Missing Telegram user")
    return user

async def require_admin(authorization: str | None = Header(default=None)):
    expected = "Bearer " + settings.admin_api_key
    if not settings.admin_api_key or "replace-with" in settings.admin_api_key or not hmac.compare_digest(authorization or "", expected):
        raise HTTPException(403, "Admin access denied")
    return True
