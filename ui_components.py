import discord
import random
from slugify import slugify
from db import save_round


def format_prompt(prompt_text):
    formatted = prompt_text.replace("_", "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    return f"**{formatted}**"


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
        embed = discord.Embed(
            title="Your Hand",
            description=f"{format_prompt(prompt)}\n\n{hand_text}",
            color=discord.Color.dark_purple()
        )
        embed.set_footer(text="Click a numbered button below to submit your card!")

        game_channel = self.channel or interaction.channel
        submit_view = SubmitCardView(self.game_session, hand, self.mode, channel=game_channel)
        await interaction.response.send_message(embed=embed, view=submit_view, ephemeral=True)


class SubmitCardView(discord.ui.View):
    def __init__(self, game_session, hand, mode="multiplayer", channel=None):
        super().__init__(timeout=300)
        self.game_session = game_session
        self.hand = hand
        self.mode = mode
        self.channel = channel

        for i, card in enumerate(hand):
            label = f"{i+1}"
            button = SubmitCardButton(index=i, label=label, card_text=card, view_ref=self)
            self.add_item(button)


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
            for item in self.view_ref.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Card submitted: **{self.card_text}**",
                embed=None,
                view=self.view_ref
            )

            try:
                from bot import finish_solo_round
                await finish_solo_round(player_id, player_card=self.card_text)
            except Exception as e:
                print(f"Error calling finish_solo_round from button: {e}")
                import traceback
                traceback.print_exc()
                channel = self.view_ref.channel or interaction.channel
                if channel:
                    await channel.send("Something went wrong during judging. Type `!solo deal` to try again.")

        else:
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

        winner_id, winning_card = self.submissions[index]
        winner = interaction.guild.get_member(int(winner_id))
        winner_name = winner.display_name if winner else "Unknown"

        prompt = self.game_session["prompt"]
        slug = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")

        await save_round(prompt, winning_card, str(winner_id), winner_name, slug)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        embed = discord.Embed(
            title="We Have a Winner!",
            color=discord.Color.purple()
        )
        embed.add_field(name="Prompt", value=prompt, inline=False)
        embed.add_field(name="Winning Card", value=winning_card, inline=False)
        embed.add_field(name="Winner", value=f"**{winner_name}**", inline=False)
        embed.set_footer(text=f"Round saved as: /rounds/{slug}")

        await self.channel.send(embed=embed)

        self.game_session["state"] = "finished"


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
        embed = discord.Embed(
            title=f"Tournament: {self.tournament_data['name']}",
            description=f"{format_prompt(prompt)}\n\n{hand_text}",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Click a numbered button below to submit your card!")

        submit_view = TournamentSubmitView(self.round_data, self.tournament_data, hand, self.channel)
        await interaction.response.send_message(embed=embed, view=submit_view, ephemeral=True)


class TournamentSubmitView(discord.ui.View):
    def __init__(self, round_data, tournament_data, hand, channel):
        super().__init__(timeout=300)
        self.round_data = round_data
        self.tournament_data = tournament_data
        self.hand = hand
        self.channel = channel

        for i, card in enumerate(hand):
            button = TournamentSubmitButton(index=i, label=f"{i+1}", card_text=card, view_ref=self)
            self.add_item(button)


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
