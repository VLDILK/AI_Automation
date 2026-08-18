"""Хмарна істина для "стандартного меню" бота (OneDrive) — Задача
користувача (2026-08-18): "хмарні параметри стандарту налаштовуватись
мають у самому застосунку. тобто які параметри я виберу в редакторі
кнопок - ті і будуть в хмарі як істина". Той самий принцип виявлення
OneDrive, що вже є в client_app.py._mirror_backup_to_onedrive (env var
"OneDrive" -> локально-синхронізована тека на диску, без Graph API/OAuth,
ОС-клієнт OneDrive сам вивантажує зміни в хмару) — навмисно продубльований
тут замість імпорту з client_app.py (важкий Tkinter-модуль).

Задача користувача (2026-08-18, уточнення того самого дня): "розкладки
кнопок більше не можуть братися нізвідки окрім як з хмари. а точніше
копіювання з хмари в локальний файл щоб лагів не було з кнопками
постійними" — крім самого хмарного (OneDrive) файлу нижче, є ОКРЕМИЙ
локальний файл-кеш (paths.STANDARD_MENU_CACHE_PATH, звичайна тека system/,
без жодного зв'язку з OneDrive) — реальна копія хмари на диску, яку
client_app.py оновлює при кожному старті, щоб гарячий шлях показу меню
бота ніколи не залежав від стану синхронізації OneDrive.

Це "дурний" модуль (той самий принцип, що й autostart.py/watchdog_task.py):
лише читання/запис JSON-файлів, жодної бізнес-логіки чи рішень "коли
звіряти" — це вирішує викликач (client_app.py, НЕ warehouse_data.py: gui.py
теж створює ExcelSqliteStore локально, а її власне дерево custom_menu_buttons
давно мертве — Редактор кнопок працює через remote-sync на ЖИВИЙ
client_app.py, — тож ця синхронізація нізащо не повинна сидіти в
ExcelSqliteStore.__init__, інакше запуск gui.py на dev-машині міг би
затерти РЕАЛЬНУ хмарну істину власним, нерелевантним локальним станом).
"""

import json
import os
from pathlib import Path

import paths

_CLOUD_FOLDER_NAME = "AI_Automation_Backups"
_CLOUD_FILE_NAME = "standard_menu.json"


# Задача користувача (2026-08-18, підтверджено на живій машині): цей ПК
# має ДВІ окремі синхронізовані теки OneDrive під тим самим акаунтом
# Windows "vladi" — особисту (C:\Users\vladi\OneDrive) і робочу, тенантну
# (C:\Users\vladi\OneDrive - Diverus, UAB). Змінна середовища "OneDrive"
# вказує на ОСОБИСТУ (яка теж реально існує), тож просте читання цієї
# змінної мовчки відкривало не ту теку — не тому, що вона застаріла чи
# "неправильна", а тому, що на цій машині вона просто вказує на ІНШИЙ,
# теж легітимний OneDrive. AI_Automation_Backups завжди йде в РОБОЧИЙ
# (тенантний) OneDrive — пробуємо його ПЕРШИМ, і лише якщо його немає на
# диску (інша машина без цього тенанту) — падаємо назад на змінну "OneDrive".
_ONEDRIVE_TENANT_SUFFIX = "OneDrive - Diverus, UAB"


def _resolve_onedrive_root():
    username = os.environ.get("USERNAME")
    tenant_path = Path(f"C:/Users/{username}/{_ONEDRIVE_TENANT_SUFFIX}") if username else None
    if tenant_path is not None and tenant_path.is_dir():
        return tenant_path
    env_value = os.environ.get("OneDrive")
    env_path = Path(env_value) if env_value else None
    if env_path is not None and env_path.is_dir():
        return env_path
    return tenant_path or env_path


def cloud_folder_path():
    """Публічна (не лише для read/write_cloud_state нижче) — Задача
    користувача (2026-08-18): "кнопка має бути видима завжди, щоб кожен
    раз з програми міг глянути" - gui.py використовує це саме для кнопки
    "Открыть папку", постійно видимої в Редакторі кнопок."""
    onedrive_root = _resolve_onedrive_root()
    if onedrive_root is None:
        return None
    return onedrive_root / _CLOUD_FOLDER_NAME


def _cloud_file_path():
    folder = cloud_folder_path()
    if folder is None:
        return None
    return folder / _CLOUD_FILE_NAME


def _read_state_file(path):
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    state = data.get("enabled_root_migration_keys")
    if not isinstance(state, dict):
        return None
    return {str(k): bool(v) for k, v in state.items()}


def _write_state_file(path, state):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"enabled_root_migration_keys": {str(k): bool(v) for k, v in state.items()}}
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return True
    except OSError:
        return False


def read_cloud_state():
    """None означає "хмара недоступна, чи файлу там ще немає" — виклик має
    трактувати це як "нічого звіряти", НЕ як "хмара каже: усе вимкнено"."""
    return _read_state_file(_cloud_file_path())


def write_cloud_state(state):
    """Best-effort — як і _mirror_backup_to_onedrive: якщо OneDrive не
    налаштований на цій машині, просто тихо нічого не робить (повертає
    False), без винятку — це резервний, не критичний шлях."""
    path = _cloud_file_path()
    if path is None:
        return False
    return _write_state_file(path, state)


def read_local_cache():
    """Локальна копія (paths.STANDARD_MENU_CACHE_PATH) — звичайна тека
    system/, ніякого зв'язку з OneDrive. None — кешу ще немає (найперший
    запуск, ще до першої звірки з хмарою)."""
    return _read_state_file(paths.STANDARD_MENU_CACHE_PATH)


def write_local_cache(state):
    return _write_state_file(paths.STANDARD_MENU_CACHE_PATH, state)
