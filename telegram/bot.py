import os
import random
import asyncio
import logging
from slugify import slugify
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.error import Forbidden, BadRequest

from api_client import (
    generate_prompt, ai_judge, dr_evil_roast,
    deal_default_hands, deal_default_solo, HAND_SIZE
)
from db import (
    init_db, save_round, batch_update_games_played,
    update_leaderboard, get_leaderboard, record_bot_group_count
)

logger = logging.getLogger("tg_bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

GAME_MODES = {
    "friend": {
        "min_players": 2, "win_score": 7, "warn_score": 5,
        "description": "Casual — no timer, first to 7 wins"
    },
    "swap": {
        "min_players": 5, "win_score": 7, "warn_score": 5,
        "description": "Cards rotate between players — chaos mode"
    },
}

AI_OPPONENT_NAMES = [
    "DevilBot", "SatanAI", "DarkLord", "EvilGPT", "ChaosMachine",
    "SnarkBot", "VileUnit", "GrimReaper", "ToxicAI", "WickedBot",
]

active_games: dict[int, dict] = {}
solo_games: dict[int, dict] = {}
timer_tasks: dict = {}


def format_prompt(text: str) -> str:
    return text.replace("_", "________")


def scores_text(game: dict) -> str:
    scores = game.get("scores", {})
    players = game.get("players", {})
    lines = []
    for pid, pts in sorted(scores.items(), key=lambda x: -x[1]):
        name = players.get(pid, str(pid))
        lines.append(f"  {name}: {pts} pts")
    return "\n".join(lines) if lines else "No scores yet"


def submission_count_text(game: dict) -> str:
    playing = game.get("playing_ids", [])
    submitted = game.get("submissions", {})
    return f"{len(submitted)}/{len(playing)} submitted"


def join_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✋ Join Game", callback_data=f"join_{chat_id}")
    ]])


def hand_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🃏 View My Hand", callback_data=f"viewhand_{chat_id}")
    ]])


def card_keyboard(hand: list[str], prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    for i, card in enumerate(hand):
        label = card[:60] + "…" if len(card) > 60 else card
        buttons.append([InlineKeyboardButton(label, callback_data=f"{prefix}_{i}")])
    return InlineKeyboardMarkup(buttons)


def judge_keyboard(submissions: list[tuple], chat_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for i, (_, card) in enumerate(submissions):
        label = f"Card {i+1}: " + (card[:50] + "…" if len(card) > 50 else card)
        buttons.append([InlineKeyboardButton(label, callback_data=f"judge_{chat_id}_{i}")])
    return InlineKeyboardMarkup(buttons)


def next_round_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Next Round", callback_data="deal_next")
    ]])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "😈 *Evil Apples for Telegram*\n\n"
        "Add me to a group and use /startgame to play!\n\n"
        "*Commands:*\n"
        "/startgame \\[mode\\] — Start a game \\(friend or swap\\)\n"
        "/join — Join the current game\n"
        "/deal — Deal cards and start a round\n"
        "/endgame — End the current game\n"
        "/scores — Show current scores\n"
        "/solo — Play solo vs AI\n"
        "/help — Show this message",
        parse_mode="MarkdownV2"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "😈 *Evil Apples — Commands*\n\n"
        "*Group Commands:*\n"
        "`/startgame` — Start a game \\(default: friend mode\\)\n"
        "`/startgame swap` — Start swap mode \\(5\\+ players\\)\n"
        "`/join` — Join the game\n"
        "`/deal` — Deal a new round\n"
        "`/scores` — Show current scores\n"
        "`/endgame` — End the game \\(host only\\)\n\n"
        "*Private Commands:*\n"
        "`/solo` — Play vs 3 AI opponents\n"
        "`/solo 5` — Play vs 5 AI opponents\n\n"
        "*How to play:*\n"
        "Judge reads the prompt card\\. Players submit their funniest answer\\. "
        "Judge picks the winner\\. First to 7 points wins\\!",
        parse_mode="MarkdownV2"
    )


