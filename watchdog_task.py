"""Автоматичний перезапуск client_app.py, якщо вона зникла НЕПЛАНОВО -
крах, примусове завершення процесу через Диспетчер завдань тощо (Задача
користувача, 2026-08-17: "якщо програма закриється - то щоб запустилась
знову, якщо включений ПК... якщо так можна").

Реальна знахідка (2026-08-17): перший варіант цього модуля створював
задачу через /XML (щоб отримати вбудовану в Планувальник "Settings ->
If the task fails, restart every..." - точно за кодом виходу процесу,
0=штатно/не 0=крах). Реально протестовано - schtasks /create /xml валиться
з "ERROR: Access is denied" саме в цьому середовищі (підтверджено і напряму
через PowerShell, і без RestartOnFailure - отже сама XML-реєстрація
задачі заблокована тут, не конкретний елемент), тоді як звичайне
/create /tr ... (без /xml) працює без проблем. Тому - періодична
перевірка (/sc minute /mo 1, той самий флаговий /create) замість
XML-based RestartOnFailure: раз на хвилину client_app.py --watchdog-check
дивиться, чи запущена ЖИВА копія AI_Automation_Client.exe (окрім себе
самої), і якщо ні - чи є paths.GRACEFUL_EXIT_MARKER_PATH (лишений
ClientApp._on_exit_clicked ПРЯМО перед свідомим закриттям) - є: споживає
(видаляє) й нічого не робить; немає: запускає застосунок знову. Реалізація
цієї перевірки - у client_app.py (_run_watchdog_check), не тут - цей
модуль лише керує самою задачею Планувальника, той самий "тупий" принцип,
що й autostart.py.
"""

import subprocess

_TASK_NAME = "AI_Automation_Client_Watchdog"

# CREATE_NO_WINDOW - інакше кожен виклик schtasks.exe блимав би чорною
# консоллю поверх --windowed застосунку.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def is_enabled():
    result = subprocess.run(
        ["schtasks", "/query", "/tn", _TASK_NAME],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    return result.returncode == 0


def enable(command):
    result = subprocess.run(
        ["schtasks", "/create", "/tn", _TASK_NAME, "/tr", command, "/sc", "minute", "/mo", "1", "/f"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or result.stdout.strip() or "schtasks завершился с ошибкой.")


def disable():
    # Перевірка is_enabled() ПЕРЕД видаленням (а не парсинг тексту
    # помилки schtasks на "задачі не існує") - той самий ідемпотентний
    # контракт, що й autostart.disable(), надійніший за крихкий рядок
    # stderr, який може відрізнятись між локалізаціями Windows.
    if not is_enabled():
        return
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "Не удалось удалить задачу перезапуска.")
