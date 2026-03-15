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
    deal_default_hands, deal_default_solo, generate_hand, HAND_SIZE
)
from db import (
    init_db, save_round, batch_update_games_played,
    update_leaderboard, get_leaderboard, record_bot_group_count,
    create_tg_tournament, get_active_tg_tournament, join_tg_tournament,
    get_tg_tournament_players, get_tg_tournament_player_count,
    start_tg_tournament, advance_tg_tournament_round, end_tg_tournament,
    is_premium_group, get_tg_user_decks, create_tg_deck, add_cards_to_tg_deck,
    get_tg_deck_cards, get_random_cards_from_decks, delete_tg_deck,
    get_tg_store_listings, get_tg_store_listing,
)

logger = logging.getLogger("tg_bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_CODE", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")

GAME_MODES = {
    "friend": {
        "min_players": 2, "win_score": 7, "warn_score": 5,
        "description": "Casual — no timer, first to 7 wins"
    },
    "swap": {
        "min_players": 5, "win_score": 7, "warn_score": 5,
        "description": "Cards rotate between players — chaos mode"
    },
    "wildcard": {
        "min_players": 2, "win_score": 7, "warn_score": 5,
        "description": "Write your own answers — no pre-made cards"
    },
}

AI_OPPONENT_NAMES = [
    "DevilBot", "SatanAI", "DarkLord", "EvilGPT", "ChaosMachine",
    "SnarkBot", "VileUnit", "GrimReaper", "ToxicAI", "WickedBot",
]

active_games: dict[int, dict] = {}
solo_games: dict[int, dict] = {}
timer_tasks: dict = {}
active_tournaments: dict[int, dict] = {}
pending_deck_uploads: dict[int, dict] = {}

WEB_BASE = os.environ.get("WEB_BASE_URL", "https://evilapples4tg.com")


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


def vote_keyboard(submissions: list[tuple], chat_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for i, (_, card) in enumerate(submissions):
        label = f"Card {i+1}: " + (card[:50] + "…" if len(card) > 50 else card)
        buttons.append([InlineKeyboardButton(label, callback_data=f"vote_{chat_id}_{i}")])
    return InlineKeyboardMarkup(buttons)


def next_round_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Next Round", callback_data="deal_next")
    ]])


def tournament_join_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Join Tournament", callback_data=f"tjoin_{tournament_id}")
    ]])


def tournament_card_keyboard(hand: list[str], tournament_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for i, card in enumerate(hand):
        label = card[:60] + "…" if len(card) > 60 else card
        buttons.append([InlineKeyboardButton(label, callback_data=f"tsub_{tournament_id}_{i}")])
    return InlineKeyboardMarkup(buttons)


def tournament_judge_keyboard(submissions: list[tuple], tournament_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for i, (_, card) in enumerate(submissions):
        label = f"Card {i+1}: " + (card[:50] + "…" if len(card) > 50 else card)
        buttons.append([InlineKeyboardButton(label, callback_data=f"tjudge_{tournament_id}_{i}")])
    return InlineKeyboardMarkup(buttons)


def tournament_hand_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🃏 View My Hand", callback_data=f"tviewhand_{tournament_id}")
    ]])