async def cmd_startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "Use /startgame in a group chat! Use /solo to play alone here."
        )
        return

    if chat_id in active_games:
        await update.message.reply_text(
            "A game is already running! Use /endgame to stop it first."
        )
        return

    mode = (context.args[0].lower() if context.args else "friend")
    if mode not in GAME_MODES:
        modes = ", ".join(f"`{m}`" for m in GAME_MODES)
        await update.message.reply_text(
            f"Unknown mode `{mode}`. Available: {modes}"
        )
        return

    mode_info = GAME_MODES[mode]
    player_id = str(user.id)

    active_games[chat_id] = {
        "chat_id": chat_id,
        "host_id": player_id,
        "state": "waiting_for_players",
        "game_mode": mode,
        "players": {player_id: user.full_name},
        "all_player_ids": [player_id],
        "scores": {player_id: 0},
        "judge_index": -1,
        "judge_id": None,
        "use_ai_judge": False,
        "hands": {},
        "submissions": {},
        "prompt": "",
        "round_id": 0,
        "playing_ids": [],
        "win_score": mode_info["win_score"],
        "warn_score": mode_info["warn_score"],
    }

    await update.message.reply_text(
        f"😈 *Evil Apples — {mode.upper()} Mode*\n\n"
        f"Started by *{user.full_name}*\\!\n\n"
        f"_{mode_info['description']}_\n"
        f"First to *{mode_info['win_score']} points* wins\\!\n"
        f"Min players: {mode_info['min_players']}\n\n"
        f"Tap the button or type /join to join\\!",
        parse_mode="MarkdownV2",
        reply_markup=join_keyboard(chat_id)
    )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    game = active_games.get(chat_id)

    if not game:
        await update.message.reply_text("No active game. Use /startgame to start one!")
        return

    player_id = str(user.id)
    if player_id in game["players"]:
        await update.message.reply_text(f"You're already in the game, {user.first_name}!")
        return

    game["players"][player_id] = user.full_name
    if player_id not in game["all_player_ids"]:
        game["all_player_ids"].append(player_id)
    game["scores"][player_id] = 0

    count = len(game["players"])
    await update.message.reply_text(
        f"✅ *{user.full_name}* joined the game! \\({count} players\\)\n\n"
        f"When everyone's in, use /deal to start the first round\\!",
        parse_mode="MarkdownV2"
    )


async def cb_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id_str = query.data.split("_", 1)[1]
    chat_id = int(chat_id_str)
    user = query.from_user
    game = active_games.get(chat_id)

    if not game:
        await query.answer("Game has ended!", show_alert=True)
        return

    player_id = str(user.id)
    if player_id in game["players"]:
        await query.answer("You're already in the game!", show_alert=True)
        return

    game["players"][player_id] = user.full_name
    if player_id not in game["all_player_ids"]:
        game["all_player_ids"].append(player_id)
    game["scores"][player_id] = 0

    count = len(game["players"])
    await context.bot.send_message(
        chat_id,
        f"✅ *{user.full_name}* joined the game\\! \\({count} players\\)",
        parse_mode="MarkdownV2"
    )


async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games.get(chat_id)

    if not game:
        await update.message.reply_text("No active game. Use /startgame to begin!")
        return

    await _do_deal(chat_id, context.bot, game)


async def cb_deal_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    game = active_games.get(chat_id)
    if not game:
        return
    await _do_deal(chat_id, context.bot, game)


async def _do_deal(chat_id: int, bot, game: dict):
    mode = game.get("game_mode", "friend")
    min_players = GAME_MODES[mode]["min_players"]

    if len(game["players"]) < min_players:
        await bot.send_message(
            chat_id,
            f"Need at least {min_players} players for {mode.upper()} mode! "
            f"Have more people type /join."
        )
        return

    if game["state"] not in ("waiting_for_players", "finished"):
        await bot.send_message(chat_id, "A round is already in progress!")
        return

    if chat_id in timer_tasks:
        timer_tasks[chat_id].cancel()
        timer_tasks.pop(chat_id, None)

    total_players = len(game["players"])
    use_ai_judge = total_players < 3

    if use_ai_judge:
        game["judge_id"] = None
        game["use_ai_judge"] = True
        playing_ids = list(game["players"].keys())
        judge_name = "😈 Dr\\. Evil"
    else:
        game["judge_index"] = (game.get("judge_index", -1) + 1) % len(game["all_player_ids"])
        judge_id = game["all_player_ids"][game["judge_index"]]
        while judge_id not in game["players"]:
            game["judge_index"] = (game["judge_index"] + 1) % len(game["all_player_ids"])
            judge_id = game["all_player_ids"][game["judge_index"]]
        game["judge_id"] = judge_id
        game["use_ai_judge"] = False
        playing_ids = [pid for pid in game["players"] if pid != judge_id]
        judge_name = f"*{game['players'][judge_id]}*"

    game["state"] = "dealing"
    game["submissions"] = {}
    game["round_id"] = game.get("round_id", 0) + 1
    game["playing_ids"] = playing_ids

    try:
        prompt = await generate_prompt()
        game["prompt"] = prompt

        hands = deal_default_hands(len(playing_ids), HAND_SIZE)
        game["hands"] = {pid: hand for pid, hand in zip(playing_ids, hands)}

        if mode == "swap" and len(playing_ids) > 1:
            ordered_hands = [game["hands"][pid] for pid in playing_ids]
            shift = random.randint(1, max(1, len(ordered_hands) - 1))
            ordered_hands = ordered_hands[shift:] + ordered_hands[:shift]
            game["hands"] = {pid: hand for pid, hand in zip(playing_ids, ordered_hands)}

        game["state"] = "submitting"

        current_scores = scores_text(game)
        prompt_display = format_prompt(prompt).replace(".", "\\.").replace("!", "\\!").replace("?", "\\?").replace("(", "\\(").replace(")", "\\)").replace("-", "\\-").replace("_", "\\_")

        await bot.send_message(
            chat_id,
            f"🟣 *PROMPT CARD*\n\n"
            f"*{prompt_display}*\n\n"
            f"⚖️ Judge this round: {judge_name}\n\n"
            f"Tap the button to view your hand and pick a card\\!\n\n"
            f"*Scores:*\n{current_scores}",
            parse_mode="MarkdownV2",
            reply_markup=hand_keyboard(chat_id)
        )

    except Exception as e:
        logger.error(f"Deal error: {e}")
        game["state"] = "waiting_for_players"
        await bot.send_message(chat_id, f"Error generating cards: {e}")


