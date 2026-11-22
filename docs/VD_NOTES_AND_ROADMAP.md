# GemPhone: виртуальный дисплей — наработка, сессия 2026-11-22, roadmap

## 1. Что уже сделано в коде (закоммичено)

- `985afb7` — Fix virtual display: реальный ImageReader surface вместо null, флаги PUBLIC+SUPPORTS_TOUCH, чтобы внешние приложения рендерились.
- `6b64424` — пакет фиксов из архива `896d69be-gemphone-android-main_3_fixed.zip`:
  - `GemVirtualDisplay.kt`: SUPPORTS_TOUCH как литерал `1 shl 6` (константы нет в публичном SDK — иначе Unresolved reference на сборке), ImageReader хранит последний кадр, синхронный `stop()` (убрана гонка stop/create через main.post), в `create` можно передать `"flags"` из запроса.
  - `LocalHttpServer.kt`: `GET /vdisplay/screenshot` (PNG/JPEG из последнего кадра ImageReader), в ответах `/vdisplay` блок `vd` со state/флагами/размером, `launch` возвращает вывод `am start`, fallback запуска по пакету, если нет launcher-компонента.
  - `ShellRunner.kt`: Shizuku `newProcess` ищется через `getDeclaredMethod` (в Shizuku 13.x метод не публичный, через getMethod команды молча падали), ошибки возвращаются текстом, пакет/компонент валидируются и экранируются (закрыта инъекция в `sh -c`).
  - `bin/gemvd-check.sh`: пошаговая проверка — пересоздать VD, проверить state, попробовать оба пакета TikTok, напечатать ответ am, сохранить кадр в `~/gemvd-check/vd.png`, режим `--swipe` для проверки касаний.
- CI `Build GemPhone Bridge APK` run 44983811818 по 6b64424 — success, артефакт `gemphone-bridge-debug` (app-debug.apk, ~2.1 MB), установлен на телефон через `adb install -r` (pm install из Termux падает: Failed transaction 2147483646).

## 2. Итоги живой сессии на телефоне (факты, не предположения)

- PUBLIC не работает: `createVirtualDisplay failed: java.lang.SecurityException: Requires ADD_MIRROR_DISPLAY permission` — у обычного приложения его нет и выдать нельзя. Рабочий обход без пересборки: флаги **66 = OWN_CONTENT_ONLY (2) + SUPPORTS_TOUCH (64)**, display_flags=12, state=ON. Дефолт моста и gemvd-check.sh надо поменять на 66.
- На флагах 66 TikTok (`com.zhiliaoapp.musically`) реально запускается на VD: `am start --display <id>` ok:true, topResumedActivity на этом displayId, скриншот VD отдаётся 200 (были кадры 149 KB — 2.4 MB). Если TikTok уже запущен на основном экране — intent уходит в существующий экземпляр (Warning: delivered to currently running top-most instance), нужен `am force-stop` перед launch.
- VD нестабилен: дисплеи 7, 8, 9, 10 по очереди умирали (status: running:false, shizuku:false, screenshot 404 `no frame yet`). Мост при этом жив (bridge_connected:true, /status ok). Причина по логам не установлена — нужен watchdog, см. раздел 3.
- Звук: TikTok сам поднимает STREAM_MUSIC с 0 до 3. Разовый `termux-volume music 0` не держится; рабочий mute: `termux-volume music 0` + `adb shell cmd audio set-stream-volume 3 0` + keyevent 164 (MUTE). Нужен постоянный контроль, см. раздел 3.
- Видение по VD ограничено: `/ui` моста отдаёт дерево только основного экрана (rootInActiveWindow), `uiautomator dump --display <id>` падает `could not get idle state` (TikTok не уходит в idle), OCR на телефоне нет. Чат с obakii во Входящих так и не был прочитан программно — это открытый долг, не закрывать как сделанное.
- Безопасность (не исправлено): `tools/gemphone.keystore` и пароль `gemphone` лежат открыто в репозитории/воркфлоу — любой может подписать APK тем же ключом. Менять ключ = переустановка моста и повторная выдача Accessibility/Shizuku.
- При stop VD приложения с него переезжают на основной экран — перед stop закрывать TikTok.

