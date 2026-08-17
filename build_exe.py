"""Пакування в .exe (PyInstaller). Задача користувача: "давай пакування в
.exe" - зібрати обидва застосунки (main.py -> gui.py, client_app.py) в
автономні дистрибутиви, які запускаються на Windows-ПК без встановленого
Python.

Запуск: .venv\\Scripts\\python.exe build_exe.py
Передумова: pip install -r requirements.txt && pip install pyinstaller

Три цілі, у такому порядку (PDF-хелпер - першим, бо його результат треба
скопіювати всередину двох інших дистрибутивів):
  1. system/pdf_stock_report.py -> dist/pdf_stock_report/ - окремий маленький
     .exe для генерації PDF (єдине місце в проєкті, що використовує
     reportlab). Причина окремого білда, а не in-process виклику -
     збереження ізоляції крашів/таймауту, навколо якої вже написана
     обробка помилок у reports.py (render_report_pdf).
  2. main.py -> dist/AI_Automation_Home/ - "стара" програма (gui.py,
     ExcelViewerApp). БЕЗ webapp/icons/cloudflared.exe - локальний запуск
     бота/тунелю в ній вимкнено безповоротно (жодна кнопка/таймер туди
     більше не веде) - це свідоме зменшення розміру, не недогляд.
  3. client_app.py -> dist/AI_Automation_Client/ - "нова" програма
     (ClientApp) - реальний хост бота+форми, потребує webapp/icons/
     cloudflared.exe.
"""

import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
SPEC_DIR = BASE_DIR / "build_specs"
RUNTIME_BACKUP_DIR = BASE_DIR / "_build_runtime_backup"

ADD_DATA_SEP = ";"  # Windows-роздільник для --add-data/--add-binary

# Реальний інцидент (2026-08-15, той самий клас багу, що вже стався в
# planner-проєкті): PyInstaller COLLECT з --clean видаляє ВСЮ dist/<ім'я>/
# перед кожною збіркою ("Removing dir ...\dist\AI_Automation_Client" у логах)
# - разом із самим .exe це стирає ВСЕ, що застосунок сам створив під час
# роботи (settings.json - шлях до токена/таблиці/тема, app_data.sqlite3,
# test_sklad.xlsx, backups/db_backups/code_backups/reports). Користувач
# запускає застосунки прямо з dist/ як реальну робочу версію - кожен
# `build_exe.py` після цього мовчки скидав усі його налаштування й дані.
# Фікс: бекап цих шляхів ПЕРЕД збіркою, відновлення ПІСЛЯ (для обох цілей).
RUNTIME_DATA_RELATIVE_PATHS = (
    "system",
    "app_data.sqlite3",
    "test_sklad.xlsx",
    "backups",
    "db_backups",
    "code_backups",
    "reports",
)


def check_pyinstaller_available():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller не встановлено в цьому Python-оточенні.\n"
            "Встановіть: .venv\\Scripts\\python.exe -m pip install pyinstaller"
        )


def check_required_files():
    missing = [
        str(path)
        for path in (
            BASE_DIR / "webapp",
            BASE_DIR / "icons",
            BASE_DIR / "cloudflared.exe",
            BASE_DIR / "system" / "cloudflared_tunnel_credentials.json",
        )
        if not path.exists()
    ]
    if missing:
        raise SystemExit("Відсутні файли/теки, потрібні для білда client_app: " + ", ".join(missing))


