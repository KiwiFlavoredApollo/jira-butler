import asyncio
import logging
import os
from pathlib import Path

import discord
from discord import app_commands

from settings import ProjectStore, normalize_project
from progress import ProgressError, ProgressService

logger = logging.getLogger(__name__)


class Butler(discord.Client):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__(intents=discord.Intents.none())
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(setup)
        self.tree.add_command(project_commands)

    async def setup_hook(self) -> None:
        await self.tree.sync()


@app_commands.command(name="setup", description="이 서버에서 사용할 Jira 프로젝트를 설정합니다.")
@app_commands.describe(project="Jira 프로젝트 URL (예: https://example.atlassian.net/jira/software/projects/FITZZA)")
@app_commands.describe(email="Atlassian 계정 이메일", api_token="Jira API 토큰")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, project: str, email: str, api_token: str) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    try:
        url = normalize_project(project)
        if not url.startswith("https://"):
            raise ValueError("HTTPS Jira 프로젝트 URL을 입력해주세요.")
        email, api_token = email.strip(), api_token.strip()
        if not email or "@" not in email or any(character.isspace() for character in email):
            raise ValueError("올바른 Atlassian 계정 이메일을 입력해주세요.")
        if not api_token or any(character.isspace() for character in api_token):
            raise ValueError("올바른 Jira API 토큰을 입력해주세요.")
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    client = interaction.client
    if not isinstance(client, Butler):
        raise RuntimeError("Butler 클라이언트가 필요합니다.")
    await asyncio.to_thread(client.store.set_project, interaction.guild_id, url, email, api_token)
    await interaction.followup.send(f"이 서버의 Jira 프로젝트를 `{url}`로 설정했습니다.", ephemeral=True)


@setup.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "프로젝트 설정에는 '서버 관리' 권한이 필요합니다."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "서버에서만 사용할 수 있습니다."
    else:
        logger.error("프로젝트 설정 실패: %s", type(error).__name__)
        message = "프로젝트 설정을 저장하지 못했습니다. 잠시 후 다시 시도해주세요."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


project_commands = app_commands.Group(name="project", description="Jira 프로젝트 조회", guild_only=True)


@project_commands.command(name="progress", description="스토리 우선순위 가중치로 프로젝트 진척도를 확인합니다.")
async def project_progress(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    client = interaction.client
    if not isinstance(client, Butler):
        raise RuntimeError("Butler 클라이언트가 필요합니다.")
    settings = await asyncio.to_thread(client.store.get_settings, interaction.guild_id)
    if settings is None:
        await interaction.followup.send("먼저 /setup으로 Jira 프로젝트 URL, 이메일, API 토큰을 설정해주세요.", ephemeral=True)
        return
    try:
        service = ProgressService(settings.base_url, settings.email, settings.api_token)
        result = await asyncio.to_thread(service.fetch, settings.project_url)
    except ProgressError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    message = "조회 가능한 스토리가 없습니다."
    if result.total:
        message = (
            f"프로젝트 진척도: **{result.percentage:.1f}%**\n"
            f"완료 스토리: {result.done}/{result.total}\n"
            f"완료 가중치: {result.done_weight}/{result.total_weight}\n"
            "우선순위 높은 순 가중치: 8·5·3·2·1 / 완료 기준: Done 상태 범주"
        )
    await interaction.followup.send(message, ephemeral=True)


@project_progress.error
async def progress_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    # Avoid logging Jira exceptions, which can contain request authentication details.
    logger.error("프로젝트 진척도 조회 실패: %s", type(error).__name__)
    message = "프로젝트 진척도를 조회하지 못했습니다. 잠시 후 다시 시도해주세요."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN 환경 변수를 설정해주세요.")
    store = ProjectStore(Path("data/butler.sqlite3"))
    Butler(store).run(token)


if __name__ == "__main__":
    main()
