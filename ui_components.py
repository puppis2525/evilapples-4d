import discord
import random
import logging
from slugify import slugify
from db import save_round, save_game_session

logger = logging.getLogger("ui_components")


def format_prompt(prompt_text):
    formatted = prompt_text.replace("_", "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    return f"**{formatted}**"


def _sanitize_for_persist(data):
    clean = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, set):
            clean[k] = list(v)
        elif isinstance(v, dict):
            clean[k] = _sanitize_for_persist(v)
        else:
            clean[k] = v
    return clean


async def _persist_from_ui(game):
    try:
        from bot import active_games, solo_games
        for ch_id, g in active_games.items():
            if g is game:
                await save_game_session(f"ch_{ch_id}", "channel", _sanitize_for_persist(game))
                return
        for pid, g in solo_games.items():
            if g is game:
                await save_game_session(f"solo_{pid}", "solo", _sanitize_for_persist(game))
                return
    except Exception as e:
        logger.error(f"Failed to persist game state from UI: {e}")


class HandView(discord.ui.View):
    def __init__(self, game_session, mode="multiplayer", channel=None):
        super().__init__(timeout=300)
        self.game_session = game_session
        self.mode = mode
        self.channel = channel

    @discord.ui.button(label="View My Hand", style=discord.ButtonStyle.green, emoji="\U0001F0CF")
    async def view_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        player_id = str(interaction.user.id)

        if self.mode == "solo":
            if player_id != self.game_session.get("player_id"):
                await interaction.response.send_message("This isn't your game!", ephemeral=True)
                return
            if self.game_session.get("state") != "submitting":
                await interaction.response.send_message("No cards to show right now.", ephemeral=True)
                return
            hand = self.game_session.get("hand", [])
            prompt = self.game_session.get("prompt", "")
        else:
            if player_id not in self.game_session.get("players", {}):
                await interaction.response.send_message("You're not in this game!", ephemeral=True)
                return
            if player_id == self.game_session.get("judge_id") and not self.game_session.get("use_ai_judge"):
                await interaction.response.send_message("You're the judge this round! Wait for everyone to submit, then pick the winner.", ephemeral=True)
                return
            if self.game_session.get("state") != "submitting":
                await interaction.response.send_message("No cards to show right now.", ephemeral=True)
                return
            hand = self.game_session.get("hands", {}).get(player_id, [])
            prompt = self.game_session.get("prompt", "")

        if not hand:
            await interaction.response.send_message("No cards in your hand.", ephemeral=True)
            return

        hand_text = "\n".join(f"**{i+1}.** {card}" for i, card in enumerate(hand))

        has_exchanged = player_id in self.game_session.get("exchanged", set())
        has_submitted = (
            (self.mode == "solo" and self.game_session.get("submitted"))
            or (self.mode != "solo" and player_id in self.game_session.get("submissions", {}))
        )
        exchange_note = ""
        if has_submitted:
            exchange_note = "\n\n*You already submitted a card.*"
        elif has_exchanged:
            exchange_note = "\n\n*You already used your exchange this round.*"
        else:
            exchange_note = "\n\n*Use the Exchange button to swap up to 3 cards (once per round).*"

        embed = discord.Embed(
            title="Your Hand",
            description=f"{format_prompt(prompt)}\n\n{hand_text}{exchange_note}",
            color=discord.Color.dark_purple()
        )
        embed.set_footer(text="Click a numbered button to submit, or Exchange to swap cards!")

        game_channel = self.channel or interaction.channel
        submit_view = SubmitCardView(self.game_session, hand, self.mode, channel=game_channel, player_id=player_id)
        await interaction.response.send_message(embed=embed, view=submit_view, ephemeral=True)


