"""Клієнтська частина віддаленого керування (gui.py, "стара" програма) -
говорить напряму з новою програмою (client_app.py, реальний хост Telegram-
бота/webapp-форми, на іншому ПК) через невеликий HTTP API (webapp_server.py,
/control/status, /control/command, /control/heartbeat).

Задача користувача (2026-08-15): "тепер змінюй це на автоматичне з'єднання
між программами" - раніше тут був файловий протокол через спільну мережеву/
OneDrive теку (людина мала вручну обрати теку і скопіювати ключ). Тепер, з
іменованим (persistent) Cloudflare Tunnel (paths.CLOUDFLARED_TUNNEL_HOSTNAME
- адреса ФІКСОВАНА, більше не змінюється між перезапусками) і фіксованим
спільним ключем (paths.REMOTE_CONTROL_TOKEN, "зашитим" в обидві програми) -
жодного дискавері більше не треба: gui.py просто стукає за відомою наперед
адресою напряму. Жодного UI/налаштувань більше не потрібно з боку людини.
"""

import json
import urllib.error
import urllib.request

import paths

_BASE_URL = f"https://{paths.CLOUDFLARED_TUNNEL_HOSTNAME}"
# Cloudflare free-tier бот-захист блокує "generic" User-Agent (403) -
# реальний браузер отримує 200, тож тут теж явно видаємо себе за нього.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# Реальна знахідка (аудит коду, 2026-08-16): токен у "?token=..." адреси
# GET-запиту потрапляє в службові логи Cloudflare (навіть попри шифрування
# самого з'єднання) - на відміну від тіла POST чи заголовка, який туди не
# логується. Переносимо в заголовок для всіх трьох GET-маршрутів нижче.
_TOKEN_HEADER = "X-Remote-Control-Token"


