import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_project(project: str) -> str:
    message = "Jira 프로젝트 URL을 입력해주세요. 예: https://example.atlassian.net/jira/software/projects/DEMO"
    value = project.strip()
    try:
        url = urlsplit(value)
        _ = url.port  # Reject malformed port numbers.
        if (
            url.scheme not in ("http", "https") or not url.hostname
            or url.username is not None or url.password is not None
            or any(character.isspace() or ord(character) < 32 for character in value)
            or any(character in value for character in ("`", "\\", "<", ">"))
        ):
            raise ValueError(message)
        match = re.fullmatch(
            r"(?P<prefix>(?:/[^/]+)*/projects/)(?P<key>[A-Za-z][A-Za-z0-9_]{0,254})(?:/.*)?",
            url.path,
        )
        if match is None:
            raise ValueError(message)
        # Keep the Jira base path; board, query and fragment are navigation state.
        path = match["prefix"] + match["key"].upper()
        return urlunsplit((url.scheme, url.netloc.lower(), path, "", ""))
    except ValueError:
        raise ValueError(message) from None


def project_key(project_url: str) -> str:
    return urlsplit(normalize_project(project_url)).path.rsplit("/", 1)[1]


@dataclass(frozen=True)
class JiraSettings:
    project_url: str
    email: str = field(repr=False)
    api_token: str = field(repr=False)

    @property
    def base_url(self) -> str:
        url = urlsplit(self.project_url)
        return urlunsplit((url.scheme, url.netloc, "", "", ""))


class ProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS guild_projects "
                "(guild_id INTEGER PRIMARY KEY, project_key TEXT NOT NULL)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(guild_projects)")}
            if "project_url" not in columns:
                connection.execute("ALTER TABLE guild_projects ADD COLUMN project_url TEXT")
            for column in ("email", "api_token"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE guild_projects ADD COLUMN {column} TEXT")

    def set_project(self, guild_id: int, project: str, email: str | None = None, api_token: str | None = None) -> str:
        url = normalize_project(project)
        key = project_key(url)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO guild_projects (guild_id, project_key, project_url, email, api_token) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET project_key = excluded.project_key, "
                "project_url = excluded.project_url, email = excluded.email, api_token = excluded.api_token",
                (guild_id, key, url, email, api_token),
            )
        return url

    def get_project(self, guild_id: int) -> str | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT project_url FROM guild_projects WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        return row[0] if row else None

    def get_settings(self, guild_id: int) -> JiraSettings | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT project_url, email, api_token FROM guild_projects WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        return JiraSettings(*row) if row and all(row) else None
