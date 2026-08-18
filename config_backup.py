"""Автоматичні знімки КОНФІГУРАЦІЙНИХ файлів (settings.json, ключ
Cloudflare-тунелю, offset Telegram-бота) — окремо від коду (code_backup.py)
і даних складу (create_db_snapshot, warehouse_data.py). Задача користувача
(2026-08-16): "так, бекап і на хмару" — ці файли раніше не потрапляли в
жоден з наявних бекапів: втрата settings.json скидає всі налаштування на
дефолт, втрата cloudflared_tunnel_credentials.json ламає фіксовану адресу
форми (bot.botaiautomationeu.trade), довелось би перестворювати тунель
заново.

Реальна знахідка (аудит коду, 2026-08-16): telegram_offset.json теж сюди
додано пізніше того самого дня — main.py._load_persisted_offset() мовчки
повертає None на будь-якій помилці читання цього файлу, а offset=None у
getUpdates означає "поверни ВЕСЬ буферизований бек-лог Telegram" - бот
заново обробив би всі недавні повідомлення (потенційно подвійні
продажі/приходи, якщо якесь із них устигло дійти до "confirm_write" і
далі). Цей файл не рятує від втрати саму по собі (30-хвилинний тік - той
самий інтервал, що й у решти цього модуля), але звужує вікно "скільки
повідомлень довелось б переграти" з "необмежено" до "не більше 30 хв".

Той самий принцип дедуплікації за вмістом, що й code_backup.py: ці файли
змінюються рідко, тож новий знімок реально створюється лише коли вміст
відрізняється від попереднього.
"""

import hashlib
import zipfile
from datetime import datetime

from paths import (
    CLOUDFLARED_TUNNEL_CREDENTIALS_PATH,
    CONFIG_BACKUP_DIR,
    GITHUB_TOKEN_PATH,
    SETTINGS_PATH,
    TELEGRAM_OFFSET_PATH,
)

CONFIG_BACKUP_LIMIT = 100

# cloudflared_tunnel_credentials.json існує лише в інсталяції client_app.py
# (тунель тримає тільки він) - _collect_files() нижче бере лише те, що
# реально є на диску, тож знімок gui.py міститиме лише settings.json (і
# telegram_offset.json, якщо там теж підключений бот).
# github_token.txt (2026-08-18, "на що ще немає бекапів?") - раніше не мав
# ЖОДНОЇ копії ніде: втрата цього файлу ламала публікацію релізів до
# видачі нового PAT-токена вручну.
_INCLUDE_FILES = (
    SETTINGS_PATH, CLOUDFLARED_TUNNEL_CREDENTIALS_PATH, TELEGRAM_OFFSET_PATH, GITHUB_TOKEN_PATH,
)


def _collect_files():
    return [path for path in _INCLUDE_FILES if path.exists()]


def _content_hash(files):
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _existing_snapshots():
    if not CONFIG_BACKUP_DIR.exists():
        return []
    return sorted(CONFIG_BACKUP_DIR.glob("config_*.zip"), key=lambda p: p.stat().st_mtime)


def _latest_snapshot_hash():
    snapshots = _existing_snapshots()
    if not snapshots:
        return None
    return snapshots[-1].stem.rsplit("_", 1)[-1]


def create_config_snapshot(force=False):
    """Повертає Path нового знімка, або None, якщо конфігурація не
    змінилась з попереднього разу (force=False), чи жоден з файлів ще не
    існує (перший запуск, налаштування ще не збережені)."""
    files = _collect_files()
    if not files:
        return None
    content_hash = _content_hash(files)
    if not force and content_hash == _latest_snapshot_hash():
        return None
    CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target_path = CONFIG_BACKUP_DIR / f"config_{timestamp}_{content_hash}.zip"
    # Той самий tmp+replace прийом, що й create_code_snapshot — перерваний
    # запис не повинен лишити усічений zip під фінальним, хеш-іменованим
    # шляхом (інакше _latest_snapshot_hash() прочитав би биту назву як
    # доказ "вже актуально" і мовчки заблокував би реальний знімок).
    tmp_path = target_path.with_name(target_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
    tmp_path.replace(target_path)
    _rotate_config_snapshots()
    return target_path


def _rotate_config_snapshots(limit=CONFIG_BACKUP_LIMIT):
    snapshots = _existing_snapshots()
    excess = len(snapshots) - limit
    if excess <= 0:
        return
    for path in snapshots[:excess]:
        path.unlink(missing_ok=True)
