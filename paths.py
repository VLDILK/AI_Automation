"""Шляхи до файлів/папок проєкту. Без залежностей від інших модулів проєкту —
тільки stdlib. Усе, що потребує шляху до файлу (settings.py, warehouse_data.py,
main.py), імпортує потрібну константу звідси, а не тримає свою копію.
"""

import getpass
import hashlib
import json
import re
import sys
from pathlib import Path

# Пакування в .exe (PyInstaller, --onedir): всередині зібраного застосунку
# Path(__file__) більше не вказує на теку поруч із самим .exe (а на
# внутрішню/тимчасову теку розпакування) - sys.executable у зібраній версії
# завжди вказує на реальний .exe, тож саме його батьківська тека і є
# правильним BASE_DIR. У dev-режимі (python main.py/client_app.py) все
# лишається як було.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
FILE_PATH = BASE_DIR / "test_sklad.xlsx"
DB_PATH = BASE_DIR / "app_data.sqlite3"
BACKUP_DIR = BASE_DIR / "backups"
# Автоматичні щоденні знімки app_data.sqlite3 (не Excel-мірору) — окрема
# піддиректорія, щоб не змішуватись із наявними у db_backups/ ручними
# файлами (зроблені один раз вручну перед міграцією схеми).
DB_BACKUP_DIR = BASE_DIR / "db_backups" / "auto"
# Задача користувача (2026-08-14): "потрібен ще бекап версій програм" -
# автоматичні знімки ВИХІДНОГО КОДУ (code_backup.py), окремо від db_backups/
# вище (той захищає ДАНІ складу) і окремо від наявного code_backups/ у
# корені проєкту (той - ручні знімки МИНУЛИХ сесій "перед ризикованою
# правкою", не автоматичні) - той самий принцип "auto/" піддиректорії.
CODE_BACKUP_DIR = BASE_DIR / "code_backups" / "auto"
# Задача користувача (2026-08-16): "бекап і на хмару" для settings.json і
# ключа Cloudflare-тунелю - раніше жоден з бекапів (Excel/БД/код) не
# торкався цих двох файлів взагалі. Той самий принцип "auto/" піддиректорії.
CONFIG_BACKUP_DIR = BASE_DIR / "config_backups" / "auto"
SYSTEM_DIR = BASE_DIR / "system"
SETTINGS_PATH = SYSTEM_DIR / "settings.json"
# Реальний ризик (аудит коду, 2026-08-14): пароль резервних копій раніше
# зберігався ПРЯМО в settings.json - тому, хто отримав доступ до цього
# файлу, ставали доступні й самі зашифровані знімки поруч (backup_password
# захищав лише "хтось скопіював ЛИШЕ папку бекапів"). Той самий принцип, що
# вже й telegram_token_file (шлях до ОКРЕМОГО файлу-секрету, не сам секрет
# у settings.json) - окремий файл, одноразова міграція значення зі старого
# ключа settings.json (warehouse_data.py:_backup_encryption_password) не
# ламає вже зроблені зашифровані знімки.
BACKUP_PASSWORD_PATH = SYSTEM_DIR / "backup_password.txt"
# Реальна знахідка (аудит коду, 2026-08-16): той самий ризик, що вже мав
# backup_password вище - GitHub Personal Access Token (право ЗАПИСУ в
# репозиторій, звідки обидві программи качають "довірені" оновлення)
# зберігався ПРЯМО в settings.json, а config_backup.py регулярно копіює
# сам settings.json у хмару (OneDrive) - токен автоматично поїхав би
# у хмару разом із рештою налаштувань. Той самий принцип - окремий файл,
# НЕ в списку _INCLUDE_FILES config_backup.py (як і backup_password.txt).
GITHUB_TOKEN_PATH = SYSTEM_DIR / "github_token.txt"
# Токен Cloudflare для вікна "Тунель" (gui.py) - той самий принцип, що й
# у GITHUB_TOKEN_PATH вище: окремий файл у SYSTEM_DIR, який уже в
# .gitignore, а не константа в коді. Потрібні права мінімальні:
# Zone:DNS:Edit на робочу зону та Account:Cloudflare Tunnel:Read.
CLOUDFLARE_TOKEN_PATH = SYSTEM_DIR / "cloudflare_token.txt"
# Задача користувача (2026-08-17): "якщо програма закриється - то щоб
# запустилась знову... якщо так можна" - watchdog_task.py's періодична
# перевірка (client_app.py --watchdog-check) відрізняє свідоме "Выход"
# від краху саме за наявністю цього файлу: ClientApp._on_exit_clicked
# створює його прямо перед закриттям, перевірка його споживає (видаляє) й
# НЕ перезапускає застосунок; відсутність файлу при "процес не знайдено" -
# ознака непланового завершення, перевірка запускає застосунок знову.
GRACEFUL_EXIT_MARKER_PATH = SYSTEM_DIR / "graceful_exit.flag"
# Задача користувача (2026-08-18): "розкладки кнопок більше не можуть
# братися нізвідки окрім як з хмари. а точніше копіювання з хмари в
# локальний файл щоб лагів не було з кнопками постійними" - реальна
# локальна копія standard_menu_cloud.py's хмарного (OneDrive) файлу,
# ОКРЕМА від самого OneDrive-файлу і від SQLite - client_app.py оновлює
# її при кожному старті (_reconcile_standard_menu_with_cloud), звичайний
# швидкий локальний файл, без жодної залежності від синхронізації хмари
# на гарячому шляху показу меню бота.
STANDARD_MENU_CACHE_PATH = SYSTEM_DIR / "standard_menu_cache.json"
# Кеш токена MSAL (OneDrive/SharePoint онлайн-джерело таблиці Excel,
# onedrive_sync.py) — містить refresh-токен, той самий рівень довіри, що й
# telegram_token_file: звичайний локальний файл, без додаткового шифрування.
MSAL_TOKEN_CACHE_PATH = SYSTEM_DIR / "msal_token_cache.json"
# Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
# з'єднання між программами" - раніше секретний ключ генерувався одноразово
# в client_app.py й передавався в gui.py через спільну теку (ручний вибір
# теки, і то лише перший крок пересилання самого ключа). Тепер, коли адреса
# теж фіксована (CLOUDFLARED_TUNNEL_HOSTNAME нижче), ключ теж стає простою
# константою, "зашитою" в обидві програми одразу - жодного файлу/теки/
# ручного кроку більше не треба, з'єднання встановлюється само собою.
REMOTE_CONTROL_TOKEN = "4qvWKA_7s4c8YxSFD0UkKLWWGG-x3aHPglqFOyLIHio"
# Задача користувача (2026-08-17): "повідомлення з результатом виконаної
# операції, має прийти ще й у інший чат" - фіксований chat_id групи,
# знайдений через власну команду бота /chatid (telegram_dialog_core.py).
# Успішне завершення приходу/продажу/списання/антисептирования дублює
# туди той самий текст звіту, з іменем співробітника попереду.
REPORT_BROADCAST_CHAT_ID = -1004477565779
# Аудит коду: getUpdates-offset жив лише в пам'яті процесу (main.py) — при
# перезапуску (крах чи сам watchdog) новий воркер стартував з offset=None,
# і Telegram міг повторно доставити повідомлення, яке вже було оброблене
# (реальний ризик підвищився саме через нововведений watchdog — він тепер
# частіше перезапускає бота сам). Тепер зберігається на диск синхронно з
# кожним зрушенням offset.
TELEGRAM_OFFSET_PATH = SYSTEM_DIR / "telegram_offset.json"
REPORTS_DIR = BASE_DIR / "reports"
PDF_REPORT_SCRIPT = SYSTEM_DIR / "pdf_stock_report.py"

