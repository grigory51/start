"""Разделы TUI для быстрого перехода (`start m <раздел>`).

Вынесено из manage.py, чтобы список читался без импорта textual — его использует
генератор bash-дополнения (cli/completion.py), которому тянуть весь TUI незачем.
"""

from __future__ import annotations

# Дружественное имя раздела → (id домена в ContentSwitcher, опц. id вкладки Claude).
M_TARGETS: dict[str, tuple[str, str | None]] = {
    "claude": ("dom-claude", None),
    "agents": ("dom-claude", "tab-agents"),
    "skills": ("dom-claude", "tab-skills"),
    "plugins": ("dom-claude", "tab-plugins"),
    "mcp": ("dom-claude", "tab-mcp"),
    "files": ("dom-files", None),
    "commands": ("dom-commands", None),
    "scripts": ("dom-commands", None),
}