def deck_picker_keyboard(decks: list[dict], chat_id: int, selected: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    for deck in decks:
        check = "✅ " if deck["id"] in selected else ""
        label = f"{check}{deck['name']} ({deck['answer_count']} cards)"[:60]
        buttons.append([InlineKeyboardButton(label, callback_data=f"dpick_{chat_id}_{deck['id']}")])
    buttons.append([
        InlineKeyboardButton("▶️ Start with selected decks", callback_data=f"dstart_{chat_id}"),
        InlineKeyboardButton("⏭ Skip — use default cards", callback_data=f"dskip_{chat_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


def store_listing_keyboard(listings: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    PAGE_SIZE = 5
    start = page * PAGE_SIZE
    chunk = listings[start:start + PAGE_SIZE]
    buttons = []
    for item in chunk:
        price = f"${item['price_cents'] / 100:.2f}"
        star = "⭐ " if item.get("featured") else ""
        buttons.append([InlineKeyboardButton(
            f"{star}{item['name']} — {price}",
            callback_data=f"sdetail_{item['id']}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"spage_{page - 1}"))
    if start + PAGE_SIZE < len(listings):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"spage_{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    await update.message.reply_text(
        "😈 *Evil Apples for Telegram*\n\n"
        "The dark comedy card game — right in your Telegram groups\\.\n\n"
        "👉 *Add me to a group* to start playing with friends\\!\n"
        "👉 Or try /solo right here to play vs AI opponents\\.\n\n"
        "*Quick Commands:*\n"
        "/startgame — Start a group game\n"
        "/solo — Play solo vs AI\n"
        "/leaderboard — Top players\n"
        "/help — All commands\n\n"
        f"🌐 [Visit our website]({WEB_BASE.replace('.', '\\.')}) for leaderboards, stats & more\\!",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "➕ Add to a Group",
                url=f"https://t.me/{bot_username}?startgroup=true"
            )],
        ]),
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "😈 *Evil Apples — Commands*\n\n"
        "*Group Game:*\n"
        "`/startgame` — Start a game \\(friend mode\\)\n"
        "`/startgame swap` — Start swap mode \\(5\\+ players\\)\n"
        "`/startgame wildcard` — Write your own answers\n"
        "`/join` — Join the game\n"
        "`/deal` — Deal a new round\n"
        "`/vote` — Show voting options\n"
        "`/score` — Show current scores\n"
        "`/endgame` — End the game \\(host only\\)\n\n"
        "*Tournaments:*\n"
        "`/tournament create <name> [rounds]` — Create a tournament\n"
        "`/tournament join` — Join the open tournament\n"
        "`/tournament start` — Start \\(min 3 players\\)\n"
        "`/tournament status` — View standings\n"
        "`/tournament end` — End early \\(creator only\\)\n\n"
        "*Custom Decks:*\n"
        "`/decks` — View your custom decks\n"
        "`/deck upload <name>` — Upload a CSV deck\n"
        "`/deck delete <name>` — Delete a deck\n"
        "`/store` — Browse purchasable card packs\n\n"
        "*Solo & Premium:*\n"
        "`/solo` — Play vs 3 AI opponents\n"
        "`/premium` — View Evil Apples Pro features\n"
        "`/leaderboard` — Top players\n\n"
        "*How to play:*\n"
        "A prompt card is dealt\\. Everyone submits their funniest answer\\. "
        "Then everyone votes for the best card\\. First to 7 points wins\\!",
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

    group_premium = False
    try:
        group_premium = await is_premium_group(chat_id)
    except Exception:
        pass

    if group_premium:
        try:
            decks = await get_tg_user_decks(player_id)
        except Exception:
            decks = []

        if decks:
            active_games[chat_id]["_available_decks"] = decks
            active_games[chat_id]["selected_deck_ids"] = []
            await update.message.reply_text(
                f"🃏 *Custom Decks Available!*\n\n"
                f"You have {len(decks)} custom deck(s). Pick up to 3 to use in this game, or skip to use default AI-generated cards:",
                parse_mode="Markdown",
                reply_markup=deck_picker_keyboard(decks, chat_id, [])
            )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    game = active_games.get(chat_id)

    if not game:
        await update.message.reply_text("No active game. Use /startgame to start one!")
        return

    if game["state"] not in ("waiting_for_players", "finished"):
        await update.message.reply_text(
            "A round is in progress — you can join after this round finishes. Hang tight!"
        )
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

    if game["state"] not in ("waiting_for_players", "finished"):
        await query.answer(
            "A round is in progress — you can join after it finishes!",
            show_alert=True,
        )
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

    playing_ids = list(game["players"].keys())
    game["judge_id"] = None
    game["use_ai_judge"] = False

    game["state"] = "dealing"
    game["submissions"] = {}
    game["votes"] = {}
    game["round_id"] = game.get("round_id", 0) + 1
    game["playing_ids"] = playing_ids

    is_wildcard = (mode == "wildcard")

    try:
        prompt = await generate_prompt()
        game["prompt"] = prompt

        if is_wildcard:
            game["hands"] = {}
        else:
            deck_ids = game.get("active_deck_ids", [])
            if deck_ids:
                try:
                    deck_hands = await _deal_deck_aware_hands(playing_ids, deck_ids)
                    game["hands"] = deck_hands
                except Exception:
                    hands = deal_default_hands(len(playing_ids), HAND_SIZE)
                    game["hands"] = {pid: hand for pid, hand in zip(playing_ids, hands)}
            else:
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

        if is_wildcard:
            await bot.send_message(
                chat_id,
                f"🟣 *PROMPT CARD*\n\n"
                f"*{prompt_display}*\n\n"
                f"✏️ *WILDCARD MODE* — Type your answer in the chat\\!\n"
                f"Just type your answer as a message\\.\n\n"
                f"*Scores:*\n{current_scores}",
                parse_mode="MarkdownV2",
            )
        else:
            hand_preview = ""
            for pid in playing_ids:
                hand = game["hands"].get(pid, [])
                if hand:
                    cards_list = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(hand))
                    try:
                        await bot.send_message(
                            int(pid),
                            f"🃏 *Your Hand*\n\n"
                            f"Prompt: _{format_prompt(game['prompt'])}_\n\n"
                            f"{cards_list}\n\n"
                            f"Reply with the card number (1-{len(hand)}) in the group chat, "
                            f"or tap the button in the group to pick:",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

            await bot.send_message(
                chat_id,
                f"🟣 *PROMPT CARD*\n\n"
                f"*{prompt_display}*\n\n"
                f"Everyone plays\\! Reply with your card number \\(1\\-{HAND_SIZE}\\) "
                f"or tap the button to view your hand\\.\n"
                f"After submissions, everyone votes for the best card\\!\n\n"
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

    game["state"] = "voting"
    submissions = list(game["submissions"].items())
    random.shuffle(submissions)
    game["_shuffled_submissions"] = submissions
    game["votes"] = {}

    sub_text = "\n".join(
        f"🃏 Card {i+1}: {card}" for i, (_, card) in enumerate(submissions)
    )

    await bot.send_message(
        chat_id,
        f"⚖️ All cards are in! Time to vote!\n\n"
        f"Prompt: *{format_prompt(game['prompt'])}*\n\n"
        f"{sub_text}\n\n"
        f"Everyone vote for the funniest card! "
        f"_(You can't vote for your own card. 90 seconds to vote or Dr. Evil decides!)_",
        parse_mode="Markdown",
        reply_markup=vote_keyboard(submissions, chat_id)
    )

    round_id = game.get("round_id", 0)

    async def vote_timeout_task():
        try:
            await asyncio.sleep(90)
            g = active_games.get(chat_id)
            if g and g.get("state") == "voting" and g.get("round_id") == round_id:
                if g.get("votes"):
                    voted = len(g["votes"])
                    total = len(g.get("players", {}))
                    await bot.send_message(
                        chat_id,
                        f"⏰ Time's up! Only {voted}/{total} players voted — tallying what we have..."
                    )
                    await _tally_votes(chat_id, bot, g)
                else:
                    await bot.send_message(
                        chat_id,
                        "⏰ Nobody voted in 90 seconds! Dr. Evil will pick the winner himself..."
                    )
                    await _ai_judge_pick(chat_id, bot, g)
        except asyncio.CancelledError:
            pass

    task_key = f"vote_{chat_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
    timer_tasks[task_key] = asyncio.create_task(vote_timeout_task())


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


async def cb_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    chat_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user
    player_id = str(user.id)
    game = active_games.get(chat_id)

    if not game or game["state"] != "voting":
        await query.answer("Voting is not active right now.", show_alert=True)
        return

    if player_id not in game.get("players", {}):
        await query.answer("You're not in this game!", show_alert=True)
        return

    if player_id in game.get("votes", {}):
        await query.answer("You already voted!", show_alert=True)
        return

    submissions = game.get("_shuffled_submissions", [])
    if card_idx >= len(submissions):
        await query.answer("Invalid card.", show_alert=True)
        return

    voted_for_id = submissions[card_idx][0]
    if voted_for_id == player_id:
        await query.answer("You can't vote for your own card!", show_alert=True)
        return

    game["votes"][player_id] = card_idx
    await query.answer(f"Vote recorded for Card {card_idx + 1}! ✅")

    voted_count = len(game["votes"])
    eligible_voters = len(game.get("players", {}))
    try:
        await context.bot.send_message(
            chat_id,
            f"🗳️ {user.full_name} voted! ({voted_count}/{eligible_voters})"
        )
    except Exception:
        pass

    if voted_count >= eligible_voters:
        task_key = f"vote_{chat_id}"
        if task_key in timer_tasks:
            timer_tasks[task_key].cancel()
            timer_tasks.pop(task_key, None)
        await _tally_votes(chat_id, context.bot, game)


async def _tally_votes(chat_id: int, bot, game: dict):
    submissions = game.get("_shuffled_submissions", list(game["submissions"].items()))
    votes = game.get("votes", {})

    vote_counts = {}
    for voter_id, card_idx in votes.items():
        vote_counts[card_idx] = vote_counts.get(card_idx, 0) + 1

    if not vote_counts:
        await _ai_judge_pick(chat_id, bot, game)
        return

    max_votes = max(vote_counts.values())
    top_indices = [idx for idx, count in vote_counts.items() if count == max_votes]

    if len(top_indices) > 1:
        winning_idx = random.choice(top_indices)
    else:
        winning_idx = top_indices[0]

    winner_id, winning_card = submissions[winning_idx]
    winner_name = game["players"].get(winner_id, "Unknown")

    vote_summary = "\n".join(
        f"  Card {i+1}: {vote_counts.get(i, 0)} vote(s)" for i in range(len(submissions))
    )

    roast = ""
    try:
        card_texts = [card for _, card in submissions]
        if random.random() < 0.4:
            roast = await dr_evil_roast(game["prompt"], card_texts)
    except Exception:
        pass

    await _announce_winner(
        chat_id, bot, game, winner_id, winner_name, winning_card,
        roast=roast, vote_summary=vote_summary
    )


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

    ai_reasons = [
        f"Dr. Evil chose Card {chosen_index + 1} — \"{winning_card}\" paired perfectly with the prompt.",
        f"Dr. Evil picked \"{winning_card}\" for its dark comedic genius.",
        f"After careful deliberation, Dr. Evil deemed \"{winning_card}\" the most delightfully twisted answer.",
        f"Dr. Evil selected Card {chosen_index + 1} — the shock value of \"{winning_card}\" was simply irresistible.",
    ]
    ai_rationale = random.choice(ai_reasons)

    roast = ""
    try:
        roast = await dr_evil_roast(prompt, card_texts)
    except Exception:
        pass

    if not roast:
        roast = ai_rationale

    await _announce_winner(chat_id, bot, game, winner_id, winner_name, winning_card, roast=roast)


async def _announce_winner(
    chat_id: int, bot, game: dict,
    winner_id: str, winner_name: str, winning_card: str,
    roast: str = "", vote_summary: str = ""
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
    if vote_summary:
        msg += f"\n\n🗳️ *Votes:*\n{vote_summary}"
    if roast:
        msg += f"\n\n😈 Dr. Evil says: _{roast}_"
    msg += f"\n\n*Scores:*\n{current_scores}"

    game["state"] = "finished"

    if player_score >= win_score:
        game["state"] = "game_over"
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
        standings = "\n".join(
            f"  {'🥇' if i == 0 else '🥈' if i == 1 else '🥉' if i == 2 else '  '} "
            f"{game['players'].get(pid, pid)}: {pts} pts"
            for i, (pid, pts) in enumerate(sorted_scores)
        )
        msg += (
            f"\n\n🎉 *GAME OVER! {winner_name} wins with {player_score} points!*\n\n"
            f"*Final Standings:*\n{standings}\n\n"
            f"Start a new game with /startgame"
        )
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

    for key in [chat_id, f"judge_{chat_id}", f"vote_{chat_id}"]:
        task = timer_tasks.pop(key, None)
        if task:
            task.cancel()

    scores = game.get("scores", {})
    players = game.get("players", {})

    if scores and any(v > 0 for v in scores.values()):
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
        winner_id, winner_pts = sorted_scores[0]
        winner_name = players.get(winner_id, "Unknown")

        standings = "\n".join(
            f"  {'🥇' if i == 0 else '🥈' if i == 1 else '🥉' if i == 2 else '  '} "
            f"{players.get(pid, pid)}: {pts} pts"
            for i, (pid, pts) in enumerate(sorted_scores)
        )

        await update.message.reply_text(
            f"🏁 *Game Over!*\n\n"
            f"🏆 *Winner: {winner_name}* with {winner_pts} points!\n\n"
            f"*Final Standings:*\n{standings}\n\n"
            f"Start a new game with /startgame!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Game ended — no rounds were played.\nStart a new one with /startgame!"
        )

    active_games.pop(chat_id, None)


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


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_scores(update, context)


async def handle_text_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    game = active_games.get(chat_id)
    if not game:
        return
    if game.get("state") != "submitting":
        return

    user = update.effective_user
    player_id = str(user.id)

    if player_id not in game.get("playing_ids", []):
        return
    if player_id in game.get("submissions", {}):
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    is_wildcard = game.get("game_mode") == "wildcard"

    if is_wildcard:
        if len(text) > 200:
            text = text[:200]
        game["submissions"][player_id] = text
    else:
        try:
            card_num = int(text)
        except ValueError:
            return

        hand = game["hands"].get(player_id, [])
        if not hand:
            return
        if card_num < 1 or card_num > len(hand):
            await update.message.reply_text(
                f"Pick a number between 1 and {len(hand)}."
            )
            return

        card = hand[card_num - 1]
        game["submissions"][player_id] = card

    submitted = len(game["submissions"])
    total = len(game.get("playing_ids", []))

    await update.message.reply_text(
        f"✅ Answer submitted! ({submitted}/{total})"
    )

    await _check_all_submitted(chat_id, context.bot, game)


async def cmd_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games.get(chat_id)
    if not game:
        await update.message.reply_text("No active game.")
        return
    if game.get("state") != "voting":
        await update.message.reply_text(
            "No voting phase active right now. Submit your cards first!"
        )
        return

    submissions = game.get("_shuffled_submissions", [])
    if not submissions:
        await update.message.reply_text("No submissions to vote on.")
        return

    sub_text = "\n".join(
        f"🃏 Card {i+1}: {card}" for i, (_, card) in enumerate(submissions)
    )
    await update.message.reply_text(
        f"🗳️ *Vote for the best card!*\n\n"
        f"Prompt: *{format_prompt(game['prompt'])}*\n\n"
        f"{sub_text}\n\n"
        f"Tap a card below to cast your vote:",
        parse_mode="Markdown",
        reply_markup=vote_keyboard(submissions, chat_id)
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


# ─── Tournament commands (Task #8) ───────────────────────────────────────────

async def cmd_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("Tournaments are group features! Add me to a group first.")
        return

    args = context.args
    sub = args[0].lower() if args else "help"

    if sub == "create":
        await _tournament_create(update, context)
    elif sub == "join":
        await _tournament_join_cmd(update, context)
    elif sub == "start":
        await _tournament_start(update, context)
    elif sub == "status":
        await _tournament_status(update, context)
    elif sub == "end":
        await _tournament_end(update, context)
    else:
        await update.message.reply_text(
            "🏆 *Tournament Commands:*\n\n"
            "`/tournament create <name> [rounds]` — Create a tournament\n"
            "`/tournament join` — Join the open tournament\n"
            "`/tournament start` — Start the tournament (creator only, min 3 players)\n"
            "`/tournament status` — View current standings\n"
            "`/tournament end` — End the tournament early (creator only)",
            parse_mode="Markdown"
        )


async def _tournament_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    args = context.args[1:] if len(context.args) > 1 else []

    existing = await get_active_tg_tournament(chat_id)
    if existing:
        await update.message.reply_text(
            f"There's already an active tournament: *{existing['name']}*\n"
            f"Use `/tournament end` to cancel it first.",
            parse_mode="Markdown"
        )
        return

    if not args:
        await update.message.reply_text(
            "Usage: `/tournament create <name> [rounds]`\nExample: `/tournament create Evil Cup 7`",
            parse_mode="Markdown"
        )
        return

    rounds_total = 5
    name_parts = args
    if args[-1].isdigit():
        rounds_total = max(1, min(20, int(args[-1])))
        name_parts = args[:-1]

    name = " ".join(name_parts)[:80]
    if not name:
        await update.message.reply_text("Please give your tournament a name!")
        return

    try:
        tournament_id = await create_tg_tournament(
            name, chat_id, str(user.id), user.full_name, rounds_total
        )
        active_tournaments[chat_id] = {
            "id": tournament_id,
            "name": name,
            "rounds_total": rounds_total,
            "current_round": 0,
            "creator_id": str(user.id),
            "state": "registration",
            "players": {},
            "round_hands": {},
            "round_submissions": {},
            "round_prompt": "",
            "judge_index": 0,
            "judge_id": None,
            "_shuffled_submissions": [],
        }
        t = active_tournaments[chat_id]
        t["players"][str(user.id)] = user.full_name

        await join_tg_tournament(tournament_id, str(user.id), user.full_name)

        await update.message.reply_text(
            f"🏆 *{name}* — Tournament Created!\n\n"
            f"Rounds: *{rounds_total}*\n"
            f"Host: *{user.full_name}*\n\n"
            f"Players can join below or type `/tournament join`\n"
            f"When ready, use `/tournament start` (min 3 players)",
            parse_mode="Markdown",
            reply_markup=tournament_join_keyboard(tournament_id)
        )
    except Exception as e:
        logger.error(f"Tournament create error: {e}")
        await update.message.reply_text(f"Error creating tournament: {e}")


async def _tournament_join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    t = active_tournaments.get(chat_id)

    if not t or t["state"] != "registration":
        db_t = await get_active_tg_tournament(chat_id)
        if not db_t or db_t["status"] != "registration":
            await update.message.reply_text("No open tournament to join. Create one with `/tournament create <name>`", parse_mode="Markdown")
            return
        if chat_id not in active_tournaments:
            active_tournaments[chat_id] = {
                "id": db_t["id"], "name": db_t["name"],
                "rounds_total": db_t["rounds_total"], "current_round": db_t["current_round"],
                "creator_id": db_t["creator_id"], "state": "registration",
                "players": {}, "round_hands": {}, "round_submissions": {},
                "round_prompt": "", "judge_index": 0, "judge_id": None,
                "_shuffled_submissions": [],
            }
        t = active_tournaments[chat_id]

    player_id = str(user.id)
    if player_id in t["players"]:
        await update.message.reply_text(f"You're already registered, {user.first_name}!")
        return

    joined = await join_tg_tournament(t["id"], player_id, user.full_name)
    if joined:
        t["players"][player_id] = user.full_name
        count = len(t["players"])
        await update.message.reply_text(
            f"✅ *{user.full_name}* joined the tournament! ({count} players registered)",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("You're already registered!")


async def cb_tournament_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tournament_id = int(query.data.split("_", 1)[1])
    user = query.from_user
    chat_id = query.message.chat_id
    t = active_tournaments.get(chat_id)

    if not t or t["id"] != tournament_id or t["state"] != "registration":
        await query.answer("Registration is closed!", show_alert=True)
        return

    player_id = str(user.id)
    if player_id in t["players"]:
        await query.answer("You're already registered!", show_alert=True)
        return

    joined = await join_tg_tournament(tournament_id, player_id, user.full_name)
    if joined:
        t["players"][player_id] = user.full_name
        count = len(t["players"])
        await context.bot.send_message(
            chat_id,
            f"✅ *{user.full_name}* joined the tournament! ({count} players registered)",
            parse_mode="Markdown"
        )


async def _tournament_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    t = active_tournaments.get(chat_id)

    if not t:
        await update.message.reply_text("No active tournament. Create one with `/tournament create <name>`", parse_mode="Markdown")
        return

    if t["state"] != "registration":
        await update.message.reply_text("The tournament has already started!")
        return

    if str(user.id) != t["creator_id"]:
        await update.message.reply_text("Only the tournament creator can start it!")
        return

    if len(t["players"]) < 3:
        await update.message.reply_text(
            f"Need at least 3 players to start! Currently: {len(t['players'])}.\n"
            f"More players can `/tournament join`.",
            parse_mode="Markdown"
        )
        return

    t["state"] = "active"
    t["current_round"] = 1
    await start_tg_tournament(t["id"])

    players_list = "\n".join(f"• {name}" for name in t["players"].values())
    await update.message.reply_text(
        f"🏆 *{t['name']}* — Starting Now!\n\n"
        f"*Players ({len(t['players'])}):\n{players_list}*\n\n"
        f"Round 1 of {t['rounds_total']} begins...",
        parse_mode="Markdown"
    )

    await _run_tournament_round(chat_id, context.bot, t)


async def _tournament_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    t = active_tournaments.get(chat_id)

    if not t:
        db_t = await get_active_tg_tournament(chat_id)
        if not db_t:
            await update.message.reply_text("No active tournament in this group.")
            return
        players = await get_tg_tournament_players(db_t["id"])
        standings = "\n".join(
            f"{i+1}. {p['discord_name']} — {p['points']} pts" for i, p in enumerate(players)
        )
        await update.message.reply_text(
            f"🏆 *{db_t['name']}*\n"
            f"Status: {db_t['status']} | Round {db_t['current_round']}/{db_t['rounds_total']}\n\n"
            f"*Standings:*\n{standings or 'No players yet'}",
            parse_mode="Markdown"
        )
        return

    players = await get_tg_tournament_players(t["id"])
    standings = "\n".join(
        f"{i+1}. {p['discord_name']} — {p['points']} pts ({p['rounds_won']} wins)"
        for i, p in enumerate(players)
    )
    await update.message.reply_text(
        f"🏆 *{t['name']}* — Round {t.get('current_round', 0)}/{t['rounds_total']}\n\n"
        f"*Standings:*\n{standings or 'No data yet'}",
        parse_mode="Markdown"
    )


async def _tournament_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    t = active_tournaments.get(chat_id)

    if not t:
        await update.message.reply_text("No active tournament to end.")
        return

    if str(user.id) != t["creator_id"]:
        await update.message.reply_text("Only the tournament creator can end it!")
        return

    await end_tg_tournament(t["id"])
    active_tournaments.pop(chat_id, None)
    timer_tasks.pop(f"tjudge_{chat_id}", None)

    await update.message.reply_text(
        f"Tournament *{t['name']}* has been ended.",
        parse_mode="Markdown"
    )


async def _run_tournament_round(chat_id: int, bot, t: dict):
    round_number = t["current_round"]
    player_ids = list(t["players"].keys())

    judge_idx = t.get("judge_index", 0) % len(player_ids)
    judge_id = player_ids[judge_idx]
    t["judge_index"] = (judge_idx + 1) % len(player_ids)
    t["judge_id"] = judge_id
    judge_name = t["players"][judge_id]

    playing_ids = [pid for pid in player_ids if pid != judge_id]
    t["round_submissions"] = {}
    t["round_prompt"] = ""
    t["_shuffled_submissions"] = []
    t["_playing_ids"] = playing_ids

    try:
        prompt = await generate_prompt()
        t["round_prompt"] = prompt

        hands = await asyncio.gather(*(generate_hand(HAND_SIZE) for _ in playing_ids))
        t["round_hands"] = {pid: list(hand) for pid, hand in zip(playing_ids, hands)}

        prompt_display = format_prompt(prompt)
        await bot.send_message(
            chat_id,
            f"🏆 *Tournament Round {round_number}/{t['rounds_total']}*\n\n"
            f"Prompt: *{prompt_display}*\n\n"
            f"⚖️ Judge: *{judge_name}*\n\n"
            f"Tap below to view your hand and submit a card!",
            parse_mode="Markdown",
            reply_markup=tournament_hand_keyboard(t["id"])
        )

    except Exception as e:
        logger.error(f"Tournament round error: {e}")
        active_tournaments.pop(chat_id, None)
        await bot.send_message(chat_id, f"Error starting round: {e}")


async def cb_tournament_view_hand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tournament_id = int(query.data.split("_", 1)[1])
    user = query.from_user
    player_id = str(user.id)

    chat_id = query.message.chat_id
    t = active_tournaments.get(chat_id)

    if not t or t["id"] != tournament_id:
        await query.answer("No active tournament round.", show_alert=True)
        return

    if player_id == t.get("judge_id"):
        await query.answer("You're the judge this round — wait for submissions!", show_alert=True)
        return

    if player_id not in t.get("_playing_ids", []):
        await query.answer("You're not playing this round.", show_alert=True)
        return

    if player_id in t.get("round_submissions", {}):
        await query.answer("You already submitted a card!", show_alert=True)
        return

    hand = t["round_hands"].get(player_id, [])
    if not hand:
        await query.answer("No cards in your hand!", show_alert=True)
        return

    try:
        await context.bot.send_message(
            user.id,
            f"🃏 *Your Tournament Hand*\n\n"
            f"Prompt: _{format_prompt(t['round_prompt'])}_\n\n"
            f"Pick a card:",
            parse_mode="Markdown",
            reply_markup=tournament_card_keyboard(hand, tournament_id)
        )
    except (Forbidden, BadRequest):
        await query.answer(
            "⚠️ I can't DM you! Please start a private chat with me first.",
            show_alert=True
        )


async def cb_tournament_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Card submitted! ✅")

    parts = query.data.split("_")
    tournament_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user
    player_id = str(user.id)

    chat_id = query.message.chat_id
    t = active_tournaments.get(chat_id)

    if not t or t["id"] != tournament_id:
        return

    if player_id not in t.get("_playing_ids", []):
        return

    if player_id in t.get("round_submissions", {}):
        await query.answer("You already submitted!", show_alert=True)
        return

    hand = t["round_hands"].get(player_id, [])
    if card_idx >= len(hand):
        return

    card = hand[card_idx]
    t["round_submissions"][player_id] = card

    await query.edit_message_text(
        f"✅ *Card submitted!*\n\nYou played: _{card}_\n\nWaiting for others...",
        parse_mode="Markdown"
    )

    submitted = len(t["round_submissions"])
    total = len(t.get("_playing_ids", []))
    try:
        await context.bot.send_message(
            chat_id, f"✅ {user.full_name} submitted their card. ({submitted}/{total})"
        )
    except Exception:
        pass

    await _check_all_tournament_submitted(chat_id, context.bot, t)


async def _check_all_tournament_submitted(chat_id: int, bot, t: dict):
    playing_ids = t.get("_playing_ids", [])
    if len(t["round_submissions"]) < len(playing_ids):
        return

    task_key = f"tjudge_{chat_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
        timer_tasks.pop(task_key, None)

    submissions = list(t["round_submissions"].items())
    random.shuffle(submissions)
    t["_shuffled_submissions"] = submissions

    judge_id = t.get("judge_id")
    judge_name = t["players"].get(judge_id, "Judge")

    sub_text = "\n".join(
        f"🃏 Card {i+1}: {card}" for i, (_, card) in enumerate(submissions)
    )

    await bot.send_message(
        chat_id,
        f"⚖️ *All cards submitted!*\n\n"
        f"Prompt: *{format_prompt(t['round_prompt'])}*\n\n"
        f"{sub_text}\n\n"
        f"*{judge_name}*, pick the winner!\n"
        f"_(2 minutes or Dr. Evil steps in)_",
        parse_mode="Markdown",
        reply_markup=tournament_judge_keyboard(submissions, t["id"])
    )

    round_id = t.get("current_round", 0)

    async def judge_timeout():
        try:
            await asyncio.sleep(120)
            current_t = active_tournaments.get(chat_id)
            if current_t and current_t.get("current_round") == round_id and current_t.get("_shuffled_submissions"):
                await bot.send_message(chat_id, "⏰ Judge took too long! Dr. Evil steps in...")
                await _tournament_ai_judge(chat_id, bot, current_t)
        except asyncio.CancelledError:
            pass

    timer_tasks[task_key] = asyncio.create_task(judge_timeout())


async def cb_tournament_judge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    tournament_id = int(parts[1])
    card_idx = int(parts[2])
    user = query.from_user
    player_id = str(user.id)

    chat_id = query.message.chat_id
    t = active_tournaments.get(chat_id)

    if not t or t["id"] != tournament_id:
        return

    if player_id != t.get("judge_id"):
        await query.answer("Only the judge can pick the winner!", show_alert=True)
        return

    task_key = f"tjudge_{chat_id}"
    if task_key in timer_tasks:
        timer_tasks[task_key].cancel()
        timer_tasks.pop(task_key, None)

    submissions = t.get("_shuffled_submissions", [])
    if card_idx >= len(submissions):
        return

    winner_id, winning_card = submissions[card_idx]
    winner_name = t["players"].get(winner_id, "Unknown")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _announce_tournament_round_winner(chat_id, context.bot, t, winner_id, winner_name, winning_card)


async def _tournament_ai_judge(chat_id: int, bot, t: dict):
    submissions = t.get("_shuffled_submissions", [])
    if not submissions:
        return
    card_texts = [card for _, card in submissions]
    try:
        chosen_idx = await ai_judge(t["round_prompt"], card_texts)
    except Exception:
        chosen_idx = random.randint(0, len(submissions) - 1)

    winner_id, winning_card = submissions[chosen_idx]
    winner_name = t["players"].get(winner_id, "Unknown")
    await _announce_tournament_round_winner(chat_id, bot, t, winner_id, winner_name, winning_card)


async def _announce_tournament_round_winner(chat_id: int, bot, t: dict,
                                             winner_id: str, winner_name: str, winning_card: str):
    round_number = t["current_round"]
    prompt = t["round_prompt"]

    slug = slugify(f"t-{t['id']}-r{round_number}-{random.randint(100, 999)}")
    try:
        await save_round(prompt, winning_card, winner_id, winner_name, slug)
    except Exception as e:
        logger.error(f"Tournament save_round error: {e}")

    try:
        is_final = await advance_tg_tournament_round(
            t["id"], round_number, prompt, winning_card, winner_id, winner_name
        )
    except Exception as e:
        logger.error(f"advance_tg_tournament_round error: {e}")
        is_final = round_number >= t["rounds_total"]

    players = await get_tg_tournament_players(t["id"])
    standings = "\n".join(
        f"{'👑 ' if p['discord_id'] == f'tg_{winner_id}' else ''}"
        f"{i+1}. {p['discord_name']} — {p['points']} pts"
        for i, p in enumerate(players)
    )

    await bot.send_message(
        chat_id,
        f"🏆 *Round {round_number} Winner: {winner_name}!*\n\n"
        f"Prompt: _{format_prompt(prompt)}_\n"
        f"Winning card: *{winning_card}*\n\n"
        f"*Standings:*\n{standings}",
        parse_mode="Markdown"
    )

    if is_final:
        top = players[0] if players else None
        champ = top["discord_name"] if top else winner_name
        await bot.send_message(
            chat_id,
            f"🎉 *Tournament Over!*\n\n"
            f"🥇 Champion: *{champ}*\n\n"
            f"Thanks for playing *{t['name']}*!\n"
            f"Start a new one with `/tournament create <name>`",
            parse_mode="Markdown"
        )
        active_tournaments.pop(chat_id, None)
    else:
        t["current_round"] += 1
        await asyncio.sleep(3)
        await _run_tournament_round(chat_id, bot, t)


# ─── Store, Decks & Premium commands (Task #9) ───────────────────────────────

async def cmd_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        listings = await get_tg_store_listings()
    except Exception as e:
        await update.message.reply_text(f"Error loading store: {e}")
        return

    if not listings:
        await update.message.reply_text(
            "🛒 *Evil Apples Store*\n\nNo decks available yet. Check back soon!",
            parse_mode="Markdown"
        )
        return

    context.user_data["store_listings"] = listings
    await update.message.reply_text(
        f"🛒 *Evil Apples Store*\n\n"
        f"{len(listings)} deck{'s' if len(listings) != 1 else ''} available.\n"
        f"Tap a deck to see details and buy:",
        parse_mode="Markdown",
        reply_markup=store_listing_keyboard(listings, page=0)
    )


async def cb_store_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[1])
    listings = context.user_data.get("store_listings")
    if not listings:
        listings = await get_tg_store_listings()
        context.user_data["store_listings"] = listings
    await query.edit_message_reply_markup(
        reply_markup=store_listing_keyboard(listings, page=page)
    )


async def cb_store_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listing_id = int(query.data.split("_")[1])
    user = query.from_user

    try:
        item = await get_tg_store_listing(listing_id)
    except Exception:
        item = None

    if not item:
        await query.answer("Item not found!", show_alert=True)
        return

    price = f"${item['price_cents'] / 100:.2f}"
    buy_url = f"{WEB_BASE}/store?platform=telegram&user_id=tg_{user.id}"

    await query.edit_message_text(
        f"🃏 *{item['name']}*\n\n"
        f"{item.get('description', '')}\n\n"
        f"Price: *{price}*\n\n"
        f"To purchase, visit the store on the web:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Buy on Web Store", url=buy_url)
        ], [
            InlineKeyboardButton("◀️ Back to Store", callback_data="sback")
        ]])
    )


async def cb_store_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    listings = context.user_data.get("store_listings")
    if not listings:
        listings = await get_tg_store_listings()
        context.user_data["store_listings"] = listings
    await query.edit_message_text(
        f"🛒 *Evil Apples Store*\n\n{len(listings)} decks available:",
        parse_mode="Markdown",
        reply_markup=store_listing_keyboard(listings, page=0)
    )


async def cmd_decks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    is_group = update.effective_chat.type != "private"
    group_premium = False
    if is_group:
        try:
            group_premium = await is_premium_group(chat_id)
        except Exception:
            pass

    try:
        decks = await get_tg_user_decks(str(user.id))
    except Exception as e:
        await update.message.reply_text(f"Error loading decks: {e}")
        return

    msg = "🃏 *Available Decks*\n\n"
    msg += "📦 *Built-in Default* — Classic card pack (always available)\n"

    if not decks:
        msg += "\nNo custom decks yet.\n\n"
        if is_group and not group_premium:
            msg += "⭐ *Custom decks require Pro!* Upgrade with /premium to use custom decks in this group.\n\n"
        msg += (
            "• Use `/deck upload <name>` and attach a CSV file to create one\n"
            "• Visit the store with `/store` to browse purchasable decks"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    deck_lines = []
    for d in decks:
        deck_lines.append(
            f"• *{d['name']}* — {d['answer_count']} answers, {d['prompt_count']} prompts"
        )

    msg += f"\n*Your Custom Decks ({len(decks)}):*\n" + "\n".join(deck_lines)
    if is_group and not group_premium:
        msg += "\n\n⭐ *Custom decks require Pro for this group.* Use /premium to upgrade."
    else:
        msg += "\n\n_Start a game with /startgame to pick your custom decks!_"
    msg += "\n\nUse `/deck delete <name>` to remove a deck."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text(
            "🃏 *Deck Commands:*\n\n"
            "`/deck upload <name>` — Upload a CSV deck (attach file to message)\n"
            "`/deck delete <name>` — Delete one of your decks\n"
            "`/decks` — View all your decks\n\n"
            "*CSV Format:*\n"
            "Column 1: `prompt` or `answer`\n"
            "Column 2: card text\n"
            "Example:\n`prompt,Complete this sentence: ________`\n`answer,A winning personality`",
            parse_mode="Markdown"
        )
        return

    sub = args[0].lower()

    if sub == "upload":
        name_parts = args[1:]
        if not name_parts:
            await update.message.reply_text(
                "Usage: `/deck upload <name>` — attach a CSV file to the same message",
                parse_mode="Markdown"
            )
            return
        name = " ".join(name_parts)[:80]
        pending_deck_uploads[user.id] = {"name": name, "creator_name": user.full_name}
        await update.message.reply_text(
            f"✅ Ready to create deck *{name}*!\n\n"
            f"Now send a CSV file attachment. Format:\n"
            f"`prompt,Your prompt text here ________`\n"
            f"`answer,Your answer card text here`",
            parse_mode="Markdown"
        )

    elif sub == "delete":
        name_parts = args[1:]
        if not name_parts:
            await update.message.reply_text("Usage: `/deck delete <name>`", parse_mode="Markdown")
            return
        name = " ".join(name_parts)
        decks = await get_tg_user_decks(str(user.id))
        target = next((d for d in decks if d["name"].lower() == name.lower()), None)
        if not target:
            await update.message.reply_text(f"No deck named *{name}* found.", parse_mode="Markdown")
            return
        deleted = await delete_tg_deck(target["id"], str(user.id))
        if deleted:
            await update.message.reply_text(f"✅ Deck *{name}* deleted.", parse_mode="Markdown")
        else:
            await update.message.reply_text("Could not delete that deck.")
    else:
        await update.message.reply_text(
            "Unknown subcommand. Use `/deck upload <name>` or `/deck delete <name>`.",
            parse_mode="Markdown"
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pending = pending_deck_uploads.pop(user.id, None)
    if not pending:
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".csv"):
        pending_deck_uploads[user.id] = pending
        await update.message.reply_text("Please send a .csv file.")
        return

    name = pending["name"]
    creator_name = pending["creator_name"]

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode("utf-8", errors="replace")
    except Exception as e:
        await update.message.reply_text(f"Error downloading file: {e}")
        return

    cards = []
    errors = 0
    for line_num, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            errors += 1
            continue
        card_type = parts[0].strip().lower()
        card_text = parts[1].strip().strip('"')
        if card_type in ("prompt", "answer") and card_text:
            cards.append((card_type, card_text))
        else:
            errors += 1

    if not cards:
        await update.message.reply_text(
            "No valid cards found in the CSV. Check the format:\n"
            "`prompt,Your prompt text ________`\n`answer,Your answer text`",
            parse_mode="Markdown"
        )
        return

    try:
        deck_id = await create_tg_deck(name, "", str(user.id), creator_name)
        if deck_id is None:
            await update.message.reply_text(
                f"You already have a deck named *{name}*. Delete it first with `/deck delete {name}`.",
                parse_mode="Markdown"
            )
            return
        counts = await add_cards_to_tg_deck(deck_id, cards)
        await update.message.reply_text(
            f"✅ *Deck '{name}' created!*\n\n"
            f"📋 {counts['prompt_count']} prompts\n"
            f"🃏 {counts['answer_count']} answers\n"
            + (f"⚠️ {errors} lines skipped (bad format)\n" if errors else "") +
            f"\nUse `/startgame` to start a game and pick this deck!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Error saving deck: {e}")


async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    try:
        is_group_premium = await is_premium_group(chat_id) if update.effective_chat.type != "private" else False
    except Exception:
        is_group_premium = False

    upgrade_url = f"{WEB_BASE}/premium?platform=telegram&chat_id={chat_id}"

    if is_group_premium:
        await update.message.reply_text(
            "⭐ *This group has Evil Apples Pro!*\n\n"
            "You're enjoying all premium features:\n"
            "✅ Custom card decks in games\n"
            "✅ Ad-free experience\n"
            "✅ Priority AI judging",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "⭐ *Evil Apples Pro*\n\n"
        "*Free tier:*\n"
        "• Core game — unlimited rounds\n"
        "• AI judging & leaderboards\n"
        "• Tournaments\n\n"
        "*Pro — $19/month per group:*\n"
        "✅ Custom card decks in games\n"
        "✅ Ad-free announcements\n"
        "✅ Priority AI responses\n\n"
        "Upgrade at the link below:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Upgrade to Pro", url=upgrade_url)
        ]])
    )


