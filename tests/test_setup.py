import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

from main import Butler, setup, setup_error
from settings import ProjectStore, normalize_project, project_key


BASE_URL = "https://example.atlassian.net/jira/software/projects/"


class ProjectTests(unittest.TestCase):
    def test_persistence_isolation_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.db"
            store = ProjectStore(path)
            self.assertIsNone(store.get_project(1))
            store.set_project(1, BASE_URL + "FITZZA")
            store.set_project(2, BASE_URL + "OTHER")
            store.set_project(1, BASE_URL + "NEW")
            reopened = ProjectStore(path)
            self.assertEqual(reopened.get_project(1), BASE_URL + "NEW")
            self.assertEqual(reopened.get_project(2), BASE_URL + "OTHER")

    def test_board_url(self) -> None:
        url = " https://fractalbarista.atlassian.net/jira/software/projects/fitzza/boards/1?filter=&groupBy=none#board "
        expected = "https://fractalbarista.atlassian.net/jira/software/projects/FITZZA"
        self.assertEqual(normalize_project(url), expected)
        self.assertEqual(project_key(url), "FITZZA")

    def test_context_path(self) -> None:
        url = "https://jira.example.com/jira/projects/TEST/summary"
        self.assertEqual(normalize_project(url), "https://jira.example.com/jira/projects/TEST")

    def test_existing_database_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE guild_projects (guild_id INTEGER PRIMARY KEY, project_key TEXT NOT NULL)")
                connection.execute("INSERT INTO guild_projects VALUES (42, 'FITZZA')")
            store = ProjectStore(path)
            self.assertIsNone(store.get_project(42))
            store.set_project(42, BASE_URL + "FITZZA")
            self.assertEqual(ProjectStore(path).get_project(42), BASE_URL + "FITZZA")

    def test_invalid_keys(self) -> None:
        for key in ("", "FITZZA", "https://jira.test", "ftp://jira.test/projects/TEST", "https://user:pass@jira.test/projects/TEST", "https://jira.test:bad/projects/TEST", BASE_URL + "A" * 256, BASE_URL + "bad key"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_project(key)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.directory.name) / "settings.db")
        self.client = Butler(self.store)
        self.interaction = MagicMock(spec=discord.Interaction)
        self.interaction.guild_id = 42
        self.interaction.client = self.client
        self.interaction.response = MagicMock()
        self.interaction.response.send_message = AsyncMock()
        self.interaction.response.defer = AsyncMock()
        self.interaction.followup.send = AsyncMock()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.directory.cleanup()

    async def test_setup_saves_normalized_url(self) -> None:
        await setup.callback(self.interaction, BASE_URL + "fitzza/boards/1?groupBy=none")
        self.assertEqual(self.store.get_project(42), BASE_URL + "FITZZA")
        self.interaction.followup.send.assert_awaited_once()

    async def test_invalid_input_does_not_overwrite(self) -> None:
        self.store.set_project(42, BASE_URL + "OLD")
        await setup.callback(self.interaction, "bad key")
        self.assertEqual(self.store.get_project(42), BASE_URL + "OLD")
        self.interaction.response.send_message.assert_awaited_once()

    async def test_dm_does_not_save(self) -> None:
        self.interaction.guild_id = None
        await setup.callback(self.interaction, BASE_URL + "FITZZA")
        self.assertIsNone(self.store.get_project(42))

    async def test_permission_check(self) -> None:
        self.interaction.permissions = discord.Permissions.none()
        with self.assertRaises(app_commands.MissingPermissions):
            setup.checks[0](self.interaction)
        self.interaction.permissions = discord.Permissions(manage_guild=True)
        self.assertTrue(setup.checks[0](self.interaction))

    async def test_storage_error_after_defer(self) -> None:
        self.interaction.response.is_done.return_value = True
        with patch("main.logger.error"):
            await setup_error(self.interaction, app_commands.AppCommandError("storage failed"))
        self.interaction.followup.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
