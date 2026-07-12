"""manage.py — Textual TUI для управления обоими доменами.

Три домена (как в config.toml) переключаются клавишей F2 (norton-стиль), а не
вкладкой — чтобы не было табов-над-табами:
  Claude  — вкладки:
    Агенты  — агенты из [[claude.agents]], сгруппированы по источнику. Enter — тело `.md`.
    Скилы   — статус/имя/источник/описание. Enter — `SKILL.md`. Space/`t` вкл/выкл скил
              (или весь источник на заголовке), `g` — глобально; правится `enabled` в
              config.toml/config.local.toml, затем install (без сабмодулей).
    Плагины — [[claude.plugins]]: toggle (локально/глобально), пересборка seed.
    MCP     — [[claude.mcp]]: toggle → ~/.claude.json.
  Files   — dotfiles ([[files.dotfiles]]): просмотр записей (source/target/posthook).
            Тогглов нет — dotfiles не выключаются; Enter показывает детали записи.
  Команды — разовые действия ([[commands.tasks]]): r/Enter запускают команду для
            текущей ОС (с выходом из TUI, чтобы sudo мог спросить пароль).

Запуск: `uv run start manage`.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    ContentSwitcher, DataTable, Footer, Header, Input, Markdown, Static,
    TabbedContent, TabPane,
)

from . import config
from .up import run_up


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _as_json_block(raw: str) -> str:
    """JSON-текст → markdown code-fence ```json (иначе Markdown схлопывает переносы).

    Дополнительно pretty-print (indent=2) — на случай минифицированного файла. Если
    не парсится как JSON — отдаём исходник в fence как есть.
    """
    try:
        pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pretty = raw
    return f"```json\n{pretty}\n```"


def _state_cells(enabled: bool, enabled_base: bool,
                 enabled_local: bool | None) -> tuple[str, str, str]:
    """Три ячейки статуса строки: лампочка (итог) + глобально + локально.

    Лампочка 💡/○ — эффективный статус (итог с учётом оверрайда). «Гл» — значение
    из config.toml, «Лок» — из config.local.toml (— если оверрайда нет). Локальный
    оверрайд подсвечиваем cyan: именно он определяет итог, маскируя глобальное значение.
    """
    lamp = "💡" if enabled else "[dim]○[/]"
    g = "[green]on[/]" if enabled_base else "[dim]off[/]"
    if enabled_local is None:
        loc = "[dim]—[/]"
    else:
        loc = "[cyan]on[/]" if enabled_local else "[cyan]off[/]"
    return lamp, g, loc


def _clear_plugin_flag(plugin: str) -> None:
    """Best-effort снять stale active-флаг плагина при его выключении.

    Плагины вроде caveman/ponytail держат $CLAUDE_CONFIG_DIR/.<plugin>-active, пока
    активны, и по нему statusline рисует HUD-бейдж. После выключения их SessionStart-хук
    больше не запускается и флаг сам не убирается — снимаем его тут, чтобы бейдж пропал
    сразу, а не висел до перезапуска сессии (которая бы его и так не переписала).
    ponytail: имя флага — конвенция .<plugin>-active, эвристика; symlink/чужое не трогаем.
    """
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR")
                or os.environ.get("CLAUDE_HOME")
                or Path.home() / ".claude")
    flag = base / f".{plugin}-active"
    try:
        if flag.is_file() and not flag.is_symlink():
            flag.unlink()
    except OSError:
        pass


class ContentScreen(ModalScreen):
    """Модалка с содержимым .md-файла (агент или скил). Esc/q/Enter — закрыть."""

    BINDINGS = [
        Binding("escape,q,enter", "dismiss", "Close", show=True),
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
                raw = self._path.read_text(errors="replace")
            except OSError as e:
                text = f"# {self._title}\n\nFailed to read `{self._path}`:\n\n```\n{e}\n```"
            else:
                # .json рендерим как code-fence (Markdown иначе схлопывает переносы);
                # .md — как есть (это и есть markdown).
                text = _as_json_block(raw) if self._path.suffix == ".json" else raw
            subtitle = str(self._path)
        with Container(id="modal-box"):
            yield Static(f"[b]{self._title}[/]  [dim]{subtitle}[/]", id="modal-title")
            with VerticalScroll(id="modal-scroll"):
                yield Markdown(text)
            yield Static("[dim]Esc / q / Enter — close · ↑↓ PgUp/PgDn — scroll[/]",
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
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="modal-box"):
            yield Static("[b]Add submodule[/]", id="modal-title")
            with VerticalScroll(id="modal-scroll"):
                yield Static("Git repo URL with skills:")
                yield Input(placeholder="https://github.com/owner/repo", id="sub-url")
                yield Static("Folder name in contrib/ [dim](empty = from URL)[/]:")
                yield Input(placeholder="(auto)", id="sub-name")
                yield Static("Skills subdir [dim](empty = autodetect)[/]:")
                yield Input(placeholder="(autodetect)", id="sub-subdir")
            yield Static("[dim]Enter — add · Esc — cancel[/]", id="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#sub-url", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        url = self.query_one("#sub-url", Input).value.strip()
        if not url:
            self.query_one("#modal-hint", Static).update("[yellow]URL required[/]")
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
        table.add_column("Agent", width=20)
        table.add_column("Description")
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
    открывает plugin.json. 💡 — итоговый статус; «Гл»/«Лок» — глобально/локально
    (— = локального оверрайда нет). ⚠ — SessionStart-хуки.
    """

    BINDINGS = [
        Binding("space,t", "toggle_local", "Toggle (local)", show=True),
        Binding("g", "toggle_global", "Toggle (global)", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[config.Plugin] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="plugins-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="plugins-status")

    def on_mount(self) -> None:
        table = self.query_one("#plugins-table", DataTable)
        table.add_column("💡", width=3)
        table.add_column("Plugin (plugin@marketplace)", width=40)
        table.add_column("Gl", width=5)
        table.add_column("Loc", width=5)
        table.add_column("⚠", width=3)
        table.add_column("Description")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#plugins-table", DataTable)
        prev = table.cursor_row
        table.clear()
        self._row_map = []
        plugins = config.load_plugins()
        for p in plugins:
            lamp, g, loc = _state_cells(p.enabled, p.enabled_base, p.enabled_local)
            ref = p.ref if p.enabled else f"[dim]{p.ref}[/]"
            warn = "[yellow]⚠[/]" if p.session_start_hooks else ""
            table.add_row(lamp, ref, g, loc, warn, _truncate(p.description, 55))
            self._row_map.append(p)
        if self._row_map:
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        if not plugins:
            self._status("No [[plugins]] sources in config.toml", warn=True)

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
        if not new_enabled:  # эффективно выключен → снять stale HUD-флаг
            _clear_plugin_flag(p.plugin)
        verb = "enabled" if new_enabled else "disabled"
        self._status(f"{p.ref} {verb} locally — rebuilding seed…")
        self._rebuild_worker(f"{p.ref} {verb} (local)")

    def action_toggle_global(self) -> None:
        p = self._at_cursor()
        if p is None:
            return
        new_enabled = not p.enabled_base
        config.set_plugin_enabled(p.source, enabled=new_enabled)
        # Итог с учётом локального оверрайда: он маскирует глобальное значение.
        effective = p.enabled_local if p.enabled_local is not None else new_enabled
        if not effective:  # эффективно выключен → снять stale HUD-флаг
            _clear_plugin_flag(p.plugin)
        verb = "enabled" if new_enabled else "disabled"
        masked = " (but local override active)" if p.enabled_local is not None else ""
        self._status(f"{p.ref} {verb} globally{masked} — rebuilding seed…")
        self._rebuild_worker(f"{p.ref} {verb} (global){masked}")

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
            self._status(f"{what}, but with warnings ({errors}). Restart claude.", warn=True)
        else:
            self._status(f"{what} ✓ seed+settings updated. Restart claude.")
        self._reload()


class McpPane(TabPane):
    """Просмотр + toggle MCP-серверов ([[mcp]] → ~/.claude.json user-scope).

    `t`/Space — toggle ЛОКАЛЬНО (config.local.toml); `g` — ГЛОБАЛЬНО (config.toml).
    Оба мержат ~/.claude.json. Enter показывает JSON-спеку сервера. 💡 — итоговый
    статус; «Гл»/«Лок» — глобально/локально (— = локального оверрайда нет).
    """

    BINDINGS = [
        Binding("space,t", "toggle_local", "Toggle (local)", show=True),
        Binding("g", "toggle_global", "Toggle (global)", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[config.McpServer] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="mcp-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="mcp-status")

    def on_mount(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        table.add_column("💡", width=3)
        table.add_column("MCP", width=24)
        table.add_column("Gl", width=5)
        table.add_column("Loc", width=5)
        table.add_column("Command / URL")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        prev = table.cursor_row
        table.clear()
        self._row_map = []
        mcp, _ = config.load_mcp()
        for m in mcp:
            lamp, g, loc = _state_cells(m.enabled, m.enabled_base, m.enabled_local)
            name = m.name if m.enabled else f"[dim]{m.name}[/]"
            srv = m.server or {}
            desc = srv.get("command", "") and (srv["command"] + " " + " ".join(srv.get("args", [])))
            desc = desc or srv.get("url", "")
            table.add_row(lamp, name, g, loc, _truncate(desc, 55))
            self._row_map.append(m)
        if self._row_map:
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        elif not mcp:
            self._status("No [[mcp]] sources in config.toml", warn=True)

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
        verb = "enabled" if new_enabled else "disabled"
        scope = "locally" if local else "globally"
        masked = " (local override active)" if (not local and m.enabled_local is not None) else ""
        self._status(f"{m.name} {verb} {scope}{masked} — merging ~/.claude.json…")
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
            self._status(f"{what}, but with warnings ({errors}). Restart claude.", warn=True)
        else:
            self._status(f"{what} ✓ ~/.claude.json updated. Restart claude.")
        self._reload()


class SkillsPane(TabPane):
    """Просмотр + toggle. Enter открывает SKILL.md, Space/`t` переключает."""

    BINDINGS = [
        Binding("space,t", "toggle_local", "Toggle (local)", show=True),
        Binding("g", "toggle_global", "Toggle (global)", show=True),
        Binding("a", "add_submodule", "Add submodule", show=True),
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
        table.add_column("Skill", width=26)
        table.add_column("Description")
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
        verb = "enabled" if new_enabled else "disabled"
        scope = "locally" if local else "globally"
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
        verb = "enabled" if new_enabled else "disabled"
        scope = "locally" if local else "globally"
        self._apply_and_report(f"source {source} {verb} {scope} ({len(group)} skills)")

    def _apply_and_report(self, what: str) -> None:
        """install (без сабмодулей) + статус + перерисовка."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            # Toggle loose-скила: пересборка seed/merge settings не нужна — быстрый путь.
            errors = run_up(skip_submodules=True, skip_seed=True, skip_settings=True, quiet=True)
        if errors:
            self._status(f"{what}, but install had warnings ({errors})", warn=True)
        else:
            self._status(f"{what} ✓ symlinks updated")
        self._reload()

    def action_add_submodule(self) -> None:
        self.app.push_screen(AddSubmoduleScreen(), self._on_submodule_form)

    def _on_submodule_form(self, fields: dict | None) -> None:
        if not fields:  # отмена
            return
        self._status(f"adding submodule {fields['url']} …")
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
                msg += f" (install: {res.install_errors} warnings)"
            self._status(f"✓ {msg}")
            self._reload()
        else:
            self._status(f"✗ {res.message}", warn=True)


class FilesPane(Container):
    """Домен Files ($HOME): dotfiles ([[files.dotfiles]]) — просмотр.

    Тогглов нет (dotfiles не выключаются, у них нет `enabled`), только просмотр
    записей: источник в репо, target в $HOME (— если только posthook), posthook.
    Enter — детали записи. Не TabPane (домен Files — единственная вьюха, без
    вложенных вкладок), поэтому обычный Container внутри доменной вкладки.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[dict] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="files-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="files-status")

    def on_mount(self) -> None:
        table = self.query_one("#files-table", DataTable)
        table.add_column("Source (repo)", width=28)
        table.add_column("→ $HOME", width=24)
        table.add_column("posthook")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#files-table", DataTable)
        table.clear()
        self._row_map = []
        entries, warnings = config.load_dotfiles()
        for e in entries:
            target = e.get("target") or "[dim]—[/]"
            ph = e.get("posthook") or ""
            ph_cell = _truncate(ph, 60) if ph else "[dim]—[/]"
            table.add_row(e["source"], target, ph_cell)
            self._row_map.append(e)
        st = self.query_one("#files-status", Static)
        if warnings:
            st.update("[yellow]⚠ " + "; ".join(warnings[:3]) + "[/]")
        elif not entries:
            st.update("[yellow]No [[files.dotfiles]] entries in config.toml[/]")
        else:
            st.update("")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row is None or row >= len(self._row_map):
            return
        e = self._row_map[row]
        lines = [f'source = "{e["source"]}"']
        if e.get("target"):
            lines.append(f'target = "{e["target"]}"')
        if e.get("posthook"):
            lines.append(f"posthook = '{e['posthook']}'")
        text = "```toml\n[[files.dotfiles]]\n" + "\n".join(lines) + "\n```"
        self.app.push_screen(ContentScreen(e["source"], text=text))


class CommandsPane(Container):
    """Домен «Команды»: разовые действия ([[commands.tasks]]) — запуск по требованию.

    `r`/Enter запускают команду для текущей ОС. Запуск идёт с выходом из TUI
    (app.suspend) — команда получает реальный терминал, поэтому sudo может спросить
    пароль. cwd = корень репо, в env прокинут `REPO` (абсолютный путь) — чтобы команда
    ссылалась на скрипты репо независимо от cwd. Команда без варианта под текущую ОС
    помечена недоступной и не запускается.
    """

    BINDINGS = [
        Binding("r,enter", "run", "Run", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._row_map: list[config.Task] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="commands-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="commands-status")

    def on_mount(self) -> None:
        table = self.query_one("#commands-table", DataTable)
        table.add_column("💡", width=3)
        table.add_column("Command", width=26)
        table.add_column("Run (this OS)")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#commands-table", DataTable)
        prev = table.cursor_row
        table.clear()
        self._row_map = []
        tasks, warnings = config.load_tasks()
        for t in tasks:
            cmd = t.command
            lamp = "💡" if cmd else "[dim]○[/]"
            title = t.title + (" [yellow]🔒[/]" if t.sudo else "")
            run_cell = _truncate(cmd, 70) if cmd else "[dim]— no variant for this OS[/]"
            table.add_row(lamp, title, run_cell)
            self._row_map.append(t)
        if self._row_map:
            table.move_cursor(row=min(prev, len(self._row_map) - 1))
        st = self.query_one("#commands-status", Static)
        if warnings:
            st.update("[yellow]⚠ " + "; ".join(warnings[:3]) + "[/]")
        elif not tasks:
            st.update("[yellow]No [[commands.tasks]] in config.toml[/]")
        else:
            st.update("[dim]r / Enter — run selected command[/]")

    def _status(self, msg: str, *, warn: bool = False) -> None:
        self.query_one("#commands-status", Static).update(
            f"[{'yellow' if warn else 'green'}]{msg}[/]")

    def _at_cursor(self) -> config.Task | None:
        table = self.query_one("#commands-table", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._row_map):
            return None
        return self._row_map[row]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_run()

    def action_run(self) -> None:
        t = self._at_cursor()
        if t is None:
            return
        cmd = t.command
        if cmd is None:
            self._status(f"{t.title}: no variant for this OS ({sys.platform})", warn=True)
            return
        # Выходим из TUI на время запуска: команда получает реальный терминал (sudo
        # сможет спросить пароль), после — возвращаемся и показываем результат. Запуск в
        # корне репо + env REPO (абсолютный путь) — чтобы команда ссылалась на скрипты
        # репо (напр. "$REPO/scripts/...") независимо от cwd запуска manage.
        env = {**os.environ, "REPO": str(config.REPO_DIR)}
        with self.app.suspend():
            # app.suspend() не чистит обычный буфер терминала — вывод команды лёг бы
            # поверх кадра TUI. Чистим экран + scrollback явно (ANSI): 2J экран, 3J
            # историю, H — курсор в начало.
            print("\x1b[2J\x1b[3J\x1b[H", end="", flush=True)
            print(f"$ {cmd}\n")
            rc = subprocess.run(cmd, shell=True, cwd=config.REPO_DIR, env=env).returncode
            # Пауза перед возвратом: иначе TUI перерисуется поверх вывода и ошибку/итог
            # не успеть прочитать. Ждём Enter (Ctrl-D/Ctrl-C тоже возвращают).
            try:
                input(f"\n[exit {rc} — press Enter to return to manager]")
            except (EOFError, KeyboardInterrupt):
                pass
        if rc == 0:
            self._status(f"{t.title}: done ✓")
        else:
            self._status(f"{t.title}: exited with code {rc}", warn=True)


# Домены верхнего уровня: (id контента, подпись). Порядок = порядок цикла по F2.
_DOMAINS = [
    ("dom-claude", "Claude"),
    ("dom-files", "Files"),
    ("dom-commands", "Commands"),
]


class ManagerApp(App):
    """Корневое приложение: домены Claude (Агенты/Скилы/Плагины/MCP), Files (dotfiles),
    Команды (разовые действия). Переключение доменов — F2 (norton-стиль)."""

    TITLE = "start — manager (Claude · Files · Commands)"

    CSS = """
    Screen { layout: vertical; }
    DataTable { height: 1fr; }
    #skills-status { height: 1; padding: 0 1; }
    #plugins-status { height: 1; padding: 0 1; }
    #mcp-status { height: 1; padding: 0 1; }
    #files-status { height: 1; padding: 0 1; }
    #commands-status { height: 1; padding: 0 1; }
    #domain-bar { height: 1; padding: 0 1; background: $boost; }

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
        Binding("q,escape", "quit", "Quit", show=True),
        Binding("f2", "toggle_domain", "Domain ⇄", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Container():
            # Домены переключаются F2 (norton-стиль), а не вкладкой — чтобы не было
            # табов-над-табами. Виден только один ряд вкладок (Claude); Files/Команды —
            # отдельные вьюхи без вкладок. ContentSwitcher показывает домен по id.
            yield Static(self._domain_bar_text("dom-claude"), id="domain-bar")
            with ContentSwitcher(initial="dom-claude", id="domains"):
                with TabbedContent(id="dom-claude"):
                    yield AgentsPane("Agents", id="tab-agents")
                    yield SkillsPane("Skills", id="tab-skills")
                    yield PluginsPane("Plugins", id="tab-plugins")
                    yield McpPane("MCP", id="tab-mcp")
                yield FilesPane(id="dom-files")
                yield CommandsPane(id="dom-commands")
        yield Footer()

    @staticmethod
    def _domain_bar_text(current: str) -> str:
        """Плашка доменов: активный — инверсией, прочие — тускло. Биндинг F2 виден в футере."""
        return "".join(
            f"[b reverse] {label} [/]" if dom == current else f"[dim] {label} [/]"
            for dom, label in _DOMAINS)

    def action_toggle_domain(self) -> None:
        cs = self.query_one("#domains", ContentSwitcher)
        ids = [dom for dom, _ in _DOMAINS]
        cs.current = ids[(ids.index(cs.current) + 1) % len(ids)]
        self.query_one("#domain-bar", Static).update(self._domain_bar_text(cs.current))
        # У доменов-вьюх (Files/Команды) — одна таблица; сразу под курсор.
        tbl = {"dom-files": "#files-table", "dom-commands": "#commands-table"}.get(cs.current)
        if tbl:
            try:
                self.query_one(tbl, DataTable).focus()
            except Exception:
                pass


def run_manage() -> int:
    ManagerApp().run()
    return 0
