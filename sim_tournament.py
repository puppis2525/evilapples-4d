import asyncio
import random
import json
import time
import sys

sys.path.insert(0, ".")

FAKE_GUILD_ID = "SIM_GUILD_999"
FAKE_CHANNEL_ID = "SIM_CHANNEL_999"
CREATOR_ID = "SIM_CREATOR_001"
CREATOR_NAME = "TournamentHost"

PLAYER_NAMES = [
    "DrEvil_Fan", "CardShark42", "ApplePicker", "WittyWombat",
    "SnarkMaster", "PunLord99", "ChaosMagnet", "LaughRiot",
    "JokesterJen", "GiggleBot", "SassQueen", "BanterKing",
    "QuipMaster", "ZingerZoe", "ClapbackKid", "RoastLord",
    "ShadeThrown", "WildCard88", "JestPilot", "FunnyBones"
]

ENTRY_FEE_CENTS = 500
NUM_PLAYERS = 20
NUM_ROUNDS = 10
PLATFORM_FEE_PERCENT = 10


async def run_simulation():
    from db import (
        get_db_pool, create_paid_tournament, join_tournament,
        start_tournament, advance_tournament_round,
        get_tournament_players, record_tournament_entry,
        get_paid_tournament_entries, end_tournament, get_tournament_by_id
    )

    with open("classic_cards.json") as f:
        cards = json.load(f)

    black_cards = cards["black_cards"]
    white_cards = cards["white_cards"]

    pool = await get_db_pool()

    print("=" * 70)
    print("EVIL APPLES 4D — PAID TOURNAMENT SIMULATION")
    print("=" * 70)
    print(f"Players:        {NUM_PLAYERS}")
    print(f"Entry Fee:      ${ENTRY_FEE_CENTS / 100:.2f}")
    print(f"Rounds:         {NUM_ROUNDS}")
    print(f"Platform Fee:   {PLATFORM_FEE_PERCENT}%")
    print(f"Total Prize Pool: ${(NUM_PLAYERS * ENTRY_FEE_CENTS) / 100:.2f}")
    platform_take = int(NUM_PLAYERS * ENTRY_FEE_CENTS * PLATFORM_FEE_PERCENT / 100)
    host_receives = NUM_PLAYERS * ENTRY_FEE_CENTS - platform_take
    print(f"Platform Take:  ${platform_take / 100:.2f}")
    print(f"Host Receives:  ${host_receives / 100:.2f}")
    print("=" * 70)

    print("\n[1/5] Creating paid tournament...")
    tournament_id = await create_paid_tournament(
        name="Sim: $5 Showdown (20 Players)",
        guild_id=FAKE_GUILD_ID,
        channel_id=FAKE_CHANNEL_ID,
        creator_id=CREATOR_ID,
        creator_name=CREATOR_NAME,
        rounds_total=NUM_ROUNDS,
        entry_fee_cents=ENTRY_FEE_CENTS,
        host_stripe_account_id="acct_SIM_HOST_STRIPE",
        max_players=NUM_PLAYERS,
        platform_fee_percent=PLATFORM_FEE_PERCENT,
    )
    print(f"    Tournament #{tournament_id} created!")

    print(f"\n[2/5] Registering {NUM_PLAYERS} players & recording payments...")
    players = {}
    for i in range(NUM_PLAYERS):
        pid = f"SIM_PLAYER_{i+1:03d}"
        name = PLAYER_NAMES[i]
        players[pid] = name
        await join_tournament(tournament_id, pid, name)
        await record_tournament_entry(
            tournament_id=tournament_id,
            discord_user_id=pid,
            stripe_session_id=f"cs_sim_{tournament_id}_{pid}",
            amount_cents=ENTRY_FEE_CENTS,
            stripe_payment_intent_id=f"pi_sim_{tournament_id}_{pid}",
        )
        print(f"    [{i+1:2d}/{NUM_PLAYERS}] {name} registered & paid ${ENTRY_FEE_CENTS/100:.2f}")

    print(f"\n[3/5] Starting tournament...")
    await start_tournament(tournament_id)
    print("    Tournament is ACTIVE!\n")

    print("[4/5] Playing rounds...")
    print("-" * 70)

    used_prompts = set()
    player_ids = list(players.keys())
    judge_rotation = list(player_ids)
    random.shuffle(judge_rotation)

    round_results = []

    for round_num in range(1, NUM_ROUNDS + 1):
        prompt = random.choice([p for p in black_cards if p not in used_prompts])
        used_prompts.add(prompt)

        judge_idx = (round_num - 1) % len(judge_rotation)
        judge_id = judge_rotation[judge_idx]
        judge_name = players[judge_id]

        playing = [pid for pid in player_ids if pid != judge_id]

        submissions = {}
        for pid in playing:
            card = random.choice(white_cards)
            submissions[pid] = card

        winner_id = random.choice(playing)
        winner_name = players[winner_id]
        winning_card = submissions[winner_id]

        completed = await advance_tournament_round(
            tournament_id, round_num, prompt, winning_card, winner_id, winner_name
        )

        display_prompt = prompt[:50] + "..." if len(prompt) > 50 else prompt
        display_card = winning_card[:40] + "..." if len(winning_card) > 40 else winning_card

        round_results.append({
            "round": round_num,
            "prompt": display_prompt,
            "judge": judge_name,
            "winner": winner_name,
            "card": display_card,
        })

        status = "FINAL ROUND!" if completed else f"Round {round_num}/{NUM_ROUNDS}"
        print(f"\n  Round {round_num:2d} | Judge: {judge_name:<15s}")
        print(f"    Prompt:  {display_prompt}")
        print(f"    Winner:  {winner_name} — \"{display_card}\"")

        if completed:
            print(f"\n    >>> TOURNAMENT COMPLETE! <<<")

    print("\n" + "-" * 70)

    print("\n[5/5] Final standings & financial summary")
    print("=" * 70)

    final_players = await get_tournament_players(tournament_id)
    entries = await get_paid_tournament_entries(tournament_id)
    tournament = await get_tournament_by_id(tournament_id)

    print(f"\nTournament: {tournament['name']}")
    print(f"Status: {tournament['status']}")
    print(f"Rounds: {tournament['rounds_total']}")
    print()

    print("FINAL STANDINGS:")
    print(f"{'Rank':<6} {'Player':<20} {'Points':<8} {'Wins':<6}")
    print("-" * 40)
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(final_players):
        medal = medals[i] if i < len(medals) else f" {i+1}."
        print(f"{medal:<6} {p['discord_name']:<20} {p['points']:<8} {p['rounds_won']:<6}")

    champion = final_players[0] if final_players else None

    total_collected = sum(e["amount_cents"] for e in entries)
    platform_fee = int(total_collected * PLATFORM_FEE_PERCENT / 100)
    host_payout = total_collected - platform_fee

    print(f"\nFINANCIAL SUMMARY:")
    print(f"  Players:              {len(entries)}")
    print(f"  Entry Fee:            ${ENTRY_FEE_CENTS / 100:.2f} each")
    print(f"  Total Collected:      ${total_collected / 100:.2f}")
    print(f"  Platform Fee ({PLATFORM_FEE_PERCENT}%):   ${platform_fee / 100:.2f}")
    print(f"  Host Payout:          ${host_payout / 100:.2f}")
    if champion:
        print(f"\n  CHAMPION: {champion['discord_name']} ({champion['points']} pts, {champion['rounds_won']} wins)")
    print(f"\n  Prize Pool in DB:     ${tournament.get('prize_pool_cents', 0) / 100:.2f}")

    print("\n" + "=" * 70)
    print("SIMULATION COMPLETE")
    print("=" * 70)

    return tournament_id


if __name__ == "__main__":
    tournament_id = asyncio.run(run_simulation())
