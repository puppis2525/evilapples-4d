"""Slash commands for the existing gameplay and admin flows.

Each callback defers, then calls the same function the ! command uses.
Prefix commands stay registered and run only when the legacy fallback is on.
"""
import logging

import discord
from discord import app_commands

from app_config import (
    discord_command_scope,
    discord_guild_id,
    mature_commands_marked_nsfw,
    mature_play_allowed,
)

logger = logging.getLogger("slash_commands")

# Names exercised by tests. Keep this list aligned with register_slash_commands.
EXPECTED_SLASH_COMMANDS = (
    "help",
    "startgame",
    "join",
    "deal",
    "endgame",
    "decks",
    "timer",
    "premium",
    "solo start",
    "solo deal",
    "solo score",
    "solo end",
    "tournament create",
    "tournament join",
    "tournament start",
    "tournament status",
    "tournament end",
    "tournament link",
    "tournament teams",
    "deck upload",
    "deck list",
    "deck use",
    "deck ai",
    "deck info",
    "deck delete",
)

MATURE_SKIP = {"help", "premium"}
MANAGE_GUILD_COMMANDS = {
    "tournament create",
    "tournament start",
    "tournament end",
    "tournament link",
}


class SlashContext:
    """Enough of a prefix context for the existing command functions."""

    def __init__(self, interaction: discord.Interaction, attachments=None):
        self.interaction = interaction
        self.author = interaction.user
        self.channel = interaction.channel
        self.guild = interaction.guild
        self.invoked_subcommand = None
        self.message = type("SlashMessage", (), {})()
        self.message.attachments = list(attachments or [])
        self.message.content = ""
        self.message.author = interaction.user

    async def send(self, content=None, **kwargs):
        kwargs.pop("ephemeral", None)
        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(content, **kwargs)
            return
        await self.interaction.followup.send(content, **kwargs)


def _gamebot():
    import bot as gamebot
    return gamebot


async def _invoke(interaction, handler, *args, mature=True, attachments=None, **kwargs):
    if mature:
        ok, message = mature_play_allowed(interaction.channel, source="slash")
        if not ok:
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            return
    if not interaction.response.is_done():
        await interaction.response.defer()
    ctx = SlashContext(interaction, attachments)
    try:
        await handler(ctx, *args, **kwargs)
    except Exception:
        logger.exception("Slash command failed: %s", getattr(handler, "__name__", handler))
        try:
            await interaction.followup.send("Something went wrong. Please try again.")
        except Exception:
            logger.exception("Slash error followup failed")


