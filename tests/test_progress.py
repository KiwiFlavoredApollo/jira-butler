import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import test_setup
from jira import JIRA, JIRAError
from main import project_progress
from progress import Progress, ProgressError, ProgressService, calculate_progress
from test_setup import BASE_URL


def story(priority: str, status: str) -> NS:
    return NS(fields=NS(priority=NS(id=priority), status=NS(statusCategory=NS(key=status))))


class CalculationTests(unittest.TestCase):
    def test_weighted_progress(self) -> None:
        result = calculate_progress([story("1", "done"), story("3", "indeterminate"), story("5", "new")],
                                    {"1": 8, "3": 3, "5": 1})
        self.assertEqual(result, Progress(3, 1, 12, 8))
        self.assertAlmostEqual(result.percentage, 100 * 8 / 12)

    def test_empty_and_complete(self) -> None:
        self.assertEqual(calculate_progress([], {}).percentage, 0)
        self.assertEqual(calculate_progress([story("1", "done")], {"1": 8}).percentage, 100)

    def test_missing_priority(self) -> None:
        with self.assertRaises(ProgressError):
            calculate_progress([story("unknown", "done")], {"1": 8})


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProgressService("https://example.atlassian.net", "email", "token")

    @patch("progress.JIRA", autospec=True)
    def test_story_query_and_all_pages(self, factory: MagicMock) -> None:
        client = factory.return_value
        client.project.return_value = NS(issueTypes=[
            NS(id="10001", name="스토리", subtask=False), NS(id="10002", name="Bug", subtask=False),
        ])
        client.priorities.return_value = [NS(id=str(i)) for i in range(1, 6)]
        client.enhanced_search_issues.return_value = [story("1", "done"), story("5", "new")]
        self.assertEqual(self.service.fetch(BASE_URL + "DEMO"), Progress(2, 1, 9, 8))
        client.enhanced_search_issues.assert_called_once_with(
            'project = "DEMO" AND issuetype in (10001) ORDER BY key',
            fields="priority,status", maxResults=0,
        )
        client.close.assert_called_once()

    @patch("progress.JIRA", autospec=True)
    def test_auth_error(self, factory: MagicMock) -> None:
        factory.side_effect = JIRAError(status_code=401, text="secret")
        with self.assertRaisesRegex(ProgressError, "인증 정보"):
            self.service.fetch(BASE_URL + "DEMO")

    @patch("progress.JIRA", autospec=True)
    def test_different_site_rejected_before_connection(self, factory: MagicMock) -> None:
        with self.assertRaises(ProgressError):
            self.service.fetch("https://other.atlassian.net/projects/TEST")
        factory.assert_not_called()

    def test_missing_configuration(self) -> None:
        with self.assertRaises(ProgressError):
            ProgressService("", "", "").fetch(BASE_URL + "DEMO")


class CommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_setup.CommandTests.asyncSetUp
    asyncTearDown = test_setup.CommandTests.asyncTearDown

    async def test_unconfigured(self) -> None:
        with patch("main.ProgressService.fetch") as fetch:
            await project_progress.callback(self.interaction)
            fetch.assert_not_called()
        self.assertIn("/setup", self.interaction.followup.send.call_args.args[0])

    async def test_legacy_project_requires_setup(self) -> None:
        self.store.set_project(42, BASE_URL + "DEMO")
        with patch("main.ProgressService.fetch") as fetch:
            await project_progress.callback(self.interaction)
            fetch.assert_not_called()
        self.assertIn("/setup", self.interaction.followup.send.call_args.args[0])

    async def test_progress(self) -> None:
        self.store.set_project(42, BASE_URL + "DEMO", "user@example.com", "test-token")
        with patch("main.ProgressService", autospec=True) as factory:
            factory.return_value.fetch.return_value = Progress(2, 1, 10, 8)
            await project_progress.callback(self.interaction)
            factory.assert_called_once_with("https://example.atlassian.net", "user@example.com", "test-token")
        self.assertIn("80.0%", self.interaction.followup.send.call_args.args[0])

    async def test_empty(self) -> None:
        self.store.set_project(42, BASE_URL + "DEMO", "user@example.com", "test-token")
        with patch("main.ProgressService.fetch", return_value=Progress()):
            await project_progress.callback(self.interaction)
        self.assertIn("스토리가 없습니다", self.interaction.followup.send.call_args.args[0])

    async def test_api_error(self) -> None:
        self.store.set_project(42, BASE_URL + "DEMO", "user@example.com", "test-token")
        with patch("main.ProgressService.fetch", side_effect=ProgressError("인증 실패")):
            await project_progress.callback(self.interaction)
        self.assertEqual(self.interaction.followup.send.call_args.args[0], "인증 실패")
