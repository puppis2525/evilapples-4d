import pytest
from migrations import MIGRATIONS


class TestMigrationStructure:
    def test_migrations_are_ordered(self):
        versions = [m["version"] for m in MIGRATIONS]
        assert versions == sorted(versions)

    def test_no_duplicate_versions(self):
        versions = [m["version"] for m in MIGRATIONS]
        assert len(versions) == len(set(versions))

    def test_all_migrations_have_required_fields(self):
        for m in MIGRATIONS:
            assert "version" in m, f"Migration missing version"
            assert "description" in m, f"Migration {m.get('version')} missing description"
            assert "sql" in m, f"Migration {m.get('version')} missing sql"
            assert isinstance(m["sql"], list), f"Migration {m['version']} sql must be a list"
            assert len(m["sql"]) > 0, f"Migration {m['version']} has no SQL statements"

    def test_all_migrations_have_descriptions(self):
        for m in MIGRATIONS:
            assert m["description"], f"Migration {m['version']} has empty description"

    def test_versions_start_from_one(self):
        assert MIGRATIONS[0]["version"] == 1

    def test_versions_are_contiguous(self):
        versions = [m["version"] for m in MIGRATIONS]
        for i in range(len(versions) - 1):
            assert versions[i + 1] == versions[i] + 1, f"Gap between versions {versions[i]} and {versions[i+1]}"

    def test_rate_limit_migration_exists(self):
        rate_limit_migrations = [m for m in MIGRATIONS if "rate_limit" in m["description"].lower()]
        assert len(rate_limit_migrations) > 0

    def test_core_tables_in_migrations(self):
        all_sql = " ".join(sql for m in MIGRATIONS for sql in m["sql"])
        assert "active_game_sessions" in all_sql
        assert "game_rounds" in all_sql
        assert "leaderboard" in all_sql
        assert "tournaments" in all_sql
        assert "tournament_players" in all_sql
        assert "custom_decks" in all_sql
        assert "store_listings" in all_sql
        assert "rate_limit_entries" in all_sql

    def test_store_purchases_backward_compat_migration(self):
        compat_migrations = [m for m in MIGRATIONS if "backward compatibility" in m["description"].lower()]
        assert len(compat_migrations) > 0
        sql = " ".join(s for m in compat_migrations for s in m["sql"])
        assert "store_purchases" in sql
        assert "status" in sql

    def test_leaderboard_composite_index_migration(self):
        m10 = [m for m in MIGRATIONS if m["version"] == 10][0]
        sql = " ".join(m10["sql"])
        assert "leaderboard_uid_platform" in sql
        assert "discord_id, platform" in sql
