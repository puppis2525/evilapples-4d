import discord
import asyncio
import random
from slugify import slugify
from api_client import generate_hand, generate_prompt, HAND_SIZE
from db import (
    create_tournament, get_active_tournament, join_tournament,
    get_tournament_players, get_tournament_player_count,
    start_tournament, advance_tournament_round, end_tournament,
    save_round, batch_update_games_played,
    get_team_standings, get_tournament_sponsors
)

active_tournament_rounds = {}

def format_prompt(prompt_text):
    formatted = prompt_text.replace("_", "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    return f"**{formatted}**"


class TournamentJudgingView(discord.ui.View):
    def __init__(self, tournament_data, round_data, channel):
        super().__init__(timeout=180)
        self.tournament_data = tournament_data
        self.round_data = round_data
        self.channel = channel
        self.submissions = list(round_data["submissions"].items())
        random.shuffle(self.submissions)
        self.picked = False

        for i, (player_id, card_text) in enumerate(self.submissions):
            button = TournamentCardButton(index=i, label=f"Card {i + 1}", view_ref=self)
            self.add_item(button)

    async def pick_winner(self, interaction: discord.Interaction, index: int):
        if self.picked:
            await interaction.response.send_message("Winner already picked!", ephemeral=True)
            return

        self.picked = True

        winner_id, winning_card = self.submissions[index]
        from discord_names import resolve_member_display_name
        players = self.round_data.get("players", {})
        fallback = players.get(str(winner_id)) or players.get(winner_id) or "Unknown"
        winner_name = await resolve_member_display_name(
            interaction.guild, winner_id, fallback
        )

        prompt = self.round_data["prompt"]
        tournament_id = self.tournament_data["id"]
        round_number = self.round_data["round_number"]

        slug = slugify(f"tournament-{tournament_id}-round-{round_number}-{random.randint(100, 999)}")
        await save_round(prompt, winning_card, str(winner_id), winner_name, slug)

        players_in_round = {pid: self.round_data["players"][pid] for pid in self.round_data["players"]}
        await batch_update_games_played(players_in_round)

        is_final = await advance_tournament_round(
            tournament_id, round_number, prompt, winning_card, str(winner_id), winner_name
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        embed = discord.Embed(
            title=f"Round {round_number} Winner!",
            color=discord.Color.purple()
        )
        embed.add_field(name="Prompt", value=prompt, inline=False)
        embed.add_field(name="Winning Card", value=winning_card, inline=False)
        embed.add_field(name="Winner", value=f"**{winner_name}** (+1 point)", inline=False)

        try:
            sponsors = await get_tournament_sponsors(tournament_id)
            if sponsors:
                sponsor_names = " · ".join(s["sponsor_name"] for s in sponsors)
                embed.add_field(name="✨ Sponsored by", value=sponsor_names, inline=False)
        except Exception:
            pass

        web_created = self.tournament_data.get("web_created", False)
        footer_text = f"Tournament: {self.tournament_data['name']}"
        if web_created:
            footer_text += f" | EvilApples4d.com/tournaments/{tournament_id}"
        embed.set_footer(text=footer_text)
        from bot import maybe_append_ad
        await maybe_append_ad(embed, self.channel)
        await self.channel.send(embed=embed)

        players = await get_tournament_players(tournament_id)
        standings_text = "\n".join(
            f"{'> ' if p['discord_id'] == str(winner_id) else ''}"
            f"**{i+1}.** {p['discord_name']} — {p['points']} pts ({p['rounds_won']} wins)"
            for i, p in enumerate(players)
        )

        standings_embed = discord.Embed(
            title="Tournament Standings",
            description=standings_text,
            color=discord.Color.purple()
        )
        standings_embed.set_footer(
            text=f"Round {round_number}/{self.tournament_data['rounds_total']} complete"
        )

        team_count = self.tournament_data.get("team_count", 0) or 0
        if team_count > 0:
            try:
                teams = await get_team_standings(tournament_id)
                if teams:
                    team_text = "\n".join(
                        f"**{i+1}.** **{t['name']}** — {t['total_points']} pts"
                        for i, t in enumerate(teams)
                    )
                    standings_embed.add_field(name="Team Standings", value=team_text, inline=False)
            except Exception:
                pass

        await self.channel.send(embed=standings_embed)

        channel_key = int(self.tournament_data["channel_id"])
        if channel_key in active_tournament_rounds:
            del active_tournament_rounds[channel_key]

        if is_final:
            await self.announce_tournament_winner(players)
        else:
            await self.channel.send(
                f"**Next round starts in 15 seconds...** (Round {round_number + 1}/{self.tournament_data['rounds_total']})"
            )
            await asyncio.sleep(15)
            await run_tournament_round(
                self.channel,
                self.tournament_data,
                round_number + 1,
                self.round_data["all_players"],
                self.round_data["judge_rotation"],
                interaction.client
            )

    async def announce_tournament_winner(self, players):
        if not players:
            return

        champion = players[0]
        embed = discord.Embed(
            title="TOURNAMENT COMPLETE!",
            description=f"**{self.tournament_data['name']}** has concluded!",
            color=discord.Color.gold()
        )

        podium = ""
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(players[:3]):
            medal = medals[i] if i < 3 else f"**{i+1}.**"
            podium += f"{medal} **{p['discord_name']}** — {p['points']} pts ({p['rounds_won']} round wins)\n"

        embed.add_field(name="Final Standings", value=podium, inline=False)
        embed.add_field(
            name="Champion",
            value=f"**{champion['discord_name']}** wins the tournament with {champion['points']} points!",
            inline=False
        )

        try:
            sponsors = await get_tournament_sponsors(self.tournament_data["id"])
            if sponsors:
                sponsor_names = " · ".join(s["sponsor_name"] for s in sponsors)
                embed.add_field(name="✨ Sponsored by", value=sponsor_names, inline=False)
        except Exception:
            pass

        tournament_id = self.tournament_data["id"]
        web_created = self.tournament_data.get("web_created", False)
        footer_text = f"{self.tournament_data['rounds_total']} rounds played"
        if web_created:
            footer_text += f" | EvilApples4d.com/tournaments/{tournament_id}"
        embed.set_footer(text=footer_text)

        team_count = self.tournament_data.get("team_count", 0) or 0
        if team_count > 0:
            try:
                teams = await get_team_standings(self.tournament_data["id"])
                if teams:
                    team_medals = ["🥇", "🥈", "🥉"]
                    team_text = "\n".join(
                        f"{team_medals[i] if i < len(team_medals) else f'**{i+1}.**'} "
                        f"**{t['name']}** — {t['total_points']} pts ({t['total_wins']} wins)"
                        for i, t in enumerate(teams)
                    )
                    embed.add_field(name="Team Standings", value=team_text, inline=False)
            except Exception:
                pass

        entry_fee = self.tournament_data.get("entry_fee_cents", 0) or 0
        if entry_fee > 0:
            try:
                from stripe_routes import process_tournament_payout
                from db import get_paid_tournament_entries
                winner_discord_id = champion.get("discord_id")
                await process_tournament_payout(self.tournament_data["id"], winner_discord_id=winner_discord_id)
                entries = await get_paid_tournament_entries(self.tournament_data["id"])
                total = sum(e["amount_cents"] for e in entries)
                fee_pct = self.tournament_data.get("platform_fee_percent", 10) or 10
                platform_fee = int(total * fee_pct / 100)
                host_received = total - platform_fee
                embed.add_field(
                    name="💰 Prize Pool",
                    value=(
                        f"Total Collected: ${total/100:.2f}\n"
                        f"Platform Fee ({fee_pct}%): ${platform_fee/100:.2f}\n"
                        f"Host Received: ${host_received/100:.2f} (via Stripe Connect)"
                    ),
                    inline=False
                )
            except Exception as e:
                import logging
                logging.getLogger("tournament").error(f"Tournament payout error: {e}")
                embed.add_field(name="Payout", value="Payout processing — check website for status.", inline=False)

        await self.channel.send(embed=embed)


class TournamentCardButton(discord.ui.Button):
    def __init__(self, index: int, label: str, view_ref):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        judge_id = self.view_ref.round_data.get("judge_id")
        if str(interaction.user.id) != str(judge_id):
            await interaction.response.send_message(
                "Only the current Judge can pick the winner!", ephemeral=True
            )
            return
        await self.view_ref.pick_winner(interaction, self.index)


async def run_tournament_round(channel, tournament_data, round_number, players, judge_rotation, bot_client):
    judge_index = (round_number - 1) % len(judge_rotation)
    judge_id = judge_rotation[judge_index]
    judge_name = players.get(judge_id, "Unknown")

    playing_players = {pid: name for pid, name in players.items() if pid != judge_id}

    round_data = {
        "round_number": round_number,
        "judge_id": judge_id,
        "all_players": players,
        "players": playing_players,
        "hands": {},
        "submissions": {},
        "prompt": "",
        "judge_rotation": judge_rotation,
        "exchanged": set(),
    }

    active_tournament_rounds[channel.id] = {
        "tournament": tournament_data,
        "round": round_data,
    }

    embed = discord.Embed(
        title=f"Tournament Round {round_number}/{tournament_data['rounds_total']}",
        description=f"**Judge:** <@{judge_id}>\nGenerating cards...",
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"Tournament: {tournament_data['name']}")
    await channel.send(embed=embed)

    try:
        prompt = await generate_prompt()
        round_data["prompt"] = prompt

        player_ids = list(playing_players.keys())
        hands = await asyncio.gather(*(generate_hand(HAND_SIZE) for _ in player_ids))
        for pid, hand in zip(player_ids, hands):
            round_data["hands"][pid] = hand

        from ui_components import TournamentHandView
        round_embed = discord.Embed(
            title=f"Round {round_number} — Cards Dealt!",
            description=f"## {format_prompt(prompt)}\n\nClick the button below to view your hand and submit a card!",
            color=discord.Color.from_rgb(106, 27, 154)
        )
        round_embed.add_field(name="Judge", value=f"<@{judge_id}>", inline=True)
        round_embed.add_field(name="Players", value=str(len(playing_players)), inline=True)
        round_embed.set_footer(text=f"Tournament: {tournament_data['name']} | Waiting for {len(playing_players)} submissions...")
        t_hand_view = TournamentHandView(round_data, tournament_data, channel)
        await channel.send(embed=round_embed, view=t_hand_view)

    except Exception as e:
        await channel.send(f"Error generating cards for round {round_number}: {str(e)}")
        if channel.id in active_tournament_rounds:
            del active_tournament_rounds[channel.id]
