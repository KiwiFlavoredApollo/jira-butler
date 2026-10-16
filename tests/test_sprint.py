import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import test_setup
from main import sprint_autocomplete, sprint_progress
from progress import Progress, ProgressError, ProgressService, Sprint
from test_progress import story
from test_setup import BASE_URL


class SprintServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProgressService("https://example.atlassian.net", "email", "token")

    @patch("progress.JIRA", autospec=True)
    def test_sprint_list_all_boards_and_deduplication(self, factory: MagicMock) -> None:
        client = factory.return_value
        client.boards.return_value = [NS(id=1), NS(id=2)]
        active = NS(id=10, name="Sprint 10", state="active")
        closed = NS(id=9, name="Sprint 9", state="closed")
        client.sprints.side_effect = [[closed, active], [active]]
        self.assertEqual(self.service.list_sprints(BASE_URL + "DEMO"),
                         [Sprint(10, "Sprint 10", "active"), Sprint(9, "Sprint 9", "closed")])
        client.boards.assert_called_once_with(projectKeyOrID="DEMO", type="scrum", maxResults=0)
        self.assertEqual(client.sprints.call_count, 2)
        self.assertEqual(client.sprints.call_args.kwargs["maxResults"], 0)
        client.close.assert_called_once()

    @patch("progress.JIRA", autospec=True)
    def test_sprint_story_query(self, factory: MagicMock) -> None:
        client = factory.return_value
        client.boards.return_value = [NS(id=1)]
        client.sprints.return_value = [NS(id=10, name="Sprint 10", state="active")]
        client.project.return_value = NS(issueTypes=[NS(id="10001", name="Story", subtask=False)])
        client.priorities.return_value = [NS(id=str(i)) for i in range(1, 6)]
        client.enhanced_search_issues.return_value = [story("1", "done"), story("5", "new")]
        result = self.service.fetch(BASE_URL + "DEMO", "10")
        self.assertEqual(result, Progress(2, 1, 9, 8))
        client.enhanced_search_issues.assert_called_once_with(
            'project = "DEMO" AND issuetype in (10001) AND sprint = 10 ORDER BY key',
            fields="priority,status", maxResults=0,
        )

    @patch("progress.JIRA", autospec=True)
    def test_unknown_sprint_does_not_query_issues(self, factory: MagicMock) -> None:
        client = factory.return_value
        client.boards.return_value = []
        with self.assertRaises(ProgressError):
            self.service.fetch(BASE_URL + "DEMO", '10 OR project = OTHER')
        client.enhanced_search_issues.assert_not_called()

    @patch("progress.JIRA", autospec=True)
    def test_ambiguous_name(self, factory: MagicMock) -> None:
        client = factory.return_value
        client.boards.return_value = [NS(id=1)]
        client.sprints.return_value = [NS(id=i, name="Same", state="active") for i in (10, 11)]
        with self.assertRaisesRegex(ProgressError, "같은 이름"):
            self.service.fetch(BASE_URL + "DEMO", "Same")
        client.enhanced_search_issues.assert_not_called()


class SprintCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_setup.CommandTests.asyncSetUp
    asyncTearDown = test_setup.CommandTests.asyncTearDown

    def configure(self) -> None:
        self.store.set_project(42, BASE_URL + "DEMO", "user@example.com", "token")

    async def test_progress(self) -> None:
        self.configure()
        with patch("main.ProgressService", autospec=True) as factory:
            factory.return_value.fetch.return_value = Progress(2, 1, 10, 8)
            await sprint_progress.callback(self.interaction, "10")
            factory.return_value.fetch.assert_called_once_with(BASE_URL + "DEMO", "10")
        self.assertIn("스프린트 진척도: **80.0%**", self.interaction.followup.send.call_args.args[0])

    async def test_unconfigured(self) -> None:
        await sprint_progress.callback(self.interaction, "10")
        self.assertIn("/setup", self.interaction.followup.send.call_args.args[0])

    async def test_api_failure(self) -> None:
        self.configure()
        with patch("main.ProgressService.fetch", side_effect=ProgressError("조회 실패")):
            await sprint_progress.callback(self.interaction, "10")
        self.assertEqual(self.interaction.followup.send.call_args.args[0], "조회 실패")

    async def test_autocomplete_filter_and_limit(self) -> None:
        self.configure()
        values = [Sprint(i, f"Sprint {i}", "active") for i in range(30)]
        with patch.object(self.client, "available_sprints", new_callable=AsyncMock, return_value=values):
            choices = await sprint_autocomplete(self.interaction, "Sprint")
            self.assertEqual(len(choices), 25)
            self.assertEqual(choices[0].value, "0")
            choices = await sprint_autocomplete(self.interaction, "29")
            self.assertEqual([choice.value for choice in choices], ["29"])

    async def test_autocomplete_failure(self) -> None:
        self.configure()
        with patch.object(self.client, "available_sprints", new_callable=AsyncMock, side_effect=ProgressError("조회 실패")), patch("main.logger.warning"):
            self.assertEqual(await sprint_autocomplete(self.interaction, ""), [])

    async def test_cache_and_credential_change(self) -> None:
        self.configure()
        settings = self.store.get_settings(42)
        with patch("main.ProgressService.list_sprints", return_value=[Sprint(10, "Sprint", "active")]) as fetch:
            await self.client.available_sprints(42, settings)
            await self.client.available_sprints(42, settings)
            self.assertEqual(fetch.call_count, 1)
            self.store.set_project(42, BASE_URL + "DEMO", "other@example.com", "new-token")
            await self.client.available_sprints(42, self.store.get_settings(42))
            self.assertEqual(fetch.call_count, 2)
