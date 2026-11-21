#!/data/data/com.termux/files/usr/bin/bash
# gemagent — «агент в фоне»: держит сессию живой, показывает плавающую капсулу
# поверх любых приложений, чтобы можно было пользоваться телефоном во время работы.
#
#   gemagent on [текст]     включить фоновый режим (капсула + уведомление)
#   gemagent status "..."   обновить текст на капсуле
#   gemagent off            выключить
#   gemagent prep           разрешения: оверлей + игнор батареи
set -euo pipefail

PORT="${GEMPHONE_PORT:-7331}"
TOKEN_FILE="$HOME/.gemphone/bridge.token"
TOKEN=""
[ -f "$TOKEN_FILE" ] && TOKEN="$(cat "$TOKEN_FILE" | tr -d '\n')"

api() {
  local payload="$1"
  if [ -n "$TOKEN" ]; then
    curl -s -m 8 -H "x-gemphone-token: $TOKEN" -H "Content-Type: application/json" \
      -d "$payload" "http://127.0.0.1:$PORT/agent"
  else
    curl -s -m 8 -H "Content-Type: application/json" \
      -d "$payload" "http://127.0.0.1:$PORT/agent"
  fi
  echo
}

json_esc() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

case "${1:-on}" in
  prep)
    if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock && echo "✓ wake-lock Termux включён"; fi
    api '{"action":"start","text":"подготовка","detail":"включите разрешение на оверлей","overlay":true}'
    am start -a android.settings.action.MANAGE_OVERLAY_PERMISSION 2>/dev/null \
      || am start -a android.settings.ACTION_MANAGE_OVERLAY_PERMISSION 2>/dev/null || true
    sleep 1
    am start -a android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS \
      -d package:com.termux 2>/dev/null || true
    ;;
  on)
    TEXT="${2:-работает}"
    if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock || true; fi
    api "{\"action\":\"start\",\"text\":$(json_esc "$TEXT"),\"detail\":\"работаю в фоне — пользуйтесь телефоном\",\"overlay\":true}"
    ;;
  status|update)
    TEXT="${2:-работает}"
    DETAIL="${3:-}"
    api "{\"action\":\"update\",\"text\":$(json_esc "$TEXT"),\"detail\":$(json_esc "$DETAIL"),\"overlay\":true}"
    ;;
  off|stop)
    api '{"action":"stop"}'
    command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
    ;;
  *)
    echo "usage: gemagent {on [text]|status <text> [detail]|off|prep}" >&2
    exit 2
    ;;
esac
