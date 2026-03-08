import os
import random
import time
import discord
from discord.ext import commands
import asyncio
from slugify import slugify
from api_client import generate_hand, generate_prompt, ai_judge, deal_default_hands, deal_default_solo
from ui_components import JudgingView, HandView, GameLobbyView, PostRoundView, SoloControlsView
from db import (
    batch_update_games_played, save_round,
    create_tournament, get_active_tournament, join_tournament,
    get_tournament_players, get_tournament_player_count,
    start_tournament, end_tournament,
    is_premium_server, get_premium_server,
    get_all_decks, get_deck_by_name, get_deck_by_id,
    get_random_cards, create_deck, add_cards_to_deck
)
from tournament import (
    active_tournament_rounds, run_tournament_round,
    TournamentJudgingView
)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

def format_prompt(prompt_text):
    formatted = prompt_text.replace("_", "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    return f"**{formatted}**"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_games = {}
channel_timers = {}
timer_tasks = {}
solo_games = {}
solo_locks = {}

AI_OPPONENT_NAMES = [
    "DevilBot", "SatanAI", "DarkLord", "EvilGPT", "ChaosMachine",
    "SnarkBot", "VileUnit", "GrimReaper", "ToxicAI", "WickedBot",
    "HellSpawn", "DoomBot", "MaliceAI", "VenomBot", "ShadowAI",
]

round_counter = {}


class GameSession:
    def __init__(self, channel_id, starter_id, starter_name):
        self.data = {
            "channel_id": channel_id,
            "host_id": str(starter_id),
            "judge_id": None,
            "state": "waiting_for_players",
            "players": {},
            "all_player_ids": [],
            "judge_index": -1,
            "use_ai_judge": False,
            "hands": {},
            "submissions": {},
            "prompt": "",
            "round_id": 0,
        }
        self.data["players"][str(starter_id)] = starter_name
        self.data["all_player_ids"].append(str(starter_id))

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

    session = GameSession(ctx.channel.id, ctx.author.id, ctx.author.display_name)
    active_games[ctx.channel.id] = session.to_dict()

    embed = discord.Embed(
        title="Evil Apples 4D",
        description=f"A new game is starting! Type `!join` to play.\n**{ctx.author.display_name}** has joined! (1 player)\n\nAnyone can type `!deal` when ready.",
        color=discord.Color.purple()
    )
    embed.add_field(name="How It Works", value=(
        "The judge rotates each round (round-robin style).\n"
        "If fewer than 3 players, Dr. Evil judges instead."
    ), inline=False)
    await ctx.send(embed=embed, view=GameLobbyView())


@bot.command(name="join")
async def join_game(ctx):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game. Use `!startgame` to start one.")
        return

    player_id = str(ctx.author.id)
    if player_id in game["players"]:
        await ctx.send("You're already in the game!")
        return

    game["players"][player_id] = ctx.author.display_name
    if player_id not in game["all_player_ids"]:
        game["all_player_ids"].append(player_id)
    await ctx.send(f"**{ctx.author.display_name}** has joined! ({len(game['players'])} players)")


@bot.command(name="deal")
async def deal_cards(ctx):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game.")
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

    if ctx.channel.id in timer_tasks:
        timer_tasks[ctx.channel.id].cancel()
        timer_tasks.pop(ctx.channel.id, None)

    total_players = len(game["players"])
    use_ai_judge = total_players < 3

    if use_ai_judge:
        game["judge_id"] = None
        game["use_ai_judge"] = True
        playing_ids = list(game["players"].keys())
        judge_name = "Dr. Evil"
    else:
        game["judge_index"] = (game.get("judge_index", -1) + 1) % len(game["all_player_ids"])
        judge_id = game["all_player_ids"][game["judge_index"]]
        while judge_id not in game["players"]:
            game["judge_index"] = (game["judge_index"] + 1) % len(game["all_player_ids"])
            judge_id = game["all_player_ids"][game["judge_index"]]
        game["judge_id"] = judge_id
        game["use_ai_judge"] = False
        playing_ids = [pid for pid in game["players"] if pid != judge_id]
        judge_name = game["players"][judge_id]

    game["state"] = "dealing"
    game["submissions"] = {}
    game["round_id"] = game.get("round_id", 0) + 1
    current_round_id = game["round_id"]

    deck_info = channel_decks.get(ctx.channel.id)
    if deck_info:
        await ctx.send(f"Drawing cards from **{deck_info['name']}**...")
    else:
        await ctx.send("Generating evil cards and dealing hands... This may take a moment.")

    try:
        if deck_info:
            prompts = await get_random_cards(deck_info["id"], "prompt", 1)
            if not prompts:
                await ctx.send("Deck has no prompt cards! Falling back to AI generation.")
                prompt = await generate_prompt()
            else:
                prompt = prompts[0]
        else:
            prompt = await generate_prompt()
        game["prompt"] = prompt

        if deck_info:
            total_needed = len(playing_ids) * 5
            all_answers = await get_random_cards(deck_info["id"], "answer", total_needed)
            if len(all_answers) < total_needed:
                await ctx.send(f"Not enough answer cards in deck (need {total_needed}, have {len(all_answers)}). Using default cards.")
                hands = deal_default_hands(len(playing_ids), 5)
            else:
                hands = [all_answers[i*5:(i+1)*5] for i in range(len(playing_ids))]
        else:
            hands = deal_default_hands(len(playing_ids), 5)
        for player_id, hand in zip(playing_ids, hands):
            game["hands"][player_id] = hand

        game["state"] = "submitting"
        game["playing_ids"] = playing_ids

        timer_seconds = channel_timers.get(ctx.channel.id)
        timer_text = ""
        if timer_seconds:
            deadline = int(time.time()) + timer_seconds
            timer_text = f"\n\n**Timer:** <t:{deadline}:R> to submit!"

        judge_line = f"\n\n\u2696\uFE0F **Judge this round:** {judge_name}" if not use_ai_judge else "\n\n\U0001F608 **Dr. Evil** is judging this round"

        embed = discord.Embed(
            title="\U0001F7E3 PROMPT CARD",
            description=f"## {format_prompt(prompt)}{judge_line}\n\nClick the button below to view your hand and submit a card!{timer_text}",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        embed.set_footer(text=f"Waiting for {len(playing_ids)} submissions...")
        view = HandView(game, mode="multiplayer", channel=ctx.channel)
        await ctx.send(embed=embed, view=view)

        if timer_seconds:
            async def timer_countdown(ch_id, secs, rid):
                try:
                    await asyncio.sleep(secs)
                    await handle_timer_expiry(ch_id, rid)
                except asyncio.CancelledError:
                    pass
                finally:
                    timer_tasks.pop(ch_id, None)

            timer_tasks[ctx.channel.id] = asyncio.create_task(
                timer_countdown(ctx.channel.id, timer_seconds, current_round_id)
            )

    except Exception as e:
        game["state"] = "waiting_for_players"
        await ctx.send(f"Error generating cards: {str(e)}")


async def ai_judge_pick(game, channel):
    prompt = game["prompt"]
    submissions = list(game["submissions"].items())

    if not submissions:
        game["state"] = "finished"
        embed = discord.Embed(
            title="Round Void",
            description="No cards were submitted. Type `!deal` for a new round.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return

    random.shuffle(submissions)

    card_texts = [card for _, card in submissions]
    chosen_index = await ai_judge(prompt, card_texts)

    winner_id, winning_card = submissions[chosen_index]
    winner_member = channel.guild.get_member(int(winner_id)) if channel.guild else None
    winner_name = winner_member.display_name if winner_member else game["players"].get(winner_id, "Unknown")

    slug = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")
    await save_round(prompt, winning_card, winner_id, winner_name, slug)

    game["state"] = "finished"

    submission_text = "\n".join(
        f"\U0001F0CF **Card {i+1}:** {card}" for i, card in enumerate(card_texts)
    )

    embed = discord.Embed(
        title="\U0001F608 Dr. Evil Has Decided!",
        description=f"## {format_prompt(prompt)}\n\n{submission_text}",
        color=discord.Color.from_rgb(106, 27, 154)
    )
    embed.add_field(name="\U0001F3C6 Winning Card", value=winning_card, inline=False)
    embed.add_field(name="Winner", value=f"**{winner_name}**", inline=False)
    embed.set_footer(text=f"Round saved as: /rounds/{slug}")

    await channel.send(embed=embed, view=PostRoundView())


async def check_all_submitted(game, interaction):
    channel_id = None
    for ch_id, g in active_games.items():
        if g is game:
            channel_id = ch_id
            break
    if channel_id is None:
        return

    playing_ids = game.get("playing_ids", [pid for pid in game["players"] if pid != game.get("judge_id")])
    if len(game["submissions"]) < len(playing_ids):
        return

    if channel_id in timer_tasks:
        timer_tasks[channel_id].cancel()
        del timer_tasks[channel_id]

    players_in_round = {
        pid: game["players"][pid] for pid in game["players"]
    }
    await batch_update_games_played(players_in_round)

    channel = bot.get_channel(channel_id)

    if game.get("use_ai_judge"):
        game["state"] = "judging"
        if channel:
            await ai_judge_pick(game, channel)
        return

    game["state"] = "judging"
    if channel:
        submissions = list(game["submissions"].items())
        random.shuffle(submissions)
        submission_text = "\n".join(
            f"\U0001F0CF **Card {i+1}:** {card}"
            for i, (_, card) in enumerate(submissions)
        )

        embed = discord.Embed(
            title="\u2696\uFE0F Time to Judge!",
            description=f"## {format_prompt(game['prompt'])}\n\n{submission_text}\n\n*Judge: pick the winning card!*",
            color=discord.Color.from_rgb(106, 27, 154)
        )

        view = JudgingView(game, channel)
        await channel.send(embed=embed, view=view)


@bot.command(name="endgame")
async def end_game(ctx):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game to end.")
        return
    if str(ctx.author.id) != game.get("host_id"):
        await ctx.send("Only the game host can end the game!")
        return
    if ctx.channel.id in timer_tasks:
        timer_tasks[ctx.channel.id].cancel()
        del timer_tasks[ctx.channel.id]
    del active_games[ctx.channel.id]
    await ctx.send("Game ended. Start a new one with `!startgame`.")


@bot.command(name="timer")
async def set_timer(ctx, seconds: int = None):
    game = active_games.get(ctx.channel.id)
    if not game:
        await ctx.send("No active game. Use `!startgame` first.")
        return

    if seconds is None:
        current = channel_timers.get(ctx.channel.id)
        if current:
            await ctx.send(f"Current timer: **{current} seconds** per round. Use `!timer off` to disable.")
        else:
            await ctx.send("No timer set. Use `!timer <10-60>` to set one, e.g. `!timer 30`.")
        return

    if seconds < 10 or seconds > 60:
        await ctx.send("Timer must be between **10** and **60** seconds.")
        return

    channel_timers[ctx.channel.id] = seconds
    await ctx.send(f"Timer set to **{seconds} seconds** per round. Players who don't submit in time will be skipped.")

@set_timer.error
async def timer_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        arg = ctx.message.content.split()[-1].strip().lower()
        if arg in ("off", "none", "disable", "0"):
            channel_timers.pop(ctx.channel.id, None)
            await ctx.send("Timer disabled. Players have unlimited time to submit.")
        else:
            await ctx.send("Usage: `!timer <10-60>` to set or `!timer off` to disable.")
    else:
        raise error

@bot.command(name="settimer")
async def set_timer_alias(ctx, seconds: str = None):
    if seconds and seconds.lower() in ("off", "none", "disable", "0"):
        channel_timers.pop(ctx.channel.id, None)
        await ctx.send("Timer disabled. Players have unlimited time to submit.")
        return
    try:
        val = int(seconds) if seconds else None
    except ValueError:
        await ctx.send("Usage: `!timer <10-60>` to set or `!timer off` to disable.")
        return
    await set_timer(ctx, val)


async def handle_timer_expiry(channel_id, expected_round_id):
    game = active_games.get(channel_id)
    if not game or game["state"] != "submitting":
        return
    if game.get("round_id", 0) != expected_round_id:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    if game["state"] != "submitting" or game.get("round_id", 0) != expected_round_id:
        return

    playing_ids = set(game.get("playing_ids", [pid for pid in game["players"] if pid != game.get("judge_id")]))
    submitted = set(game["submissions"].keys())
    missing = playing_ids - submitted

    if not game["submissions"]:
        game["state"] = "finished"
        embed = discord.Embed(
            title="Time's Up!",
            description="Nobody submitted a card in time. The round is void.\nType `!deal` to start a new round.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return

    game["state"] = "judging"

    missing_names = [game["players"][pid] for pid in missing if pid in game["players"]]
    if missing_names:
        await channel.send(f"**Time's up!** {', '.join(missing_names)} didn't submit in time and will be skipped this round.")

    players_in_round = {
        pid: game["players"][pid] for pid in game["players"]
    }
    await batch_update_games_played(players_in_round)

    if game.get("use_ai_judge"):
        await ai_judge_pick(game, channel)
        return

    submission_text = "\n".join(
        f"\U0001F0CF **Card {i+1}:** {card}"
        for i, (_, card) in enumerate(list(game["submissions"].items()))
    )

    embed = discord.Embed(
        title="\u2696\uFE0F Time's Up — Time to Judge!",
        description=f"## {format_prompt(game['prompt'])}\n\n{submission_text}\n\n*Judge: pick the winning card!*",
        color=discord.Color.from_rgb(106, 27, 154)
    )

    view = JudgingView(game, channel)
    await channel.send(embed=embed, view=view)


@bot.group(name="solo", invoke_without_command=True)
async def solo_group(ctx, opponents: int = 3):
    if ctx.invoked_subcommand is not None:
        return

    player_id = str(ctx.author.id)

    if player_id in solo_games:
        await ctx.send("You already have a solo game running! Use `!solo end` to stop it first.")
        return

    if opponents < 1:
        opponents = 1
    if opponents > 5:
        opponents = 5

    ai_names = random.sample(AI_OPPONENT_NAMES, opponents)

    solo_games[player_id] = {
        "channel_id": ctx.channel.id,
        "player_id": player_id,
        "player_name": ctx.author.display_name,
        "ai_opponents": ai_names,
        "scores": {ctx.author.display_name: 0},
        "round_number": 0,
        "state": "idle",
        "round_id": 0,
    }
    for name in ai_names:
        solo_games[player_id]["scores"][name] = 0

    embed = discord.Embed(
        title="Solo Mode — Evil Apples 4D",
        description=f"**{ctx.author.display_name}** vs. {', '.join(f'**{n}**' for n in ai_names)}\n\nType `!solo deal` to start a round!\n\U0001F608 Dr. Evil will judge each round.",
        color=discord.Color.dark_purple()
    )
    await ctx.send(embed=embed, view=SoloControlsView(player_id))


@solo_group.command(name="deal")
async def solo_deal(ctx):
    player_id = str(ctx.author.id)
    game = solo_games.get(player_id)
    if not game:
        await ctx.send("No solo game running. Start one with `!solo <opponents>`.")
        return

    if player_id not in solo_locks:
        solo_locks[player_id] = asyncio.Lock()

    if solo_locks[player_id].locked():
        await ctx.send("Already dealing! Please wait for the current round.")
        return

    async with solo_locks[player_id]:
        if game["state"] in ("submitting", "dealing", "judging"):
            await ctx.send("A round is already in progress! Check your DMs if you need to submit a card.")
            return

        game["state"] = "dealing"
        game["round_number"] += 1
        game["round_id"] = game.get("round_id", 0) + 1
        current_round_id = game["round_id"]

        await ctx.send(f"**Round {game['round_number']}** — Generating cards...")

        try:
            deck_info = channel_decks.get(ctx.channel.id)
            if deck_info:
                prompts = await get_random_cards(deck_info["id"], "prompt", 1)
                if prompts:
                    prompt = prompts[0]
                else:
                    prompt = await generate_prompt()

                total_needed = 5 + len(game["ai_opponents"])
                all_answers = await get_random_cards(deck_info["id"], "answer", total_needed)
                if len(all_answers) >= total_needed:
                    player_hand = all_answers[:5]
                    ai_cards = all_answers[5:]
                else:
                    player_hand, ai_cards = deal_default_solo(5, len(game["ai_opponents"]))
            else:
                prompt = await generate_prompt()
                player_hand, ai_cards = deal_default_solo(5, len(game["ai_opponents"]))

            game["prompt"] = prompt
            game["hand"] = player_hand
            game["ai_cards"] = ai_cards
            game["state"] = "submitting"
            game["submitted"] = False

            timer_seconds = channel_timers.get(ctx.channel.id)
            timer_text = ""
            if timer_seconds:
                deadline = int(time.time()) + timer_seconds
                timer_text = f"\n**Timer:** <t:{deadline}:R> to submit!"

            embed = discord.Embed(
                title=f"\U0001F7E3 PROMPT CARD — Solo Round {game['round_number']}",
                description=f"## {format_prompt(prompt)}\n\nClick the button below to view your hand and submit a card!{timer_text}",
                color=discord.Color.from_rgb(106, 27, 154)
            )
            embed.set_footer(text=f"Playing against {', '.join(game['ai_opponents'])}")
            view = HandView(game, mode="solo", channel=ctx.channel)
            await ctx.send(embed=embed, view=view)

            if timer_seconds:
                async def solo_timer(pid, secs, rid):
                    try:
                        await asyncio.sleep(secs)
                        g = solo_games.get(pid)
                        if g and g["state"] == "submitting" and g.get("round_id") == rid:
                            await finish_solo_round(pid, timed_out=True)
                    except asyncio.CancelledError:
                        pass

                task_key = f"solo_{player_id}"
                if task_key in timer_tasks:
                    timer_tasks[task_key].cancel()
                timer_tasks[task_key] = asyncio.create_task(
                    solo_timer(player_id, timer_seconds, current_round_id)
                )

        except Exception as e:
            game["state"] = "idle"
            await ctx.send(f"Error generating cards: {str(e)}")


async def finish_solo_round(player_id: str, player_card: str = None, timed_out: bool = False, channel=None):
    game = solo_games.get(player_id)
    if not game:
        return

    if game["state"] not in ("submitting", "judging"):
        return

    if game["state"] == "judging":
        return

    task_key = f"solo_{player_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
        timer_tasks.pop(task_key, None)

    game["state"] = "judging"
    if not channel:
        channel = bot.get_channel(game["channel_id"])
    if not channel:
        try:
            channel = await bot.fetch_channel(game["channel_id"])
        except Exception:
            pass
    if not channel:
        game["state"] = "idle"
        print(f"finish_solo_round: could not find channel {game['channel_id']}")
        return

    try:
        if timed_out and not player_card:
            player_card = random.choice(game["hand"])
            await channel.send(f"**Time's up!** A random card was played for you: *{player_card}*")

        all_entries = [(game["player_name"], player_card)]
        for i, ai_name in enumerate(game["ai_opponents"]):
            all_entries.append((ai_name, game["ai_cards"][i]))

        random.shuffle(all_entries)
        submission_cards = [card for _, card in all_entries]

        submission_text = "\n".join(
            f"\U0001F0CF **Card {i+1}:** {card}" for i, card in enumerate(submission_cards)
        )

        judging_embed = discord.Embed(
            title="\U0001F608 Dr. Evil is judging...",
            description=f"## {format_prompt(game['prompt'])}\n\n{submission_text}",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        await channel.send(embed=judging_embed)

        try:
            winner_idx = await ai_judge(game["prompt"], submission_cards)
        except Exception:
            winner_idx = 0

        winner_name, winner_card = all_entries[winner_idx]
        game["scores"][winner_name] = game["scores"].get(winner_name, 0) + 1

        is_player = winner_name == game["player_name"]

        result_embed = discord.Embed(
            title="\U0001F608 Dr. Evil Has Decided!",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        result_embed.add_field(name="Prompt", value=format_prompt(game["prompt"]), inline=False)

        for i, (name, card) in enumerate(all_entries):
            marker = " \U0001F3C6" if card == winner_card and name == winner_name else ""
            result_embed.add_field(
                name=f"Card {i+1}{marker}",
                value=f"{card}\n*— {name}*",
                inline=True
            )

        if is_player:
            verdict = f"\U0001F389 **You win this round!**"
        else:
            verdict = f"**{winner_name}** wins this round!"

        scores_text = "\n".join(
            f"{'> ' if name == game['player_name'] else ''}{name}: **{pts}**"
            for name, pts in sorted(game["scores"].items(), key=lambda x: -x[1])
        )

        result_embed.add_field(name="Result", value=verdict, inline=False)
        result_embed.add_field(name="Scores", value=scores_text, inline=False)
        await channel.send(embed=result_embed, view=SoloControlsView(player_id))

        game["state"] = "idle"

    except Exception as e:
        print(f"Error in finish_solo_round: {e}")
        import traceback
        traceback.print_exc()
        game["state"] = "idle"
        try:
            await channel.send(f"Something went wrong during judging. Type `!solo deal` to try again.")
        except Exception:
            pass


@solo_group.command(name="score")
async def solo_score(ctx):
    player_id = str(ctx.author.id)
    game = solo_games.get(player_id)
    if not game:
        await ctx.send("No solo game running. Start one with `!solo <opponents>`.")
        return

    scores_text = "\n".join(
        f"{'> ' if name == game['player_name'] else ''}{name}: **{pts}**"
        for name, pts in sorted(game["scores"].items(), key=lambda x: -x[1])
    )

    embed = discord.Embed(
        title=f"Solo Scores — Round {game['round_number']}",
        description=scores_text,
        color=discord.Color.dark_purple()
    )
    await ctx.send(embed=embed)


@solo_group.command(name="end")
async def solo_end(ctx):
    player_id = str(ctx.author.id)
    game = solo_games.pop(player_id, None)
    solo_locks.pop(player_id, None)
    if not game:
        await ctx.send("No solo game to end.")
        return

    task_key = f"solo_{player_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
        timer_tasks.pop(task_key, None)

    if game["round_number"] == 0:
        await ctx.send("Solo game ended before any rounds were played.")
        return

    scores = game["scores"]
    leader = max(scores, key=scores.get)
    scores_text = "\n".join(
        f"{'> ' if name == game['player_name'] else ''}{name}: **{pts}**"
        for name, pts in sorted(scores.items(), key=lambda x: -x[1])
    )

    is_player_winner = leader == game["player_name"]
    title = "You Win!" if is_player_winner else f"{leader} Wins!"

    embed = discord.Embed(
        title=f"Solo Game Over — {title}",
        description=f"**Final Scores after {game['round_number']} rounds:**\n\n{scores_text}",
        color=discord.Color.gold() if is_player_winner else discord.Color.dark_grey()
    )
    await ctx.send(embed=embed)


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


channel_decks = {}

FREE_DAILY_GAME_LIMIT = 3
daily_game_counts = {}
MAX_UPLOAD_SIZE_BOT = 2 * 1024 * 1024
MAX_CARDS_PER_DECK = 5000


def get_daily_key(guild_id):
    from datetime import date
    return f"{guild_id}:{date.today().isoformat()}"


async def check_pro_for_decks(ctx):
    if not ctx.guild:
        await ctx.send("Deck commands can only be used in a server.")
        return False
    premium = await is_premium_server(str(ctx.guild.id))
    if not premium:
        host = os.environ.get("REPLIT_DOMAINS", "evilapples4d.com").split(",")[0]
        await ctx.send(
            f"**Custom decks are a Pro feature!**\n"
            f"Upgrade your server to Pro to upload and use custom card decks.\n"
            f"Use `!premium` or visit https://{host}/premium"
        )
        return False
    return True


@bot.group(name="deck", invoke_without_command=True)
async def deck_group(ctx):
    embed = discord.Embed(
        title="Evil Apples 4D — Card Decks (Pro)",
        description="Upload and use your own custom card decks! *(Pro feature)*",
        color=discord.Color.purple()
    )
    embed.add_field(name="Commands", value=(
        "`!deck upload <name>` — Upload a CSV deck (attach file)\n"
        "`!deck list` — View your decks\n"
        "`!deck use <name>` — Use one of your decks for this channel\n"
        "`!deck ai` — Switch back to AI-generated cards\n"
        "`!deck info <name>` — View deck details\n"
        "`!deck delete <name>` — Delete one of your decks"
    ), inline=False)

    current = channel_decks.get(ctx.channel.id)
    if current:
        embed.add_field(
            name="Current Deck",
            value=f"**{current['name']}** ({current['prompt_count']} prompts, {current['answer_count']} answers)",
            inline=False
        )
    else:
        embed.add_field(name="Current Mode", value="AI-Generated Cards (Venice.ai)", inline=False)

    await ctx.send(embed=embed)


@deck_group.command(name="upload")
async def deck_upload(ctx, *, name: str = ""):
    if not await check_pro_for_decks(ctx):
        return

    if not name:
        await ctx.send("Usage: `!deck upload <deck name>` — attach a CSV file to your message.")
        return

    if not ctx.message.attachments:
        await ctx.send("Please attach a CSV file to your message.\n"
                       "CSV format: `type,text` columns. Types: `prompt` or `answer`.")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.lower().endswith(".csv"):
        await ctx.send("Please attach a **.csv** file.")
        return

    if attachment.size > MAX_UPLOAD_SIZE_BOT:
        await ctx.send(f"File too large! Maximum size is {MAX_UPLOAD_SIZE_BOT // (1024*1024)}MB.")
        return

    user_id = str(ctx.author.id)

    existing = await get_deck_by_name(name, discord_user_id=user_id)
    if existing:
        await ctx.send(f"You already have a deck named **{name}**. Delete it first with `!deck delete {name}`.")
        return

    await ctx.send("Processing your deck...")

    try:
        import csv
        import io

        content = await attachment.read()
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        cards = []
        for row in reader:
            card_type_raw = (row.get("type") or row.get("Type") or row.get("TYPE") or "").strip().lower()
            card_text = (row.get("text") or row.get("Text") or row.get("TEXT") or "").strip()

            if not card_type_raw or not card_text:
                continue

            if card_type_raw in ("prompt", "black", "question", "b"):
                card_type = "prompt"
            elif card_type_raw in ("answer", "white", "response", "w", "a"):
                card_type = "answer"
            else:
                continue

            cards.append({"type": card_type, "text": card_text})

        if not cards:
            await ctx.send("No valid cards found. CSV needs `type` and `text` columns.\n"
                           "Types: `prompt`/`black` or `answer`/`white`.")
            return

        if len(cards) > MAX_CARDS_PER_DECK:
            await ctx.send(f"Too many cards! Maximum is {MAX_CARDS_PER_DECK} per deck.")
            return

        deck_id = await create_deck(name, "", ctx.author.display_name, discord_user_id=user_id)
        await add_cards_to_deck(deck_id, cards)

        prompt_count = sum(1 for c in cards if c["type"] == "prompt")
        answer_count = sum(1 for c in cards if c["type"] == "answer")

        embed = discord.Embed(
            title=f"Deck Uploaded: {name}",
            description=f"**{prompt_count}** prompts and **{answer_count}** answers loaded!",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Use !deck use {name} to play with this deck")
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"Failed to process CSV: {str(e)}")


@deck_group.command(name="list")
async def deck_list(ctx):
    if not await check_pro_for_decks(ctx):
        return

    user_id = str(ctx.author.id)
    decks = await get_all_decks(discord_user_id=user_id)
    if not decks:
        await ctx.send("You don't have any custom decks yet. Upload one with `!deck upload <name>` and attach a CSV file.")
        return

    deck_text = "\n".join(
        f"**{d['name']}** — {d['prompt_count']} prompts, {d['answer_count']} answers"
        for d in decks
    )

    embed = discord.Embed(
        title="Your Card Decks",
        description=deck_text,
        color=discord.Color.purple()
    )
    embed.set_footer(text="Use !deck use <name> to select a deck")
    await ctx.send(embed=embed)


@deck_group.command(name="use")
async def deck_use(ctx, *, name: str = ""):
    if not await check_pro_for_decks(ctx):
        return

    if not name:
        await ctx.send("Please specify a deck name: `!deck use <name>`")
        return

    user_id = str(ctx.author.id)
    deck = await get_deck_by_name(name, discord_user_id=user_id)
    if not deck:
        await ctx.send(f"Deck **{name}** not found in your decks. Use `!deck list` to see your decks.")
        return

    if deck["prompt_count"] < 1:
        await ctx.send(f"Deck **{deck['name']}** has no prompt cards. You need at least 1 prompt to play.")
        return
    if deck["answer_count"] < 5:
        await ctx.send(f"Deck **{deck['name']}** only has {deck['answer_count']} answer cards. You need at least 5 to deal a hand.")
        return

    channel_decks[ctx.channel.id] = {
        "id": deck["id"],
        "name": deck["name"],
        "prompt_count": deck["prompt_count"],
        "answer_count": deck["answer_count"],
    }

    embed = discord.Embed(
        title=f"Deck Selected: {deck['name']}",
        description=f"**{deck['prompt_count']}** prompts and **{deck['answer_count']}** answers loaded.\nCards will now be drawn from this deck instead of AI generation.",
        color=discord.Color.green()
    )
    embed.set_footer(text="Use !deck ai to switch back to AI-generated cards")
    await ctx.send(embed=embed)


@deck_group.command(name="ai")
async def deck_ai(ctx):
    if ctx.channel.id in channel_decks:
        old = channel_decks.pop(ctx.channel.id)
        await ctx.send(f"Switched from **{old['name']}** back to **AI-generated cards** (Venice.ai).")
    else:
        await ctx.send("Already using AI-generated cards!")


@deck_group.command(name="info")
async def deck_info(ctx, *, name: str = ""):
    if not await check_pro_for_decks(ctx):
        return

    if not name:
        await ctx.send("Please specify a deck name: `!deck info <name>`")
        return

    user_id = str(ctx.author.id)
    deck = await get_deck_by_name(name, discord_user_id=user_id)
    if not deck:
        await ctx.send(f"Deck **{name}** not found in your decks. Use `!deck list` to see your decks.")
        return

    embed = discord.Embed(
        title=deck["name"],
        description=deck.get("description", "") or "No description",
        color=discord.Color.purple()
    )
    embed.add_field(name="Prompts", value=str(deck["prompt_count"]), inline=True)
    embed.add_field(name="Answers", value=str(deck["answer_count"]), inline=True)
    embed.add_field(name="Created By", value=deck["creator_name"], inline=True)
    await ctx.send(embed=embed)


@deck_group.command(name="delete")
async def deck_delete(ctx, *, name: str = ""):
    if not await check_pro_for_decks(ctx):
        return

    if not name:
        await ctx.send("Please specify a deck name: `!deck delete <name>`")
        return

    user_id = str(ctx.author.id)
    deck = await get_deck_by_name(name, discord_user_id=user_id)
    if not deck:
        await ctx.send(f"Deck **{name}** not found in your decks.")
        return

    from db import delete_deck
    await delete_deck(deck["id"])
    await ctx.send(f"Deck **{name}** has been deleted.")


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

        game_channel = bot.get_channel(channel_id)
        if game_channel:
            card_num = len(game["submissions"])
            total = len(game["players"])
            card_embed = discord.Embed(
                description=f"## \U0001F0CF  {selected_card}",
                color=discord.Color.from_rgb(255, 255, 255)
            )
            card_embed.set_footer(text=f"Card {card_num} of {total}")
            await game_channel.send(embed=card_embed)

        if len(game["submissions"]) == len(game["players"]):
            if channel_id in timer_tasks:
                timer_tasks[channel_id].cancel()
                del timer_tasks[channel_id]

            players_in_round = {
                pid: game["players"][pid] for pid in game["players"]
            }
            await batch_update_games_played(players_in_round)

            game["state"] = "judging"
            channel = bot.get_channel(channel_id)
            if channel:
                submission_text = "\n".join(
                    f"\U0001F0CF **Card {i+1}:** {card}"
                    for i, (_, card) in enumerate(
                        list(game["submissions"].items())
                    )
                )

                embed = discord.Embed(
                    title="\u2696\uFE0F Time to Judge!",
                    description=f"## {format_prompt(game['prompt'])}\n\n{submission_text}\n\n*Judge: pick the winning card!*",
                    color=discord.Color.from_rgb(106, 27, 154)
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

        t_channel = bot.get_channel(channel_id)
        if t_channel:
            card_num = len(round_data["submissions"])
            total = len(round_data["players"])
            card_embed = discord.Embed(
                description=f"## \U0001F0CF  {selected_card}",
                color=discord.Color.from_rgb(255, 255, 255)
            )
            card_embed.set_footer(text=f"Card {card_num} of {total}")
            await t_channel.send(embed=card_embed)

        if len(round_data["submissions"]) == len(round_data["players"]):
            channel = bot.get_channel(channel_id)
            if channel:
                submissions = list(round_data["submissions"].items())
                random.shuffle(submissions)

                submission_text = "\n".join(
                    f"\U0001F0CF **Card {i+1}:** {card}"
                    for i, (_, card) in enumerate(submissions)
                )

                embed = discord.Embed(
                    title=f"\u2696\uFE0F Round {round_data['round_number']} — Time to Judge!",
                    description=f"## {format_prompt(round_data['prompt'])}\n\n{submission_text}\n\n*Judge: pick the winning card!*",
                    color=discord.Color.from_rgb(106, 27, 154)
                )
                embed.set_footer(
                    text=f"Tournament: {tournament_data['name']}"
                )

                view = TournamentJudgingView(tournament_data, round_data, channel)
                await channel.send(embed=embed, view=view)
        return

    solo = solo_games.get(player_id)
    if solo and solo["state"] == "submitting" and not solo.get("submitted"):
        hand = solo.get("hand", [])
        if choice < 1 or choice > len(hand):
            await message.channel.send(f"Pick a number between 1 and {len(hand)}.")
            return

        selected_card = hand[choice - 1]
        solo["submitted"] = True
        await message.channel.send(f"Card submitted: **{selected_card}**\nDr. Evil is deciding...")
        await finish_solo_round(player_id, player_card=selected_card, channel=message.channel)
        return


def run_bot():
    bot.run(DISCORD_TOKEN)
