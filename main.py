import asyncio
import logging
import os
from pathlib import Path

import discord
from discord import app_commands

from settings import ProjectStore, normalize_project

logger = logging.getLogger(__name__)


class Butler(discord.Client):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__(intents=discord.Intents.none())
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(setup)

    async def setup_hook(self) -> None:
        await self.tree.sync()


@app_commands.command(name="setup", description="이 서버에서 사용할 Jira 프로젝트를 설정합니다.")
@app_commands.describe(project="Jira 프로젝트 URL (예: https://example.atlassian.net/jira/software/projects/FITZZA)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, project: str) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    try:
        url = normalize_project(project)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    client = interaction.client
    if not isinstance(client, Butler):
        raise RuntimeError("Butler 클라이언트가 필요합니다.")
    await asyncio.to_thread(client.store.set_project, interaction.guild_id, url)
    await interaction.followup.send(f"이 서버의 Jira 프로젝트를 `{url}`로 설정했습니다.", ephemeral=True)


@setup.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "프로젝트 설정에는 '서버 관리' 권한이 필요합니다."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "서버에서만 사용할 수 있습니다."
    else:
        logger.error("프로젝트 설정 실패", exc_info=(type(error), error, error.__traceback__))
        message = "프로젝트 설정을 저장하지 못했습니다. 잠시 후 다시 시도해주세요."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN 환경 변수를 설정해주세요.")
    store = ProjectStore(Path(os.environ.get("DATABASE_PATH", "data/butler.sqlite3")))
    Butler(store).run(token)


if __name__ == "__main__":
    main()
