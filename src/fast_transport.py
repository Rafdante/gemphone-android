"""Ускоренный транспортный слой и единая политика безопасности для GemPhone 3.9."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from safety import check_fetch_url, detect_destructive_command, write_private

try:
    import httpx  # type: ignore[import-not-found]

    HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HAS_HTTPX = False


class FastBridgeClient:
    """HTTP/1.1 Keep-Alive клиент для GemPhone Bridge с кэшем метаданных экрана."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7331,
        token_path: Path | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.port = port
        self.token_path = token_path or (Path.home() / ".gemphone" / "bridge.token")
        self.timeout = timeout
        self.token: str = self.load_token()

        self._last_ping_at: float = 0.0
        self._last_ping_ok: bool = False
        self._last_unauthorized: bool = False
        self._last_info: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._http: Any = None

    def _get_client(self) -> Any:
        if not HAS_HTTPX or httpx is None:
            return None
        with self._lock:
            if self._http is None or self._http.is_closed:
                self._http = httpx.Client(
                    base_url=self.base_url(),
                    timeout=self.timeout,
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=25.0),
                )
            return self._http

    def close(self) -> None:
        with self._lock:
            if self._http is not None:
                try:
                    self._http.close()
                except Exception:
                    pass
                self._http = None

    def load_token(self) -> str:
        if self.token_path.exists():
            try:
                tok = self.token_path.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
            except OSError:
                pass
        return os.environ.get("GEMPHONE_BRIDGE_TOKEN", "").strip()

    def save_token(self, token: str) -> None:
        cleaned = token.strip()
        self.token = cleaned
        self._last_ping_at = 0.0
        self._last_unauthorized = False
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            write_private(self.token_path, cleaned + "\n")
        except OSError:
            pass

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _update_cached_meta(self, data: dict[str, Any]) -> None:
        if not data:
            return
        w = data.get("screen_width") or data.get("width")
        h = data.get("screen_height") or data.get("height")
        app = data.get("foreground_package") or data.get("app")
        if w and h:
            self._last_info["screen_width"] = int(w)
            self._last_info["screen_height"] = int(h)
        if app:
            self._last_info["foreground_package"] = str(app)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any], bytes]:
        if not self.token:
            self.token = self.load_token()
        headers: dict[str, str] = {"User-Agent": "GemPhone/3.9"}
        if self.token:
            headers["x-gemphone-token"] = self.token

        client = self._get_client()
        if client is not None:
            resp = client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout or self.timeout,
            )
            raw = resp.content
            parsed: dict[str, Any] = {}
            if "json" in resp.headers.get("content-type", "").lower():
                try:
                    obj = resp.json()
                    if isinstance(obj, dict):
                        parsed = obj
                        self._update_cached_meta(parsed)
                except ValueError:
                    pass
            return resp.status_code, parsed, raw

        url = f"{self.base_url()}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                parsed = {}
                if "json" in (resp.headers.get("Content-Type") or "").lower():
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                        if isinstance(obj, dict):
                            parsed = obj
                            self._update_cached_meta(parsed)
                    except json.JSONDecodeError:
                        pass
                return resp.status, parsed, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            parsed = {}
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(obj, dict):
                    parsed = obj
            except json.JSONDecodeError:
                pass
            return exc.code, parsed, raw

    def is_available(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and (now - self._last_ping_at) < 12.0:
            return self._last_ping_ok
        self._last_ping_at = now
        try:
            status, info, _ = self._request("GET", "/status", timeout=1.5)
            if status in (401, 403):
                self._last_ping_ok = False
                self._last_unauthorized = True
                return False
            self._last_unauthorized = False
            ok = status == 200 and bool(info.get("ok", info.get("service_running", True)))
            self._last_ping_ok = ok
            if ok and info:
                self._last_info.update(info)
                self._update_cached_meta(info)
            return ok
        except Exception:
            self._last_ping_ok = False
            self._last_unauthorized = False
            return False

    def status_info(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._last_ping_ok and self._last_info:
            return dict(self._last_info)
        if self.is_available(force=True) and self._last_info:
            return dict(self._last_info)
        return {
            "connected": False,
            "unauthorized": self._last_unauthorized,
            "port": self.port,
            "token_set": bool(self.token),
        }

    def dump_ui(self, *, only_interactive: bool = False, max_elements: int = 60) -> dict[str, Any]:
        _, data, _ = self._request(
            "GET",
            "/ui",
            params={"only_interactive": "1" if only_interactive else "0", "max_elements": str(max_elements)},
            timeout=10.0,
        )
        if not data.get("ok", True) and data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def tap(
        self,
        *,
        x: int,
        y: int,
        node_id: str = "",
        long_press: bool = False,
        duration_ms: int = 700,
        auto_dump: bool = False,
        settle_ms: int = 200,
    ) -> dict[str, Any]:
        del node_id
        payload: dict[str, Any] = {
            "x": x,
            "y": y,
            "duration_ms": duration_ms if long_press else 50,
        }
        if auto_dump:
            payload["auto_dump"] = True
            payload["settle_ms"] = settle_ms
        _, data, _ = self._request("POST", "/tap", json_body=payload, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def swipe(
        self,
        *,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 260,
        auto_dump: bool = False,
        settle_ms: int = 200,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "duration_ms": duration_ms,
        }
        if auto_dump:
            payload["auto_dump"] = True
            payload["settle_ms"] = settle_ms
        _, data, _ = self._request("POST", "/swipe", json_body=payload, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def type_text(
        self,
        text: str,
        *,
        press_enter: bool = False,
        auto_dump: bool = False,
        settle_ms: int = 180,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text, "press_enter": press_enter}
        if auto_dump:
            payload["auto_dump"] = True
            payload["settle_ms"] = settle_ms
        _, data, _ = self._request("POST", "/type", json_body=payload, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def global_action(self, action_name: str, *, auto_dump: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": action_name}
        if auto_dump:
            payload["auto_dump"] = True
        _, data, _ = self._request("POST", "/key", json_body=payload, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def app_action(self, action: str, *, package: str = "", query: str = "", url: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"action": action}
        if package:
            payload["package"] = package
        if query:
            payload["query"] = query
        if url:
            payload["url"] = url
        _, data, _ = self._request("POST", "/app", json_body=payload, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def batch(
        self,
        steps: list[dict[str, Any]],
        *,
        auto_dump: bool = True,
        only_interactive: bool = False,
        settle_ms: int = 220,
    ) -> dict[str, Any]:
        _, data, _ = self._request(
            "POST",
            "/batch",
            json_body={
                "steps": steps,
                "auto_dump": auto_dump,
                "only_interactive": only_interactive,
                "settle_ms": settle_ms,
            },
            timeout=25.0,
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def capture_screenshot_bytes(
        self,
        *,
        max_dim: int = 1280,
        quality: int = 82,
        as_png: bool = False,
    ) -> tuple[bytes, str]:
        params = {
            "max_dim": str(max_dim),
            "quality": str(quality),
            "format": "png" if as_png else "jpeg",
        }
        status, data, raw = self._request("GET", "/screenshot", params=params, timeout=15.0)
        if data and isinstance(data.get("image_b64"), str) and data["image_b64"]:
            mime = str(data.get("mime_type") or "image/jpeg")
            return base64.b64decode(data["image_b64"]), mime
        if status >= 400 or (data and data.get("error")):
            raise RuntimeError(str(data.get("error") or f"Bridge screenshot HTTP {status}"))
        if len(raw) > 100:
            mime = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
            return raw, mime
        raise RuntimeError("Bridge не вернул изображение скриншота (проверьте Android 11+).")


class PersistentLLMPool:
    """Единый пул httpx.Client для переиспользования TLS/HTTP2 между шагами LLM."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Any = None

    def get(self, timeout: int = 120) -> Any:
        if not HAS_HTTPX or httpx is None:
            return None
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=12, max_keepalive_connections=6, keepalive_expiry=60.0),
                )
            return self._client


_LLM_POOL = PersistentLLMPool()


def patch_gemphone_runtime(gp: Any) -> None:
    """Применяет все ускорения и патчи безопасности к модулю gemphone (идемпотентно)."""
    if getattr(gp, "_FAST_TRANSPORT_PATCHED", False):
        return

    gp.AccessibilityBridgeClient = FastBridgeClient

    if "phone_batch" not in gp.SYSTEM_PROMPT:
        gp.SYSTEM_PROMPT += (
            "\n7. SPEED: When 2+ UI actions are clear (e.g. tap field -> sleep 200 -> type -> enter), "
            "combine them into a single `phone_batch` call (auto_dump=true) to cut latency."
        )

    def _safe_config_save(self: Any) -> None:
        payload = {
            "active_provider": self.active_provider,
            "providers": {name: cfg.to_dict() for name, cfg in self.providers.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_private(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            pass

    gp.ConfigStore.save = _safe_config_save

    orig_parse = gp.PhoneController._parse_bridge_nodes

    def _compat_parse_bridge_nodes(self: Any, payload: dict[str, Any], *, only_interactive: bool = False) -> list[Any]:
        raw_nodes = payload.get("nodes") or payload.get("elements") or []
        if isinstance(raw_nodes, list):
            for item in raw_nodes:
                if isinstance(item, dict):
                    if "contentDesc" in item and "content_desc" not in item:
                        item["content_desc"] = item["contentDesc"]
                    if "resourceId" in item and "resource_id" not in item:
                        item["resource_id"] = item["resourceId"]
                    if "className" in item and "class_name" not in item:
                        item["class_name"] = item["className"]
        return orig_parse(self, payload, only_interactive=only_interactive)

    gp.PhoneController._parse_bridge_nodes = _compat_parse_bridge_nodes

    def _fast_get_screen_size(self: Any) -> tuple[int, int]:
        if self._screen_size_cache is not None:
            return self._screen_size_cache
        if self.has_bridge():
            info = self.bridge.status_info(force=False)
            w = int(info.get("screen_width") or info.get("width") or 0)
            h = int(info.get("screen_height") or info.get("height") or 0)
            if w > 0 and h > 0:
                self._screen_size_cache = (w, h)
                return self._screen_size_cache
        if self.active_adb_serial():
            try:
                proc = self.run_adb(["shell", "wm", "size"], timeout=6)
                m = gp.re.search(r"(\d+)\s*x\s*(\d+)", proc.stdout)
                if m:
                    self._screen_size_cache = (int(m.group(1)), int(m.group(2)))
                    return self._screen_size_cache
            except Exception:
                pass
        return (1080, 2400)

    def _fast_get_foreground_app(self: Any) -> str:
        if self.has_bridge():
            info = self.bridge.status_info(force=False)
            pkg = str(info.get("foreground_package") or info.get("app") or "").strip()
            if pkg:
                return pkg
        if self.active_adb_serial():
            try:
                proc = self.run_adb(["shell", "dumpsys", "window"], timeout=8)
                for pat in (
                    r"mCurrentFocus=Window\{[^\}]+\s+([^\s\}]+)\}",
                    r"mFocusedApp=[^\n]*ActivityRecord\{[^\}]+\s+([^\s\}]+)",
                ):
                    m = gp.re.search(pat, proc.stdout)
                    if m:
                        return m.group(1)
            except Exception:
                pass
        return "unknown"

    gp.PhoneController.get_screen_size = _fast_get_screen_size
    gp.PhoneController.get_foreground_app = _fast_get_foreground_app

    orig_screenshot = gp.PhoneController.capture_screenshot

    def _fast_capture_screenshot(
        self: Any,
        *,
        save_path: Path | None = None,
        max_dim: int | None = None,
        quality: int | None = None,
        hq: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> tuple[dict[str, Any], Any]:
        target_dim = gp.SCREENSHOT_HQ_MAX_DIM if hq else (max_dim or self.screenshot_max_dim)
        target_qual = 90 if hq else (quality or self.screenshot_quality)

        if self.has_bridge():
            try:
                raw_bytes, mime_type = self.bridge.capture_screenshot_bytes(
                    max_dim=target_dim,
                    quality=target_qual,
                    as_png=False,
                )
                saved_to = ""
                if save_path is not None:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.write_bytes(raw_bytes)
                    saved_to = str(save_path)
                meta = {
                    "ok": True,
                    "via": "bridge",
                    "app": self.get_foreground_app(),
                    "dim": f"<={target_dim}px",
                    "kb": round(len(raw_bytes) / 1024, 1),
                    "saved": saved_to or None,
                }
                return meta, gp.ImageAttachment(
                    mime_type=mime_type,
                    data_b64=base64.b64encode(raw_bytes).decode("ascii"),
                )
            except Exception:
                pass

        return orig_screenshot(
            self,
            save_path=save_path,
            max_dim=max_dim,
            quality=quality,
            hq=hq,
            cancel_event=cancel_event,
        )

    gp.PhoneController.capture_screenshot = _fast_capture_screenshot

    orig_app_control = gp.PhoneController.app_control

    def _fast_app_control(
        self: Any,
        action: str,
        *,
        package: str = "",
        query: str = "",
        url: str = "",
    ) -> dict[str, Any]:
        act = action.strip().lower()
        if act == "open_url":
            scheme = urllib.parse.urlsplit(url.strip()).scheme.lower()
            if scheme not in ("http", "https"):
                raise ValueError("open_url: разрешены только http:// и https:// ссылки.")
        if self.has_bridge() and act in ("list", "launch", "open_url"):
            try:
                res = self.bridge.app_action(act, package=package, query=query, url=url)
                if res.get("ok", False):
                    res["via"] = "bridge"
                    return res
            except Exception:
                pass
        return orig_app_control(self, action, package=package, query=query, url=url)

    gp.PhoneController.app_control = _fast_app_control

    def _batch_actions(
        self: Any,
        steps: list[dict[str, Any]],
        *,
        auto_dump: bool = True,
        only_interactive: bool = False,
        settle_ms: int = 220,
    ) -> dict[str, Any]:
        if not self.has_bridge():
            raise RuntimeError("phone_batch требует активный GemPhone Bridge (127.0.0.1:7331).")

        resolved_steps: list[dict[str, Any]] = []
        for st in steps[:20]:
            if not isinstance(st, dict):
                continue
            item = dict(st)
            op = str(item.get("op", "")).lower()
            if op == "tap" and item.get("element_id") is not None:
                el = self.last_ui_elements.get(int(item["element_id"]))
                if el is None:
                    raise ValueError(f"element_id={item['element_id']} не найден в последнем дампе UI.")
                item["x"] = el.center_x
                item["y"] = el.center_y
            elif op == "swipe" and item.get("direction"):
                w, h = self.get_screen_size()
                d = str(item["direction"]).lower()
                if d == "down":
                    item.update(x1=w // 2, y1=int(h * 0.78), x2=w // 2, y2=int(h * 0.25))
                elif d == "up":
                    item.update(x1=w // 2, y1=int(h * 0.25), x2=w // 2, y2=int(h * 0.78))
                elif d == "left":
                    item.update(x1=int(w * 0.85), y1=h // 2, x2=int(w * 0.15), y2=h // 2)
                elif d == "right":
                    item.update(x1=int(w * 0.15), y1=h // 2, x2=int(w * 0.85), y2=h // 2)
            elif op == "app" and str(item.get("action", "")).lower() == "open_url":
                scheme = urllib.parse.urlsplit(str(item.get("url", "")).strip()).scheme.lower()
                if scheme not in ("http", "https"):
                    raise ValueError("open_url в batch: разрешены только http(s).")
            resolved_steps.append(item)

        bres = self.bridge.batch(
            resolved_steps,
            auto_dump=auto_dump,
            only_interactive=only_interactive,
            settle_ms=settle_ms,
        )
        out: dict[str, Any] = {
            "ok": bool(bres.get("ok", False)),
            "via": "bridge",
            "steps_done": len(bres.get("results") or []),
            "results": bres.get("results") or [],
        }
        ui_raw = bres.get("ui_dump")
        if isinstance(ui_raw, dict):
            elements = self._parse_bridge_nodes(ui_raw, only_interactive=only_interactive)
            self.last_ui_elements = {e.element_id: e for e in elements}
            out["app"] = ui_raw.get("app") or self.get_foreground_app()
            out["ui"] = "\n".join(e.to_compact_line() for e in elements)
        return out

    gp.PhoneController.batch_actions = _batch_actions

    orig_toolkit_init = gp.AgentToolkit.__init__
    orig_tool_specs = gp.AgentToolkit.tool_specs
    orig_run_command = gp.AgentToolkit._tool_run_command
    orig_web_fetch = gp.AgentToolkit._tool_web_fetch

    def _safe_run_command(self: Any, args: dict[str, Any]) -> Any:
        cmd = str(args.get("command", "")).strip()
        blocked = detect_destructive_command(cmd)
        if blocked:
            return gp.ToolExecutionResult({"error": f"Заблокировано safety.py: {blocked}"})
        return orig_run_command(self, args)

    def _safe_web_fetch(self: Any, args: dict[str, Any]) -> Any:
        url = str(args.get("url", "")).strip()
        reason = check_fetch_url(url)
        if reason:
            return gp.ToolExecutionResult({"error": f"URL отклонён политикой безопасности: {reason}"})
        return orig_web_fetch(self, args)

    def _tool_phone_batch(self: Any, args: dict[str, Any]) -> Any:
        steps = args.get("steps")
        if not isinstance(steps, list) or not steps:
            return gp.ToolExecutionResult({"error": "Передайте непустой массив steps."})
        ok, reason = self._confirm(f"Серия из {len(steps)} действий на экране (Bridge batch)", risk="moderate")
        if not ok:
            return gp.ToolExecutionResult({"rejected": reason})
        return gp.ToolExecutionResult(
            self.phone.batch_actions(
                steps,
                auto_dump=bool(args.get("auto_dump", True)),
                only_interactive=bool(args.get("only_interactive", False)),
                settle_ms=int(args.get("settle_ms", 220)),
            )
        )

    def _patched_toolkit_init(self: Any, *a: Any, **kw: Any) -> None:
        orig_toolkit_init(self, *a, **kw)
        self._dispatch["phone_batch"] = self._tool_phone_batch

    def _patched_tool_specs(self: Any) -> list[dict[str, Any]]:
        specs = orig_tool_specs(self)
        if self.mode in ("all", "phone"):
            specs.append(
                {
                    "name": "phone_batch",
                    "description": (
                        "FAST macro: execute multiple UI steps (tap/swipe/type/key/sleep/app) in ONE call "
                        "and auto-return fresh UI dump. Use whenever >=2 UI actions are obvious."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "op": {"type": "string", "enum": ["tap", "swipe", "type", "key", "sleep", "app"]},
                                        "element_id": {"type": "integer"},
                                        "x": {"type": "integer"},
                                        "y": {"type": "integer"},
                                        "direction": {"type": "string", "enum": ["down", "up", "left", "right"]},
                                        "text": {"type": "string"},
                                        "press_enter": {"type": "boolean"},
                                        "key": {"type": "string"},
                                        "ms": {"type": "integer"},
                                        "delay_ms": {"type": "integer"},
                                        "action": {"type": "string"},
                                        "package": {"type": "string"},
                                    },
                                    "required": ["op"],
                                },
                            },
                            "auto_dump": {"type": "boolean"},
                            "settle_ms": {"type": "integer"},
                        },
                        "required": ["steps"],
                    },
                }
            )
        return specs

    gp.AgentToolkit.__init__ = _patched_toolkit_init
    gp.AgentToolkit.tool_specs = _patched_tool_specs
    gp.AgentToolkit._tool_run_command = _safe_run_command
    gp.AgentToolkit._tool_web_fetch = _safe_web_fetch
    gp.AgentToolkit._tool_phone_batch = _tool_phone_batch
    gp.TOOL_DISPLAY_NAMES["phone_batch"] = "Batch"

    if HAS_HTTPX and httpx is not None:
        orig_sync = gp._http_post_json_sync

        def _pooled_http_post_json_sync(
            *,
            url: str,
            payload: dict[str, Any],
            headers: dict[str, str],
            timeout: int = 120,
            max_retries: int = 3,
            cancel_event: threading.Event | None = None,
        ) -> dict[str, Any]:
            client = _LLM_POOL.get(timeout=timeout)
            if client is None:
                return orig_sync(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                    max_retries=max_retries,
                    cancel_event=cancel_event,
                )
            retryable = {429, 500, 502, 503, 504}
            merged = {
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": gp.HTTP_USER_AGENT,
                **headers,
            }
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                if cancel_event is not None and cancel_event.is_set():
                    raise gp.RequestCancelledError("Запрос отменён.")
                try:
                    resp = client.post(url, json=payload, headers=merged, timeout=timeout)
                    if resp.status_code >= 400:
                        msg = gp._extract_error_message(resp.text) or f"HTTP {resp.status_code}"
                        if resp.status_code == 429 and "limit: 0" in msg:
                            raise gp.ApiError(f"HTTP 429: {msg}", 429)
                        if resp.status_code in retryable and attempt < max_retries:
                            time.sleep(min(12.0, 1.5 * (2**attempt)))
                            last_error = gp.ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                            continue
                        raise gp.ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                    return resp.json()
                except gp.ApiError:
                    raise
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    if attempt < max_retries:
                        time.sleep(min(8.0, 1.5 * (2**attempt)))
                        last_error = exc
                        continue
                    raise gp.ApiError(f"Сетевая ошибка: {exc}") from exc
            raise gp.ApiError(f"Не удалось выполнить запрос: {last_error}")

        gp._http_post_json_sync = _pooled_http_post_json_sync

    gp._FAST_TRANSPORT_PATCHED = True