# Форма введення даних (Telegram Mini App) замість чек-листа вільного тексту —
# webapp/ роздається локально (webapp_server.py), назовні виведено через
# Cloudflare Tunnel. cloudflared.exe постачається поруч із проєктом — той
# самий принцип, що й BUNDLED_PYTHON_EXE вище (нуль дій з боку людини, що
# запускає програму).
WEBAPP_DIR = BASE_DIR / "webapp"
CLOUDFLARED_EXE = BASE_DIR / "cloudflared.exe"
WEBAPP_LOCAL_PORT = 8765

# Задача користувача (2026-08-15): "яку папку вибрати одну у різних людей?
# так не піде" - Quick Tunnel (--url, без акаунту) видавав НОВУ випадкову
# адресу щоразу при перезапуску, тому й знадобився спільний статус-файл
# лише для того, щоб стара програма (gui.py) знала, яка адреса АКТУАЛЬНА
# зараз. Іменований (persistent) Tunnel прив'язаний до фіксованого
# піддомену - адреса більше НІКОЛИ не змінюється, тож усе це відпадає.
# Реєстрація: cloudflared tunnel login -> tunnel create -> tunnel route dns
# (зроблено 2026-08-15, домен botaiautomationeu.trade). Файл облікових
# даних тунелю - секрет того самого рівня, що й REMOTE_CONTROL_TOKEN_PATH
# вище, тому й лежить під SYSTEM_DIR, а не в .cloudflared користувача
# (там він прив'язаний до ОДНІЄЇ машини - тут має подорожувати разом із
# зібраною програмою на будь-який ПК, де вона реально запускається).
CLOUDFLARED_TUNNEL_ID = "85a0ec48-8db0-4e29-965c-232d838d9ea7"
CLOUDFLARED_TUNNEL_HOSTNAME = "bot.botaiautomationeu.trade"
CLOUDFLARED_TUNNEL_CREDENTIALS_PATH = SYSTEM_DIR / "cloudflared_tunnel_credentials.json"


