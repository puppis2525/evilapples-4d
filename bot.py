import os
import random
import discord
from discord.ext import commands
import asyncio
from api_client import generate_hand, generate_prompt
from ui_components import JudgingView
from db import (
    batch_update_games_played,
    create_tournament, get_active_tournament, join_tournament,
    get_tournament_players, get_tournament_player_count,
    start_tournament, end_tournament,
    is_premium_server, get_premium_server
)
from tournament import (
    active_tournament_rounds, run_tournament_round,
    TournamentJudgingView
)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_games = {}


class GameSession:
    def __init__(self, channel_id, judge_id):
        self.data = {
            "channel_id": channel_id,
            "judge_id": str(judge_id),
            "state": "waiting_for_players",
            "players": {},
            "hands": {},
            "submissions": {},
            "prompt": "",
        }

    def to_dict(self):
        return self.data


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")


@bot.command(name="startgame")
async def start_game(ctx):
    if ctx.channel.id in active_games:
        await ctx.send("A game is already running in this channel! Use `!endgame` to stop it.")
        return

    session = GameSession(ctx.channel.id, ctx.author.id)
    active_games[ctx.channel.id] = session.to_dict()

    embed = discord.Embed(
        title="Evil Apples 4D",
        description="A new game is starting! Type `!join` to play.\nThe game starts when the Judge types `!deal`.",
        color=discord.Color.purple()
    )
    embed.add_field(name="Judge", value=ctx.author.mention, inline=False)
    embed.add_field(name="Commands", value=(
        "`!join` - Join the game\n"
        "`!deal` - Deal cards and start the round (Judge only)\n"
        "`!endgame` - End the current game"
    ), inline=False)

    await ctx.send(embed=embed)


@bot.command(name="join")
async def join_game(ctx):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game. Use `!startgame` to start one.")
        return

    if str(ctx.author.id) == game["judge_id"]:
        await ctx.send("The Judge can't play cards! You'll pick the winner instead.")
        return

    player_id = str(ctx.author.id)
    if player_id in game["players"]:
        await ctx.send("You're already in the game!")
        return

    game["players"][player_id] = ctx.author.display_name
    await ctx.send(f"**{ctx.author.display_name}** has joined! ({len(game['players'])} players)")


@bot.command(name="deal")
async def deal_cards(ctx):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game.")
        return

    if str(ctx.author.id) != game["judge_id"]:
        await ctx.send("Only the Judge can deal cards!")
        return

    if len(game["players"]) < 2:
        await ctx.send("Need at least 2 players to start! Have players type `!join`.")
        return

    if game["state"] not in ("waiting_for_players", "finished"):
        await ctx.send("A round is already in progress!")
        return

    if ctx.guild:
        premium = await is_premium_server(str(ctx.guild.id))
        if not premium:
            key = get_daily_key(str(ctx.guild.id))
            count = daily_game_counts.get(key, 0)
            if count >= FREE_DAILY_GAME_LIMIT:
                host = os.environ.get("REPLIT_DOMAINS", "evilapples4d.com").split(",")[0]
                await ctx.send(
                    f"**Free tier limit reached!** ({FREE_DAILY_GAME_LIMIT} games/day)\n"
                    f"Upgrade to Pro for unlimited games: https://{host}/premium\n"
                    f"Or use `!premium` for details."
                )
                return
            daily_game_counts[key] = count + 1

    game["state"] = "dealing"
    game["submissions"] = {}

    await ctx.send("Generating evil cards and dealing hands... This may take a moment.")

    try:
        prompt = await generate_prompt()
        game["prompt"] = prompt

        player_ids = list(game["players"].keys())
        hands = await asyncio.gather(*(generate_hand(5) for _ in player_ids))
        for player_id, hand in zip(player_ids, hands):
            game["hands"][player_id] = hand

        for player_id in player_ids:
            hand = game["hands"][player_id]
            player_name = game["players"][player_id]

            try:
                member = ctx.guild.get_member(int(player_id))
                if member:
                    hand_text = "\n".join(f"**{i+1}.** {card}" for i, card in enumerate(hand))
                    dm_embed = discord.Embed(
                        title="Your Hand",
                        description=f"**Prompt:** {prompt}\n\n{hand_text}\n\nReply with the **number** of the card you want to play.",
                        color=discord.Color.blue()
                    )
                    await member.send(embed=dm_embed)
            except discord.Forbidden:
                await ctx.send(f"Couldn't DM {player_name}. They need to enable DMs from server members.")

        game["state"] = "submitting"

        embed = discord.Embed(
            title="Round Started!",
            description=f"**Prompt:** {prompt}\n\nCheck your DMs for your cards! Reply to the bot's DM with a number (1-5) to submit.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Waiting for {len(game['players'])} submissions...")
        await ctx.send(embed=embed)

    except Exception as e:
        game["state"] = "waiting_for_players"
        await ctx.send(f"Error generating cards: {str(e)}")