## 3. Как улучшить ФОНОВУЮ работу

1. **Watchdog VD в мосте:** heartbeat раз в 5-10 c проверяет vd.state и Shizuku binder; при running:false — автоматически create(flags=66) + relaunch целевого пакета, id нового дисплея пишется в `~/.gemphone/vd_state.json`, чтобы Termux/агент не работал со старым id.
2. **Shizuku binder-death listener:** переподключение и повторная выдача grant без участия пользователя, лог причины смерти VD (сейчас причина неизвестна — добавить Log.e с трейсом в create/stop и в onDisplayRemoved).
3. **Foreground service с постоянным уведомлением** для моста + wake-lock на время сессии агента: Android душит фоновые процессы, отсюда часть смертей VD.
4. **Volume guard:** поток/слушатель AudioManager — при любом поднятии STREAM_MUSIC > 0 во время VD-сессии сразу сброс в 0 и MUTE; опция в конфиге моста `vd_mute:true`.
5. **Состояние в одном месте:** мост сам хранит {display_id, package, flags} и умеет `action:ensure` — идемпотентно поднять всё одной командой вместо ручной цепочки status/create/launch из Termux.
6. **Отключить анимации на VD-сессии** (`window_animation_scale 0` и т.д. через shell) — меньше нагрузка, стабильнее idle для дампов.

## 4. Как улучшить СКОРОСТЬ и качество ВИДЕНИЯ в целом

1. **Не PNG, а JPEG/WebP + даунскейл:** кадр VD сейчас до 2.4 MB PNG. Отдавать JPEG q=70 и ширину 720 вместо 1080 — в 5-10 раз меньше байт и быстрее распознавание, для чтения чатов хватает.
2. **ROI-кроп:** для известных экранов (лента, Входящие, чат) резать только нужную область (нижняя навигация, список, поле ввода) прямо в мосте перед отдачей.
3. **Дифф кадров:** не слать кадр, если он не изменился (сравнение хэша/пикселей с предыдущим) — агент не жжёт время и токены на одинаковые скриншоты.
4. **On-device OCR (ML Kit Text Recognition) в мосте:** новый эндпоинт `/vdisplay/ocr` возвращает текст+координаты с кадра VD. Это решает главную боль сессии — чтение списка чатов и сообщений без uiautomator и без vision-модели по картинке, и в разы быстрее.
5. **UI-дерево именно VD:** расширить AccessibilityService — собирать окна по displayId (AccessibilityWindowInfo.getDisplayId) и отдавать `/ui?display=<id>`; fallback — `uiautomator dump --display` с флагом --compressed и предварительным pause видео (тап по центру), чтобы проходил idle.
6. **Троттлинг ImageReader:** брать только последний кадр (acquireLatestImage уже так делает), не копить очередь; кэшировать кадр 300-500 мс, чтобы повторные запросы не дёргали дисплей.
7. **phone_batch для VD:** макрос «тап -> sleep -> скриншот/ocr» одной командой, как уже сделано для основного экрана, — минус 2-3 round-trip на каждое действие.
8. **Профиль 720p для VD:** создавать дисплей 720x1560 вместо 1080x2344, когда не нужен FullHD: меньше кадр, быстрее ImageReader, меньше расход батареи в фоне.

## 5. Следующие шаги по приоритету

1. Дефолтные флаги 66 в мосте и в gemvd-check.sh + коммит.
2. `/vdisplay/ocr` на ML Kit — разблокирует чтение чатов (долг с obakii).
3. Watchdog + `action:ensure` + volume guard — стабильный фон.
4. JPEG/720p/ROI в screenshot — скорость видения.
5. Ротация ключа подписи (отдельно, с переустановкой).
