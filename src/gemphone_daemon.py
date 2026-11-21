#!/usr/bin/env python3
"""GemPhone Background Daemon (без Telegram) — автономная работа агента в фоне в Termux."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import fast_transport
import gemphone as gp

log = logging.getLogger("gemphone.daemon")

HOME = Path.home()
QUEUE_DIR = gp.APP_DIR / "queue"
OUTBOX_DIR = gp.APP_DIR / "out"
LAST_ANSWER_PATH = gp.APP_DIR / "last_answer.txt"
PID_FILE = gp.APP_DIR / "daemon.pid"


class SilentUI(gp.ConsoleUI):
    """Неблокирующий headless UI для фонового режима."""

    def __init__(self) -> None:
        self.rich_console = None
        self.ansi = False

    def info(self, text: str) -> None:
        log.info("%s", text)

    def dim(self, text: str) -> None:
        log.debug("%s", text)

    def success(self, text: str) -> None:
        log.info("OK: %s", text)

    def warn(self, text: str) -> None:
        log.warning("%s", text)

    def error(self, text: str) -> None:
        log.error("%s", text)

    def panel(self, title: str, body: str, style: str = "") -> None:
        del style
        log.info("[%s] %s", title, body.replace("\n", " | "))

    def markdown(self, text: str) -> None:
        log.info("ANSWER: %s", text[:300])

    def tool_tree_result(self, name: str, args: dict[str, Any], status: str, summary: str) -> None:
        log.info("TOOL %s(%s) -> %s: %s", name, gp._format_tool_args(args), status, summary)

    def diff(self, old_text: str, new_text: str, filename: str) -> None:
        del old_text, new_text
        log.info("DIFF %s", filename)

    def done_banner(self, *, steps: int, tokens: int, elapsed: float, status: str = "done") -> None:
        log.info("DONE status=%s steps=%d tokens=%d elapsed=%.1fs", status, steps, tokens, elapsed)


def atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def acquire_wake_lock() -> bool:
    if shutil.which("termux-wake-lock") is None:
        return False
    try:
        subprocess.run(["termux-wake-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False


def release_wake_lock() -> None:
    if shutil.which("termux-wake-unlock") is None:
        return
    try:
        subprocess.run(["termux-wake-unlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


class DaemonRunner:
    """Управляет фоновым экземпляром PhoneAgent и очередью локальных задач."""

    def __init__(
        self,
        *,
        workspace: Path,
        provider: str | None,
        model: str | None,
        mode: gp.ToolMode,
        max_steps: int,
        budget: int,
        allow_ui: bool,
        allow_dangerous: bool,
        tts: bool = False,
        idle_reset_sec: int = 1800,
    ) -> None:
        fast_transport.patch_gemphone_runtime(gp)

        self.workspace = workspace.resolve()
        self.ui = SilentUI()
        self.store = gp.ConfigStore()
        self.memory = gp.MemoryStore()
        self.phone = gp.PhoneController(bridge=fast_transport.FastBridgeClient())
        self.notifier = gp.PhoneNotifier(
            enabled=True,
            vibrate=True,
            toast=True,
            notification=True,
            tts=tts,
        )
        self.mode = mode
        self.max_steps = max_steps
        self.budget = budget
        self.allow_ui = allow_ui
        self.allow_dangerous = allow_dangerous
        self.idle_reset_sec = idle_reset_sec

        cfg = self.store.get(provider.lower()) if provider else self.store.get_active()
        if cfg is None:
            raise SystemExit(f"Провайдер '{provider}' не найден в {self.store.path}")
        self.runtime_cfg = gp.ProviderConfig.from_dict(cfg.name, cfg.to_dict())
        if model:
            self.runtime_cfg.model = model
        self.api_key = gp.ensure_api_key(cfg, self.store, non_interactive=True)

        self._agent: gp.PhoneAgent | None = None
        self._last_used: float = 0.0

    def _get_agent(self) -> gp.PhoneAgent:
        now = time.monotonic()
        if self._agent is not None:
            if (now - self._last_used) > self.idle_reset_sec:
                log.info("Автосброс устаревшего контекста (>%d с простоя)", self.idle_reset_sec)
                self._agent.clear_history()
            self._last_used = now
            return self._agent

        client = gp.create_llm_client(self.runtime_cfg, self.api_key)
        policy = gp.ApprovalPolicy(
            auto_approve_moderate=self.allow_ui,
            auto_approve_dangerous=self.allow_dangerous,
            non_interactive=True,
        )
        toolkit = gp.AgentToolkit(
            workspace=self.workspace,
            phone=self.phone,
            memory=self.memory,
            ui=self.ui,
            policy=policy,
            mode=self.mode,
            notifier=None,
        )
        self._agent = gp.PhoneAgent(
            client=client,
            toolkit=toolkit,
            store=self.store,
            memory=self.memory,
            ui=self.ui,
            notifier=self.notifier,
            max_steps=self.max_steps,
            turn_token_budget=self.budget,
        )
        self._last_used = now
        return self._agent

    def _handle_meta(self, text: str) -> str | None:
        low = text.strip().lower()
        if low == "!clear":
            if self._agent is not None:
                self._agent.clear_history()
            return "Контекст фонового агента очищен."
        if low == "!status":
            agent = self._get_agent()
            return (
                f"GemPhone Daemon {gp.APP_VERSION}\n"
                f"Провайдер: {agent.client.provider_name} / {agent.client.model}\n"
                f"Транспорт: {self.phone.transport_badge(force=True)}\n"
                f"Режим: {agent.toolkit.mode} | Шаги: {agent.max_steps}\n"
                f"Токены сессии: {gp.format_tokens(agent.usage.total_tokens)}\n"
                f"UI: {'ON' if self.allow_ui else 'OFF'} | Dangerous: {'ON' if self.allow_dangerous else 'BLOCKED'}"
            )
        if low.startswith("!mode "):
            new_mode = low.split(maxsplit=1)[1].strip()
            if new_mode in ("all", "phone", "code", "chat"):
                self.mode = new_mode  # type: ignore[assignment]
                self._get_agent().toolkit.mode = self.mode
                return f"Режим инструментов: {self.mode}"
            return "Доступно: !mode all | phone | code | chat"
        if low.startswith("!model "):
            new_model = text.strip().split(maxsplit=1)[1].strip()
            self.runtime_cfg.model = new_model
            self._get_agent().client.model = new_model
            return f"Модель переключена: {new_model}"
        return None

    def _write_result(self, task_id: str, ok: bool, answer: str, meta: dict[str, Any] | None = None) -> None:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            OUTBOX_DIR / f"{task_id}.json",
            {"id": task_id, "ok": ok, "answer": answer, "meta": meta or {}, "ts": time.time()},
        )
        try:
            LAST_ANSWER_PATH.write_text(answer + "\n", encoding="utf-8")
        except OSError:
            pass

    def process_file(self, job_path: Path) -> None:
        processing = job_path.with_suffix(".processing")
        try:
            job_path.rename(processing)
        except OSError:
            return

        try:
            data = json.loads(processing.read_text(encoding="utf-8"))
            task_id = str(data.get("id") or job_path.stem)
            text = str(data.get("text", "")).strip()
        except Exception as exc:
            log.warning("Битый файл очереди %s: %s", job_path.name, exc)
            processing.rename(processing.with_suffix(".bad"))
            return

        if not text:
            processing.unlink(missing_ok=True)
            return

        log.info("Задача [%s]: %s", task_id, text[:140])
        try:
            meta_reply = self._handle_meta(text)
            if meta_reply is not None:
                self._write_result(task_id, True, meta_reply)
                self.notifier.notify_done(meta_reply, steps=1, tokens=0, elapsed=0.1)
                processing.unlink(missing_ok=True)
                return

            agent = self._get_agent()
            answer = agent.ask(text) or "Задача выполнена."
            meta = dict(agent.last_turn_meta or {})
            self._write_result(task_id, True, answer, meta)
            processing.unlink(missing_ok=True)
        except Exception as exc:
            log.exception("Ошибка задачи [%s]: %s", task_id, exc)
            err_msg = f"Ошибка фонового агента: {exc}"
            self._write_result(task_id, False, err_msg)
            self.notifier.notify_error(err_msg)
            processing.rename(processing.with_suffix(".failed"))

    def run_forever(self, poll_sec: float = 0.25) -> None:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        gp.APP_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        wake_ok = acquire_wake_lock()
        log.info(
            "GemPhone Daemon %s запущен (PID=%d, wake_lock=%s, transport=%s)",
            gp.APP_VERSION,
            os.getpid(),
            wake_ok,
            self.phone.transport_badge(force=True),
        )

        stop = False

        def _on_signal(signum: int, _frame: Any) -> None:
            nonlocal stop
            log.info("Сигнал %d — останавливаю демон...", signum)
            stop = True

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        try:
            while not stop:
                jobs = sorted(QUEUE_DIR.glob("*.json"))
                for job in jobs:
                    if stop:
                        break
                    self.process_file(job)
                if not jobs:
                    time.sleep(poll_sec)
        finally:
            if hasattr(self.phone.bridge, "close"):
                self.phone.bridge.close()
            self.notifier.clear()
            PID_FILE.unlink(missing_ok=True)
            release_wake_lock()
            log.info("GemPhone Daemon остановлен.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GemPhone Background Daemon (без Telegram)")
    p.add_argument("-d", "--dir", type=Path, default=HOME, help="Рабочая папка агента.")
    p.add_argument("-P", "--provider", default=None, help="Провайдер LLM.")
    p.add_argument("-m", "--model", default=None, help="Модель LLM.")
    p.add_argument("--mode", choices=("all", "phone", "code", "chat"), default="phone", help="Профиль инструментов.")
    p.add_argument("--max-steps", type=int, default=12, help="Лимит шагов на задачу.")
    p.add_argument("--budget", type=int, default=60_000, help="Лимит токенов на задачу.")
    p.add_argument("--poll", type=float, default=0.25, help="Интервал опроса очереди в секундах.")
    p.add_argument("--tts", action="store_true", help="Озвучивать завершение через termux-tts-speak.")
    p.add_argument("--no-ui", action="store_true", help="Запретить UI-действия (только чтение).")
    p.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Разрешить опасные действия в фоне (shell/SMS/звонки) — осторожно!",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    runner = DaemonRunner(
        workspace=args.dir,
        provider=args.provider,
        model=args.model,
        mode=args.mode,
        max_steps=max(1, args.max_steps),
        budget=max(0, args.budget),
        allow_ui=not args.no_ui,
        allow_dangerous=args.allow_dangerous,
        tts=args.tts,
    )
    runner.run_forever(poll_sec=max(0.1, args.poll))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
