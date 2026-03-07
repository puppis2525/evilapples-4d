import discord
import random
from slugify import slugify
from db import save_round


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
            color=discord.Color.gold()
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
