"""Permission checks shared by prefix commands and slash dispatch."""


def has_permission(guild_permissions, name: str) -> bool:
    if guild_permissions is None:
        return False
    return bool(getattr(guild_permissions, name, False))


def can_end_game(author_id, host_id, guild_permissions) -> bool:
    if host_id is not None and str(author_id) == str(host_id):
        return True
    return has_permission(guild_permissions, "manage_messages")


def can_end_tournament(author_id, creator_id, guild_permissions) -> bool:
    if creator_id is not None and str(author_id) == str(creator_id):
        return True
    return has_permission(guild_permissions, "manage_guild")


def can_start_or_link_tournament(author_id, creator_id) -> bool:
    return creator_id is not None and str(author_id) == str(creator_id)
