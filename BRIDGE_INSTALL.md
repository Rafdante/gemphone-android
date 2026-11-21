# Установка GemPhone 3.8.1 + GemPhone Bridge (без ADB и Wi-Fi)

## 1. Обновление GemPhone в Termux

Распакуй архив в `~/gemphone-android` (или залей файлы в свой GitHub-репозиторий и запусти `install.sh`):

```bash
unzip -o ~/storage/downloads/gemphone-android-v3.8.1-bridge.zip -d ~
cd ~/gemphone-android
chmod +x src/gemphone.py src/gemcode.py install.sh
pip install -q httpx prompt_toolkit Pillow rich
ln -sf ~/gemphone-android/src/gemphone.py "$PREFIX/bin/gemphone"
```

Проверка: запусти `gemphone` — в шапке должно быть `✻ GemPhone 3.8.1`.

## 2. Сборка APK-компаньона `GemPhone Bridge`

### Вариант А — прямо на GitHub (без ПК и Android Studio)
1. Залей папки `bridge/` и `.github/workflows/build-bridge-apk.yml` в свой репозиторий `Rafdante/gemphone-android`.
2. Открой на GitHub вкладку **Actions -> Build GemPhone Bridge APK -> Run workflow** (или дождись автозапуска после push).
3. Через ~1–2 минуты скачай артефакт **`gemphone-bridge-debug-apk`**, распакуй и установи `app-debug.apk` на телефон.

### Вариант Б — локально на ПК (Android Studio / Gradle)
1. Открой папку `bridge/` в Android Studio (или выполни `cd bridge && gradle assembleDebug` с JDK 17 + Android SDK 34).
2. Возьми готовый файл `bridge/app/build/outputs/apk/debug/app-debug.apk` и установи на телефон.

## 3. Первичная настройка на смартфоне (1 раз)

1. Открой приложение **GemPhone Bridge**.
2. **Если Android 13 / 14 / 15** (при включении спецвозможностей пишет *«Настройка ограничена»*):
   - Нажми кнопку **«0. Android 13+: Разрешить огр. настройки (⋮)»** -> в правом верхнем углу нажми **`⋮`** -> **«Разрешить ограниченные настройки»** (подтверди отпечатком/ПИНом).
3. Нажми **«1. Включить в Спец. возможностях»** -> найди **GemPhone Bridge** -> включи переключатель.
4. Вернись в приложение — статус станет зелёным: **`● Сервис активен (127.0.0.1:7331)`**.
5. Нажми **«2. Скопировать команду для GemPhone»**.
6. В Termux открой `gemphone` и просто вставь скопированную строку:
   ```text
   /bridge token <ваш_токен>
   ```
7. Проверь работу командой `/screen` или `/status` — транспорт покажет `Bridge:7331`.

## Полезные команды в GemPhone 3.8.1
- `/bridge` — статус транспорта (`Bridge` или `ADB`)
- `/bridge auto | bridge | adb` — переключить приоритет транспорта
- `/steps 30` — изменить лимит шагов агента прямо в чате
- `/status` — полная диагностика всех модулей
