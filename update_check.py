"""Спільна перевірка оновлень для gui.py (розробник) і client_app.py
(клієнт) - один модуль, щоб обидва боти не дублювали логіку.

Задача користувача (2026-08-12): "додай і до моєї проги і до нової -
оновлення, кнопку. оновлюється на перевірку раз в 5 хв. якщо є оновлення -
показує в головному меню зверху справа у обох версіях синю кнопку
оновлення. і далі якщо це нова версія - завантажити, якщо це моя локальна -
встановити".

Джерело "яка версія зараз актуальна" - простий спільний JSON-файл-маніфест
(шлях - update_manifest_path у settings.json, порожньо за замовчуванням).
Навмисно жодної нової інфраструктури (сервера, хмарного API) - будь-яке
місце з файловим доступом (мережева папка, синхронізована OneDrive-тека)
підійде однаково, це вже питання розгортання, не коду. Формат:

    {
      "gui": {"version": "1.0.1", "package_path": "...", "notes": "..."},
      "client": {"version": "0.1.1", "package_path": "...", "notes": "..."}
    }

Пакування (.exe/zip для реальної доставки на ноутбук клієнта) - окремий,
ще не побудований етап (PyInstaller тощо, обговорювалось раніше). Наразі
package_path для "client" - шлях до теки з файлами client_app.py, яку
download_update просто копіює цілком; для "gui" (розробник) - install_
current_version публікує ПОТОЧНУ версію коду як нову офіційну, package_path
за замовчуванням - сам проєктний каталог.
"""

import json
import os
import shutil
from pathlib import Path


def _read_manifest(manifest_path):
    if not manifest_path:
        return None
    path = Path(manifest_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Нитпік з аудиту коду (2026-08-16): нечисловий фрагмент у сегменті
# (напр. "10-beta") раніше відкидав увесь сегмент до 0, тихо втрачаючи
# "10" ("0.2.10-beta" -> (0, 2, 0)). Тепер беруться ведучі цифри сегмента
# ("10-beta" -> "10" -> 10) - жодна з реально опублікованих версій цього
# проєкту такого не використовує (завжди чисто числові X.Y.Z), тож це
# лише страховка на майбутнє, не діюча зміна поведінки.
def _parse_version(text):
    parts = []
    for chunk in str(text or "0").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate_version, current_version):
    return _parse_version(candidate_version) > _parse_version(current_version)


# Повертає manifest-запис ({"version": ..., "package_path": ..., "notes":
# ...}), якщо там значиться версія НОВІША за current_version, інакше None
# (і "маніфест ще не налаштований", і "версія вже актуальна" - той самий
# результат: кнопку оновлення показувати не треба).
def check_for_update(manifest_path, app_key, current_version):
    manifest = _read_manifest(manifest_path)
    if not manifest:
        return None
    entry = manifest.get(app_key)
    if not entry or not entry.get("version"):
        return None
    if not is_newer(entry["version"], current_version):
        return None
    return entry


def download_update(entry, destination_dir):
    package_path = entry.get("package_path")
    if not package_path:
        raise RuntimeError("У маніфесті не вказано package_path.")
    source = Path(package_path)
    if not source.exists():
        raise RuntimeError(f"Файл/тека оновлення не знайдені: {source}")
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / source.name
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        shutil.copyfile(source, target)
    return target


# "встановити" з боку розробника: не завантаження ззовні (код і так уже
# тут, локально), а публікація ПОТОЧНОЇ версії як нової офіційної в
# маніфесті - саме це client_app.py потім побачить при своїй перевірці.
def publish_current_version(manifest_path, app_key, version, package_path, notes=""):
    if not manifest_path:
        raise RuntimeError("Шлях до маніфесту оновлень ще не налаштовано.")
    manifest = _read_manifest(manifest_path) or {}
    manifest[app_key] = {"version": version, "package_path": str(package_path), "notes": notes}
    # Реальний ризик (аудит коду, 2026-08-14): маніфест - ОДИН спільний файл
    # для gui І client_app.py (docstring вище прямо документує "мережева
    # папка/OneDrive-тека" як типове місце розгортання - мережевий збій
    # посеред запису тут цілком реалістичний, не гіпотетичний). Пряме
    # write_text посеред запису лишило б обрізаний/биту JSON - _read_manifest
    # це проковтне (повертає None), але тоді перевірка оновлень мовчки
    # перестає бачити ОБИДВІ версії (gui і client), не лише щойно опубліковану.
    # Той самий tmp-файл + os.replace, що вже рятує excel_source.py/
    # _set_backup_encryption_password.
    target_path = Path(manifest_path)
    tmp_path = target_path.with_name(target_path.name + ".tmp")
    tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target_path)
