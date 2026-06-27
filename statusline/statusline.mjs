#!/usr/bin/env node
/**
 * statusline.mjs — статусбар Claude Code (своя плашка, без зависимостей).
 *
 * Работает на нативных полях stdin Claude Code — без внешних пакетов и sqlite.
 * Линкуется в ~/.claude/ менеджером claude-agents и подключается через
 * settings.json `statusLine` (см. config.toml [statusline]).
 *
 * CC передаёт JSON на stdin (docs: code.claude.com/docs/en/statusline). Используем:
 *   model.display_name                     — модель
 *   rate_limits.five_hour / seven_day      — лимиты использования (used% + resets_at)
 *   cost.total_duration_ms                 — длительность сессии
 *   context_window.used_percentage         — заполнение контекста
 *
 * Дополнительно (не из stdin) читаем ~/.claude/state/focus.json — внешнее состояние
 * серии работы, которое ведёт hook focus-track.sh на UserPromptSubmit. Из него берём
 * «focus» — длительность текущего непрерывного захода (statusline сам по себе stateless,
 * поэтому «время подряд» считать неоткуда, кроме этого файла).
 *
 * Печатает одну строку. Всегда exit 0 — статусбар не должен ронять сессию.
 */

import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

// --- ANSI ---
const C = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  cyan: "\x1b[36m",
  green: "\x1b[38;2;0;255;0m",     // чистый зелёный (как края heat-шкалы)
  yellow: "\x1b[38;2;255;255;0m",  // чистый жёлтый
  red: "\x1b[38;2;255;0;0m",       // чистый красный
  blink: "\x1b[5m",
};

// Цвет лимита по pace-ratio (used% / ожидаемый равномерный расход), дискретно:
//   ≤0.70 зелёный · ≤0.90 жёлтый · ≤1.0 красный · >1.0 красный+мигание (перерасход).
function paceColor(ratio) {
  if (ratio <= 0.70) return C.green;
  if (ratio <= 0.90) return C.yellow;
  if (ratio <= 1.0) return C.red;
  return C.blink + C.red;
}
const sep = `${C.dim} | ${C.reset}`;

// Плавный цвет-«термометр» по заполнению 0..100: зелёный → жёлтый → красный.
// Линейная интерполяция hue 120°(зелёный)→0°(красный) в RGB, 24-bit ANSI (truecolor).
function heat(p) {
  const v = Math.max(0, Math.min(100, p));
  const hue = 120 * (1 - v / 100);  // 120=зелёный, 60=жёлтый, 0=красный
  // HSV→RGB при S=V=1.
  const c = 1, x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  let r, g, b;
  if (hue < 60) [r, g, b] = [c, x, 0];
  else [r, g, b] = [x, c, 0];
  const to = (n) => Math.round(n * 255);
  return `\x1b[38;2;${to(r)};${to(g)};${to(b)}m`;
}

// Процент с цветом-термометром.
function pct(n) {
  if (n == null) return null;
  const v = Math.round(n);
  return `${heat(v)}${v}%${C.reset}`;
}