# ─── Deck-aware startgame (Task #9) ──────────────────────────────────────────

async def _deal_deck_aware_hands(playing_ids: list[str], deck_ids: list[int]) -> dict[str, list[str]]:
    hands = {}
    for pid in playing_ids:
        if deck_ids:
            try:
                cards = await get_random_cards_from_decks(deck_ids, "answer", HAND_SIZE)
                if len(cards) >= 3:
                    hands[pid] = cards[:HAND_SIZE]
                    continue
            except Exception:
                pass
        hands[pid] = deal_default_hands(1, HAND_SIZE)[0]
    return hands


async def cb_deck_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    chat_id = int(parts[1])
    deck_id = int(parts[2])
    user = query.from_user

    game = active_games.get(chat_id)
    if not game or game.get("host_id") != str(user.id):
        await query.answer("Only the host can pick decks!", show_alert=True)
        return

    selected = game.setdefault("selected_deck_ids", [])
    if deck_id in selected:
        selected.remove(deck_id)
    else:
        if len(selected) >= 3:
            await query.answer("Max 3 decks! Deselect one first.", show_alert=True)
            return
        selected.append(deck_id)

    decks = game.get("_available_decks", [])
    await query.edit_message_reply_markup(
        reply_markup=deck_picker_keyboard(decks, chat_id, selected)
    )


