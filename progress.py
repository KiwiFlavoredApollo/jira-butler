from dataclasses import dataclass
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from jira import JIRA, JIRAError
from requests import RequestException

from settings import normalize_project, project_key


class ProgressError(Exception):
    pass


@dataclass(frozen=True)
class Sprint:
    id: int
    name: str
    state: str


@dataclass(frozen=True)
class Progress:
    total: int = 0
    done: int = 0
    total_weight: int = 0
    done_weight: int = 0

    @property
    def percentage(self) -> float:
        return self.done_weight / self.total_weight * 100 if self.total_weight else 0.0


def calculate_progress(issues: list[Any], weights: dict[str, int]) -> Progress:
    done = total_weight = done_weight = 0
    for issue in issues:
        priority = getattr(issue.fields, "priority", None)
        weight = weights.get(str(getattr(priority, "id", "")))
        if weight is None:
            raise ProgressError("스토리에 우선순위가 없거나 지원하지 않는 우선순위가 있습니다.")
        total_weight += weight
        if issue.fields.status.statusCategory.key == "done":
            done += 1
            done_weight += weight
    return Progress(len(issues), done, total_weight, done_weight)


class ProgressService:
    def __init__(self, base_url: str, email: str, token: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.email = email.strip()
        self.token = token.strip()

    @contextmanager
    def _client(self, project_url: str) -> Iterator[JIRA]:
        if not all((self.base_url, self.email, self.token)):
            raise ProgressError("/setup으로 Jira 프로젝트 URL, 이메일, API 토큰을 설정해주세요.")
        project = urlsplit(normalize_project(project_url))
        base = urlsplit(self.base_url)
        if (base.scheme != "https" or not base.hostname or base.username or base.password
                or base.path or base.query or base.fragment):
            raise ProgressError("/setup에 HTTPS Jira Cloud 프로젝트 URL을 설정해주세요.")
        if (project.scheme, project.netloc.lower()) != (base.scheme, base.netloc.lower()):
            raise ProgressError("프로젝트 URL과 Jira 사이트 주소가 다릅니다. /setup 설정을 확인해주세요.")
        client: JIRA | None = None
        try:
            client = JIRA(server=self.base_url, basic_auth=(self.email, self.token),
                          timeout=30, max_retries=0)
            yield client
        except JIRAError as error:
            if error.status_code in (401, 403):
                raise ProgressError("Jira 인증 정보와 프로젝트 조회 권한을 확인해주세요.") from None
            if error.status_code == 404:
                raise ProgressError("Jira 프로젝트 또는 스프린트를 찾을 수 없습니다. URL과 조회 권한을 확인해주세요.") from None
            if error.status_code == 429:
                raise ProgressError("Jira 요청 한도에 도달했습니다. 잠시 후 다시 시도해주세요.") from None
            raise ProgressError("Jira 조회에 실패했습니다. 잠시 후 다시 시도해주세요.") from None
        except RequestException:
            raise ProgressError("Jira에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.") from None
        finally:
            if client is not None:
                client.close()

    def _sprints(self, client: JIRA, key: str) -> list[Sprint]:
        found: dict[int, Sprint] = {}
        boards = client.boards(projectKeyOrID=key, type="scrum", maxResults=0)
        for board in boards:
            for item in client.sprints(board.id, maxResults=0):
                found[int(item.id)] = Sprint(int(item.id), item.name, item.state)
        order = {"active": 0, "future": 1, "closed": 2}
        return sorted(found.values(), key=lambda item: (order.get(item.state, 3), -item.id))

    def list_sprints(self, project_url: str) -> list[Sprint]:
        with self._client(project_url) as client:
            return self._sprints(client, project_key(project_url))

    def fetch(self, project_url: str, sprint: str | None = None) -> Progress:
        with self._client(project_url) as client:
            key = project_key(project_url)
            sprint_filter = ""
            if sprint is not None:
                available = self._sprints(client, key)
                matches = [item for item in available if str(item.id) == sprint.strip()]
                if not matches:
                    matches = [item for item in available if item.name.casefold() == sprint.strip().casefold()]
                if not matches:
                    raise ProgressError("이 프로젝트에서 스프린트를 찾을 수 없습니다. 자동완성 목록에서 선택해주세요.")
                if len(matches) != 1:
                    raise ProgressError("같은 이름의 스프린트가 여러 개입니다. 자동완성 목록에서 선택해주세요.")
                sprint_filter = f" AND sprint = {matches[0].id}"
            metadata = client.project(key)
            story_ids = [str(item.id) for item in metadata.issueTypes
                         if item.name.casefold() in ("story", "스토리") and not item.subtask]
            if not story_ids:
                raise ProgressError("프로젝트에 Story 또는 스토리 이슈 유형이 없습니다.")
            if not all(value.isdigit() for value in story_ids):
                raise ProgressError("Jira 이슈 유형 정보가 올바르지 않습니다.")
            priorities = client.priorities()
            if len(priorities) != 5:
                raise ProgressError("우선순위가 5단계인 Jira 설정에서 진척도를 계산할 수 있습니다.")
            weights = {str(priority.id): weight for priority, weight in zip(priorities, (8, 5, 3, 2, 1))}
            issues = client.enhanced_search_issues(
                f'project = "{key}" AND issuetype in ({",".join(story_ids)}){sprint_filter} ORDER BY key',
                fields="priority,status", maxResults=0,
            )
            return calculate_progress(issues, weights)