async def cb_view_hand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = int(query.data.split("_", 1)[1])
    user = query.from_user
    player_id = str(user.id)
    game = active_games.get(chat_id)

    if not game or game["state"] != "submitting":
        await query.answer("No active round right now.", show_alert=True)
        return

    if player_id not in game.get("playing_ids", []):
        if player_id == game.get("judge_id"):
            await query.answer("You're the judge this round — wait for submissions!", show_alert=True)
        else:
            await query.answer("You're not in this round.", show_alert=True)
        return

    if player_id in game.get("submissions", {}):
        await query.answer("You already submitted a card this round!", show_alert=True)
        return

    hand = game["hands"].get(player_id, [])
    if not hand:
        await query.answer("No cards in your hand!", show_alert=True)
        return

    prompt_display = format_prompt(game["prompt"])

    try:
        await context.bot.send_message(
            user.id,
            f"🃏 *Your Hand*\n\n"
            f"Prompt: _{prompt_display}_\n\n"
            f"Pick a card to submit:",
            parse_mode="Markdown",
            reply_markup=card_keyboard(hand, f"submit_{chat_id}")
        )
    except (Forbidden, BadRequest):
        await query.answer(
            "⚠️ I can't DM you! Please start a private chat with me first, then try again.",
            show_alert=True
        )


async def cb_submit_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Card submitted! ✅")

    parts = query.data.split("_")
    chat_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user
    player_id = str(user.id)
    game = active_games.get(chat_id)

    if not game or game["state"] != "submitting":
        return

    if player_id not in game.get("playing_ids", []):
        return

    if player_id in game.get("submissions", {}):
        await query.answer("You already submitted!", show_alert=True)
        return

    hand = game["hands"].get(player_id, [])
    if card_idx >= len(hand):
        return

    card = hand[card_idx]
    game["submissions"][player_id] = card

    await query.edit_message_text(
        f"✅ *Card submitted!*\n\nYou played: _{card}_\n\nWaiting for others...",
        parse_mode="Markdown"
    )

    submitted = len(game["submissions"])
    total = len(game.get("playing_ids", []))
    try:
        await context.bot.send_message(
            chat_id,
            f"✅ {user.full_name} submitted their card. ({submitted}/{total})",
        )
    except Exception:
        pass

    await _check_all_submitted(chat_id, context.bot, game)


