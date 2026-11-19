#!/usr/bin/env python3
"""
GemPhone — продвинутый мультипровайдерный ИИ-агент для полноценного управления
Android-смартфоном из Termux (ADB + UI Automator + Vision + Termux:API + Код)
с жёстким режимом экономии токенов (Token-Saver) и чётким Vision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
import base64
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import difflib
import getpass
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
try:
    import termios
    import tty

    HAS_TERMIOS = True
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
    HAS_TERMIOS = False
import threading
import time
from typing import Any, Final, Literal
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Опциональные библиотеки (Termux: pip / pkg)
# ---------------------------------------------------------------------------

try:
    import httpx  # type: ignore[import-not-found]

    HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HAS_HTTPX = False

try:
    from PIL import Image, ImageEnhance  # type: ignore[import-not-found]

    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]
    HAS_PIL = False

try:
    from prompt_toolkit import PromptSession  # type: ignore[import-not-found]
    from prompt_toolkit.history import FileHistory  # type: ignore[import-not-found]

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    PromptSession = None  # type: ignore[assignment]
    FileHistory = None  # type: ignore[assignment]
    HAS_PROMPT_TOOLKIT = False

try:
    from rich.console import Console  # type: ignore[import-not-found]
    from rich.markdown import Markdown  # type: ignore[import-not-found]
    from rich.markup import escape as rich_escape  # type: ignore[import-not-found]
    from rich.panel import Panel  # type: ignore[import-not-found]
    from rich.table import Table  # type: ignore[import-not-found]

    HAS_RICH = True
except ImportError:
    Console = None  # type: ignore[assignment]
    Markdown = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    HAS_RICH = False

    def rich_escape(text: str) -> str:
        return text


try:
    import readline  # noqa: F401
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Константы, лимиты экономии токенов и пресеты провайдеров
# ---------------------------------------------------------------------------

APP_DIR: Final[Path] = Path.home() / ".gemphone"
CONFIG_PATH: Final[Path] = APP_DIR / "config.json"
LEGACY_GEMCODE_CONFIG: Final[Path] = Path.home() / ".gemcode.json"
MEMORY_PATH: Final[Path] = APP_DIR / "memory.json"
SESSIONS_DIR: Final[Path] = APP_DIR / "sessions"
HISTORY_PATH: Final[Path] = APP_DIR / "prompt_history.txt"

DEFAULT_PROVIDER: Final[str] = "gemini"
DEFAULT_MAX_STEPS: Final[int] = 10
DEFAULT_TURN_TOKEN_BUDGET: Final[int] = 60_000
DEFAULT_HTTP_TIMEOUT: Final[int] = 120
DEFAULT_CMD_TIMEOUT: Final[int] = 30
MAX_TOOL_OUTPUT_CHARS: Final[int] = 5_000
STALE_TOOL_OUTPUT_CHARS: Final[int] = 400
MAX_READ_LINES: Final[int] = 150
SCREENSHOT_MAX_DIM: Final[int] = 1280
SCREENSHOT_HQ_MAX_DIM: Final[int] = 1920
SCREENSHOT_JPEG_QUALITY: Final[int] = 85
HTTP_USER_AGENT: Final[str] = "GemPhone/3.5 (Android; Termux)"
CANCEL_FLAG_PATH: Final[Path] = APP_DIR / "cancel.flag"

RiskLevel = Literal["safe", "moderate", "dangerous"]
ToolMode = Literal["all", "phone", "code", "chat"]

PROVIDER_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "gemini": {
        "type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "api_key": "",
        "env_var": "GEMINI_API_KEY",
        "model": "gemini-3.8-flash",
        "requires_key": True,
        "supports_vision": True,
        "description": "Google AI Studio (мультимодальный, видит скриншоты)",
    },
    "deepseek": {
        "type": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "env_var": "DEEPSEEK_API_KEY",
        "model": "deepseek-flash",
        "requires_key": True,
        "supports_vision": True,
        "description": "DeepSeek API (V4.1 Flash: Vision, 1M контекст)",
    },
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "env_var": "OPENROUTER_API_KEY",
        "model": "nvidia/nemotron-3.5-super-vl:free",
        "requires_key": True,
        "supports_vision": True,
        "description": "OpenRouter (десятки моделей, включая :free и DeepSeek)",
    },
    "groq": {
        "type": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "env_var": "GROQ_API_KEY",
        "model": "openai/gpt-oss-120b",
        "requires_key": True,
        "supports_vision": False,
        "description": "Groq (сверхбыстрый API)",
    },
    "openai": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "env_var": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "requires_key": True,
        "supports_vision": True,
        "description": "OpenAI (GPT с Vision)",
    },
    "anthropic": {
        "type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "env_var": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "requires_key": True,
        "supports_vision": True,
        "description": "Anthropic (Claude с Vision)",
    },
    "mistral": {
        "type": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "api_key": "",
        "env_var": "MISTRAL_API_KEY",
        "model": "mistral-large-latest",
        "requires_key": True,
        "supports_vision": True,
        "description": "Mistral AI",
    },
    "ollama": {
        "type": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "env_var": "OLLAMA_API_KEY",
        "model": "qwen2.5-coder:7b",
        "requires_key": False,
        "supports_vision": False,
        "description": "Локальная нейросеть (Ollama / llama.cpp без ключа)",
    },
}

ANDROID_KEYEVENTS: Final[dict[str, str]] = {
    "HOME": "KEYCODE_HOME",
    "BACK": "KEYCODE_BACK",
    "RECENTS": "KEYCODE_APP_SWITCH",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
    "POWER": "KEYCODE_POWER",
    "WAKEUP": "KEYCODE_WAKEUP",
    "SLEEP": "KEYCODE_SLEEP",
    "ENTER": "KEYCODE_ENTER",
    "DEL": "KEYCODE_DEL",
    "BACKSPACE": "KEYCODE_DEL",
    "TAB": "KEYCODE_TAB",
    "ESC": "KEYCODE_ESCAPE",
    "VOLUME_UP": "KEYCODE_VOLUME_UP",
    "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
    "VOLUME_MUTE": "KEYCODE_VOLUME_MUTE",
    "MEDIA_PLAY_PAUSE": "KEYCODE_MEDIA_PLAY_PAUSE",
    "MEDIA_NEXT": "KEYCODE_MEDIA_NEXT",
    "MEDIA_PREVIOUS": "KEYCODE_MEDIA_PREVIOUS",
    "PASTE": "KEYCODE_PASTE",
    "COPY": "KEYCODE_COPY",
    "SEARCH": "KEYCODE_SEARCH",
    "NOTIFICATIONS": "KEYCODE_NOTIFICATION",
}

IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
    }
)

SYSTEM_PROMPT: Final[str] = (
    "You are GemPhone, an autonomous AI agent running in Termux on the user's Android phone.\n"
    "Rules:\n"
    "1. Reply concisely in the user's language (usually Russian).\n"
    "2. Minimize tool calls: do not repeat failing actions more than twice; stop and ask if stuck.\n"
    "3. Prefer fast `phone_*` tools (Termux:API) for sensors, notifications, clipboard, SMS, calls, media.\n"
    "4. For UI control via ADB: call `adb_ui_dump` first, then `adb_tap(element_id=...)`. "
    "Use `adb_screenshot` when visual inspection or reading on-screen text is needed (pass `hq=true` for fine print).\n"
    "5. Read on-screen text carefully and literally; never guess or fabricate words that are unclear.\n"
    "6. If ADB is not connected, tell the user to run `/adb`."
)


# ---------------------------------------------------------------------------
# Терминальный интерфейс (Rich + ANSI-fallback)
# ---------------------------------------------------------------------------


class _FallbackSpinner:
    _FRAMES: Final[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, message: str) -> None:
        self._message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = sys.stderr.isatty()

    def __enter__(self) -> _FallbackSpinner:
        if self._active:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            sys.stderr.write(f"\r\033[K\033[2m{frame} {self._message}\033[0m")
            sys.stderr.flush()
            idx += 1
            self._stop.wait(0.09)


class ConsoleUI:
    """Единая обёртка терминального вывода."""

    def __init__(self) -> None:
        self.rich_console = Console() if HAS_RICH else None
        self.ansi = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def width(self) -> int:
        cols = shutil.get_terminal_size(fallback=(60, 24)).columns
        return max(34, min(cols, 84))

    def status(self, message: str) -> Any:
        if self.rich_console is not None:
            return self.rich_console.status(f"[cyan]{rich_escape(message)}[/cyan]", spinner="dots")
        return _FallbackSpinner(message)

    def info(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(rich_escape(text))
        else:
            print(text)

    def dim(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[dim]{rich_escape(text)}[/dim]")
        elif self.ansi:
            print(f"\033[2m{text}\033[0m")
        else:
            print(text)

    def success(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[bold green]{rich_escape(text)}[/bold green]")
        elif self.ansi:
            print(f"\033[32m{text}\033[0m")
        else:
            print(text)

    def warn(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[yellow]{rich_escape(text)}[/yellow]")
        elif self.ansi:
            print(f"\033[33m{text}\033[0m")
        else:
            print(text)

    def error(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[bold red]{rich_escape(text)}[/bold red]")
        elif self.ansi:
            print(f"\033[31m{text}\033[0m")
        else:
            print(text)

    def panel(self, title: str, body: str, style: str = "cyan") -> None:
        if self.rich_console is not None:
            self.rich_console.print(
                Panel(rich_escape(body), title=f"[bold]{rich_escape(title)}[/bold]", border_style=style)
            )
            return
        sep = "-" * self.width()
        print(sep)
        print(f"[{title}]")
        print(body)
        print(sep)

    def markdown(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print()
            self.rich_console.print(
                Panel(
                    Markdown(text),
                    title="[bold cyan]GemPhone[/bold cyan]",
                    border_style="cyan",
                )
            )
            return
        print(f"\nGemPhone >\n{text}")

    def tool_badge(self, name: str, args: dict[str, Any]) -> None:
        items: list[str] = []
        for k, v in args.items():
            s = str(v).replace("\n", "\\n")
            if len(s) > 42:
                s = f"{s[:39]}..."
            items.append(f"{k}={s}")
        joined = ", ".join(items)
        if self.rich_console is not None:
            self.rich_console.print(
                f"  [bold cyan]⚙ {rich_escape(name)}[/bold cyan][dim]({rich_escape(joined)})[/dim]"
            )
        elif self.ansi:
            print(f"  \033[36m⚙ {name}\033[0m\033[2m({joined})\033[0m")
        else:
            print(f"  * {name}({joined})")

    def diff(self, old_text: str, new_text: str, filename: str) -> None:
        lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                n=2,
            )
        )
        if not lines:
            self.dim("(без изменений)")
            return
        limit = 70
        for line in lines[:limit]:
            clean = line.rstrip("\r\n")
            if self.rich_console is not None:
                esc = rich_escape(clean)
                if clean.startswith(("+++", "---")):
                    self.rich_console.print(f"[bold]{esc}[/bold]")
                elif clean.startswith("@@"):
                    self.rich_console.print(f"[cyan]{esc}[/cyan]")
                elif clean.startswith("+"):
                    self.rich_console.print(f"[green]{esc}[/green]")
                elif clean.startswith("-"):
                    self.rich_console.print(f"[red]{esc}[/red]")
                else:
                    self.rich_console.print(f"[dim]{esc}[/dim]")
            else:
                print(clean)
        if len(lines) > limit:
            self.warn(f"... ещё {len(lines) - limit} строк diff скрыто")

    def done_banner(
        self,
        *,
        steps: int,
        tokens: int,
        elapsed: float,
        status: str = "done",
    ) -> None:
        """Выводит заметный маркер завершения задачи в терминале."""
        stats = f"шагов: {steps} | токенов: {format_tokens(tokens)} | время: {elapsed:.1f}с"
        if status == "done":
            label = f"✓ ГОТОВО  ({stats})"
            style = "bold black on green"
            ansi_code = "1;32"
        elif status == "limit":
            label = f"⏸ ПАУЗА: ЛИМИТ ШАГОВ  ({stats})"
            style = "bold black on yellow"
            ansi_code = "1;33"
        else:
            label = f"⏹ ОСТАНОВЛЕНО  ({stats})"
            style = "bold white on red"
            ansi_code = "1;31"

        if self.rich_console is not None:
            self.rich_console.print(f"[{style}] {rich_escape(label)} [/]")
        elif self.ansi:
            print(f"\033[{ansi_code}m=== {label} ===\033[0m")
        else:
            print(f"=== {label} ===")


class CancellationMonitor:
    """
    Отслеживает отмену текущего запроса из трёх источников одновременно:
    1. Клавиши ESC / Enter / Ctrl+C в терминале Termux (без необходимости жать Ctrl+C).
    2. Кнопка «⏹ Отменить» в уведомлении шторки Android (через файл-флаг).
    3. Сигнал SIGINT.
    """

    def __init__(self, cancel_flag: Path = CANCEL_FLAG_PATH) -> None:
        self.cancel_flag = cancel_flag
        self.event = threading.Event()
        self.reason = ""
        self._stop_watcher = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_termios: Any = None
        self._stdin_fd: int | None = None

    def cancel(self, reason: str = "Отменено пользователем") -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()

    def is_cancelled(self) -> bool:
        return self.event.is_set()

    def __enter__(self) -> CancellationMonitor:
        self.event.clear()
        self.reason = ""
        self._stop_watcher.clear()
        try:
            self.cancel_flag.unlink(missing_ok=True)
        except OSError:
            pass

        if HAS_TERMIOS and termios is not None and tty is not None and sys.stdin.isatty():
            try:
                self._stdin_fd = sys.stdin.fileno()
                self._old_termios = termios.tcgetattr(self._stdin_fd)
                tty.setcbreak(self._stdin_fd)
            except Exception:
                self._stdin_fd = None
                self._old_termios = None

        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop_watcher.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if (
            self._stdin_fd is not None
            and self._old_termios is not None
            and HAS_TERMIOS
            and termios is not None
        ):
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass
        try:
            self.cancel_flag.unlink(missing_ok=True)
        except OSError:
            pass

    def _watch_loop(self) -> None:
        while not self._stop_watcher.is_set() and not self.event.is_set():
            if self.cancel_flag.exists():
                self.cancel("Отменено кнопкой из шторки уведомлений")
                return

            if self._stdin_fd is not None:
                try:
                    readable, _, _ = select.select([self._stdin_fd], [], [], 0.08)
                    if readable:
                        ch = os.read(self._stdin_fd, 1)
                        if ch in (b"\x1b", b"\x03", b"\r", b"\n"):
                            label = "ESC" if ch == b"\x1b" else ("Ctrl+C" if ch == b"\x03" else "Enter")
                            self.cancel(f"Отменено клавишей {label}")
                            return
                except Exception:
                    pass
            else:
                self._stop_watcher.wait(0.08)


class PhoneNotifier:
    """
    Фоновые оповещения Android через Termux:API (вибрация, всплывающий тост,
    уведомление в шторке и опционально голос TTS).
    """

    NOTIF_ID: Final[str] = "gemphone_status"

    def __init__(
        self,
        *,
        enabled: bool = True,
        vibrate: bool = True,
        toast: bool = True,
        notification: bool = True,
        tts: bool = False,
    ) -> None:
        self.enabled = enabled
        self.vibrate = vibrate
        self.toast = toast
        self.notification = notification
        self.tts = tts
        self._lock = threading.Lock()
        self._seq = 0

    def is_available(self) -> bool:
        return any(
            shutil.which(cmd) is not None
            for cmd in ("termux-vibrate", "termux-toast", "termux-notification")
        )

    def _run_cmd_async(self, cmd: list[str], *, serialized: bool = False, seq: int | None = None) -> None:
        if shutil.which(cmd[0]) is None:
            return

        def _worker() -> None:
            try:
                if serialized:
                    with self._lock:
                        if seq is not None and seq != self._seq:
                            return
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                else:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _terminal_bell(self) -> None:
        if sys.stdout.isatty():
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except OSError:
                pass

    def notify_working(self, prompt: str) -> None:
        if not self.enabled or not self.notification:
            return
        self._seq += 1
        seq = self._seq
        snippet = prompt.replace("\n", " ").strip()[:80]
        cancel_cmd = f"mkdir -p '{APP_DIR}' && touch '{CANCEL_FLAG_PATH}'"
        self._run_cmd_async(
            [
                "termux-notification",
                "--id",
                self.NOTIF_ID,
                "--title",
                "⏳ GemPhone работает... (ESC в Termux — стоп)",
                "--content",
                snippet or "Выполняю задачу...",
                "--ongoing",
                "--button1",
                "⏹ Отменить",
                "--button1-action",
                cancel_cmd,
            ],
            serialized=True,
            seq=seq,
        )

    def notify_done(self, summary: str, *, steps: int, tokens: int, elapsed: float) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", summary).strip()[:140] or "Задача выполнена."
        meta = f"{steps} ш. · {format_tokens(tokens)} ток. · {elapsed:.1f}с"

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "180"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", f"✅ GemPhone закончил ({meta})"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    f"✅ GemPhone закончил ({meta})",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )
        if self.tts:
            self._run_cmd_async(["termux-tts-speak", "Готово"])

    def notify_attention(self, reason: str) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", reason).strip()[:120]

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "320"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", f"⚠️ GemPhone ждёт ответ: {clean[:60]}"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    "⚠️ GemPhone ждёт ваш ответ",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )

    def notify_error(self, message: str) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", message).strip()[:140]

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "420"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", "❌ GemPhone: ошибка (см. Termux)"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    "❌ GemPhone: ошибка",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )

    def clear(self) -> None:
        if not self.enabled or not self.notification:
            return
        self._seq += 1
        seq = self._seq
        self._run_cmd_async(["termux-notification-remove", self.NOTIF_ID], serialized=True, seq=seq)


# ---------------------------------------------------------------------------
# Утилиты: обрезка текста, форматирование чисел, HTML-парсер
# ---------------------------------------------------------------------------


# Деструктивные команды, которые run_command не выполняет даже после подтверждения.
_DESTRUCTIVE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("рекурсивное удаление корня/дома", re.compile(
        r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)"
        r"(/|/\*|~|~/|\$HOME)(\s|/|\*|$)"
    )),
    ("dd на устройство", re.compile(r"\bdd\b[^\n]*\bof=/dev/(block|sd|mmcblk|zero|sda)")),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:")),
    ("форматирование/mkfs", re.compile(r"\bmkfs(\.[a-z0-9]+)?\b|\bmke2fs\b")),
    ("перезапись диска", re.compile(r"\b(shred|wipefs)\b[^\n]*/dev/")),
    ("терминальный chmod/chown на /", re.compile(r"\bch(mod|own)\s+-R\s+[^\n]*\s/(\s|$)")),
    ("history wipe", re.compile(r"\brm\b[^\n]*(\.bash_history|\.zsh_history)")),
    ("poweroff/reboot через root", re.compile(r"\b(su\s+-c\s+)?(reboot|poweroff|halt)\b")),
    # am start -d/--data с не-http схемой открывает диплинки (tel:, sms:,
    # intent://, market:, системные настройки) в обход подтверждения open_url.
    ("ADB intent с не-http схемой", re.compile(
        r"\badb\b[^\n]*\bshell\b[^\n]*\b(am|monkey)\b[^\n]*"
        r"(-d|--data|data)[ =]*(?!https?://)[a-z][a-z0-9+.-]*:"
    )),
    ("ADB intent с компонентом/flags", re.compile(
        r"\badb\b[^\n]*\bshell\b[^\n]*\bam\s+start\b[^\n]*(-n\s|--component|-f\s|--flags|--user\s)"
    )),
)


def detect_destructive_command(command: str) -> str | None:
    """Возвращает описание сработавшего правила или None, если команда допустима."""
    normalized = " ".join(command.split())
    for label, pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return label
    return None


def truncate_text(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[обрезано ещё {omitted} симв.]"


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def mask_secret(secret: str) -> str:
    if not secret:
        return "(не задан)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return False


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        elif tag.lower() in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "noscript", "svg") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self._chunks.append(cleaned + " ")

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Конфигурация провайдеров, долгосрочная память и сессии
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    name: str
    provider_type: str  # "gemini" | "openai" | "anthropic"
    base_url: str
    model: str
    api_key: str = ""
    env_var: str = ""
    requires_key: bool = True
    supports_vision: bool = True
    description: str = ""

    def effective_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        if self.env_var:
            val = os.environ.get(self.env_var, "").strip()
            if val:
                return val
        if self.provider_type == "gemini":
            return os.environ.get("GOOGLE_API_KEY", "").strip()
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.provider_type,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "env_var": self.env_var,
            "model": self.model,
            "requires_key": self.requires_key,
            "supports_vision": self.supports_vision,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderConfig:
        preset = PROVIDER_PRESETS.get(name, {})
        return cls(
            name=name,
            provider_type=str(data.get("type", preset.get("type", "openai"))).lower(),
            base_url=str(data.get("base_url", preset.get("base_url", ""))).strip(),
            model=str(data.get("model", preset.get("model", ""))).strip(),
            api_key=str(data.get("api_key", "")).strip(),
            env_var=str(data.get("env_var", preset.get("env_var", ""))).strip(),
            requires_key=bool(data.get("requires_key", preset.get("requires_key", True))),
            supports_vision=bool(data.get("supports_vision", preset.get("supports_vision", True))),
            description=str(data.get("description", preset.get("description", ""))),
        )


class ConfigStore:
    """Управляет настройками провайдеров в ~/.gemphone/config.json."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.active_provider: str = DEFAULT_PROVIDER
        self.providers: dict[str, ProviderConfig] = {}
        self.load()

    def load(self) -> None:
        raw: dict[str, Any] = {}
        created = False
        migrated = False

        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        else:
            created = True
            if LEGACY_GEMCODE_CONFIG.exists():
                try:
                    raw = json.loads(LEGACY_GEMCODE_CONFIG.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}

        saved = raw.get("providers")
        if not isinstance(saved, dict):
            saved = {}

        self.providers = {}
        for name, preset in PROVIDER_PRESETS.items():
            merged = {**preset, **(saved.get(name) or {})}
            self.providers[name] = ProviderConfig.from_dict(name, merged)

        for name, pdata in saved.items():
            if name not in self.providers and isinstance(pdata, dict):
                self.providers[name] = ProviderConfig.from_dict(name, pdata)

        ds = self.providers.get("deepseek")
        if ds is not None and ds.model == "deepseek-chat":
            ds.model = "deepseek-flash"
            ds.supports_vision = True
            migrated = True

        gr = self.providers.get("groq")
        if gr is not None and gr.model == "llama-3.3-70b-versatile":
            gr.model = "openai/gpt-oss-120b"
            migrated = True

        env_gemini_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_gemini_model and created and "gemini" in self.providers:
            self.providers["gemini"].model = env_gemini_model

        active = str(raw.get("active_provider", DEFAULT_PROVIDER)).strip()
        self.active_provider = active if active in self.providers else DEFAULT_PROVIDER

        if created or migrated:
            self.save()

    def save(self) -> None:
        payload = {
            "active_provider": self.active_provider,
            "providers": {name: cfg.to_dict() for name, cfg in self.providers.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def get(self, name: str) -> ProviderConfig | None:
        return self.providers.get(name)

    def get_active(self) -> ProviderConfig:
        cfg = self.providers.get(self.active_provider)
        if cfg is None:
            self.active_provider = next(iter(self.providers))
            cfg = self.providers[self.active_provider]
        return cfg


class MemoryStore:
    """Долгосрочная key-value память агента между сессиями."""

    def __init__(self, path: Path = MEMORY_PATH) -> None:
        self.path = path
        self.data: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def set(self, key: str, value: str) -> None:
        self.data[key.strip()] = value.strip()
        self.save()

    def delete(self, key: str) -> bool:
        existed = key in self.data
        if existed:
            del self.data[key]
            self.save()
        return existed


# ---------------------------------------------------------------------------
# Мультимодальные REST-клиенты с автосжатием контекста (Token-Saver)
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RequestCancelledError(Exception):
    """Исключение при ручной отмене активного запроса пользователем."""


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int = 0, completion: int = 0, total: int | None = None) -> None:
        self.prompt_tokens += max(0, int(prompt))
        self.completion_tokens += max(0, int(completion))
        if total is not None and total > 0:
            self.total_tokens += int(total)
        else:
            self.total_tokens += max(0, int(prompt)) + max(0, int(completion))


@dataclass(frozen=True)
class ImageAttachment:
    mime_type: str
    data_b64: str
    label: str = "Phone screenshot"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionResult:
    payload: dict[str, Any]
    image: ImageAttachment | None = None


@dataclass(frozen=True)
class TurnResponse:
    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _extract_error_message(raw_body: str) -> str:
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and "message" in err:
                return str(err["message"])
            if isinstance(err, str):
                return err
            if "message" in data:
                return str(data["message"])
    except json.JSONDecodeError:
        pass
    return raw_body[:350].strip()


def _http_post_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    max_retries: int = 3,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Отправляет HTTP POST с поддержкой мгновенной отмены через cancel_event."""
    if cancel_event is not None and cancel_event.is_set():
        raise RequestCancelledError("Запрос отменён.")

    if cancel_event is None:
        return _http_post_json_sync(
            url=url,
            payload=payload,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
        )

    result_box: list[dict[str, Any]] = []
    error_box: list[BaseException] = []

    def _worker() -> None:
        try:
            res = _http_post_json_sync(
                url=url,
                payload=payload,
                headers=headers,
                timeout=timeout,
                max_retries=max_retries,
                cancel_event=cancel_event,
            )
            result_box.append(res)
        except BaseException as exc:  # noqa: BLE001
            error_box.append(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    while t.is_alive():
        if cancel_event.is_set():
            raise RequestCancelledError("Запрос отменён.")
        t.join(timeout=0.06)

    if cancel_event.is_set():
        raise RequestCancelledError("Запрос отменён.")
    if error_box:
        raise error_box[0]
    if result_box:
        return result_box[0]
    raise ApiError("Пустой ответ от сетевого потока.")


def _http_post_json_sync(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    max_retries: int = 3,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    retryable = {429, 500, 502, 503, 504}
    merged_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": HTTP_USER_AGENT,
        **headers,
    }
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("Запрос отменён.")
        if HAS_HTTPX and httpx is not None:
            try:
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    resp = client.post(url, json=payload, headers=merged_headers)
                if resp.status_code >= 400:
                    msg = _extract_error_message(resp.text) or f"HTTP {resp.status_code}"
                    if resp.status_code == 429 and "limit: 0" in msg:
                        raise ApiError(f"HTTP 429: {msg}", 429)
                    if resp.status_code in retryable and attempt < max_retries:
                        time.sleep(min(12.0, 1.5 * (2**attempt)))
                        last_error = ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                        continue
                    raise ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                return resp.json()
            except ApiError:
                raise
            except (httpx.HTTPError, ValueError, OSError) as exc:
                if attempt < max_retries:
                    time.sleep(min(8.0, 1.5 * (2**attempt)))
                    last_error = exc
                    continue
                raise ApiError(f"Сетевая ошибка: {exc}") from exc
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url=url,
                data=body,
                method="POST",
                headers={**merged_headers, "Connection": "close"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                msg = _extract_error_message(err_body) or f"HTTP {exc.code}"
                if exc.code == 429 and "limit: 0" in msg:
                    raise ApiError(f"HTTP 429: {msg}", 429) from exc
                if exc.code in retryable and attempt < max_retries:
                    time.sleep(min(12.0, 1.5 * (2**attempt)))
                    last_error = ApiError(f"HTTP {exc.code}: {msg}", exc.code)
                    continue
                raise ApiError(f"HTTP {exc.code}: {msg}", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if attempt < max_retries:
                    time.sleep(min(8.0, 1.5 * (2**attempt)))
                    last_error = exc
                    continue
                raise ApiError(f"Сетевая ошибка: {exc}") from exc

    raise ApiError(f"Не удалось выполнить запрос: {last_error}")


def _http_get_text(url: str, timeout: int = 20) -> tuple[int, str, str]:
    headers = {"User-Agent": HTTP_USER_AGENT}
    if HAS_HTTPX and httpx is not None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            return resp.status_code, resp.headers.get("content-type", ""), resp.text

    req = urllib.request.Request(url=url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        raw = resp.read(512_000).decode("utf-8", errors="replace")
        return resp.status, ctype, raw


class BaseLLMClient(ABC):
    """Абстрактный мультимодальный клиент с поддержкой сжатия старого контекста."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        self.config = config
        self.api_key = api_key
        self.model = config.model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return self.config.name

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def snapshot(self) -> int:
        pass

    @abstractmethod
    def rollback(self, marker: int) -> None:
        pass

    @abstractmethod
    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        pass

    @abstractmethod
    def add_user_message(self, text: str) -> None:
        pass

    @abstractmethod
    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        pass

    def prune_stale_context(self) -> None:
        """Удаляет устаревшие скриншоты, старые UI-дампы и сжимает старые выводы инструментов."""

    def finalize_turn(self) -> None:
        """Вызывается по завершении пользовательского хода (очистка старых рассуждений и т.п.)."""

    @abstractmethod
    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        pass


class GeminiRestClient(BaseLLMClient):
    """Нативный клиент Google AI Studio (сохраняет thoughtSignature, Vision и Token-Saver)."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._contents: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._contents.clear()

    def snapshot(self) -> int:
        return len(self._contents)

    def rollback(self, marker: int) -> None:
        del self._contents[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._contents.clear()
        for role, text in transcript[-16:]:
            self._contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": text}]})

    def add_user_message(self, text: str) -> None:
        self._contents.append({"role": "user", "parts": [{"text": text}]})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        parts: list[dict[str, Any]] = []
        attached_images: list[ImageAttachment] = []

        for call, res in results:
            fn_resp: dict[str, Any] = {"name": call.name, "response": res.payload}
            if call.id:
                fn_resp["id"] = call.id
            parts.append({"functionResponse": fn_resp})
            if res.image is not None and self.config.supports_vision:
                attached_images.append(res.image)

        for img in attached_images:
            parts.append({"inlineData": {"mimeType": img.mime_type, "data": img.data_b64}})

        self._contents.append({"role": "user", "parts": parts})

    def prune_stale_context(self) -> None:
        """Оставляет в истории только самый свежий скриншот и последний UI-дамп."""
        seen_image = False
        seen_ui_dump = False
        keep_recent_from = max(0, len(self._contents) - 4)

        for idx in range(len(self._contents) - 1, -1, -1):
            msg = self._contents[idx]
            if msg.get("role") != "user":
                continue
            parts = msg.get("parts")
            if not isinstance(parts, list):
                continue

            new_parts: list[dict[str, Any]] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if "inlineData" in part:
                    if seen_image:
                        new_parts.append({"text": "[old screenshot pruned]"})
                        continue
                    seen_image = True
                    new_parts.append(part)
                    continue

                if "functionResponse" in part and isinstance(part["functionResponse"], dict):
                    fr = part["functionResponse"]
                    name = fr.get("name", "")
                    if name == "adb_ui_dump":
                        if seen_ui_dump:
                            fr["response"] = {"pruned": "older UI dump removed to save tokens"}
                        else:
                            seen_ui_dump = True
                    elif idx < keep_recent_from:
                        serialized = json.dumps(fr.get("response", {}), ensure_ascii=False)
                        if len(serialized) > STALE_TOOL_OUTPUT_CHARS:
                            fr["response"] = {
                                "pruned_preview": serialized[:STALE_TOOL_OUTPUT_CHARS] + "..."
                            }
                new_parts.append(part)
            msg["parts"] = new_parts

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        url = f"{self.config.base_url.rstrip('/')}/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": self._contents,
            "generationConfig": {"temperature": temperature},
        }
        if tool_specs:
            payload["tools"] = [{"functionDeclarations": tool_specs}]

        response = _http_post_json(
            url=url,
            payload=payload,
            headers={"x-goog-api-key": self.api_key},
            timeout=self.timeout,
            cancel_event=cancel_event,
        )

        candidates = response.get("candidates")
        if not candidates:
            reason = response.get("promptFeedback", {}).get("blockReason")
            raise ApiError(f"Модель не вернула ответ (blockReason={reason}).")

        model_content = candidates[0].get("content")
        if not isinstance(model_content, dict):
            raise ApiError("Некорректный ответ Gemini API: отсутствует content.")

        if "role" not in model_content:
            model_content = {**model_content, "role": "model"}
        self._contents.append(model_content)

        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in model_content.get("parts") or []:
            if not isinstance(part, dict) or part.get("thought") is True:
                continue
            if part.get("text"):
                texts.append(str(part["text"]))
            if isinstance(part.get("functionCall"), dict):
                fc = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=str(fc.get("id") or ""),
                        name=str(fc.get("name") or ""),
                        args=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                    )
                )

        usage = response.get("usageMetadata") or {}
        return TurnResponse(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            total_tokens=int(usage.get("totalTokenCount", 0)),
        )


class OpenAICompatClient(BaseLLMClient):
    """
    Универсальный клиент OpenAI-совместимых API с жёсткой экономией токенов:
    - Удаляет устаревшие скриншоты и старые UI-дампы из истории.
    - Сжимает старые длинные выводы инструментов.
    - Очищает `reasoning_content` / `reasoning_details` после завершения хода.
    """

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._messages: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._messages.clear()

    def snapshot(self) -> int:
        return len(self._messages)

    def rollback(self, marker: int) -> None:
        del self._messages[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._messages.clear()
        for role, text in transcript[-16:]:
            self._messages.append({"role": "user" if role == "user" else "assistant", "content": text})

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        images: list[ImageAttachment] = []
        for call, res in results:
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(res.payload, ensure_ascii=False),
                }
            )
            if res.image is not None and self.config.supports_vision:
                images.append(res.image)

        if images:
            content_blocks: list[dict[str, Any]] = [{"type": "text", "text": "Screenshot:"}]
            for img in images:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.mime_type};base64,{img.data_b64}",
                            "detail": "high",
                        },
                    }
                )
            self._messages.append({"role": "user", "content": content_blocks})

    def prune_stale_context(self) -> None:
        """Оставляет только последний скриншот и последний UI-дамп; сжимает старые tool-ответы."""
        seen_image = False
        seen_ui_dump = False
        keep_recent_from = max(0, len(self._messages) - 4)

        for idx in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[idx]
            role = msg.get("role")

            if role == "user" and isinstance(msg.get("content"), list):
                has_img = any(
                    isinstance(blk, dict) and blk.get("type") == "image_url"
                    for blk in msg["content"]
                )
                if has_img:
                    if seen_image:
                        msg["content"] = "[old screenshot pruned to save tokens]"
                    else:
                        seen_image = True

            elif role == "tool":
                name = msg.get("name", "")
                if name == "adb_ui_dump":
                    if seen_ui_dump:
                        msg["content"] = '{"pruned":"older UI dump removed"}'
                    else:
                        seen_ui_dump = True
                elif idx < keep_recent_from:
                    raw = str(msg.get("content", ""))
                    if len(raw) > STALE_TOOL_OUTPUT_CHARS:
                        msg["content"] = raw[:STALE_TOOL_OUTPUT_CHARS] + "...[pruned]"

    def finalize_turn(self) -> None:
        """После завершения хода убираем тяжёлые `reasoning_content`, чтобы не гонять их дальше."""
        for msg in self._messages:
            if msg.get("role") == "assistant":
                msg.pop("reasoning_content", None)
                msg.pop("reasoning_details", None)

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        base = self.config.base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_instruction}, *self._messages],
            "temperature": temperature,
        }
        if tool_specs:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec.get("description", ""),
                        "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
                for spec in tool_specs
            ]
            payload["tool_choice"] = "auto"

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in base:
            headers["HTTP-Referer"] = "https://github.com/gemphone-cli"
            headers["X-Title"] = "GemPhone Android Agent"

        response = _http_post_json(
            url=url,
            payload=payload,
            headers=headers,
            timeout=self.timeout,
            cancel_event=cancel_event,
        )
        choices = response.get("choices")
        if not choices or not isinstance(choices[0], dict):
            raise ApiError("Некорректный ответ API: отсутствует choices.")

        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise ApiError("Некорректный ответ API: отсутствует message.")

        raw_content = msg.get("content")
        raw_calls = msg.get("tool_calls") or []
        record: dict[str, Any] = {
            "role": "assistant",
            "content": raw_content if raw_content is not None else "",
        }
        if raw_calls:
            if msg.get("reasoning_content") is not None:
                record["reasoning_content"] = msg["reasoning_content"]
            if msg.get("reasoning_details") is not None:
                record["reasoning_details"] = msg["reasoning_details"]
            record["tool_calls"] = raw_calls
        self._messages.append(record)

        calls: list[ToolCall] = []
        for idx, tc in enumerate(raw_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments")
            parsed_args: dict[str, Any] = {}
            if isinstance(raw_args, dict):
                parsed_args = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    loaded = json.loads(raw_args)
                    if isinstance(loaded, dict):
                        parsed_args = loaded
                except json.JSONDecodeError:
                    pass
            calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"call_{idx}_{int(time.time())}"),
                    name=str(fn.get("name") or ""),
                    args=parsed_args,
                )
            )

        text = self._extract_text(raw_content)
        usage = response.get("usage") or {}
        return TurnResponse(
            text=text,
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p).strip()
        return ""


class AnthropicRestClient(BaseLLMClient):
    """Нативный клиент Anthropic Claude API (/v1/messages) с поддержкой Vision и Token-Saver."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._messages: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._messages.clear()

    def snapshot(self) -> int:
        return len(self._messages)

    def rollback(self, marker: int) -> None:
        del self._messages[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._messages.clear()
        for role, text in transcript[-16:]:
            self._messages.append({"role": "user" if role == "user" else "assistant", "content": text})

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        blocks: list[dict[str, Any]] = []
        for call, res in results:
            content: Any = json.dumps(res.payload, ensure_ascii=False)
            if res.image is not None and self.config.supports_vision:
                content = [
                    {"type": "text", "text": content},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": res.image.mime_type,
                            "data": res.image.data_b64,
                        },
                    },
                ]
            blocks.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
        self._messages.append({"role": "user", "content": blocks})

    def prune_stale_context(self) -> None:
        seen_image = False
        keep_recent_from = max(0, len(self._messages) - 4)

        for idx in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[idx]
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for blk in msg["content"]:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                content = blk.get("content")
                if isinstance(content, list):
                    has_img = any(isinstance(c, dict) and c.get("type") == "image" for c in content)
                    if has_img:
                        if seen_image:
                            blk["content"] = "[old screenshot pruned]"
                        else:
                            seen_image = True
                elif isinstance(content, str) and idx < keep_recent_from and len(content) > STALE_TOOL_OUTPUT_CHARS:
                    blk["content"] = content[:STALE_TOOL_OUTPUT_CHARS] + "...[pruned]"

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        base = self.config.base_url.rstrip("/")
        url = base if base.endswith("/messages") else f"{base}/messages"
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_instruction,
            "messages": self._messages,
            "temperature": temperature,
        }
        if tool_specs:
            payload["tools"] = [
                {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "input_schema": spec.get("parameters", {"type": "object", "properties": {}}),
                }
                for spec in tool_specs
            ]

        response = _http_post_json(
            url=url,
            payload=payload,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            timeout=self.timeout,
            cancel_event=cancel_event,
        )
        blocks = response.get("content")
        if not isinstance(blocks, list):
            raise ApiError("Некорректный ответ Anthropic API: отсутствует content.")

        self._messages.append({"role": "assistant", "content": blocks})
        texts: list[str] = []
        calls: list[ToolCall] = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text" and blk.get("text"):
                texts.append(str(blk["text"]))
            elif blk.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(blk.get("id") or ""),
                        name=str(blk.get("name") or ""),
                        args=blk.get("input") if isinstance(blk.get("input"), dict) else {},
                    )
                )

        usage = response.get("usage") or {}
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        return TurnResponse(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        )


