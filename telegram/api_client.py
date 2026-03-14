import os
import re
import json
import random
import httpx
from rapidfuzz import fuzz

VENICE_API_KEY = os.environ.get("VENICE_API_KEY", "")
VENICE_API_URL = "https://api.venice.ai/api/v1/chat/completions"

_cards_path = os.path.join(os.path.dirname(__file__), "..", "classic_cards.json")
with open(_cards_path, "r") as f:
    CLASSIC_CARDS = json.load(f)

ALL_CLASSIC = CLASSIC_CARDS["black_cards"] + CLASSIC_CARDS["white_cards"]

SYSTEM_PROMPT_WHITE = """You are a Divergent Comedy card generator for an adult party game similar to Cards Against Humanity.
Generate a single short, funny, edgy, and original answer card (white card).
Rules:
- Keep it under 15 words
- Be creative, dark, absurd, or shocking
- Write in English only — no other languages or scripts
- Do NOT copy or closely paraphrase any existing Cards Against Humanity cards
- Return ONLY the card text, no quotes, no numbering, no explanation"""

SYSTEM_PROMPT_BLACK = """You are a Divergent Comedy card generator for an adult party game similar to Cards Against Humanity.
Generate a single short, funny prompt card (black card) with a blank represented by _.
Rules:
- Keep it under 20 words
- Include exactly one _ where the answer goes
- Be creative, dark, absurd, or provocative
- Write in English only — no other languages or scripts
- Do NOT copy or closely paraphrase any existing Cards Against Humanity cards
- Return ONLY the card text, no quotes, no numbering, no explanation"""

SIMILARITY_THRESHOLD = 75

HAND_SIZE = 7


def sanitize_card_text(text: str) -> str:
    text = re.sub(r'[^\x20-\x7E\n]', '', text)
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def is_duplicate(card_text: str) -> bool:
    for classic in ALL_CLASSIC:
        if fuzz.token_sort_ratio(card_text.lower(), classic.lower()) > SIMILARITY_THRESHOLD:
            return True
    return False


async def generate_card(card_type: str = "white") -> str:
    system_prompt = SYSTEM_PROMPT_WHITE if card_type == "white" else SYSTEM_PROMPT_BLACK
    user_msg = "Generate one answer card." if card_type == "white" else "Generate one prompt card."

    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "llama-3.3-70b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 100,
        "temperature": 1.1,
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(VENICE_API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"]
                card_text = sanitize_card_text(raw_text)

                if not card_text:
                    if attempt == 0:
                        continue
                    card_text = "A card so evil it censored itself"

                if not is_duplicate(card_text):
                    return card_text

                if attempt == 0:
                    payload["temperature"] = 1.3
                    continue
        except Exception as e:
            if attempt == 0:
                continue
            raise e

    return card_text


DEFAULT_ANSWER_CARDS = CLASSIC_CARDS["white_cards"]


def deal_default_hands(num_players: int, hand_size: int = HAND_SIZE) -> list[list[str]]:
    total_needed = num_players * hand_size
    pool = list(DEFAULT_ANSWER_CARDS)
    random.shuffle(pool)

    if len(pool) >= total_needed:
        hands = [pool[i * hand_size:(i + 1) * hand_size] for i in range(num_players)]
    else:
        hands = []
        for i in range(num_players):
            start = (i * hand_size) % len(pool)
            hand = []
            for j in range(hand_size):
                hand.append(pool[(start + j) % len(pool)])
            hands.append(hand)
    return hands


def deal_default_solo(hand_size: int = HAND_SIZE, num_ai: int = 3) -> tuple[list[str], list[str]]:
    pool = list(DEFAULT_ANSWER_CARDS)
    random.shuffle(pool)
    player_hand = pool[:hand_size]
    ai_cards = pool[hand_size:hand_size + num_ai]
    return player_hand, ai_cards


def deal_replacement_cards(count: int, exclude: list[str] | None = None) -> list[str]:
    pool = list(DEFAULT_ANSWER_CARDS)
    if exclude:
        exclude_set = set(exclude)
        pool = [c for c in pool if c not in exclude_set]
    random.shuffle(pool)
    return pool[:count]


async def generate_hand(count: int = HAND_SIZE) -> list[str]:
    cards = []
    for _ in range(count):
        card = await generate_card("white")
        cards.append(card)
    return cards


async def generate_prompt() -> str:
    try:
        return await generate_card("black")
    except Exception:
        black_cards = CLASSIC_CARDS.get("black_cards", [])
        if black_cards:
            return random.choice(black_cards)
        return "What's the worst thing you can say at a funeral? _"


SYSTEM_PROMPT_JUDGE = """You are the judge of a dark comedy card game called Evil Apples.
You will be given a prompt card and several answer cards submitted by players.
Pick the funniest, most creative, or most fittingly dark answer.
Consider shock value, comedic timing, and how well the answer completes the prompt.
Return ONLY the number of the winning card (1-based index). Nothing else — just the number."""

SYSTEM_PROMPT_ROAST = """You are Dr. Evil — a diabolical, sarcastic villain who judges a Cards Against Humanity-style game called Evil Apples.
You just finished judging a round and you're appalled (yet secretly impressed) by how depraved and twisted the players' submissions were.
Write a short, savage roast (1-3 sentences max) commenting on the cards that were played. Be theatrical, condescending, and darkly funny.
Reference the actual card content in your roast. Act disgusted but entertained.
Channel the energy of a Bond villain who can't believe what he just read.
Do NOT use asterisks or markdown formatting. Just plain text. Keep it under 200 characters if possible."""


async def ai_judge(prompt: str, submissions: list[str]) -> int:
    choices_text = "\n".join(f"{i+1}. {text}" for i, text in enumerate(submissions))
    user_msg = f"Prompt card: \"{prompt}\"\n\nAnswer cards:\n{choices_text}\n\nWhich card wins? Reply with only the number."

    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "llama-3.3-70b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_JUDGE},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 10,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(VENICE_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            result = data["choices"][0]["message"]["content"].strip()

            match = re.search(r'\d+', result)
            if match:
                idx = int(match.group()) - 1
                if 0 <= idx < len(submissions):
                    return idx
            return 0
    except Exception:
        return 0


async def dr_evil_roast(prompt: str, submissions: list[str]) -> str:
    cards_text = "\n".join(f"- {card}" for card in submissions)
    user_msg = f'The prompt was: "{prompt}"\n\nThe players submitted these cards:\n{cards_text}\n\nRoast these degenerates.'

    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "llama-3.3-70b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_ROAST},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 100,
        "temperature": 0.9,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(VENICE_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            roast = data["choices"][0]["message"]["content"].strip().strip('"')
            return roast
    except Exception:
        return ""