async def _check_all_submitted(chat_id: int, bot, game: dict):
    playing_ids = game.get("playing_ids", [])
    if len(game["submissions"]) < len(playing_ids):
        return

    if chat_id in timer_tasks:
        timer_tasks[chat_id].cancel()
        timer_tasks.pop(chat_id, None)

    players_in_round = {pid: game["players"][pid] for pid in game["players"]}
    try:
        await batch_update_games_played(players_in_round)
    except Exception as e:
        logger.error(f"batch_update_games_played error: {e}")

    if game.get("use_ai_judge"):
        game["state"] = "judging"
        await _ai_judge_pick(chat_id, bot, game)
        return

    game["state"] = "judging"
    submissions = list(game["submissions"].items())
    random.shuffle(submissions)
    game["_shuffled_submissions"] = submissions

    judge_id = game.get("judge_id")
    judge_name = game["players"].get(judge_id, "Judge")

    sub_text = "\n".join(
        f"🃏 Card {i+1}: {card}" for i, (_, card) in enumerate(submissions)
    )

    await bot.send_message(
        chat_id,
        f"⚖️ All cards are in!\n\n"
        f"Prompt: *{format_prompt(game['prompt'])}*\n\n"
        f"{sub_text}\n\n"
        f"*{judge_name}*, pick the winning card!\n"
        f"_(Judge has 2 minutes or Dr. Evil steps in)_",
        parse_mode="Markdown",
        reply_markup=judge_keyboard(submissions, chat_id)
    )

    round_id = game.get("round_id", 0)

    async def judge_timeout_task():
        try:
            await asyncio.sleep(120)
            g = active_games.get(chat_id)
            if g and g.get("state") == "judging" and g.get("round_id") == round_id:
                await bot.send_message(
                    chat_id, "⏰ Judge took too long! Dr. Evil is stepping in..."
                )
                await _ai_judge_pick(chat_id, bot, g)
        except asyncio.CancelledError:
            pass

    task_key = f"judge_{chat_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
    timer_tasks[task_key] = asyncio.create_task(judge_timeout_task())


async def cb_judge_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    chat_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user
    player_id = str(user.id)
    game = active_games.get(chat_id)

    if not game or game["state"] != "judging":
        return

    if str(player_id) != game.get("judge_id"):
        await query.answer("Only the judge can pick the winner!", show_alert=True)
        return

    task_key = f"judge_{chat_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
        timer_tasks.pop(task_key, None)

    submissions = game.get("_shuffled_submissions", list(game["submissions"].items()))
    if card_idx >= len(submissions):
        return

    winner_id, winning_card = submissions[card_idx]
    winner_name = game["players"].get(winner_id, "Unknown")

    await query.edit_message_reply_markup(reply_markup=None)
    await _announce_winner(chat_id, context.bot, game, winner_id, winner_name, winning_card)


async def _ai_judge_pick(chat_id: int, bot, game: dict):
    prompt = game["prompt"]
    submissions = list(game["submissions"].items())

    if not submissions:
        game["state"] = "finished"
        await bot.send_message(
            chat_id, "No cards were submitted — round is void. Use /deal for a new round."
        )
        return

    random.shuffle(submissions)
    card_texts = [card for _, card in submissions]

    sub_text = "\n".join(
        f"🃏 Card {i+1}: {card}" for i, card in enumerate(card_texts)
    )
    await bot.send_message(
        chat_id,
        f"😈 Dr. Evil is deliberating...\n\n"
        f"*{format_prompt(prompt)}*\n\n{sub_text}",
        parse_mode="Markdown"
    )
    await asyncio.sleep(5)

    try:
        chosen_index = await ai_judge(prompt, card_texts)
    except Exception:
        chosen_index = random.randint(0, len(submissions) - 1)

    winner_id, winning_card = submissions[chosen_index]
    winner_name = game["players"].get(winner_id, "Unknown")

    roast = ""
    try:
        if random.random() < 0.4:
            roast = await dr_evil_roast(prompt, card_texts)
    except Exception:
        pass

    await _announce_winner(chat_id, bot, game, winner_id, winner_name, winning_card, roast=roast)


async def _announce_winner(
    chat_id: int, bot, game: dict,
    winner_id: str, winner_name: str, winning_card: str,
    roast: str = ""
):
    prompt = game["prompt"]
    slug = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")

    try:
        await save_round(prompt, winning_card, winner_id, winner_name, slug)
    except Exception as e:
        logger.error(f"save_round error: {e}")

    try:
        await update_leaderboard(winner_id, winner_name, won=True)
    except Exception as e:
        logger.error(f"update_leaderboard error: {e}")

    scores = game.setdefault("scores", {})
    scores[winner_id] = scores.get(winner_id, 0) + 1
    player_score = scores[winner_id]
    win_score = game.get("win_score", 7)
    warn_score = game.get("warn_score", 5)

    current_scores = scores_text(game)

    msg = (
        f"🏆 *Winner: {winner_name}!*\n\n"
        f"Prompt: _{format_prompt(prompt)}_\n\n"
        f"Winning card: *{winning_card}*"
    )
    if roast:
        msg += f"\n\n😈 Dr. Evil says: _{roast}_"
    msg += f"\n\n*Scores:*\n{current_scores}"

    game["state"] = "finished"

    if player_score >= win_score:
        game["state"] = "game_over"
        msg += f"\n\n🎉 *GAME OVER! {winner_name} wins with {player_score} points!*\n\nStart a new game with /startgame"
        await bot.send_message(chat_id, msg, parse_mode="Markdown")
        active_games.pop(chat_id, None)
        return

    if player_score >= warn_score:
        msg += f"\n\n🔥 _{winner_name}_ is getting close to winning!"

    await bot.send_message(
        chat_id, msg,
        parse_mode="Markdown",
        reply_markup=next_round_keyboard()
    )


