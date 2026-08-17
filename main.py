"""Точка входу застосунку (GUI + Telegram-бот) — тонкий транспортний шар.

Повна карта проєкту (опис кожного файлу, історія фіч і рішень) винесена в
PROJECT_MAP.md — оновлюй ЙОГО щоразу, коли якийсь шматок переїжджає в
окремий файл чи з'являється нова істотна фіча. Цей файл сам лишається
маленьким: TelegramBotWorker(TelegramDialogMixin) — HTTP до Telegram,
polling-цикл, надсилання повідомлень; складання ExcelSqliteStore/
TelegramBotWorker/ExcelViewerApp і запуск GUI/бота через
`if __name__ == "__main__"`.
"""

import tkinter as tk
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from paths import (
    FILE_PATH,
    SETTINGS_PATH,
    TELEGRAM_OFFSET_PATH,
)
from settings import (
    DEFAULT_SETTINGS,
    EXCEL_SYNC_MODES,
    REQUEST_PROCESSING_MODES,
)
from warehouse_data import (
    ANTISEPTIC_DIRTY_MARKER,
    ExcelSqliteStore,
    maybe_create_scheduled_snapshot,
    sync_antiseptic_to_excel,
    sync_sheets_to_excel,
)
from telegram_dialog import TelegramApiError, TelegramDialogMixin

TELEGRAM_POLL_TIMEOUT = 5