@bot.command(name="endgame")
async def end_game(ctx):
    if ctx.channel.id in active_games:
        del active_games[ctx.channel.id]
        await ctx.send("Game ended. Start a new one with `!startgame`.")
    else:
        await ctx.send("No active game to end.")


@bot.group(name="tournament", invoke_without_command=True)
async def tournament_group(ctx):
    embed = discord.Embed(
        title="Evil Apples 4D — Tournaments",
        description="Run a multi-round tournament with standings and a champion!",
        color=discord.Color.purple()
    )
    embed.add_field(name="Commands", value=(
        "`!tournament create <name> [rounds]` — Create a tournament (default 5 rounds)\n"
        "`!tournament join` — Join the open tournament\n"
        "`!tournament start` — Start the tournament (creator only)\n"
        "`!tournament status` — View current standings\n"
        "`!tournament end` — End the tournament early"
    ), inline=False)
    await ctx.send(embed=embed)


@tournament_group.command(name="create")
async def tournament_create(ctx, *, args: str = ""):
    existing = await get_active_tournament(str(ctx.channel.id))
    if existing:
        await ctx.send("There's already an active tournament in this channel! Use `!tournament end` first.")
        return

    parts = args.rsplit(" ", 1)
    rounds_total = 5
    name = args.strip() or f"{ctx.author.display_name}'s Tournament"

    if len(parts) > 1 and parts[-1].isdigit():
        rounds_total = max(2, min(20, int(parts[-1])))
        name = parts[0].strip() or f"{ctx.author.display_name}'s Tournament"

    tournament_id = await create_tournament(
        name=name,
        guild_id=str(ctx.guild.id),
        channel_id=str(ctx.channel.id),
        creator_id=str(ctx.author.id),
        creator_name=ctx.author.display_name,
        rounds_total=rounds_total
    )

    embed = discord.Embed(
        title=f"Tournament Created: {name}",
        description=(
            f"**Rounds:** {rounds_total}\n"
            f"**Created by:** {ctx.author.mention}\n\n"
            f"Type `!tournament join` to enter!\n"
            f"The creator starts it with `!tournament start` when ready."
        ),
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"Tournament ID: {tournament_id} | Need at least 3 players")
    await ctx.send(embed=embed)


@tournament_group.command(name="join")
async def tournament_join(ctx):
    tournament = await get_active_tournament(str(ctx.channel.id))
    if not tournament:
        await ctx.send("No active tournament. Create one with `!tournament create <name>`.")
        return

    if tournament["status"] != "registration":
        await ctx.send("This tournament has already started! Wait for the next one.")
        return

    player_count = await get_tournament_player_count(tournament["id"])
    if player_count >= tournament["max_players"]:
        await ctx.send(f"Tournament is full ({tournament['max_players']} players max)!")
        return

    await join_tournament(tournament["id"], str(ctx.author.id), ctx.author.display_name)
    new_count = await get_tournament_player_count(tournament["id"])

    await ctx.send(
        f"**{ctx.author.display_name}** joined **{tournament['name']}**! "
        f"({new_count} player{'s' if new_count != 1 else ''} registered)"
    )