# Реальна знахідка (2026-08-16, "чому він блокує? як це обійти?"):
# PyInstaller --onedir спершу видаляє СТАРУ dist/<ім'я>/ повністю, ДО
# запису нової - якщо застосунок (AI_Automation_Home.exe чи
# AI_Automation_Client.exe) саме зараз запущений із ЦІЄЇ теки, Windows
# тримає його .exe/.pyd-файли заблокованими в пам'яті (на відміну від
# Linux, де видалити запущений бінарник можна) - shutil.rmtree() падає з
# "[WinError 5] Access is denied" глибоко всередині PyInstaller, сирим
# трейсбеком, що нічого не каже про РЕАЛЬНУ причину. Дані користувача при
# цьому НЕ втрачаються (backup_runtime_data/restore_runtime_data нижче
# спрацьовують у будь-якому разі, навіть якщо сам білд впав) - лишається
# тільки незрозуміле повідомлення. Захоплюємо вивід (замість живого
# стріму в консоль) лише для того, щоб розпізнати САМЕ цей клас помилки і
# замінити його на дію, яку людина реально може зробити.
def run_pyinstaller(args):
    cmd = [sys.executable, "-m", "PyInstaller", *args]
    print("\n>>> " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    print(result.stderr)
    if result.returncode != 0:
        combined = result.stdout + result.stderr
        if "Access is denied" in combined or "WinError 5" in combined:
            raise SystemExit(
                "PyInstaller не зміг перезаписати стару збірку - файл(и) заблоковані, бо "
                "AI_Automation_Home.exe або AI_Automation_Client.exe зараз запущені з теки dist/. "
                "Закрийте застосунок і спробуйте знову - жодні дані (settings.json, БД, Excel) при "
                "цьому не постраждали, вони вже відновлені з резервної копії."
            )
        raise SystemExit(f"PyInstaller завершився з помилкою (код {result.returncode}).")


def build_pdf_helper():
    run_pyinstaller(
        [
            "system/pdf_stock_report.py",
            "--name",
            "pdf_stock_report",
            "--onedir",
            "--console",
            "--contents-directory",
            ".",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(SPEC_DIR),
            "--clean",
            "--noconfirm",
        ]
    )


def build_gui():
    run_pyinstaller(
        [
            "main.py",
            "--name",
            "AI_Automation_Home",
            "--onedir",
            "--windowed",
            # PyInstaller 6.x за замовчуванням кладе весь вміст --onedir у
            # підтеку _internal/, не поруч із самим .exe - paths.py очікує
            # BASE_DIR (= тека .exe) як плаский корінь (той самий принцип, що
            # й до PyInstaller 6.0). "." повертає стару плоску структуру.
            "--contents-directory",
            ".",
            "--collect-data",
            "certifi",  # onedrive_sync.py -> msal/requests, інакше TLS мовчки ламається
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(SPEC_DIR),
            "--clean",
            "--noconfirm",
        ]
    )


def build_client():
    run_pyinstaller(
        [
            "client_app.py",
            "--name",
            "AI_Automation_Client",
            "--onedir",
            "--windowed",
            "--contents-directory",
            ".",
            "--collect-data",
            "certifi",
            "--collect-data",
            "customtkinter",  # теми/шрифти/іконки в customtkinter/assets/
            # Абсолютні шляхи джерела: з окремим --specpath PyInstaller
            # резолвить відносні шляхи в --add-data/--add-binary відносно
            # ТЕКИ .spec-файлу (build_specs/), а не CWD - відносний
            # "webapp" не знаходився.
            "--add-data",
            f"{BASE_DIR / 'webapp'}{ADD_DATA_SEP}webapp",
            "--add-data",
            f"{BASE_DIR / 'icons'}{ADD_DATA_SEP}icons",
            "--add-binary",
            f"{BASE_DIR / 'cloudflared.exe'}{ADD_DATA_SEP}.",
            # Задача користувача (2026-08-15): іменований (persistent)
            # Cloudflare Tunnel замість Quick Tunnel - файл облікових даних
            # тунелю (paths.CLOUDFLARED_TUNNEL_CREDENTIALS_PATH) має їхати
            # РАЗОМ із зібраною програмою на будь-який ПК, де вона реально
            # запускається (на відміну від решти system/, яка навмисно НЕ
            # пакується - це єдиний виняток, бо без нього форма взагалі не
            # підключиться до Cloudflare).
            "--add-data",
            f"{BASE_DIR / 'system' / 'cloudflared_tunnel_credentials.json'}{ADD_DATA_SEP}system",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(SPEC_DIR),
            "--clean",
            "--noconfirm",
        ]
    )


def backup_runtime_data(app_dist_name):
    src_root = DIST_DIR / app_dist_name
    backup_root = RUNTIME_BACKUP_DIR / app_dist_name
    if not src_root.exists():
        # dist/<name>/ вже відсутня (наприклад, попередній запуск впав
        # ПОСЕРЕД --clean і не встиг відновити) - лишаємо будь-який СТАРИЙ
        # backup_root незайманим: це, можливо, єдина ще ціла копія даних.
        return backup_root if backup_root.exists() else None
    # Реальний баг (аудит коду, 2026-08-15): раніше існуючий backup_root
    # видалявся ОДРАЗУ, до того, як новий бекап підтверджено успішним.
    # Пишемо у тимчасову теку і заміняємо старий backup_root лише ПІСЛЯ
    # того, як новий бекап реально щось знайшов і повністю скопіювався -
    # інакше невдалий/порожній новий прохід міг би стерти останню ще цілу
    # копію даних користувача перед тим, як з'ясується, що копіювати нема що.
    tmp_root = RUNTIME_BACKUP_DIR / f"{app_dist_name}.tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    found_any = False
    for rel in RUNTIME_DATA_RELATIVE_PATHS:
        src = src_root / rel
        if not src.exists():
            continue
        dest = tmp_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        found_any = True
    if not found_any:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return backup_root if backup_root.exists() else None
    if backup_root.exists():
        shutil.rmtree(backup_root)
    tmp_root.rename(backup_root)
    return backup_root


# Відновлення ПІСЛЯ збірки. dirs_exist_ok=True зливає поверх свіжого дерева -
# для "system/" це означає, що щойно розпаковані build-файли (наприклад,
# cloudflared_tunnel_credentials.json) тимчасово повертаються до старої копії,
# але install_pdf_helper_into (викликається одразу після) і сам факт, що
# credentials.json незмінний між збірками, роблять це нешкідливим.
def restore_runtime_data(app_dist_name, backup_root):
    if backup_root is None:
        return
    dest_root = DIST_DIR / app_dist_name
    for rel in RUNTIME_DATA_RELATIVE_PATHS:
        src = backup_root / rel
        if not src.exists():
            continue
        dest = dest_root / rel
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    shutil.rmtree(backup_root, ignore_errors=True)


def install_pdf_helper_into(app_dist_name):
    src = DIST_DIR / "pdf_stock_report"
    dest = DIST_DIR / app_dist_name / "system" / "pdf_stock_report"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


def folder_size_mb(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def main():
    check_pyinstaller_available()
    check_required_files()

    # Реальний баг (2026-08-17, живий продакшн): backup_runtime_data/
    # restore_runtime_data("AI_Automation_Client", ...) раніше викликались
    # тут так само, як і для Home - але, на відміну від Home (реально
    # запущена локально програма, чиї settings.json/БД/Excel треба
    # захистити від --clean), AI_Automation_Client НІКОЛИ не запускається
    # локально на цій машині - dist/AI_Automation_Client тут лише тека для
    # збирання й ПУБЛІКАЦІЇ на GitHub Releases. restore_runtime_data
    # підкладала в щойно зібраний пакет МІСЦЕВИЙ system/settings.json (з
    # локальними шляхами - OneDrive Excel, C:\IT\... - цієї машини) - його
    # й публікувало. Самовстановлення на робочому ПК (client_app.py._install_
    # downloaded_update) робить robocopy БЕЗ /MIR (нічого зайвого не
    # видаляє), АЛЕ файли з однаковими іменами воно ПЕРЕЗАПИСУЄ - тож
    # settings.json з опублікованого пакета щоразу затирав РЕАЛЬНИЙ
    # settings.json робочого ПК (шлях до ключа Telegram, до Excel тощо).
    # Звідси "ключ Telegram злітає після кожного оновлення". cloudflared_
    # tunnel_credentials.json (єдине, що клієнту дійсно потрібне в system/)
    # і так завжди свіжо копіюється через --add-data у build_client() нижче
    # - backup/restore тут ніколи не був потрібен для коректності, лише
    # шкодив.
    gui_backup = backup_runtime_data("AI_Automation_Home")

    # Реальний баг (аудит коду, 2026-08-15): якщо PyInstaller впаде ПОСЕРЕД
    # будь-якої з трьох збірок (заблокований файл, антивірус, брак місця) -
    # без try/finally відновлення нижче просто НЕ виконалось би, лишаючи
    # dist/ без даних користувача (--clean уже стер їх) без жодного сліду,
    # що треба відновити вручну.
    try:
        build_pdf_helper()
        build_gui()
        build_client()
    finally:
        restore_runtime_data("AI_Automation_Home", gui_backup)

    install_pdf_helper_into("AI_Automation_Home")
    install_pdf_helper_into("AI_Automation_Client")

    print("\n" + "=" * 60)
    print("Готово. Зібрані дистрибутиви:")
    for name in ("AI_Automation_Home", "AI_Automation_Client"):
        folder = DIST_DIR / name
        print(f"  {folder}  ({folder_size_mb(folder):.1f} МБ)")
    print("=" * 60)


if __name__ == "__main__":
    main()
