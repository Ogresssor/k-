"""Режим 1 из 3: MCP-сервер.

Запускается локально клиентом (Claude Desktop, Claude Code, Cursor, VS Code,
Cline, Windsurf, Zed, LM Studio и другие клиенты с поддержкой MCP) по stdio.
Ничего не слушает на портах и наружу не публикуется.

Свою логику этот модуль не содержит — он публикует реестр из tools.py.
"""
from __future__ import annotations

try:  # SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import tools
from .prompt import SYSTEM_PROMPT

mcp = _Server("kplus", instructions=SYSTEM_PROMPT)


def _register() -> None:
    """Публикуем реестр как MCP-инструменты.

    Регистрируем сами реализации: SDK выводит схему аргументов из их
    аннотаций типов, а описание берём из реестра — чтобы формулировка
    была одна и та же во всех трёх режимах работы.
    """
    for t in tools.TOOLS:
        mcp.add_tool(t.fn, name=t.name, description=t.description)


_register()


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
