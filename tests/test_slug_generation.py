import pytest
import re
from slugify import slugify


class TestSlugGeneration:
    def test_slug_from_prompt(self):
        prompt = "What is the most evil thing?"
        slug = slugify(f"{prompt[:40]}-{42}")
        assert slug
        assert isinstance(slug, str)
        assert " " not in slug

    def test_slug_is_url_safe(self):
        prompt = "What's the WORST gift for Grandma?!"
        slug = slugify(f"{prompt[:40]}-{123}")
        assert re.match(r'^[a-z0-9-]+$', slug)

    def test_slug_truncates_long_prompts(self):
        prompt = "A" * 200
        truncated = prompt[:40]
        slug = slugify(f"{truncated}-{999}")
        assert len(slug) < 100

    def test_slug_handles_special_characters(self):
        prompt = "Café & Résumé! @#$%"
        slug = slugify(f"{prompt[:40]}-{100}")
        assert slug
        assert "@" not in slug
        assert "$" not in slug
        assert "%" not in slug

    def test_slug_handles_unicode(self):
        prompt = "日本語テスト"
        slug = slugify(f"{prompt[:40]}-{100}")
        assert isinstance(slug, str)

    def test_slug_handles_empty_prompt(self):
        prompt = ""
        slug = slugify(f"{prompt[:40]}-{100}")
        assert slug
        assert "100" in slug

    def test_slug_includes_random_suffix(self):
        import random
        prompt = "Test prompt"
        slug1 = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")
        slug2 = slugify(f"{prompt[:40]}-{random.randint(100, 999)}")
        assert slug1.startswith("test-prompt-")
        assert slug2.startswith("test-prompt-")

    def test_slug_format_matches_bot_pattern(self):
        prompt = "What is evil?"
        num = 456
        slug = slugify(f"{prompt[:40]}-{num}")
        assert slug == "what-is-evil-456"

    def test_tournament_slug_format(self):
        tournament_id = 7
        round_number = 3
        num = 123
        slug = slugify(f"tournament-{tournament_id}-round-{round_number}-{num}")
        assert slug == "tournament-7-round-3-123"

    def test_slug_uniqueness_with_different_suffixes(self):
        prompt = "Same prompt"
        slugs = set()
        for i in range(100, 110):
            s = slugify(f"{prompt[:40]}-{i}")
            slugs.add(s)
        assert len(slugs) == 10