# Задача користувача (2026-08-19): "дім слухає то одну програму то іншу" -
# кілька реальних серверів НЕ можуть ділити один тунель (Cloudflare веде
# ім'я лише до ОДНОГО активного з'єднання, хто останній підключився - той
# і відповідає). Кожен окремий сервер тепер отримує ВЛАСНИЙ named tunnel
# (свій credentials-файл у system/, своя адреса) - CLOUDFLARED_TUNNEL_ID
# вище лишається лише запасним значенням ДЛЯ ЦІЄЇ (розробницької) машини;
# реальний ID тунеля читається напряму з credentials-файлу, що фізично
# лежить на кожній конкретній машині - той самий файл, що cloudflared й
# так вимагає для --credentials-file, тож нового поля вводу для ID не треба.
#
# Задача користувача (2026-08-20): "зроби цю заміну оновленням новим" -
# вшитий у збірку credentials.json НЕ можна замінити тестовим секретом
# (публічний репозиторій, той самий клас витоку, що вже стався з
# REMOTE_CONTROL_TOKEN) - тому опційний ЗОВНІШНІЙ файл (шлях у
# settings.json, той самий принцип, що вже й "Адрес тунеля этого
# сервера") лишається єдиним безпечним шляхом підмінити секрет.
def read_cloudflared_tunnel_id(credentials_path=None):
    path = credentials_path or CLOUDFLARED_TUNNEL_CREDENTIALS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tunnel_id = data.get("TunnelID")
        if tunnel_id:
            return tunnel_id
    except (OSError, ValueError):
        pass
    return CLOUDFLARED_TUNNEL_ID

# Задача користувача (2026-08-16): "щоб не в момент увімкненого серверу це
# було... клієнт вимкнений, вранці увімкнув - отримав оновлення" - push через
# тунель (remote_control_client.push_client_update) вимагає обидві сторони
# онлайн одночасно, не підходить. Публічний GitHub-репозиторій, призначений
# ЛИШЕ для хостингу файлів оновлень (не вихідного коду - жодних секретів
# туди не потрапляє) - публічна інформація, безпечно хардкодити тут, той
# самий рівень, що й CLOUDFLARED_TUNNEL_HOSTNAME вище.
GITHUB_RELEASES_OWNER = "VLDILK"
GITHUB_RELEASES_REPO = "AI_Automation"

