# -*- coding: utf-8 -*-
"""Замок на другу копію програми на одному ПК.

Задача користувача (2026-09-05): "потрібно заборонити програмі повторний
запуск копії, якщо вже на даному ПК є запущена ця програма. щоб не
дублювались записи при випадково двох запущених програмах".

Чому це справді ламало дані: дві копії пишуть в ОДИН test_sklad.xlsx і одну
app_data.sqlite3, тож один проведений продаж лягає двома рядками, а залишок
зменшується вдвічі. Ніщо цьому не заважало - перевірено пошуком по коду:
ні мьютекса, ні файла-замка не було взагалі; порт 8765 друга копія клієнта
зайняти не може, але помилку там мовчки проковтнуто (except OSError: pass),
а 409 від Telegram зупиняє ЛИШЕ бота - GUI, форма і звіти другої копії
пишуть далі.

Замок тримає ЯДРО Windows (іменований мьютекс), а не файл на диску. Після
будь-якого зникнення процесу - закрили, впав, убили через Диспетчер задач -
мьютекс звільняється тієї ж миті, тож застряглого замка, який доводиться
знімати руками, не буває в принципі. Це ж причина, чому замок не заважає
самооновленню: .bat чекає зникнення старого процесу і тільки тоді запускає
новий (перевірено в коді встановлення - client_app.py і gui.py).

Перевірено прямим запуском CreateMutexW на цій машині під НЕ адміністратором
(SeCreateGlobalPrivilege у списку прав відсутнє): і Global\\, і Local\\
створюються з кодом помилки 0, а друга спроба чесно повертає
ERROR_ALREADY_EXISTS. Global\\ узято першим - він бачить копію, запущену під
ІНШИМ користувачем Windows; якщо політика ПК глобальний простір імен закрила,
відбувається тихий перехід на Local\\ (тоді замок діє в межах сеансу).

Вікно першої копії шукається за ІМЕНЕМ ПРОЦЕСУ, а не за заголовком: обидві
програми називають своє вікно однаково - "AI Automation" (gui.py, client_app.py),
тож за заголовком домашка піднімала б вікно клієнта і навпаки.
"""

import ctypes
import os
import sys
from ctypes import wintypes

# Імена замків. Різні для двох програм - вони мають право працювати поруч
# на одному ПК; заборонена саме ДРУГА копія тієї самої програми.
HOME_LOCK_NAME = "AI_Automation_Home_v1"
CLIENT_LOCK_NAME = "AI_Automation_Client_v1"

_ERROR_ALREADY_EXISTS = 183
_ERROR_ACCESS_DENIED = 5

_SW_RESTORE = 9
_GW_OWNER = 4
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL

# Дескриптор живе стільки ж, скільки процес. Тримається в модулі навмисно:
# змінна, яку ніхто не тримає, нічим не загрожує сама по собі (ядро звільняє
# мьютекс при завершенні процесу в будь-якому разі), але явне посилання
# показує намір - замок беруть НА ВЕСЬ час роботи, а не на мить перевірки.
_held_handle = None


def is_free(lock_name):
    """Бере замок. True - ця копія єдина; False - програма вже працює.

    Помилка, якої ми не очікували, запуск НЕ блокує: краще пустити другу
    копію, ніж не дати людині відкрити програму взагалі через несподіванку
    в системному виклику.
    """
    global _held_handle
    if not _IS_WINDOWS:
        return True

    for prefix in ("Global\\", "Local\\"):
        handle = _kernel32.CreateMutexW(None, True, prefix + lock_name)
        error = ctypes.get_last_error()
        if handle and error == _ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        if handle:
            _held_handle = handle
            return True
        if error != _ERROR_ACCESS_DENIED:
            # Щось несподіване, а не відмова доступу - працюємо далі без
            # замка, як і сказано вище.
            return True
        # Відмова доступу в Global\ означає або закритий політикою ПК
        # глобальний простір імен - тоді пробуємо Local\ наступним колом,
        # або мьютекс, який тримає копія з іншими правами ("Запуск від
        # імені адміністратора").
    # Обидва простори відмовили доступом - замок таки тримає чужа копія.
    return False


def _process_image_name(pid):
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(512)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value)
    finally:
        _kernel32.CloseHandle(handle)


def show_running_copy():
    """Піднімає вікно вже запущеної копії. True, якщо вікно знайдено.

    Шукається головне вікно процесу з тим самим іменем .exe, що й у нас:
    видиме, без власника (тобто справжнє головне вікно, а не діалог) і не
    наше власне.
    """
    if not _IS_WINDOWS:
        return False

    own_pid = os.getpid()
    own_image = os.path.basename(sys.executable)
    found = []

    def visit(hwnd, _param):
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _user32.GetWindow(hwnd, _GW_OWNER):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == own_pid:
            return True
        if _process_image_name(pid.value).lower() != own_image.lower():
            return True
        found.append(hwnd)
        return False

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    if not found:
        return False

    hwnd = found[0]
    _user32.ShowWindow(hwnd, _SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    return True


def report_second_copy(silent=False):
    """Поведінка "В": сказати людині й підняти вікно першої копії.

    silent=True - нічого не показувати (перезапуск сторожем: там вікно
    посеред екрана було б несподіванкою, а не поясненням).
    """
    if silent:
        show_running_copy()
        return

    import tkinter as tk

    wants_window = {"value": False}
    root = tk.Tk()
    root.title("Программа уже запущена")
    root.resizable(False, False)
    root.configure(bg="#ffffff")

    frame = tk.Frame(root, bg="#ffffff", padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame, text="Программа уже работает на этом компьютере.",
        font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#1f2937", justify="left",
    ).pack(anchor="w")
    tk.Label(
        frame,
        text=(
            "Второй запуск не нужен: две копии писали бы в один склад,\n"
            "и каждая продажа попала бы в таблицу дважды."
        ),
        font=("Segoe UI", 10), bg="#ffffff", fg="#4b5563", justify="left",
    ).pack(anchor="w", pady=(8, 16))

    buttons = tk.Frame(frame, bg="#ffffff")
    buttons.pack(anchor="e")

    def close_only():
        root.destroy()

    def show_and_close():
        wants_window["value"] = True
        root.destroy()

    tk.Button(
        buttons, text="Закрыть", font=("Segoe UI", 10), width=12,
        command=close_only,
    ).pack(side="left", padx=(0, 8))
    show_button = tk.Button(
        buttons, text="Показать окно", font=("Segoe UI", 10, "bold"), width=16,
        command=show_and_close,
    )
    show_button.pack(side="left")

    root.bind("<Return>", lambda _event: show_and_close())
    root.bind("<Escape>", lambda _event: close_only())
    root.protocol("WM_DELETE_WINDOW", close_only)

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry("+%d+%d" % (x, y))
    root.attributes("-topmost", True)
    show_button.focus_set()
    root.mainloop()

    if wants_window["value"]:
        show_running_copy()