def create_llm_client(config: ProviderConfig, api_key: str) -> BaseLLMClient:
    ptype = config.provider_type.lower()
    if ptype == "gemini":
        return GeminiRestClient(config=config, api_key=api_key)
    if ptype == "anthropic":
        return AnthropicRestClient(config=config, api_key=api_key)
    if ptype == "openai":
        return OpenAICompatClient(config=config, api_key=api_key)
    raise ValueError(f"Неподдерживаемый тип API: '{ptype}'")


# ---------------------------------------------------------------------------
# Ядро управления Android: ADB + UI Automator + Termux:API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiElement:
    element_id: int
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    center_x: int
    center_y: int
    bounds: tuple[int, int, int, int]
    clickable: bool
    scrollable: bool
    editable: bool
    focused: bool

    def to_compact_line(self) -> str:
        """Сверхкомпактная строка (в ~4 раза меньше токенов, чем JSON-объект)."""
        flags: list[str] = []
        if self.clickable:
            flags.append("c")
        if self.editable:
            flags.append("in")
        if self.scrollable:
            flags.append("sc")
        if self.focused:
            flags.append("foc")
        label = self.text or self.content_desc
        if self.content_desc and self.text and self.content_desc != self.text:
            label = f"{self.text} ({self.content_desc})"
        label = label[:55]
        res = self.resource_id.split("/")[-1][:25] if self.resource_id else ""
        flag_str = f"[{','.join(flags)}]" if flags else ""
        res_str = f" #{res}" if res else ""
        lbl_str = f' "{label}"' if label else ""
        return f"{self.element_id}:{flag_str}{lbl_str}{res_str} @{self.center_x},{self.center_y}"