// Человекочитаемый остаток до resets_at (unix sec) — "2h13m" / "4d14h" / "" если нет.
function untilReset(epochSec) {
  if (!epochSec) return "";
  const ms = epochSec * 1000 - Date.now();
  if (ms <= 0) return "";
  const min = Math.floor(ms / 60000);
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  const m = min % 60;
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m`;
}

// Сокращение числа токенов: 0..999 как есть, 1000+ → "10k"/"150k", 1e6+ → "1M".
function k(n) {
  if (n == null) return "0";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return (m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, "")) + "M";
  }
  if (n >= 1000) return Math.round(n / 1000) + "k";
  return String(n);
}

// Длительность сессии из мс — "0m" / "57m" / "1h05m".
function dur(ms) {
  if (!ms) return "0m";
  const min = Math.floor(ms / 60000);
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

function read() {
  try {
    return JSON.parse(readFileSync(0, "utf8"));
  } catch {
    return {};
  }
}

// «focus» — минуты текущей непрерывной серии работы. Состояние ведёт hook
// focus-track.sh (TSV "<start>\t<last>" в ~/.claude/state/focus.json). Возвращает
// минуты от start. Если файла нет / он не читается / серия протухла (последний промпт
// старше FOCUS_GAP — был перерыв), отдаём 0: focus показываем ВСЕГДА, отсчёт с нуля.
// FOCUS_GAP должен совпадать с GAP в focus-track.sh (10 мин).
const FOCUS_GAP = 600;            // секунд: разрыв больше → серия неактуальна
const FOCUS_NUDGE = 30, FOCUS_WARN = 60, FOCUS_MAX = 120;  // минуты: пороги сигнала
function focusElapsed() {
  const base = process.env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude");
  let start, last;
  try {
    const raw = readFileSync(join(base, "state", "focus.json"), "utf8").trim();
    [start, last] = raw.split("\t").map(Number);
  } catch {
    return 0;
  }
  if (!Number.isFinite(start) || !Number.isFinite(last)) return 0;
  const now = Date.now();
  if (now - last * 1000 > FOCUS_GAP * 1000) return 0;  // серия протухла — отсчёт с нуля
  return Math.floor((now - start * 1000) / 60000);
}

// Ширина терминала. CC запускает statusline child-процессом с piped stdout, поэтому
// process.stdout.columns/$COLUMNS/tput не работают (см. issue anthropics/claude-code
// #22115, #5430). Обходим: поднимаемся по предкам процесса до реального tty (его держит
// процесс CC) и снимаем ширину через `stty -f /dev/<tty> size` (rows cols). null —
// если tty не нашёлся / stty недоступен; вызывающий тогда не выравнивает по краю.
// Приоритет у env CC_STATUSLINE_COLS — ручной оверрайд, если детект подведёт.
function termCols() {
  const override = Number(process.env.CC_STATUSLINE_COLS);
  if (Number.isFinite(override) && override > 0) return override;
  try {
    let pid = process.pid;
    for (let i = 0; i < 12; i++) {
      const line = execSync(`ps -o ppid=,tty= -p ${pid}`, { encoding: "utf8" }).trim();
      if (!line) break;
      const [ppid, tty] = line.split(/\s+/);
      if (tty && tty !== "??" && tty !== "?") {
        const size = execSync(`stty -f /dev/${tty} size 2>/dev/null`, { encoding: "utf8" }).trim();
        const cols = Number(size.split(/\s+/)[1]);
        return Number.isFinite(cols) && cols > 0 ? cols : null;
      }
      if (!ppid || ppid === "1") break;
      pid = Number(ppid);
    }
  } catch { /* нет ps/stty или нет доступа — не выравниваем */ }
  return null;
}

// Видимая ширина строки в ячейках терминала: без ANSI-escape и со счётом эмодзи как 2.
// Нужно для right-align — байтовая длина строки с цветами не равна занятым колонкам.
function visibleWidth(s) {
  // eslint-disable-next-line no-control-regex
  const noAnsi = s.replace(/\x1b\[[0-9;]*m/g, "");
  let w = 0;
  for (const ch of noAnsi) {
    const cp = ch.codePointAt(0);
    // Эмодзи/символы вне BMP и в диапазонах пиктограмм занимают 2 ячейки.
    w += cp >= 0x1100 && (cp >= 0x1f000 || (cp >= 0x2600 && cp <= 0x27bf) || cp >= 0x10000) ? 2 : 1;
  }
  return w;
}

function main() {
  const d = read();
  const parts = [];

  // Бренд + модель.
  const model = d?.model?.display_name;
  if (model) parts.push(`${C.bold}${C.cyan}${model}${C.reset}`);

  // Лимиты использования. Цвет — по PACE (темпу относительно равномерного расхода
  // к текущему моменту окна), а не по абсолютному used%. ratio = used% / expected%,
  // где expected% = доля прошедшего времени окна. ratio<1 зелёный (отстаёшь/экономишь),
  // ≈1 жёлтый (ровно по графику), >1 краснее (ближе к перерасходу). Число показываем used%.
  const FIVE_H = 5 * 3600 * 1000;
  const SEVEN_D = 7 * 86400 * 1000;
  const rl = d?.rate_limits ?? {};
  const limit = (lim, label, windowMs) => {
    if (!lim || lim.used_percentage == null) return null;
    const used = Math.round(lim.used_percentage);
    const reset = untilReset(lim.resets_at);
    const tag = reset ? `${label}(${reset})` : label;
    // pace-ratio = used% / expected%, где expected% — доля прошедшего времени окна
    // (равномерный расход к текущему моменту). Это и цвет, и подпись:
    //   <1 темп ниже графика (экономишь), ≈1 ровно по графику, >1 перерасход.
    // Множитель ×N прямо отвечает «насколько умерить»: ×0.41 — впятеро ниже потолка,
    // ×1.3 — режь темп до ~0.77 от текущего, чтобы вернуться к ×1.
    let color, ratio = null;
    if (lim.resets_at) {
      const remaining = lim.resets_at * 1000 - Date.now();
      const elapsedFrac = Math.max(0, Math.min(1, (windowMs - remaining) / windowMs));
      const expected = elapsedFrac * 100;
      // В самом начале окна (expected≈0) не вспыхиваем красным: считаем pace по факту.
      ratio = expected >= 1 ? lim.used_percentage / expected : (used > 0 ? 2 : 0);
      color = paceColor(ratio);  // ≤.70 зел · ≤.90 жёлт · ≤1 красн · >1 мигание
    } else {
      color = paceColor(used / 100);  // нет resets_at → по абсолютной доле
    }
    const pace = ratio != null ? ` ${C.dim}×${ratio.toFixed(ratio < 10 ? 2 : 0)}${C.reset}` : "";
    return `${C.dim}${tag}: ${C.reset}${color}${used}%${C.reset}${pace}`;
  };
  const lims = [limit(rl.five_hour, "5h", FIVE_H), limit(rl.seven_day, "7d", SEVEN_D)].filter(Boolean);
  if (lims.length) parts.push(lims.join(" "));

  // Сессия.
  if (d?.cost?.total_duration_ms != null) {
    parts.push(`${C.dim}session: ${C.reset}${C.green}${dur(d.cost.total_duration_ms)}${C.reset}`);
  }

  // Контекст: использовано/размер окна, цвет по заполнению (напр. "ctx: 43k/200k").
  const cw = d?.context_window ?? {};
  const size = cw.context_window_size;
  if (size) {
    const used = (cw.total_input_tokens ?? 0) + (cw.total_output_tokens ?? 0);
    const p = cw.used_percentage ?? (used / size) * 100;
    parts.push(`${C.dim}ctx: ${C.reset}${heat(p)}${k(used)}/${k(size)}${C.reset}`);
  } else if (cw.used_percentage != null) {
    parts.push(`${C.dim}ctx: ${C.reset}${pct(cw.used_percentage)}`);
  }

  const left = parts.join(sep);

  // Focus — длительность непрерывного захода (анти-залипание). Прижимаем к ПРАВОМУ краю
  // экрана: между левой частью и focus набиваем пробелы до ширины терминала. Цвет-
  // термометр по доле к FOCUS_MAX, эмодзи на порогах, мигание на переборе.
  const fm = focusElapsed();
  let focusSeg = "";
  if (fm != null) {
    const emoji = fm >= FOCUS_MAX ? " 🔥" : fm >= FOCUS_WARN ? " ⏰" : fm >= FOCUS_NUDGE ? " 🍅" : "";
    const blink = fm >= FOCUS_MAX ? C.blink : "";
    const color = blink + heat(Math.min(100, (fm / FOCUS_MAX) * 100));
    focusSeg = `${C.dim}focus: ${C.reset}${color}${dur(fm * 60000)}${emoji}${C.reset}`;
  }

  if (!focusSeg) {
    process.stdout.write(left);
    return;
  }

  // CC съедает несколько колонок справа (паддинг рамки + место под свои сообщения),
  // поэтому реальная ширина меньше stty cols. Держим запас, чтобы focus не обрезался и
  // не вызвал перенос строки. Подстраивается через env CC_STATUSLINE_MARGIN.
  const RIGHT_MARGIN = Number(process.env.CC_STATUSLINE_MARGIN) || 5;
  const cols = termCols();
  const gap = cols != null
    ? cols - RIGHT_MARGIN - visibleWidth(left) - visibleWidth(focusSeg)
    : null;
  // Если ширину не узнали или строка не влезает — фолбэк: focus просто последним
  // сегментом через обычный разделитель (без выравнивания).
  if (gap != null && gap >= 1) {
    process.stdout.write(left + " ".repeat(gap) + focusSeg);
  } else {
    process.stdout.write(left + sep + focusSeg);
  }
}

main();
