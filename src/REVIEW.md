# Патч GemPhone по ревью (проверено по коду, не на догадках)

## 1. Почему падал adb_type_text — диагноз подтверждён
Код до патча (GemAccessibilityService.kt:159):
    val root = rootInActiveWindow ?: return false
    val target = root.findFocus(FOCUS_INPUT) ?: findFirstEditable(root) ?: return false
    val ok = target.performAction(ACTION_SET_TEXT, args)

Подтверждено: окна не перебирались (windows не использовался), actionList не проверялся,
причина отказа не возвращалась — /type отдавал только {"ok": false}.

НЕ подтверждено: «клиент ходит на /tree,/click,/global, которых нет в сервере» —
в текущем gemphone.py таких вызовов нет, уже починено.

## 2. Что внесено
Bridge (Kotlin):
- performTypeText -> TypeResult(ok, warnings, reason): перебор всех windows, collectEditables(),
  приоритет FOCUS_INPUT, проверка ACTION_SET_TEXT в actionList (supportsSetText),
  человекочитаемые причины: no editable field found / ACTION_SET_TEXT rejected /
  ACTION_SET_TEXT unsupported (custom/WebView field?) / enter not delivered.
- /type отдаёт ok + warnings[] + reason.
- handleAppAction: понятно "POWER_DIALOG" (клиент слал его зря); open_url принимает ТОЛЬКО http/https.
- LocalHttpServer.start(): BindException не глотается — lastError, running=false, logcat,
  новый isListening(); /status отдаёт port_bound и server_error.
- MainActivity.refreshUi(): «Сервис активен» только если порт реально слушается, иначе причина.
- accessibility_service_config.xml: добавлен android:isAccessibilityTool="true".

Клиент (Python):
- type_text: reason больше не теряется. ok:false + нет ADB -> исключение с причиной бриджа,
  а не «подключите ADB». При успехе с предупреждениями они возвращаются в warnings.
- deploy.sh: ставит в $PREFIX/bin, py_compile перед установкой, вычищает alias'ы
  gemphone/gemcode из rc-файлов, предупреждает про старую копию в ~/bin.

Применено на телефоне: alias'ы удалены, gemphone -> $PREFIX/bin/gemphone.py,
старая копия ~/bin/gemphone.py удалена.

## 3. Осознанно не сделано
- ACTION_PASTE фолбэк: требует фокуса и работает не везде; вместо этого клиент внятно
  сообщает причину, ручной обход = adb_tap + вставка из буфера.
- Токен: в .bashrc его не было (только GEMINI_API_KEY и alias'ы) — не менял.
- compare_digest для токена, ограничение пула потоков — localhost, низкий приоритет.
- Монолит 5100 строк и отсутствие тестов — отдельная задача.

## 4. Проверка
- Python: py_compile + gemphone --help — OK, новая версия запускается.
- Kotlin: НЕ компилировался (Gradle/JDK на телефоне нет) — только CI workflow.

## Файлы
- /sdcard/Download/gemphone_patched.zip — src/
- /sdcard/Download/gemphone_bridge_patch.zip — bridge/app/