class PhoneController:
    """Высокоуровневый драйвер Android для Termux (ADB + Termux:API)."""

    _BOUNDS_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

    def __init__(self, target_serial: str | None = None) -> None:
        self.target_serial = target_serial
        self.last_ui_elements: dict[int, UiElement] = {}
        self._screen_size_cache: tuple[int, int] | None = None
        self.screenshot_max_dim: int = SCREENSHOT_MAX_DIM
        self.screenshot_quality: int = SCREENSHOT_JPEG_QUALITY

    def has_adb(self) -> bool:
        return shutil.which("adb") is not None

    def has_termux_api(self) -> bool:
        return shutil.which("termux-battery-status") is not None

    def adb_devices(self) -> list[dict[str, str]]:
        if not self.has_adb():
            return []
        proc = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=8)
        devices: list[dict[str, str]] = []
        for line in proc.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices

    def active_adb_serial(self) -> str | None:
        devices = [d for d in self.adb_devices() if d["state"] == "device"]
        if not devices:
            return None
        if self.target_serial:
            for d in devices:
                if d["serial"] == self.target_serial:
                    return self.target_serial
        return devices[0]["serial"]

    def run_adb(
        self,
        args: list[str],
        *,
        timeout: int = 15,
        binary: bool = False,
        require_device: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        if not self.has_adb():
            raise RuntimeError("`adb` не установлен (pkg install android-tools).")
        cmd = ["adb"]
        if require_device:
            serial = self.active_adb_serial()
            if not serial:
                raise RuntimeError("Нет ADB-устройств. Подключите командой `/adb`.")
            cmd.extend(["-s", serial])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)

    def run_termux_cmd(
        self,
        cmd_name: str,
        args: list[str] | None = None,
        *,
        stdin_text: str | None = None,
        timeout: int = 15,
        parse_json: bool = False,
    ) -> Any:
        if shutil.which(cmd_name) is None:
            raise RuntimeError(f"`{cmd_name}` не найден (pkg install termux-api + приложение Termux:API).")
        proc = subprocess.run(
            [cmd_name, *(args or [])],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"{cmd_name} exit {proc.returncode}: {err}")
        out = proc.stdout.strip()
        if parse_json:
            return json.loads(out) if out else {}
        return out

    def get_screen_size(self) -> tuple[int, int]:
        if self._screen_size_cache is not None:
            return self._screen_size_cache
        proc = self.run_adb(["shell", "wm", "size"], timeout=8)
        match = re.search(r"(\d+)\s*x\s*(\d+)", proc.stdout)
        if match:
            self._screen_size_cache = (int(match.group(1)), int(match.group(2)))
            return self._screen_size_cache
        return (1080, 2400)

    def get_foreground_app(self) -> str:
        proc = self.run_adb(["shell", "dumpsys", "window"], timeout=10)
        for pattern in (
            r"mCurrentFocus=Window\{[^\}]+\s+([^\s\}]+)\}",
            r"mFocusedApp=[^\n]*ActivityRecord\{[^\}]+\s+([^\s\}]+)",
        ):
            m = re.search(pattern, proc.stdout)
            if m:
                return m.group(1)
        return "unknown"

    def dump_ui(self, *, only_interactive: bool = False) -> dict[str, Any]:
        """Снимает XML-дерево экрана и возвращает сверхкомпактный список элементов (до 60 шт.)."""
        remote_path = "/sdcard/gemphone_ui.xml"
        dump_proc = self.run_adb(["shell", "uiautomator", "dump", remote_path], timeout=20)
        if dump_proc.returncode != 0:
            raise RuntimeError(f"uiautomator dump failed: {(dump_proc.stderr or dump_proc.stdout).strip()}")

        cat_proc = self.run_adb(["shell", "cat", remote_path], timeout=12)
        xml_raw = cat_proc.stdout.strip()
        if not xml_raw.startswith("<"):
            xml_start = xml_raw.find("<?xml")
            if xml_start != -1:
                xml_raw = xml_raw[xml_start:]
        if not xml_raw:
            raise RuntimeError("Пустой XML от uiautomator. Попробуйте adb_screenshot.")

        root = ET.fromstring(xml_raw)
        elements: list[UiElement] = []
        next_id = 1

        for node in root.iter("node"):
            attrib = node.attrib
            m = self._BOUNDS_RE.match(attrib.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
            if x2 <= x1 or y2 <= y1:
                continue

            text = (attrib.get("text") or "").strip()
            desc = (attrib.get("content-desc") or "").strip()
            res_id = (attrib.get("resource-id") or "").strip()
            cls_name = (attrib.get("class") or "").strip()
            clickable = attrib.get("clickable") == "true"
            scrollable = attrib.get("scrollable") == "true"
            focused = attrib.get("focused") == "true"
            editable = "EditText" in cls_name or "AutoComplete" in cls_name

            is_interactive = clickable or scrollable or editable or focused
            has_label = bool(text or desc)

            if only_interactive and not is_interactive:
                continue
            if not (is_interactive or has_label):
                continue

            elements.append(
                UiElement(
                    element_id=next_id,
                    text=text,
                    content_desc=desc,
                    resource_id=res_id,
                    class_name=cls_name,
                    center_x=(x1 + x2) // 2,
                    center_y=(y1 + y2) // 2,
                    bounds=(x1, y1, x2, y2),
                    clickable=clickable,
                    scrollable=scrollable,
                    editable=editable,
                    focused=focused,
                )
            )
            next_id += 1
            if len(elements) >= 60:
                break

        self.last_ui_elements = {e.element_id: e for e in elements}
        w, h = self.get_screen_size()
        return {
            "screen": f"{w}x{h}",
            "app": self.get_foreground_app(),
            "legend": "id:[c=click,in=input,sc=scroll] \"text\" #res @x,y",
            "ui": "\n".join(e.to_compact_line() for e in elements),
        }

    def capture_screenshot(
        self,
        *,
        save_path: Path | None = None,
        max_dim: int | None = None,
        quality: int | None = None,
        hq: bool = False,
    ) -> tuple[dict[str, Any], ImageAttachment]:
        proc = self.run_adb(["exec-out", "screencap", "-p"], timeout=20, binary=True)
        raw_png: bytes = proc.stdout
        if proc.returncode != 0 or len(raw_png) < 100:
            raise RuntimeError("Не удалось снять скриншот (FLAG_SECURE или обрыв ADB).")

        target_dim = SCREENSHOT_HQ_MAX_DIM if hq else (max_dim or self.screenshot_max_dim)
        target_qual = 92 if hq else (quality or self.screenshot_quality)

        mime_type = "image/png"
        out_bytes = raw_png
        dimensions = "original"

        if HAS_PIL and Image is not None:
            try:
                img = Image.open(io.BytesIO(raw_png))
                orig_w, orig_h = img.size
                if max(orig_w, orig_h) > target_dim:
                    resampling = getattr(Image, "Resampling", Image).LANCZOS
                    img.thumbnail((target_dim, target_dim), resample=resampling)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                if ImageEnhance is not None:
                    img = ImageEnhance.Sharpness(img).enhance(1.25)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=target_qual, optimize=True, subsampling=0)
                out_bytes = buf.getvalue()
                mime_type = "image/jpeg"
                dimensions = f"{img.size[0]}x{img.size[1]}"
            except Exception:
                out_bytes = raw_png
                mime_type = "image/png"

        saved_to = ""
        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(out_bytes)
            saved_to = str(save_path)

        b64 = base64.b64encode(out_bytes).decode("ascii")
        meta = {
            "ok": True,
            "app": self.get_foreground_app(),
            "dim": dimensions,
            "kb": round(len(out_bytes) / 1024, 1),
            "saved": saved_to or None,
        }
        return meta, ImageAttachment(mime_type=mime_type, data_b64=b64)

    def tap(
        self,
        *,
        element_id: int | None = None,
        x: int | None = None,
        y: int | None = None,
        long_press: bool = False,
        duration_ms: int = 700,
    ) -> dict[str, Any]:
        label = ""
        if element_id is not None:
            elem = self.last_ui_elements.get(element_id)
            if elem is None:
                raise ValueError(f"Элемент id={element_id} не найден. Сначала вызовите adb_ui_dump.")
            x, y = elem.center_x, elem.center_y
            label = elem.text or elem.content_desc or elem.resource_id

        if x is None or y is None:
            raise ValueError("Укажите element_id или x, y.")

        if long_press:
            dur = max(300, min(int(duration_ms), 4000))
            self.run_adb(["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(dur)])
            action = f"long_press({dur}ms)"
        else:
            self.run_adb(["shell", "input", "tap", str(x), str(y)])
            action = "tap"

        return {"ok": True, "action": action, "x": x, "y": y, "label": label or None}

    def swipe(
        self,
        *,
        direction: str | None = None,
        x1: int | None = None,
        y1: int | None = None,
        x2: int | None = None,
        y2: int | None = None,
        duration_ms: int = 320,
    ) -> dict[str, Any]:
        w, h = self.get_screen_size()
        if direction:
            d = direction.strip().lower()
            if d == "down":
                x1, y1, x2, y2 = w // 2, int(h * 0.78), w // 2, int(h * 0.25)
            elif d == "up":
                x1, y1, x2, y2 = w // 2, int(h * 0.25), w // 2, int(h * 0.78)
            elif d == "left":
                x1, y1, x2, y2 = int(w * 0.85), h // 2, int(w * 0.15), h // 2
            elif d == "right":
                x1, y1, x2, y2 = int(w * 0.15), h // 2, int(w * 0.85), h // 2
            else:
                raise ValueError("direction: down, up, left, right.")

        if None in (x1, y1, x2, y2):
            raise ValueError("Укажите direction или x1, y1, x2, y2.")

        dur = max(50, min(int(duration_ms), 3000))
        self.run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(dur)])
        return {"ok": True, "from": [x1, y1], "to": [x2, y2]}

    def type_text(self, text: str, *, press_enter: bool = False) -> dict[str, Any]:
        is_simple_ascii = bool(re.fullmatch(r"[A-Za-z0-9._:/@+-]+", text))
        method = "adb_input"

        if is_simple_ascii:
            self.run_adb(["shell", "input", "text", text])
        elif shutil.which("termux-clipboard-set") is not None:
            self.run_termux_cmd("termux-clipboard-set", stdin_text=text)
            time.sleep(0.15)
            self.run_adb(["shell", "input", "keyevent", "279"])
            method = "clipboard_paste"
        else:
            escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
            self.run_adb(["shell", "input", "text", escaped])
            method = "adb_fallback"

        if press_enter:
            time.sleep(0.1)
            self.run_adb(["shell", "input", "keyevent", "KEYCODE_ENTER"])

        return {"ok": True, "method": method, "chars": len(text)}

    def keyevent(self, key: str) -> dict[str, Any]:
        keycode = ANDROID_KEYEVENTS.get(key.strip().upper(), key.strip().upper())
        self.run_adb(["shell", "input", "keyevent", keycode])
        return {"ok": True, "keycode": keycode}

    def app_control(
        self,
        action: str,
        *,
        package: str = "",
        query: str = "",
        url: str = "",
    ) -> dict[str, Any]:
        act = action.strip().lower()
        if act == "current":
            return {"app": self.get_foreground_app()}
        if act == "list":
            proc = self.run_adb(["shell", "pm", "list", "packages", "-3"], timeout=12)
            pkgs = [ln.replace("package:", "").strip() for ln in proc.stdout.splitlines() if ln.startswith("package:")]
            if query:
                q = query.lower()
                pkgs = [p for p in pkgs if q in p.lower()]
            pkgs.sort()
            return {"count": len(pkgs), "packages": pkgs[:60]}
        if act == "launch":
            if not package:
                raise ValueError("Укажите package.")
            proc = self.run_adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=12)
            return {"ok": proc.returncode == 0, "package": package}
        if act == "stop":
            if not package:
                raise ValueError("Укажите package.")
            self.run_adb(["shell", "am", "force-stop", package], timeout=10)
            return {"ok": True, "stopped": package}
        if act == "open_url":
            if not url:
                raise ValueError("Укажите url.")
            self.run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url])
            return {"ok": True, "url": url}
        raise ValueError("action: current, list, launch, stop, open_url.")