# Python для генерації PDF через subprocess (system/pdf_stock_report.py).
# Аудит коду: раніше тут був жорсткий шлях у приватну теку одного конкретного
# комп'ютера розробника — на будь-якій іншій машині (зокрема реальному
# робочому ПК) PDF-експорт був фізично неможливий. Тепер: спершу власне
# .venv проєкту (де вже встановлено reportlab — стоїть поруч із кодом,
# переїжджає разом із проєктом), інакше — той самий Python, що зараз
# виконує саму програму (sys.executable, завжди валідний шлях).
def _resolve_pdf_python_exe():
    venv_python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


BUNDLED_PYTHON_EXE = _resolve_pdf_python_exe()

# Пакування в .exe: у зібраній версії немає ні .venv, ні sys.executable, що
# вказував би на реальний Python (sys.executable там - сам застосунок). PDF
# генерується окремим маленьким .exe (system/pdf_stock_report/), зібраним
# окремо і скопійованим під SYSTEM_DIR при пакуванні (build_exe.py) - той
# самий принцип ізоляції підпроцесу, що вже був із BUNDLED_PYTHON_EXE.
PDF_HELPER_EXE = SYSTEM_DIR / "pdf_stock_report" / "pdf_stock_report.exe"

# Персональний файл налаштувань показу — окремий на кожного користувача Windows,
# щоб кілька людей на одному ПК не ділили формат дати між собою.
# Свіжий пере-аудит (2026-08-02, Minor #7): "просто санітизований логін"
# (без суфікса) дозволяв колізію — будь-який символ-роздільник (крапка/
# пробіл/інше) згортався в один "_", тож напр. "Ivan.Petrenko" і
# "Ivan Petrenko" (крапка проти пробілу — цілком реалістична пара, напр.
# Firstname.Lastname-домен-акаунт проти локального Windows-профілю з
# пробілом) обидва санітизувались у те саме "Ivan_Petrenko" — дві різні
# людини мовчки ділили б один файл персональних налаштувань. Хеш-суфікс
# рахується від СИРОГО (несанітизованого) логіна — інакше хешування вже
# зіткнутого рядка відтворило б ту саму колізію.
def _sanitize_username(raw_username):
    return re.sub(r"[^0-9A-Za-zА-Яа-яІЇЄҐієїґ_-]+", "_", raw_username).strip("_") or "default"


def _compute_display_user_key(raw_username):
    username_hash = hashlib.md5(raw_username.encode("utf-8")).hexdigest()[:8]
    return f"{_sanitize_username(raw_username)}_{username_hash}"


try:
    _raw_username = getpass.getuser()
except Exception:
    # getpass.getuser() читає змінні середовища/системний виклик — у
    # нетиповому (неінтерактивному/сервісному) середовищі виконання міг би
    # кинути виняток ще на рівні імпорту цього модуля, зупиняючи запуск
    # програми ще до першого рядка коду. Малоймовірно для цього десктопного
    # застосунку, але дешево захистити зараз, раз уже редагуємо ці рядки.
    _raw_username = "default"
DISPLAY_USER_KEY = _compute_display_user_key(_raw_username)
DISPLAY_SETTINGS_PATH = SYSTEM_DIR / f"display_settings_{DISPLAY_USER_KEY}.json"

# Одноразова міграція: зміна формули ключа мовчки "осиротила" б уже наявний
# display_settings_<старий_ключ>.json для будь-кого, хто вже користувався
# програмою (персональні налаштування вигляду виглядали б скинутими на
# дефолт) — якщо старий файл існує, а новий ще ні, переносимо замість
# створення з нуля.
_old_display_settings_path = SYSTEM_DIR / f"display_settings_{_sanitize_username(_raw_username)}.json"
if _old_display_settings_path.exists() and not DISPLAY_SETTINGS_PATH.exists():
    try:
        _old_display_settings_path.replace(DISPLAY_SETTINGS_PATH)
    except OSError:
        pass
