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