# ---------------------------------------------------------------------------
# Единый реестр инструментов с компактными схемами и профилями (/mode)
# ---------------------------------------------------------------------------


@dataclass
class ApprovalPolicy:
    auto_approve_moderate: bool = False
    auto_approve_dangerous: bool = False
    non_interactive: bool = False


PHONE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "adb_status",
        "adb_ui_dump",
        "adb_screenshot",
        "adb_tap",
        "adb_swipe",
        "adb_type_text",
        "adb_keyevent",
        "adb_app_control",
        "phone_sensors",
        "phone_controls",
        "phone_notifications",
        "phone_clipboard",
        "phone_comms",
        "phone_media",
        "memory",
    }
)

CODE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "list_files",
        "read_file",
        "search_text",
        "edit_file",
        "write_file",
        "run_command",
        "web_fetch",
        "memory",
    }
)


class AgentToolkit:
    """Набор инструментов управления смартфоном, файлами и вебом."""

    def __init__(
        self,
        workspace: Path,
        phone: PhoneController,
        memory: MemoryStore,
        ui: ConsoleUI,
        policy: ApprovalPolicy,
        mode: ToolMode = "all",
        notifier: PhoneNotifier | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.phone = phone
        self.memory = memory
        self.ui = ui
        self.policy = policy
        self.mode: ToolMode = mode
        self.notifier = notifier
        self._dispatch: dict[str, Callable[[dict[str, Any]], ToolExecutionResult]] = {
            "adb_status": self._tool_adb_status,
            "adb_ui_dump": self._tool_adb_ui_dump,
            "adb_screenshot": self._tool_adb_screenshot,
            "adb_tap": self._tool_adb_tap,
            "adb_swipe": self._tool_adb_swipe,
            "adb_type_text": self._tool_adb_type_text,
            "adb_keyevent": self._tool_adb_keyevent,
            "adb_app_control": self._tool_adb_app_control,
            "phone_sensors": self._tool_phone_sensors,
            "phone_controls": self._tool_phone_controls,
            "phone_notifications": self._tool_phone_notifications,
            "phone_clipboard": self._tool_phone_clipboard,
            "phone_comms": self._tool_phone_comms,
            "phone_media": self._tool_phone_media,
            "list_files": self._tool_list_files,
            "read_file": self._tool_read_file,
            "search_text": self._tool_search_text,
            "edit_file": self._tool_edit_file,
            "write_file": self._tool_write_file,
            "run_command": self._tool_run_command,
            "web_fetch": self._tool_web_fetch,
            "memory": self._tool_memory,
        }

    def tool_specs(self) -> list[dict[str, Any]]:
        """Компактные схемы инструментов (лаконичный English снижает расход токенов в ~2.5x)."""
        if self.mode == "chat":
            return []

        all_specs: list[dict[str, Any]] = [
            {
                "name": "adb_status",
                "description": "ADB connection status, screen size, foreground app.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "adb_ui_dump",
                "description": "Dump Android UI elements with id and x,y coords.",
                "parameters": {
                    "type": "object",
                    "properties": {"only_interactive": {"type": "boolean"}},
                },
            },
            {
                "name": "adb_screenshot",
                "description": "Capture phone screenshot for visual inspection (set hq=true to read small text clearly).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "save_as": {"type": "string"},
                        "hq": {"type": "boolean"},
                    },
                },
            },
            {
                "name": "adb_tap",
                "description": "Tap or long-press by element_id (from adb_ui_dump) or x,y.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "long_press": {"type": "boolean"},
                    },
                },
            },
            {
                "name": "adb_swipe",
                "description": "Swipe screen by direction (down/up/left/right) or x1,y1,x2,y2.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["down", "up", "left", "right"]},
                        "x1": {"type": "integer"},
                        "y1": {"type": "integer"},
                        "x2": {"type": "integer"},
                        "y2": {"type": "integer"},
                        "duration_ms": {"type": "integer"},
                    },
                },
            },
            {
                "name": "adb_type_text",
                "description": "Type text (supports Cyrillic/Unicode) into focused field.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "press_enter": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "adb_keyevent",
                "description": "Press Android key: HOME, BACK, RECENTS, ENTER, DEL, VOLUME_UP/DOWN, MEDIA_PLAY_PAUSE.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
            {
                "name": "adb_app_control",
                "description": "Manage Android apps: current, list, launch, stop, open_url.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["current", "list", "launch", "stop", "open_url"]},
                        "package": {"type": "string"},
                        "query": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_sensors",
                "description": "Termux:API sensors: battery, wifi, wifi_scan, location, volume.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["battery", "wifi", "wifi_scan", "location", "volume"]},
                        "provider": {"type": "string", "enum": ["network", "gps", "passive"]},
                    },
                    "required": ["kind"],
                },
            },
            {
                "name": "phone_controls",
                "description": "Termux:API hardware control: torch(on/off), brightness(0..255/auto), volume, vibrate(ms), toast, open_url.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["torch", "brightness", "volume", "vibrate", "toast", "open_url"]},
                        "value": {"type": "string"},
                        "stream": {"type": "string", "enum": ["music", "ring", "alarm", "notification", "call", "system"]},
                    },
                    "required": ["action", "value"],
                },
            },
            {
                "name": "phone_notifications",
                "description": "Android notifications: list, send, remove.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "send", "remove"]},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "notif_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_clipboard",
                "description": "Get or set Android clipboard.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["get", "set"]},
                        "text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_comms",
                "description": "Contacts, SMS, calls (send_sms/call ask confirmation).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search_contacts", "list_sms", "send_sms", "call"]},
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "number": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_media",
                "description": "TTS speak, camera photo, or mic recording.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["tts_speak", "camera_photo", "mic_record_start", "mic_record_stop"]},
                        "text": {"type": "string"},
                        "camera_id": {"type": "integer"},
                        "output_file": {"type": "string"},
                        "duration_sec": {"type": "integer"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "list_files",
                "description": "List files in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "max_depth": {"type": "integer"}},
                },
            },
            {
                "name": "read_file",
                "description": "Read text file lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "search_text",
                "description": "Search text/regex in workspace files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "is_regex": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "edit_file",
                "description": "Replace unique old_text with new_text in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
            {
                "name": "run_command",
                "description": "Run Termux shell command (asks confirmation).",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                    "required": ["command"],
                },
            },
            {
                "name": "web_fetch",
                "description": "Fetch webpage/API URL as plain text.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
            {
                "name": "memory",
                "description": "Persistent key-value memory across sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "set", "delete"]},
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
        ]

        if self.mode == "phone":
            return [s for s in all_specs if s["name"] in PHONE_TOOLS]
        if self.mode == "code":
            return [s for s in all_specs if s["name"] in CODE_TOOLS]
        return all_specs

    def execute(self, name: str, args: dict[str, Any] | None) -> ToolExecutionResult:
        handler = self._dispatch.get(name)
        if handler is None:
            return ToolExecutionResult({"error": f"Неизвестный инструмент: {name}"})
        safe_args = args if isinstance(args, dict) else {}
        try:
            return handler(safe_args)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return ToolExecutionResult({"error": str(exc)})
        except Exception as exc:
            return ToolExecutionResult({"error": f"Ошибка {name}: {exc}"})

    # --- Безопасность и подтверждение ---

    def _confirm(self, summary: str, *, risk: RiskLevel) -> tuple[bool, str]:
        if risk == "safe":
            return True, ""
        if risk == "moderate" and self.policy.auto_approve_moderate:
            return True, ""
        if risk == "dangerous" and self.policy.auto_approve_dangerous:
            return True, ""
        if self.policy.non_interactive:
            return False, "Отклонено: неинтерактивный режим без --yolo."

        badge = "⚠️ ОПАСНОЕ ДЕЙСТВИЕ" if risk == "dangerous" else "Подтверждение"
        opts = (
            "[y] Да / [n] Нет / [a] Разрешать опасные"
            if risk == "dangerous"
            else "[y] Да / [n] Нет / [a] Всегда (экран/файлы)"
        )
        self.ui.warn(f"{badge}: {summary}\n  {opts}")
        if self.notifier is not None:
            self.notifier.notify_attention(summary)
        try:
            ans = input("  Выбор > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False, "Отменено пользователем."

        if ans in ("y", "yes", "д", "да"):
            return True, ""
        if ans in ("a", "always", "в", "всегда"):
            if risk == "dangerous":
                self.policy.auto_approve_dangerous = True
            else:
                self.policy.auto_approve_moderate = True
            self.ui.dim("  -> Автоподтверждение включено до конца сессии.")
            return True, ""
        return False, f"Пользователь отклонил действие ('{ans or 'n'}')."

    def _resolve_safe_path(self, raw_path: str) -> Path:
        candidate = (self.workspace / raw_path).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError(f"Путь '{raw_path}' выходит за пределы {self.workspace}")
        return candidate

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target)
        except OSError:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            target.write_text(content, encoding="utf-8")

    # --- ADB-инструменты ---

    def _tool_adb_status(self, _args: dict[str, Any]) -> ToolExecutionResult:
        if not self.phone.has_adb():
            return ToolExecutionResult({"installed": False})
        active = self.phone.active_adb_serial()
        info: dict[str, Any] = {"installed": True, "connected": active is not None, "device": active}
        if active:
            w, h = self.phone.get_screen_size()
            info["screen"] = f"{w}x{h}"
            info["app"] = self.phone.get_foreground_app()
        return ToolExecutionResult(info)

    def _tool_adb_ui_dump(self, args: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(self.phone.dump_ui(only_interactive=bool(args.get("only_interactive", False))))

    def _tool_adb_screenshot(self, args: dict[str, Any]) -> ToolExecutionResult:
        save_as = str(args.get("save_as", "")).strip()
        save_path = self._resolve_safe_path(save_as) if save_as else None
        hq = bool(args.get("hq", False))
        meta, image = self.phone.capture_screenshot(save_path=save_path, hq=hq)
        return ToolExecutionResult(meta, image=image)

    def _tool_adb_tap(self, args: dict[str, Any]) -> ToolExecutionResult:
        el_id, x, y = args.get("element_id"), args.get("x"), args.get("y")
        lp = bool(args.get("long_press", False))
        desc = f"id={el_id}" if el_id is not None else f"({x},{y})"
        ok, reason = self._confirm(f"ADB {'долгое нажатие' if lp else 'тап'} {desc}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(
            self.phone.tap(
                element_id=int(el_id) if el_id is not None else None,
                x=int(x) if x is not None else None,
                y=int(y) if y is not None else None,
                long_press=lp,
            )
        )

    def _tool_adb_swipe(self, args: dict[str, Any]) -> ToolExecutionResult:
        direction = args.get("direction")
        desc = f"{direction}" if direction else f"({args.get('x1')},{args.get('y1')})->({args.get('x2')},{args.get('y2')})"
        ok, reason = self._confirm(f"ADB свайп: {desc}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(
            self.phone.swipe(
                direction=str(direction) if direction else None,
                x1=int(args["x1"]) if args.get("x1") is not None else None,
                y1=int(args["y1"]) if args.get("y1") is not None else None,
                x2=int(args["x2"]) if args.get("x2") is not None else None,
                y2=int(args["y2"]) if args.get("y2") is not None else None,
                duration_ms=int(args.get("duration_ms", 320)),
            )
        )

    def _tool_adb_type_text(self, args: dict[str, Any]) -> ToolExecutionResult:
        text = str(args.get("text", ""))
        ok, reason = self._confirm(f"Ввести текст: «{text[:60]}»", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.type_text(text, press_enter=bool(args.get("press_enter", False))))

    def _tool_adb_keyevent(self, args: dict[str, Any]) -> ToolExecutionResult:
        key = str(args.get("key", ""))
        ok, reason = self._confirm(f"Кнопка Android: {key}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.keyevent(key))

    def _tool_adb_app_control(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        package = str(args.get("package", "")).strip()
        query = str(args.get("query", "")).strip()
        url = str(args.get("url", "")).strip()
        if action in ("launch", "stop"):
            ok, reason = self._confirm(f"ADB {action}: {package}", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
        elif action == "open_url":
            # am start -a android.intent.action.VIEW -d <url> открывает не только
            # веб-ссылки, но и диплинки (tel:, sms:, intent://), системные
            # настройки и произвольные приложения -> считаем опасным.
            scheme = url.split(":", 1)[0].lower() if ":" in url else ""
            if scheme not in ("http", "https"):
                return ToolExecutionResult(
                    {"error": f"open_url разрешает только http/https (получено: {url or 'пусто'})."}
                )
            ok, reason = self._confirm(f"Открыть URL: {url}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.app_control(action, package=package, query=query, url=url))

    # --- Termux:API инструменты ---

    def _tool_phone_sensors(self, args: dict[str, Any]) -> ToolExecutionResult:
        kind = str(args.get("kind", "")).lower()
        if kind == "battery":
            return ToolExecutionResult(self.phone.run_termux_cmd("termux-battery-status", parse_json=True))
        if kind == "wifi":
            return ToolExecutionResult(self.phone.run_termux_cmd("termux-wifi-connectioninfo", parse_json=True))
        if kind == "wifi_scan":
            nets = self.phone.run_termux_cmd("termux-wifi-scaninfo", parse_json=True, timeout=20)
            return ToolExecutionResult({"networks": nets[:15] if isinstance(nets, list) else nets})
        if kind == "location":
            prov = str(args.get("provider", "network")).lower()
            loc = self.phone.run_termux_cmd("termux-location", ["-p", prov], parse_json=True, timeout=25)
            return ToolExecutionResult(loc if isinstance(loc, dict) else {"location": loc})
        if kind == "volume":
            return ToolExecutionResult({"streams": self.phone.run_termux_cmd("termux-volume", parse_json=True)})
        return ToolExecutionResult({"error": f"Неизвестный сенсор: {kind}"})

    def _tool_phone_controls(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        value = str(args.get("value", "")).strip()
        stream = str(args.get("stream", "music")).lower()
        ok, reason = self._confirm(f"Телефон: {action} = {value}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})

        if action == "torch":
            val = "on" if value.lower() in ("on", "1", "true", "вкл") else "off"
            self.phone.run_termux_cmd("termux-torch", [val])
            return ToolExecutionResult({"ok": True, "torch": val})
        if action == "brightness":
            self.phone.run_termux_cmd("termux-brightness", [value])
            return ToolExecutionResult({"ok": True, "brightness": value})
        if action == "volume":
            self.phone.run_termux_cmd("termux-volume", [stream, value])
            return ToolExecutionResult({"ok": True, "stream": stream, "volume": value})
        if action == "vibrate":
            ms = str(max(50, min(int(value or "300"), 5000)))
            self.phone.run_termux_cmd("termux-vibrate", ["-d", ms])
            return ToolExecutionResult({"ok": True, "ms": int(ms)})
        if action == "toast":
            self.phone.run_termux_cmd("termux-toast", [value])
            return ToolExecutionResult({"ok": True})
        if action == "open_url":
            self.phone.run_termux_cmd("termux-open-url", [value])
            return ToolExecutionResult({"ok": True, "url": value})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_notifications(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "list":
            items = self.phone.run_termux_cmd("termux-notification-list", parse_json=True)
            if isinstance(items, list):
                compact = [
                    {
                        "app": it.get("packageName"),
                        "title": it.get("title"),
                        "text": str(it.get("content", ""))[:120],
                    }
                    for it in items[:20]
                ]
                return ToolExecutionResult({"count": len(items), "items": compact})
            return ToolExecutionResult({"items": items})
        if action == "send":
            title = str(args.get("title", "GemPhone"))
            content = str(args.get("content", ""))
            notif_id = str(args.get("notif_id", "gemphone"))
            ok, reason = self._confirm(f"Уведомление: «{title}: {content[:50]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-notification", ["--id", notif_id, "--title", title, "--content", content])
            return ToolExecutionResult({"ok": True, "id": notif_id})
        if action == "remove":
            notif_id = str(args.get("notif_id", "gemphone"))
            self.phone.run_termux_cmd("termux-notification-remove", [notif_id])
            return ToolExecutionResult({"ok": True})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_clipboard(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "get":
            return ToolExecutionResult({"text": truncate_text(self.phone.run_termux_cmd("termux-clipboard-get"), 2000)})
        if action == "set":
            text = str(args.get("text", ""))
            ok, reason = self._confirm(f"В буфер: «{text[:60]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-clipboard-set", stdin_text=text)
            return ToolExecutionResult({"ok": True, "chars": len(text)})
        return ToolExecutionResult({"error": "action: get | set"})

    def _tool_phone_comms(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "search_contacts":
            q = str(args.get("query", "")).lower()
            contacts = self.phone.run_termux_cmd("termux-contact-list", parse_json=True, timeout=20)
            if not isinstance(contacts, list):
                return ToolExecutionResult({"contacts": contacts})
            filtered = [c for c in contacts if not q or q in str(c.get("name", "")).lower() or q in str(c.get("number", "")).lower()]
            return ToolExecutionResult({"count": len(filtered), "contacts": filtered[:20]})
        if action == "list_sms":
            limit = max(1, min(int(args.get("limit", 8)), 20))
            msgs = self.phone.run_termux_cmd("termux-sms-list", ["-l", str(limit)], parse_json=True, timeout=20)
            return ToolExecutionResult({"messages": msgs})
        if action == "send_sms":
            num, txt = str(args.get("number", "")).strip(), str(args.get("text", "")).strip()
            if not num or not txt:
                raise ValueError("Нужны number и text.")
            ok, reason = self._confirm(f"ОТПРАВИТЬ SMS на {num}: «{txt}»", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-sms-send", ["-n", num, txt])
            return ToolExecutionResult({"sent": True, "number": num})
        if action == "call":
            num = str(args.get("number", "")).strip()
            if not num:
                raise ValueError("Нужен number.")
            ok, reason = self._confirm(f"ПОЗВОНИТЬ на {num}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-telephony-call", [num])
            return ToolExecutionResult({"calling": True, "number": num})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_media(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "tts_speak":
            txt = str(args.get("text", "")).strip()
            ok, reason = self._confirm(f"Озвучить (TTS): «{txt[:60]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-tts-speak", [txt], timeout=30)
            return ToolExecutionResult({"ok": True})
        if action == "camera_photo":
            cam = str(int(args.get("camera_id", 0)))
            rel = str(args.get("output_file", f"photo_{int(time.time())}.jpg"))
            target = self._resolve_safe_path(rel)
            ok, reason = self._confirm(f"Фото с камеры #{cam} -> {rel}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            target.parent.mkdir(parents=True, exist_ok=True)
            self.phone.run_termux_cmd("termux-camera-photo", ["-c", cam, str(target)], timeout=25)
            return ToolExecutionResult({"ok": True, "saved": self._rel(target)})
        if action == "mic_record_start":
            rel = str(args.get("output_file", f"rec_{int(time.time())}.m4a"))
            dur = max(1, min(int(args.get("duration_sec", 10)), 120))
            target = self._resolve_safe_path(rel)
            ok, reason = self._confirm(f"Запись микрофона ({dur}с) -> {rel}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            target.parent.mkdir(parents=True, exist_ok=True)
            self.phone.run_termux_cmd("termux-microphone-record", ["-f", str(target), "-l", str(dur)])
            return ToolExecutionResult({"recording": True, "file": self._rel(target)})
        if action == "mic_record_stop":
            self.phone.run_termux_cmd("termux-microphone-record", ["-q"])
            return ToolExecutionResult({"stopped": True})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    # --- Файлы, терминал, веб и память ---

    def _tool_list_files(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel = str(args.get("path", ".") or ".")
        max_depth = max(1, min(int(args.get("max_depth", 2)), 5))
        root = self._resolve_safe_path(rel)
        if not root.exists():
            return ToolExecutionResult({"error": f"Не найден: {rel}"})
        if root.is_file():
            return ToolExecutionResult({"files": [self._rel(root)]})

        entries: list[str] = []
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and len(entries) < 120:
            cur, depth = stack.pop()
            try:
                children = sorted(os.scandir(cur), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
            except OSError:
                continue
            subdirs: list[tuple[Path, int]] = []
            for entry in children:
                if entry.name in IGNORED_DIRS:
                    continue
                p = Path(entry.path)
                is_dir = entry.is_dir(follow_symlinks=False)
                entries.append(f"{self._rel(p)}/" if is_dir else self._rel(p))
                if len(entries) >= 120:
                    break
                if is_dir and depth + 1 < max_depth:
                    subdirs.append((p, depth + 1))
            for sub in reversed(subdirs):
                stack.append(sub)
        return ToolExecutionResult({"count": len(entries), "entries": entries})

    def _tool_read_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel = str(args.get("path", ""))
        target = self._resolve_safe_path(rel)
        if not target.exists() or not target.is_file():
            return ToolExecutionResult({"error": f"Файл не найден: {rel}"})
        if is_binary_file(target):
            return ToolExecutionResult({"error": f"Бинарный файл: {rel}"})
        start = max(1, int(args.get("start_line", 1)))
        req_end = args.get("end_line")
        end = int(req_end) if req_end is not None else start + 119
        end = min(max(end, start), start + MAX_READ_LINES - 1)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        numbered = [f"{i:4d}|{ln}" for i, ln in enumerate(lines[start - 1 : end], start=start)]
        return ToolExecutionResult(
            {
                "path": self._rel(target),
                "total": len(lines),
                "range": f"{start}-{min(end, len(lines))}",
                "content": truncate_text("\n".join(numbered)),
            }
        )

    def _tool_search_text(self, args: dict[str, Any]) -> ToolExecutionResult:
        query = str(args.get("query", ""))
        if not query:
            return ToolExecutionResult({"error": "Пустой query."})
        root = self._resolve_safe_path(str(args.get("path", ".") or "."))
        pattern = re.compile(query if args.get("is_regex") else re.escape(query), re.IGNORECASE)
        files: list[Path] = [root] if root.is_file() else []
        if not files:
            stack = [root]
            while stack:
                cur = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for e in it:
                            if e.name in IGNORED_DIRS:
                                continue
                            p = Path(e.path)
                            if e.is_dir(follow_symlinks=False):
                                stack.append(p)
                            elif e.is_file(follow_symlinks=False):
                                files.append(p)
                except OSError:
                    continue
        matches: list[str] = []
        for fp in sorted(files):
            if len(matches) >= 40:
                break
            try:
                if fp.stat().st_size > 1_000_000 or is_binary_file(fp):
                    continue
                for idx, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        matches.append(f"{self._rel(fp)}:{idx}: {line.strip()[:120]}")
                        if len(matches) >= 40:
                            break
            except OSError:
                continue
        return ToolExecutionResult({"count": len(matches), "matches": matches})

    def _tool_edit_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel, old_text, new_text = str(args.get("path", "")), str(args.get("old_text", "")), str(args.get("new_text", ""))
        target = self._resolve_safe_path(rel)
        if not target.exists() or not target.is_file():
            return ToolExecutionResult({"error": f"Файл не найден: {rel}"})
        original = target.read_text(encoding="utf-8")
        cnt = original.count(old_text)
        if cnt != 1:
            return ToolExecutionResult({"error": f"old_text найден {cnt} раз(а); нужен ровно 1."})
        updated = original.replace(old_text, new_text, 1)
        rel_lbl = self._rel(target)
        self.ui.info(f"\nПравка: {rel_lbl}")
        self.ui.diff(original, updated, rel_lbl)
        ok, reason = self._confirm(f"Применить правку в {rel_lbl}?", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        self._atomic_write(target, updated)
        self.ui.success(f"  ✓ Обновлено: {rel_lbl}")
        return ToolExecutionResult({"ok": True, "path": rel_lbl})

    def _tool_write_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel, content = str(args.get("path", "")), str(args.get("content", ""))
        target = self._resolve_safe_path(rel)
        rel_lbl = self._rel(target)
        old_content = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else ""
        self.ui.info(f"\nФайл: {rel_lbl}")
        self.ui.diff(old_content, content, rel_lbl)
        ok, reason = self._confirm(f"Записать {rel_lbl}?", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        self._atomic_write(target, content)
        self.ui.success(f"  ✓ Сохранено: {rel_lbl}")
        return ToolExecutionResult({"ok": True, "path": rel_lbl})

    def _tool_run_command(self, args: dict[str, Any]) -> ToolExecutionResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolExecutionResult({"error": "Пустая команда."})
        timeout = max(1, min(int(args.get("timeout", DEFAULT_CMD_TIMEOUT)), 120))
        blocked = detect_destructive_command(command)
        if blocked:
            self.ui.error(f"Заблокировано: {blocked}")
            return ToolExecutionResult({"error": f"Команда отклонена политикой безопасности: {blocked}"})
        self.ui.panel("Команда", f"$ {command}", style="magenta")
        ok, reason = self._confirm("Выполнить в Termux?", risk="dangerous")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        proc = subprocess.run(command, shell=True, cwd=self.workspace, capture_output=True, text=True, timeout=timeout)
        stdout = truncate_text(proc.stdout.strip(), MAX_TOOL_OUTPUT_CHARS // 2)
        stderr = truncate_text(proc.stderr.strip(), MAX_TOOL_OUTPUT_CHARS // 2)
        if proc.returncode == 0:
            self.ui.success("  -> exit 0")
        else:
            self.ui.error(f"  -> exit {proc.returncode}")
        if stdout:
            self.ui.dim(stdout[:350] + ("..." if len(stdout) > 350 else ""))
        if stderr:
            self.ui.error(stderr[:350] + ("..." if len(stderr) > 350 else ""))
        return ToolExecutionResult({"code": proc.returncode, "stdout": stdout, "stderr": stderr})

    def _tool_web_fetch(self, args: dict[str, Any]) -> ToolExecutionResult:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return ToolExecutionResult({"error": "Нужен http:// или https://"})
        status, ctype, body = _http_get_text(url)
        if "html" in ctype.lower():
            parser = _HtmlTextExtractor()
            parser.feed(body)
            clean_text = parser.get_text()
        else:
            clean_text = body
        return ToolExecutionResult({"status": status, "content": truncate_text(clean_text, 4_000)})

    def _tool_memory(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "list")).lower()
        if action == "list":
            return ToolExecutionResult({"memory": self.memory.data})
        if action == "set":
            k, v = str(args.get("key", "")).strip(), str(args.get("value", "")).strip()
            if not k or not v:
                return ToolExecutionResult({"error": "Нужны key и value."})
            self.memory.set(k, v)
            return ToolExecutionResult({"saved": k})
        if action == "delete":
            k = str(args.get("key", "")).strip()
            return ToolExecutionResult({"deleted": self.memory.delete(k), "key": k})
        return ToolExecutionResult({"error": "action: list | set | delete"})


# ---------------------------------------------------------------------------
# Ядро цикла агента с контролем бюджета токенов
# ---------------------------------------------------------------------------


@dataclass
class PhoneAgent:
    client: BaseLLMClient
    toolkit: AgentToolkit
    store: ConfigStore
    memory: MemoryStore
    ui: ConsoleUI
    notifier: PhoneNotifier = field(default_factory=PhoneNotifier)
    max_steps: int = DEFAULT_MAX_STEPS
    turn_token_budget: int = DEFAULT_TURN_TOKEN_BUDGET
    usage: TokenUsage = field(default_factory=TokenUsage)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    last_turn_meta: dict[str, Any] = field(default_factory=dict)

    def clear_history(self) -> None:
        self.client.clear()
        self.transcript.clear()

    def switch_client(self, new_client: BaseLLMClient, *, preserve_context: bool = True) -> None:
        self.client = new_client
        if preserve_context and self.transcript:
            self.client.seed_transcript(self.transcript)

    def _build_system_prompt(self) -> str:
        if not self.memory.data:
            return SYSTEM_PROMPT
        mem_lines = [f"- {k}: {v}" for k, v in self.memory.data.items()]
        return SYSTEM_PROMPT + "\nUser memory:\n" + "\n".join(mem_lines)

    def ask(self, user_prompt: str) -> str:
        marker = self.client.snapshot()
        self.client.add_user_message(user_prompt)
        specs = self.toolkit.tool_specs()
        sys_prompt = self._build_system_prompt()
        turn_tokens = 0
        budget_limit = self.turn_token_budget
        started_at = time.perf_counter()
        self.last_turn_meta = {}
        self.notifier.notify_working(user_prompt)

        monitor = CancellationMonitor()

        def _check_cancel() -> None:
            if monitor.is_cancelled():
                raise RequestCancelledError(monitor.reason or "Отменено пользователем.")

        try:
            with monitor:
                for step in range(1, self.max_steps + 1):
                    _check_cancel()
                    label = (
                        f"Думаю ({self.client.provider_name}/{self.client.model}, "
                        f"шаг {step}/{self.max_steps}) [ESC — отмена]..."
                    )
                    with self.ui.status(label):
                        turn = self.client.generate_step(
                            tool_specs=specs,
                            system_instruction=sys_prompt,
                            cancel_event=monitor.event,
                        )
                    _check_cancel()

                    step_total = turn.total_tokens or (turn.prompt_tokens + turn.completion_tokens)
                    turn_tokens += step_total
                    self.usage.add(
                        prompt=turn.prompt_tokens,
                        completion=turn.completion_tokens,
                        total=step_total,
                    )

                    if step_total > 0:
                        self.ui.dim(
                            f"  [шаг {step}: +{format_tokens(step_total)} ток. "
                            f"(вход {format_tokens(turn.prompt_tokens)} / выход {format_tokens(turn.completion_tokens)}) "
                            f"| запрос: {format_tokens(turn_tokens)}]"
                        )

                    if not turn.tool_calls:
                        elapsed = time.perf_counter() - started_at
                        self.client.finalize_turn()
                        self.transcript.append(("user", user_prompt))
                        if turn.text:
                            self.transcript.append(("assistant", turn.text))
                        self.last_turn_meta = {
                            "status": "done",
                            "steps": step,
                            "tokens": turn_tokens,
                            "elapsed": elapsed,
                        }
                        self.notifier.notify_done(
                            turn.text or "Задача выполнена",
                            steps=step,
                            tokens=turn_tokens,
                            elapsed=elapsed,
                        )
                        if turn.prompt_tokens > 30_000:
                            self.ui.warn(
                                f"  Совет: контекст вырос до {format_tokens(turn.prompt_tokens)} токенов — "
                                "введите /clear, чтобы не переплачивать в следующих сообщениях."
                            )
                        return turn.text

                    # Защитный стоп-кран по бюджету токенов внутри одного запроса
                    if budget_limit > 0 and turn_tokens >= budget_limit and not self.toolkit.policy.non_interactive:
                        self.ui.warn(
                            f"\n⚠️ Лимит экономии: этот запрос уже потратил {format_tokens(turn_tokens)} токенов "
                            f"(бюджет: {format_tokens(budget_limit)})."
                        )
                        self.notifier.notify_attention(
                            f"Лимит экономии: {format_tokens(turn_tokens)} ток. Подтвердите продолжение в Termux."
                        )
                        ans = input("  Продолжить выполнение ещё на один блок? [y/N] > ").strip().lower()
                        if ans not in ("y", "yes", "д", "да"):
                            elapsed = time.perf_counter() - started_at
                            self.client.finalize_turn()
                            self.last_turn_meta = {
                                "status": "stopped",
                                "steps": step,
                                "tokens": turn_tokens,
                                "elapsed": elapsed,
                            }
                            self.notifier.clear()
                            return "Остановлено по лимиту токенов (/budget)."
                        budget_limit += self.turn_token_budget
                        self.notifier.notify_working(user_prompt)

                    if turn.text:
                        self.ui.dim(turn.text)

                    results: list[tuple[ToolCall, ToolExecutionResult]] = []
                    for call in turn.tool_calls:
                        _check_cancel()
                        self.ui.tool_badge(call.name, call.args)
                        res = self.toolkit.execute(call.name, call.args)
                        _check_cancel()
                        results.append((call, res))

                    self.client.add_tool_results(results)

                elapsed = time.perf_counter() - started_at
                self.client.finalize_turn()
                self.last_turn_meta = {
                    "status": "limit",
                    "steps": self.max_steps,
                    "tokens": turn_tokens,
                    "elapsed": elapsed,
                }
                self.notifier.notify_attention(
                    f"Достигнут лимит шагов ({self.max_steps}). Напишите «продолжай», если нужно закончить."
                )
                return (
                    f"Достигнут лимит шагов ({self.max_steps}, потрачено {format_tokens(turn_tokens)} ток.). "
                    "Напишите «продолжай», если нужно довести задачу до конца."
                )
        except (KeyboardInterrupt, RequestCancelledError) as exc:
            self.client.rollback(marker)
            self.notifier.clear()
            reason = str(exc) if isinstance(exc, RequestCancelledError) and str(exc) else "Запрос отменён"
            self.last_turn_meta = {
                "status": "cancelled",
                "steps": 0,
                "tokens": turn_tokens,
                "elapsed": time.perf_counter() - started_at,
                "reason": reason,
            }
            raise RequestCancelledError(reason) from exc
        except ApiError as exc:
            self.client.rollback(marker)
            self.notifier.notify_error(str(exc))
            raise


# ---------------------------------------------------------------------------
# Интерактивные мастера и CLI
# ---------------------------------------------------------------------------


def ensure_api_key(
    cfg: ProviderConfig,
    store: ConfigStore,
    *,
    cli_key: str | None = None,
    non_interactive: bool = False,
) -> str:
    if cli_key and cli_key.strip():
        return cli_key.strip()
    key = cfg.effective_api_key()
    if key or not cfg.requires_key:
        return key
    if non_interactive:
        raise SystemExit(f"Ошибка: для '{cfg.name}' не задан API-ключ ({store.path}).")

    print(f"Для провайдера '{cfg.name}' ({cfg.description}) не задан API-ключ.")
    try:
        entered = getpass.getpass("Вставьте API-ключ (скрыт, Enter для сохранения): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nВыход.")
    if not entered:
        raise SystemExit("API-ключ не указан.")
    cfg.api_key = entered
    store.save()
    print(f"✓ Ключ сохранён в {store.path}")
    return entered


def print_status_report(agent: PhoneAgent) -> None:
    phone = agent.toolkit.phone
    active_adb = phone.active_adb_serial()
    n = agent.notifier
    notify_state = (
        f"ВКЛ (вибр/тост/шторка{', TTS' if n.tts else ''})"
        if n.enabled and n.is_available()
        else ("ВКЛ (только терминал)" if n.enabled else "ВЫКЛ")
    )
    rows = [
        ["API-провайдер", f"{agent.client.provider_name} ({agent.client.config.provider_type})"],
        ["Модель", f"{agent.client.model} (Vision: {'да' if agent.client.config.supports_vision else 'нет'})"],
        ["Качество скринов", f"{phone.screenshot_max_dim}px, JPEG {phone.screenshot_quality}% (/quality)"],
        ["Режим инструментов", f"{agent.toolkit.mode} ({len(agent.toolkit.tool_specs())} шт.)"],
        ["Бюджет / Шаги", f"{format_tokens(agent.turn_token_budget)} ток. на запрос / макс {agent.max_steps} шагов"],
        ["Оповещения (/notify)", notify_state],
        ["ADB (экран/тапы)", f"Подключён: {active_adb}" if active_adb else ("Установлен, нет устройств (/adb)" if phone.has_adb() else "НЕ установлен")],
        ["Termux:API", "Доступен" if phone.has_termux_api() else "НЕ найден"],
        ["Библиотеки", f"rich={'OK' if HAS_RICH else '-'}, httpx={'OK' if HAS_HTTPX else '-'}, PIL={'OK' if HAS_PIL else '-'}, ptk={'OK' if HAS_PROMPT_TOOLKIT else '-'}"],
        ["Память фактов", f"{len(agent.memory.data)} записей"],
        ["Рабочая папка", str(agent.toolkit.workspace)],
    ]
    agent.ui.table("Диагностика GemPhone", ["Компонент", "Статус"], rows)


def interactive_adb_wizard(agent: PhoneAgent) -> None:
    ui = agent.ui
    phone = agent.toolkit.phone
    if not phone.has_adb():
        ui.error("ADB не установлен: pkg install android-tools")
        return

    ui.panel(
        "Мастер подключения ADB (Wireless Debugging)",
        "1. Включите Wi-Fi и откройте: Настройки -> Для разработчиков -> Отладка по Wi-Fi.\n"
        "2. Откройте Настройки и Termux в разделённом экране (чтобы порт не менялся).\n"
        "3. Сначала [1] Сопряжение (pair), затем [2] Подключение (connect).",
        style="yellow",
    )
    devs = phone.adb_devices()
    if devs:
        ui.info("Устройства ADB: " + ", ".join(f"{d['serial']} ({d['state']})" for d in devs))

    print("  [1] Сопряжение (adb pair 127.0.0.1:PORT + код)")
    print("  [2] Подключение (adb connect 127.0.0.1:PORT)")
    print("  [3] Перезапустить ADB-сервер")
    print("  [Enter] Выход")
    try:
        choice = input("  Выбор > ").strip()
        if choice == "1":
            port = input("  Порт сопряжения: ").strip()
            code = input("  6-значный код: ").strip()
            if port and code:
                addr = port if ":" in port else f"127.0.0.1:{port}"
                proc = subprocess.run(["adb", "pair", addr, code], capture_output=True, text=True, timeout=15)
                ui.info((proc.stdout + "\n" + proc.stderr).strip())
        elif choice == "2":
            port = input("  Основной порт: ").strip()
            if port:
                addr = port if ":" in port else f"127.0.0.1:{port}"
                proc = subprocess.run(["adb", "connect", addr], capture_output=True, text=True, timeout=15)
                ui.info((proc.stdout + "\n" + proc.stderr).strip())
        elif choice == "3":
            subprocess.run(["adb", "kill-server"], capture_output=True, text=True, timeout=10)
            subprocess.run(["adb", "start-server"], capture_output=True, text=True, timeout=10)
            ui.success("Сервер ADB перезапущен.")
    except (EOFError, KeyboardInterrupt):
        print()


def interactive_add_provider(agent: PhoneAgent) -> None:
    ui = agent.ui
    ui.info("Добавление API-провайдера (пустая строка — отмена):")
    try:
        name = re.sub(r"[^a-z0-9_-]", "", input("  1. Короткое имя (напр. depp, nvidia): ").strip().lower())
        if not name:
            return
        ptype = input("  2. Протокол [openai / gemini / anthropic] (по умолчанию openai): ").strip().lower() or "openai"
        if ptype not in ("openai", "gemini", "anthropic"):
            ui.warn("Поддерживаются только openai, gemini, anthropic.")
            return
        def_url = (
            "https://generativelanguage.googleapis.com/v1beta/models"
            if ptype == "gemini"
            else "https://api.anthropic.com/v1"
            if ptype == "anthropic"
            else "https://api.openai.com/v1"
        )
        base_url = input(f"  3. Base URL ({def_url}): ").strip() or def_url
        model = input("  4. ID модели: ").strip()
        if not model:
            return
        api_key = getpass.getpass("  5. API-ключ (скрыт; Enter, если локальный без ключа): ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    cfg = ProviderConfig(
        name=name,
        provider_type=ptype,
        base_url=base_url,
        model=model,
        api_key=api_key,
        requires_key=bool(api_key),
        supports_vision=True,
        description=f"Свой API ({base_url})",
    )
    agent.store.providers[name] = cfg
    agent.store.active_provider = name
    agent.store.save()
    agent.switch_client(create_llm_client(cfg, api_key), preserve_context=True)
    ui.success(f"✓ Провайдер '{name}' сохранён и активирован.")


def handle_slash_command(line: str, agent: PhoneAgent) -> bool:
    ui = agent.ui
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/q"):
        return False

    if cmd == "/help":
        ui.panel(
            "Команды GemPhone",
            "/quality [тип]  — чёткость скриншотов: eco | normal | hd\n"
            "/notify [режим] — сигналы завершения: on | off | tts | test\n"
            "/mode [тип]     — профиль инструментов: all | phone | code | chat (экономит токены!)\n"
            "/budget [число] — лимит токенов на один запрос (0 — без лимита, по умолч. 60000)\n"
            "/status         — диагностика (ADB, Termux:API, бюджет, модель)\n"
            "/adb            — мастер подключения беспроводной отладки ADB\n"
            "/screen         — тест: показать элементы текущего экрана\n"
            "/providers (/p) — список ИИ-провайдеров и ключей\n"
            "/use <имя>      — переключить нейросеть\n"
            "/key [имя]      — задать API-ключ\n"
            "/add            — добавить свой кастомный API\n"
            "/model [имя]    — сменить и сохранить модель\n"
            "/vision         — вкл/выкл отправку скриншотов\n"
            "/auto           — вкл/выкл автоподтверждение действий\n"
            "/memory         — долгосрочная память\n"
            "/save / /load   — сохранить или загрузить диалог\n"
            "/tokens         — общий расход токенов за сессию\n"
            "/clear          — очистить историю диалога (сброс контекста!)\n"
            "/exit           — выход",
            style="cyan",
        )
        return True

    if cmd == "/quality":
        phone = agent.toolkit.phone
        sub = arg.lower()
        if not sub:
            ui.info(f"Текущее качество скриншотов: {phone.screenshot_max_dim}px, JPEG {phone.screenshot_quality}%.")
            ui.dim("Пресеты: /quality eco (720px, q=65) | /quality normal (1280px, q=85) | /quality hd (1920px, q=92)")
        elif sub == "eco":
            phone.screenshot_max_dim, phone.screenshot_quality = 720, 65
            ui.success("Качество скриншотов: ECO (720px, JPEG 65%) — минимум токенов.")
        elif sub == "normal":
            phone.screenshot_max_dim, phone.screenshot_quality = 1280, 85
            ui.success("Качество скриншотов: NORMAL (1280px, JPEG 85%) — чёткий текст.")
        elif sub == "hd":
            phone.screenshot_max_dim, phone.screenshot_quality = 1920, 92
            ui.success("Качество скриншотов: HD (1920px, JPEG 92%) — максимальная чёткость.")
        else:
            ui.warn("Выберите: /quality eco | /quality normal | /quality hd")
        return True

    if cmd == "/notify":
        n = agent.notifier
        sub = arg.lower()
        if not sub:
            state = "ВКЛ" if n.enabled else "ВЫКЛ"
            tts_state = "ВКЛ" if n.tts else "ВЫКЛ"
            ui.info(
                f"Оповещения о завершении: {state} | Голос (TTS): {tts_state} | "
                f"Termux:API: {'OK' if n.is_available() else 'нет'}"
            )
            ui.dim("Использование: /notify on | /notify off | /notify tts | /notify test")
        elif sub in ("on", "вкл", "1"):
            n.enabled = True
            ui.success("Оповещения о завершении ВКЛЮЧЕНЫ (вибрация + тост + уведомление).")
        elif sub in ("off", "выкл", "0"):
            n.enabled = False
            n.clear()
            ui.info("Оповещения Android ВЫКЛЮЧЕНЫ (останется только плашка ✓ ГОТОВО в терминале).")
        elif sub == "tts":
            n.tts = not n.tts
            ui.info(f"Голосовое «Готово» (TTS): {'ВКЛ' if n.tts else 'ВЫКЛ'}")
        elif sub == "test":
            n.notify_done("Тестовое оповещение: ИИ завершил задачу!", steps=1, tokens=1200, elapsed=1.5)
            ui.done_banner(steps=1, tokens=1200, elapsed=1.5, status="done")
            ui.dim("Отправлен тестовый сигнал (вибрация + тост + уведомление).")
        else:
            ui.warn("Варианты: /notify on | /notify off | /notify tts | /notify test")
        return True

    if cmd == "/mode":
        valid: tuple[ToolMode, ...] = ("all", "phone", "code", "chat")
        if not arg:
            ui.info(f"Текущий режим: {agent.toolkit.mode} ({len(agent.toolkit.tool_specs())} инструментов). Доступны: all, phone, code, chat.")
        elif arg.lower() in valid:
            agent.toolkit.mode = arg.lower()  # type: ignore[assignment]
            ui.success(f"Режим переключён на '{agent.toolkit.mode}' ({len(agent.toolkit.tool_specs())} инструментов).")
        else:
            ui.warn("Выберите: /mode all | /mode phone | /mode code | /mode chat")
        return True

    if cmd == "/budget":
        if not arg:
            ui.info(f"Лимит на один запрос: {format_tokens(agent.turn_token_budget)} токенов (0 = выкл).")
        else:
            try:
                val = int(arg.lower().replace("k", "000").replace("к", "000"))
                agent.turn_token_budget = max(0, val)
                ui.success(f"Новый лимит на один запрос: {format_tokens(agent.turn_token_budget)} токенов.")
            except ValueError:
                ui.warn("Укажите число, например: /budget 40000 или /budget 50k")
        return True

    if cmd == "/status":
        print_status_report(agent)
        return True

    if cmd == "/adb":
        interactive_adb_wizard(agent)
        return True

    if cmd == "/screen":
        try:
            dump = agent.toolkit.phone.dump_ui(only_interactive=True)
            ui.panel(f"Экран ({dump['app']}, {dump['screen']})", dump["ui"] or "(пусто)", style="green")
        except Exception as exc:
            ui.error(f"Ошибка: {exc}")
        return True

    if cmd in ("/providers", "/p"):
        rows = [
            [
                "*" if name == agent.client.provider_name else "",
                name,
                cfg.provider_type,
                "без ключа" if not cfg.requires_key else ("OK" if cfg.effective_api_key() else "нет"),
                cfg.model,
            ]
            for name, cfg in agent.store.providers.items()
        ]
        ui.table("ИИ-провайдеры", ["", "Имя", "API", "Ключ", "Модель"], rows)
        return True

    if cmd == "/add":
        interactive_add_provider(agent)
        return True

    if cmd == "/key":
        target = arg.lower() if arg else agent.client.provider_name
        cfg = agent.store.get(target)
        if cfg is None:
            ui.warn(f"Провайдер '{target}' не найден.")
            return True
        try:
            new_key = getpass.getpass(f"Ключ для '{target}' (скрыт, Enter — отмена): ").strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if new_key:
            cfg.api_key = new_key
            agent.store.save()
            if target == agent.client.provider_name:
                agent.client.api_key = new_key
            ui.success(f"✓ Ключ сохранён ({mask_secret(new_key)}).")
        return True

    if cmd in ("/use", "/provider"):
        if not arg:
            return handle_slash_command("/providers", agent)
        target = arg.lower()
        cfg = agent.store.get(target)
        if cfg is None:
            ui.warn(f"Провайдер '{target}' не найден.")
            return True
        try:
            key = ensure_api_key(cfg, agent.store)
            agent.store.active_provider = target
            agent.store.save()
            agent.switch_client(create_llm_client(cfg, key), preserve_context=True)
            ui.success(f"Активный API: {target} | Модель: {cfg.model}")
        except (SystemExit, ValueError) as exc:
            ui.error(str(exc))
        return True

    if cmd == "/model":
        if not arg:
            ui.info(f"Модель ({agent.client.provider_name}): {agent.client.model}")
        else:
            agent.client.model = arg
            agent.client.config.model = arg
            agent.store.save()
            ui.success(f"Модель сохранена: {arg}")
        return True

    if cmd == "/vision":
        cfg = agent.client.config
        cfg.supports_vision = not cfg.supports_vision
        agent.store.save()
        ui.info(f"Vision для '{cfg.name}': {'ВКЛ' if cfg.supports_vision else 'ВЫКЛ'}")
        return True

    if cmd == "/auto":
        pol = agent.toolkit.policy
        pol.auto_approve_moderate = not pol.auto_approve_moderate
        ui.info(f"Автоподтверждение: {'ВКЛ' if pol.auto_approve_moderate else 'ВЫКЛ'}")
        return True

    if cmd == "/memory":
        if not agent.memory.data:
            ui.dim("Память пуста.")
        else:
            ui.table("Память", ["Ключ", "Значение"], [[k, v] for k, v in agent.memory.data.items()])
        return True

    if cmd == "/save":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", arg) or "last"
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{name}.json"
        path.write_text(json.dumps(agent.transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        ui.success(f"✓ Сохранено: {path}")
        return True

    if cmd == "/load":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", arg) or "last"
        path = SESSIONS_DIR / f"{name}.json"
        if not path.exists():
            ui.warn(f"Не найдено: {path}")
            return True
        loaded = json.loads(path.read_text(encoding="utf-8"))
        agent.transcript = [(str(r), str(t)) for r, t in loaded]
        agent.client.seed_transcript(agent.transcript)
        ui.success(f"✓ Загружено сообщений: {len(agent.transcript)}")
        return True

    if cmd == "/tokens":
        u = agent.usage
        ui.info(
            f"Сессия: ввод={format_tokens(u.prompt_tokens)}, "
            f"вывод={format_tokens(u.completion_tokens)}, "
            f"всего={format_tokens(u.total_tokens)}"
        )
        return True

    if cmd == "/clear":
        agent.clear_history()
        ui.success("История очищена (контекст сброшен до нуля).")
        return True

    ui.warn(f"Неизвестная команда: {cmd}. Введите /help.")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gemphone", description="ИИ-агент управления Android с режимом экономии токенов.")
    parser.add_argument("-d", "--dir", type=Path, default=Path.cwd(), help="Рабочая директория.")
    parser.add_argument("-P", "--provider", default=None, help="Провайдер.")
    parser.add_argument("-m", "--model", default=None, help="ID модели.")
    parser.add_argument("--mode", choices=("all", "phone", "code", "chat"), default="all", help="Набор инструментов.")
    parser.add_argument("--budget", type=int, default=DEFAULT_TURN_TOKEN_BUDGET, help="Лимит токенов на 1 запрос.")
    parser.add_argument("--api-key", default=None, help="API-ключ.")
    parser.add_argument("--adb-serial", default=None, help="Serial устройства ADB.")
    parser.add_argument("-p", "--prompt", default=None, help="Одноразовый запрос.")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="Максимум шагов (по умолч. 10).")
    parser.add_argument("--auto", action="store_true", help="Автоподтверждение действий.")
    parser.add_argument("--yolo", action="store_true", help="Полное автоподтверждение.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = args.dir.resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(f"Ошибка: папка не найдена: {workspace}", file=sys.stderr)
        return 1

    ui = ConsoleUI()
    store = ConfigStore()
    memory = MemoryStore()

    cfg = store.get(args.provider.lower()) if args.provider else store.get_active()
    if cfg is None:
        ui.error(f"Провайдер '{args.provider}' не найден.")
        return 1

    runtime_cfg = ProviderConfig.from_dict(cfg.name, cfg.to_dict())
    if args.model:
        runtime_cfg.model = args.model

    non_interactive = args.prompt is not None
    api_key = ensure_api_key(cfg, store, cli_key=args.api_key, non_interactive=non_interactive)

    try:
        client = create_llm_client(runtime_cfg, api_key)
    except ValueError as exc:
        ui.error(str(exc))
        return 1

    phone = PhoneController(target_serial=args.adb_serial)
    notifier = PhoneNotifier(enabled=not non_interactive)
    policy = ApprovalPolicy(
        auto_approve_moderate=args.auto or args.yolo,
        auto_approve_dangerous=args.yolo,
        non_interactive=non_interactive,
    )
    toolkit = AgentToolkit(
        workspace=workspace,
        phone=phone,
        memory=memory,
        ui=ui,
        policy=policy,
        mode=args.mode,
        notifier=notifier,
    )
    agent = PhoneAgent(
        client=client,
        toolkit=toolkit,
        store=store,
        memory=memory,
        ui=ui,
        notifier=notifier,
        max_steps=max(1, args.max_steps),
        turn_token_budget=max(0, args.budget),
    )

    if args.prompt is not None:
        try:
            ans = agent.ask(args.prompt)
            if ans:
                ui.markdown(ans)
            return 0
        except ApiError as exc:
            ui.error(f"Ошибка API: {exc}")
            return 1
        except KeyboardInterrupt:
            return 130

    adb_state = f"ADB: {phone.active_adb_serial()}" if phone.active_adb_serial() else "ADB: нет (/adb)"
    api_state = "Termux:API: OK" if phone.has_termux_api() else "Termux:API: нет"
    ui.panel(
        "GemPhone 3.5 — Android AI Agent (Instant Cancel)",
        f"API    : {client.provider_name} / {client.model}\n"
        f"Режим  : {toolkit.mode} ({len(toolkit.tool_specs())} инстр.) | Скрины: {phone.screenshot_max_dim}px (/quality)\n"
        f"Модули : {adb_state}  |  {api_state}  |  Отмена: ESC или кнопка в шторке\n"
        f"Команды: /quality  /mode  /budget  /notify  /clear  /status  /adb  /help  /exit",
        style="cyan",
    )

    prompt_session = None
    if HAS_PROMPT_TOOLKIT and PromptSession is not None and FileHistory is not None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            prompt_session = PromptSession(history=FileHistory(str(HISTORY_PATH)))
        except Exception:
            prompt_session = None

    while True:
        try:
            user_input = (prompt_session.prompt("\nВы > ") if prompt_session else input("\nВы > ")).strip()
        except EOFError:
            ui.info("\nДо встречи!")
            return 0
        except KeyboardInterrupt:
            ui.dim("\n(Для выхода введите /exit или Ctrl+D)")
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash_command(user_input, agent):
                ui.info("До встречи!")
                return 0
            continue

        try:
            answer = agent.ask(user_input)
            if answer:
                ui.markdown(answer)
            meta = agent.last_turn_meta
            if meta:
                ui.done_banner(
                    steps=int(meta.get("steps", 1)),
                    tokens=int(meta.get("tokens", 0)),
                    elapsed=float(meta.get("elapsed", 0.0)),
                    status=str(meta.get("status", "done")),
                )
        except (KeyboardInterrupt, RequestCancelledError) as exc:
            msg = str(exc) if str(exc) else "Запрос отменён"
            ui.warn(f"\n⏹ {msg} (история возвращена в исходное состояние).")
        except ApiError as exc:
            ui.error(f"\nОшибка API: {exc}")


if __name__ == "__main__":
    sys.exit(main())