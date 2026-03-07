import os
import discord
from discord.ext import commands
import asyncio
from api_client import generate_hand, generate_prompt
from ui_components import JudgingView
from db import batch_update_games_played

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


def run_bot():
    bot.run(DISCORD_TOKEN)
