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
 * Печатает одну строку. Всегда exit 0 — статусбар не должен ронять сессию.
 */

import { readFileSync } from "node:fs";

// --- ANSI ---
const C = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  cyan: "\x1b[36m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  red: "\x1b[31m",
};
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

function main() {
  const d = read();
  const parts = [];

  // Бренд + модель.
  const model = d?.model?.display_name;
  if (model) parts.push(`${C.bold}${C.cyan}${model}${C.reset}`);

  // Лимиты использования.
  const rl = d?.rate_limits ?? {};
  const limit = (lim, label) => {
    if (!lim || lim.used_percentage == null) return null;
    const reset = untilReset(lim.resets_at);
    const tag = reset ? `${label}(${reset})` : label;
    return `${C.dim}${tag}: ${C.reset}${pct(lim.used_percentage)}`;
  };
  const lims = [limit(rl.five_hour, "5h"), limit(rl.seven_day, "7d")].filter(Boolean);
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

  process.stdout.write(parts.join(sep));
}

main();
