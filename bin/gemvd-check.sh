#!/data/data/com.termux/files/usr/bin/bash
# gemvd-check — проверка виртуального дисплея GemPhone Bridge шаг за шагом.
# Ничего не предполагает: печатает, что ответил мост, и сохраняет кадры дисплея в файлы.
#
#   gemvd-check.sh                         создать дисплей, запустить TikTok, снять кадр
#   gemvd-check.sh com.example.app         то же для другого пакета
#   gemvd-check.sh --swipe                 свайп вверх на дисплее + кадр до/после
#   gemvd-check.sh --stop                  остановить дисплей (приложения закройте заранее)
#
# Переменные: GEMPHONE_PORT (7331), OUT_DIR (~/gemvd-check)
set -u

PORT="${GEMPHONE_PORT:-7331}"
BASE="http://127.0.0.1:$PORT"
TOKEN_FILE="$HOME/.gemphone/bridge.token"
OUT_DIR="${OUT_DIR:-$HOME/gemvd-check}"
DEFAULT_PKGS=(com.zhiliaoapp.musically com.ss.android.ugc.trill)

[ -r "$TOKEN_FILE" ] || { echo "Нет токена: $TOKEN_FILE (в gemphone: /bridge token <токен>)"; exit 1; }
TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE")
mkdir -p "$OUT_DIR"

api()  { curl -s --max-time 40 -H "x-gemphone-token: $TOKEN" "$@"; }
vd()   { api -X POST -H "Content-Type: application/json" "$BASE/vdisplay" -d "$1"; }
# jget <json> <a.b.c> — достать поле, пусто если нет
jget() { python3 -c 'import json,sys
try:
    d=json.loads(sys.argv[1])
    for k in sys.argv[2].split("."):
        d=d[k]
    print(d)
except Exception:
    print("")' "$1" "$2"; }
shot() { # shot <файл> — кадр виртуального дисплея; печатает HTTP-код
    api -o "$1" -w "%{http_code}" "$BASE/vdisplay/screenshot"; }

echo "== 0. Мост =="
st=$(api "$BASE/status")
[ -n "$st" ] || { echo "Мост не отвечает на $BASE (включён ли GemPhone Bridge в Спец. возможностях?)"; exit 1; }
echo "$st"
if [ "$(jget "$st" ok)" != "True" ]; then
    echo "Мост ответил ошибкой (неверный токен? обновите: в gemphone /bridge token <токен>)."; exit 1
fi
echo "(foreground_package — лишь подсказка; события окон с других дисплеев тоже могут попадать сюда)"

if [ "${1:-}" = "--stop" ]; then
    echo "ВНИМАНИЕ: приложения, открытые на дисплее, при его остановке переезжают на ОСНОВНОЙ экран."
    echo "Скрипт их не закрывает: закройте приложение заранее (am force-stop <пакет> через Shizuku/ADB)."
    vd '{"action":"stop"}'; echo
    exit 0
fi

if [ "${1:-}" = "--swipe" ]; then
    id=$(jget "$(vd '{"action":"status"}')" display_id)
    [ -n "$id" ] && [ "$id" != "-1" ] || { echo "Дисплей не создан"; exit 1; }
    echo "кадр до:  $(shot "$OUT_DIR/before.png") -> $OUT_DIR/before.png"
    vd "{\"action\":\"swipe\",\"display_id\":$id,\"x1\":540,\"y1\":1800,\"x2\":540,\"y2\":600,\"duration_ms\":300}"; echo
    sleep 2
    echo "кадр после: $(shot "$OUT_DIR/after.png") -> $OUT_DIR/after.png"
    echo "Сравните before.png и after.png: на дисплее должно смениться содержимое."
    exit 0
fi

if [ "$#" -gt 0 ]; then PKGS=("$@"); else PKGS=("${DEFAULT_PKGS[@]}"); fi

echo; echo "== 1. Пересоздаём дисплей =="
vd '{"action":"stop"}' >/dev/null; sleep 1
out=$(vd '{"action":"create"}')
echo "$out"
id=$(jget "$out" display_id)
state=$(jget "$out" vd.state)
if [ "$(jget "$out" need_permission)" = "True" ]; then
    echo "Нужно разрешение Shizuku: подтвердите GemPhone Bridge в приложении Shizuku и запустите скрипт снова."; exit 1
fi
if [ -z "$id" ] || [ "$id" = "-1" ]; then
    echo "Дисплей не создан. Смотрите ответ выше (granted/shizuku)."; exit 1
fi
echo "display_id=$id state=$state flags(запрошено/применено)=$(jget "$out" vd.requested_flags)/$(jget "$out" vd.display_flags)"
if [ "$state" != "ON" ]; then
    echo "!! Дисплей в состоянии '$state', а не ON — Activity на него не выведутся. Дальше не идём."; exit 1
fi

echo; echo "== 2. Запуск приложения на дисплее $id =="
launched=""
for p in "${PKGS[@]}"; do
    echo "-- $p"
    r=$(vd "{\"action\":\"launch\",\"display_id\":$id,\"package\":\"$p\"}")
    echo "команда: $(jget "$r" command)"
    echo "ответ am: $(jget "$r" output)"
    if [ "$(jget "$r" ok)" = "True" ]; then launched="$p"; break; fi
done
[ -n "$launched" ] || { echo "Ни один пакет не запустился: причина — в строках «ответ am» выше."; exit 1; }

echo; echo "== 3. Проверка по кадру дисплея (не по факту запуска) =="
sleep 4
code=$(shot "$OUT_DIR/vd.png")
size=$(wc -c < "$OUT_DIR/vd.png" 2>/dev/null || echo 0)
echo "HTTP $code, $size байт -> $OUT_DIR/vd.png"
if [ "$code" != "200" ]; then echo "Кадра нет: $(cat "$OUT_DIR/vd.png" 2>/dev/null | head -c 300)"; exit 1; fi
echo "Откройте файл (termux-open $OUT_DIR/vd.png) или пришлите его: там должен быть интерфейс $launched, а не пустой/чёрный экран."
echo "Затем проверьте отклик на касания: gemvd-check.sh --swipe"