async def cmd_endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    game = active_games.get(chat_id)

    if not game:
        await update.message.reply_text("No active game to end.")
        return

    if str(user.id) != game.get("host_id"):
        await update.message.reply_text("Only the game host can end the game!")
        return

    for key in [chat_id, f"judge_{chat_id}"]:
        task = timer_tasks.pop(key, None)
        if task:
            task.cancel()

    active_games.pop(chat_id, None)
    await update.message.reply_text(
        "Game ended. Start a new one with /startgame!"
    )


async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games.get(chat_id)
    if not game:
        await update.message.reply_text("No active game.")
        return
    await update.message.reply_text(
        f"*Current Scores:*\n\n{scores_text(game)}",
        parse_mode="Markdown"
    )


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = await get_leaderboard(10)
        if not rows:
            await update.message.reply_text(
                "No games played yet! Start one with /startgame"
            )
            return
        lines = ["🏆 *Evil Apples Leaderboard*\n"]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"{i}. *{row['discord_name']}* — {row['wins']} wins / {row['games_played']} games"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error fetching leaderboard: {e}")


async def cmd_solo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player_id = user.id
    chat_id = update.effective_chat.id

    if player_id in solo_games:
        await update.message.reply_text(
            "You already have a solo game running! Use /soloend to stop it."
        )
        return

    try:
        opponents = int(context.args[0]) if context.args else 3
        opponents = max(1, min(5, opponents))
    except (ValueError, IndexError):
        opponents = 3

    ai_names = random.sample(AI_OPPONENT_NAMES, min(opponents, len(AI_OPPONENT_NAMES)))

    solo_games[player_id] = {
        "chat_id": chat_id,
        "player_id": player_id,
        "player_name": user.full_name,
        "ai_opponents": ai_names,
        "scores": {user.full_name: 0, **{n: 0 for n in ai_names}},
        "state": "idle",
        "round_number": 0,
        "hand": [],
        "prompt": "",
        "ai_cards": [],
        "submitted": False,
    }

    await update.message.reply_text(
        f"😈 *Solo Mode*\n\n"
        f"*{user.full_name}* vs {', '.join(f'**{n}**' for n in ai_names)}\n\n"
        f"Dr. Evil will judge each round.\n"
        f"Use /solodeal to start a round!",
        parse_mode="Markdown"
    )


async def cmd_solo_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player_id = user.id
    game = solo_games.get(player_id)

    if not game:
        await update.message.reply_text(
            "No solo game. Start one with /solo!"
        )
        return

    if game["state"] in ("dealing", "submitting", "judging"):
        await update.message.reply_text("A round is already in progress!")
        return

    game["state"] = "dealing"
    game["round_number"] += 1
    game["submitted"] = False

    try:
        prompt = await generate_prompt()
        player_hand, ai_cards = deal_default_solo(HAND_SIZE, len(game["ai_opponents"]))
        game["prompt"] = prompt
        game["hand"] = player_hand
        game["ai_cards"] = ai_cards
        game["state"] = "submitting"

        prompt_display = format_prompt(prompt)
        await update.message.reply_text(
            f"🟣 *Solo Round {game['round_number']}*\n\n"
            f"Prompt: *{prompt_display}*\n\n"
            f"Pick your card:",
            parse_mode="Markdown",
            reply_markup=card_keyboard(player_hand, f"solosub_{player_id}")
        )
    except Exception as e:
        game["state"] = "idle"
        await update.message.reply_text(f"Error: {e}")


