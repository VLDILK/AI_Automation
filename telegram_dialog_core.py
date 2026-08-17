"""Спільна основа TelegramDialogMixin: єдина точка входу (_build_reply_pipeline/_build_reply_by_mode/_build_reply), дозволи (_current_user_role/_require_permission), "Назад"/скасування, message_context, генеричні keyboard/reply-хелпери. Частина розбиття telegram_dialog.py на кілька файлів за доменом (2026) - див. telegram_dialog.py для повної карти."""

import html
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import permissions as perm
import webapp_server
from paths import DISPLAY_SETTINGS_PATH, REPORT_BROADCAST_CHAT_ID, SETTINGS_PATH
from settings import DisplaySettingsStore, SettingsStore
from utils import (
    _display_bot_number,
    _normalize_phrase,
    _number_value,
)
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
    INCOME_VOLUME_TOLERANCE,
    antiseptic_rows,
    income_item_size,
    income_report_rows,
    low_stock_report_rows,
    operation_template_entries,
    resolve_operation_for_payload,
    row_value,
    sale_position_text,
    sales_rows,
    warehouse_rows,
    writeoff_report_rows,
)


class TelegramApiError(Exception):
    def __init__(self, status_code=None, description="", retry_after=None):
        super().__init__(description)
        self.status_code = status_code
        self.description = description
        # Аудит коду: 429 (забагато запитів) від Telegram завжди чекав
        # фіксовані 5с (main.py._run) незалежно від того, скільки сервер
        # НАСПРАВДІ попросив зачекати (parameters.retry_after у відповіді).
        # Поле необов'язкове — заповнюється лише _telegram_http_error, коли
        # відповідь дійсно містить це поле; для решти помилок лишається None.
        self.retry_after = retry_after