@tournament_group.command(name="start")
async def tournament_start(ctx):
    tournament = await get_active_tournament(str(ctx.channel.id))
    if not tournament:
        await ctx.send("No active tournament in this channel.")
        return

    if str(ctx.author.id) != tournament["creator_id"]:
        await ctx.send("Only the tournament creator can start it!")
        return

    if tournament["status"] != "registration":
        await ctx.send("This tournament has already started!")
        return

    players = await get_tournament_players(tournament["id"])
    if len(players) < 3:
        await ctx.send(f"Need at least 3 players to start! Currently have {len(players)}. Use `!tournament join`.")
        return

    await start_tournament(tournament["id"])

    player_list = "\n".join(f"**{i+1}.** {p['discord_name']}" for i, p in enumerate(players))
    embed = discord.Embed(
        title=f"Tournament Starting: {tournament['name']}!",
        description=(
            f"**{len(players)} players** competing over **{tournament['rounds_total']} rounds**!\n\n"
            f"**Players:**\n{player_list}\n\n"
            f"The judge rotates each round. First round begins now!"
        ),
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

    player_dict = {p["discord_id"]: p["discord_name"] for p in players}
    judge_rotation = list(player_dict.keys())
    random.shuffle(judge_rotation)

    await asyncio.sleep(3)
    await run_tournament_round(ctx.channel, tournament, 1, player_dict, judge_rotation, bot)


@tournament_group.command(name="status")
async def tournament_status(ctx):
    tournament = await get_active_tournament(str(ctx.channel.id))
    if not tournament:
        await ctx.send("No active tournament in this channel.")
        return

    players = await get_tournament_players(tournament["id"])

    if not players:
        await ctx.send(f"**{tournament['name']}** — No players registered yet. Use `!tournament join`.")
        return

    standings = "\n".join(
        f"**{i+1}.** {p['discord_name']} — {p['points']} pts ({p['rounds_won']} wins)"
        for i, p in enumerate(players)
    )

    status_label = {
        "registration": "Registration Open",
        "active": f"Round {tournament['current_round']}/{tournament['rounds_total']}",
        "completed": "Completed"
    }.get(tournament["status"], tournament["status"])

    embed = discord.Embed(
        title=f"Tournament: {tournament['name']}",
        description=f"**Status:** {status_label}\n**Players:** {len(players)}\n\n**Standings:**\n{standings}",
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed)


@tournament_group.command(name="end")
async def tournament_end_cmd(ctx):
    tournament = await get_active_tournament(str(ctx.channel.id))
    if not tournament:
        await ctx.send("No active tournament in this channel.")
        return

    if str(ctx.author.id) != tournament["creator_id"]:
        await ctx.send("Only the tournament creator can end it!")
        return

    await end_tournament(tournament["id"])

    players = await get_tournament_players(tournament["id"])
    if players and players[0]["points"] > 0:
        champion = players[0]
        embed = discord.Embed(
            title=f"Tournament Ended: {tournament['name']}",
            description=f"**Champion:** {champion['discord_name']} with {champion['points']} points!",
            color=discord.Color.gold()
        )
    else:
        embed = discord.Embed(
            title=f"Tournament Ended: {tournament['name']}",
            description="The tournament has been cancelled.",
            color=discord.Color.red()
        )

    await ctx.send(embed=embed)

    if ctx.channel.id in active_tournament_rounds:
        del active_tournament_rounds[ctx.channel.id]


FREE_DAILY_GAME_LIMIT = 3
daily_game_counts = {}


def get_daily_key(guild_id):
    from datetime import date
    return f"{guild_id}:{date.today().isoformat()}"


@bot.command(name="premium")
async def premium_cmd(ctx):
    if not ctx.guild:
        await ctx.send("This command can only be used in a server.")
        return

    premium = await get_premium_server(str(ctx.guild.id))

    if premium and premium.get("active"):
        embed = discord.Embed(
            title="Evil Apples 4D Pro — Active",
            description=(
                f"This server has **Pro** status!\n\n"
                f"**Plan:** {premium['plan'].title()}\n"
                f"**Activated by:** {premium['activated_by_name']}\n\n"
                "**Pro Features:**\n"
                "- Unlimited games per day\n"
                "- Unlimited tournaments\n"
                "- Priority AI card generation\n"
                "- Premium card packs (coming soon)"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Thank you for supporting Evil Apples 4D!")
        await ctx.send(embed=embed)
    else:
        host = os.environ.get("REPLIT_DOMAINS", "evilapples4d.com").split(",")[0]
        upgrade_url = f"https://{host}/premium"

        embed = discord.Embed(
            title="Evil Apples 4D Pro",
            description=(
                "Upgrade your server to **Pro** for unlimited evil fun!\n\n"
                "**Free Tier:**\n"
                f"- {FREE_DAILY_GAME_LIMIT} games per day\n"
                "- 1 tournament at a time\n"
                "- Standard AI cards\n\n"
                "**Pro — $5.99/month:**\n"
                "- Unlimited games per day\n"
                "- Unlimited tournaments\n"
                "- Priority AI card generation\n"
                "- Premium card packs (coming soon)\n"
                "- Support the developer!\n\n"
                f"**[Upgrade Now]({upgrade_url})**"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="Use the link above to upgrade your server")
        await ctx.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    if not isinstance(message.channel, discord.DMChannel):
        return

    if not message.content.strip().isdigit():
        return

    choice = int(message.content.strip())
    player_id = str(message.author.id)

    for channel_id, game in active_games.items():
        if game["state"] != "submitting":
            continue
        if player_id not in game["players"]:
            continue
        if player_id in game["submissions"]:
            await message.channel.send("You already submitted a card this round!")
            return

        hand = game["hands"].get(player_id, [])
        if choice < 1 or choice > len(hand):
            await message.channel.send(f"Pick a number between 1 and {len(hand)}.")
            return

        selected_card = hand[choice - 1]
        game["submissions"][player_id] = selected_card

        await message.channel.send(f"Card submitted: **{selected_card}**\nCheck the main channel for results!")

        if len(game["submissions"]) == len(game["players"]):
            players_in_round = {
                pid: game["players"][pid] for pid in game["players"]
            }
            await batch_update_games_played(players_in_round)

            game["state"] = "judging"
            channel = bot.get_channel(channel_id)
            if channel:
                submission_text = "\n".join(
                    f"**Card {i+1}:** {card}"
                    for i, (_, card) in enumerate(
                        list(game["submissions"].items())
                    )
                )

                embed = discord.Embed(
                    title="All Cards Are In! Time to Judge!",
                    description=f"**Prompt:** {game['prompt']}\n\n{submission_text}",
                    color=discord.Color.purple()
                )

                view = JudgingView(game, channel)
                await channel.send(embed=embed, view=view)
        return

    for channel_id, tdata in active_tournament_rounds.items():
        round_data = tdata["round"]
        tournament_data = tdata["tournament"]

        if player_id not in round_data["players"]:
            continue
        if player_id in round_data["submissions"]:
            await message.channel.send("You already submitted a card this round!")
            return

        hand = round_data["hands"].get(player_id, [])
        if not hand:
            continue
        if choice < 1 or choice > len(hand):
            await message.channel.send(f"Pick a number between 1 and {len(hand)}.")
            return

        selected_card = hand[choice - 1]
        round_data["submissions"][player_id] = selected_card

        await message.channel.send(f"Card submitted: **{selected_card}**\nCheck the tournament channel for results!")

        if len(round_data["submissions"]) == len(round_data["players"]):
            channel = bot.get_channel(channel_id)
            if channel:
                submissions = list(round_data["submissions"].items())
                random.shuffle(submissions)

                submission_text = "\n".join(
                    f"**Card {i+1}:** {card}"
                    for i, (_, card) in enumerate(submissions)
                )

                embed = discord.Embed(
                    title=f"Round {round_data['round_number']} — All Cards In!",
                    description=f"**Prompt:** {round_data['prompt']}\n\n{submission_text}",
                    color=discord.Color.purple()
                )
                embed.set_footer(
                    text=f"Judge: pick the winner! | Tournament: {tournament_data['name']}"
                )

                view = TournamentJudgingView(tournament_data, round_data, channel)
                await channel.send(embed=embed, view=view)
        return


def run_bot():
    bot.run(DISCORD_TOKEN)
