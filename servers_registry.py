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

Задача користувача (2026-08-20): тестовий сервер писав у реєстр (успішно,
registry_last_error=None), але на іншій машині (де читає gui.py) файл
лишався порожнім - причина: РІЗНІ Windows-акаунти (vladi/Vladimir2) на
двох фізичних машинах, обидва з ОДНАКОВОЮ назвою тенантної теки
"OneDrive - Diverus, UAB" - але користувач підтвердив, що насправді хоче
навмисно РІЗНІ акаунти для тесту й робочої версії ("той що не
використовується наразі" - тест, "той що вже використовується" -
робочий, лишити як є). Обидва акаунти доступні на ОБОХ машинах (users
підтвердив), тож gui.py тепер читає ОБИДВІ теки й об'єднує список."""

import json
import os
from datetime import datetime
from pathlib import Path

_CLOUD_FOLDER_NAME = "AI_Automation_Backups"
_CLOUD_FILE_NAME = "servers_registry.json"

# Робочий (main) сервер - тенантний OneDrive, як і завжди був, ніхто це не
# чіпав. Тестовий (test) сервер - НАВМИСНО інший, особистий OneDrive (без
# суфікса) - окремий акаунт, а не просто підтека, щоб реєстри двох середовищ
# фізично не залежали від того, який акаунт "переміг" при синхронізації.
_ONEDRIVE_TENANT_SUFFIX = "OneDrive - Diverus, UAB"


def _resolve_onedrive_root(kind):
    username = os.environ.get("USERNAME")
    if kind == "test":
        guessed_path = Path(f"C:/Users/{username}/OneDrive") if username else None
        env_value = os.environ.get("OneDriveConsumer")
    else:
        guessed_path = Path(f"C:/Users/{username}/{_ONEDRIVE_TENANT_SUFFIX}") if username else None
        env_value = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive")
    if guessed_path is not None and guessed_path.is_dir():
        return guessed_path
    env_path = Path(env_value) if env_value else None
    if env_path is not None and env_path.is_dir():
        return env_path
    return guessed_path or env_path


def _cloud_file_path(kind):
    onedrive_root = _resolve_onedrive_root(kind)
    if onedrive_root is None:
        return None
    return onedrive_root / _CLOUD_FOLDER_NAME / _CLOUD_FILE_NAME


# Показує шлях, куди ЦЯ машина реально пише СВІЙ запис (залежить від
# власного kind) - видно через /control/status без потреби локального
# доступу до машини, де це реально стається.
def resolved_path_str(kind="main"):
    path = _cloud_file_path(kind)
    return str(path) if path else None


def read_servers():
    """Читає ОБИДВА реєстри (робочий тенантний і тестовий особистий) та
    об'єднує їх - "Сервері" має бачити і main, і test одночасно, незалежно
    від того, у якому акаунті кожен фізично зберігається. {} для одного з
    них означає "той акаунт тут недоступний, чи файлу там ще немає" -
    викликач (gui.py) має трактувати відсутність записів як "поки нічого не
    відомо", не як "серверів справді немає"."""
    merged = {}
    for kind in ("main", "test"):
        path = _cloud_file_path(kind)
        if path is None or not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("servers")
        if isinstance(servers, dict):
            merged.update(servers)
    return merged


# Best-effort, як і standard_menu_cloud.write_cloud_state - якщо OneDrive
# не налаштований на цій машині, просто тихо нічого не робить. Read-
# modify-write ОДНОГО файлу (свого kind) - кожен виклик чіпає ЛИШЕ свій
# власний запис (ключ = name, зазвичай ім'я комп'ютера), не зачіпаючи
# записи інших машин того самого kind; класична гонка при одночасному
# записі ДВОХ машин теоретично можлива, але виклик іде раз на кілька
# хвилин - той самий рівень допущення, що вже й іншими "дурними" cloud-
# модулями цього проєкту.
def register_this_server(name, hostname, kind, version):
    kind = kind if kind in ("main", "test") else "main"
    path = _cloud_file_path(kind)
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
            "kind": kind,
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
# лише зі свого локального перегляду. Викликач (gui.py) не знає, під яким
# kind реєструвався той сервер - шукаємо й видаляємо з ОБОХ реєстрів.
def remove_server(name):
    all_ok = True
    for kind in ("main", "test"):
        path = _cloud_file_path(kind)
        if path is None or not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("servers")
            if isinstance(servers, dict) and name in servers:
                del servers[name]
                data["servers"] = servers
                tmp_path = path.with_name(path.name + ".tmp")
                tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp_path.replace(path)
        except (OSError, ValueError):
            all_ok = False
    return all_ok
