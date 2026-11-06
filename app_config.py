"""Host-agnostic settings read from the environment.

Secret values are never logged. Empty settings stay empty strings.
"""
import os
from urllib.parse import urlparse

_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE


def _valid_absolute_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def legacy_prefix_enabled() -> bool:
    """Guild ! commands. Also turns on the privileged message-content intent."""
    return _flag("ENABLE_LEGACY_PREFIX_COMMANDS", False)


def message_content_intent_enabled() -> bool:
    return legacy_prefix_enabled()


def members_intent_enabled() -> bool:
    """Off by default. Names come from the game state, cache, then one member fetch."""
    return _flag("ENABLE_MEMBERS_INTENT", False)


def mature_content_enforcement() -> str:
    """nsfw_channel (default) or off.

    off does not make the app eligible for the Discord App Directory.
    Distribution is an invite link. Discovery stays off either way.
    """
    raw = os.environ.get("MATURE_CONTENT_ENFORCEMENT", "nsfw_channel").strip().lower()
    if raw in ("off", "0", "false", "no"):
        return "off"
    return "nsfw_channel"


def mature_commands_marked_nsfw() -> bool:
    return mature_content_enforcement() == "nsfw_channel"


def channel_is_age_restricted(channel) -> bool:
    if channel is None:
        return False
    if bool(getattr(channel, "nsfw", False)):
        return True
    is_nsfw = getattr(channel, "is_nsfw", None)
    if callable(is_nsfw):
        try:
            return bool(is_nsfw())
        except Exception:
            return False
    return False


def mature_play_allowed(channel, *, source: str = "prefix") -> tuple[bool, str]:
    if mature_content_enforcement() == "off":
        return True, ""
    if channel_is_age_restricted(channel):
        return True, ""
    if source == "slash":
        return False, (
            "Evil Apples gameplay is mature: the card pool and AI judge include sexual, "
            "violent, and substance-related humor. Use this command in a channel marked "
            "Age-Restricted. Discord shows these commands there to members who are 18 or older."
        )
    return False, (
        "Evil Apples gameplay is mature: the card pool and AI judge include sexual, "
        "violent, and substance-related humor. Turn on Age-Restricted Channel for this "
        "channel, then try again. Slash commands are the supported way to play."
    )


def public_base_url(request=None) -> str:
    """Canonical public origin for redirects, ads, and upgrade links.

    PUBLIC_BASE_URL wins when it is set, including over the incoming Host header.
    Otherwise a request uses its own host. Background code falls back to
    REPLIT_DOMAINS, then WEB_BASE_URL, then the published site.
    """
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit and _valid_absolute_url(explicit):
        return explicit
    if request is not None:
        host = (request.headers.get("host") or "localhost:5000").strip()
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if forwarded in ("http", "https"):
            proto = forwarded
        elif host.startswith("localhost") or host.startswith("127.0.0.1"):
            proto = "http"
        else:
            proto = "https"
        return f"{proto}://{host}"
    domains = os.environ.get("REPLIT_DOMAINS", "").split(",")[0].strip()
    if domains:
        if domains.startswith("http://") or domains.startswith("https://"):
            return domains.rstrip("/")
        return f"https://{domains}"
    web = os.environ.get("WEB_BASE_URL", "").strip().rstrip("/")
    if web and _valid_absolute_url(web):
        return web
    return "https://evilapples4d.com"


def should_start_discord_bot() -> bool:
    if _flag("DISABLE_DISCORD_BOT", False):
        return False
    if not os.environ.get("DISCORD_TOKEN", "").strip():
        return False
    if os.environ.get("REPLIT_DEPLOYMENT") == "1":
        return True
    # Replit's dev workflow already runs a deployed bot. Keep that skip.
    if os.environ.get("REPLIT_DEV_DOMAIN", "").strip():
        return False
    return True


def discord_command_scope() -> str:
    scope = os.environ.get("DISCORD_COMMAND_SCOPE", "global").strip().lower()
    if scope not in ("global", "guild"):
        return "global"
    return scope


def discord_guild_id() -> str:
    return os.environ.get("DISCORD_GUILD_ID", "").strip()
