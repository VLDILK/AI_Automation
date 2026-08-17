"""Автоматичні знімки ВИХІДНОГО КОДУ програми — окремо від даних складу
(для тих уже є create_db_snapshot, warehouse_data.py). Задача користувача
(2026-08-14): "потрібен ще бекап версій програм. щоб зберігався раз в 30 хв.
а також щоб створювався перед кожним оновленням".

Той самий принцип, що й DB-знімки (тимчасовий архів у CODE_BACKUP_DIR,
ротація за лімітом кількості), але з дедуплікацією за вмістом замість
"раз на день": код змінюється значно рідше, ніж дані складу, тож новий
знімок реально створюється лише коли вміст ВІДРІЗНЯЄТЬСЯ від попереднього —
інакше 30-хвилинний тік (gui.py/client_app.py) плодив би сотні однакових
копій під час тихих періодів без жодної правки коду. force=True (виклик
перед публікацією/завантаженням оновлення) обходить цю дедуплікацію —
той самий принцип, що й create_db_snapshot(label="pre_restore") завжди
створює знімок, незалежно від "чи вже є сьогоднішній".
"""

import hashlib
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from paths import BASE_DIR, CODE_BACKUP_DIR

CODE_BACKUP_LIMIT = 200

# Лише реальний вихідний код — жодних даних/секретів/тимчасових файлів.
# system/ навмисно лише *.py (pdf_stock_report.py) — settings.json/
# msal_token_cache.json/telegram_offset.json лежать у тій самій теці, але
# це секрети/стан, не код, їм тут не місце.
_INCLUDE_SUBDIR_PATTERNS = {
    "": ("*.py",),
    "webapp": ("*.py", "*.js", "*.html", "*.css"),
    "system": ("*.py",),
}


def _collect_source_files():
    files = []
    for subdir, patterns in _INCLUDE_SUBDIR_PATTERNS.items():
        base = BASE_DIR / subdir if subdir else BASE_DIR
        if not base.is_dir():
            continue
        for pattern in patterns:
            files.extend(base.glob(pattern))
    return sorted(files, key=lambda p: str(p.relative_to(BASE_DIR)))


def _content_hash(files):
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(BASE_DIR)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _existing_snapshots():
    if not CODE_BACKUP_DIR.exists():
        return []
    return sorted(CODE_BACKUP_DIR.glob("app_code_*.zip"), key=lambda p: p.stat().st_mtime)


def _latest_snapshot_hash():
    snapshots = _existing_snapshots()
    if not snapshots:
        return None
    # Ім'я файлу: app_code_<timestamp>[_<label>]_<hash12>.zip - хеш завжди
    # останній "_"-сегмент перед розширенням.
    return snapshots[-1].stem.rsplit("_", 1)[-1]


def create_code_snapshot(label=None, force=False):
    """Повертає Path нового знімка, або None, якщо код не змінився з
    попереднього разу (і force=False)."""
    # Пакування в .exe: у зібраній версії немає вихідних .py-файлів
    # (_collect_source_files() глобить BASE_DIR/*.py й повернув би порожньо) -
    # без цієї перевірки функція мовчки "успішно" створювала б порожній zip.
    # force=False (звичайний 30-хвилинний тік) - тиша, той самий контракт, що
    # й сьогоднішнє "код не змінився". force=True (ручна кнопка/
    # pre_publish/pre_update - свідомі дії) - явна помилка користувачу.
    if getattr(sys, "frozen", False):
        if not force:
            return None
        raise OSError(
            "Резервное копирование исходного кода недоступно в собранной "
            "(.exe) версии программы — исходных .py-файлов в дистрибутиве нет."
        )
    CODE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = _collect_source_files()
    content_hash = _content_hash(files)
    if not force and content_hash == _latest_snapshot_hash():
        return None
    # Мікросекундна точність (той самий прийом, що й create_db_snapshot,
    # warehouse_data.py, і з тієї самої причини) - секундної роздільності не
    # вистачило б, якби два знімки (напр. звичайний тік і force=True перед
    # публікацією) стались в межах однієї секунди БЕЗ зміни вмісту коду між
    # ними: секундного імені файлу + того самого хешу було б досить, щоб
    # другий запис тихо переписав перший замість створення окремого файлу.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = f"_{label}" if label else ""
    target_path = CODE_BACKUP_DIR / f"app_code_{timestamp}{suffix}_{content_hash}.zip"
    # Реальний баг (аудит коду, 2026-08-15): перерваний запис (закриття
    # застосунку/крах саме під час 30-хвилинного тіку чи pre_update/
    # pre_publish знімка) раніше лишав би усічений zip одразу під ФІНАЛЬНИМ,
    # хеш-іменованим шляхом - _latest_snapshot_hash() читав би цю назву як
    # доказ "останній знімок вже актуальний" і мовчки блокував би створення
    # справжнього знімка, поки вміст коду не зміниться знову. Пишемо у
    # тимчасовий файл і атомарно перейменовуємо лише ПІСЛЯ успішного
    # закриття архіву.
    tmp_path = target_path.with_name(target_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(BASE_DIR))
    tmp_path.replace(target_path)
    _rotate_code_snapshots()
    return target_path


def _rotate_code_snapshots(limit=CODE_BACKUP_LIMIT):
    snapshots = _existing_snapshots()
    excess = len(snapshots) - limit
    if excess <= 0:
        return
    for path in snapshots[:excess]:
        path.unlink(missing_ok=True)


def list_code_snapshots():
    """Метадані знімків для GUI, найновіші перші. "label" - лише прості,
    наперед відомі мітки (pre_publish/pre_update) через пряму перевірку
    підрядка в імені файлу - структурний розбір довільного label_у з хешем
    після нього (теж через "_") був би неоднозначним."""
    snapshots = sorted(_existing_snapshots(), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for path in snapshots:
        if "_pre_publish_" in path.name:
            label = "pre_publish"
        elif "_pre_update_" in path.name:
            label = "pre_update"
        else:
            label = None
        result.append({
            "path": path,
            "mtime": path.stat().st_mtime,
            "size": path.stat().st_size,
            "label": label,
        })
    return result