def register_slash_commands(target_bot) -> None:
    if getattr(target_bot, "_ea_slash_ready", False):
        return
    nsfw = mature_commands_marked_nsfw()
    tree = target_bot.tree

    @tree.command(name="help", description="Show Evil Apples commands.")
    @app_commands.describe(command="Command name, such as startgame or tournament")
    async def slash_help(interaction: discord.Interaction, command: str = ""):
        await _invoke(interaction, _gamebot().help_cmd, mature=False, command_name=command)

    @tree.command(name="startgame", description="Start a multiplayer game in this channel.", nsfw=nsfw)
    @app_commands.guild_only()
    @app_commands.describe(mode="friend, swap, or wildcard")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Friend", value="friend"),
        app_commands.Choice(name="Swap", value="swap"),
        app_commands.Choice(name="Wildcard", value="wildcard"),
    ])
    async def slash_startgame(interaction: discord.Interaction, mode: str = "friend"):
        await _invoke(interaction, _gamebot().start_game, mode)

    @tree.command(name="join", description="Join the game in this channel.", nsfw=nsfw)
    @app_commands.guild_only()
    async def slash_join(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().join_game)

    @tree.command(name="deal", description="Deal the next round.", nsfw=nsfw)
    @app_commands.guild_only()
    async def slash_deal(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().deal_cards)

    @tree.command(name="endgame", description="End the game in this channel.", nsfw=nsfw)
    @app_commands.guild_only()
    async def slash_endgame(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().end_game)

    @tree.command(name="decks", description="Show or pick decks for this channel.", nsfw=nsfw)
    @app_commands.guild_only()
    async def slash_decks(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().show_decks)

    @tree.command(name="timer", description="Show or set the round timer.", nsfw=nsfw)
    @app_commands.guild_only()
    @app_commands.describe(seconds="10 to 60. Omit to show the current timer.", off="Turn the timer off")
    async def slash_timer(
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 10, 60] | None = None,
        off: bool = False,
    ):
        if off:
            await _invoke(interaction, _gamebot().set_timer_alias, "off")
        else:
            await _invoke(interaction, _gamebot().set_timer, seconds)

    @tree.command(name="premium", description="Show this server's plan and upgrade link.")
    @app_commands.guild_only()
    async def slash_premium(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().premium_cmd, mature=False)

    solo = app_commands.Group(
        name="solo",
        description="Play a solo game against AI opponents.",
        nsfw=nsfw,
        guild_only=True,
    )

    @solo.command(name="start", description="Start a solo game.")
    @app_commands.guild_only()
    @app_commands.describe(opponents="How many AI opponents, from 1 to 5")
    async def solo_start(interaction: discord.Interaction, opponents: app_commands.Range[int, 1, 5] = 3):
        await _invoke(interaction, _gamebot().solo_group, opponents)

    @solo.command(name="deal", description="Deal a solo round.")
    @app_commands.guild_only()
    async def solo_deal(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().solo_deal)

    @solo.command(name="score", description="Show solo scores.")
    @app_commands.guild_only()
    async def solo_score(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().solo_score)

    @solo.command(name="end", description="End your solo game.")
    @app_commands.guild_only()
    async def solo_end(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().solo_end)

    tree.add_command(solo)

    tournament = app_commands.Group(
        name="tournament",
        description="Run a multi-round tournament in this channel.",
        nsfw=nsfw,
        guild_only=True,
    )

    @tournament.command(name="create", description="Create a tournament in this channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Tournament name",
        rounds="Number of rounds, from 2 to 20",
        entry_fee_dollars="Entry fee in dollars. Use 0 for a free tournament.",
    )
    async def tournament_create_cmd(
        interaction: discord.Interaction,
        name: str,
        rounds: app_commands.Range[int, 2, 20] = 5,
        entry_fee_dollars: float = 0.0,
    ):
        if entry_fee_dollars < 0:
            await interaction.response.send_message("Entry fee cannot be negative.", ephemeral=True)
            return
        args = f"{name} {int(rounds)}"
        if entry_fee_dollars > 0:
            args += f" ${entry_fee_dollars:.2f}"
        await _invoke(interaction, _gamebot().tournament_create, args=args)

    @tournament.command(name="join", description="Join the open tournament in this channel.")
    @app_commands.guild_only()
    async def tournament_join_cmd(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().tournament_join)

    @tournament.command(name="start", description="Start the tournament. Creator only.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def tournament_start_cmd(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().tournament_start)

    @tournament.command(name="status", description="Show tournament standings.")
    @app_commands.guild_only()
    async def tournament_status_cmd(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().tournament_status)

    @tournament.command(name="end", description="End or cancel the tournament.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def tournament_end_slash(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().tournament_end_cmd)

    @tournament.command(name="link", description="Link a web tournament to this channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(tournament_id="Id from the website tournament page")
    async def tournament_link_cmd(interaction: discord.Interaction, tournament_id: int):
        await _invoke(interaction, _gamebot().tournament_link, tournament_id)

    @tournament.command(name="teams", description="Show team standings.")
    @app_commands.guild_only()
    async def tournament_teams_slash(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().tournament_teams_cmd)

    tree.add_command(tournament)

    deck = app_commands.Group(
        name="deck",
        description="Manage custom decks for this server.",
        nsfw=nsfw,
        guild_only=True,
    )

    @deck.command(name="upload", description="Upload a CSV deck you own.")
    @app_commands.guild_only()
    @app_commands.describe(name="Deck name", file="CSV with type,text columns")
    async def deck_upload_cmd(interaction: discord.Interaction, name: str, file: discord.Attachment):
        await _invoke(interaction, _gamebot().deck_upload, attachments=[file], name=name)

    @deck.command(name="list", description="List your custom decks.")
    @app_commands.guild_only()
    async def deck_list_cmd(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().deck_list)

    @deck.command(name="use", description="Use one of your decks in this channel.")
    @app_commands.guild_only()
    @app_commands.describe(name="Deck name")
    async def deck_use_cmd(interaction: discord.Interaction, name: str):
        await _invoke(interaction, _gamebot().deck_use, name=name)

    @deck.command(name="ai", description="Switch back to the default cards.")
    @app_commands.guild_only()
    async def deck_ai_cmd(interaction: discord.Interaction):
        await _invoke(interaction, _gamebot().deck_ai)

    @deck.command(name="info", description="Show one of your decks.")
    @app_commands.guild_only()
    @app_commands.describe(name="Deck name")
    async def deck_info_cmd(interaction: discord.Interaction, name: str):
        await _invoke(interaction, _gamebot().deck_info, name=name)

    @deck.command(name="delete", description="Delete one of your decks.")
    @app_commands.guild_only()
    @app_commands.describe(name="Deck name")
    async def deck_delete_cmd(interaction: discord.Interaction, name: str):
        await _invoke(interaction, _gamebot().deck_delete, name=name)

    tree.add_command(deck)
    target_bot._ea_slash_ready = True
    logger.info(
        "Registered slash commands (nsfw gameplay=%s, legacy prefix=%s)",
        nsfw,
        __import__("app_config").legacy_prefix_enabled(),
    )


def describe_slash_commands(target_bot) -> list[dict]:
    rows = []
    for cmd in target_bot.tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            for child in cmd.commands:
                rows.append({
                    "name": f"{cmd.name} {child.name}",
                    "nsfw": bool(child.nsfw or cmd.nsfw),
                    "guild_only": bool(cmd.guild_only or child.guild_only),
                    "default_permissions": child.default_permissions,
                })
        else:
            rows.append({
                "name": cmd.name,
                "nsfw": bool(cmd.nsfw),
                "guild_only": bool(cmd.guild_only),
                "default_permissions": cmd.default_permissions,
            })
    rows.sort(key=lambda row: row["name"])
    return rows


async def sync_slash_commands(target_bot):
    scope = discord_command_scope()
    guild_id = discord_guild_id()
    if scope == "guild":
        if not guild_id.isdigit():
            logger.error("DISCORD_COMMAND_SCOPE=guild requires a numeric DISCORD_GUILD_ID. Slash sync skipped.")
            return []
        guild = discord.Object(id=int(guild_id))
        target_bot.tree.copy_global_to(guild=guild)
        synced = await target_bot.tree.sync(guild=guild)
        logger.info("Synced %s slash commands to guild %s", len(synced), guild_id)
        return synced
    synced = await target_bot.tree.sync()
    logger.info("Synced %s global slash commands", len(synced))
    return synced