# Тонкий транспортний шар: HTTP до Telegram, polling-цикл, надсилання
# повідомлень/документів. Уся діалогова логіка (питання, підтвердження,
# розбір тексту, бізнес-правила) — у TelegramDialogMixin (telegram_dialog.py),
# який цей клас успадковує. self.foo() всередині TelegramDialogMixin працює
# так само, як і раніше — Python шукає метод по MRO незалежно від файлу.
class TelegramBotWorker(TelegramDialogMixin):
    # --- Життєвий цикл воркера + читання налаштувань ---
    def __init__(self, token, db_path, settings_path=None, status_callback=None):
        self.token = token
        self.db_path = Path(db_path)
        self.settings_path = Path(settings_path) if settings_path else SETTINGS_PATH
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.thread = None
        self.offset = None
        # Нагляд за ботом (watchdog у gui.py): last_loop_tick рухається щоразу
        # на початку ітерації циклу (успіх чи оброблена помилка - байдуже) і
        # виявляє "завис" (потік технічно живий, але реальної роботи нема);
        # last_success_at рухається лише на РЕАЛЬНО успішному getMe/getUpdates
        # і показує людині, коли був останній справжній контакт з Telegram.
        self.last_loop_tick = None
        self.last_success_at = None
        # Форма введення даних (Telegram Mini App) - актуальна публічна
        # адреса тунелю (gui.py керує тунелем і сама виставляє це поле;
        # порожньо, доки тунель не піднявся, - _webapp_keyboard тоді просто
        # не показує кнопку форми, старий текстовий шлях лишається єдиним).
        self.webapp_public_url = ""

        # Задача користувача (2026-08-13): "якщо активна робота в боті -
        # відразу не перезаписує... якщо простій 5-10с - оновлюємо... якщо
        # немає простою і з моменту відліку пройшло 30с - оновлюється
        # примусово" - раніше apply_sale_operation/apply_income_operation/
        # apply_writeoff_operation/антисептування синхронно переписували
        # ВЕСЬ лист Excel ПІД ЧАС відповіді боту на КОЖНУ операцію (повний
        # delete_rows+append+save_workbook, ще й create_excel_backup) - і
        # мовчки здавались, якщо файл у цей момент відкритий в Excel.
        # Тепер операції лише позначають лист "брудним" (mark_excel_dirty),
        # а справжній запис відкладається й пакетується тут-таки, у тому
        # самому поллінг-циклі (_run) - жодного окремого потоку не треба,
        # getUpdates і так повертається щонайбільше раз на
        # TELEGRAM_POLL_TIMEOUT секунд, достатня частота для 8с/30с порогів.
        self._excel_dirty_sheets = set()
        self._excel_dirty_since = None
        self._excel_last_activity_at = None
        self._excel_sync_lock = threading.Lock()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=TELEGRAM_POLL_TIMEOUT + 2)

    # Аудит коду: offset жив лише в пам'яті — при перезапуску (крах чи сам
    # watchdog) новий воркер стартував з offset=None, і Telegram міг
    # повторно доставити вже оброблене повідомлення (жодного підтвердження
    # ще не було відправлено). Тепер зберігається на диск одразу після
    # кожного зрушення (_run, нижче) — той самий тихий "ігноруємо помилку
    # файлу" підхід, що вже є для щоденних знімків БД у цій самій функції.
    def _load_persisted_offset(self):
        try:
            data = json.loads(TELEGRAM_OFFSET_PATH.read_text(encoding="utf-8"))
            return data.get("offset")
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def _persist_offset(self):
        try:
            tmp_path = TELEGRAM_OFFSET_PATH.with_suffix(TELEGRAM_OFFSET_PATH.suffix + ".tmp")
            tmp_path.write_text(json.dumps({"offset": self.offset}), encoding="utf-8")
            os.replace(tmp_path, TELEGRAM_OFFSET_PATH)
        except OSError:
            pass

    def _set_status(self, text):
        if self.status_callback:
            self.status_callback(text)

    def _request_processing_mode(self):
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        mode = loaded.get("request_processing_mode", DEFAULT_SETTINGS["request_processing_mode"])
        return mode if mode in {code for code, _, _ in REQUEST_PROCESSING_MODES} else DEFAULT_SETTINGS["request_processing_mode"]

    def _excel_sync_mode(self):
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        mode = loaded.get("excel_sync_mode", DEFAULT_SETTINGS["excel_sync_mode"])
        return mode if mode in EXCEL_SYNC_MODES else DEFAULT_SETTINGS["excel_sync_mode"]

    # Задача користувача: "якщо простій в роботі 5-10 сек - тоді оновлюємо".
    _EXCEL_SYNC_IDLE_SECONDS = 8
    # "якщо немає вікна простою... і з моменту відліку пройшло 30 сек -
    # оновлюється автоматично" - жорстка стеля, щоб безперервна робота не
    # відкладала запис нескінченно.
    _EXCEL_SYNC_MAX_WAIT_SECONDS = 30

    # Викликається з apply_sale_operation/apply_income_operation/apply_
    # writeoff_operation/антисептування (warehouse_data.py) замість
    # синхронного sync_sheets_to_excel - лише позначає лист і скидає
    # відлік простою, сама операція завершується й відповідає боту миттю.
    def mark_excel_dirty(self, sheet_names):
        with self._excel_sync_lock:
            now = time.monotonic()
            self._excel_dirty_sheets.update(sheet_names)
            if self._excel_dirty_since is None:
                self._excel_dirty_since = now
            self._excel_last_activity_at = now

    # Задача користувача: "кнопка таймер оновлень... секунди відліку до
    # наступного оновлення... реальну цифру маю бачити вживу" - client_app.py
    # опитує це раз в секунду для живого зворотного відліку. None = немає
    # незбережених змін (нічого не заплановано).
    def excel_sync_seconds_remaining(self):
        with self._excel_sync_lock:
            if not self._excel_dirty_sheets:
                return None
            now = time.monotonic()
            idle_deadline = self._excel_last_activity_at + self._EXCEL_SYNC_IDLE_SECONDS
            max_deadline = self._excel_dirty_since + self._EXCEL_SYNC_MAX_WAIT_SECONDS
            return max(0.0, min(idle_deadline, max_deadline) - now)

    # Викликається раз на ітерацію поллінг-циклу (_run, нижче) - той самий
    # store, що й решта цієї ітерації (той самий потік, тому спільне
    # з'єднання SQLite безпечне - на відміну від client_app.py, де фонове
    # оновлення Excel мусило відкривати ОКРЕМЕ з'єднання для свого потоку).
    def _excel_sync_tick(self, store):
        with self._excel_sync_lock:
            if not self._excel_dirty_sheets:
                return
            now = time.monotonic()
            idle = now - self._excel_last_activity_at
            total = now - self._excel_dirty_since
            if idle < self._EXCEL_SYNC_IDLE_SECONDS and total < self._EXCEL_SYNC_MAX_WAIT_SECONDS:
                return
            pending = set(self._excel_dirty_sheets)
            self._excel_dirty_sheets.clear()
            self._excel_dirty_since = None
            self._excel_last_activity_at = None
        # sync_antiseptic_to_excel пише ІНШУ логіку (шапка+підсумкові рядки),
        # ніж загальний sync_sheets_to_excel - маркер веде до окремого шляху.
        sync_antiseptic = ANTISEPTIC_DIRTY_MARKER in pending
        sheets = [name for name in pending if name != ANTISEPTIC_DIRTY_MARKER]
        failed = set()
        if sheets:
            try:
                sync_sheets_to_excel(store, sheets)
            except (PermissionError, OSError, RuntimeError):
                failed.update(sheets)
        if sync_antiseptic:
            try:
                sync_antiseptic_to_excel(store)
            except (PermissionError, OSError, RuntimeError):
                failed.add(ANTISEPTIC_DIRTY_MARKER)
        if failed:
            # Не вдалось (файл відкритий тощо) - позначаємо знову брудним,
            # спробуємо ще раз на наступному тіку (той самий 8с/30с цикл).
            self.mark_excel_dirty(failed)

    # "якщо користувач оновив вручну - тоді таймер відліку скидається на
    # початок" - викликається з gui.py.sync_excel_manually після успішного
    # ручного оновлення, щоб не робити зайвий повтор одразу після нього.
    def clear_excel_dirty(self, sheet_names):
        with self._excel_sync_lock:
            self._excel_dirty_sheets.difference_update(sheet_names)
            if not self._excel_dirty_sheets:
                self._excel_dirty_since = None
                self._excel_last_activity_at = None

    # --- Telegram: HTTP-запити, polling-цикл, надсилання повідомлень ---
    def _api(self, method, params=None, timeout=30):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise self._telegram_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise TelegramApiError(description=f"Нет соединения с Telegram: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TelegramApiError(description="Telegram не ответил вовремя. Попробуйте еще раз.") from exc
        except UnicodeDecodeError as exc:
            # Свіжий пере-аудит (2026-08-02, Minor #4): це стається ДО
            # json.loads - помилка форми відповіді, спійманна нижче, цього
            # не покриє.
            raise TelegramApiError(description="Telegram вернул нечитаемый ответ.") from exc

        result = json.loads(payload)
        # Свіжий пере-аудит (2026-08-02, Minor #4): якщо Telegram (чи проксі
        # перед ним) колись поверне JSON-значення верхнього рівня, що НЕ dict
        # (масив/рядок/число), result.get(...) кидав би сирий AttributeError -
        # не заплановано жодним except у циклі поллінгу (лише (TelegramApiError,
        # json.JSONDecodeError)) - той самий клас "вбиває весь цикл", що й
        # інші знахідки цього ж пере-аудиту.
        if not isinstance(result, dict):
            raise TelegramApiError(description="Некорректный ответ Telegram API.")
        if not result.get("ok"):
            raise TelegramApiError(description=result.get("description", "Telegram API error"))
        return result.get("result")

    def _telegram_http_error(self, exc):
        description = ""
        retry_after = None
        try:
            payload = exc.read().decode("utf-8")
            body = json.loads(payload)
            # Свіжий пере-аудит (2026-08-02, Minor #4): те саме "не-dict
            # тіло" припущення, що й у _api() вище - якщо body не dict,
            # просто лишаємо description/retry_after порожніми (як і зараз
            # для будь-якої іншої помилки парсингу), замість сирого
            # AttributeError на .get(...).
            if isinstance(body, dict):
                description = body.get("description", "")
                # Аудит коду: 429-відповідь Telegram несе parameters.retry_after
                # (скільки секунд НАСПРАВДІ чекати) — раніше геть не читалось,
                # бот завжди чекав фіксовані 5с, незалежно від реальної підказки.
                retry_after = (body.get("parameters") or {}).get("retry_after")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            description = str(exc)
        return TelegramApiError(exc.code, description, retry_after=retry_after)

    def _human_error(self, exc):
        if isinstance(exc, TelegramApiError):
            description = exc.description or ""
            if exc.status_code == 409 or "Conflict" in description:
                return (
                    "этот бот уже запущен в другом месте или предыдущее подключение "
                    "еще завершается. Закройте другие копии программы, подождите несколько секунд "
                    "и подключите Telegram еще раз."
                )
            if exc.status_code == 401 or "Unauthorized" in description:
                return "токен неправильный или устарел. Проверьте txt-файл с ключом."
            if exc.status_code == 404 or "Not Found" in description:
                return "Telegram не нашел этого бота. Проверьте токен в txt-файле."
            if "Нет соединения" in description or "Немає з'єднання" in description:
                return description
            return description or "произошла неизвестная ошибка Telegram."
        return str(exc)

    # Аудит коду: раніше опитування завжди чекало фіксовані 5с між спробами
    # при будь-якій помилці — навіть коли Telegram (429, забагато запитів)
    # явно підказав через parameters.retry_after, скільки секунд чекати
    # насправді (_telegram_http_error вище тепер це поле читає). Невідома
    # помилка чи відсутнє/некоректне значення — той самий типовий орієнтир
    # 5с, що й раніше.
    def _retry_wait_seconds(self, exc):
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return retry_after
        return 5

    def _run(self):
        # Важлива знахідка нового аудиту (28.07.2026, #7): раніше
        # ExcelSqliteStore(...) стояв ПЕРЕД єдиним try/except нижче -
        # пошкоджена/заблокована БД кидала sqlite3.OperationalError/
        # DatabaseError НЕЗАХИЩЕНО, потік тихо гинув, _set_status ніколи не
        # викликався (екран лишався зі старим "Telegram запускается..."
        # назавжди, watchdog нескінченно намагався перепідключити без
        # жодної видимої причини). Ловимо тут, тим самим стилем
        # повідомлення, що вже є нижче.
        try:
            store = ExcelSqliteStore(self.db_path)
        except Exception as exc:
            self._set_status(f"Telegram не подключен: не удалось открыть базу данных: {self._human_error(exc)}")
            return
        try:
            maybe_create_scheduled_snapshot(self.db_path)
        except Exception:
            # Розширено з OSError - create_db_snapshot теж використовує
            # sqlite3 (не лише файлові операції), тож сам знімок міг убити
            # потік так само незахищено, як і відкриття БД вище.
            pass
        try:
            bot_info = self._api("getMe", timeout=10)
            username = bot_info.get("username", "bot")
            self._set_status(f"Telegram подключен: @{username}")
            self.last_success_at = datetime.now().isoformat()
            if self.offset is None:
                self.offset = self._load_persisted_offset()

            while not self.stop_event.is_set():
                self.last_loop_tick = datetime.now().isoformat()
                try:
                    maybe_create_scheduled_snapshot(self.db_path)
                except Exception:
                    # Розширено з OSError - той самий "database is locked"
                    # ризик, що вже виправлений для виклику перед циклом
                    # вище (sqlite3.OperationalError - не OSError).
                    pass
                try:
                    updates = self._api(
                        "getUpdates",
                        {
                            "timeout": TELEGRAM_POLL_TIMEOUT,
                            "offset": self.offset or "",
                            "allowed_updates": json.dumps(["message"]),
                        },
                        timeout=TELEGRAM_POLL_TIMEOUT + 10,
                    )
                    self.last_success_at = datetime.now().isoformat()
                except (TelegramApiError, json.JSONDecodeError) as exc:
                    self._set_status(f"Telegram ожидает соединения: {self._human_error(exc)}")
                    self.stop_event.wait(self._retry_wait_seconds(exc))
                    continue

                for update in updates:
                    # Свіжий пере-аудит (2026-08-02, Minor #4): пряма
                    # індексація update["update_id"] кидала б сирий
                    # KeyError на одному зіпсованому апдейті РАНІШЕ, ніж
                    # offset посунеться повз нього - на відміну від решти
                    # цього циклу (message.get(...)/chat.get(...) вже й так
                    # захисні). Пропускаємо лише ЦЕЙ один апдейт, обробка
                    # решти в тому самому батчі триває нормально.
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if update_id is None:
                        continue
                    self.offset = update_id + 1
                    self._persist_offset()
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    text = (message.get("text") or "").strip()
                    web_app_data = message.get("web_app_data")
                    # Форма введення даних (Telegram Mini App) - надсилає
                    # message.web_app_data замість text, той самий chat/from,
                    # тож увесь захист нижче (except Exception, не даємо
                    # одному поганому повідомленню вбити цикл поллінгу)
                    # застосовуємо тим самим стилем, паралельно до тексту.
                    if chat_id and web_app_data:
                        try:
                            reply = self._build_reply_pipeline_web_app(web_app_data, store, message)
                            self._send_reply(chat_id, reply)
                        except Exception as exc:
                            self._set_status(f"Ошибка обработки данных формы: {self._human_error(exc)}")
                            try:
                                self._send_reply(
                                    chat_id,
                                    "⚠️ Произошла внутренняя ошибка при обработке данных формы. "
                                    "Попробуйте еще раз или напишите Отмена.",
                                )
                            except Exception:
                                pass
                    elif chat_id and text:
                        # Реальний ризик з аудиту: раніше виняток тут (пошкоджений
                        # payload, sqlite3.OperationalError від блокування,
                        # TelegramApiError від самого _send_reply) вилітав аж до
                        # зовнішнього except Exception нижче — той лише виставляє
                        # статус і дає _run() завершитись, тобто ОДНЕ погане
                        # повідомлення назавжди зупиняло бота (без авто-
                        # перезапуску). _build_reply_pipeline і так логує помилку
                        # (add_action_log, status="error") перед власним raise —
                        # тут лише не даємо їй піти далі й вбити цикл поллінгу.
                        try:
                            reply = self._build_reply_pipeline(text, store, message)
                            # Задача користувача (2026-08-17): у груповому
                            # чаті без дозволеної команди _build_reply_pipeline
                            # тепер навмисно повертає None ("бот мовчить") -
                            # без цієї перевірки _send_reply(chat_id, None)
                            # падав би всередині _send_message/_split_message_
                            # text (len(None)).
                            if reply is not None:
                                self._send_reply(chat_id, reply)
                        except Exception as exc:
                            self._set_status(f"Ошибка обработки сообщения: {self._human_error(exc)}")
                            try:
                                self._send_reply(
                                    chat_id,
                                    "⚠️ Произошла внутренняя ошибка при обработке сообщения. "
                                    "Попробуйте еще раз или напишите Отмена.",
                                )
                            except Exception:
                                pass

                self._excel_sync_tick(store)
        except Exception as exc:
            self._set_status(f"Telegram не подключен: {self._human_error(exc)}")
        finally:
            store.close()

    # Аудит коду (перевірка охоплення Fix #13): retry_after уже парситься й
    # використовується для getUpdates у поллінг-циклі, але надсилання
    # відповіді користувачу (sendMessage/sendDocument) досі не мало жодної
    # повторної спроби на 429 — перше ж влучення в rate-limit одразу летіло
    # в зовнішній except Exception (_run) і тихо губило відповідь: користувач
    # бачив або типову "внутренняя ошибка", або взагалі нічого, якщо й
    # запасне повідомлення теж впало на той самий ліміт. Один повтор (не
    # нескінченний цикл — це синхронна обробка одного повідомлення, а не
    # фоновий поллінг) з тим самим _retry_wait_seconds покриває звичайний
    # короткий rate-limit; якщо не вдалось і вдруге — виняток іде далі як і
    # раніше, той самий фолбек-шлях у _run().
    def _send_with_retry(self, send_once):
        try:
            return send_once()
        except TelegramApiError as exc:
            if exc.status_code != 429:
                raise
            self.stop_event.wait(self._retry_wait_seconds(exc))
            return send_once()

    # Задача користувача (2026-08-17): "Сумма позиции: ... зроби грубим
    # шрифтом" - parse_mode необов'язковий і ЗА ЗАМОВЧУВАННЯМ вимкнений
    # (None) - лишає всі ІНШІ, вже наявні виклики _send_message (меню,
    # довідка, помилки і т.д. по всьому боту) абсолютно без змін: жоден із
    # них не екранований під HTML, і випадкове "<"/"&" будь-де в них
    # тепер зламало б відправку, якби HTML був увімкнений глобально. Лише
    # 4 функції звіту операцій (apply_sale/income/writeoff/antiseptic_
    # operation, warehouse_data.py) явно передають parse_mode="HTML" і
    # заздалегідь екранують УВЕСЬ динамічний текст через html.escape().
    def _send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        chunks = self._split_message_text(text, 3500)
        for index, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup is not None and index == len(chunks) - 1:
                payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
            self._send_with_retry(lambda payload=payload: self._api("sendMessage", payload, timeout=10))

    def _split_message_text(self, text, limit):
        # Розбиває по межі рядка (останній "\n" перед лімітом), а не по
        # жорсткому числу символів — інакше довге число чи слово, що якраз
        # припадає на межу шматка, розрізається навпіл між двома
        # повідомленнями (напр. підсумок "419,2948" ставав "41" + "9,2948").
        if len(text) <= limit:
            return [text]
        chunks = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks or [""]

    def _send_reply(self, chat_id, reply):
        if isinstance(reply, dict) and reply.get("type") == "document":
            self._send_document(
                chat_id,
                Path(reply["path"]),
                caption=reply.get("caption", ""),
                reply_markup=reply.get("reply_markup"),
            )
            return
        if isinstance(reply, dict) and reply.get("type") == "message":
            self._send_message(
                chat_id,
                reply.get("text", ""),
                reply_markup=reply.get("reply_markup"),
                parse_mode=reply.get("parse_mode"),
            )
            return
        self._send_message(chat_id, reply)

    def _send_document(self, chat_id, file_path, caption="", reply_markup=None):
        fields = {
            "chat_id": str(chat_id),
            "caption": caption,
        }
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self._send_with_retry(
            lambda: self._api_multipart("sendDocument", fields, "document", file_path, timeout=30)
        )


def _show_startup_splash(root):
    # Задача користувача (2026-08-15): "не справжнє, а бутафорію... хай
    # завантаження відбувається, а вікно хай буде відокремлене... щоб не
    # лагало і плавно ходило" - ExcelViewerApp тепер сама переносить
    # реально повільну частину (Excel-імпорт) у фоновий потік, тож
    # root.mainloop() ніколи не блокується - indeterminate progressbar
    # анімується САМА, вбудованим Tk-таймером (.start() нижче), без жодного
    # ручного втручання звідси. Це "бутафорія" в буквальному сенсі - смуга
    # рухається постійно й рівномірно, зовсім не відображаючи реальний
    # відсоток завантаження (такого поняття тут більше нема).
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    width, height = 320, 130
    x = (splash.winfo_screenwidth() - width) // 2
    y = (splash.winfo_screenheight() - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")
    splash.configure(bg="#1f2937")
    tk.Label(
        splash, text="AI Automation", font=("Segoe UI", 14, "bold"),
        fg="white", bg="#1f2937",
    ).pack(pady=(22, 6))
    tk.Label(
        splash, text="Загрузка, подождите...", font=("Segoe UI", 10),
        fg="#9ca3af", bg="#1f2937",
    ).pack()
    progress = ttk.Progressbar(splash, mode="indeterminate", length=240)
    progress.pack(pady=16)
    splash.attributes("-topmost", True)
    splash.update()
    return splash, progress


if __name__ == "__main__":
    from gui import ExcelViewerApp

    root = tk.Tk()
    root.withdraw()
    splash, splash_progress = _show_startup_splash(root)
    splash_progress.start(12)

    def _on_app_ready():
        splash_progress.stop()
        splash.destroy()
        root.deiconify()

    app = ExcelViewerApp(root, FILE_PATH, on_ready=_on_app_ready)
    root.mainloop()