class CoreDialogMixin:
    # Задача користувача (2026-08-17): спершу - "бот в чаті не має
    # реагувати взагалі на повідомлення... а в загальному він лише видає
    # інформацію" (звідси спершу лишався виняток для /status/sheets/
    # first/chatid), а потім уточнення - "в загальному чаті бот лише може
    # надавати звіт про успішно виконану операцію і все. ніяких більше
    # повідомлень. тільки звіт, без панелей, без нічого." Тобто ГРУПОВИЙ
    # чат отримує ЛИШЕ проактивний push через _notify_report_broadcast
    # (окремий шлях, не через цей pipeline) - жодної відповіді на
    # ВХІДНЕ повідомлення, включно з діагностичними командами. /chatid
    # і решта лишаються робочими лише в ПРИВАТНОМУ чаті з ботом.
    def _build_reply_pipeline(self, text, store, message=None):
        context = self._message_context(message)
        if context.get("chat_type") in ("group", "supergroup"):
            return None
        started_at = datetime.now()
        mode = self._request_processing_mode()
        user_preference = None
        pending_before = None
        pending_after = None
        command_code = None
        reply = None
        status = "success"
        error_text = ""

        try:
            if context["chat_id"] is not None and context["user_id"] is not None:
                if self._is_real_telegram_user(context):
                    user_preference = store.get_user_preference(context["user_id"])
                    # Задача користувача: "щоб програма бачила користувачів,
                    # які вже хоча б натиснули кнопку розпочати чи старт
                    # бота" - без ручного введення ID адміністратором. Лише
                    # приватні чати - "Персонал" це конкретні співробітники,
                    # а не будь-хто з групового чату, куди міг бути доданий бот.
                    if context.get("chat_type") == "private":
                        store.ensure_bot_user_seen(
                            context["user_id"], context["username"], context["full_name"]
                        )
                pending_before = store.get_pending_operation(context["chat_id"], context["user_id"])
                selection_result = self._maybe_handle_bot_selection(text, store, context, user_preference)
                if selection_result:
                    reply = selection_result["reply"]
                    mode = selection_result.get("mode") or mode
                    command_code = selection_result.get("command") or "bot_selection"
                    pending_after = store.get_pending_operation(context["chat_id"], context["user_id"])
                    status = self._pipeline_status(reply, pending_before, pending_after)
                    return reply
                if self._is_real_telegram_user(context) and not user_preference:
                    user_preference = store.get_user_preference(context["user_id"])

            if user_preference and user_preference.get("bot_mode"):
                mode = user_preference["bot_mode"]

            command_code = (
                pending_before["operation_type"]
                if pending_before
                else self._command_hint_by_mode(text, store, mode)
            )
            reply = self._build_reply_by_mode(text, store, message, mode)
            if context["chat_id"] is not None and context["user_id"] is not None:
                pending_after = store.get_pending_operation(context["chat_id"], context["user_id"])
            status = self._pipeline_status(reply, pending_before, pending_after)
            return reply
        except Exception as exc:
            status = "error"
            error_text = str(exc)
            raise
        finally:
            try:
                duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
                store.add_action_log(
                    "telegram_message",
                    {
                        "pipeline_version": "telegram_router_v2",
                        "mode": mode,
                        "status": status,
                        "duration_ms": duration_ms,
                        "telegram": context,
                        "incoming_text": self._sanitize_secret_text(text),
                        "recognized_command": command_code or "unknown",
                        "pending_before": self._pending_log_payload(pending_before),
                        "pending_after": self._pending_log_payload(pending_after),
                        "reply": self._reply_log_payload(reply),
                        "error": error_text,
                    },
                )
            except Exception:
                pass

    # Задача користувача: "від людини нічого не ховай. а то людина буде
    # думати що може помилилась" - справжня "не розпізнав" гілка (обидва
    # відомі маркери відсутні) досі МАЄ бути прозорою: показує, що саме
    # реально прийшло (клієнт/адреса/оплата/кількість позицій), а не голе
    # "Активной операции нет." - людина бачить, що дані НЕ загубились, і
    # може судити сама, надсилати ще раз чи писати текстом.
    _WEBAPP_UNRECOGNIZED_ECHO_LABELS = (
        ("client", "Клиент"),
        ("address", "Адрес выгрузки"),
        ("payment_method", "Способ оплаты"),
    )

    def _webapp_unrecognized_submission_reply(self, store, submitted):
        lines = ["Не удалось определить операцию по полученным данным."]
        for key, label in self._WEBAPP_UNRECOGNIZED_ECHO_LABELS:
            value = submitted.get(key)
            if value:
                lines.append(f"{label}: {value}")
        positions = submitted.get("positions")
        if isinstance(positions, list) and positions:
            lines.append(f"Позиций получено: {len(positions)}")
        lines.append("Попробуйте отправить форму еще раз или напишите данные текстом.")
        return self._with_main_menu("\n".join(lines), store)

    # Форма введення даних (Telegram Mini App) - друга точка входу, паралельна
    # _build_reply_pipeline вище (main.py викликає одну АБО іншу, залежно від
    # того, чи в апдейті text, чи web_app_data). Навмисно НЕ через
    # _parse_income_message/_extract_sale_fields (regex-парсер, який форма й
    # має обійти) - лише merge готових, уже позначених ключем значень у
    # payload, потім та сама continue_operation, що й вільний текст.
    def _build_reply_pipeline_web_app(self, web_app_data, store, message=None):
        context = self._message_context(message)
        started_at = datetime.now()
        pending_before = None
        pending_after = None
        reply = None
        status = "success"
        error_text = ""
        raw_data = web_app_data.get("data") if isinstance(web_app_data, dict) else None

        try:
            if context["chat_id"] is None or context["user_id"] is None:
                reply = self._no_active_operation_reply(store)
                return reply
            pending_before = store.get_pending_operation(context["chat_id"], context["user_id"])
            try:
                submitted = json.loads(raw_data) if raw_data else None
            except (TypeError, ValueError):
                submitted = None
            if not isinstance(submitted, dict):
                reply = self._with_main_menu(
                    "Не удалось прочитать данные формы. Попробуйте еще раз или напишите данные текстом.",
                    store,
                )
                return reply
            # Задача користувача: "3 окремі можливості створювати свої
            # шаблони" - кнопка "Сохранить шаблон" усередині форми надсилає
            # ОКРЕМИЙ, самодостатній payload (не пов'язаний з жодною
            # pending-операцією) - перевіряється РАНІШЕ звичайного розгалуження
            # нижче, бо збереження шаблону не залежить від того, чи є зараз
            # активна операція.
            if submitted.get("save_template"):
                reply = self._save_operation_template_reply(store, context, submitted)
                return reply
            # Задача користувача: "завжди може видалити історію чи шаблон по
            # 1 рядку" - той самий термінальний "закриває Mini App і одразу
            # перевідкриває форму" патерн, що й save_template вище.
            if submitted.get("delete_template"):
                return self._delete_operation_template_reply(store, context, submitted)
            if submitted.get("delete_recent"):
                return self._delete_operation_recent_use_reply(store, context, submitted)
            if not pending_before:
                # Пряме відкриття форми "РЕАЛИЗАЦИЯ (форма)"/"СПИСАНИЕ
                # (форма)" з головного меню (web_app-кнопка на самій кнопці
                # меню) НІКОЛИ не проходить через проміжний бот-крок, що
                # ставив би pending заздалегідь - Telegram відкриває Mini App
                # одразу по тапу, без жодного запиту до бота. Розпізнаємо
                # саме такі форми за унікальним маркером їх payload -
                # category_operation_id (одно-позиційне подання - продаж БЕЗ
                # кошика, антисептирование чи списання) АБО positions (кошик
                # продажу, задача користувача "Продолжить продажу") - жодна
                # інша форма їх не надсилає. РЕАЛЬНИЙ баг, знайдений
                # користувачем: коли кошик додав "positions" як альтернативу,
                # цю перевірку не розширили - будь-яке пряме відкриття з
                # кошиком (>1 позиції чи навіть одна, зібрана через
                # "Продолжить продажу") мовчки потрапляло в "Активной
                # операции нет.", хоча дані реально прийшли й були
                # розпізнавані. Списання (той самий маркер, нова форма) далі
                # розрізняється від продажу за РЕАЛЬНИМ kind категорії
                # (_continue_direct_open_webapp_submission), не за окремим
                # клієнтським прапорцем.
                if "category_operation_id" not in submitted and not submitted.get("positions"):
                    reply = self._webapp_unrecognized_submission_reply(store, submitted)
                    return reply
                reply = self._continue_direct_open_webapp_submission(store, context, submitted)
            else:
                reply = self._continue_operation_with_webapp_payload(store, context, pending_before, submitted)
            pending_after = store.get_pending_operation(context["chat_id"], context["user_id"])
            status = self._pipeline_status(reply, pending_before, pending_after)
            return reply
        except Exception as exc:
            status = "error"
            error_text = str(exc)
            raise
        finally:
            try:
                duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
                store.add_action_log(
                    "telegram_web_app_data",
                    {
                        "status": status,
                        "duration_ms": duration_ms,
                        "telegram": context,
                        "pending_before": self._pending_log_payload(pending_before),
                        "pending_after": self._pending_log_payload(pending_after),
                        "reply": self._reply_log_payload(reply),
                        "error": error_text,
                    },
                )
            except Exception:
                pass

    # Ключі, які в payload["rows"] належать конкретному РЯДКУ розміру (не
    # усій операції одразу) - товщина/ширина/довжина/кількість. Порода/
    # клієнт/адреса/ціна/спосіб оплати - завжди одноразові поля на всю
    # операцію (перевірено _warehouse_row_matches: payload["breed"] звіряється
    # з КОЖНИМ рядком, а не бере окреме значення на рядок).
    _WEBAPP_PER_ROW_FIELD_KEYS = {"thickness", "width", "length", "quantity"}
    _WEBAPP_NUMBER_FIELD_KEYS = {"thickness", "width", "length", "quantity", "price_per_unit", "volume"}
    _WEBAPP_DECIMAL_FIELD_KEYS = {"price_per_unit", "volume"}
    # Реальна знахідка перед імплементацією: наївний обхід усього
    # list_operation_fields(operation_id) підходив би далеко не всім потокам
    # - напр. sale_antiseptic узагалі НЕ має поля-запиту "payment_method" у
    # конфігурації (задокументований розрив - сам крок питає його жорстко,
    # незалежно від "Дії"), а МАЄ купу чисто облікових полів (дата/№ послуги/
    # статус оплати/№ документа/приход наличных/по банку/отражение/
    # ответственный), які НІКОЛИ не питаються інтерактивно. Тому форма
    # будується з явного, перевіреного переліку ключів на кожен потік (той
    # самий набір, що реально гейтиться в _income_missing_fields/
    # _flat_checklist_missing_fields/_continue_antiseptic_operation_step), а
    # не з усієї конфігурації "Дії" — мітки (коли поле реально настроєне)
    # усе одно беруться з list_operation_fields, лишаючи перейменування через
    # "Дії" робочим.
    _WEBAPP_IDENTITY_DIMENSION_KEYS = ("product", "breed", "condition", "thickness", "width", "length", "quantity")
    _WEBAPP_SALE_FLAT_KEYS = ("client", "address", "price_per_unit", "payment_method")
    _WEBAPP_ANTISEPTIC_KEYS = ("client", "address", "volume", "price_per_unit", "payment_method")
    _WEBAPP_DEFAULT_LABELS = {
        "product": "Товар",
        "breed": "Порода",
        "condition": "Тип продукта",
        "thickness": "Толщина, мм",
        "width": "Ширина, мм",
        "length": "Длина, мм",
        "quantity": "Количество, шт",
        "client": "Клиент",
        "address": "Адрес выгрузки",
        "price_per_unit": "Цена",
        "payment_method": "Способ оплаты",
        "volume": "Объем, м3",
        "comment": "Причина списания",
    }
    # Порода/товщина/ширина/довжина — поля-запити форми, для яких має сенс
    # "оберіть із уже наявних" (сталий, повторюваний набір значень на
    # категорію) — на відміну від quantity/price/volume, які щоразу реально
    # різні. Дропдаун опціональний (allow_custom у _webapp_form_context) -
    # нова порода чи довжина для нового товару й далі вводиться вручну.
    _WEBAPP_CATEGORY_SELECT_KEYS = ("breed", "thickness", "width", "length")

    # Той самий фільтр за категорією (product+condition), що вже перевірений
    # у _warehouse_row_matches/_similar_sale_rows — condition_values рахується
    # ОДИН раз (не на кожен рядок), rows порівнюються через _text_equal
    # БЕЗЗАСТЕРЕЖНО (навіть коли condition порожній — то самий аудит-фікс, що
    # вже застосований для _warehouse_row_matches). Порожній список — legit
    # (категорія ще без жодного рядка складу) — викликач тоді фолбекає на
    # звичайне текстове/числове поле, а не на дропдаун без опцій.
    # require_balance (Задача користувача, "не показувати того з розмірів...
    # чого немає в наявності"): продаж/списання МОЖУТЬ мати справу лише з
    # тим, що РЕАЛЬНО є на складі ЗАРАЗ (balance_qty > 0) - рядок, що існує,
    # але вже повністю розпродано, для них не легітимна опція. Приход - НЕ
    # передає це (require_balance=False, default) - нова поставка на вже
    # порожню позицію - нормальний, очікуваний сценарій.
    def _existing_dimension_values(self, store, product, condition, field_key, numeric=True, require_balance=False):
        if not product:
            return []
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return []
        column_index = columns.get(field_key)
        if column_index is None:
            return []
        balance_qty_idx = columns.get("balance_qty")
        condition_values = self._existing_product_type_values(rows, columns.get("product"))
        matching_rows = []
        for row_id, row in rows:
            row_product, product_suffix_type = self._split_product_condition(
                row_value(row, columns["product"]), condition_values
            )
            if not self._text_equal(row_product, product):
                continue
            # Реальний баг користувача (розмір 175x225x6500 не з'являвся у
            # дропдауні "Толщина"/"Ширина"/"Длина", хоча реально є на складі
            # й видно в "Данные"): склад містить ДВА формати того самого
            # товару - старий ("Продукт"="Доска AD" одним рядком) і новий
            # ("Продукт"="Доска" окремо + "Состояние"="AD"). Тип рядка
            # визначався ЛИШЕ через суфікс у тексті "Продукт" - для нового
            # формату суфікса нема, рядок мовчки не проходив фільтр.
            # condition is None означає "у цієї категорії взагалі немає
            # виміру типу" (ОСБ/Вагонка - prefill без "condition") - тоді
            # перевірку пропускаємо цілком (як і "Данные", де категорійні
            # чипи фільтрують лише за товаром), а не змушуємо "Состояние"
            # бути порожнім: там часто лежить плейсхолдер на кшталт "N/A"
            # чи навіть випадкове старе значення, які не повинні впливати.
            if condition is not None:
                row_condition = row_value(row, columns.get("condition")) or product_suffix_type
                if not self._text_equal(row_condition, condition):
                    continue
            if require_balance and _number_value(row_value(row, balance_qty_idx)) <= 0:
                continue
            matching_rows.append((row_id, row))
        return sorted(self._existing_values(matching_rows, column_index, numeric=numeric))

    # Реальний баг, знайдений користувачем (скріншот "???"): товщина/ширина/
    # довжина - 3 ОКРЕМІ, незалежні дропдауни (_existing_dimension_values
    # вище викликається окремо на кожен ключ), тож можна обрати товщину і
    # ширину, які САМІ ПО СОБІ реальні, але РАЗОМ на складі не існують
    # (25мм існує лише з шириною 120, ширина 150 - лише з товщиною 47).
    # Ця функція повертає РЕАЛЬНІ трійки (товщина, ширина, довжина), що
    # РЕАЛЬНО зустрічаються РАЗОМ - webapp/app.js звужує дропдауни ширини/
    # довжини під уже обрану товщину (і ширину), щоб неможливу комбінацію
    # неможливо було зібрати. Не прив'язано до породи (той самий рівень
    # деталізації, що вже має _existing_dimension_values вище - жодного
    # нового фільтра, який там ще не застосовується).
    # Задача користувача (2026-08-08, скріншот мега-форми): (1) розмір має
    # ховатись у дропдауні, якщо саме для ОБРАНОЇ породи його залишок 0 (не
    # лише "десь узагалі є з якоюсь породою", як було раніше); (2) поле
    # "Количество, шт" показує поруч клікабельну цифру поточного залишку
    # САМЕ для обраної породи+розміру. Обидві потреби покриває ОДНА й та
    # сама структура — кожен combo тепер [порода, товщина, ширина, довжина,
    # залишок] (5 елементів, було 3 без породи й без залишку) — окрема
    # "stock_balances"-мапа (рядковий ключ "порода|т|ш|д") більше НЕ
    # потрібна взагалі й прибрана з ctx: те саме число вже є тут, без
    # дублювання даних другою структурою (свідомо компактніше — той самий
    # принцип, що й real-bug фікс URL-розміру мега-форми того ж дня).
    # Реальний баг живого тестування (2026-08-08, скріншот): balance_qty ("на
    # складе N шт") і реальний фізичний вимір (balance_volume/area/linear)
    # можуть розійтись для конкретного рядка складу — приклад: Ель/Доска AD/
    # 25x150x6000 показувала "На складе: 7428 шт", але реального об'єму на
    # складі лише 58,265 м3 (за розміром рядка це ~2589 шт, не 7428) —
    # клік по хінту вписував 7428, і бот одразу відмовляв "недостаточно
    # объема". Користувач підтвердив: "там в кубатурі помилка... потрібно
    # щось придумати для таких випадків". Не намагаємось виправити саму
    # розбіжність у даних (це може бути застаріле ручне введення) — натомість
    # цифра, яку бачить і на яку може клацнути людина, ЗАВЖДИ обмежена тим,
    # що РЕАЛЬНО можна продати без відмови сервера: min(balance_qty,
    # скільки штук поміщається у balance_volume/area/linear для цього
    # ТОЧНОГО розміру) — той самий двоступеневий інваріант, що вже перевіряє
    # _sale_stock_issue при самому записі. Товари без фізичного виміру (ОСБ)
    # не мають другого обмеження — balance_qty лишається як є.
    def _sellable_combo_quantity(self, product, columns, row, thickness, width, length, balance_qty):
        measure_key = self._row_measure_kind(
            {"product": product}, {"thickness": thickness, "width": width}
        )
        if measure_key is None:
            return balance_qty
        measure_column = self._MEASURE_KIND_BALANCE_COLUMN.get(measure_key)
        measure_idx = columns.get(measure_column) if measure_column else None
        if measure_idx is None:
            return balance_qty
        piece_amount = self._piece_measure(
            {"thickness": thickness, "width": width, "length": length}, measure_key
        )
        if piece_amount <= 0:
            return balance_qty
        balance_measure = _number_value(row_value(row, measure_idx))
        measure_limited_qty = int((balance_measure + INCOME_VOLUME_TOLERANCE) / piece_amount + 1e-9)
        return min(balance_qty, max(0, measure_limited_qty))

    def _existing_dimension_combos(self, store, product, condition, require_balance=False):
        if not product:
            return []
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return []
        breed_idx = columns.get("breed")
        thickness_idx = columns.get("thickness")
        width_idx = columns.get("width")
        length_idx = columns.get("length")
        balance_qty_idx = columns.get("balance_qty")
        if None in (breed_idx, thickness_idx, width_idx, length_idx):
            return []
        condition_values = self._existing_product_type_values(rows, columns.get("product"))
        seen = set()
        combos = []
        for _row_id, row in rows:
            row_product, product_suffix_type = self._split_product_condition(
                row_value(row, columns["product"]), condition_values
            )
            if not self._text_equal(row_product, product):
                continue
            # Той самий фікс, що й у _existing_dimension_values вище - "Состояние"
            # читається НАПРЯМУ, з фолбеком на суфікс лише коли колонка
            # порожня; коли condition взагалі None (ОСБ/Вагонка - категорія
            # без виміру типу) перевірка пропускається цілком, а не вимагає
            # "Состояние" бути порожнім (там часто лежить плейсхолдер/старе
            # значення, що не повинно впливати).
            if condition is not None:
                row_condition = row_value(row, columns.get("condition")) or product_suffix_type
                if not self._text_equal(row_condition, condition):
                    continue
            balance_qty = _number_value(row_value(row, balance_qty_idx)) if balance_qty_idx is not None else 0
            breed = row_value(row, breed_idx)
            thickness = row_value(row, thickness_idx)
            width = row_value(row, width_idx)
            length = row_value(row, length_idx)
            if breed in (None, "") or thickness in (None, "") or width in (None, "") or length in (None, ""):
                continue
            balance = self._sellable_combo_quantity(
                product, columns, row, thickness, width, length, balance_qty
            )
            if require_balance and balance <= 0:
                continue
            key = (
                str(breed).strip(),
                _display_bot_number(thickness),
                _display_bot_number(width),
                _display_bot_number(length),
            )
            if key in seen:
                continue
            seen.add(key)
            combos.append([key[0], key[1], key[2], key[3], balance])
        return combos

    # Клієнт/адреса — на відміну від породи/розмірів — НЕ прив'язані до
    # конкретної категорії товару (той самий клієнт купує будь-що), тож без
    # category-scoping: комбінуємо ПРОДАЖА МАТЕРИАЛА + АНТИСЕПТИРОВАНИЕ
    # (клієнтська база спільна для обох потоків), дедуплікуючи вручну через
    # _normalize_phrase (кожен окремий виклик _existing_values дедуплікує
    # лише В МЕЖАХ свого аркуша, не між двома).
    def _existing_sales_field_values(self, store, field_key):
        values = []
        for rows_fn in (sales_rows, antiseptic_rows):
            try:
                _, columns, rows = rows_fn(store)
            except sqlite3.Error:
                continue
            column_index = columns.get(field_key)
            if column_index is None:
                continue
            values.extend(self._existing_values(rows, column_index, numeric=False))
        unique = []
        seen = set()
        for value in values:
            key = _normalize_phrase(value)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return sorted(unique)

    # РЕАЛЬНИЙ баг, знайдений користувачем ("баг.", скріншот): форма завжди
    # перепитує ВСІ 4 per-row поля разом (thickness/width/length/quantity -
    # _WEBAPP_IDENTITY_DIMENSION_KEYS, ніколи підмножину "лише те, що
    # бракує" - перевірено _webapp_form_context, per_row-поля не мають
    # "вже відомо -> сховати" скорочення). Тому подання форми ЗАВЖДИ несе
    # ПОВНИЙ, самодостатній рядок - і має ЗАМІНЯТИ payload["rows"], а не
    # додаватись до нього. Раніше APPEND лишав СТАРИЙ (нез'наЙдений на
    # складі) рядок у payload["rows"][0] - виправлена форма (нові
    # товщина/ширина/довжина) дописувалась ЯК ДРУГИЙ рядок, а перевірка
    # "не найдено на складе" зупинялась на першому, застарілому рядку,
    # ніколи не доходячи до щойно виправленого.
    def _merge_webapp_submission(self, payload, submitted):
        rows = submitted.get("rows")
        if isinstance(rows, list) and rows:
            payload["rows"] = [dict(row) for row in rows if isinstance(row, dict)]
        for key, value in submitted.items():
            if key in ("operation_id", "rows"):
                continue
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, ""):
                payload[key] = value
        # Задача користувача: "або користувач йде покроково через бот, або
        # користувач все робить через застосунок... все інше в новому режимі
        # через БОТа неможна пропускати" - _merge_webapp_submission
        # викликається ВИКЛЮЧНО з боку подання форми (ніколи з вільного
        # тексту чату), тож це єдине надійне місце позначити payload як
        # "прийшов з форми" - решта конвеєра (_continue_sale_operation_impl/
        # _continue_writeoff_operation_step/_continue_income_operation_impl)
        # звіряється з цим прапорцем, щоб замінити будь-яку інтерактивну
        # Так/Ні-розвилку на один термінальний результат.
        payload["_from_webapp_form"] = True

    # Термінальна відповідь для форм-режиму: жодної reply_markup, що чекає
    # подальшого тексту - лише головне меню (людина повертається у форму
    # заново, якщо хоче спробувати ще раз), і pending прибирається одразу,
    # щоб застарілий payload не міг "зловити" наступне випадкове повідомлення.
    def _webapp_form_terminal_reply(self, store, context, text, parse_mode=None):
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu(text, store, parse_mode=parse_mode)

    # Задача користувача (скріншот): "додай ще повернення в форму. і у всіх
    # подібних місцях... знизу третя кнопка" - на екрані підтвердження
    # (форма-режим) поруч із Так/Ні тепер є "Вернуться в форму", що знову
    # відкриває ТУ САМУ мега-форму (Приход/Реализация/Списание), а не старий
    # текстовий "Редактировать" (той самий принцип, що й увесь цей захід:
    # виправлення в формі-режимі мають лишатись у формі). Антисептирование
    # живе як категорія всередині sale-мегаформи (operation_kind="antiseptic"
    # ставить _continue_sale_all_in_one_submission), тож теж повертається
    # через _start_sale_all_in_one_reply. Свідомо НЕ намагаємось передати
    # вже введені значення назад у форму - "known"-поля мега-форми й так
    # завжди будуються лише з prefill_json категорії (product/condition), не
    # з поточного payload, тому повторне заповнення однаково почалось би
    # "з чистого аркуша" для цього розміру/категорії.
    _WEBAPP_FORM_RETURN_LABEL = "Вернуться в форму"

    def _reopen_webapp_form_reply(self, store, context, payload):
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        kind = payload.get("operation_kind")
        if kind == "income":
            return self._start_income_all_in_one_reply(store, context, resume_payload=payload)
        if kind == "writeoff":
            return self._start_writeoff_all_in_one_reply(store, context, resume_payload=payload)
        # Реальний баг (той самий вузол): kind="antiseptic" сюди теж
        # доходить (telegram_dialog_antiseptic.py), але раніше не мав ГІЛКИ
        # взагалі - падав у sale-форму замість форми антисептирования.
        if kind == "antiseptic":
            return self._start_antiseptic_all_in_one_reply(store, context, resume_payload=payload)
        # Задача користувача (скріншот "Вернуться в форму"): продажа - єдина
        # мега-форма з кошиком, тож єдина, де є що "повернути" -
        # resume_payload несе вже введені позиції/клієнта/адресу/оплату.
        return self._start_sale_all_in_one_reply(store, context, resume_payload=payload)

    def _continue_operation_with_webapp_payload(self, store, context, pending, submitted):
        # "Реализация (форма)" - на відміну від усіх інших форм, тут ЩЕ НЕ
        # відомо, яка саме операція (категорія дерева/антисептирование) -
        # її обирає сама форма (поле "Категория"), тож дисптечеризація за
        # operation_type тут ще НЕ застосовна - окрема гілка ПЕРЕД нею.
        if pending.get("status") == "sale_all_in_one":
            return self._continue_sale_all_in_one_submission(store, context, submitted)
        if pending.get("status") == "writeoff_all_in_one":
            return self._continue_writeoff_all_in_one_submission(store, context, submitted)
        if pending.get("status") == "income_all_in_one":
            return self._continue_income_all_in_one_submission(store, context, submitted)
        if pending.get("status") == "antiseptic_all_in_one":
            return self._continue_antiseptic_all_in_one_submission(store, context, submitted)
        operation_type = pending["operation_type"]
        payload = pending["payload"]
        self._merge_webapp_submission(payload, submitted)
        if operation_type == "add_income":
            return self._continue_income_operation(store, context, payload)
        if operation_type == "stock_sale":
            return self._continue_sale_operation(store, context, payload)
        if operation_type == "stock_writeoff":
            return self._continue_writeoff_operation_impl(store, context, payload)
        if operation_type == "antiseptic_service":
            return self._continue_antiseptic_operation_step(store, context, payload)
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu(
            "Не удалось применить данные формы к текущей операции. Отправьте запрос заново.", store
        )

    # Форма сама надсилає лише "category_operation_id" (реальний id рядка
    # bot_operations, обраний у select-і "Категория") - НІКОЛИ product/
    # condition напряму з JS (клієнту не довіряємо жодних текстових значень,
    # що впливають на пошук рядка складу - лише сам ідентифікатор категорії,
    # який тут звіряється з живою БД, той самий принцип, що вже застосований
    # у _category_from_text для звичайного чатового шляху). kind=='service'
    # (АНТИСЕПТИРОВАНИЕ) веде зовсім іншим payload-шляхом (client/address/
    # volume/price_per_unit/payment_method, без рядків/розмірів) - інакше
    # (дерев'яні категорії) переиспользує _new_sale_payload + вже наявний
    # _merge_webapp_submission (той самий код, що й для одно-категорійної
    # форми), лише з product/condition, підставленими СЕРВЕРНО за prefill_json.
    def _continue_sale_all_in_one_submission(self, store, context, submitted):
        # Пряме відкриття форми з головного меню (web_app-кнопка) пропускає
        # серверний round-trip, який раніше єдиний перевіряв право доступу
        # (_start_sale_all_in_one_reply) - гість технічно може відкрити й
        # заповнити форму, але саме ЗАПИС має лишитись заблокованим тут.
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        submitted = dict(submitted) if isinstance(submitted, dict) else {}
        # Задача користувача: "застосунок не відразу відправляє звіт, а
        # збирає інформацію" - webapp/app.js тепер накопичує кілька позицій
        # У СЕБЕ (жодного контакту з ботом до фінального "Отправить") і
        # надсилає їх РАЗОМ як positions[] - окрема, нова гілка, СТАРИЙ шлях
        # (одна категорія напряму в submitted, без positions) лишається без
        # жодної зміни нижче (і далі потрібен для АНТИСЕПТИРОВАНИЕ, яке
        # мультипозиційність не підтримує взагалі).
        # Задача користувача (2026-08-14): "так же як це реалізовано в
        # реалізації" - прихід тепер ТЕЖ може прийти як positions[], тому
        # клієнт (webapp/app.js) явно позначає "positions_kind" - без цього
        # позиції приходу помилково пішли б у продажний обробник.
        positions_data = submitted.get("positions")
        if isinstance(positions_data, list) and positions_data:
            if submitted.get("positions_kind") == "income":
                return self._continue_income_all_in_one_multi_position(store, context, submitted, positions_data)
            return self._continue_sale_all_in_one_multi_position(store, context, submitted, positions_data)
        operation_id = submitted.pop("category_operation_id", None)
        operation = store.get_operation(operation_id) if operation_id is not None else None
        if operation is None:
            return self._with_main_menu(
                "Не удалось определить категорию из формы. Начните продажу заново.", store
            )
        _op_id, _code, kind, _requires_identity, _label, _parent, prefill_json, *_rest = operation
        if kind == "service":
            payload = {
                "operation_kind": "antiseptic",
                "original_text": "",
                "user": {
                    "id": context["user_id"],
                    "username": context["username"],
                    "full_name": context["full_name"],
                },
                "confirmed_new": [],
            }
            for key in ("client", "address", "volume", "price_per_unit", "payment_method"):
                value = submitted.get(key)
                if isinstance(value, str):
                    value = value.strip()
                if value not in (None, ""):
                    payload[key] = value
            return self._prepend_reply_text(
                "Антисептирование.", self._continue_antiseptic_operation_step(store, context, payload)
            )
        prefill = json.loads(prefill_json) if prefill_json else {}
        payload = self._new_sale_payload("", context)
        payload["product"] = prefill.get("product")
        if prefill.get("condition"):
            payload["condition"] = prefill.get("condition")
        self._merge_webapp_submission(payload, submitted)
        self._record_webapp_operation_use(store, "sale", operation_id, payload)
        return self._continue_sale_operation(store, context, payload)

    # Кожен елемент positions[] - {category_operation_id, breed, rows,
    # price_per_unit} (та сама форма даних, що й одно-позиційне подання,
    # лише category_operation_id тепер РІЗНИЙ на кожну позицію - webapp/
    # app.js зібрала їх у себе через "Добавить позицию" БЕЗ жодного
    # контакту з ботом до цього моменту). Останню позицію беремо як
    # "поточну" (payload), решту складаємо в payload["completed_positions"]
    # (той самий формат, що й _archive_current_sale_position_and_reset) -
    # далі йде ЦІЛКОМ той самий, уже перевірений _continue_sale_operation,
    # що й для одно-позиційної продажі чи старого покрокового шляху.
    def _continue_sale_all_in_one_multi_position(self, store, context, submitted, positions_data):
        resolved = []
        for position in positions_data:
            if not isinstance(position, dict):
                continue
            position = dict(position)
            operation_id = position.pop("category_operation_id", None)
            operation = store.get_operation(operation_id) if operation_id is not None else None
            if operation is None:
                return self._with_main_menu(
                    "Не удалось определить категорию одной из позиций. Начните продажу заново.", store
                )
            _op_id, _code, kind, _requires_identity, _label, _parent, prefill_json, *_rest = operation
            if kind == "service":
                return self._with_main_menu(
                    "Антисептирование нельзя объединить с другими позициями в одной продаже. "
                    "Оформите его отдельным подтверждением.",
                    store,
                )
            # Реальний ризик (аудит коду, 2026-08-14): це єдина перевірка kind
            # тут була - "не service", але НЕ "дійсно sale". category_operation_id
            # приходить від клієнта (webapp/app.js), а positions_kind (за яким
            # _continue_sale_all_in_one_submission обирає sale- чи income-гілку
            # вище) - теж лише клієнтський прапорець, що НЕ звіряється з
            # реальним kind кожної позиції. Без цієї перевірки id категорії
            # "прихід"/"списання" міг би дійти сюди (positions_kind не
            # виставлено чи виставлено неправильно) і бути записаним як
            # ПРОДАЖ - з ціною/клієнтом і списанням зі складу для того, що
            # мало бути надходженням. _continue_income_all_in_one_multi_position
            # (нижче) вже мала симетричну перевірку "kind != income" - тут її
            # просто бракувало.
            if kind != "sale":
                return self._with_main_menu(
                    "Не удалось определить категорию одной из позиций. Начните продажу заново.", store
                )
            prefill = json.loads(prefill_json) if prefill_json else {}
            item_payload = self._new_sale_payload("", context)
            item_payload["product"] = prefill.get("product")
            if prefill.get("condition"):
                item_payload["condition"] = prefill.get("condition")
            self._merge_webapp_submission(item_payload, position)
            # _continue_sale_operation нижче рахує об'єм/площу/мп лише для
            # ОСТАННЬОЇ (поточної) позиції - решта йдуть напряму в
            # completed_positions, обходячи цей крок, тож рахуємо ТУТ явно
            # для кожної позиції (інакше total_amount/показ об'єму лишається
            # "0" для всіх, крім останньої).
            amount_issue = self._prepare_income_amounts(item_payload)
            if amount_issue:
                return self._with_main_menu(
                    "Не удалось рассчитать одну из позиций. Проверьте введённые данные и начните продажу заново.",
                    store,
                )
            # Реальний баг (живий скріншот користувача): _continue_sale_operation
            # нижче резолвить row_id ЛИШЕ для останньої (поточної) позиції -
            # решта йшли прямо в completed_positions БЕЗ жодного _resolve_sale_
            # rows, тож item["row_id"] лишався None. apply_sale_operation
            # (warehouse_data.py) для row_id=None рядків НЕ показує розмір у
            # підсумковому повідомленні (пропускає весь блок звірки залишку
            # ВЗАГАЛІ - тихо НЕ списує зі складу!). Резолвимо тут явно, для
            # КОЖНОЇ позиції, а не лише для останньої.
            match_issue = self._resolve_sale_rows(store, item_payload)
            if match_issue:
                item = match_issue.get("item")
                text = match_issue.get("message") or (
                    f"Не найдено на складе: {sale_position_text(item_payload, item)}."
                    if item is not None
                    else "Не найдено на складе. Проверьте размер и породу."
                )
                text += "\n\nИсправьте размер этой позиции в форме и отправьте её заново."
                return self._webapp_form_terminal_reply(store, context, text)
            stock_issue = self._sale_stock_issue(store, item_payload)
            if stock_issue:
                text = self._sale_stock_issue_text(item_payload, stock_issue)
                text += "\n\nУменьшите количество в форме и отправьте её заново."
                return self._webapp_form_terminal_reply(store, context, text)
            self._record_webapp_operation_use(store, "sale", operation_id, item_payload)
            resolved.append(item_payload)

        if not resolved:
            return self._with_main_menu("Не удалось определить ни одной позиции. Начните продажу заново.", store)

        payload = resolved[-1]
        completed_positions = []
        for item_payload in resolved[:-1]:
            position_entry = {
                field: item_payload[field] for field in self._SALE_POSITION_FIELDS if field in item_payload
            }
            position_entry["total_amount"] = self._sale_total_amount(item_payload)
            completed_positions.append(position_entry)
        payload["completed_positions"] = completed_positions
        common = {key: value for key, value in submitted.items() if key != "positions"}
        self._merge_webapp_submission(payload, common)
        return self._continue_sale_operation(store, context, payload)

    # Задача користувача (2026-08-14): "щоб міг продовжувати приход і
    # внести кілька різних позицій. так же як це реалізовано в реалізації" -
    # той самий принцип, що й _continue_sale_all_in_one_multi_position вище,
    # адаптований під прихід: (1) немає перевірки достатності залишку -
    # прихід ДОДАЄ на склад, забирати нема з чого; (2) _resolve_income_rows
    # (не _resolve_sale_rows) - повертає рядок помилки АБО None (інший
    # контракт, не {item, message}); (3) прихід дозволяє "рядок не
    # знайдено -> створити новий" (create_new=True), тому МАЄ сенс НЕ
    # вимагати обов'язкового збігу, на відміну від продажу.
    def _continue_income_all_in_one_multi_position(self, store, context, submitted, positions_data):
        resolved = []
        for position in positions_data:
            if not isinstance(position, dict):
                continue
            position = dict(position)
            operation_id = position.pop("category_operation_id", None)
            operation = store.get_operation(operation_id) if operation_id is not None else None
            if operation is None:
                return self._with_main_menu(
                    "Не удалось определить категорию одной из позиций. Начните приход заново.", store
                )
            _op_id, _code, kind, _requires_identity, _label, _parent, prefill_json, *_rest = operation
            if kind != "income":
                return self._with_main_menu(
                    "Эту позицию нельзя объединить с приходом в одной форме. "
                    "Оформите её отдельным подтверждением.",
                    store,
                )
            prefill = json.loads(prefill_json) if prefill_json else {}
            item_payload = self._new_income_payload("", context)
            item_payload["product"] = prefill.get("product")
            if prefill.get("condition"):
                item_payload["condition"] = prefill.get("condition")
            self._merge_webapp_submission(item_payload, position)
            # _continue_income_operation нижче рахує об'єм/площу/мп лише для
            # ОСТАННЬОЇ (поточної) позиції - решта йдуть напряму в
            # completed_positions, обходячи цей крок, тож рахуємо ТУТ явно
            # для кожної позиції (той самий фікс, що вже має продаж вище).
            amount_issue = self._prepare_income_amounts(item_payload)
            if amount_issue:
                return self._with_main_menu(
                    "Не удалось рассчитать одну из позиций. Проверьте введённые данные и начните приход заново.",
                    store,
                )
            match_issue = self._resolve_income_rows(store, item_payload)
            if match_issue:
                return self._webapp_form_terminal_reply(store, context, match_issue)
            self._record_webapp_operation_use(store, "income", operation_id, item_payload)
            resolved.append(item_payload)

        if not resolved:
            return self._with_main_menu("Не удалось определить ни одной позиции. Начните приход заново.", store)

        payload = resolved[-1]
        completed_positions = []
        for item_payload in resolved[:-1]:
            position_entry = {
                field: item_payload[field] for field in self._INCOME_POSITION_FIELDS if field in item_payload
            }
            completed_positions.append(position_entry)
        payload["completed_positions"] = completed_positions
        common = {key: value for key, value in submitted.items() if key != "positions"}
        self._merge_webapp_submission(payload, common)
        return self._continue_income_operation(store, context, payload)

    # Будує JSON-контекст форми з ЯВНОГО переліку ключів (field_keys —
    # один із _WEBAPP_*_KEYS вище, обраний викликачем за видом операції),
    # а не всієї конфігурації "Дії" (див. коментар над константами). Мітка
    # береться з list_operation_fields, коли поле реально настроєне (адмін
    # може перейменувати) — інакше типовий підпис із _WEBAPP_DEFAULT_LABELS.
    # Одноразові поля, значення яких у payload вже є, йдуть у "known" (лише
    # показ), решта — в "fields" (реальні поля вводу форми).
    # restrict_to_existing_combos (Задача користувача): продаж/списання
    # МОЖУТЬ мати справу лише з тим, що РЕАЛЬНО є на складі (не можна
    # продати чи списати розмір, якого фізично немає) - для них дропдауни
    # товщини/ширини/довжини звужуються під уже обрані значення (webapp/
    # app.js's wireDimensionCascade). Приход - НАВПАКИ: нова, ще НЕ наявна
    # на складі комбінація - це нормальний, очікуваний сценарій (саме так
    # з'являється нова позиція), тож приход передає False і бачить УСІ
    # унікальні значення без жодного звуження.
    # Задача користувача (скріншот "Проверьте данные"): "хочу мати змогу
    # вибрати колір фону, колір тексту, розмір тексту... який жирний, який
    # ні" — читає display_settings_<user>.json (той самий файл, що вже
    # керує "Формат кнопок" у десктоп-програмі, DisplaySettingsStore, НЕ
    # SettingsStore/system/settings.json - це навмисно РІЗНИЙ, суто
    # косметичний файл). Порожній колір ("") - сигнал для webapp/app.js "не
    # перевизначати, лишити колір теми Telegram". Свіжий store на кожен
    # виклик (той самий принцип, що вже є для excel_source.py/SettingsStore
    # у telegram_dialog_reports.py:1248) - бот-потік читає файл, який
    # редагує GUI-процес, без кешу.
    def _webapp_style_ctx(self):
        display = DisplaySettingsStore(DISPLAY_SETTINGS_PATH)
        return {
            "confirm_heading_text": display.get("webapp_confirm_heading_text"),
            "style": {
                "title_color": display.get("webapp_title_color"),
                "title_size": display.get("webapp_title_size"),
                "title_bold": display.get("webapp_title_bold"),
                "category_color": display.get("webapp_category_color"),
                "category_size": display.get("webapp_category_size"),
                "category_bold": display.get("webapp_category_bold"),
                "body_color": display.get("webapp_body_color"),
                "body_size": display.get("webapp_body_size"),
                "body_bold": display.get("webapp_body_bold"),
                "common_color": display.get("webapp_common_color"),
                "common_size": display.get("webapp_common_size"),
                "common_bold": display.get("webapp_common_bold"),
                "card_bg_color": display.get("webapp_card_bg_color"),
                "entry_bg_color": display.get("webapp_entry_bg_color"),
                "page_bg_color": display.get("webapp_page_bg_color"),
                "group1_text_color": display.get("webapp_group1_text_color"),
                "group1_border_color": display.get("webapp_group1_border_color"),
                "group1_fill_color": display.get("webapp_group1_fill_color"),
                "group2_text_color": display.get("webapp_group2_text_color"),
                "group2_border_color": display.get("webapp_group2_border_color"),
                "group2_fill_color": display.get("webapp_group2_fill_color"),
                "group3_text_color": display.get("webapp_group3_text_color"),
                "group3_border_color": display.get("webapp_group3_border_color"),
                "group3_fill_color": display.get("webapp_group3_fill_color"),
            },
            "field_label_styles": display.get("webapp_field_label_styles") or {},
        }

    def _webapp_form_context(self, store, operation_id, field_keys, payload, title, restrict_to_existing_combos=True):
        configured_labels = {}
        if operation_id is not None:
            configured_labels = {field[2]: field[3] for field in store.list_operation_fields(operation_id)}
        known = {}
        fields = []
        for field_key in field_keys:
            label = configured_labels.get(field_key) or self._WEBAPP_DEFAULT_LABELS.get(field_key, field_key)
            per_row = field_key in self._WEBAPP_PER_ROW_FIELD_KEYS
            if not per_row:
                existing_value = payload.get(field_key)
                if existing_value not in (None, ""):
                    known[label] = existing_value
                    continue
            entry = {"key": field_key, "label": label, "per_row": per_row}
            # Причина списання - єдине необов'язкове поле форми (той самий
            # бізнес-принцип, що вже діє в чатовому потоці списання:
            # _writeoff_missing_prompt/_income_missing_fields ніколи не
            # вимагають "comment"). required=False - вже наявний, готовий
            # механізм на боці webapp/app.js (buildFieldElement/collectField),
            # просто досі жодне поле його не встановлювало.
            if field_key == "comment":
                entry["required"] = False
            if field_key in self._WEBAPP_NUMBER_FIELD_KEYS:
                entry["numeric"] = True
            if field_key == "payment_method":
                entry["type"] = "select"
                entry["options"] = [option[1] for option in store.list_payment_method_options()]
            elif field_key in self._WEBAPP_CATEGORY_SELECT_KEYS:
                numeric = field_key in self._WEBAPP_NUMBER_FIELD_KEYS
                existing_values = self._existing_dimension_values(
                    store,
                    payload.get("product"),
                    payload.get("condition"),
                    field_key,
                    numeric=numeric,
                    require_balance=restrict_to_existing_combos,
                )
                if existing_values:
                    entry["type"] = "select"
                    entry["options"] = (
                        [_display_bot_number(value) for value in existing_values]
                        if numeric
                        else existing_values
                    )
                    entry["allow_custom"] = True
                else:
                    entry["type"] = "number" if numeric else "text"
            elif field_key in ("client", "address"):
                existing_values = self._existing_sales_field_values(store, field_key)
                if existing_values:
                    entry["type"] = "select"
                    entry["options"] = existing_values
                    entry["allow_custom"] = True
                else:
                    entry["type"] = "text"
            elif field_key in self._WEBAPP_NUMBER_FIELD_KEYS:
                entry["type"] = "number"
                if field_key in self._WEBAPP_DECIMAL_FIELD_KEYS:
                    entry["decimal"] = True
            else:
                entry["type"] = "text"
            fields.append(entry)
        return {
            "operation_id": operation_id,
            "title": title,
            "known": known,
            "fields": fields,
            **self._webapp_style_ctx(),
            # Задача користувача: "потрібно щоб відразу рахувало і показувало
            # одиницю вимірювання" - той самий JS-двійник row_measure_kind, що
            # й у мега-формах (де ключ "product" уже додається на кожну
            # категорію) - тут категорія одна, тож ключ на верхньому рівні.
            "product": payload.get("product"),
            # Задача користувача: "кожна неіснуюча позиция може додати нову
            # позицию, якщо користувач це підтвердить" (приход) — на
            # відміну від продажу/списання, де ручне (allow_custom) значення
            # для розміру майже завжди помилка (не можна продати/списати
            # те, чого нема на складі). Явний прапорець замість клієнтського
            # вгадування "чи це приход" — webapp/app.js показує підтвердження
            # нового розміру лише коли True.
            "allow_new_positions": not restrict_to_existing_combos,
            # Задача користувача: "сама програма не має випустити із
            # неіснуючим залишком" - webapp/app.js звіряє введену кількість
            # проти РЕАЛЬНОГО залишку САМЕ породи+розміру, читаючи його
            # прямо з combo[4] нижче (окрема "stock_balances"-мапа більше не
            # потрібна - real bug 2026-08-08, дублювання даних роздувало
            # web_app-URL мега-форми понад межу, яку Telegram мовчки
            # відхиляє). Приход не консьюмер залишку, тож поки що НЕ
            # переиспользує цю саму інформацію.
            "dimension_combos": (
                self._existing_dimension_combos(
                    store, payload.get("product"), payload.get("condition"), require_balance=True
                )
                if restrict_to_existing_combos
                else []
            ),
        }

    # Кнопка форми додається ПОВЕРХ уже наявної reply-клавіатури (Telegram
    # дозволяє web_app і на звичайній KeyboardButton, не лише на inline) -
    # текст чек-листа й "Отмена"/"Редактировать" лишаються без жодної зміни,
    # форма - лише ДОДАНА альтернатива. Якщо тунель ще не піднявся
    # (self.webapp_public_url порожній) - повертає fallback_keyboard як є,
    # старий текстовий шлях лишається єдиним, без жодної помилки.
    def _webapp_keyboard(
        self, store, operation_id, field_keys, payload, title, fallback_keyboard, restrict_to_existing_combos=True
    ):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return fallback_keyboard
        ctx = self._webapp_form_context(
            store, operation_id, field_keys, payload, title, restrict_to_existing_combos=restrict_to_existing_combos
        )
        if not ctx["fields"]:
            return fallback_keyboard
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/index.html?t={token}"
        rows = [[{"text": "Заполнить данные", "web_app": {"url": url}}]]
        rows.extend(fallback_keyboard.get("keyboard", []))
        keyboard = dict(fallback_keyboard)
        keyboard["keyboard"] = rows
        return keyboard

    # "Приход (форма)" - той самий мега-формат, що вже мають РЕАЛИЗАЦИЯ
    # (форма)/СПИСАНИЕ (форма): категорія обирається прямо в самій формі,
    # без чатових кроків. Найпростіша з трьох - без ціни/клієнта/адреси/
    # оплати (прихід їх не має взагалі) і без кошика (одна позиція за раз,
    # як і списання - "Продолжить" тут не потрібен, кожен прихід окремий).
    # Єдина СВІДОМА відмінність від sale/writeoff: restrict_to_existing_
    # combos=False - на відміну від продажу/списання (де можна оперувати
    # ЛИШЕ тим, що вже реально є на складі), прихід - це якраз спосіб
    # завести НОВИЙ розмір/породу, яких на складі ще нема, тож дропдауни
    # мають показувати ПОВНИЙ список (не звужений до існуючих комбінацій).
    _WEBAPP_ALL_IN_ONE_INCOME_KEYS = ("breed", "thickness", "width", "length", "quantity")

    # Задача користувача ("роби і для приходу/списання"): та сама
    # "Вернуться в форму" з відновленими даними, що вже має продаж/
    # антисептирование, лише для ОДНОПОЗИЦІЙНИХ форм (нема кошика - просто
    # підставляємо категорію+розмір напряму, той самий принцип, що вже
    # реалізований окремо для антисептирования, лише спільний тут, бо
    # прихід і списання мають однакову форму даних (product/condition/
    # breed/rows), на відміну від антисептика (volume замість rows).
    def _build_single_position_resume(self, store, payload, parent_action_code, kind):
        if not payload or not payload.get("rows"):
            return None
        operation_id = resolve_operation_for_payload(store, parent_action_code, kind, payload)
        if operation_id is None:
            return None
        return {
            "category_operation_id": operation_id,
            "breed": payload.get("breed"),
            "rows": payload.get("rows"),
        }

    def _webapp_all_in_one_income_context(self, store, resume_payload=None):
        categories = []
        for operation in store.list_operations("start_income"):
            op_id, _code, kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
            prefill = json.loads(prefill_json) if prefill_json else {}
            sub_payload = {"product": prefill.get("product"), "condition": prefill.get("condition")}
            sub_ctx = self._webapp_form_context(
                store, op_id, self._WEBAPP_ALL_IN_ONE_INCOME_KEYS, sub_payload, label,
                restrict_to_existing_combos=False,
            )
            categories.append({
                "key": op_id,
                "label": label,
                "kind": kind,
                "fields": sub_ctx["fields"],
                "dimension_combos": sub_ctx["dimension_combos"],
                # Задача користувача (скріншот екрана прихід-форми): "потрібно
                # щоб відразу рахувало і показувало одиницю вимірювання" -
                # webapp/app.js реюзає ЦЕ поле, щоб на клієнті класифікувати
                # рядок (м3/м2/мп/без виміру), тим самим правилом, що вже й
                # так має utils.py's row_measure_kind/is_area_based_product/
                # is_quantity_only_product/is_linear_meter_size (JS-двійник,
                # той самий підхід, що вже застосований для formatServerNumber).
                "product": prefill.get("product"),
            })
        ctx = {
            "mode": "all_in_one",
            "kind": "income",
            "title": "Приход одной формой",
            "categories": categories,
            "common_fields": [],
            **self._webapp_style_ctx(),
            # Знову вбудовано (задача користувача: "чи є якийсь інший
            # шлях?" - sendData() для збереження шаблону не потребує
            # окремого запиту "list", а сам ctx тепер ідe через короткий
            # токен, не base64-URL, тож роздування вже не загрожує).
            **self._webapp_templates_ctx(store, "income"),
        }
        resume = self._build_single_position_resume(store, resume_payload, "start_income", "income")
        if resume:
            ctx["resume"] = resume
        return ctx

    def _income_all_in_one_webapp_button(self, store, resume_payload=None):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return None
        ctx = self._webapp_all_in_one_income_context(store, resume_payload=resume_payload)
        if not ctx["categories"]:
            return None
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/index.html?t={token}"
        return {"web_app": {"url": url}}

    # Форма приходу надсилає ЛИШЕ category_operation_id + fields напряму
    # (та сама одно-позиційна форма, що й списання) - permission-перевірка
    # тут, а не лише у fallback-вході (_start_income_all_in_one_reply), той
    # самий принцип, що й у sale/writeoff: пряме відкриття форми з головного
    # меню пропускає серверний round-trip, який раніше єдиний перевіряв
    # право доступу.
    def _continue_income_all_in_one_submission(self, store, context, submitted):
        denied = self._require_permission(store, context, perm.INCOME)
        if denied:
            return denied
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        submitted = dict(submitted) if isinstance(submitted, dict) else {}
        # Реальний баг (живий скріншот користувача, 2026-08-14): "Приход
        # (форма)" відкривається з status="income_all_in_one" - дисптечиться
        # СЮДИ (_continue_operation_with_webapp_payload), а НЕ в
        # _continue_sale_all_in_one_submission. webapp/app.js для приходу
        # ЗАВЖДИ шле positions[] (навіть одну позицію, "positions_kind":
        # "income") - без цієї перевірки category_operation_id (він живе
        # ВСЕРЕДИНІ кожної позиції, не на верхньому рівні) завжди був None ->
        # "Не удалось определить категорию из формы" на КОЖНОМУ приході.
        positions_data = submitted.get("positions")
        if isinstance(positions_data, list) and positions_data:
            return self._continue_income_all_in_one_multi_position(store, context, submitted, positions_data)
        operation_id = submitted.pop("category_operation_id", None)
        operation = store.get_operation(operation_id) if operation_id is not None else None
        if operation is None:
            return self._with_main_menu(
                "Не удалось определить категорию из формы. Начните приход заново.", store
            )
        _op_id, _code, _kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
        prefill = json.loads(prefill_json) if prefill_json else {}
        payload = self._new_income_payload(label, context)
        payload["product"] = prefill.get("product")
        if prefill.get("condition"):
            payload["condition"] = prefill.get("condition")
        self._merge_webapp_submission(payload, submitted)
        self._record_webapp_operation_use(store, "income", operation_id, payload)
        return self._continue_income_operation(store, context, payload)

    # "Реализация (форма)" - друга, паралельна кнопка поруч зі звичайною
    # "РЕАЛИЗАЦИЯ": замість чатових кроків (спосіб оплати -> категорія ->
    # чек-лист) - ОДНА форма, де категорія сама є полем форми. Перевикористовує
    # _webapp_form_context ПОВНІСТЮ (жодного дубльованого правила dropdown-ів
    # breed/thickness/width/length/client/address/payment_method) - викликає
    # її окремо на кожну категорію (product/condition у sub_payload лише
    # впливають на _existing_dimension_values всередині, самі НЕ потрапляють
    # у видимі поля, бо їх немає в field_keys) і на спільні поля один раз.
    # price_per_unit - НА РІВНІ КАТЕГОРІЇ (не common): застосунок тепер сам
    # накопичує кілька позицій (webapp/app.js "Добавить позицию", жодного
    # контакту з ботом до фінального "Отправить") - кожна позиція може мати
    # СВОЮ ціну, тож ціна більше не єдине спільне поле на всю форму.
    _WEBAPP_ALL_IN_ONE_CATEGORY_KEYS = ("breed", "thickness", "width", "length", "quantity", "price_per_unit")
    _WEBAPP_ALL_IN_ONE_SERVICE_KEYS = ("volume", "price_per_unit")
    _WEBAPP_ALL_IN_ONE_COMMON_KEYS = ("client", "address", "payment_method")

    # Задача користувача (скріншот 2 — "між бот аі та продажа одной форми...
    # має поміщатись 5 рядків із шаблонами"): панель шаблонів (ліворуч,
    # користувач зберігає сам) + "5 останніх створених" (праворуч, наповнюється
    # автоматично) над самою формою — рівно ОДИН спільний хелпер на всі 3
    # мега-форми (kind="sale"/"income"/"writeoff"), бо форма даних однакова.
    # Мітка категорії резолвиться тут (не зберігається в самих таблицях) —
    # адмін міг перейменувати bot_operations.label з того часу.
    def _webapp_operation_template_entries(self, store, rows, source):
        return operation_template_entries(store, rows, source)

    # "Сохранить шаблон" ЗАВЖДИ закриває Mini App (Telegram.WebApp.sendData
    # так влаштований - жодного проміжного round-trip без закриття не існує),
    # тому відповідь одразу пропонує ту саму "Заполнить форму..."-кнопку -
    # людина повертається до порожньої форми, де новий шаблон уже видно
    # у списку зверху.
    def _save_operation_template_reply(self, store, context, submitted):
        kind = submitted.get("kind")
        permission_by_kind = {
            "sale": perm.SALE_CREATE,
            "income": perm.INCOME,
            "writeoff": perm.WRITEOFF,
            "antiseptic": perm.SALE_CREATE,
        }
        required_permission = permission_by_kind.get(kind)
        if required_permission is None:
            return self._with_main_menu("Не удалось сохранить шаблон: неизвестный тип операции.", store)
        denied = self._require_permission(store, context, required_permission)
        if denied:
            return denied
        operation_id = submitted.get("category_operation_id")
        operation = store.get_operation(operation_id) if operation_id is not None else None
        if operation is None:
            return self._with_main_menu("Не удалось сохранить шаблон: не выбрана категория.", store)
        store.add_operation_template(
            kind,
            operation_id,
            breed=submitted.get("breed"),
            thickness=submitted.get("thickness"),
            width=submitted.get("width"),
            length=submitted.get("length"),
            client=submitted.get("client"),
            address=submitted.get("address"),
            payment_method=submitted.get("payment_method"),
        )
        return self._prepend_reply_text("Шаблон сохранён.", self._reopen_operation_all_in_one_form(store, context, kind))

    def _record_webapp_operation_use(self, store, kind, operation_id, payload):
        if operation_id is None:
            return
        rows = payload.get("rows") or [{}]
        row = rows[0] if rows else {}
        store.record_operation_use(
            kind,
            operation_id,
            breed=payload.get("breed"),
            thickness=row.get("thickness"),
            width=row.get("width"),
            length=row.get("length"),
            client=payload.get("client"),
            address=payload.get("address"),
            payment_method=payload.get("payment_method"),
        )

    def _webapp_templates_ctx(self, store, kind):
        return {
            "templates": self._webapp_operation_template_entries(
                store, store.list_operation_templates(kind), "template",
            ),
            "recent": self._webapp_operation_template_entries(
                store, store.recent_operation_uses(kind), "recent",
            ),
        }

    # "Завжди може видалити історію чи шаблон по 1 рядку" - той самий
    # термінальний патерн, що й збереження: sendData() закриває Mini App,
    # тому відповідь одразу перевідкриває ту саму форму (вже без видаленого
    # рядка).
    def _delete_operation_template_reply(self, store, context, submitted):
        kind = submitted.get("kind")
        permission_by_kind = {
            "sale": perm.SALE_CREATE,
            "income": perm.INCOME,
            "writeoff": perm.WRITEOFF,
            "antiseptic": perm.SALE_CREATE,
        }
        required_permission = permission_by_kind.get(kind)
        if required_permission is None:
            return self._with_main_menu("Не удалось удалить шаблон: неизвестный тип операции.", store)
        denied = self._require_permission(store, context, required_permission)
        if denied:
            return denied
        template_id = submitted.get("template_id")
        # Реальна знахідка (аудит коду, 2026-08-16): право на видалення
        # перевірялось за kind, який НАДІСЛАВ клієнт, а не за реальним
        # kind рядка в БД - користувач із правом лише на "writeoff" міг
        # надіслати kind="writeoff" і template_id чужого "sale"-шаблону, і
        # право пройшло б перевірку вище, хоча реально видаляється чужий
        # рядок. Звіряємо ще й РЕАЛЬНИЙ kind рядка перед видаленням - той
        # самий тихий idempotent-паттерн, що й для template_id is None.
        if template_id is not None:
            row = store.get_operation_template(template_id)
            if row is not None and row[1] == kind:
                store.delete_operation_template(template_id)
        return self._prepend_reply_text("Шаблон удалён.", self._reopen_operation_all_in_one_form(store, context, kind))

    def _delete_operation_recent_use_reply(self, store, context, submitted):
        kind = submitted.get("kind")
        permission_by_kind = {
            "sale": perm.SALE_CREATE,
            "income": perm.INCOME,
            "writeoff": perm.WRITEOFF,
            "antiseptic": perm.SALE_CREATE,
        }
        required_permission = permission_by_kind.get(kind)
        if required_permission is None:
            return self._with_main_menu("Не удалось удалить запись истории: неизвестный тип операции.", store)
        denied = self._require_permission(store, context, required_permission)
        if denied:
            return denied
        recent_id = submitted.get("recent_id")
        # Той самий фікс, що й у _delete_operation_template_reply вище -
        # звіряємо РЕАЛЬНИЙ kind рядка з БД, а не лише той, що надіслав
        # клієнт, перш ніж видаляти.
        if recent_id is not None:
            row = store.get_operation_recent_use(recent_id)
            if row is not None and row[1] == kind:
                store.delete_operation_recent_use(recent_id)
        return self._prepend_reply_text(
            "Запись истории удалена.", self._reopen_operation_all_in_one_form(store, context, kind),
        )

    def _reopen_operation_all_in_one_form(self, store, context, kind):
        reopen_by_kind = {
            "sale": self._start_sale_all_in_one_reply,
            "income": self._start_income_all_in_one_reply,
            "writeoff": self._start_writeoff_all_in_one_reply,
            "antiseptic": self._start_antiseptic_all_in_one_reply,
        }
        reopen_fn = reopen_by_kind.get(kind)
        return reopen_fn(store, context) if reopen_fn else self._main_menu_reply(store)

    # Задача користувача (скріншот "Вернуться в форму"): кнопка мала
    # відкривати ПОРОЖНЮ форму, все введене губилось. Дані насправді вже й
    # так лежать у payload незавершеної операції (той самий pending_
    # operation, що дозволяє чатовому потоку "пам'ятати" крок) - лишається
    # лише перетворити його на кошик мега-форми, а не заводити окреме
    # сховище. resolve_operation_for_payload знаходить category_operation_id
    # за product/condition - ні completed_positions, ні "поточна" позиція їх
    # напряму не несуть (лише product/condition, як prefill_json категорії).
    def _build_sale_resume_cart(self, store, payload):
        if not payload:
            return []
        positions = list(payload.get("completed_positions") or [])
        if payload.get("rows"):
            current = {
                field: payload[field] for field in self._SALE_POSITION_FIELDS if field in payload
            }
            current["total_amount"] = self._sale_total_amount(payload)
            positions.append(current)
        cart = []
        for position in positions:
            operation_id = resolve_operation_for_payload(store, "start_sale", "sale", position)
            if operation_id is None:
                continue
            entry = {"category_operation_id": operation_id}
            for field in ("breed", "rows", "price_per_unit", "antiseptic"):
                if position.get(field) is not None:
                    entry[field] = position[field]
            cart.append(entry)
        return cart

    def _webapp_all_in_one_sale_context(self, store, resume_payload=None):
        categories = []
        for operation in store.list_operations("start_sale"):
            op_id, _code, kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
            if kind == "service":
                sub_payload = {}
                field_keys = self._WEBAPP_ALL_IN_ONE_SERVICE_KEYS
                product = None
            else:
                prefill = json.loads(prefill_json) if prefill_json else {}
                sub_payload = {"product": prefill.get("product"), "condition": prefill.get("condition")}
                field_keys = self._WEBAPP_ALL_IN_ONE_CATEGORY_KEYS
                product = prefill.get("product")
            sub_ctx = self._webapp_form_context(store, op_id, field_keys, sub_payload, label)
            categories.append({
                "key": op_id,
                "label": label,
                "kind": kind,
                "fields": sub_ctx["fields"],
                "dimension_combos": sub_ctx["dimension_combos"],
                # Задача користувача: "потрібно щоб відразу рахувало і
                # показувало одиницю вимірювання" - той самий JS-двійник
                # row_measure_kind, що й у прихід/списання-мега-формах.
                "product": product,
                # НАВМИСНО без "stock_balances" тут (на відміну від
                # одно-категорійного _webapp_form_context) - реальний баг
                # живого тестування 2026-08-08: ця "зв'язана" (усі категорії
                # разом) ctx уже й так велика (dimension_combos на кожну
                # категорію), і кнопка з нею вбудована НАПРЯМУ в головне
                # меню (сама частий, завжди-присутній елемент клавіатури) -
                # додавання повної мапи залишків на КОЖНУ категорію штовхнуло
                # довжину web_app-URL за реальну межу, яку Telegram мовчки
                # відхиляє (sendMessage падає з помилкою, спіймано ЗОВНІШНІМ
                # try/except у main.py - не самим _build_reply_pipeline,
                # тому в журналі дій це виглядає як "success", а в чаті
                # людина бачить лише загальну "Произошла внутренняя
                # ошибка" - на КОЖНЕ наступне повідомлення, бо головне меню
                # з тією самою кнопкою показується знову і знову). Серверний
                # backstop (_sale_stock_issue) і так перевіряє достатність
                # залишку незалежно від клієнтської підказки - функціонально
                # захист не втрачається, лише немає завчасного попередження
                # саме на цьому екрані.
            })
        common_ctx = self._webapp_form_context(
            store, None, self._WEBAPP_ALL_IN_ONE_COMMON_KEYS, {}, "Продажа"
        )
        ctx = {
            "mode": "all_in_one",
            "kind": "sale",
            "title": "Продажа одной формой",
            "categories": categories,
            "common_fields": common_ctx["fields"],
            **self._webapp_style_ctx(),
            **self._webapp_templates_ctx(store, "sale"),
        }
        resume_cart = self._build_sale_resume_cart(store, resume_payload)
        if resume_cart:
            ctx["resume"] = {
                "cart": resume_cart,
                "common": {
                    "client": resume_payload.get("client"),
                    "address": resume_payload.get("address"),
                    "payment_method": resume_payload.get("payment_method"),
                },
            }
        return ctx

    # Задача користувача: "нащо це вікно додаткове? чому не відразу кидати
    # в програму ще з головного меню?" - кнопка "РЕАЛИЗАЦИЯ (форма)" сама
    # має нести web_app-посилання (Telegram відкриває форму ОДРАЗУ по тапу,
    # без проміжного повідомлення-посередника). Повертає None, якщо тунель
    # не піднятий чи категорій немає взагалі - викликач тоді лишає звичайну
    # текстову кнопку (старий, кроковий шлях як безпечний fallback).
    def _sale_all_in_one_webapp_button(self, store, resume_payload=None):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return None
        ctx = self._webapp_all_in_one_sale_context(store, resume_payload=resume_payload)
        if not ctx["categories"]:
            return None
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/index.html?t={token}"
        return {"web_app": {"url": url}}

    # "СПИСАНИЕ (форма)" - Задача користувача: "додай подібні клавіші
    # ...(форма) і для реализации і для списание" - той самий "категорія
    # прямо в формі" мега-формат, що вже має РЕАЛИЗАЦИЯ (форма), тепер і для
    # списання. На відміну від продажу - без price_per_unit/client/address/
    # payment_method (списання їх не має взагалі) і без кошика: списання
    # завжди залишається одноразовим поданням (той самий принцип, що вже
    # застосований для антисептирования - "Продолжить" тут не має сенсу,
    # немає кількох незалежних товарних позицій, які варто накопичувати).
    _WEBAPP_ALL_IN_ONE_WRITEOFF_KEYS = ("breed", "thickness", "width", "length", "quantity")
    _WEBAPP_ALL_IN_ONE_WRITEOFF_COMMON_KEYS = ("comment",)

    def _webapp_all_in_one_writeoff_context(self, store, resume_payload=None):
        categories = []
        for operation in store.list_operations("start_writeoff"):
            op_id, _code, kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
            prefill = json.loads(prefill_json) if prefill_json else {}
            sub_payload = {"product": prefill.get("product"), "condition": prefill.get("condition")}
            sub_ctx = self._webapp_form_context(
                store, op_id, self._WEBAPP_ALL_IN_ONE_WRITEOFF_KEYS, sub_payload, label
            )
            categories.append({
                "key": op_id,
                "label": label,
                "kind": kind,
                "fields": sub_ctx["fields"],
                "dimension_combos": sub_ctx["dimension_combos"],
                # Задача користувача: "потрібно щоб відразу рахувало і
                # показувало одиницю вимірювання" - той самий JS-двійник
                # row_measure_kind, що й у прихід/продаж-мега-формах.
                "product": prefill.get("product"),
                # Без "stock_balances" - той самий real-bug фікс, що й у
                # _webapp_all_in_one_sale_context (див. коментар там):
                # web_app-URL, вбудований НАПРЯМУ в головне меню, не сміє
                # роздуватись повною мапою залишків на кожну з 4 категорій.
            })
        common_ctx = self._webapp_form_context(
            store, None, self._WEBAPP_ALL_IN_ONE_WRITEOFF_COMMON_KEYS, {}, "Списание"
        )
        ctx = {
            "mode": "all_in_one",
            "kind": "writeoff",
            "title": "Списание одной формой",
            "categories": categories,
            "common_fields": common_ctx["fields"],
            **self._webapp_style_ctx(),
            **self._webapp_templates_ctx(store, "writeoff"),
        }
        resume = self._build_single_position_resume(store, resume_payload, "start_writeoff", "writeoff")
        if resume:
            resume["common"] = {"comment": resume_payload.get("comment")}
            ctx["resume"] = resume
        return ctx

    def _writeoff_all_in_one_webapp_button(self, store, resume_payload=None):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return None
        ctx = self._webapp_all_in_one_writeoff_context(store, resume_payload=resume_payload)
        if not ctx["categories"]:
            return None
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/index.html?t={token}"
        return {"web_app": {"url": url}}

    # "ДАННЫЕ (форма)" - Задача користувача: "має красиво в апсі показувати
    # дані" - на відміну від усіх інших форм (income/sale/writeoff), ця
    # НІЧОГО не надсилає боту назад - чистий перегляд/фільтрація вже
    # наявного залишку складу, повністю на клієнті (webapp/data.js).
    # Тому й немає жодного pending_operation/webapp_data-обробника - лише
    # ctx-будівник + кнопка, той самий register_context/token-механізм,
    # що вже використовують income/sale/writeoff-форми.
    # Задача користувача: вкладки збоку мають бути ТІ Ж САМІ 5 пунктів, що
    # й під "ДАННЫЕ" в самому боті (custom_menu_buttons, warehouse_data.py
    # _CUSTOM_BUTTON_SEED) - назви беремо буквально звідти, а не вигадуємо
    # свої. Кожна вкладка показує РІВНО ті самі поля, що й відповідний
    # звіт бота (_stock_balance_rows/_sales_report_rows/
    # _antiseptic_report_rows/low_stock_warehouse_items - ті самі функції,
    # що вже реально формують чат-повідомлення), лише період для продажів/
    # антисептирования тут завжди "весь период" - JS фільтрує період на
    # клієнті (той самий принцип, що вже діє для розміру/породи/типу на
    # вкладці СКЛАД), щоб не робити окремий round-trip на кожен клік по
    # "Неделя"/"Месяц". "Клиенты" не має власного бек-енд запиту -
    # групування по клієнту (_sales_by_client_rows) рахується на клієнті з
    # ТИХ САМИХ рядків продажів, що й вкладка "Продажи".
    # migration_key -> запасна мітка (та сама, що й у _CUSTOM_BUTTON_SEED);
    # реальна мітка читається живою через get_custom_button_by_migration_key
    # (переживає перейменування адміністратором через "Редактор кнопок"),
    # запасна лишається лише якщо кнопку взагалі видалили з дерева.
    _DATA_BROWSER_TAB_KEYS = [
        ("stock_report_section", "СКЛАД"),
        ("sales_report_section", "ПРОДАЖИ"),
        ("antiseptic_report_section", "АНТИСЕПТИРОВАНИЕ"),
        # Задача користувача: "додай вкладку списання... будемо бачити що і
        # коли списали і чому" - без власної кнопки в custom_menu_buttons
        # (не seed-ена, на відміну від решти) - _data_browser_tab_labels
        # нижче й так коректно падає на fallback-мітку, коли кнопки нема.
        ("writeoff_report_section", "Списание"),
        ("sales_by_client_report_section", "Клиенты"),
        ("low_stock_report_section", "Низкий остаток"),
        # Задача користувача (2026-08-14): "Приход" - нова вкладка, без
        # власної кнопки в custom_menu_buttons (той самий випадок, що й
        # "Списание" вище) - завжди падає на fallback-мітку "Приход".
        ("income_report_section", "Приход"),
    ]

    def _data_browser_tab_labels(self, store):
        labels = []
        for migration_key, fallback_label in self._DATA_BROWSER_TAB_KEYS:
            button = store.get_custom_button_by_migration_key(migration_key)
            labels.append(button[2] if button and button[2] else fallback_label)
        return labels

    # Задача користувача (2026-08-14, скріншот "Позиция"): "не має бути
    # такого злиття - і тип, і ширина, і товщина, все в одному" - "Продукт"
    # часто вже містить суфікс стану в самому тексті (напр. "Доска AD"), той
    # самий факт, через який _webapp_stock_tab_rows нижче вже викликає
    # _split_product_condition. Спільний хелпер тут, щоб Продажи/Списание/
    # Приход не дублювали ту саму 3-рядкову логіку кожен по-своєму.
    def _webapp_split_product_display(self, product, condition=None):
        base, suffix = self._split_product_condition(product or "", [])
        clean_product = base or product or ""
        resolved_condition = condition if condition else suffix
        return clean_product, resolved_condition

    def _webapp_stock_tab_rows(self, store):
        report_rows, _error = self._stock_balance_rows(store)
        categories = []
        rows = []
        for row in report_rows or []:
            product = row["product"] or ""
            condition = row["condition"] or ""
            # Задача користувача: верхні чипи раніше змішували товар і стан
            # ("Доска AD", "Доска KD", "ОСБ N/A") - тепер чипи лише за
            # товаром, а стан (AD/KD/N/A) переїхав у власну колонку "Тип" у
            # таблиці. "Продукт" часто вже містить суфікс стану в самому
            # тексті (напр. "Доска AD") - _split_product_condition (та сама
            # евристика, що й _stock_balance_rows вище) відрізає його, щоб
            # чипи лишились чистими назвами товару.
            base_product, _suffix = self._split_product_condition(product, [])
            display_product = base_product or product
            if display_product and display_product not in categories:
                categories.append(display_product)
            if row["volume"] is not None:
                measure, unit = row["volume"], "м3"
            elif row["area"] is not None:
                measure, unit = row["area"], "м2"
            elif row["linear"] is not None:
                measure, unit = row["linear"], "мп"
            else:
                measure, unit = None, None
            rows.append({
                "product": display_product,
                "condition": condition,
                "breed": row["breed"],
                "thickness": row["thickness"],
                "width": row["width"],
                "length": row["length"],
                "quantity": row["quantity"],
                "measure": measure,
                "unit": unit,
            })
        return categories, rows

    # Задача користувача (2026-08-14): "Автор" - хто провів операцію -
    # окрема webapp-назва для того самого "manager"/"manager_final", що вже
    # рахує _sales_report_rows/_antiseptic_report_rows (Excel-колонка
    # "Менеджер (итог)"/"Ответственный" - лишається як є, тут лише НОВЕ
    # поле у відповіді для клієнта, назва самої Excel-колонки не міняється).
    # Задача користувача (2026-08-14, скріншот "Позиция"): "не має бути
    # такого злиття - і тип, і ширина, і товщина, все в одному" - "position"
    # (product/breed/size одним рядком) лишається лише у _sales_report_rows
    # для PDF/Excel-звіту; тут, для webapp-таблиці "Продажи", - окремі
    # product/breed/condition/size/thickness/width/length, той самий
    # контракт, що вже мають Продукт/Порода/Тип/Размер в Остатки/Низкий
    # остаток/Приход (_webapp_split_product_display відрізає AD/KD-суфікс,
    # якщо він і досі злитий з "Продукт" в самому тексті).
    def _webapp_sales_tab_rows(self, store):
        all_period = {"from": None, "to": None, "label": "весь период"}
        report_rows, _error = self._sales_report_rows(store, all_period, None)
        result = []
        for row in report_rows or []:
            product, condition = self._webapp_split_product_display(row.get("product"))
            result.append({
                "date": row.get("date"),
                "client": row.get("client"),
                "product": product,
                "breed": row.get("breed"),
                "condition": condition,
                "size": row.get("size"),
                "thickness": row.get("thickness"),
                "width": row.get("width"),
                "length": row.get("length"),
                "quantity": row.get("quantity"),
                "volume": row.get("volume"),
                "area": row.get("area"),
                "linear": row.get("linear"),
                "total_amount": row.get("total_amount"),
                "author": row.get("manager"),
            })
        return result

    def _webapp_antiseptic_tab_rows(self, store):
        all_period = {"from": None, "to": None, "label": "весь период"}
        report_rows, _error = self._antiseptic_report_rows(store, all_period)
        return [
            {
                "date": row.get("date"),
                "client": row.get("client"),
                "volume": row.get("volume"),
                "total_amount": row.get("total_amount"),
                "author": row.get("manager"),
            }
            for row in report_rows or []
        ]

    def _webapp_low_stock_tab_rows(self, store):
        threshold = SettingsStore(SETTINGS_PATH).get("low_stock_threshold")
        rows = low_stock_report_rows(store, threshold)
        # Реальний ризик (аудит коду, 2026-08-14): ця функція раніше
        # читала product/condition НАПРЯМУ, без _webapp_split_product_
        # display (той самий хелпер, що вже рятує Продажи/Списание/Приход
        # від злиття "Доска AD" в один текст) - якщо в конкретного рядка
        # СКЛАДУ порожня комірка "Состояние", але суфікс AD/KD і досі
        # злитий з "Продукт" у самому тексті, "Тип" на "Низкий остаток"
        # для того самого товару лишався порожнім, хоча на "Остатки" (яка
        # цей самий хелпер уже викликає) показувався б коректно.
        for row in rows:
            row["product"], row["condition"] = self._webapp_split_product_display(
                row.get("product"), row.get("condition")
            )
        return threshold, rows

    # Задача користувача: "додай вкладку списання... будемо бачити що і
    # коли списали і чому" - writeoff_report_rows (warehouse_data.py) читає
    # напряму з WRITEOFF_SHEET_NAME, той самий принцип, що вже мають
    # ПРОДАЖИ/АНТИСЕПТИРОВАНИЕ (лише без окремого "за період" параметра -
    # завжди "весь период", як і решта вкладок тут).
    # "author" (2026-08-14): writeoff_report_rows вже повертає "manager" -
    # тут лише перейменування поля під спільний webapp-контракт ("author"
    # скрізь), сама Excel-колонка "Менеджер" не змінюється.
    def _webapp_writeoff_tab_rows(self, store):
        rows = writeoff_report_rows(store)
        for row in rows:
            row["author"] = row.pop("manager", None)
            # Задача користувача (2026-08-14): "не має бути такого злиття -
            # і тип..." - "Продукт" тут теж часто містить AD/KD суфіксом у
            # самому тексті (_webapp_split_product_display, той самий
            # хелпер, що й Продажи/Приход вище).
            row["product"], row["condition"] = self._webapp_split_product_display(row.get("product"))
        return rows

    # Задача користувача (2026-08-14): "Приход" - нова вкладка. ПРИХОД
    # МАТЕРИАЛА (INCOME_SHEET_NAME) - повноцінний документ-журнал, той самий
    # принцип, що вже мають Продажи/Списание/Антисептирование - income_
    # report_rows (warehouse_data.py) читає напряму звідти, той самий
    # контракт, що й writeoff_report_rows (thickness/width/length окремо
    # для фільтра розміру, "manager" перейменовується на "author" тут же).
    def _webapp_income_tab_rows(self, store):
        rows = income_report_rows(store)
        for row in rows:
            row["author"] = row.pop("manager", None)
            # Задача користувача (2026-08-14): "не має бути такого злиття -
            # і тип..." - "condition" тут зазвичай вже приходить окремо (з
            # "Состояние"), тому split лише ДОПОВНЮЄ його, якщо порожній
            # (не перезаписує реальне значення) - той самий хелпер, що й
            # Продажи/Списание вище.
            row["product"], row["condition"] = self._webapp_split_product_display(row.get("product"), row.get("condition"))
        return rows

    # is_admin - Задача користувача: поріг "Низкий остаток" міняти можна
    # ТІЛЬКИ адміністратору. Клієнт (data.js) лише ХОВАЄ поле редагування
    # для не-адмінів - справжня перевірка ролі все одно повторюється на
    # запису (webapp_server.py, action "update_low_stock_threshold"), бо
    # /api/template - реальна, інтернет-досяжна точка, а не довірений клієнт.
    # Задача користувача (2026-08-14): "прибери запам'ятовування. криво
    # працює" - фільтри/сортування/позиція панелі більше НЕ зберігаються й
    # не підвантажуються між відкриттями "Данные" (раніше - webapp_data_
    # browser_prefs, ctx["saved_prefs"]/["remember_enabled"] - обидва прибрані
    # звідси; store.get_webapp_data_browser_prefs більше ніде не викликається).
    # Реальна знахідка (аудит коду, 2026-08-16): "Дані" (єдина кнопка,
    # обʼєднує всі 7 вкладок) раніше перевіряла лише WAREHOUSE_VIEW на
    # вході (_start_data_browser_reply нижче) і тоді віддавала АБСОЛЮТНО
    # ВСІ вкладки без розбору - роль "Склад" (WAREHOUSE_VIEW є, SALE_VIEW
    # немає, permissions.py:127) бачила Продажі/Антисептирование з іменами
    # клієнтів і сумами, хоча старі ОКРЕМІ звіти (_start_sales_report_reply/
    # _start_antiseptic_report_reply/_start_sales_by_client_report_reply)
    # завжди коректно вимагали саме SALE_VIEW для цих самих даних.
    # can_view_sales=True за замовчуванням - навмисно, щоб НЕ зламати
    # десктопний перегляд (client_app.py._on_open_data_in_browser_clicked,
    # передає лише is_admin=False, без ролі/дозволів - оператор програми на
    # своєму ж комп'ютері має повний доступ і так). Лише РЕАЛЬНИЙ виклик з
    # Telegram-бота (_data_browser_webapp_button нижче) явно рахує й передає
    # справжнє значення за роллю користувача.
    def _webapp_data_browser_context(self, store, is_admin=False, telegram_id=None, can_view_sales=True):
        categories, stock_rows = self._webapp_stock_tab_rows(store)
        sales_rows = self._webapp_sales_tab_rows(store) if can_view_sales else []
        antiseptic_rows = self._webapp_antiseptic_tab_rows(store) if can_view_sales else []
        writeoff_rows = self._webapp_writeoff_tab_rows(store)
        income_rows = self._webapp_income_tab_rows(store)
        low_stock_threshold, low_stock_rows = self._webapp_low_stock_tab_rows(store)
        return {
            "mode": "data_browser",
            "title": "Данные склада",
            "telegram_id": telegram_id,
            "tabs": self._data_browser_tab_labels(store),
            "categories": categories,
            "rows": stock_rows,
            "sales_rows": sales_rows,
            "antiseptic_rows": antiseptic_rows,
            "writeoff_rows": writeoff_rows,
            "income_rows": income_rows,
            "low_stock_rows": low_stock_rows,
            "low_stock_threshold": low_stock_threshold,
            "can_edit_low_stock_threshold": is_admin,
            # Задача користувача (2026-08-16): реальний баг, знайдений одразу
            # слідом за can_edit_low_stock_threshold вище (той самий скрін) -
            # "Обновить" (refresh_data_browser, webapp_server.py) вимагає
            # СПРАВЖНІЙ telegram_id усередині ctx (шукає роль наново в базі)
            # - без нього сервер завжди повертає "Ссылка устарела", навіть
            # якщо посилання щойно створене. Це той самий "рівень 2" з
            # docstring _handle_template_action, не помилка авторизації як
            # така, просто десктопний перегляд (client_app.py, is_admin=False,
            # telegram_id=None) не має РЕАЛЬНОГО telegram-користувача, за
            # роллю якого можна було б перевірити щось наново.
            "can_refresh": telegram_id is not None,
            **self._webapp_style_ctx(),
        }

    def _data_browser_webapp_button(self, store, context):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return None
        role = self._current_user_role(store, context)
        is_admin = role == perm.ADMIN
        ctx = self._webapp_data_browser_context(
            store, is_admin, telegram_id=context["user_id"],
            can_view_sales=perm.has_permission(role, perm.SALE_VIEW),
        )
        has_any_data = any(
            ctx[key]
            for key in ("rows", "sales_rows", "antiseptic_rows", "writeoff_rows", "income_rows", "low_stock_rows")
        )
        if not has_any_data:
            return None
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/data.html?t={token}"
        return {"web_app": {"url": url}}

    def _start_data_browser_reply(self, store, context):
        denied = self._require_permission(store, context, perm.WAREHOUSE_VIEW)
        if denied:
            return denied
        web_app = self._data_browser_webapp_button(store, context)
        if web_app is None:
            return self._with_main_menu(
                "Просмотр данных одной формой сейчас недоступен (форма не подключена "
                "или на складе пока нет данных). Используйте обычное «ДАННЫЕ».",
                store,
            )
        keyboard = {
            "keyboard": [
                [{"text": "Открыть данные склада", **web_app}],
                [{"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
        }
        return {
            "type": "message",
            "text": store.get_message_template("start_data_browser_form", BOT_MESSAGE_DEFAULTS["start_data_browser_form"]),
            "reply_markup": keyboard,
        }

    # Форма списання надсилає ЛИШЕ category_operation_id + fields напряму
    # (та сама одно-позиційна форма, що й антисептирование/старий
    # однокатегорійний шлях продажу - без "positions", кошик списанню не
    # потрібен, webapp/app.js's submit() свідомо не будує його для kind
    # "writeoff"). Permission-перевірка тут, а не лише в fallback-вході
    # (_start_writeoff_all_in_one_reply) - той самий принцип, що й
    # _continue_sale_all_in_one_submission: пряме відкриття форми з
    # головного меню пропускає серверний round-trip, який раніше єдиний
    # перевіряв право доступу.
    def _continue_writeoff_all_in_one_submission(self, store, context, submitted):
        denied = self._require_permission(store, context, perm.WRITEOFF)
        if denied:
            return denied
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        submitted = dict(submitted) if isinstance(submitted, dict) else {}
        operation_id = submitted.pop("category_operation_id", None)
        operation = store.get_operation(operation_id) if operation_id is not None else None
        if operation is None:
            return self._with_main_menu(
                "Не удалось определить категорию из формы. Начните списание заново.", store
            )
        _op_id, _code, _kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
        prefill = json.loads(prefill_json) if prefill_json else {}
        payload = self._new_income_payload(label, context)
        payload["operation_kind"] = "writeoff"
        payload["product"] = prefill.get("product")
        if prefill.get("condition"):
            payload["condition"] = prefill.get("condition")
        self._merge_webapp_submission(payload, submitted)
        self._record_webapp_operation_use(store, "writeoff", operation_id, payload)
        return self._continue_writeoff_operation_impl(store, context, payload)

    # Пряме відкриття мега-форми (тап на кнопку меню, без pending) сьогодні
    # розпізнається лише за маркерами category_operation_id/positions -
    # обидва можуть належати і продажу, і списанню (та сама форма даних).
    # Розрізняємо за РЕАЛЬНИМ kind обраної операції (bot_operations, довірене
    # джерело - client лише передає id, той самий принцип, що вже застосований
    # у _continue_sale_all_in_one_submission), а не за жодним клієнтським
    # прапорцем - позиції з positions[] завжди належать продажу (списання
    # кошика не будує).
    def _continue_direct_open_webapp_submission(self, store, context, submitted):
        # "Антисептирование (форма)" перевикористовує РЕАЛЬНІ sale-категорії
        # (Доска AD/KD/ОСБ/Вагонка) для вибору товару/розміру - тому
        # category_operation_id тут веде на operation[2] == "sale", той самий
        # kind, що й звичайна продажа. Явний клієнтський прапорець
        # antiseptic_form (webapp/app.js, лише коли ctx.kind == "antiseptic")
        # - єдиний спосіб відрізнити це подання від справжньої продажі ще ДО
        # звичайної розгалуження за kind нижче.
        if submitted.get("antiseptic_form"):
            return self._continue_antiseptic_all_in_one_submission(store, context, submitted)
        category_operation_id = submitted.get("category_operation_id")
        operation = store.get_operation(category_operation_id) if category_operation_id is not None else None
        if operation is not None and operation[2] == "writeoff":
            return self._continue_writeoff_all_in_one_submission(store, context, submitted)
        if operation is not None and operation[2] == "income":
            return self._continue_income_all_in_one_submission(store, context, submitted)
        return self._continue_sale_all_in_one_submission(store, context, submitted)

    def _is_real_telegram_user(self, context):
        # Аудит коду: раніше "не справжній користувач" означало user_id in
        # (None, "", "local") — тобто РІВНО той самий висновок і для
        # свідомого внутрішнього виклику (message=None -> _message_context
        # ставить user_id="local", єдиний намірений сентинел), і для
        # СПРАВЖНЬОГО Telegram-повідомлення, у якому раптом немає "from"
        # (user_id стає None тим самим шляхом _message_context). Обидва тоді
        # трактувались однаково -> _current_user_role видавав ADMIN. Лише
        # буквальне "local" — реальний, навмисний сентинел; None/"" з
        # СПРАВЖНЬОГО message — інша, непередбачена ситуація, якій довіряти
        # статус адміна не можна (fail-closed: невідома роль -> 0 прав,
        # perm.normalize_role(None) -> None -> ROLE_PERMISSIONS.get(None, set())).
        return context.get("user_id") != "local"

    # Роль для перевірки прав доступу (permissions.py). Локальний/тестовий
    # контекст (не справжній Telegram-користувач) вважаємо адміном — це не
    # реальний сценарій доступу, а внутрішній виклик (наприклад з GUI).
    def _current_user_role(self, store, context):
        if not self._is_real_telegram_user(context):
            return perm.ADMIN
        return perm.normalize_role(store.get_user_role(context["user_id"]))

    # Повертає None, якщо дія дозволена, інакше — готове повідомлення відмови.
    def _require_permission(self, store, context, capability):
        role = self._current_user_role(store, context)
        if perm.has_permission(role, capability):
            return None
        return perm.permission_denied_reply(capability)

    # Задача користувача (2026-08-17): "додамо в налаштування ID чату, щоб
    # через файл приєднувало тхт" - той самий принцип, що вже має "ТГ
    # ключ" (client_app.py._read_telegram_token): шлях до .txt-файлу
    # лежить у settings.json ("report_chat_id_file"), сам ID читається
    # звідти ЩОРАЗУ свіжо (новий короткочасний SettingsStore, не кешується
    # - той самий підхід, що вже виправив гонку в SettingsStore.set()) -
    # можна змінити чат для дублю звітів БЕЗ нового релізу. Якщо файл не
    # обрано/порожній/зіпсований - тихо повертається до старого
    # захардкодженого paths.REPORT_BROADCAST_CHAT_ID, щоб нічого не
    # зламалось для тих, хто ще не встиг обрати файл.
    def _report_broadcast_chat_id(self):
        try:
            settings = SettingsStore(self.settings_path)
        except OSError:
            return REPORT_BROADCAST_CHAT_ID
        chat_id_file = settings.get("report_chat_id_file")
        if not chat_id_file:
            return REPORT_BROADCAST_CHAT_ID
        try:
            text = Path(chat_id_file).read_text(encoding="utf-8-sig").strip()
        except OSError:
            return REPORT_BROADCAST_CHAT_ID
        try:
            return int(text)
        except ValueError:
            return REPORT_BROADCAST_CHAT_ID

    # Задача користувача (2026-08-17): "повідомлення з результатом виконаної
    # операції, має прийти ще й у інший чат" - дублює текст успішного
    # звіту приходу/продажу/списання/антисептирования у фіксовану групу
    # (знайдений через /chatid), з іменем співробітника попереду. try/
    # except - навмисно широкий і мовчазний: це ДОДАТКОВЕ сповіщення понад
    # основну відповідь користувачу, збій мережі/тимчасова недоступність
    # групи НЕ повинні ламати чи затримувати саму операцію, яка вже
    # успішно записана в БД.
    def _notify_report_broadcast(self, context, message_text):
        chat_id = self._report_broadcast_chat_id()
        if not chat_id:
            return
        full_name = context.get("full_name") or "Неизвестный"
        try:
            # Задача користувача (2026-08-17): "прибери це меню з групи.
            # залиш тільки в чат-боті" - Telegram reply-клавіатура (кнопки
            # ПРИХОД/РЕАЛИЗАЦИЯ/... унизу екрана) - клієнтський UI-стан
            # ЧАТУ, що лишається видимим, поки якесь повідомлення явно НЕ
            # замінить/не прибере її (навіть якщо бот більше НІЧОГО туди
            # не шле - стара клавіатура від ще ДО фіксу "групи мовчать"
            # лишається висіти назавжди сама по собі). remove_keyboard
            # тут - явна, постійна гарантія, а не одноразовий фікс: кожен
            # звіт у групу заразом прибирає будь-яку клавіатуру, якщо вона
            # там ще є.
            self._send_message(
                chat_id, f"<b>{html.escape(full_name)}</b>:\n{message_text}",
                reply_markup={"remove_keyboard": True}, parse_mode="HTML",
            )
        except Exception:
            pass

    def _main_menu_reply(self, store=None):
        return {
            "type": "message",
            "text": "Главное меню. Выберите действие:",
            "reply_markup": self._main_command_keyboard(store),
        }

    # Будь-яка відповідь, що ЗАВЕРШУЄ операцію (скасування, відмова,
    # успішний запис, видача звіту) МАЄ повертати клавіатуру головного меню,
    # а не голий текст — інакше в Telegram лишається "прилипла" стара
    # клавіатура (Да/Нет/Редактировать, Показать/Очистить фильтр, PDF/Excel/
    # Сообщением...) без жодного виходу. Повторне натискання такої кнопки,
    # коли операція вже завершена, падає у "Список доступных команд", що
    # виглядає як випадковий стрибок у головне меню.
    def _with_main_menu(self, text, store=None, parse_mode=None):
        reply = self._main_menu_reply(store)
        reply["text"] = f"{text}\n\n{reply['text']}"
        if parse_mode:
            reply["parse_mode"] = parse_mode
        return reply

    def _cancelled_reply(self, text="Операция отменена.", store=None):
        # Задача користувача: "давай трішки приукрасимо бота" - при відміні
        # завжди видимий значок "❌", той самий принцип, що й "✅ Выполнено."
        # для успішних операцій (warehouse_data.py). Один спільний виклик -
        # усі численні місця, що передають власний текст скасування, теж
        # отримують значок автоматично, без правки кожного окремо.
        return self._with_main_menu(f"❌ {text}", store)

    def _no_active_operation_reply(self, store=None):
        return self._with_main_menu("Активной операции нет.", store)

    # allow_edit=False — Задача користувача: "якщо в програмі вже є
    # редагувати [форма], то редагування ботом непотрібне" - подання, що
    # прийшли з webapp-форми (payload["_from_webapp_form"]), ховають кнопку
    # "Редактировать", лишаючи звичайну Да/Нет. show_form_return=True додає
    # окремим рядком "Вернуться в форму" (див. _reopen_webapp_form_reply) -
    # завжди разом з allow_edit=False (форма-режим), ніколи одночасно з
    # "Редактировать".
    # yes_cancel_only (Задача користувача, скріншот антисептирования): "Да/
    # Нет/Редактировать" -> "Да/Отмена". "Нет" і "Отмена" вже семантично
    # однакові (обидва скасовують - "Отмена" ще й перехоплюється глобально,
    # незалежно від клавіатури, коментар нижче), тож просто НЕ показуємо
    # зайву третю кнопку.
    def _confirmation_keyboard(self, allow_edit=True, show_form_return=False, yes_cancel_only=False):
        if yes_cancel_only:
            buttons = [{"text": "Да"}, {"text": "Отмена"}]
        else:
            buttons = [{"text": "Да"}, {"text": "Нет"}]
            if allow_edit:
                buttons.append({"text": "Редактировать"})
        rows = [buttons]
        if show_form_return:
            rows.append([{"text": self._WEBAPP_FORM_RETURN_LABEL}])
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Реальний баг з аудиту: confirm_category_change/confirm_new_value/
    # confirm_volume_conflict/confirm_single_thickness/confirm_new_
    # antiseptic_client (усі рендеряться через _yes_no_reply) мали лише
    # Да/Нет — жодної видимої кнопки виходу. Текстом "Отмена" й раніше
    # працювала (глобальний перехоплювач скасування спрацьовує ДО того, як
    # відповідь взагалі доходить до цих статусів), просто не була видима.
    def _yes_no_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Да"}, {"text": "Нет"}, {"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Для будь-якого кроку, де бот очікує вільний текст (розмір/порода/
    # клієнт/ціна/оплата тощо) — поруч з "Отмена" завжди видима "Редактировать",
    # щоб виправити вже введені раніше дані не довелось спершу вгадувати
    # слово-команду. Замінює _cancel_only_keyboard як стандартна клавіатура
    # для таких кроків (див. _save_income_question/_save_antiseptic_question).
    def _cancel_and_edit_keyboard(self):
        return {
            "keyboard": [[{"text": "Отмена"}, {"text": "Редактировать"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Мінімальна клавіатура для кроків, де очікується вільний текст (розмір,
    # кількість, назва тощо) і жодного фіксованого набору кнопок не існує —
    # але вихід МАЄ бути завжди видимим, а не "remove_keyboard" (без жодної
    # кнопки — теж тупик, як показав реальний тест).
    def _cancel_only_keyboard(self):
        return {
            "keyboard": [[{"text": "Отмена"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Кроки-меню (вибір періоду/категорії/формату тощо, не вільний текст) —
    # замість одинокої "Отмена" дві чіткі дії: "Назад" (повернутись на
    # попередній крок ЦІЄЇ Ж операції, _handle_back_request) і "Главное меню"
    # (одразу й повністю вийти, як і "Отмена" раніше). Свідомо НЕ замінює
    # _cancel_only_keyboard — кроки вільного вводу тексту (розмір/кількість/
    # клієнт тощо) поки лишаються з однією "Отмена" (окрема, більша задача).
    def _back_and_main_menu_keyboard(self):
        return {
            "keyboard": [[{"text": "Назад"}, {"text": "Главное меню"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _yes_no_reply(self, text):
        return {
            "type": "message",
            "text": text,
            "reply_markup": self._yes_no_keyboard(),
        }

    def _confirmation_reply(self, text, allow_edit=True, show_form_return=False, yes_cancel_only=False):
        return {
            "type": "message",
            "text": text,
            "reply_markup": self._confirmation_keyboard(
                allow_edit=allow_edit, show_form_return=show_form_return, yes_cancel_only=yes_cancel_only,
            ),
        }

    def _prepend_reply_text(self, prefix, reply, parse_mode=None):
        if isinstance(reply, dict) and reply.get("type") == "message":
            updated = dict(reply)
            reply_text = reply.get("text", "")
            if parse_mode:
                # prefix (result["message"] з warehouse_data.py) уже сам
                # екранований і навмисно несе <b>...</b> - re-escape зламав
                # би саме форматування, яке ми хочемо. Але reply_text тут -
                # це НАСТУПНЕ повідомлення (найчастіше store.get_message_
                # template(...) - шаблон, який адмін сам редагує в боті),
                # і воно НІКОЛИ не було під HTML раніше - будь-який "<"/"&"
                # у ньому (цілком реальне в довільному тексті) зламав би
                # відправку ВСЬОГО об'єднаного повідомлення. Екрануємо лише
                # цю частину перед склеюванням.
                reply_text = html.escape(reply_text)
            updated["text"] = f"{prefix}\n\n{reply_text}"
            if parse_mode:
                updated["parse_mode"] = parse_mode
            return updated
        return f"{prefix}\n\n{reply}"

    # Раніше жорстко показувала клавіатуру ДАННЫЕ (СКЛАД/ПРОДАЖИ) незалежно
    # від того, звідки насправді викликали (напр. АНТИСЕПТИРОВАНИЕ з
    # РЕАЛИЗАЦИЯ) — виглядало як "фантомне" чуже меню. Ця функція не знає
    # контексту виклику, тож найбезпечніший, завжди доречний варіант —
    # головне меню (той самий патерн, що й для будь-якого завершення дії).
    def _in_development_reply(self, title, store=None):
        return self._with_main_menu(f"{title} — в разработке.", store)

    def _is_main_menu_back_request(self, text):
        return _normalize_phrase(text) in {"назад", "back", "главное меню", "головне меню"}

    def _build_reply_by_mode(self, text, store, message, mode):
        context = self._message_context(message)
        pending = None
        if context["chat_id"] is not None and context["user_id"] is not None:
            pending = store.get_pending_operation(context["chat_id"], context["user_id"])
        # Аудит коду: раніше /start і "Помощь" перевірялись ДО того, як
        # pending взагалі завантажувався — на відміну від УСІХ сусідніх
        # перевірок нижче (data_menu/stock_data_menu/main_menu_back/
        # custom_root/placeholder/calculator), кожна з яких явно гейтиться
        # "not pending". Через це /start чи "Помощь" посеред приходу/продажу
        # міняли клавіатуру на головне меню, НЕ чіпаючи саму незавершену
        # операцію — наступний тап по кнопці меню йшов як відповідь на
        # застарілий крок. _build_reply/_build_online_ai_reply/_build_local_
        # ai_reply (нижче) вже й так коректно перевіряють pending ПЕРШИМ —
        # тепер /start/"Помощь" туди й доходять, замість перехоплення раніше.
        if not pending and self._is_start_command(text):
            return self._start_reply(store, context)
        if not pending and self._is_help_request(text):
            return self._show_help_reply(store)
        if not pending and self._is_data_menu_request(text):
            return self._enter_data_menu_node(store, context)
        if not pending and self._is_stock_data_menu_request(text):
            return self._stock_data_menu_reply(store, context)
        if not pending and self._is_main_menu_back_request(text):
            return self._main_menu_reply(store)
        if not pending:
            custom_root = self._custom_root_button_by_label(text, store)
            if custom_root:
                return self._enter_custom_button_node(custom_root, store, context)
        placeholder = self._warehouse_placeholder_command(text) if not pending else None
        if placeholder == "Фильтры":
            return self._start_stock_browse_filters(store, context)
        if placeholder:
            return self._in_development_reply(placeholder, store)
        if not pending and self._is_calculator_request(text, _normalize_phrase(text), store):
            return self._start_calculator_operation(text, store, context)
        if mode == "online_ai":
            return self._build_online_ai_reply(text, store, message)
        if mode == "local_ai":
            return self._build_local_ai_reply(text, store, message)
        return self._build_reply(text, store, message)

    # --- Службові утиліти діалогу: логування, /start, довідка, контекст повідомлення ---
    def _pipeline_status(self, reply, pending_before, pending_after):
        if pending_after:
            return "waiting"
        if isinstance(reply, dict) and reply.get("type") == "document":
            return "success"
        if isinstance(reply, dict) and reply.get("type") == "message":
            reply_text = str(reply.get("text") or "")
        else:
            reply_text = str(reply or "")
        if "Не знаю такую команду" in reply_text:
            return "unknown"
        if "отменена" in reply_text.lower() or "отменено" in reply_text.lower():
            return "cancelled"
        # Реальний ризик (аудит коду, 2026-08-14): раніше тут ще був блок, що
        # вгадував "waiting" по підрядках "Да / Нет"/"Напишите"/"Укажите"/
        # "выберите режим бота" у ТЕКСТІ відповіді - а ці слова трапляються і
        # в УСПІШНИХ підказках (напр. "Напишите /start, чтобы начать
        # заново"), тож могли помилково позначити реальний успіх як
        # "waiting" у журналі дій. Перевірено: КОЖНЕ місце в коді, що реально
        # показує один із цих текстів (усі "Да/Нет"-підтвердження в
        # income/sale/writeoff/antiseptic, і єдиний виклик
        # _bot_mode_selection_reply(change=True)) ЗАВЖДИ спершу викликає
        # store.save_pending_operation(...) - тобто pending_after (перевірка
        # вище) вже й так надійно ловить кожен такий випадок за РЕАЛЬНИМ
        # станом. Текстовий блок був чистим дублюванням, яке могло лише
        # помилятись, ніколи не додавало покриття - видалено.
        if pending_before and not pending_after:
            return "success"
        return "success"

    # Реальний ризик (аудит коду, 2026-08-14): це власна, спрощена копія
    # ПОРЯДКУ й перевірок реального диспетчера (_build_reply_by_mode вище) -
    # писалась вручну окремо для журналу, тож розійшлась із живою логікою
    # У ДВОХ місцях: (1) стартова/довідкова перевірка тут була ЛИШЕ
    # слеш-командою ("/help"), тоді як реальний диспетчер (_is_help_request)
    # так само розпізнає й голе "Помощь"/"справка"/"команды" без слеша -
    # такі повідомлення в журналі мовчки йшли як "unknown"; (2) кастомні
    # кореневі кнопки меню (_custom_root_button_by_label) тут не
    # перевірялись ВЗАГАЛІ - будь-яке натискання кастомної кнопки логувалось
    # як "unknown", хоча бот її коректно розпізнавав і обробляв. Тепер
    # викликає ТІ САМІ функції-предикати, у ТОМУ САМОМУ порядку, що й
    # _build_reply_by_mode - а не власне перевинайдене дублювання.
    def _legacy_command_hint(self, text, store):
        if self._is_start_command(text):
            return "start"
        if self._is_help_request(text):
            return "help"
        if self._is_data_menu_request(text):
            return "data_menu"
        if self._is_stock_data_menu_request(text):
            return "stock_data_menu"
        if self._is_main_menu_back_request(text):
            return "main_menu"
        custom_root = self._custom_root_button_by_label(text, store)
        if custom_root:
            return custom_root.get("action_code") or _normalize_phrase(custom_root.get("label") or "")
        placeholder = self._warehouse_placeholder_command(text)
        if placeholder:
            return _normalize_phrase(placeholder)
        # /status, /sheets, /first — службові DEBUG_TOOLS-команди, перевіряються
        # напряму в _build_reply (нижче), а не через find_command_code_in_text
        # (той дивиться в іншу, окрему таблицю кастомних команд) — без цієї
        # перевірки лишились би "unknown" у журналі, хоча бот їх коректно
        # обробляє.
        command_text = text.split(maxsplit=1)
        command = command_text[0].split("@", 1)[0].lower() if command_text else ""
        if command in {"/status", "/sheets", "/first", "/chatid"}:
            return command.lstrip("/")
        if self._is_calculator_request(text, _normalize_phrase(text), store):
            return "calculator"
        return store.find_command_code_in_text(text) or "unknown"

    def _pending_log_payload(self, pending):
        if not pending:
            return None
        payload = pending.get("payload") or {}
        return {
            "operation_type": pending.get("operation_type"),
            "status": pending.get("status"),
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
            "payload": payload,
        }

    def _reply_log_payload(self, reply):
        if isinstance(reply, dict):
            payload = dict(reply)
            if "path" in payload:
                payload["path"] = str(payload["path"])
            if "text" in payload:
                payload["text"] = self._sanitize_secret_text(payload["text"])
            if "caption" in payload:
                payload["caption"] = self._sanitize_secret_text(payload["caption"])
            return payload
        return {"type": "message", "text": self._sanitize_secret_text(str(reply or ""))}

    def _api_multipart(self, method, fields, file_field, file_path, timeout=30):
        boundary = f"----AIAutomationBoundary{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        body = bytearray()

        for name, value in fields.items():
            if value is None:
                continue
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        filename = file_path.name
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        body.extend(b"Content-Type: application/pdf\r\n\r\n")
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        request = urllib.request.Request(
            url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise self._telegram_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise TelegramApiError(description=f"Нет соединения с Telegram: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TelegramApiError(description="Telegram не ответил вовремя. Попробуйте еще раз.") from exc

        result = json.loads(payload)
        if not result.get("ok"):
            raise TelegramApiError(description=result.get("description", "Telegram API error"))
        return result.get("result")

    def _build_reply(self, text, store, message=None):
        context = self._message_context(message)
        pending = None
        if context["chat_id"] is not None and context["user_id"] is not None:
            pending = store.get_pending_operation(context["chat_id"], context["user_id"])
            if self._is_cancel_request(text, store):
                if pending:
                    store.delete_pending_operation(context["chat_id"], context["user_id"])
                    return self._cancelled_reply(store=store)
                return self._no_active_operation_reply(store)
            if pending and self._is_back_request(text):
                return self._handle_back_request(store, context, pending)
            if pending:
                return self._handle_pending_operation(text, store, context, pending)

        command_text = text.split(maxsplit=1)
        command = command_text[0].split("@", 1)[0].lower()
        argument = command_text[1].strip() if len(command_text) > 1 else ""

        if command == "/start":
            return self._start_reply(store, context)

        if command == "/help" or self._is_help_request(text):
            return self._show_help_reply(store)

        if self._is_data_menu_request(text):
            return self._enter_data_menu_node(store, context)

        if self._is_stock_data_menu_request(text):
            return self._stock_data_menu_reply(store, context)

        if self._is_main_menu_back_request(text):
            return self._main_menu_reply(store)

        custom_root = self._custom_root_button_by_label(text, store)
        if custom_root:
            return self._enter_custom_button_node(custom_root, store, context)

        placeholder = self._warehouse_placeholder_command(text)
        if placeholder == "Фильтры":
            return self._start_stock_browse_filters(store, context)
        if placeholder:
            return self._in_development_reply(placeholder, store)

        if command in {"/status", "/sheets", "/first", "/chatid"}:
            # Реальний баг з аудиту: ці 3 службові команди зовсім не мали
            # перевірки прав, тож будь-який користувач (навіть без
            # призначеної ролі) міг через /first ПРОДАЖА МАТЕРИАЛА чи
            # /first СКЛАД дістати ті самі сирі дані, які SALE_VIEW/
            # WAREHOUSE_VIEW щойно закрили в _start_sales_report_reply/
            # stock_income_history. DEBUG_TOOLS навмисно нікому, крім
            # ADMIN, не виданий (permissions.py) — це діагностичні команди,
            # не окрема бізнес-дія.
            denied = self._require_permission(store, context, perm.DEBUG_TOOLS)
            if denied:
                return denied

        # Задача користувача (2026-08-17): "де дістати chat_id групи?" -
        # звичайний Telegram-клієнт ніде цей id не показує (свідомо
        # прибрано з UI Telegram), а сторонні боти-утиліти (RawDataBot
        # тощо) або не можна додати в групу, або пересилання приховане
        # налаштуваннями приватності. Найнадійніший шлях - спитати
        # ВЛАСНОГО бота, який уже сидить у групі: команди починаються з
        # "/" і завжди доходять до бота незалежно від privacy mode.
        if command == "/chatid":
            return f"chat_id этого чата: {context['chat_id']}"

        if command == "/status":
            sheet_names = store.sheet_names()
            total_rows = sum(store.count_rows(sheet_name) for sheet_name in sheet_names)
            return (
                "SQLite-кеш готов.\n"
                f"Листов: {len(sheet_names)}\n"
                f"Строк: {total_rows}"
            )

        if command == "/sheets":
            sheet_names = store.sheet_names()
            return "Листы:\n" + "\n".join(f"- {name}" for name in sheet_names)

        if command == "/first":
            return self._first_rows_reply(argument, store)

        command_code = store.find_command_code_in_text(text)
        if command_code == "stock_balance":
            denied = self._require_permission(store, context, perm.WAREHOUSE_VIEW)
            if denied:
                return denied
            return self._stock_balance_reply(store, context)
        if command_code == "add_income":
            return self._start_income_operation(text, store, context)
        if command_code == "stock_sale":
            return self._start_sale_operation(text, store, context)
        if command_code == "calculator":
            return self._start_calculator_operation(text, store, context)
        if command_code == "cancel_operation":
            return self._no_active_operation_reply(store)
        if command_code == "help":
            return self._help_reply(store)

        return self._help_reply(store, title="Список доступных вам команд:")

    def _start_reply(self, store, context):
        user_name = self._telegram_display_name(context)
        role = self._telegram_user_role(store, context)
        text = "\n".join(
            [
                f"Привет, {user_name}. Я твой помощник.",
                f"Твой статус: {role}.",
                "",
                "Тебе доступны следующие операции:",
                "- Приход — принять товар на склад.",
                "- Реализация — оформить продажу.",
                "- Данные — склад по категориям/фильтру, продажи.",
                "- Калькулятор — посчитать числа, кубатуру или штуки.",
                "- Отмена — отменить текущую операцию.",
                "- Помощь — показать все доступные команды.",
            ]
        )
        return {
            "type": "message",
            "text": text,
            "reply_markup": self._main_command_keyboard(store),
        }

    def _telegram_display_name(self, context):
        full_name = str(context.get("full_name") or "").strip()
        username = str(context.get("username") or "").strip()
        if full_name and full_name != "local":
            return full_name
        if username:
            return f"@{username}"
        user_id = context.get("user_id")
        return str(user_id) if user_id not in (None, "", "local") else "пользователь"

    def _telegram_user_role(self, store, context):
        user_id = str(context.get("user_id") or "")
        if not user_id or user_id == "local":
            return "пользователь"
        for _, telegram_id, _, _, role, _ in store.list_users():
            if str(telegram_id) == user_id:
                return self._public_user_role(role)
        return "пользователь"

    def _public_user_role(self, role):
        normalized = _normalize_phrase(role)
        if normalized in {"admin", "админ", "administrator", "администратор"}:
            return "админ"
        if normalized in {"user", "пользователь", "користувач"}:
            return "пользователь"
        # Задача користувача (аудит коду): без цієї гілки нова людина при
        # першому /start побачила б "Твой статус: guest." - англійське
        # слово в чисто російському тексті бота (давнє правило проєкту).
        if normalized in {"guest", "гость", "гість"}:
            return "гость"
        return str(role or "пользователь").strip() or "пользователь"

    def _help_reply(self, store=None, prefix="", title="Доступные команды:"):
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.extend(
            [
                title,
                "Приход - принять товар на склад",
                "Реализация - оформить продажу",
                "Данные - склад по категориям/фильтру, продажи",
                "Калькулятор - посчитать числа, м3 или количество штук",
                "Отмена - отменить текущую операцию",
                "Помощь - показать все доступные команды",
            ]
        )
        return {
            "type": "message",
            "text": "\n".join(lines),
            "reply_markup": self._main_command_keyboard(store),
        }

    # "Показать справку" (Задача користувача: редагований текст) —
    # використовується скрізь, де раніше був self._help_reply(store) З
    # ТИПОВИМИ prefix/title (бо це буквально одне й те саме повідомлення:
    # bare "помощь"/"/help", і кнопка "Помощь" у дереві). ІНШІ виклики
    # _help_reply (з власним title, напр. "Список доступных вам команд:")
    # НЕ чіпаємо — той текст семантично інший, лишається хардкодженим.
    def _show_help_reply(self, store):
        return {
            "type": "message",
            "text": store.get_message_template("show_help", BOT_MESSAGE_DEFAULTS["show_help"]),
            "reply_markup": self._main_command_keyboard(store),
        }

    def _is_help_request(self, text):
        normalized = _normalize_phrase(text)
        return normalized in {
            "помощь",
            "help",
            "справка",
            "команды",
            "список команд",
        }

    def _is_cancel_request(self, text, store):
        normalized = _normalize_phrase(text)
        cancel_words = {
            "отмена",
            "отменить",
            "стоп",
            "скасувати",
            "відміна",
            "відмінити",
            # "Назад" — тепер ОКРЕМА дія (_is_back_request/_handle_back_request):
            # повертає на попередній крок ТІЄЇ Ж операції там, де це визначено,
            # і лише як фолбек поводиться як повне скасування. "Главное меню" —
            # явний, завжди повний вихід (те саме, що й "Отмена"), незалежно
            # від того, наскільки глибоко триває операція.
            "главное меню",
            "головне меню",
        }
        return normalized in cancel_words or store.find_command_code_by_phrase(text) == "cancel_operation"

    def _is_back_request(self, text):
        return _normalize_phrase(text) in {"назад", "back"}

    # "Назад" повертає на конкретно визначений попередній крок ТІЄЇ Ж
    # операції там, де це має сенс (звіти, вибір категорії ПРИХОД/
    # РЕАЛИЗАЦИЯ) — кожна гілка нижче явно каже, який саме крок є
    # "попереднім" для даного (operation_type, status). Для решти кроків
    # (вільний ввід тексту всередині збору даних, проміжні кнопкові кроки
    # приходу/продажі — Похожие позиции, Фильтры складу, Способ оплаты тощо)
    # окремого "кроку назад" ще не визначено — там "Назад" поводиться так
    # само, як і раніше ("Отмена": повне скасування), щоб нічого не
    # зламати. Розширювати цей список — окрема задача на кожен крок.
    def _handle_back_request(self, store, context, pending):
        operation_type = pending["operation_type"]
        status = pending["status"]
        payload = pending["payload"]

        if operation_type == "sales_report":
            if status == "choose_period":
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._enter_data_menu_node(store, context, re_entering=True)
            if status == "choose_category":
                return self._sales_period_prompt_reply(store, context, payload)
            if status == "choose_format":
                return self._sales_category_prompt_reply(store, context, payload)

        if operation_type == "antiseptic_report":
            if status == "choose_period":
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._enter_data_menu_node(store, context, re_entering=True)
            if status == "choose_format":
                # Нема кроку категорії (як у sales_report) — "назад" з формату
                # йде одразу до вибору періоду.
                return self._antiseptic_period_prompt_reply(store, context, payload)

        if operation_type == "sales_by_client_report":
            if status == "choose_period":
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._enter_data_menu_node(store, context, re_entering=True)
            if status == "choose_format":
                return self._sales_by_client_period_prompt_reply(store, context, payload)

        # Немає кроку періоду взагалі (живий знімок складу) — єдиний можливий
        # статус, "назад" з нього завжди веде прямо в ДАННЫЕ (той самий
        # найпростіший патерн, що й у stock_report нижче).
        if operation_type == "low_stock_report" and status == "choose_format":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._enter_data_menu_node(store, context, re_entering=True)

        if operation_type == "stock_report" and status == "choose_format":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._stock_data_menu_reply(store, context)

        if operation_type == "stock_report" and status == "choose_stock_message_limit":
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "stock_report", "choose_format", payload
            )
            return {
                "type": "message",
                "text": "В каком виде показать?",
                "reply_markup": self._stock_report_format_keyboard(),
            }

        if operation_type == "stock_browse" and status == "choose_stock_category":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._enter_data_menu_node(store, context, re_entering=True)

        if operation_type in {"add_income", "stock_sale"} and status == "choose_category":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        if operation_type == "stock_writeoff" and status == "choose_category":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        if operation_type == "stock_sale" and status == "choose_sale_payment_method":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        if operation_type == "stock_sale" and status == "sale_all_in_one":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        if operation_type == "stock_writeoff" and status == "writeoff_all_in_one":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        if operation_type == "add_income" and status == "income_all_in_one":
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._main_menu_reply(store)

        # Реальний баг з аудиту: "Назад" на БУДЬ-ЯКОМУ іншому кроці збору
        # даних приходу/продажу/антисептирования (colect_income_missing,
        # ask_sale_client/price/payment_method, Похожие позиции ->
        # Фильтры складу тощо) падав на фолбек нижче — повне скасування,
        # яке мовчки стирало вже введені рядки/клієнта/ціну/оплату.
        # Найгірший випадок: після "Похожие позиции" -> "Весь остаток" ->
        # "Назад" стирало майже готову продажу. Тепер зберігає все — той
        # самий "reopen"-екран, що й "Редактировать" (не справжній
        # покроковий "крок назад" для кожного статусу окремо, але дані
        # більше НІКОЛИ не губляться мовчки).
        if operation_type in {"add_income", "stock_sale"}:
            return (
                self._reopen_sale_collection(store, context, payload)
                if operation_type == "stock_sale"
                else self._reopen_income_collection(store, context, payload)
            )
        if operation_type == "antiseptic_service":
            return self._reopen_antiseptic_collection(store, context, payload)

        if operation_type == "stock_writeoff":
            return self._reopen_writeoff_collection(store, context, payload)

        if operation_type == "custom_menu":
            node_row = store.get_custom_button(payload["node_id"])
            node = self._custom_button_full_row_to_node(node_row) if node_row else None
            if not node or node["parent_id"] is None:
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._main_menu_reply(store)
            parent_row = store.get_custom_button(node["parent_id"])
            if not parent_row:
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._main_menu_reply(store)
            parent_node = self._custom_button_full_row_to_node(parent_row)
            return self._enter_custom_button_node(parent_node, store, context, re_entering=True)

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._cancelled_reply(store=store)

    def _message_context(self, message):
        if not message:
            return {
                "chat_id": "local",
                "chat_type": "private",
                "user_id": "local",
                "username": "",
                "full_name": "local",
            }
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        full_name = " ".join(
            part for part in [user.get("first_name", ""), user.get("last_name", "")] if part
        )
        # Задача користувача: автоматичне ім'я кожному, щоб не вводити вручну.
        # Коли Telegram взагалі не дає ні імені, ні username (рідкісний
        # випадок) - "Гость {id}" замість голого номера: читається як
        # присвоєне ім'я, а не як помилка/порожнє поле, і в "Персонал", і в
        # привітанні бота (_telegram_display_name читає це саме поле).
        user_id = user.get("id", "")
        return {
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type", ""),
            "user_id": user.get("id"),
            "username": user.get("username", ""),
            "full_name": full_name or user.get("username", "") or f"Гость {user_id}",
        }

    def _yes_no(self, text):
        normalized = _normalize_phrase(text)
        if normalized in {
            "да",
            "da",
            "так",
            "tak",
            "yes",
            "y",
            "ok",
            "okay",
            "окей",
            "ок",
            "ага",
            "угу",
            "добре",
            "хорошо",
            "podtverzhdayu",
            "подтверждаю",
        }:
            return True
        if normalized in {
            "нет",
            "net",
            "ні",
            "ni",
            "no",
            "n",
            "не",
            "ne",
            "nope",
            "otmena",
            "отмена",
        }:
            return False
        return None

    # Реальний баг з аудиту: раніше перевіряв ЛИШЕ balance_volume — позиція,
    # що ведеться в площі (Вагонка) чи мп (25x50/30x50/50x50), з нульовим
    # (порожнім) balance_volume мовчки вважалась "без залишку", навіть якщо
    # свій власний залишок (area/linear) насправді був додатнім.
    def _warehouse_row_has_balance(self, row, columns):
        return (
            _number_value(row_value(row, columns["balance_qty"])) > 0
            or _number_value(row_value(row, columns["balance_volume"])) > 0
            or _number_value(row_value(row, columns.get("balance_area"))) > 0
            or _number_value(row_value(row, columns.get("balance_linear"))) > 0
        )

    # Реальний баг з аудиту: раніше вимір визначався за ТЕКСТОМ у колонці
    # "Основная ед. учета" — та сама проблема, що й у _stock_balance_rows:
    # цей текст записується ОДИН РАЗ, лише при створенні нового рядка
    # складу, і ніколи не оновлюється для вже існуючого (а через звичайний
    # GUI-редактор таблиці й узагалі може бути вписаний вручну без жодної
    # перевірки). Тепер визначаємо вимір за товаром/розміром — так само, як
    # і скрізь у коді (_row_measure_kind), а не за текстом, що міг застаріти.
    def _warehouse_row_summary(self, row, columns):
        item = {
            "thickness": row_value(row, columns["thickness"]),
            "width": row_value(row, columns["width"]),
            "length": row_value(row, columns["length"]),
        }
        product = row_value(row, columns["product"])
        is_area_based = self._is_area_based_product(product)
        is_linear_based = not is_area_based and self._is_linear_meter_size(item["thickness"], item["width"])
        if is_area_based:
            measure_column, measure_unit = "balance_area", "м2"
        elif is_linear_based:
            measure_column, measure_unit = "balance_linear", "мп"
        else:
            measure_column, measure_unit = "balance_volume", "м3"
        return (
            f"{product} / "
            f"{row_value(row, columns['breed'])} / "
            f"{income_item_size(item)} — "
            f"остаток {_display_bot_number(row_value(row, columns['balance_qty']))} шт / "
            f"{_display_bot_number(row_value(row, columns.get(measure_column)))} {measure_unit}"
        )

    def _text_equal(self, left, right):
        return _normalize_phrase(left) == _normalize_phrase(right)

    def _number_equal(self, left, right):
        return abs(_number_value(left) - _number_value(right)) < 0.000001
