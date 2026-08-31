#!/usr/bin/env bash
# Обёртка на случай, если клиент MCP не умеет задавать cwd.
cd "$(dirname "$0")"
exec ./.venv/bin/python -m kplus_mcp.server
