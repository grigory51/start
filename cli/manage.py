"""manage.py — Textual TUI для просмотра агентов и управления скилами.

Две вкладки:
  Агенты  — агенты из [[agents]]-источников config.toml, сгруппированные по
            источнику (имя + описание). Enter открывает модалку с телом `.md`.
  Скилы   — список скилов: статус, имя, источник, описание. Enter открывает
            модалку с `SKILL.md`. Space/`t` включает/выключает скил (или весь
            источник, если курсор на заголовке): правится `enabled`-список
            источника в config.toml, затем дёргается install (без сабмодулей).

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

    def __init__(self, title: str, path: Path | None = None, *, text: str | None = None) -> None:
        super().__init__()
        self._title = title
        self._path = path
        self._text = text  # альтернатива path: показать готовый текст (напр. JSON-спека MCP)

    def compose(self) -> ComposeResult:
        if self._text is not None:
            text = self._text
            subtitle = ""
        else:
            try:
                text = self._path.read_text(errors="replace")
            except OSError as e:
                text = f"# {self._title}\n\nНе удалось прочитать `{self._path}`:\n\n```\n{e}\n```"
            subtitle = str(self._path)
        with Container(id="modal-box"):
            yield Static(f"[b]{self._title}[/]  [dim]{subtitle}[/]", id="modal-title")
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


class PluginsPane(TabPane):
    """Просмотр + toggle нативных CC-плагинов ([[plugins]]).

    `t`/Space — toggle ЛОКАЛЬНО (config.local.toml, эта машина); `g` — ГЛОБАЛЬНО
    (config.toml, для всех машин). Оба пересобирают seed + мержат settings. Enter
    открывает plugin.json. ⚠ — SessionStart-хуки. [L] — есть локальный оверрайд.
    """

    BINDINGS = [
        Binding("space,t", "toggle_local", "Вкл/выкл (локально)", show=True),
        Binding("g", "toggle_global", "Вкл/выкл (глобально)", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[config.Plugin] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="plugins-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="plugins-status")

    def on_mount(self) -> None:
        table = self.query_one("#plugins-table", DataTable)
        table.add_column("◉", width=3)
        table.add_column("Плагин (plugin@marketplace)", width=44)
        table.add_column("⚠", width=3)
        table.add_column("Описание")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#plugins-table", DataTable)
        prev = table.cursor_row
        table.clear()
        self._row_map = []
        plugins = config.load_plugins()
        for p in plugins:
            mark = "[green]●[/]" if p.enabled else "[dim]○[/]"
            ref = p.ref if p.enabled else f"[dim]{p.ref}[/]"
            if p.enabled_local is not None:
                ref += " [cyan][L][/]"  # локальный оверрайд (config.local.toml)
            warn = "[yellow]⚠[/]" if p.session_start_hooks else ""
            table.add_row(mark, ref, warn, _truncate(p.description, 70))
            self._row_map.append(p)
        if self._row_map:
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        if not plugins:
            self._status("Нет [[plugins]]-источников в config.toml", warn=True)

    def _status(self, msg: str, *, warn: bool = False) -> None:
        st = self.query_one("#plugins-status", Static)
        st.update(f"[{'yellow' if warn else 'green'}]{msg}[/]")

    def _at_cursor(self) -> config.Plugin | None:
        table = self.query_one("#plugins-table", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._row_map):
            return None
        return self._row_map[row]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        p = self._at_cursor()
        if p is not None:
            self.app.push_screen(ContentScreen(p.ref, p.path / ".claude-plugin" / "plugin.json"))

    def action_toggle_local(self) -> None:
        p = self._at_cursor()
        if p is None:
            return
        new_enabled = not p.enabled
        config.set_plugin_enabled_local(p.source, enabled=new_enabled)
        verb = "включён" if new_enabled else "выключен"
        self._status(f"{p.ref} {verb} локально — пересобираю seed…")
        self._rebuild_worker(f"{p.ref} {verb} (локально)")

    def action_toggle_global(self) -> None:
        p = self._at_cursor()
        if p is None:
            return
        new_enabled = not p.enabled_base
        config.set_plugin_enabled(p.source, enabled=new_enabled)
        verb = "включён" if new_enabled else "выключен"
        masked = " (но локальный оверрайд активен)" if p.enabled_local is not None else ""
        self._status(f"{p.ref} {verb} глобально{masked} — пересобираю seed…")
        self._rebuild_worker(f"{p.ref} {verb} (глобально){masked}")

    @work(thread=True, exclusive=True)
    def _rebuild_worker(self, what: str) -> None:
        # Полная пересборка: seed + settings (без сабмодулей, без loose-symlink-прунинга
        # — он не нужен для plugin-toggle, но run_up дёшев и идемпотентен).
        buf = io.StringIO()
        with redirect_stdout(buf):
            errors = run_up(skip_submodules=True, quiet=True)
        self.app.call_from_thread(self._rebuild_done, what, errors)

    def _rebuild_done(self, what: str, errors: int) -> None:
        if errors:
            self._status(f"{what}, но с предупреждениями ({errors}). Перезапусти claude.", warn=True)
        else:
            self._status(f"{what} ✓ seed+settings обновлены. Перезапусти claude.")
        self._reload()


class McpPane(TabPane):
    """Просмотр + toggle MCP-серверов ([[mcp]] → ~/.claude.json user-scope).

    `t`/Space — toggle ЛОКАЛЬНО (config.local.toml); `g` — ГЛОБАЛЬНО (config.toml).
    Оба мержат ~/.claude.json. Enter показывает JSON-спеку сервера. [L] — локальный оверрайд.
    """

    BINDINGS = [
        Binding("space,t", "toggle_local", "Вкл/выкл (локально)", show=True),
        Binding("g", "toggle_global", "Вкл/выкл (глобально)", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[config.McpServer] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="mcp-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="mcp-status")

    def on_mount(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        table.add_column("◉", width=3)
        table.add_column("MCP", width=28)
        table.add_column("Команда / URL")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        prev = table.cursor_row
        table.clear()
        self._row_map = []
        mcp, _ = config.load_mcp()
        for m in mcp:
            mark = "[green]●[/]" if m.enabled else "[dim]○[/]"
            name = m.name if m.enabled else f"[dim]{m.name}[/]"
            if m.enabled_local is not None:
                name += " [cyan][L][/]"
            srv = m.server or {}
            desc = srv.get("command", "") and (srv["command"] + " " + " ".join(srv.get("args", [])))
            desc = desc or srv.get("url", "")
            table.add_row(mark, name, _truncate(desc, 60))
            self._row_map.append(m)
        if self._row_map:
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        elif not mcp:
            self._status("Нет [[mcp]]-источников в config.toml", warn=True)

    def _status(self, msg: str, *, warn: bool = False) -> None:
        st = self.query_one("#mcp-status", Static)
        st.update(f"[{'yellow' if warn else 'green'}]{msg}[/]")

    def _at_cursor(self) -> config.McpServer | None:
        table = self.query_one("#mcp-table", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._row_map):
            return None
        return self._row_map[row]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        m = self._at_cursor()
        if m is not None:
            import json
            self.app.push_screen(ContentScreen(
                m.name, text=f"```json\n{json.dumps(m.server or {}, indent=2, ensure_ascii=False)}\n```"))

    def action_toggle_local(self) -> None:
        self._toggle(local=True)

    def action_toggle_global(self) -> None:
        self._toggle(local=False)

    def _toggle(self, *, local: bool) -> None:
        m = self._at_cursor()
        if m is None:
            return
        if local:
            new_enabled = not m.enabled
            config.set_mcp_enabled_local(m.name, enabled=new_enabled)
        else:
            new_enabled = not m.enabled_base
            config.set_mcp_enabled(m.name, enabled=new_enabled)
        verb = "включён" if new_enabled else "выключен"
        scope = "локально" if local else "глобально"
        masked = " (локальный оверрайд активен)" if (not local and m.enabled_local is not None) else ""
        self._status(f"{m.name} {verb} {scope}{masked} — мержу ~/.claude.json…")
        self._merge_worker(f"{m.name} {verb} {scope}{masked}")

    @work(thread=True, exclusive=True)
    def _merge_worker(self, what: str) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            # settings+claude.json merge нужны; seed/сабмодули — нет.
            errors = run_up(skip_submodules=True, skip_seed=True, quiet=True)
        self.app.call_from_thread(self._merge_done, what, errors)

    def _merge_done(self, what: str, errors: int) -> None:
        if errors:
            self._status(f"{what}, но с предупреждениями ({errors}). Перезапусти claude.", warn=True)
        else:
            self._status(f"{what} ✓ ~/.claude.json обновлён. Перезапусти claude.")
        self._reload()


class SkillsPane(TabPane):
    """Просмотр + toggle. Enter открывает SKILL.md, Space/`t` переключает."""

    BINDINGS = [
        Binding("space,t", "toggle_local", "Вкл/выкл (локально)", show=True),
        Binding("g", "toggle_global", "Вкл/выкл (глобально)", show=True),
        Binding("a", "add_submodule", "Добавить сабмодуль", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Параллельно строкам таблицы: Skill — строка-скил; str — строка-заголовок
        # источника (хранит его path, чтобы space по заголовку toggle'ил весь источник).
        self._row_map: list[config.Skill | str] = []

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

    def _at_cursor(self) -> config.Skill | str | None:
        """Объект под курсором: Skill (строка-скил), str (заголовок источника) или None."""
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

        # Сгруппировано по источнику; для заголовка считаем, все ли скилы вкл.
        by_source: dict[str, list[config.Skill]] = {}
        for s in cfg.skills:
            by_source.setdefault(s.source, []).append(s)

        last_source: str | None = None
        for s in cfg.skills:
            if s.source != last_source:
                # Заголовок-разделитель группы источника. Маркер ◉/◐/○ — все/часть/ни одного.
                group = by_source[s.source]
                on = sum(g.enabled for g in group)
                head = "[green]◉[/]" if on == len(group) else ("○" if on == 0 else "[yellow]◐[/]")
                table.add_row(head, f"[b]{s.source}[/]", "", height=1)
                self._row_map.append(s.source)
                last_source = s.source
            mark = "[green]●[/]" if s.enabled else "[dim]○[/]"
            name = f"  {s.name}" if s.enabled else f"  [dim]{s.name}[/]"
            table.add_row(mark, name, _truncate(s.description, 80))
            self._row_map.append(s)

        if self._row_map:
            # Курсор на той же строке (toggle не меняет число строк); без снапа.
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        if cfg.warnings:
            self._status("⚠ " + "; ".join(cfg.warnings[:3]), warn=True)

    def _status(self, msg: str, *, warn: bool = False) -> None:
        st = self.query_one("#skills-status", Static)
        color = "yellow" if warn else "green"
        st.update(f"[{color}]{msg}[/]")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row is None or row >= len(self._row_map):
            return
        s = self._row_map[row]
        if not isinstance(s, config.Skill):  # заголовок источника
            return
        self.app.push_screen(ContentScreen(s.name, s.path / "SKILL.md"))

    def action_toggle_local(self) -> None:
        self._toggle(local=True)

    def action_toggle_global(self) -> None:
        self._toggle(local=False)

    def _toggle(self, *, local: bool) -> None:
        target = self._at_cursor()
        if isinstance(target, str):  # заголовок источника → toggle всему содержимому
            self._toggle_source(target, local=local)
        elif isinstance(target, config.Skill):
            self._toggle_skill(target, local=local)

    def _toggle_skill(self, skill: config.Skill, *, local: bool) -> None:
        new_enabled = not skill.enabled
        if local:
            config.set_skill_enabled_local(skill.source, skill.name, enabled=new_enabled)
        else:
            config.set_skill_enabled(skill.source, skill.name, enabled=new_enabled)
        verb = "включён" if new_enabled else "выключен"
        scope = "локально" if local else "глобально"
        self._apply_and_report(f"{skill.name} {verb} {scope}")

    def _toggle_source(self, source: str, *, local: bool) -> None:
        # Все вкл → выключить весь источник; иначе (часть/ни одного) → включить все.
        cfg = config.load()
        group = [s for s in cfg.skills if s.source == source]
        new_enabled = not (group and all(s.enabled for s in group))
        if local:
            config.set_source_enabled_local(source, enabled=new_enabled)
        else:
            config.set_source_enabled(source, enabled=new_enabled)
        verb = "включён" if new_enabled else "выключен"
        scope = "локально" if local else "глобально"
        self._apply_and_report(f"источник {source} {verb} {scope} ({len(group)} скилов)")

    def _apply_and_report(self, what: str) -> None:
        """install (без сабмодулей) + статус + перерисовка."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            # Toggle loose-скила: пересборка seed/merge settings не нужна — быстрый путь.
            errors = run_up(skip_submodules=True, skip_seed=True, skip_settings=True, quiet=True)
        if errors:
            self._status(f"{what}, но install с предупреждениями ({errors})", warn=True)
        else:
            self._status(f"{what} ✓ symlink'и обновлены")
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
    #plugins-status { height: 1; padding: 0 1; }
    #mcp-status { height: 1; padding: 0 1; }

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
                yield PluginsPane("Плагины", id="tab-plugins")
                yield McpPane("MCP", id="tab-mcp")
        yield Footer()


def run_manage() -> int:
    ManagerApp().run()
    return 0
