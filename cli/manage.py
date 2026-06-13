"""manage.py — Textual TUI для просмотра агентов и управления скилами.

Две вкладки:
  Агенты  — агенты из [[agents]]-источников config.toml, сгруппированные по
            источнику (имя + описание). Enter открывает модалку с телом `.md`.
  Скилы   — список скилов: статус, имя, источник, описание. Enter открывает
            модалку с `SKILL.md`. Space/`t` включает/выключает скил: правится
            `disabled` в config.local.toml, затем дёргается install (без
            обновления сабмодулей).

Запуск: `uv run claude-agents manage`.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable, Footer, Header, Input, Markdown, Static, TabbedContent, TabPane,
)

from . import config
from .up import run_up


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


class ContentScreen(ModalScreen):
    """Модалка с содержимым .md-файла (агент или скил). Esc/q/Enter — закрыть."""

    BINDINGS = [
        Binding("escape,q,enter", "dismiss", "Закрыть", show=True),
    ]

    def __init__(self, title: str, path: Path) -> None:
        super().__init__()
        self._title = title
        self._path = path

    def compose(self) -> ComposeResult:
        try:
            text = self._path.read_text(errors="replace")
        except OSError as e:
            text = f"# {self._title}\n\nНе удалось прочитать `{self._path}`:\n\n```\n{e}\n```"
        with Container(id="modal-box"):
            yield Static(f"[b]{self._title}[/]  [dim]{self._path}[/]", id="modal-title")
            with VerticalScroll(id="modal-scroll"):
                yield Markdown(text)
            yield Static("[dim]Esc / q / Enter — закрыть · ↑↓ PgUp/PgDn — скролл[/]",
                         id="modal-hint")

    def action_dismiss(self, result=None) -> None:
        self.dismiss()


class AddSubmoduleScreen(ModalScreen):
    """Модалка добавления сабмодуля. Поля: URL, имя (опц.), подпапка (опц.).

    Enter в любом поле / на кнопке-инструкции запускает добавление. Возвращает
    (через dismiss) словарь полей или None при отмене. Сам git-вызов делает
    вызывающая сторона — модалка только собирает ввод.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Отмена", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="modal-box"):
            yield Static("[b]Добавить сабмодуль[/]", id="modal-title")
            with VerticalScroll(id="modal-scroll"):
                yield Static("URL git-репозитория со скилами:")
                yield Input(placeholder="https://github.com/owner/repo", id="sub-url")
                yield Static("Имя папки в contrib/ [dim](пусто = из URL)[/]:")
                yield Input(placeholder="(авто)", id="sub-name")
                yield Static("Подпапка со скилами [dim](пусто = автодетект)[/]:")
                yield Input(placeholder="(автодетект)", id="sub-subdir")
            yield Static("[dim]Enter — добавить · Esc — отмена[/]", id="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#sub-url", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        url = self.query_one("#sub-url", Input).value.strip()
        if not url:
            self.query_one("#modal-hint", Static).update("[yellow]URL обязателен[/]")
            return
        name = self.query_one("#sub-name", Input).value.strip() or None
        subdir = self.query_one("#sub-subdir", Input).value.strip()
        # пусто → автодетект (None); заполнено → точное значение.
        self.dismiss({"url": url, "name": name,
                      "skills_subdir": subdir if subdir else None})

    def action_cancel(self) -> None:
        self.dismiss(None)


class AgentsPane(TabPane):
    """Просмотр агентов. Enter открывает модалку с телом .md."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Параллельно строкам таблицы: Agent или None для строк-разделителей.
        self._row_map: list[config.Agent | None] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="agents-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        table.add_column("Агент", width=20)
        table.add_column("Описание")
        self._row_map = []
        last_source: str | None = None
        for a in config.load_agents():
            if a.source != last_source:
                # Заголовок-разделитель группы источника.
                table.add_row(f"[b]{a.source}[/]", "", height=1)
                self._row_map.append(None)
                last_source = a.source
            table.add_row(f"  {a.name}", _truncate(a.description, 100))
            self._row_map.append(a)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row is None or row >= len(self._row_map):
            return
        a = self._row_map[row]
        if a is None:  # строка-разделитель
            return
        self.app.push_screen(ContentScreen(a.name, a.path))


class SkillsPane(TabPane):
    """Просмотр + toggle. Enter открывает SKILL.md, Space/`t` переключает."""

    BINDINGS = [
        Binding("space,t", "toggle", "Вкл/выкл", show=True),
        Binding("a", "add_submodule", "Добавить сабмодуль", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Параллельно строкам таблицы: Skill или None для строк-разделителей.
        self._row_map: list[config.Skill | None] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="skills-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="skills-status")

    def on_mount(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        table.add_column("◉", width=3)
        table.add_column("Скил", width=26)
        table.add_column("Описание")
        self._reload()
        table.focus()

    def _skill_at_cursor(self) -> config.Skill | None:
        table = self.query_one("#skills-table", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._row_map):
            return None
        return self._row_map[row]

    def _reload(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        prev = table.cursor_row
        table.clear()
        cfg = config.load()
        self._row_map = []

        last_source: str | None = None
        for s in cfg.skills:
            if s.source != last_source:
                # Заголовок-разделитель группы источника.
                table.add_row("", f"[b]{s.source}[/]", "", height=1)
                self._row_map.append(None)
                last_source = s.source
            mark = "[green]●[/]" if s.enabled else "[dim]○[/]"
            name = f"  {s.name}" if s.enabled else f"  [dim]{s.name}[/]"
            table.add_row(mark, name, _truncate(s.description, 80))
            self._row_map.append(s)

        if self._row_map:
            row = min(prev, len(self._row_map) - 1)
            table.move_cursor(row=self._nearest_skill_row(row))
        if cfg.warnings:
            self._status("⚠ " + "; ".join(cfg.warnings[:3]), warn=True)

    def _nearest_skill_row(self, row: int) -> int:
        """Ближайшая строка-скил (не разделитель), начиная с row и вниз/вверх."""
        n = len(self._row_map)
        for r in range(row, n):
            if self._row_map[r] is not None:
                return r
        for r in range(row - 1, -1, -1):
            if self._row_map[r] is not None:
                return r
        return row

    def _status(self, msg: str, *, warn: bool = False) -> None:
        st = self.query_one("#skills-status", Static)
        color = "yellow" if warn else "green"
        st.update(f"[{color}]{msg}[/]")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row is None or row >= len(self._row_map):
            return
        s = self._row_map[row]
        if s is None:  # строка-разделитель
            return
        self.app.push_screen(ContentScreen(s.name, s.path / "SKILL.md"))

    def action_toggle(self) -> None:
        skill = self._skill_at_cursor()
        if skill is None:
            return
        new_enabled = not skill.enabled

        # 1. config.local.toml: добавить/убрать из disabled (версионный не трогаем)
        config.set_disabled(skill.name, disabled=not new_enabled)

        # 2. install (без сабмодулей), вывод глушим в буфер
        buf = io.StringIO()
        with redirect_stdout(buf):
            errors = run_up(skip_submodules=True, quiet=True)

        verb = "включён" if new_enabled else "выключен"
        if errors:
            self._status(f"{skill.name} {verb}, но install с предупреждениями ({errors})",
                         warn=True)
        else:
            self._status(f"{skill.name} {verb} ✓ symlink'и обновлены")
        self._reload()

    def action_add_submodule(self) -> None:
        self.app.push_screen(AddSubmoduleScreen(), self._on_submodule_form)

    def _on_submodule_form(self, fields: dict | None) -> None:
        if not fields:  # отмена
            return
        self._status(f"добавляю сабмодуль {fields['url']} …")
        # git submodule add сетевой — может занять время; выполняем в thread-воркере,
        # чтобы не блокировать UI-поток. Результат рисуем через call_from_thread.
        self._add_submodule_worker(fields)

    @work(thread=True, exclusive=True)
    def _add_submodule_worker(self, fields: dict) -> None:
        from .submodule import add_submodule

        res = add_submodule(
            fields["url"], name=fields["name"],
            skills_subdir=fields["skills_subdir"], quiet=True)
        self.app.call_from_thread(self._on_submodule_done, res)

    def _on_submodule_done(self, res) -> None:
        if res.ok:
            msg = res.message
            if res.install_errors:
                msg += f" (install: {res.install_errors} предупр.)"
            self._status(f"✓ {msg}")
            self._reload()
        else:
            self._status(f"✗ {res.message}", warn=True)


class ManagerApp(App):
    """Корневое приложение: вкладки Агенты / Скилы."""

    TITLE = "Claude Agents Management"

    CSS = """
    Screen { layout: vertical; }
    DataTable { height: 1fr; }
    #skills-status { height: 1; padding: 0 1; }

    ContentScreen { align: center middle; }
    #modal-box {
        width: 90%;
        height: 90%;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    #modal-title { height: 1; padding: 0 1; background: $boost; }
    #modal-scroll { height: 1fr; padding: 0 1; }
    #modal-hint { height: 1; padding: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q,escape", "quit", "Выход", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Container():
            with TabbedContent():
                yield AgentsPane("Агенты", id="tab-agents")
                yield SkillsPane("Скилы", id="tab-skills")
        yield Footer()


def run_manage() -> int:
    ManagerApp().run()
    return 0
