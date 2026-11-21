#!/data/data/com.termux/files/usr/bin/bash
# gemphone-bg — управление фоновым агентом GemPhone 3.9 в Termux (без Telegram).
set -euo pipefail

APP_DIR="$HOME/.gemphone"
QUEUE_DIR="$APP_DIR/queue"
OUT_DIR="$APP_DIR/out"
LOG_DIR="$APP_DIR/logs"
PID_FILE="$APP_DIR/daemon.pid"
LAST_ANS="$APP_DIR/last_answer.txt"
DAEMON_BIN="${GEMPHONE_DAEMON:-$HOME/gemphone-android/src/gemphone_daemon.py}"
[ -f "$DAEMON_BIN" ] || DAEMON_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/gemphone_daemon.py"

mkdir -p "$QUEUE_DIR" "$OUT_DIR" "$LOG_DIR"

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null
}

start_daemon() {
  if is_running; then
    echo "[gemphone-bg] Уже запущен (PID $(cat "$PID_FILE"))"
    return 0
  fi
  if [ ! -f "$DAEMON_BIN" ]; then
    echo "[gemphone-bg] ОШИБКА: не найден $DAEMON_BIN" >&2
    exit 1
  fi
  if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
  fi
  nohup python3 "$DAEMON_BIN" "$@" >> "$LOG_DIR/daemon.log" 2>&1 &
  echo $! > "$PID_FILE"
  echo "[gemphone-bg] Фоновый агент запущен (PID $(cat "$PID_FILE"))"
}

stop_daemon() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
    echo "[gemphone-bg] Остановлен (PID $pid)"
  else
    echo "[gemphone-bg] Не запущен"
  fi
  rm -f "$PID_FILE"
  if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock || true
  fi
}

enqueue_task() {
  local wait_mode=0
  if [ "${1:-}" = "--wait" ]; then
    wait_mode=1
    shift
  fi
  local prompt="$*"
  if [ -z "$prompt" ]; then
    echo "Использование: gemphone-bg ask [--wait] \"текст задачи\"" >&2
    exit 1
  fi

  if ! is_running; then
    echo "[gemphone-bg] Демон не запущен — запускаю автоматически..."
    start_daemon --mode phone
    sleep 0.6
  fi

  local task_id
  task_id="$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')"
  python3 - "$QUEUE_DIR" "$task_id" "$prompt" <<'PYEOF'
import json, os, sys
from pathlib import Path
qdir, tid, text = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
tmp = qdir / f"{tid}.tmp"
dst = qdir / f"{tid}.json"
tmp.write_text(json.dumps({"id": tid, "text": text}, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, dst)
PYEOF

  echo "[gemphone-bg] Задача #$task_id поставлена в очередь."
  if [ "$wait_mode" = "1" ]; then
    local out_file="$OUT_DIR/${task_id}.json"
    for _ in $(seq 1 600); do
      if [ -f "$out_file" ]; then
        python3 - "$out_file" <<'PYEOF'
import json, sys
data = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(data.get("answer", ""))
PYEOF
        return 0
      fi
      sleep 0.25
    done
    echo "[gemphone-bg] Таймаут ожидания ответа (проверь: gemphone-bg logs)" >&2
    return 1
  fi
}

voice_task() {
  if ! command -v termux-speech-to-text >/dev/null 2>&1; then
    echo "[gemphone-bg] Нет termux-speech-to-text (установи Termux:API)" >&2
    exit 1
  fi
  echo "[gemphone-bg] Говорите задачу..."
  local spoken
  spoken="$(termux-speech-to-text | tr -d '\r')"
  if [ -z "$spoken" ]; then
    echo "[gemphone-bg] Речь не распознана."
    exit 1
  fi
  echo "[gemphone-bg] Распознано: $spoken"
  enqueue_task "$spoken"
}

show_status() {
  if is_running; then
    echo "● GemPhone Daemon : работает (PID $(cat "$PID_FILE"))"
  else
    echo "○ GemPhone Daemon : остановлен"
  fi
  local qcount
  qcount="$(find "$QUEUE_DIR" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "• В очереди задач : $qcount"
  if command -v curl >/dev/null 2>&1 && [ -f "$APP_DIR/bridge.token" ]; then
    local tok
    tok="$(tr -d '\r\n' < "$APP_DIR/bridge.token")"
    if curl -fsS -m 1.5 -H "x-gemphone-token: $tok" http://127.0.0.1:7331/status >/dev/null 2>&1; then
      echo "● Bridge :7331    : активен"
    else
      echo "○ Bridge :7331    : не отвечает"
    fi
  fi
}

install_boot() {
  mkdir -p "$HOME/.termux/boot"
  local f="$HOME/.termux/boot/start-gemphone-daemon.sh"
  cat > "$f" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock || true
sleep 5
gemphone-bg start --mode phone
EOF
  chmod +x "$f"
  echo "[gemphone-bg] Установлен скрипт Termux:Boot: $f"
}

install_widget() {
  mkdir -p "$HOME/.shortcuts"
  local f="$HOME/.shortcuts/GemPhone-Ask"
  cat > "$f" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
RES=$(termux-dialog text -t "GemPhone (фон)" -i "Что сделать на телефоне?" 2>/dev/null)
TEXT=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("text","") if d.get("code")==-1 else "")' "$RES" 2>/dev/null || true)
[ -n "$TEXT" ] && gemphone-bg ask "$TEXT"
EOF
  chmod +x "$f"
  echo "[gemphone-bg] Создан ярлык Termux:Widget: $f"
}

cmd="${1:-status}"
shift || true

case "$cmd" in
  start)   start_daemon "$@" ;;
  stop)    stop_daemon ;;
  restart) stop_daemon; sleep 0.3; start_daemon "$@" ;;
  status)  show_status ;;
  ask)     enqueue_task "$@" ;;
  voice)   voice_task ;;
  result)  [ -f "$LAST_ANS" ] && cat "$LAST_ANS" || echo "(ответов пока нет)" ;;
  logs)    tail -n 50 -f "$LOG_DIR/daemon.log" ;;
  boot)    install_boot ;;
  widget)  install_widget ;;
  *)
    echo "Использование: gemphone-bg {start|stop|restart|status|ask|voice|result|logs|boot|widget}"
    exit 1
    ;;
esac
