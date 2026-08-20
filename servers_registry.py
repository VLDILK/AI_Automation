"""Хмарний реєстр серверів (OneDrive) - Задача користувача (2026-08-19):
"що за ручне налаштування? ...щоб автоматом бачив... і щоб я міг
перемикатись між цими серверами" - кожен client_app.py сам вписує СЕБЕ в
спільний файл на старті й періодично (register_this_server), gui.py лише
читає весь файл і показує все, що там є (read_servers) - жодного ручного
вводу адреси більше не потрібно.

Той самий "дурний" read/write JSON модуль і той самий принцип виявлення
OneDrive, що вже й standard_menu_cloud.py - навмисно продубльований тут
(не імпортований), той самий explicit-причина: gui.py не тягне важкий
client_app.py, а сам client_app.py уникає циклічного імпорту.

Задача користувача (2026-08-20, відкочено): короткий експеримент з
поділом main/test на ДВА окремі акаунти OneDrive (особистий + тенантний)
виявився хибним - перевірка реального email через реєстр Windows
показала, що "особистий" слот на двох різних машинах насправді ДВА РІЗНІ
акаунти (vladimirilkov24@gmail.com на одній, vladimir.ilkov@diverus.com
на іншій), тоді як ТЕНАНТНИЙ акаунт (Diverus, UAB) підтверджено
ОДНАКОВИЙ на обох машинах. Повернено єдиний спільний файл у тенантному
OneDrive - той самий акаунт, який і так уже все стабільно синхронізує в
цьому проєкті (бекапи, standard_menu_cloud.py тощо).

Задача користувача (2026-08-20, продовження): замість вгадування назви
теки по тенанту - опційне явне поле "email" (client_app.py, "Учётная
запись OneDrive") шукає ТОЧНИЙ обліковий запис через реєстр Windows
(той самий механізм, яким цього сеансу вручну діагностували плутанину
акаунтів) і бере його UserFolder напряму - надійніше за вгадування,
жодних сюрпризів з назвою тенантної теки. Порожньо/не знайдено = стара
поведінка (вгадування), нічого не ламається для тих, хто це не чіпав."""

import json
import os
from datetime import datetime
from pathlib import Path

_CLOUD_FOLDER_NAME = "AI_Automation_Backups"
_CLOUD_FILE_NAME = "servers_registry.json"

# Той самий реальний випадок (2026-08-18, standard_menu_cloud.py) - ця
# машина має ДВІ окремі синхронізовані теки OneDrive під тим самим
# акаунтом Windows: особисту й робочу (тенантну). AI_Automation_Backups
# завжди йде в РОБОЧИЙ (тенантний) OneDrive.
_ONEDRIVE_TENANT_SUFFIX = "OneDrive - Diverus, UAB"


# Windows зберігає КОЖЕН залогінений OneDrive-акаунт (особистий,
# Business1, Business2...) окремим підключем реєстру з парою UserEmail/
# UserFolder - той самий шлях, яким цього сеансу вручну через PowerShell
# з'ясували, що "особистий" слот на двох машинах виявився двома різними
# акаунтами. Пошук за email тут точний, без здогадок по назві теки.
def find_account_folder(email):
    try:
        import winreg
    except ImportError:
        return None
    email_normalized = email.strip().lower()
    if not email_normalized:
        return None
    try:
        accounts_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts")
    except OSError:
        return None
    try:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(accounts_key, index)
            except OSError:
                break
            index += 1
            try:
                subkey = winreg.OpenKey(accounts_key, subkey_name)
                try:
                    user_email = winreg.QueryValueEx(subkey, "UserEmail")[0]
                    user_folder = winreg.QueryValueEx(subkey, "UserFolder")[0]
                finally:
                    winreg.CloseKey(subkey)
            except OSError:
                continue
            if isinstance(user_email, str) and user_email.strip().lower() == email_normalized:
                folder_path = Path(user_folder)
                if folder_path.is_dir():
                    return folder_path
    finally:
        winreg.CloseKey(accounts_key)
    return None


def _resolve_onedrive_root(email=None):
    if email:
        matched = find_account_folder(email)
        if matched is not None:
            return matched
    username = os.environ.get("USERNAME")
    tenant_path = Path(f"C:/Users/{username}/{_ONEDRIVE_TENANT_SUFFIX}") if username else None
    if tenant_path is not None and tenant_path.is_dir():
        return tenant_path
    env_value = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive")
    env_path = Path(env_value) if env_value else None
    if env_path is not None and env_path.is_dir():
        return env_path
    return tenant_path or env_path


def _cloud_file_path(email=None):
    onedrive_root = _resolve_onedrive_root(email)
    if onedrive_root is None:
        return None
    return onedrive_root / _CLOUD_FOLDER_NAME / _CLOUD_FILE_NAME


# Задача користувача (2026-08-19, живий продакшн): "не показує взагалі. і
# через 15 хв" - register_this_server() підтверджено ВІДПОВІДАЄ успіхом
# (ok=True) на обох машинах, тенантний акаунт підтверджено ОДНАКОВИЙ
# (email через реєстр Windows) - лишок розбіжності, якщо він і далі
# трапляється, тепер варто списувати на звичайну затримку синхронізації
# OneDrive, не на різні акаунти. Видно через /control/status без потреби
# локального доступу до машини, де це реально стається.
def resolved_path_str(email=None):
    path = _cloud_file_path(email)
    return str(path) if path else None


def read_servers(email=None):
    """{} означає "хмара недоступна, чи файлу там ще немає" - викликач
    (gui.py) має трактувати це як "поки нічого не відомо", не як "серверів
    справді немає"."""
    path = _cloud_file_path(email)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("servers")
    if not isinstance(servers, dict):
        return {}
    return servers


# Best-effort, як і standard_menu_cloud.write_cloud_state - якщо OneDrive
# не налаштований на цій машині, просто тихо нічого не робить. Read-
# modify-write ОДНОГО спільного файлу - кожен виклик чіпає ЛИШЕ свій
# власний запис (ключ = name, зазвичай ім'я комп'ютера), не зачіпаючи
# записи інших машин; класична гонка при одночасному записі ДВОХ машин
# теоретично можлива, але виклик іде раз на кілька хвилин - той самий
# рівень допущення, що вже й іншими "дурними" cloud-модулями цього проєкту.
def register_this_server(name, hostname, kind, version, email=None):
    path = _cloud_file_path(email)
    if path is None or not name or not hostname:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            data = {}
        servers = data.get("servers")
        if not isinstance(servers, dict):
            servers = {}
        servers[name] = {
            "hostname": hostname,
            "kind": kind if kind in ("main", "test") else "main",
            "version": version or "",
            "updated_at": datetime.now().isoformat(),
        }
        data["servers"] = servers
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return True
    except OSError:
        return False


# Задача користувача: "✕" у попапі - прибрати сервер, що реально
# демонтований (стара тестова машина тощо), зі СПІЛЬНОГО реєстру, а не
# лише зі свого локального перегляду.
def remove_server(name, email=None):
    path = _cloud_file_path(email)
    if path is None:
        return False
    try:
        if not path.exists():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data.get("servers")
        if isinstance(servers, dict) and name in servers:
            del servers[name]
            data["servers"] = servers
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        return True
    except (OSError, ValueError):
        return False