class SubmitCardView(discord.ui.View):
    def __init__(self, game_session, hand, mode="multiplayer", channel=None, player_id=None):
        super().__init__(timeout=300)
        self.game_session = game_session
        self.hand = hand
        self.mode = mode
        self.channel = channel
        self.player_id = player_id

        for i, card in enumerate(hand):
            label = f"{i+1}"
            button = SubmitCardButton(index=i, label=label, card_text=card, view_ref=self)
            self.add_item(button)

        has_exchanged = player_id in game_session.get("exchanged", set())
        has_submitted = (
            (mode == "solo" and game_session.get("submitted"))
            or (mode != "solo" and player_id in game_session.get("submissions", {}))
        )
        exchange_btn = discord.ui.Button(
            label="Exchange",
            style=discord.ButtonStyle.secondary,
            emoji="\U0001F504",
            row=1,
            disabled=has_exchanged or has_submitted,
        )
        exchange_btn.callback = self.exchange_callback
        self.add_item(exchange_btn)

    async def exchange_callback(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        game = self.game_session

        if game.get("state") != "submitting":
            await interaction.response.send_message("Round is no longer accepting submissions.", ephemeral=True)
            return

        if player_id in game.get("exchanged", set()):
            await interaction.response.send_message("You already used your exchange this round!", ephemeral=True)
            return

        if self.mode == "solo" and game.get("submitted"):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return
        if self.mode != "solo" and player_id in game.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return

        if self.mode == "solo":
            hand = game.get("hand", [])
        else:
            hand = game.get("hands", {}).get(player_id, [])

        if not hand:
            await interaction.response.send_message("No cards to exchange.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=f"{i+1}. {card[:95]}",
                value=str(i),
            )
            for i, card in enumerate(hand)
        ]

        select = discord.ui.Select(
            placeholder="Pick up to 3 cards to exchange...",
            min_values=1,
            max_values=min(3, len(hand)),
            options=options,
        )

        exchange_view = ExchangeConfirmView(game, self.mode, self.channel, player_id, select)
        select.callback = exchange_view.on_select
        exchange_view.add_item(select)

        await interaction.response.edit_message(
            content="**Select up to 3 cards to exchange:**",
            embed=None,
            view=exchange_view,
        )


class ExchangeConfirmView(discord.ui.View):
    def __init__(self, game_session, mode, channel, player_id, select):
        super().__init__(timeout=60)
        self.game_session = game_session
        self.mode = mode
        self.channel = channel
        self.player_id = player_id

    async def on_select(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        game = self.game_session

        if game.get("state") != "submitting":
            await interaction.response.send_message("Round is no longer accepting submissions.", ephemeral=True)
            return

        if player_id in game.get("exchanged", set()):
            await interaction.response.send_message("You already used your exchange this round!", ephemeral=True)
            return

        if self.mode == "solo" and game.get("submitted"):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return
        if self.mode != "solo" and player_id in game.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return

        selected_indices = sorted([int(v) for v in interaction.data["values"]], reverse=True)

        if self.mode == "solo":
            hand = game.get("hand", [])
        else:
            hand = game.get("hands", {}).get(player_id, [])

        from api_client import deal_replacement_cards
        new_cards = deal_replacement_cards(len(selected_indices), exclude=hand)

        swapped_old = []
        for idx in selected_indices:
            if 0 <= idx < len(hand):
                swapped_old.append(hand[idx])

        for i, idx in enumerate(selected_indices):
            if 0 <= idx < len(hand) and i < len(new_cards):
                hand[idx] = new_cards[i]

        game.setdefault("exchanged", set()).add(player_id)
        await _persist_from_ui(game)

        hand_text = "\n".join(f"**{i+1}.** {card}" for i, card in enumerate(hand))
        swap_summary = f"Swapped {len(selected_indices)} card{'s' if len(selected_indices) > 1 else ''}."

        embed = discord.Embed(
            title="Your Hand (Updated)",
            description=f"{format_prompt(game.get('prompt', ''))}\n\n{hand_text}\n\n*{swap_summary} Exchange used — pick a card to submit!*",
            color=discord.Color.dark_purple()
        )
        embed.set_footer(text="Click a numbered button below to submit your card!")

        game_channel = self.channel or interaction.channel
        new_view = SubmitCardView(game, hand, self.mode, channel=game_channel, player_id=player_id)
        await interaction.response.edit_message(content=None, embed=embed, view=new_view)


class SubmitCardButton(discord.ui.Button):
    def __init__(self, index: int, label: str, card_text: str, view_ref):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index
        self.card_text = card_text
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        game = self.view_ref.game_session
        mode = self.view_ref.mode

        if mode == "solo":
            hand = game.get("hand", [])
            self.card_text = hand[self.index] if self.index < len(hand) else self.card_text

            if player_id != game.get("player_id"):
                await interaction.response.send_message("This isn't your game!", ephemeral=True)
                return
            if game.get("state") != "submitting":
                await interaction.response.send_message("Round is no longer accepting submissions.", ephemeral=True)
                return
            if game.get("submitted"):
                await interaction.response.send_message("You already submitted a card!", ephemeral=True)
                return

            game["submitted"] = True
            await _persist_from_ui(game)
            for item in self.view_ref.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Card submitted: **{self.card_text}**",
                embed=None,
                view=self.view_ref
            )

            try:
                from bot import finish_solo_round
                game_channel = self.view_ref.channel or interaction.channel
                await finish_solo_round(player_id, player_card=self.card_text, channel=game_channel)
            except Exception as e:
                print(f"Error calling finish_solo_round from button: {e}")
                import traceback
                traceback.print_exc()
                game_channel = self.view_ref.channel or interaction.channel
                if game_channel:
                    await game_channel.send("Something went wrong during judging. Type `!solo deal` to try again.")

        else:
            hand = game.get("hands", {}).get(player_id, [])
            self.card_text = hand[self.index] if self.index < len(hand) else self.card_text

            if player_id not in game.get("players", {}):
                await interaction.response.send_message("You're not in this game!", ephemeral=True)
                return
            if game.get("state") != "submitting":
                await interaction.response.send_message("Round is no longer accepting submissions.", ephemeral=True)
                return
            if player_id in game.get("submissions", {}):
                await interaction.response.send_message("You already submitted a card!", ephemeral=True)
                return

            game["submissions"][player_id] = self.card_text
            await _persist_from_ui(game)
            for item in self.view_ref.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Card submitted: **{self.card_text}**",
                embed=None,
                view=self.view_ref
            )

            channel = self.view_ref.channel
            if channel:
                card_num = len(game["submissions"])
                playing_ids = game.get("playing_ids", [pid for pid in game.get("players", {}) if pid != game.get("judge_id")])
                total = len(playing_ids)
                card_embed = discord.Embed(
                    description=f"## \U0001F0CF  {self.card_text}",
                    color=discord.Color.from_rgb(255, 255, 255)
                )
                card_embed.set_footer(text=f"Card {card_num} of {total}")
                await channel.send(embed=card_embed)

            from bot import check_all_submitted
            await check_all_submitted(game, interaction)