def fetch_remote_status(timeout=10):
    """None означає "статус невідомий" (мережевий збій, сервер ще не
    піднявся, тунель ще не з'єднався тощо) - не "сервер офлайн" (той стан
    визначається за свіжістю updated_at всередині статусу, коли він
    успішно отриманий)."""
    request = urllib.request.Request(
        f"{_BASE_URL}/control/status",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    status = data.get("status")
    return status if isinstance(status, dict) else None


def fetch_remote_personnel(timeout=10):
    """None означає "не вдалось отримати" (мережевий збій, сервер офлайн) -
    той самий контракт, що й fetch_remote_status: викликач (gui.py) сам
    вирішує, як показати відсутність даних, тут жодного UI."""
    request = urllib.request.Request(
        f"{_BASE_URL}/control/personnel",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    users = data.get("users")
    return users if isinstance(users, list) else None


def fetch_remote_action_log(limit=50, timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/action_log?limit={limit}",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    entries = data.get("entries")
    return entries if isinstance(entries, list) else None


def send_home_heartbeat(timeout=10):
    """Найкраще зусилля, без винятку назовні - невдача тут лише означає,
    що зворотний датчик client_app.py "домашня программа" застаріє, не
    критична помилка, яку gui.py має показувати користувачу."""
    request = urllib.request.Request(
        f"{_BASE_URL}/control/heartbeat",
        data=json.dumps({"token": paths.REMOTE_CONTROL_TOKEN}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except (urllib.error.URLError, OSError):
        pass



# Задача користувача (2026-08-16): "додай змогу редагувати ролі тут
# теж... зміна ролі в мене - зміна ролі в клієнті" - user_id (не
# telegram_id) навмисно: той самий id, що вже приходить у кожному рядку
# fetch_remote_personnel(), напряму підходить під ExcelSqliteStore.
# update_user(user_id, ...) на боці client_app.py - жодного додаткового
# пошуку не треба. Той самий exception-контракт, що й send_remote_command.
# Задача користувача (2026-08-17): "редактор кнопок зроби синхронним" -
# той самий None-контракт, що вже й fetch_remote_personnel: "не вдалось
# отримати" (мережа/сервер офлайн), не "кнопок немає" - викликач (gui.py)
# сам вирішує, як показати відсутність зв'язку.
# Задача користувача (2026-08-18): "поправ там шлях" - "Открыть папку" в
# gui.py досі відкривала СВІЙ ЛОКАЛЬНИЙ OneDrive (реальний випадок,
# знайдений тут-таки: домашня программа й client_app.py виявились на
# ДВОХ різних машинах/акаунтах - "Vladimir2\OneDrive - Diverus, UAB", не
# особистий акаунт gui.py). Той самий None-контракт, що й fetch_remote_status.
def fetch_remote_standard_menu_cloud_path(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/standard_menu_cloud_path",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    cloud_path = data.get("cloud_path")
    return cloud_path if isinstance(cloud_path, str) else None


def fetch_remote_custom_buttons(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/custom_buttons",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    buttons = data.get("buttons")
    return buttons if isinstance(buttons, list) else None


# Той самий exception-контракт, що вже й set_remote_role/send_remote_command
# нижче - HTTPError/URLError/OSError пролітають до викликача як є, тут
# жодного UI. Спільна для трьох дій (add/update/delete) - самі публічні
# функції нижче лише формують payload під конкретну дію.
def _post_custom_button_action(payload, timeout=10):
    body = dict(payload)
    body["token"] = paths.REMOTE_CONTROL_TOKEN
    request = urllib.request.Request(
        f"{_BASE_URL}/control/custom_button_action",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def add_remote_custom_button(
    label, message_text, action_code, parent_id=None, layout="full", operation_id=None,
    position_index=None, timeout=10,
):
    return _post_custom_button_action(
        {
            "op": "add", "label": label, "message_text": message_text, "action_code": action_code,
            "parent_id": parent_id, "layout": layout, "operation_id": operation_id,
            "position_index": position_index,
        },
        timeout=timeout,
    )


def update_remote_custom_button(
    node_id, label, message_text, action_code, layout="full", operation_id=None,
    position_index=None, timeout=10,
):
    return _post_custom_button_action(
        {
            "op": "update", "node_id": node_id, "label": label, "message_text": message_text,
            "action_code": action_code, "layout": layout, "operation_id": operation_id,
            "position_index": position_index,
        },
        timeout=timeout,
    )


def delete_remote_custom_button(node_id, timeout=10):
    return _post_custom_button_action({"op": "delete", "node_id": node_id}, timeout=timeout)


# Задача користувача (2026-08-18, аудит "у всього є істина?"): "Способи
# оплати" в gui.py досі писали у ВЛАСНУ локальну self.store - той самий
# None/exception-контракт, що й custom-button-функції вище.
def fetch_remote_payment_methods(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/payment_methods",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    options = data.get("options")
    return options if isinstance(options, list) else None


def _post_payment_method_action(payload, timeout=10):
    body = dict(payload)
    body["token"] = paths.REMOTE_CONTROL_TOKEN
    request = urllib.request.Request(
        f"{_BASE_URL}/control/payment_method_action",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def add_remote_payment_method(label, timeout=10):
    return _post_payment_method_action({"op": "add", "label": label}, timeout=timeout)


def update_remote_payment_method(option_id, label, timeout=10):
    return _post_payment_method_action({"op": "update", "option_id": option_id, "label": label}, timeout=timeout)


def set_remote_payment_method_kind(option_id, kind, timeout=10):
    return _post_payment_method_action({"op": "set_kind", "option_id": option_id, "kind": kind}, timeout=timeout)


def delete_remote_payment_method(option_id, timeout=10):
    return _post_payment_method_action({"op": "delete", "option_id": option_id}, timeout=timeout)


# Крок "Дії" remote-sync (2026-08-18, аудит "у всього є істина?"): той самий
# None/exception-контракт, що й custom-button/payment-method-функції вище.
def fetch_remote_operations_tree(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/operations_tree",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    operations = data.get("operations")
    fields = data.get("fields")
    columns = data.get("columns")
    if not isinstance(operations, list) or not isinstance(fields, list) or not isinstance(columns, list):
        return None
    return {"operations": operations, "fields": fields, "columns": columns}


# Задача користувача (2026-08-18): "додай кнопку яка буде перезберігати ці
# дані у хмарі... лише якщо кнопку натис - хмара оновилась... точний
# контроль" - той самий exception-контракт, що й send_remote_command/
# _post_custom_button_action: HTTPError/URLError/OSError пролітають до
# викликача (gui.py) як є, тут жодного UI.
def save_standard_menu_to_cloud(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/save_standard_menu_to_cloud",
        data=json.dumps({"token": paths.REMOTE_CONTROL_TOKEN}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# Задача користувача (2026-08-19): "додай кнопку системні команди
# чат-боту... галочки на ввімкнення... кнопка зберегти" - той самий None-
# контракт, що вже й fetch_remote_payment_methods (мережа/сервер офлайн,
# не "команд немає"); збереження - разовий POST усього словника одразу
# (кнопка "Зберегти" в діалозі), не поштучні add/update/delete дії, як у
# custom-button/payment-method-функціях вище - тут лише 4 фіксовані
# перемикачі, множити маршрути не було потреби.
def fetch_remote_system_commands(timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/system_commands",
        headers={"User-Agent": _USER_AGENT, _TOKEN_HEADER: paths.REMOTE_CONTROL_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    commands = data.get("commands")
    return commands if isinstance(commands, dict) else None


def save_remote_system_commands(commands, timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/system_commands_save",
        data=json.dumps({"token": paths.REMOTE_CONTROL_TOKEN, "commands": commands}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def set_remote_role(user_id, role, timeout=10):
    request = urllib.request.Request(
        f"{_BASE_URL}/control/set_role",
        data=json.dumps({"token": paths.REMOTE_CONTROL_TOKEN, "user_id": user_id, "role": role}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def send_remote_command(action, timeout=10):
    """Кидає той самий exception-контракт, що й звичайний urllib -
    HTTPError/URLError/OSError - викликач (gui.py) сам вирішує, як
    показати помилку користувачу, тут жодного UI."""
    request = urllib.request.Request(
        f"{_BASE_URL}/control/command",
        data=json.dumps({"token": paths.REMOTE_CONTROL_TOKEN, "action": action}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