async def cb_deck_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split("_")[1])
    user = query.from_user
    game = active_games.get(chat_id)

    if not game or game.get("host_id") != str(user.id):
        await query.answer("Only the host can start!", show_alert=True)
        return

    deck_ids = game.get("selected_deck_ids", [])
    game["active_deck_ids"] = deck_ids
    game.pop("_available_decks", None)

    if deck_ids:
        deck_names = [d["name"] for d in game.get("selected_deck_ids_meta", []) if d["id"] in deck_ids]
        await context.bot.send_message(
            chat_id,
            f"✅ Custom decks selected! Cards will be dealt from your chosen decks.\n\n"
            f"Use /deal to start the first round!"
        )
    else:
        await context.bot.send_message(chat_id, "Using default AI-generated cards. Use /deal to start!")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def cb_deck_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split("_")[1])
    user = query.from_user
    game = active_games.get(chat_id)

    if not game or game.get("host_id") != str(user.id):
        await query.answer("Only the host can do that!", show_alert=True)
        return

    game["active_deck_ids"] = []
    game.pop("_available_decks", None)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await context.bot.send_message(chat_id, "Using default AI-generated cards. Use /deal to start!")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram bot error: {context.error}", exc_info=context.error)


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("deal", cmd_deal))
    app.add_handler(CommandHandler("play", cmd_deal))
    app.add_handler(CommandHandler("endgame", cmd_endgame))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("vote", cmd_vote))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("solo", cmd_solo))
    app.add_handler(CommandHandler("solodeal", cmd_solo_deal))
    app.add_handler(CommandHandler("soloend", cmd_solo_end))

    app.add_handler(CommandHandler("tournament", cmd_tournament))
    app.add_handler(CommandHandler("store", cmd_store))
    app.add_handler(CommandHandler("decks", cmd_decks))
    app.add_handler(CommandHandler("deck", cmd_deck))
    app.add_handler(CommandHandler("premium", cmd_premium))

    app.add_handler(CallbackQueryHandler(cb_join, pattern=r"^join_"))
    app.add_handler(CallbackQueryHandler(cb_view_hand, pattern=r"^viewhand_"))
    app.add_handler(CallbackQueryHandler(cb_submit_card, pattern=r"^submit_"))
    app.add_handler(CallbackQueryHandler(cb_judge_pick, pattern=r"^judge_"))
    app.add_handler(CallbackQueryHandler(cb_vote, pattern=r"^vote_"))
    app.add_handler(CallbackQueryHandler(cb_deal_next, pattern=r"^deal_next$"))
    app.add_handler(CallbackQueryHandler(cb_solo_submit, pattern=r"^solosub_"))

    app.add_handler(CallbackQueryHandler(cb_tournament_join, pattern=r"^tjoin_"))
    app.add_handler(CallbackQueryHandler(cb_tournament_view_hand, pattern=r"^tviewhand_"))
    app.add_handler(CallbackQueryHandler(cb_tournament_submit, pattern=r"^tsub_"))
    app.add_handler(CallbackQueryHandler(cb_tournament_judge, pattern=r"^tjudge_"))

    app.add_handler(CallbackQueryHandler(cb_deck_pick, pattern=r"^dpick_"))
    app.add_handler(CallbackQueryHandler(cb_deck_start, pattern=r"^dstart_"))
    app.add_handler(CallbackQueryHandler(cb_deck_skip, pattern=r"^dskip_"))

    app.add_handler(CallbackQueryHandler(cb_store_detail, pattern=r"^sdetail_"))
    app.add_handler(CallbackQueryHandler(cb_store_page, pattern=r"^spage_"))
    app.add_handler(CallbackQueryHandler(cb_store_back, pattern=r"^sback$"))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_text_submission
    ))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.add_error_handler(error_handler)

    return app


async def set_commands(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Welcome message and quick start guide"),
        BotCommand("startgame", "Start a new game (friend, swap, or wildcard)"),
        BotCommand("join", "Join the current game"),
        BotCommand("play", "Deal cards and start the next round"),
        BotCommand("deal", "Deal cards and start the next round"),
        BotCommand("score", "Show current game scores"),
        BotCommand("vote", "Show voting options for the current round"),
        BotCommand("endgame", "End the current game (host only)"),
        BotCommand("leaderboard", "Show top players"),
        BotCommand("solo", "Play solo vs AI opponents"),
        BotCommand("solodeal", "Deal a solo round"),
        BotCommand("soloend", "End your solo game"),
        BotCommand("tournament", "Create or manage a group tournament"),
        BotCommand("store", "Browse the card deck store"),
        BotCommand("decks", "View your custom card decks"),
        BotCommand("deck", "Upload or delete a custom deck"),
        BotCommand("premium", "View or upgrade to Evil Apples Pro"),
        BotCommand("help", "Show all commands"),
    ])
