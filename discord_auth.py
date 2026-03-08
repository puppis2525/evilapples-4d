import os
import hmac
import hashlib
import json
import base64
import time
import secrets
import logging
import urllib.parse
import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

logger = logging.getLogger("discord_auth")

router = APIRouter()

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

if not SESSION_SECRET:
    logger.warning("SESSION_SECRET not set — generating random key (sessions won't persist across restarts)")
    SESSION_SECRET = secrets.token_hex(32)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"

COOKIE_NAME = "evil_session"
STATE_COOKIE = "oauth_state"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60

ALLOWED_NEXT_PATHS = {"/", "/store", "/decks", "/premium"}


def _get_redirect_uri(request: Request) -> str:
    host = request.headers.get("host", "")
    scheme = request.headers.get("x-forwarded-proto", "https")
    return f"{scheme}://{host}/auth/discord/callback"


def _sign_cookie(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def _verify_cookie(cookie: str) -> dict | None:
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def get_discord_user(request: Request) -> dict | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    return _verify_cookie(cookie)


@router.get("/auth/discord/login")
async def discord_login(request: Request):
    if not DISCORD_CLIENT_ID:
        return RedirectResponse(url="/?auth_error=config")

    state = secrets.token_urlsafe(32)
    redirect_uri = _get_redirect_uri(request)
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    url = f"{DISCORD_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    next_path = request.query_params.get("next", "/")
    if next_path not in ALLOWED_NEXT_PATHS:
        next_path = "/"

    response = RedirectResponse(url=url)
    response.set_cookie(
        STATE_COOKIE,
        _sign_cookie({"state": state, "next": next_path, "exp": int(time.time()) + 600}),
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", error: str = "", state: str = ""):
    if error or not code:
        logger.warning(f"Discord OAuth error: {error}")
        return RedirectResponse(url="/?auth_error=1")

    state_cookie = request.cookies.get(STATE_COOKIE)
    if not state_cookie:
        logger.warning("Missing OAuth state cookie")
        return RedirectResponse(url="/?auth_error=1")

    state_data = _verify_cookie(state_cookie)
    if not state_data or state_data.get("state") != state:
        logger.warning("OAuth state mismatch")
        return RedirectResponse(url="/?auth_error=1")

    next_path = state_data.get("next", "/")
    if next_path not in ALLOWED_NEXT_PATHS:
        next_path = "/"

    redirect_uri = _get_redirect_uri(request)

    try:
        async with aiohttp.ClientSession() as session:
            token_resp = await session.post(DISCORD_TOKEN_URL, data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})

            if token_resp.status != 200:
                body = await token_resp.text()
                logger.error(f"Discord token exchange failed: {token_resp.status} {body}")
                return RedirectResponse(url="/?auth_error=1")

            token_data = await token_resp.json()
            access_token = token_data["access_token"]

            user_resp = await session.get(f"{DISCORD_API_BASE}/users/@me", headers={
                "Authorization": f"Bearer {access_token}"
            })

            if user_resp.status != 200:
                logger.error(f"Discord user fetch failed: {user_resp.status}")
                return RedirectResponse(url="/?auth_error=1")

            user_data = await user_resp.json()

        discord_id = user_data["id"]
        username = user_data.get("username", "")
        global_name = user_data.get("global_name", username)
        avatar = user_data.get("avatar", "")

        avatar_url = ""
        if avatar:
            ext = "gif" if avatar.startswith("a_") else "png"
            avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar}.{ext}?size=64"

        cookie_data = {
            "discord_id": discord_id,
            "username": username,
            "global_name": global_name,
            "avatar_url": avatar_url,
            "exp": int(time.time()) + COOKIE_MAX_AGE,
        }

        response = RedirectResponse(url=next_path)
        response.set_cookie(
            COOKIE_NAME,
            _sign_cookie(cookie_data),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(STATE_COOKIE, path="/")

        logger.info(f"Discord login: {global_name} ({discord_id})")
        return response

    except Exception as e:
        logger.error(f"Discord OAuth error: {e}")
        return RedirectResponse(url="/?auth_error=1")


@router.get("/auth/discord/logout")
async def discord_logout():
    response = RedirectResponse(url="/")
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/api/auth/user")
async def api_auth_user(request: Request):
    user = get_discord_user(request)
    if user:
        return JSONResponse(content={
            "logged_in": True,
            "discord_id": user["discord_id"],
            "username": user.get("username", ""),
            "global_name": user.get("global_name", ""),
            "avatar_url": user.get("avatar_url", ""),
        })
    return JSONResponse(content={"logged_in": False})