async def cb_solo_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Card submitted!")

    parts = query.data.split("_")
    player_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user

    if user.id != player_id:
        await query.answer("This isn't your game!", show_alert=True)
        return

    game = solo_games.get(player_id)
    if not game or game["state"] != "submitting":
        return

    if game["submitted"]:
        await query.answer("Already submitted!", show_alert=True)
        return

    hand = game["hand"]
    if card_idx >= len(hand):
        return

    player_card = hand[card_idx]
    game["submitted"] = True
    game["state"] = "judging"

    await query.edit_message_text(
        f"✅ You played: *{player_card}*\n\nDr. Evil is judging...",
        parse_mode="Markdown"
    )

    prompt = game["prompt"]
    ai_cards = game["ai_cards"]
    all_entries = [(game["player_name"], player_card)] + list(zip(game["ai_opponents"], ai_cards))
    random.shuffle(all_entries)

    card_texts = [card for _, card in all_entries]
    player_entry_idx = next(i for i, (name, _) in enumerate(all_entries) if name == game["player_name"])

    sub_text = "\n".join(f"🃏 Card {i+1}: {card}" for i, card in enumerate(card_texts))

    chat_id = game["chat_id"]
    try:
        await context.bot.send_message(
            chat_id,
            f"😈 Dr. Evil deliberates...\n\n*{format_prompt(prompt)}*\n\n{sub_text}",
            parse_mode="Markdown"
        )
        await asyncio.sleep(4)

        chosen_idx = await ai_judge(prompt, card_texts)
    except Exception:
        chosen_idx = random.randint(0, len(all_entries) - 1)

    winner_name, winning_card = all_entries[chosen_idx]
    roast = ""
    try:
        if random.random() < 0.5:
            roast = await dr_evil_roast(prompt, card_texts)
    except Exception:
        pass

    scores = game["scores"]
    scores[winner_name] = scores.get(winner_name, 0) + 1

    score_text = "\n".join(
        f"  {n}: {p} pts" for n, p in sorted(scores.items(), key=lambda x: -x[1])
    )

    won_this_round = winner_name == game["player_name"]
    result_msg = (
        f"🏆 *{'You win' if won_this_round else winner_name + ' wins'} this round!*\n\n"
        f"Prompt: _{format_prompt(prompt)}_\n"
        f"Winning card: *{winning_card}*"
    )
    if roast:
        result_msg += f"\n\n😈 _{roast}_"
    result_msg += f"\n\n*Scores:*\n{score_text}\n\nUse /solodeal for the next round!"

    game["state"] = "idle"

    try:
        if won_this_round:
            await update_leaderboard(str(player_id), game["player_name"], won=True)
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id, result_msg, parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            user.id, result_msg, parse_mode="Markdown"
        )


async def cmd_solo_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    game = solo_games.pop(user.id, None)
    if game:
        final = "\n".join(
            f"  {n}: {p} pts"
            for n, p in sorted(game["scores"].items(), key=lambda x: -x[1])
        )
        await update.message.reply_text(
            f"Solo game ended!\n\n*Final Scores:*\n{final}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("No solo game running.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram bot error: {context.error}", exc_info=context.error)


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("deal", cmd_deal))
    app.add_handler(CommandHandler("endgame", cmd_endgame))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("solo", cmd_solo))
    app.add_handler(CommandHandler("solodeal", cmd_solo_deal))
    app.add_handler(CommandHandler("soloend", cmd_solo_end))

    app.add_handler(CallbackQueryHandler(cb_join, pattern=r"^join_"))
    app.add_handler(CallbackQueryHandler(cb_view_hand, pattern=r"^viewhand_"))
    app.add_handler(CallbackQueryHandler(cb_submit_card, pattern=r"^submit_"))
    app.add_handler(CallbackQueryHandler(cb_judge_pick, pattern=r"^judge_"))
    app.add_handler(CallbackQueryHandler(cb_deal_next, pattern=r"^deal_next$"))
    app.add_handler(CallbackQueryHandler(cb_solo_submit, pattern=r"^solosub_"))

    app.add_error_handler(error_handler)

    return app


async def set_commands(app: Application):
    await app.bot.set_my_commands([
        BotCommand("startgame", "Start a new game (add: friend or swap)"),
        BotCommand("join", "Join the current game"),
        BotCommand("deal", "Deal cards for a new round"),
        BotCommand("scores", "Show current game scores"),
        BotCommand("endgame", "End the current game (host only)"),
        BotCommand("leaderboard", "Show top players"),
        BotCommand("solo", "Play solo vs AI opponents"),
        BotCommand("solodeal", "Deal a solo round"),
        BotCommand("soloend", "End your solo game"),
        BotCommand("help", "Show all commands"),
    ])
