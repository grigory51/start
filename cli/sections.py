"""Разделы TUI для быстрого перехода (`start m <раздел>`).

Вынесено из manage.py, чтобы список читался без импорта textual — его использует
генератор bash-дополнения (cli/completion.py), которому тянуть весь TUI незачем.
"""

from __future__ import annotations

# Дружественное имя раздела → (id домена в ContentSwitcher, опц. id вкладки AI).
M_TARGETS: dict[str, tuple[str, str | None]] = {
    "ai": ("dom-ai", None),
    "ai:claude": ("dom-ai", "tab-status"),
    "ai:codex": ("dom-ai", "tab-status"),
    "agents": ("dom-ai", "tab-agents"),
    "skills": ("dom-ai", "tab-skills"),
    "plugins": ("dom-ai", "tab-plugins"),
    "mcp": ("dom-ai", "tab-mcp"),
    "status": ("dom-ai", "tab-status"),
    "files": ("dom-files", None),
    "commands": ("dom-commands", None),
    "scripts": ("dom-commands", None),
}