class JudgingView(discord.ui.View):
    def __init__(self, game_session, channel):
        super().__init__(timeout=120)
        self.game_session = game_session
        self.channel = channel
        self.submissions = list(game_session["submissions"].items())
        random.shuffle(self.submissions)

        for i, (player_id, card_text) in enumerate(self.submissions):
            button = CardButton(index=i, label=f"Card {i + 1}", view_ref=self)
            self.add_item(button)

    async def pick_winner(self, interaction: discord.Interaction, index: int):
        if self.game_session.get("state") == "finished":
            await interaction.response.send_message("Winner already picked!", ephemeral=True)
            return

        self.game_session["state"] = "finished"

        judge_task = self.game_session.pop("_judge_timeout_task", None)
        if judge_task and not judge_task.done():
            judge_task.cancel()

        winner_id, winning_card = self.submissions[index]
        winner = interaction.guild.get_member(int(winner_id))
        winner_name = winner.display_name if winner else "Unknown"

        prompt = self.game_session["prompt"]
        slug = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")

        await save_round(prompt, winning_card, str(winner_id), winner_name, slug)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        from bot import handle_round_winner
        game_over = await handle_round_winner(self.game_session, winner_id, winner_name, self.channel)

        scores = self.game_session.setdefault("scores", {})
        scores_text = " | ".join(
            f"{'**' if pid == winner_id else ''}{self.game_session['players'].get(pid, pid)}: {scores.get(pid, 0)}{'**' if pid == winner_id else ''}"
            for pid in sorted(scores, key=lambda p: -scores.get(p, 0))
        )

        embed = discord.Embed(
            title="\U0001F3C6 We Have a Winner!",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        embed.add_field(name="Prompt", value=format_prompt(prompt), inline=False)
        embed.add_field(name="Winning Card", value=winning_card, inline=False)
        embed.add_field(name="Winner", value=f"**{winner_name}**", inline=False)
        embed.add_field(name="Scores", value=scores_text, inline=False)
        embed.set_footer(text=f"Round saved as: /rounds/{slug}")

        from bot import maybe_append_ad
        await maybe_append_ad(embed, self.channel)

        if not game_over:
            await self.channel.send(embed=embed, view=PostRoundView())
            await _persist_from_ui(self.game_session)
        else:
            await self.channel.send(embed=embed)


class CardButton(discord.ui.Button):
    def __init__(self, index: int, label: str, view_ref):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        judge_id = self.view_ref.game_session.get("judge_id")
        if str(interaction.user.id) != str(judge_id):
            await interaction.response.send_message(
                "Only the Judge can pick the winner!", ephemeral=True
            )
            return

        await self.view_ref.pick_winner(interaction, self.index)


class TournamentHandView(discord.ui.View):
    def __init__(self, round_data, tournament_data, channel):
        super().__init__(timeout=300)
        self.round_data = round_data
        self.tournament_data = tournament_data
        self.channel = channel

    @discord.ui.button(label="View My Hand", style=discord.ButtonStyle.green, emoji="\U0001F0CF")
    async def view_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        player_id = str(interaction.user.id)

        if player_id not in self.round_data.get("players", {}):
            await interaction.response.send_message("You're not in this tournament round!", ephemeral=True)
            return

        hand = self.round_data.get("hands", {}).get(player_id, [])
        prompt = self.round_data.get("prompt", "")

        if not hand:
            await interaction.response.send_message("No cards in your hand.", ephemeral=True)
            return

        if player_id in self.round_data.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card this round!", ephemeral=True)
            return

        hand_text = "\n".join(f"**{i+1}.** {card}" for i, card in enumerate(hand))

        has_exchanged = player_id in self.round_data.get("exchanged", set())
        exchange_note = ""
        if has_exchanged:
            exchange_note = "\n\n*You already used your exchange this round.*"
        else:
            exchange_note = "\n\n*Use the Exchange button to swap up to 3 cards (once per round).*"

        embed = discord.Embed(
            title=f"Tournament: {self.tournament_data['name']}",
            description=f"{format_prompt(prompt)}\n\n{hand_text}{exchange_note}",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Click a numbered button to submit, or Exchange to swap cards!")

        submit_view = TournamentSubmitView(self.round_data, self.tournament_data, hand, self.channel, player_id=player_id)
        await interaction.response.send_message(embed=embed, view=submit_view, ephemeral=True)


class TournamentSubmitView(discord.ui.View):
    def __init__(self, round_data, tournament_data, hand, channel, player_id=None):
        super().__init__(timeout=300)
        self.round_data = round_data
        self.tournament_data = tournament_data
        self.hand = hand
        self.channel = channel
        self.player_id = player_id

        for i, card in enumerate(hand):
            button = TournamentSubmitButton(index=i, label=f"{i+1}", card_text=card, view_ref=self)
            self.add_item(button)

        has_exchanged = player_id in round_data.get("exchanged", set())
        has_submitted = player_id in round_data.get("submissions", {})
        exchange_btn = discord.ui.Button(
            label="Exchange",
            style=discord.ButtonStyle.secondary,
            emoji="\U0001F504",
            row=1,
            disabled=has_exchanged or has_submitted,
        )
        exchange_btn.callback = self.exchange_callback
        self.add_item(exchange_btn)

    async def exchange_callback(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        rd = self.round_data

        if player_id in rd.get("exchanged", set()):
            await interaction.response.send_message("You already used your exchange this round!", ephemeral=True)
            return

        if player_id in rd.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return

        hand = rd.get("hands", {}).get(player_id, [])
        if not hand:
            await interaction.response.send_message("No cards to exchange.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=f"{i+1}. {card[:95]}",
                value=str(i),
            )
            for i, card in enumerate(hand)
        ]

        select = discord.ui.Select(
            placeholder="Pick up to 3 cards to exchange...",
            min_values=1,
            max_values=min(3, len(hand)),
            options=options,
        )

        exchange_view = TournamentExchangeView(rd, self.tournament_data, self.channel, player_id, select)
        select.callback = exchange_view.on_select
        exchange_view.add_item(select)

        await interaction.response.edit_message(
            content="**Select up to 3 cards to exchange:**",
            embed=None,
            view=exchange_view,
        )


class TournamentExchangeView(discord.ui.View):
    def __init__(self, round_data, tournament_data, channel, player_id, select):
        super().__init__(timeout=60)
        self.round_data = round_data
        self.tournament_data = tournament_data
        self.channel = channel
        self.player_id = player_id

    async def on_select(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        rd = self.round_data

        if player_id in rd.get("exchanged", set()):
            await interaction.response.send_message("You already used your exchange!", ephemeral=True)
            return

        if player_id in rd.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return

        selected_indices = sorted([int(v) for v in interaction.data["values"]], reverse=True)

        hand = rd.get("hands", {}).get(player_id, [])

        from api_client import deal_replacement_cards
        new_cards = deal_replacement_cards(len(selected_indices), exclude=hand)

        for i, idx in enumerate(selected_indices):
            if 0 <= idx < len(hand) and i < len(new_cards):
                hand[idx] = new_cards[i]

        rd.setdefault("exchanged", set()).add(player_id)

        hand_text = "\n".join(f"**{i+1}.** {card}" for i, card in enumerate(hand))
        swap_summary = f"Swapped {len(selected_indices)} card{'s' if len(selected_indices) > 1 else ''}."

        embed = discord.Embed(
            title=f"Tournament: {self.tournament_data['name']} (Updated Hand)",
            description=f"{format_prompt(rd.get('prompt', ''))}\n\n{hand_text}\n\n*{swap_summary} Exchange used — pick a card to submit!*",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Click a numbered button below to submit your card!")

        new_view = TournamentSubmitView(rd, self.tournament_data, hand, self.channel, player_id=player_id)
        await interaction.response.edit_message(content=None, embed=embed, view=new_view)


class TournamentSubmitButton(discord.ui.Button):
    def __init__(self, index: int, label: str, card_text: str, view_ref):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index
        self.card_text = card_text
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)
        round_data = self.view_ref.round_data
        tournament_data = self.view_ref.tournament_data
        channel = self.view_ref.channel

        hand = round_data.get("hands", {}).get(player_id, [])
        self.card_text = hand[self.index] if self.index < len(hand) else self.card_text

        if player_id not in round_data.get("players", {}):
            await interaction.response.send_message("You're not in this tournament round!", ephemeral=True)
            return

        if player_id in round_data.get("submissions", {}):
            await interaction.response.send_message("You already submitted a card!", ephemeral=True)
            return

        round_data["submissions"][player_id] = self.card_text
        for item in self.view_ref.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Card submitted: **{self.card_text}**",
            embed=None,
            view=self.view_ref
        )

        if channel:
            card_num = len(round_data["submissions"])
            total = len(round_data.get("players", {}))
            card_embed = discord.Embed(
                description=f"## \U0001F0CF  {self.card_text}",
                color=discord.Color.from_rgb(255, 255, 255)
            )
            card_embed.set_footer(text=f"Card {card_num} of {total}")
            await channel.send(embed=card_embed)

        if len(round_data["submissions"]) == len(round_data["players"]):
            from tournament import TournamentJudgingView
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


class GameLobbyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.green, emoji="\u2705")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import active_games
        game = active_games.get(interaction.channel_id)
        if not game:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return
        player_id = str(interaction.user.id)
        if player_id in game["players"]:
            await interaction.response.send_message("You're already in the game!", ephemeral=True)
            return
        game["players"][player_id] = interaction.user.display_name
        if player_id not in game["all_player_ids"]:
            game["all_player_ids"].append(player_id)
        if player_id not in game.get("scores", {}):
            game.setdefault("scores", {})[player_id] = 0
        await interaction.response.send_message(
            f"**{interaction.user.display_name}** has joined! ({len(game['players'])} players)"
        )

        from db import get_user_deck_count, get_all_decks
        deck_count = await get_user_deck_count(player_id)
        if deck_count > 0:
            decks = await get_all_decks(discord_user_id=player_id)
            if decks:
                await interaction.channel.send(
                    f"**{interaction.user.display_name}**, you own card decks! Pick up to 3 to play with, or skip to use default cards.",
                    view=DeckSelectView(decks, player_id, interaction.channel_id)
                )

    @discord.ui.button(label="Deal", style=discord.ButtonStyle.primary, emoji="\U0001F0CF")
    async def deal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import active_games, bot
        game = active_games.get(interaction.channel_id)
        if not game:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return
        player_id = str(interaction.user.id)
        if player_id not in game["players"]:
            await interaction.response.send_message("You need to join the game first!", ephemeral=True)
            return
        if game["state"] not in ("waiting_for_players", "finished"):
            await interaction.response.send_message("A round is already in progress!", ephemeral=True)
            return
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        ctx.channel = interaction.channel
        await interaction.response.defer()
        deal_cmd = bot.get_command("deal")
        if deal_cmd:
            await ctx.invoke(deal_cmd)

    @discord.ui.button(label="End Game", style=discord.ButtonStyle.danger, emoji="\u274C")
    async def end_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import active_games, timer_tasks
        game = active_games.get(interaction.channel_id)
        if not game:
            await interaction.response.send_message("No active game to end.", ephemeral=True)
            return
        if str(interaction.user.id) != game.get("host_id"):
            await interaction.response.send_message("Only the game host can end the game!", ephemeral=True)
            return
        if interaction.channel_id in timer_tasks:
            timer_tasks[interaction.channel_id].cancel()
            del timer_tasks[interaction.channel_id]
        del active_games[interaction.channel_id]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.channel.send("Game ended. Start a new one with `!startgame`.")


class DeckSelectView(discord.ui.View):
    def __init__(self, decks, user_id, channel_id, callback_type="multiplayer"):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.channel_id = channel_id
        self.callback_type = callback_type

        options = [
            discord.SelectOption(
                label=d["name"][:100],
                value=str(d["id"]),
                description=f"{d['answer_count']} answers" + (f", {d['prompt_count']} prompts" if d['prompt_count'] > 0 else ""),
            )
            for d in decks[:25]
        ]

        select = discord.ui.Select(
            placeholder="Pick 1-3 decks to play with...",
            min_values=1,
            max_values=min(3, len(options)),
            options=options,
            custom_id="deck_select",
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This deck selector is for someone else!", ephemeral=True)
            return

        from bot import channel_decks
        selected_ids = [int(v) for v in interaction.data["values"]]

        from db import get_deck_by_id
        names = []
        verified_ids = []
        for did in selected_ids:
            d = await get_deck_by_id(did)
            if d and d.get("discord_user_id") == self.user_id:
                names.append(d["name"])
                verified_ids.append(did)
        selected_ids = verified_ids

        if not selected_ids:
            await interaction.response.send_message("None of those decks belong to you!", ephemeral=True)
            return

        if self.channel_id not in channel_decks:
            channel_decks[self.channel_id] = {}
        channel_decks[self.channel_id][self.user_id] = {
            "ids": selected_ids,
            "names": names,
        }

        for item in self.children:
            item.disabled = True
        player_name = interaction.user.display_name
        await interaction.response.edit_message(
            content=f"**{player_name}** selected deck{'s' if len(names) > 1 else ''}: {', '.join(names)}",
            view=self,
        )

    @discord.ui.button(label="Skip — Use Default Cards", style=discord.ButtonStyle.secondary, emoji="⏭️", row=1)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This deck selector is for someone else!", ephemeral=True)
            return

        from bot import channel_decks
        ch_decks = channel_decks.get(self.channel_id, {})
        ch_decks.pop(self.user_id, None)

        for item in self.children:
            item.disabled = True
        player_name = interaction.user.display_name
        await interaction.response.edit_message(
            content=f"**{player_name}** will use **default cards**.",
            view=self,
        )


class PostRoundView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Deal Next Round", style=discord.ButtonStyle.green, emoji="\U0001F0CF")
    async def deal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import active_games, bot
        game = active_games.get(interaction.channel_id)
        if not game:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return
        player_id = str(interaction.user.id)
        if player_id not in game["players"]:
            await interaction.response.send_message("You need to be in the game to deal!", ephemeral=True)
            return
        if game["state"] != "finished":
            await interaction.response.send_message("Can't deal right now — round still in progress!", ephemeral=True)
            return
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        ctx.channel = interaction.channel
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        deal_cmd = bot.get_command("deal")
        if deal_cmd:
            await ctx.invoke(deal_cmd)

    @discord.ui.button(label="End Game", style=discord.ButtonStyle.danger, emoji="\u274C")
    async def end_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import active_games, timer_tasks
        game = active_games.get(interaction.channel_id)
        if not game:
            await interaction.response.send_message("No active game to end.", ephemeral=True)
            return
        if str(interaction.user.id) != game.get("host_id"):
            await interaction.response.send_message("Only the game host can end the game!", ephemeral=True)
            return
        if interaction.channel_id in timer_tasks:
            timer_tasks[interaction.channel_id].cancel()
            del timer_tasks[interaction.channel_id]
        judge_task = game.pop("_judge_timeout_task", None)
        if judge_task and not judge_task.done():
            judge_task.cancel()
        del active_games[interaction.channel_id]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.channel.send("Game ended. Start a new one with `!startgame`.")


class SoloControlsView(discord.ui.View):
    def __init__(self, player_id):
        super().__init__(timeout=None)
        self.player_id = player_id

    @discord.ui.button(label="Deal", style=discord.ButtonStyle.green, emoji="\U0001F0CF")
    async def deal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.player_id:
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        from bot import bot
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        ctx.channel = interaction.channel
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        solo_cmd = bot.get_command("solo")
        if solo_cmd:
            deal_sub = solo_cmd.get_command("deal")
            if deal_sub:
                await ctx.invoke(deal_sub)

    @discord.ui.button(label="Score", style=discord.ButtonStyle.secondary, emoji="\U0001F4CA")
    async def score_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.player_id:
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        from bot import solo_games
        game = solo_games.get(int(self.player_id))
        if not game:
            await interaction.response.send_message("No active solo game.", ephemeral=True)
            return
        wins = game.get("wins", 0)
        losses = game.get("losses", 0)
        total = wins + losses
        embed = discord.Embed(
            title=f"\U0001F4CA Solo Score — Round {game.get('round_number', 0)}",
            description=f"**Wins:** {wins}\n**Losses:** {losses}\n**Total Rounds:** {total}",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Quit", style=discord.ButtonStyle.danger, emoji="\u274C")
    async def quit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.player_id:
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        from bot import solo_games
        game = solo_games.pop(int(self.player_id), None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        if game:
            wins = game.get("wins", 0)
            losses = game.get("losses", 0)
            await interaction.channel.send(f"Solo game ended! Final score: **{wins}W / {losses}L**")
        else:
            await interaction.channel.send("No active solo game to quit.")


class WildcardModal(discord.ui.Modal, title="Write Your Answer"):
    answer = discord.ui.TextInput(
        label="Your custom answer card",
        placeholder="Type your funniest, most outrageous answer...",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )

    def __init__(self, game_session, channel):
        super().__init__()
        self.game_session = game_session
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        player_id = str(interaction.user.id)

        if player_id not in self.game_session.get("players", {}):
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        if player_id == self.game_session.get("judge_id") and not self.game_session.get("use_ai_judge"):
            await interaction.response.send_message("You're the judge this round!", ephemeral=True)
            return

        if player_id in self.game_session.get("submissions", {}):
            await interaction.response.send_message("You already submitted an answer!", ephemeral=True)
            return

        card_text = self.answer.value.strip()
        if not card_text:
            await interaction.response.send_message("You can't submit an empty answer!", ephemeral=True)
            return

        self.game_session["submissions"][player_id] = card_text
        await _persist_from_ui(self.game_session)

        submitted = len(self.game_session["submissions"])
        playing_ids = self.game_session.get("playing_ids", [])
        total = len(playing_ids)

        await interaction.response.send_message(
            f"Your answer has been submitted!", ephemeral=True
        )

        await self.channel.send(
            f"\U0001F4DD Card **{submitted}** of **{total}** submitted!"
        )

        from bot import check_all_submitted
        await check_all_submitted(self.game_session, interaction)


class WildcardView(discord.ui.View):
    def __init__(self, game_session, channel=None):
        super().__init__(timeout=300)
        self.game_session = game_session
        self.channel = channel

    @discord.ui.button(label="Write Your Answer", style=discord.ButtonStyle.green, emoji="\u270D\uFE0F")
    async def write_answer(self, interaction: discord.Interaction, button: discord.ui.Button):
        player_id = str(interaction.user.id)

        if player_id not in self.game_session.get("players", {}):
            await interaction.response.send_message("You're not in this game!", ephemeral=True)
            return

        if player_id == self.game_session.get("judge_id") and not self.game_session.get("use_ai_judge"):
            await interaction.response.send_message("You're the judge this round! Wait for answers, then pick the winner.", ephemeral=True)
            return

        if self.game_session.get("state") != "submitting":
            await interaction.response.send_message("No round in progress right now.", ephemeral=True)
            return

        if player_id in self.game_session.get("submissions", {}):
            await interaction.response.send_message("You already submitted an answer!", ephemeral=True)
            return

        game_channel = self.channel or interaction.channel
        modal = WildcardModal(self.game_session, game_channel)
        await interaction.response.send_modal(modal)
