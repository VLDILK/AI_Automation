"""Локальний HTTP-сервер, що роздає статичну форму webapp/ (Telegram Mini
App) на 127.0.0.1 і обслуговує невеликий JSON API для мега-форми (шаблони/
недавні позиції). Лише стандартна бібліотека - жодної нової залежності.
Сервер живе у фоновому потоці; назовні виводиться через Cloudflare Quick
Tunnel (gui.py), не сам по собі.

Задача користувача: "коли я беру зберегти шаблон і мене викидує з операції -
жах, прибери це". Telegram.WebApp.sendData() ЗАВЖДИ закриває Mini App - це
не можна обійти на боці бота. Тому збереження/видалення шаблону більше НЕ
йде через sendData()+бот - webapp/app.js напряму викликає POST /api/template
на цьому самому сервері (той самий origin, що роздає й саму форму), і форма
лишається відкритою.

Оскільки цей сервер виводиться назовні через публічний Cloudflare-тунель
(та сама адреса, що відкриває саму форму), новий /api/template - це РЕАЛЬНА,
інтернет-досяжна точка запису в базу даних, доки бот підключений. Тому
кожен запит обов'язково перевіряється через Telegram.WebApp.initData (HMAC-
підпис, той самий алгоритм, що офіційно рекомендує Telegram для Mini Apps) -
без дійсного, свіжого initData жоден запис/видалення не проходить.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import paths
import permissions as perm
from settings import SettingsStore
from utils import measure_classification_data
from warehouse_data import ExcelSqliteStore, low_stock_report_rows, operation_template_entries

# Задача користувача: "чи є якийсь інший шлях?" (замість роздутого web_app
# URL з усіма даними форми одразу) - кнопка тепер несе лише короткий
# випадковий "квиток" (?t=...), а самі дані (усі категорії/розміри/
# залишки) лежать тут, у пам'яті процесу, до першого запиту форми чи
# спливання TTL. Токен сам і є "правом доступу" (той самий рівень довіри,
# що мав і раніше сирий URL - будь-хто з посиланням бачив ці дані) - тому
# get_context НЕ вимагає Telegram initData: ця дія лише ЧИТАЄ нечутливі
# складські дані, не веде запис, і має бути доступна ще ДО того, як
# сторінка встигла хоч раз побачити initData.
_CONTEXT_STORE = {}
_CONTEXT_STORE_LOCK = threading.Lock()
# Задача користувача (2026-08-15): "нащо міняти посилання?" - 1 година
# була занадто короткою для реальної робочої зміни (людина відкриває форму
# зранку, повертається до неї кілька разів за день - стара кнопка з чату
# ставала "непрацюючою" ще до обіду). 12 годин - повний робочий день з
# запасом, не вимагає повторного тапу на кнопку в чаті посеред дня.
# Перезапуск сервера (оновлення/крах) і далі стирає все миттєво незалежно
# від TTL - це окрема, неминуча причина (див. коментар у register_context).
_CONTEXT_TTL_SECONDS = 12 * 3600
# Реальна знахідка (аудит коду, 2026-08-16): та сама 12-годинна TTL раніше
# діяла і для "Відкрити дані в браузері" (client_app.py, webbrowser.open) -
# на відміну від Mini App кнопок (WebView Telegram, не звичайна історія
# браузера), це посилання з токеном лишається в ЗВИЧАЙНІЙ історії браузера
# на весь час дії, довше, ніж потрібно для одного погляду на дані. Лише
# цей виклик передає коротшу TTL - Mini App кнопки нижче нею не зачіпаються.
_BROWSER_VIEW_CONTEXT_TTL_SECONDS = 3600

def register_context(ctx, ttl_seconds=None):
    # Реальний ризик (аудит коду, 2026-08-14): webapp/app.js тримав власну
    # РУЧНУ копію класифікації товару (площинний/безрозмірний/мп-розмір) -
    # єдина точка, через яку йде КОЖЕН ctx будь-якої форми, тож саме тут
    # додаємо її раз - JS відтепер читає це з ctx, а не зі своєї копії.
    ctx.setdefault("measure_classification", measure_classification_data())
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _CONTEXT_STORE_LOCK:
        expired = [key for key, (_, ts, entry_ttl) in _CONTEXT_STORE.items() if now - ts > entry_ttl]
        for key in expired:
            del _CONTEXT_STORE[key]
        _CONTEXT_STORE[token] = (ctx, now, ttl_seconds if ttl_seconds is not None else _CONTEXT_TTL_SECONDS)
    return token


def _get_context(token):
    with _CONTEXT_STORE_LOCK:
        entry = _CONTEXT_STORE.get(token)
        if entry is None:
            return None
        ctx, ts, entry_ttl = entry
        if time.time() - ts > entry_ttl:
            del _CONTEXT_STORE[token]
            return None
        return ctx

# Telegram рекомендує відхиляти застарілий initData (захист від повторного
# використання перехопленого запиту) - 24 години з запасом на реальне
# використання (людина відкрила форму і не поспішала).
_INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60

_PERMISSION_BY_KIND = {
    "sale": perm.SALE_CREATE,
    "income": perm.INCOME,
    "writeoff": perm.WRITEOFF,
    "antiseptic": perm.SALE_CREATE,
}


def _validate_init_data(init_data, bot_token):
    """Перевіряє Telegram.WebApp.initData за офіційним алгоритмом Telegram
    (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)
    і повертає (telegram_id, None), або (None, причина) якщо підпис
    невірний/застарілий/відсутній - причина потрібна для діагностики
    (той самий текст, що бачить адміністратор, тимчасово - поки не
    підтверджено, що перевірка стабільно проходить на реальному Telegram)."""
    if not bot_token:
        return None, "не настроен токен бота (перезапустите программу)"
    if not init_data:
        return None, "пустой initData (форма открыта не через Telegram)"
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None, "не удалось разобрать initData"
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None, "в initData нет hash"
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None, "подпись не совпадает (неверный токен бота или изменённый initData)"
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None, "некорректный auth_date"
    if auth_date <= 0 or time.time() - auth_date > _INIT_DATA_MAX_AGE_SECONDS:
        return None, "initData устарел, откройте форму заново"
    user_raw = pairs.get("user")
    if not user_raw:
        return None, "в initData нет данных пользователя"
    try:
        user = json.loads(user_raw)
    except (TypeError, ValueError):
        return None, "не удалось разобрать данные пользователя"
    return user.get("id"), None


_ASSET_REF_PATTERN = re.compile(r'(src|href)="([^"?:]+\.(?:js|css))"')


def _inject_cache_busting(html_text, directory):
    # Задача користувача: "точно такий самий?" (2-й раз) - Cache-Control:
    # no-store сам по собі виявився недостатнім, WebView Telegram все одно
    # мовчки показував стару версію app.js/style.css. Єдиний спосіб, що
    # гарантовано працює незалежно від того, чи WebView зважає на заголовки
    # взагалі - зробити URL самого файлу іншим при кожній зміні (mtime),
    # тоді "стара" адреса просто ніколи не запитується повторно.
    def _replace(match):
        attr, filename = match.group(1), match.group(2)
        file_path = os.path.join(directory, filename)
        try:
            version = int(os.path.getmtime(file_path))
        except OSError:
            return match.group(0)
        return f'{attr}="{filename}?v={version}"'

    return _ASSET_REF_PATTERN.sub(_replace, html_text)


# Реальний ризик (аудит коду, 2026-08-14): цей сервер виведений у публічний
# інтернет через Cloudflare-тунель, а обробник запитів раніше не мав ЖОДНОГО
# тайм-ауту читання сокета й ЖОДНОГО ліміту розміру тіла запиту - повільне/
# "зависле" з'єднання (чи навмисна атака) могло тримати потік вічно, а
# необмежений Content-Length читався б одразу в пам'ять. _MAX_REQUEST_BODY -
# з великим запасом над реальним розміром (навіть багатопозиційний кошик
# приходу/продажу - десятки кБ), timeout - клас-атрибут, який http.server
# сам застосовує до сокета (BaseHTTPRequestHandler/StreamRequestHandler).
_MAX_REQUEST_BODY_BYTES = 512 * 1024


class _QuietRequestHandler(SimpleHTTPRequestHandler):
    timeout = 15

    def __init__(
        self, *args, db_path=None, get_token=None, get_fresh_context=None,
        get_remote_control_token=None, get_remote_status=None, handle_remote_command=None,
        handle_home_heartbeat=None, handle_set_role=None,
        get_form_content_enabled=None, **kwargs
    ):
        self.db_path = db_path
        self.get_token = get_token
        self.get_fresh_context = get_fresh_context
        # Задача користувача (2026-08-16): "прибери ту кнопку... і пофіксь
        # помилку" (502 Bad Gateway при "Включить форму" після "Отключить
        # форму") - раніше "вимкнути форму" зупиняло сам тунель+сервер, а
        # /control/* маршрути (якими gui.py й шле "увімкнути назад") йдуть
        # ЧЕРЕЗ ТОЙ САМИЙ тунель - вимкнена форма назавжди відрізала канал,
        # яким її можна було ввімкнути назад. Тепер тунель/сервер лишаються
        # живими, а "вимкнено" лише гасить КОНТЕНТ (усе, крім /control/*) -
        # None (сервер створений без цього callback'а, старий виклик) означає
        # "завжди увімкнено", той самий контракт "за замовчуванням дозволено",
        # що вже мають get_remote_status/handle_remote_command вище.
        self.get_form_content_enabled = get_form_content_enabled
        # Задача користувача (2026-08-15): "налаштувати керування із
        # старої програми до нової" - стара програма (gui.py, інший ПК)
        # авторизує запити секретним ключем (paths.REMOTE_CONTROL_TOKEN),
        # не Telegram-initData (стара програма - не Mini App, у неї нема
        # initData взагалі). get_remote_control_token/get_remote_status/
        # handle_remote_command - той самий callback-принцип, що вже й
        # get_token/get_fresh_context вище.
        self.get_remote_control_token = get_remote_control_token
        self.get_remote_status = get_remote_status
        self.handle_remote_command = handle_remote_command
        # Задача користувача (2026-08-15): "автоматичне з'єднання між
        # программами" - зворотний heartbeat: стара програма (gui.py) НЕ
        # має власної публічної адреси (не тунельована), тож єдиний спосіб
        # для НОВОЇ програми дізнатись "домашня чи жива" - стара сама
        # регулярно стукає СЮДИ, а не навпаки.
        self.handle_home_heartbeat = handle_home_heartbeat
        # Задача користувача (2026-08-16): "зміна ролі в мене - зміна ролі
        # в клієнті" - той самий callback-принцип, що вже й handle_remote_
        # command/handle_home_heartbeat вище (сповіщення в Telegram +
        # оновлення вікна "Персонал" - прикладна логіка client_app.py, не
        # цього HTTP-протоколу).
        self.handle_set_role = handle_set_role
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Не засмічувати статус/консоль звичайними GET-запитами форми.
        pass

    # Задача користувача: "точно такий самий?" - реальний баг живого
    # тестування: WebView кешував СТАРУ версію app.js/style.css за адресою
    # (SimpleHTTPRequestHandler за замовчуванням шле Last-Modified, але не
    # Cache-Control - деякі WebView все одно кешують евристично). Форма й
    # так завжди відкривається за НОВИМ токеном (?t=...), тож свіжість
    # самого HTML/JS/CSS нічим не захищена окремо - забороняємо кеш явно.
    def send_response(self, code, message=None):
        super().send_response(code, message)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")

    def do_GET(self):
        parsed = urlsplit(self.path)
        url_path = parsed.path
        if url_path == "/control/status":
            self._handle_remote_status(dict(parse_qsl(parsed.query)))
            return
        if url_path == "/control/personnel":
            self._handle_remote_personnel(dict(parse_qsl(parsed.query)))
            return
        if url_path == "/control/action_log":
            self._handle_remote_action_log(dict(parse_qsl(parsed.query)))
            return
        if self.get_form_content_enabled is not None and not self.get_form_content_enabled():
            self._send_form_disabled_page()
            return
        if url_path == "/":
            url_path = "/index.html"
        if url_path.endswith(".html"):
            # Реальна знахідка (аудит коду, 2026-08-16, підтверджено живим
            # запуском): os.path.join(self.directory, url_path.lstrip("/"))
            # не обмежує результат теку self.directory - "../../paths.py.html"
            # виходить за її межі через звичайні ".." (SimpleHTTPRequestHandler.
            # translate_path() САМ це прибирає, але цей блок його свідомо
            # обходить і НІКОЛИ не перевіряв межі сам), а на Windows
            # "C:/Windows/win.ini.html" ЗАМІНЮЄ self.directory повністю
            # (os.path.join трактує другий аргумент, що виглядає як
            # диск-абсолютний шлях, як новий корінь) - будь-хто з доступом до
            # публічного тунелю міг прочитати будь-який .html-файл на диску.
            # Тепер - резолвимо обидва шляхи й перевіряємо, що результат
            # реально лежить УСЕРЕДИНІ self.directory, перш ніж відкривати.
            base_dir = Path(self.directory).resolve()
            requested_path = (base_dir / url_path.lstrip("/")).resolve()
            if requested_path != base_dir and base_dir not in requested_path.parents:
                # Реальна знахідка (2026-08-16, підтверджено живим тестом):
                # HTTP-протокол вимагає ASCII/latin-1 у reason-phrase рядка
                # статусу - кириличний рядок тут кидав непійманий
                # UnicodeEncodeError глибоко всередині http.server, убиваючи
                # потік запиту БЕЗ жодної відповіді клієнту замість чистої 404.
                self.send_error(404, "Not found")
                return
            try:
                html_text = requested_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                # UnicodeDecodeError/ValueError (аудит коду, 2026-08-16): не
                # OSError - раніше непійманий виняток тут убивав потік
                # запиту з трасуванням у stderr замість чистої 404.
                # Реальна знахідка (2026-08-16, підтверджено живим тестом):
                # HTTP-протокол вимагає ASCII/latin-1 у reason-phrase рядка
                # статусу - кириличний рядок тут кидав непійманий
                # UnicodeEncodeError глибоко всередині http.server, убиваючи
                # потік запиту БЕЗ жодної відповіді клієнту замість чистої 404.
                self.send_error(404, "Not found")
                return
            body = _inject_cache_busting(html_text, self.directory).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # ?v=<mtime> - лише кеш-бастинг адреси; сам роздавач файлів (js/css)
        # шукає файл за реальним іменем, тому запит-рядок прибирається.
        self.path = url_path
        super().do_GET()

    def do_POST(self):
        parsed = urlsplit(self.path)
        if (
            not parsed.path.startswith("/control/")
            and self.get_form_content_enabled is not None
            and not self.get_form_content_enabled()
        ):
            self._send_json(503, {"ok": False, "error": "Форма временно отключена."})
            return
        if self.path not in ("/api/template", "/control/command", "/control/heartbeat", "/control/set_role"):
            self._send_json(404, {"ok": False, "error": "Не найдено."})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Некорректный запрос."})
            return
        if length > _MAX_REQUEST_BODY_BYTES:
            # Перевірка ДО читання тіла - інакше сам rfile.read(length) уже
            # спробував би виділити/прочитати весь заявлений розмір.
            # close_connection=True - непрочитані байти тіла лишились би в
            # сокеті й зіпсували б межу наступного запиту на тому самому
            # keep-alive з'єднанні.
            self.close_connection = True
            self._send_json(413, {"ok": False, "error": "Слишком большой запрос."})
            return
        try:
            raw_body = self.rfile.read(length) if length else b""
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "Некорректный запрос."})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Некорректный запрос."})
            return
        if self.path == "/control/command":
            self._handle_remote_command_request(payload)
            return
        if self.path == "/control/heartbeat":
            self._handle_home_heartbeat_request(payload)
            return
        if self.path == "/control/set_role":
            self._handle_set_role_request(payload)
            return
        self._handle_template_action(payload)

    # Задача користувача (2026-08-15): "налаштувати керування із старої
    # програми до нової" - авторизація секретним ключем (не Telegram
    # initData - стара програма не Mini App), hmac.compare_digest той
    # самий принцип, що вже й _validate_init_data вище використовує для
    # порівняння хешу.
    def _remote_control_token_valid(self, provided_token):
        expected = self.get_remote_control_token() if self.get_remote_control_token else None
        if not expected or not provided_token:
            return False
        return hmac.compare_digest(str(provided_token), str(expected))

    # Реальна знахідка (аудит коду, 2026-08-16): GET-запити раніше несли
    # токен у "?token=..." - потрапляє в службові логи Cloudflare.
    # Клієнт (remote_control_client.py) тепер завжди шле заголовок
    # X-Remote-Control-Token - але gui.py й client_app.py оновлюються
    # САМООНОВЛЕННЯМ незалежно, на різних ПК, тож query-string лишається
    # запасним варіантом на час, поки ОБИДВІ сторони ще не оновлені (не
    # постійна діра - реальний сценарій "тільки старий клієнт" саму URL з
    # токеном все одно шле лише той, хто ще НЕ оновився).
    def _remote_control_query_token(self, query):
        return self.headers.get(self._REMOTE_CONTROL_TOKEN_HEADER) or query.get("token")

    _REMOTE_CONTROL_TOKEN_HEADER = "X-Remote-Control-Token"

    def _handle_remote_status(self, query):
        if not self._remote_control_token_valid(self._remote_control_query_token(query)):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        if self.get_remote_status is None:
            self._send_json(503, {"ok": False, "error": "Статус недоступен."})
            return
        self._send_json(200, {"ok": True, "status": self.get_remote_status()})

    # Задача користувача (2026-08-15): "синхронізація" - Персонал/Журнали в
    # домашній программі (gui.py) досі читали ВЛАСНУ, окрему й порожню
    # локальну базу (gui.py більше не хостить бота, тож нічого туди й не
    # пишеться) - той самий факт, що вже пояснено для Редактора кнопок.
    # Ці два маршрути - той самий read-only принцип, що й /control/status
    # вище: домашня программа лише ТЯГНЕ й показує РЕАЛЬНІ дані клієнта,
    # нічого не пише назад (додавання/редагування персоналу і далі робиться
    # прямо в client_app.py, де це реально впливає на живого бота).
    def _handle_remote_personnel(self, query):
        if not self._remote_control_token_valid(self._remote_control_query_token(query)):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        if self.db_path is None:
            self._send_json(503, {"ok": False, "error": "База данных недоступна."})
            return
        store = ExcelSqliteStore(self.db_path)
        try:
            users = store.list_users()
        finally:
            store.close()
        self._send_json(200, {"ok": True, "users": users})

    # Задача користувача (2026-08-16): "додай змогу редагувати ролі тут
    # теж. зміна ролі в мене - зміна ролі в клієнті" - на відміну від
    # _handle_remote_personnel вище (лише читання), тут РЕАЛЬНИЙ запис у
    # БД client_app.py - той самий підхід (свіже ExcelSqliteStore-з'єднання
    # в межах запиту, а не self.store client_app.py напряму - те з'єднання
    # створене на головному Tk-потоці, чіпати його з потоку HTTP-сервера
    # небезпечно). Сповіщення користувача в Telegram + оновлення відкритого
    # вікна "Персонал" - через callback handle_set_role (той самий
    # callback-принцип, що вже й handle_remote_command/handle_home_
    # heartbeat) - той код живе в client_app.py, де вже є
    # TelegramBotWorker/_notify_role_change.
    #
    # "коли я був офлайн і клієнт змінив 3м людям по 3 рази ролі - то коли
    # я буду онлайн... мені має прийти лише остання вірна інформація" -
    # тут це виходить безкоштовно: /control/personnel завжди повертає
    # ПОТОЧНИЙ стан з БД, не журнал змін, тож проміжні зміни просто ніким
    # не запитуються, доки gui.py не потягне актуальний стан заново.
    def _handle_set_role_request(self, payload):
        if not self._remote_control_token_valid(payload.get("token")):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        user_id = payload.get("user_id")
        role = payload.get("role")
        # bool - підклас int у Python (isinstance(True, int) - True), тож
        # {"user_id": true} пройшов би перевірку нижче як user_id=1 без
        # цього виключення (нитпік з аудиту коду, 2026-08-16).
        if not isinstance(user_id, int) or isinstance(user_id, bool) or role not in perm.ROLES:
            self._send_json(400, {"ok": False, "error": "Некорректные данные."})
            return
        if self.db_path is None:
            self._send_json(503, {"ok": False, "error": "База данных недоступна."})
            return
        store = ExcelSqliteStore(self.db_path)
        try:
            row = store.get_user(user_id)
            if not row:
                self._send_json(404, {"ok": False, "error": "Пользователь не найден."})
                return
            _id, telegram_id, username, full_name, old_role, _last_seen_at = row
            store.update_user(user_id, username, full_name, role)
        finally:
            store.close()
        if self.handle_set_role is not None and role != old_role:
            try:
                self.handle_set_role(telegram_id, old_role, role)
            except Exception:
                # Сповіщення в Telegram/оновлення вікна - бонус, не критична
                # дія - сам запис ролі в БД уже успішний і не відкочується.
                pass
        self._send_json(200, {"ok": True})

    def _handle_remote_action_log(self, query):
        if not self._remote_control_token_valid(self._remote_control_query_token(query)):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        if self.db_path is None:
            self._send_json(503, {"ok": False, "error": "База данных недоступна."})
            return
        try:
            limit = int(query.get("limit", 50))
        except ValueError:
            limit = 50
        # Реальний баг (аудит коду, 2026-08-15): SQLite трактує від'ємний
        # LIMIT як "без обмежень" - без цієї межі ?limit=-1 повертав би
        # ВЕСЬ журнал дій одним запитом замість обмеженої сторінки.
        limit = max(1, min(limit, 500))
        store = ExcelSqliteStore(self.db_path)
        try:
            entries = store.list_action_log(limit)
        finally:
            store.close()
        self._send_json(200, {"ok": True, "entries": entries})

    _REMOTE_CONTROL_ACTIONS = (
        "start_bot", "stop_bot", "restart_bot", "start_form", "stop_form", "restart_form",
    )

    def _handle_remote_command_request(self, payload):
        if not self._remote_control_token_valid(payload.get("token")):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        action = payload.get("action")
        if action not in self._REMOTE_CONTROL_ACTIONS:
            self._send_json(400, {"ok": False, "error": "Неизвестное действие."})
            return
        if self.handle_remote_command is None:
            self._send_json(503, {"ok": False, "error": "Управление недоступно."})
            return
        try:
            self.handle_remote_command(action)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True})

    def _handle_home_heartbeat_request(self, payload):
        if not self._remote_control_token_valid(payload.get("token")):
            self._send_json(401, {"ok": False, "error": "Недействительный токен."})
            return
        if self.handle_home_heartbeat is not None:
            self.handle_home_heartbeat()
        self._send_json(200, {"ok": True})

    # Аудит коду (2026-08-14): цей диспетчер навмисно має 3 РІЗНІ рівні
    # довіри для різних дій - без явного пояснення тут наступна дія легко
    # копіюється з "найближчого сусіда" і випадково отримує НЕ той рівень,
    # що потрібен (саме так одного разу сталось із update_low_stock_
    # threshold - дивись критичну знахідку в карті проєкту). Три рівні:
    #   1. Токен сам є правом доступу, initData НЕ перевіряється - лише
    #      для дій, що ЧИТАЮТЬ нечутливі складські дані (get_context).
    #      Обґрунтування - коментар над register_context() вище.
    #   2. Токен + СВІЖИЙ запит ролі з бази за telegram_id, який лежить
    #      УСЕРЕДИНІ вже збереженого ctx (не з payload) - для дій, що
    #      МІНЯЮТЬ дані, але дешево перевіряються без повного initData
    #      (refresh_data_browser).
    #   3. Повна перевірка Telegram initData (HMAC-підпис) + свіжий запит
    #      ролі за ВЖЕ підтвердженим telegram_id - для БУДЬ-ЯКОЇ дії, що
    #      пише в базу даних на основі даних, надісланих клієнтом
    #      (save/delete_template/delete_recent/list). Це рівень за
    #      замовчуванням для нової дії, якщо немає чіткої причини брати
    #      рівень 1 чи 2.
    #
    # update_low_stock_threshold перенесено з рівня 3 на рівень 2 (2026-08-
    # 17, реальний баг: скріншот користувача "пустой initData" при
    # редагуванні порогу, хоча форма відкрита у СПРАВЖНЬОМУ Telegram) -
    # webapp/data.js:4-10 вже документує, що tg.initData на практиці
    # лишається порожнім навіть усередині справжнього Telegram на деяких
    # клієнтах/платформах (не таймінг-баг - спроба почекати чи перечитати
    # нічого не змінює, значення просто ніколи не приходить). Це НЕ
    # повторення старого бага (карта проєкту, критична знахідка) - тоді
    # авторизація трималась ЛИШЕ на замороженому прапорці can_edit_low_
    # stock_threshold з моменту відкриття форми, без жодної свіжої
    # перевірки. Тут роль перечитується з бази щоразу (як і в refresh_
    # data_browser), токен лишається обов'язковим і чат-прив'язаним -
    # той самий рівень довіри, що вже прийнятний для рівня 2 нижче.
    def _handle_template_action(self, payload):
        action = payload.get("action")
        if action == "get_context":
            # Свідомо БЕЗ init_data-перевірки - див. коментар над
            # register_context() вище: токен сам є правом доступу, дані не
            # чутливіші за те, що раніше й так лежало прямо в URL.
            ctx = _get_context(payload.get("token"))
            if ctx is None:
                self._send_json(404, {"ok": False, "error": "Ссылка на форму устарела. Откройте её заново из чата."})
                return
            self._send_json(200, {"ok": True, "ctx": ctx})
            return
        if action == "refresh_data_browser":
            # Задача користувача (2026-08-14): кнопка "Обновить" поруч із
            # заголовком - тягне ЖИВІ дані з бази (не той статичний знімок,
            # що get_context вище повертає з register_context/_CONTEXT_STORE
            # - той не оновлюється без нового виклику кнопки в Telegram).
            # webapp/data.js застосовує з відповіді лише самі рядки/
            # категорії/поріг - НІЯКІ фільтри/вкладка/сортування на клієнті
            # не чіпаються. Той самий токен-замість-initData фікс, що вже
            # має get_context вище й update_low_stock_threshold нижче.
            ctx = _get_context(payload.get("token"))
            telegram_id = ctx.get("telegram_id") if ctx else None
            if telegram_id is None:
                self._send_json(404, {"ok": False, "error": "Ссылка на форму устарела. Откройте её заново из чата."})
                return
            if self.db_path is None or self.get_fresh_context is None:
                self._send_json(503, {"ok": False, "error": "Бот сейчас не запущен - обновление недоступно."})
                return
            store = ExcelSqliteStore(self.db_path)
            try:
                role = perm.normalize_role(store.get_user_role(telegram_id))
                fresh_ctx = self.get_fresh_context(store, role == perm.ADMIN)
            finally:
                store.close()
            if fresh_ctx is None:
                self._send_json(503, {"ok": False, "error": "Бот сейчас не запущен - обновление недоступно."})
                return
            self._send_json(200, {"ok": True, "ctx": fresh_ctx})
            return
        if action == "update_low_stock_threshold":
            # Рівень 2 (токен + свіжа роль з бази за telegram_id усередині
            # ctx), не рівень 3 - див. пояснення над _handle_template_action
            # (2026-08-17: tg.initData на практиці порожній навіть у
            # справжньому Telegram на деяких клієнтах, webapp/data.js:4-10).
            # Це НЕ старий баг "лише заморожений прапорець без перевірки" -
            # роль перечитується з бази щоразу, як і в refresh_data_browser.
            ctx = _get_context(payload.get("token"))
            telegram_id = ctx.get("telegram_id") if ctx else None
            if telegram_id is None:
                self._send_json(404, {"ok": False, "error": "Ссылка на форму устарела. Откройте её заново из чата."})
                return
            if self.db_path is None:
                self._send_json(500, {"ok": False, "error": "База данных недоступна."})
                return
            store = ExcelSqliteStore(self.db_path)
            try:
                role = perm.normalize_role(store.get_user_role(telegram_id))
                # Задача користувача: поріг "Низкий остаток" міняти може
                # ТІЛЬКИ адміністратор - пряма перевірка ролі (не
                # _PERMISSION_BY_KIND/has_permission - тут нема "kind"
                # операції приходу/продажу/списання, лише один окремий
                # системний параметр).
                if role != perm.ADMIN:
                    self._send_json(403, {"ok": False, "error": "Нет доступа к этому действию."})
                    return
                raw_threshold = payload.get("threshold")
                try:
                    threshold = int(raw_threshold)
                except (TypeError, ValueError):
                    threshold = -1
                if threshold < 0:
                    self._send_json(400, {"ok": False, "error": "Введите неотрицательное целое число."})
                    return
                settings_store = SettingsStore(paths.SETTINGS_PATH)
                settings_store.set("low_stock_threshold", threshold)
                # Задача користувача: "відразу застосовувати зміну" - рядки
                # зареєстрованого токена лишаються заморожені на моменті
                # відкриття форми (register_context), тож самого нового
                # порогу недостатньо - клієнт має отримати ЗАНОВО порахований
                # список тут-таки, без повторного відкриття форми з чату.
                rows = low_stock_report_rows(store, threshold)
                self._send_json(200, {"ok": True, "threshold": threshold, "rows": rows})
            finally:
                store.close()
            return
        if action not in ("save", "delete_template", "delete_recent", "list"):
            self._send_json(400, {"ok": False, "error": "Неизвестное действие."})
            return
        token = self.get_token() if self.get_token else None
        telegram_id, reason = _validate_init_data(payload.get("init_data"), token)
        if telegram_id is None:
            self._send_json(403, {"ok": False, "error": reason or "Не удалось подтвердить пользователя Telegram."})
            return
        if self.db_path is None:
            self._send_json(500, {"ok": False, "error": "База данных недоступна."})
            return
        store = ExcelSqliteStore(self.db_path)
        try:
            role = perm.normalize_role(store.get_user_role(telegram_id))

            if action == "save":
                kind = payload.get("kind")
                required_permission = _PERMISSION_BY_KIND.get(kind)
                operation_id = payload.get("category_operation_id")
                operation = store.get_operation(operation_id) if operation_id is not None else None
                # antiseptic (окрема форма антисептирования) переиспользує
                # РЕАЛЬНІ sale-категорії (Доска AD/KD) - шаблон лише позначає
                # їх ІНШИМ "кошиком" (kind="antiseptic"), сама категорія в
                # bot_operations так і лишається kind="sale". Тому тут окремо
                # приймаємо operation[2]=="sale" за kind=="antiseptic".
                operation_kind_matches = operation is not None and (
                    operation[2] == kind or (kind == "antiseptic" and operation[2] == "sale")
                )
                if not operation_kind_matches or required_permission is None:
                    # Реальна категорія (bot_operations.kind) вирішує право
                    # доступу, а не те, що клієнт заявив у payload - інакше
                    # хтось із лише income-правом міг би позначити payload
                    # як kind="income" і зберегти шаблон для sale-категорії.
                    self._send_json(400, {"ok": False, "error": "Не выбрана категория."})
                    return
                if not perm.has_permission(role, required_permission):
                    self._send_json(403, {"ok": False, "error": "Нет доступа к этому действию."})
                    return
                store.add_operation_template(
                    kind, operation_id,
                    breed=payload.get("breed"), thickness=payload.get("thickness"),
                    width=payload.get("width"), length=payload.get("length"),
                    client=payload.get("client"), address=payload.get("address"),
                    payment_method=payload.get("payment_method"),
                )
                kind_for_response = kind
            elif action == "list":
                # Задача користувача (шаблони): панель раніше вбудовувалась
                # у сам web_app-URL - реальний баг, який зламав УСІ
                # sale/income/writeoff-відповіді (URL переріс ліміт розміру
                # Telegram reply_markup, той самий клас бага, що й Крок
                # "мега-форма занадто довга кнопка"). Тепер webapp/app.js
                # підвантажує шаблони окремим запитом ПІСЛЯ відкриття форми,
                # а не отримує їх одразу в тілі кнопки.
                kind = payload.get("kind")
                required_permission = _PERMISSION_BY_KIND.get(kind)
                if required_permission is None:
                    self._send_json(400, {"ok": False, "error": "Неизвестный тип операции."})
                    return
                if not perm.has_permission(role, required_permission):
                    self._send_json(403, {"ok": False, "error": "Нет доступа к этому действию."})
                    return
                kind_for_response = kind
            elif action == "delete_template":
                template_id = payload.get("template_id")
                row = store.get_operation_template(template_id) if template_id is not None else None
                if row is not None:
                    _row_id, row_kind, _category_operation_id = row
                    required_permission = _PERMISSION_BY_KIND.get(row_kind)
                    if required_permission is not None and not perm.has_permission(role, required_permission):
                        self._send_json(403, {"ok": False, "error": "Нет доступа к этому действию."})
                        return
                    store.delete_operation_template(template_id)
                kind_for_response = row[1] if row is not None else payload.get("kind")
            else:
                recent_id = payload.get("recent_id")
                row = store.get_operation_recent_use(recent_id) if recent_id is not None else None
                if row is not None:
                    _row_id, row_kind, _category_operation_id = row
                    required_permission = _PERMISSION_BY_KIND.get(row_kind)
                    if required_permission is not None and not perm.has_permission(role, required_permission):
                        self._send_json(403, {"ok": False, "error": "Нет доступа к этому действию."})
                        return
                    store.delete_operation_recent_use(recent_id)
                kind_for_response = row[1] if row is not None else payload.get("kind")

            if kind_for_response not in _PERMISSION_BY_KIND:
                self._send_json(400, {"ok": False, "error": "Неизвестный тип операции."})
                return
            templates = operation_template_entries(store, store.list_operation_templates(kind_for_response), "template")
            recent = operation_template_entries(store, store.recent_operation_uses(kind_for_response), "recent")
            self._send_json(200, {"ok": True, "templates": templates, "recent": recent})
        finally:
            store.close()

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_form_disabled_page(self):
        body = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>Форма отключена</title></head>"
            "<body style=\"font-family:sans-serif;text-align:center;padding-top:60px;\">"
            "<p>Форма временно отключена.</p></body></html>"
        ).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebappServer:
    # get_fresh_context: Задача користувача (2026-08-14) - кнопка "Обновить"
    # у "Данные (форма)" має тягнути ЖИВІ дані з бази, а не той статичний
    # знімок, що register_context() зберігає в пам'яті лише один раз, у
    # момент створення кнопки в Telegram. Callable(store, is_admin) -> dict
    # | None - той самий _webapp_data_browser_context, що вже будує кнопку,
    # просто викликаний повторно на живому TelegramBotWorker (None, якщо
    # бот зараз не запущений).
    def __init__(
        self, port=None, directory=None, db_path=None, get_token=None, get_fresh_context=None,
        get_remote_control_token=None, get_remote_status=None, handle_remote_command=None,
        handle_home_heartbeat=None, handle_set_role=None,
        get_form_content_enabled=None,
    ):
        self.port = port or paths.WEBAPP_LOCAL_PORT
        self.directory = str(directory or paths.WEBAPP_DIR)
        self.db_path = db_path
        self.get_token = get_token
        self.get_fresh_context = get_fresh_context
        self.get_remote_control_token = get_remote_control_token
        self.get_remote_status = get_remote_status
        self.handle_remote_command = handle_remote_command
        self.handle_home_heartbeat = handle_home_heartbeat
        self.handle_set_role = handle_set_role
        self.get_form_content_enabled = get_form_content_enabled
        self._httpd = None
        self._thread = None

    def start(self):
        if self.is_alive():
            return
        handler = partial(
            _QuietRequestHandler, directory=self.directory, db_path=self.db_path, get_token=self.get_token,
            get_fresh_context=self.get_fresh_context, get_remote_control_token=self.get_remote_control_token,
            get_remote_status=self.get_remote_status, handle_remote_command=self.handle_remote_command,
            handle_home_heartbeat=self.handle_home_heartbeat,
            handle_set_role=self.handle_set_role, get_form_content_enabled=self.get_form_content_enabled,
        )
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()
