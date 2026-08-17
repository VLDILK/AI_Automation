"""Прихід/продаж: старт операції, категорія, мега-диспетчер _handle_pending_operation, продовження операції, редагування вже введених даних, "підозріла кількість", пошук рядків складу/прев'ю. Найбільший і найкрихкіший блок (керування СТАНОМ операції) - парсинг/нормалізація тексту живе окремо, у telegram_dialog_income_sale_parsing.py. Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти."""

import json
import re
import sqlite3

import permissions as perm
from utils import (
    _display_bot_number,
    _normalize_phrase,
    _number_value,
)
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
    INCOME_QUANTITY_TOLERANCE,
    INCOME_VOLUME_TOLERANCE,
    apply_antiseptic_operation,
    apply_income_operation,
    apply_sale_operation,
    display_product_name,
    income_item_size,
    item_measure_kind,
    ITEM_MEASURE_UNIT,
    resolve_operation_for_payload,
    row_value,
    sale_position_text,
    warehouse_rows,
)

class IncomeSaleFlowDialogMixin:

    # stock_sale-специфічна клавіатура кроку підтвердження (Задача
    # користувача: накопичення кількох позицій в одну продажу) —
    # add_income і далі використовує звичайну _confirmation_keyboard.
    # from_webapp_form=True — Задача користувача: "якщо в програмі вже є
    # редагувати [форма], то редагування ботом непотрібне", плюс "Продолжить"
    # (додати ще позицію ЧЕРЕЗ ЧАТ) веде у старий текстовий вибір категорії -
    # той самий клас "пастки", який форма й так закриває власним кошиком.
    # Лишається лише бінарне рішення - Оформить продажу/Отмена.
    def _sale_confirm_keyboard(self, from_webapp_form=False):
        if from_webapp_form:
            return {
                "keyboard": [
                    [{"text": "Оформить продажу"}, {"text": "Отмена"}],
                    [{"text": self._WEBAPP_FORM_RETURN_LABEL}],
                ],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            }
        return {
            "keyboard": [
                [{"text": "Оформить продажу"}, {"text": "Продолжить"}],
                [{"text": "Редактировать"}, {"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _free_value_role_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Порода"}, {"text": "Тип продукта"}],
                [{"text": "Товар"}, {"text": "Редактировать"}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _sale_not_found_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Похожие позиции"}, {"text": "Весь остаток"}],
                [{"text": "Редактировать"}, {"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _quantity_issue_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Да"}, {"text": "Нет"}],
                [{"text": "Оставить как есть"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _quantity_change_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Да"}, {"text": "Нет"}],
                [{"text": "Изменить цифру"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _edit_prompt_reply(self):
        return {
            "type": "message",
            "text": (
                "Что нужно отредактировать?\n"
                "Введите новые данные. Я заменю только то, что вы укажете.\n"
                "Например: Доска AD, 6000 или 120 шт."
            ),
            "reply_markup": self._cancel_only_keyboard(),
        }

    # =====================================================================
    # ДІАЛОГОВА МАШИНА СТАНІВ: прихід і продажа складу.
    # Найбільший і найкрихкіший блок файлу — саме тут живуть усі "нелогічні"
    # місця, які ми знаходили й правили (див. історію розмови). Стан поточної
    # операції зберігається в store.pending_operation (SQLite), поле "status"
    # визначає, який з _handle_* методів нижче обробить наступну відповідь
    # користувача.
    # =====================================================================

    # --- Запуск операції приход/продажа за командою користувача ---
    def _start_income_operation(self, text, store, context):
        denied = self._require_permission(store, context, perm.INCOME)
        if denied:
            return denied
        if store.is_legacy_non_form_button_hidden("income"):
            return self._start_income_all_in_one_reply(store, context)
        if self._is_income_command_only(text, store):
            return self._start_income_category_menu(store, context)

        payload, error = self._parse_income_message(text)
        if error:
            return error

        payload.update(self._new_income_payload(text, context))
        return self._continue_income_operation(store, context, payload)

    def _start_sale_operation(self, text, store, context):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        if store.is_legacy_non_form_button_hidden("sale"):
            return self._start_sale_all_in_one_reply(store, context)
        if self._is_sale_command_only(text, store):
            return self._start_sale_payment_method_menu(store, context)

        sale_text, extra_fields = self._extract_sale_fields(text, store)
        sale_text = self._strip_sale_command_words(sale_text)
        payload, error = self._parse_income_message(sale_text)
        if error:
            return error

        payload.update(self._new_sale_payload(text, context))
        payload.update(extra_fields)
        return self._continue_sale_operation(store, context, payload)

    # Кнопкове дерево категорій (ПРИХОД/РЕАЛИЗАЦИЯ -> ДОСКА AD/KD/OSB/
    # ВАГОНКА/АНТИСЕПТИРОВАНИЕ). Кнопка лише наперед заповнює product/
    # condition у payload і одразу передає в continue_operation — той самий
    # рушій збору/валідації/підтвердження, що й вільний текст. АНТИСЕПТИРОВАНИЕ
    # (лише в реалізації) веде у ЗОВСІМ ІНШИЙ, окремий флоу
    # (_start_antiseptic_operation/_continue_antiseptic_operation) — інша
    # структура даних (клієнт+обсяг+ціна+оплата, без розмірів/складу), тому
    # НЕ через continue_operation тут.
    # Задача користувача ("додай можливість додавати та видаляти ці
    # параметри (кнопки), а також перейменовувати"): store-driven —
    # будується з store.list_operations(parent_action_code), тож жива
    # категорія (додана/перейменована/видалена через "Дії") одразу
    # показується так, як реально налаштована. АНТИСЕПТИРОВАНИЕ для
    # "start_sale" вже є серед list_operations (kind='service'), окремого
    # include_antiseptic-прапорця більше не потрібно — parent_action_code
    # сам визначає, чи він там є. Пари по 2 в рядок — той самий вигляд,
    # що й раніше з фіксованими 4-5 категоріями, тепер узагальнено для
    # будь-якої кількості.
    def _category_keyboard(self, store, parent_action_code):
        labels = [operation[4] for operation in store.list_operations(parent_action_code)]
        rows = []
        pending_label = None
        for label in labels:
            if pending_label is None:
                pending_label = label
            else:
                rows.append([{"text": pending_label}, {"text": label}])
                pending_label = None
        if pending_label is not None:
            rows.append([{"text": pending_label}])
        rows.append([{"text": "Назад"}])
        return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}

    # store.resolve_operation_category — єдина точка "яка це категорія"
    # (поточні мітки + всі збережені синоніми, включно з "osb" латиницею
    # та старими назвами після перейменування). Повертає той самий
    # контракт, що й раніше: None / "antiseptic" / (product, condition).
    def _category_from_text(self, store, parent_action_code, text):
        normalized = _normalize_phrase(text)
        if not normalized:
            return None
        # Легасі-толерантність до старих клавіатур/скриптів зі суфіксом
        # "(в разработке)" — прив'язана саме до цього фіксованого префіксу
        # (не до поточної мітки дії), незалежно від подальших перейменувань.
        if normalized.startswith("антисептирование"):
            return "antiseptic"
        operation_id = store.resolve_operation_category(parent_action_code, text)
        if operation_id is None:
            return None
        operation = store.get_operation(operation_id)
        if operation is None:
            return None
        _op_id, _code, kind, _requires_identity, _label, _parent, prefill_json, *_rest = operation
        if kind == "service":
            return "antiseptic"
        prefill = json.loads(prefill_json) if prefill_json else {}
        return (prefill.get("product"), prefill.get("condition"))

    def _parent_action_code_for_operation_type(self, operation_type):
        return "start_sale" if operation_type == "stock_sale" else "start_income"

    def _category_label(self, product, condition):
        return f"{product} {condition}" if condition else product

    # Чи вже є в payload щось, крім самої обраної категорії (порода або хоч
    # один розмір/кількість) — визначає, чи можна тихо перемкнути категорію,
    # чи спершу треба спитати підтвердження (щоб не загубити вже введене).
    def _income_has_entered_data(self, payload):
        if payload.get("breed"):
            return True
        for row in payload.get("rows") or []:
            if any(
                _number_value(row.get(field)) > 0
                for field in ("thickness", "width", "length", "quantity", "volume", "area", "linear")
            ):
                return True
        return False

    def _current_category_matches(self, payload, product, condition):
        return self._text_equal(payload.get("product"), product) and _normalize_phrase(
            payload.get("condition") or ""
        ) == _normalize_phrase(condition or "")

    # Викликається на кожній відповіді під час збору даних приходу/продажу:
    # якщо користувач замість розміру/кількості раптом назвав ІНШУ категорію
    # (напр. натиснув "OSB", хоча щойно обрав "ДОСКА AD") — це реальний намір
    # змінити категорію, а не випадкове слово. Повертає None, якщо це не той
    # випадок (звичайні дані йдуть у наявний парсер без змін).
    def _handle_category_reselection(self, answer, store, context, payload, operation_type, continue_operation):
        parent_action_code = self._parent_action_code_for_operation_type(operation_type)
        category = self._category_from_text(store, parent_action_code, answer.strip())
        if category is None:
            return None
        if category == "antiseptic":
            request = {"antiseptic": True}
            new_label = self._antiseptic_operation_label(store)
        else:
            product, condition = category
            if self._current_category_matches(payload, product, condition):
                return None
            request = {"product": product, "condition": condition}
            new_label = self._category_label(product, condition)

        if not self._income_has_entered_data(payload):
            return self._apply_category_change(
                answer, context, request, operation_type, continue_operation, store, payload
            )

        current_label = self._category_label(payload.get("product"), payload.get("condition")) or "текущей категории"
        payload["category_change_request"] = request
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            operation_type,
            "confirm_category_change",
            payload,
        )
        return self._yes_no_reply(
            f'Вы уже вводите данные для "{current_label}".\n'
            f'Сменить категорию на "{new_label}"? Введенные данные будут потеряны.\n'
            "Да / Нет"
        )

    def _apply_category_change(self, answer, context, request, operation_type, continue_operation, store, prior_payload):
        label = self._antiseptic_operation_label(store) if request.get("antiseptic") else self._category_label(
            request.get("product"), request.get("condition")
        )
        if request.get("antiseptic"):
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            carry_over = prior_payload if operation_type == "stock_sale" else None
            return self._prepend_reply_text(
                f"Категория изменена: {label}.",
                self._start_antiseptic_operation(store, context, carry_over=carry_over),
            )
        new_payload = (
            self._new_sale_payload(answer, context)
            if operation_type == "stock_sale"
            else self._new_income_payload(answer, context)
        )
        new_payload["product"] = request.get("product")
        if request.get("condition"):
            new_payload["condition"] = request.get("condition")
        if operation_type == "stock_sale":
            self._carry_over_sale_accumulation(prior_payload, new_payload)
        result = continue_operation(store, context, new_payload)
        return self._prepend_reply_text(f"Категория изменена: {label}.", result)

    def _start_income_category_menu(self, store, context):
        denied = self._require_permission(store, context, perm.INCOME)
        if denied:
            return denied
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "add_income",
            "choose_category",
            {},
        )
        return {
            "type": "message",
            "text": store.get_message_template("start_income", BOT_MESSAGE_DEFAULTS["start_income"]),
            "reply_markup": self._category_keyboard(store, "start_income"),
        }

    # carry_over — payload, з якого прийшли (реальний баг з аудиту: детур в
    # "Антисептирование" посеред накопиченої продажі й відхилення typo-
    # підказки кількості обидва раніше губили вже введені completed_
    # positions/client/payment_method, бо цей метод завжди будував порожній
    # payload "з нуля"). Той самий _carry_over_sale_accumulation, що вже
    # переносить ці поля при звичайній зміні категорії.
    def _start_sale_category_menu(self, store, context, carry_over=None):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        payload = {}
        if carry_over:
            self._carry_over_sale_accumulation(carry_over, payload)
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_sale",
            "choose_category",
            payload,
        )
        return {
            "type": "message",
            "text": store.get_message_template(
                "start_sale_category_prompt", BOT_MESSAGE_DEFAULTS["start_sale_category_prompt"]
            ),
            "reply_markup": self._category_keyboard(store, "start_sale"),
        }

    # Нове правило користувача: спосіб оплати (ЕФАКТУРА Б/Н, ЕФАКТУРА Н,
    # АЛЬТ) тепер обирається ОДРАЗУ після "Реализация", ДО вибору категорії
    # товару — а не наприкінці збору даних (ask_sale_payment_method
    # лишається як запасний варіант для "одним реченням" повідомлень, де
    # користувач одразу вписав усе, включно зі способом оплати).
    def _start_sale_payment_method_menu(self, store, context):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_sale",
            "choose_sale_payment_method",
            {},
        )
        return {
            "type": "message",
            "text": store.get_message_template("start_sale", BOT_MESSAGE_DEFAULTS["start_sale"]),
            "reply_markup": self._payment_method_keyboard(store),
        }

    # "Реализация (форма)" - друга кнопка поруч зі звичайною "РЕАЛИЗАЦИЯ"
    # (Задача користувача: "розділити" кнопку на 2 - один окремий шлях
    # крок-за-кроком як є, і одна кнопка, де все обирається одразу). На
    # відміну від _start_sale_payment_method_menu - жодного чатового кроку
    # (спосіб оплати/категорія) немає взагалі: категорія й спосіб оплати -
    # звичайні поля самої форми (_webapp_all_in_one_sale_context), обидва
    # реактивно показують потрібний набір полів. pending-статус
    # "sale_all_in_one" - лише "форма ще не подана" (payload завжди
    # порожній, справжній payload будується вже після подання форми,
    # _continue_sale_all_in_one_submission у telegram_dialog_core.py).
    # "Приход (форма)" - той самий fallback-вхід, що й у sale/writeoff:
    # спрацьовує лише коли кнопка головного меню НЕ змогла понести web_app
    # напряму (тунель ще не піднятий на момент показу клавіатури).
    def _start_income_all_in_one_reply(self, store, context, resume_payload=None):
        denied = self._require_permission(store, context, perm.INCOME)
        if denied:
            return denied
        web_app = self._income_all_in_one_webapp_button(store, resume_payload=resume_payload)
        if web_app is None:
            return self._with_main_menu(
                "Приход одной формой сейчас недоступен (форма не подключена). "
                "Используйте обычный «ПРИХОД».",
                store,
            )
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "add_income", "income_all_in_one", {},
        )
        keyboard = {
            "keyboard": [
                [{"text": "Заполнить форму прихода", **web_app}],
                [{"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
        }
        return {
            "type": "message",
            "text": store.get_message_template("start_income_form", BOT_MESSAGE_DEFAULTS["start_income_form"]),
            "reply_markup": keyboard,
        }

    def _start_sale_all_in_one_reply(self, store, context, resume_payload=None):
        # Fallback-шлях: спрацьовує лише коли кнопка головного меню НЕ змогла
        # понести web_app напряму (тунель ще не піднятий на момент показу
        # клавіатури) - людина тоді бачить звичайну текстову кнопку, тап якою
        # й веде сюди. Якщо тунель піднявся вже до цього моменту - показуємо
        # ту саму форму, лише одним кроком пізніше, а не "недоступно".
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        web_app = self._sale_all_in_one_webapp_button(store, resume_payload=resume_payload)
        if web_app is None:
            return self._with_main_menu(
                "Продажа одной формой сейчас недоступна (форма не подключена). "
                "Используйте обычную «РЕАЛИЗАЦИЯ».",
                store,
            )
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "stock_sale", "sale_all_in_one", {},
        )
        keyboard = {
            "keyboard": [
                [{"text": "Заполнить форму продажи", **web_app}],
                [{"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
        }
        return {
            "type": "message",
            "text": store.get_message_template("start_sale_form", BOT_MESSAGE_DEFAULTS["start_sale_form"]),
            "reply_markup": keyboard,
        }

    # Реалізація тепер може накопичувати кілька товарних позицій в одну
    # продажу (Задача користувача: "клієнти інколи замовляють по декілька
    # видів пиломатеріалу") — спосіб оплати й клієнт спільні на всю
    # продажу, completed_positions — уже завершені позиції. Вибір категорії
    # для НАСТУПНОЇ позиції (як явним натисканням кнопки, так і одразу
    # вільним текстом одним реченням) будує payload "з нуля"
    # (_new_sale_payload/_new_income_payload) — це накопичення інакше
    # губилось би щоразу.
    def _carry_over_sale_accumulation(self, payload, new_payload):
        for key in ("payment_method", "completed_positions", "client", "address", "confirmed_new"):
            if payload.get(key):
                new_payload[key] = payload[key]

    # Поля ОДНІЄЇ товарної позиції продажу (те, що відрізняється між
    # "доска КД сосна" і, скажімо, "вагонка сосна" в тій самій продажі) —
    # решта payload (клієнт/спосіб оплати/user/confirmed_new тощо) спільна
    # на всю продажу і НЕ архівується разом з позицією.
    # Задача користувача: "чому розпізнало лише 1 антисептирование, якщо я
    # 2 антисептіровав?" - "antiseptic" тепер частина позиції (як price_per_
    # unit), тож переживає архівування разом зі своєю позицією замість
    # губитись, коли форма переходить до наступного товару.
    _SALE_POSITION_FIELDS = ("product", "condition", "breed", "rows", "price_per_unit", "antiseptic")

    # Викликається кнопкою "Продовжити" (Задача користувача: кілька видів
    # пиломатеріалу в ОДНІЙ продажі -> один підсумковий звіт бухгалтеру).
    # Знімок поточної позиції йде в completed_positions зі СВОЇМ порахованим
    # total_amount (бо після очищення price_per_unit/rows цієї позиції
    # _sale_total_amount(payload) вже нічого не порахує) — решта payload
    # лишається як є, дозволяючи одразу почати збір наступної позиції з
    # choose_category, не гублячи client/payment_method/confirmed_new.
    # Задача користувача (2026-08-14): "щоб міг продовжувати приход і
    # внести кілька різних позицій. так же як це реалізовано в реалізації" -
    # той самий принцип, що й _SALE_POSITION_FIELDS/_archive_current_sale_
    # position_and_reset вище, для приходу. "antiseptic" немає сенсу тут
    # (лише продаж має цю доплату), "supplier"/"total_amount" - реальні
    # поля приходу (income_columns, warehouse_data.py). На відміну від
    # продажу - total_amount тут НЕ рахується окремим методом (немає
    # "_income_total_amount"): income_sheet_values уже й так рахує його з
    # price_per_unit при записі кожної позиції, знімок лише переносить те,
    # що людина ввела явно.
    _INCOME_POSITION_FIELDS = ("product", "condition", "breed", "rows", "price_per_unit", "total_amount", "supplier")

    def _archive_current_income_position_and_reset(self, payload):
        position = {
            field: payload[field] for field in self._INCOME_POSITION_FIELDS if field in payload
        }
        completed_positions = list(payload.get("completed_positions") or [])
        completed_positions.append(position)
        reset_payload = dict(payload)
        for field in self._INCOME_POSITION_FIELDS:
            reset_payload.pop(field, None)
        reset_payload["completed_positions"] = completed_positions
        return reset_payload

    def _archive_current_sale_position_and_reset(self, payload):
        position = {
            field: payload[field] for field in self._SALE_POSITION_FIELDS if field in payload
        }
        position["total_amount"] = self._sale_total_amount(payload)
        completed_positions = list(payload.get("completed_positions") or [])
        completed_positions.append(position)
        reset_payload = dict(payload)
        for field in self._SALE_POSITION_FIELDS:
            reset_payload.pop(field, None)
        reset_payload["completed_positions"] = completed_positions
        return reset_payload

    def _new_income_payload(self, text, context):
        return {
            "operation_kind": "income",
            "original_text": text,
            "user": {
                "id": context["user_id"],
                "username": context["username"],
                "full_name": context["full_name"],
            },
            "confirmed_new": [],
        }

    def _new_sale_payload(self, text, context):
        payload = self._new_income_payload(text, context)
        payload["operation_kind"] = "sale"
        return payload

    # Продажні поля (клієнт/ціна/оплата/документ/менеджер/коментар) —
    # окремий, ізольований розбір "мітка: значення" рядків, який НЕ йде через
    # спільний _parse_income_message (той повертає лише product/breed/
    # condition, решта полів там мовчки губилась би). Явний роздільник
    # (:/=/-/—) обов'язковий — на відміну від product/breed, де можливий і
    # "мітка значення" без роздільника, щоб не хапати випадкові слова з
    # вільного тексту продажу.
    # total_amount навмисне ВІДСУТНІЙ у цьому словнику — за новим правилом
    # користувача суму більше не можна вказати напряму (ні "Сумма:", ні
    # "Итого:"), бот завжди рахує її сам як price_per_unit * обсяг.
    _SALE_FIELD_ALIASES = {
        "client": ["клиент", "заказчик"],
        "address": ["адрес выгрузки", "адрес доставки", "адрес"],
        "price_per_unit": ["цена"],
        "payment_method": ["способ оплаты", "форма оплаты", "оплата"],
        "document_type": ["тип документа", "документ"],
        "manager": ["ответственный менеджер", "ответственный", "менеджер"],
        "comment": ["комментарий", "коммент"],
        # Необов'язкове — якщо не вказано, sale_sheet_values (warehouse_data.py)
        # підставляє сьогоднішню дату сама (_parse_date_text(None) -> None).
        "date": ["дата продажи", "дата"],
    }

    # Аудит коду: "цена за куб"/"цена за мп" тощо не розпізнавались — у
    # словнику вище були жорстко прописані лише "цена за ед"/"цена за м3",
    # тож будь-яка інша одиниця ("за куб", "за мп", "за штуку") не збігалась
    # із жодним варіантом, падала на найкоротшу мітку "цена", і весь хвіст
    # "за куб: 6200" ставав самим ЗНАЧЕННЯМ ціни (_number_value з такого
    # тексту = 0, тиха неправильна ціна). Той самий клас — "ответственный
    # за продажу: Иван" ставив "за продажу: Иван" у поле менеджера дослівно.
    # Замість переліку конкретних одиниць — необов'язковий "за <слово>" одразу
    # після мітки, який ковтається ЯК ЧАСТИНА МІТКИ (до роздільника), а не
    # потрапляє у значення. Стосується price_per_unit/manager/payment_method —
    # три поля, де ця конструкція трапляється в реальних повідомленнях
    # ("Способ оплаты за услугу: нал" — той самий гандж, знайдений повторною
    # перевіркою охоплення цього фіксу; бот сам підказує саме "Способ оплаты"
    # як мітку поля, тож користувач, що ехає цю підказку з "за <іменник>",
    # так само реалістичний, як і вже виправлені price_per_unit/manager).
    _SALE_FIELD_UNIT_SUFFIX_FIELDS = {"price_per_unit", "manager", "payment_method"}
    _SALE_FIELD_UNIT_SUFFIX_PATTERN = r"(?:\s*за\s*\S+)?"

    def _extract_sale_fields(self, text, store):
        fields = {}
        kept_lines = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            matched = False
            if stripped:
                for field, labels in self._SALE_FIELD_ALIASES.items():
                    unit_suffix = (
                        self._SALE_FIELD_UNIT_SUFFIX_PATTERN
                        if field in self._SALE_FIELD_UNIT_SUFFIX_FIELDS
                        else ""
                    )
                    for label in sorted(labels, key=len, reverse=True):
                        # Роздільник (:/=/-/—) необов'язковий — люди часто
                        # пишуть "сумма 13000" без двокрапки; \b після мітки
                        # — щоб "цена" не хапала середину слова "ценах" тощо.
                        match = re.match(
                            rf"^\s*{re.escape(label)}\b{unit_suffix}\s*[:=\-—]?\s*(?P<value>.+?)\s*$",
                            stripped,
                            flags=re.IGNORECASE,
                        )
                        if match:
                            value = match.group("value").strip()
                            if value:
                                # Аудит коду (перевірка охоплення фіксу
                                # "розділювач тисяч"): ця мітка-гілка
                                # ("Цена: <value>") записувала СИРИЙ текст
                                # без жодного парсингу — на відміну від
                                # _extract_unlabeled_sale_markers нижче
                                # (._take_price), яка вже й так викликає
                                # _parse_number_with_thousands_separator.
                                # "Цена: 6.200" (=6200) ставало рядком
                                # "6.200", і кожен подальший _number_value(...)
                                # читав його як 6.2 — ціна й сума в
                                # ПРОДАЖА МАТЕРИАЛА/АНТИСЕПТИРОВАНИЕ тихо
                                # занижувались у 1000 разів.
                                if field == "price_per_unit":
                                    fields[field] = self._parse_number_with_thousands_separator(value)
                                else:
                                    fields[field] = value
                            matched = True
                            break
                    if matched:
                        break
            if not matched:
                kept_lines.append(line)

        remaining_text = "\n".join(kept_lines)
        # Реальний баг зі скріна: "СОСНА / 47Х100Х6000 - 50шт / Физ лицо /
        # 6200 мдл/м3 / АЛЬТ" в ОДНОМУ повідомленні без явних міток вище
        # ("клиент:"/"цена:"/"способ оплаты:") — усі три нерозпізнані шматки
        # зливались в один незрозумілий client_candidate. Спершу розділення
        # (кожен з трьох впізнаваних без мітки маркерів вирізається одразу
        # тут, ДО того як лишок піде на слово-за-словом розбір вільного
        # тексту), а тоді вже обробка — а не навпаки.
        marker_fields, remaining_text = self._extract_unlabeled_sale_markers(remaining_text, store)
        for field, value in marker_fields.items():
            fields.setdefault(field, value)
        return remaining_text, fields

    _PHYSICAL_PERSON_PATTERN = re.compile(r"\bфиз(?:ическое)?\.?\s*лицо\b", re.IGNORECASE)

    # Реальний баг з аудиту: попередня версія (\d+(?:[.,]\d+)?) трактувала
    # "." так само, як і "," — десятковою крапкою, тож "6.200 мдл/м3"
    # (роздільник тисяч) читався як 6,2, а не 6200. Тепер ЛІВА альтернатива
    # ловить саме групування по 3 цифри (крапкою чи пробілом) — "6.200",
    # "6 200", "6.200,50" — а ПРАВА (без обов'язкового роздільника тисяч)
    # лишається для звичайних чисел і "," як десяткової: "6200", "6200,50".
    _PRICE_CURRENCY_PATTERN = re.compile(
        r"\b(?P<value>\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*"
        r"(?:мдл|mdl|лей|leu|леев|молд\.?\s*лей)"
        r"(?:\s*/\s*(?:м3|m3|м2|m2|куб\w*|мп|мп\.?))?\b",
        re.IGNORECASE,
    )

    # Реальний баг зі скріна: "Мдл 6200" (валютна одиниця ПЕРЕД числом) не
    # розпізнавалось взагалі — _PRICE_CURRENCY_PATTERN вище ловить лише
    # значення-ПЕРЕД-одиницею ("6200 мдл"). Той самий принцип, що й
    # _DIMENSION_VALUE_LABEL_PATTERN для розмірів — мітка (тут валютна
    # одиниця) може йти в будь-якому порядку відносно числа.
    _CURRENCY_VALUE_PATTERN = re.compile(
        r"\b(?:мдл|mdl|лей|leu|леев|молд\.?\s*лей)\s*[:=\-—]?\s*"
        r"(?P<value>\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\b",
        re.IGNORECASE,
    )

    # store.resolve_payment_method (Крок 4.4) — звіряє і з поточними
    # мітками способів оплати, і з усіма збереженими синонімами (старі
    # перейменовані назви + початковий латинський "alt" для АЛЬТ),
    # повертає САМЕ ПОТОЧНУ (канонічну) мітку.
    def _match_payment_method_token(self, token, store):
        normalized = _normalize_phrase(token)
        if not normalized:
            return None
        return store.resolve_payment_method(token)

    # Способи оплати з пробілом усередині ("ЕФАКТУРА Б/Н", "ЕФАКТУРА Н")
    # НІКОЛИ не могли збігтись у _take_payment нижче — той пропускає текст
    # через re.sub(r"\S+", ...), тобто бачить рівно одне слово за раз
    # ("ЕФАКТУРА" й "Б/Н" окремо), і жодне з них саме по собі не дорівнює
    # повній назві. Тут шукаємо повну фразу цілком (у вихідному тексті,
    # до пословного розбору), толерантно до пунктуації між словами (напр.
    # "Б/Н" чи "Б / Н"). Довші фрази йдуть першими, щоб "ЕФАКТУРА Б/Н" не
    # "з'їдалась" як "ЕФАКТУРА" + залишок "Б/Н".
    # Крок 4.4: перебудовується з store.payment_method_recognized_phrases()
    # (поточні мітки + всі синоніми, зокрема старі назви після
    # перейменування) щоразу — перелік завжди малий (кілька варіантів),
    # тож перебудова щоразу коштує копійки, зате живі зміни (додав/
    # перейменував/видалив варіант) діють одразу, без перезапуску бота.
    def _payment_method_phrase_patterns(self, store):
        return [
            (
                current_label,
                re.compile(
                    r"(?<![0-9a-zа-яіїєґ])"
                    + r"[^0-9a-zа-яіїєґ]+".join(
                        re.escape(tok)
                        for tok in re.findall(
                            r"[0-9a-zа-яіїєґ]+", phrase.casefold().replace("ё", "е")
                        )
                    )
                    + r"(?![0-9a-zа-яіїєґ])",
                    re.IGNORECASE,
                ),
            )
            for phrase, current_label in sorted(
                store.payment_method_recognized_phrases(), key=lambda pair: len(pair[0]), reverse=True
            )
        ]

    # Реальний баг зі скріна: "ЕФАКТУРА" (без "Б/Н" чи "Н") не збігається
    # ТОЧНО із жодним способом оплати — раніше мовчки падало у вільний текст
    # і зливалось із клієнтом. Тут ловимо слово, що є ПОЧАТКОМ (префіксом)
    # одного чи кількох способів оплати, але не рівне жодному цілком.
    # Мінімальна довжина (5 символів після нормалізації) — щоб випадкове
    # коротке слово не збігалось як "префікс" довгої назви способу оплати.
    # Звіряється лише з ПОТОЧНИМИ мітками (не з синонімами) — так само, як
    # і раніше з PAYMENT_METHODS.
    def _payment_method_prefix_matches(self, token, store):
        normalized = _normalize_phrase(token)
        if len(normalized) < 5:
            return []
        matches = []
        for _option_id, label, _kind in store.list_payment_method_options():
            label_normalized = _normalize_phrase(label)
            if label_normalized != normalized and label_normalized.startswith(normalized):
                matches.append(label)
        return matches

    def _payment_method_candidate_keyboard(self, options):
        return {
            "keyboard": [[{"text": option}] for option in options]
            + [[{"text": "Отмена"}, {"text": "Редактировать"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Витягує 3 конкретні поняття, які МОЖУТЬ трапитись у вільному тексті без
    # явної мітки (на відміну від _SALE_FIELD_ALIASES вище, де мітка
    # обов'язкова) — "Физ лицо" (тип клієнта), ціну з валютною одиницею
    # ("6200 мдл/м3") і код способу оплати ("АЛЬТ", кирилична фонетика ALT).
    # Кожен збіг вирізається одразу (замінюється на пробіл), щоб не потрапити
    # ще й у вільний список слів нижче по конвеєру (_split_income_free_text).
    def _extract_unlabeled_sale_markers(self, text, store):
        fields = {}
        remaining = str(text or "")

        def _take_person(match):
            fields.setdefault("client", "Физ лицо")
            return " "

        remaining = self._PHYSICAL_PERSON_PATTERN.sub(_take_person, remaining)

        def _take_price(match):
            value = self._parse_number_with_thousands_separator(match.group("value"))
            if value > 0:
                fields.setdefault("price_per_unit", value)
            return " "

        remaining = self._PRICE_CURRENCY_PATTERN.sub(_take_price, remaining)
        remaining = self._CURRENCY_VALUE_PATTERN.sub(_take_price, remaining)

        for _method, _pattern in self._payment_method_phrase_patterns(store):
            if "payment_method" in fields:
                break
            _match = _pattern.search(remaining)
            if _match:
                fields["payment_method"] = _method
                remaining = remaining[: _match.start()] + " " + remaining[_match.end():]

        def _take_payment(match):
            token = match.group(0)
            method = self._match_payment_method_token(token, store)
            if method:
                fields.setdefault("payment_method", method)
                return " "
            # Реальний баг зі скріна: "ЕФАКТУРА" (без "Б/Н" чи "Н") не мала
            # ЖОДНОГО розпізнавання — мовчки падала у вільний текст і
            # зливалась із клієнтом ("ACOPERIS PENTRU FIECARE ЕФАКТУРА").
            # Замість цього — вирізаємо й стейджимо як кандидат на
            # уточнення (confirm_payment_method_candidate), а не даємо
            # потрапити в client_candidate.
            if "payment_method_candidate" not in fields:
                prefix_matches = self._payment_method_prefix_matches(token, store)
                if prefix_matches:
                    fields["payment_method_candidate"] = {
                        "typed": token,
                        "candidates": prefix_matches,
                    }
                    return " "
            return token

        remaining = re.sub(r"\S+", _take_payment, remaining)
        return fields, remaining

    def _is_income_command_only(self, text, store):
        normalized = _normalize_phrase(text)
        if not normalized:
            return False
        return store.find_command_code_by_phrase(text) == "add_income"

    def _is_sale_command_only(self, text, store):
        normalized = _normalize_phrase(text)
        if not normalized:
            return False
        return store.find_command_code_by_phrase(text) == "stock_sale"

    def _strip_sale_command_words(self, text):
        return re.sub(
            r"(?<![\wА-Яа-яІЇЄҐієїґ])(продажа|продажи|продать|продай|реализация)(?![\wА-Яа-яІЇЄҐієїґ])",
            " ",
            str(text or ""),
            flags=re.IGNORECASE,
        ).strip()

    def _pending_service_word_reply(self, answer, operation_type, status, payload, store):
        normalized = _normalize_phrase(answer)
        if not normalized:
            return None
        if status == "confirm_write" and self._is_edit_request(answer):
            return None
        if status == "edit_operation_data" and self._is_edit_request(answer):
            return None
        if status == "choose_category":
            return None

        # Свіжий пере-аудит (2026-08-02, Minor #3): "назад"/"главное меню"/
        # "головне меню" прибрано - усі 3 точки виклику _handle_pending_
        # operation (єдиний виклик цієї функції) уже перехоплюють ці слова
        # РАНІШЕ через _is_cancel_request/_is_back_request (telegram_dialog_
        # core.py) - до сюди відповідь з такими словами дійти фізично не
        # може, ці 3 записи були недосяжним мертвим кодом.
        service_words = {
            "приход",
            "продажа",
            "продажи",
            "продати",
            "продать",
            "склад",
        }
        if normalized not in service_words:
            return None

        operation_title = "приход" if operation_type == "add_income" else "продажу"
        kind = "income" if operation_type == "add_income" else "sale"
        missing = self._income_missing_fields(store, payload, kind=kind)
        if missing:
            lines = [f"Мы уже оформляем {operation_title}. Сейчас не хватает:"]
            lines.extend(f"- {field}" for field in missing)
            lines.append("Если хотите отменить операцию, напишите: Отмена.")
            return {"type": "message", "text": "\n".join(lines), "reply_markup": self._cancel_only_keyboard()}

        return {
            "type": "message",
            "text": (
                f"Мы уже оформляем {operation_title}.\n"
                "Напишите данные для текущего шага или используйте Отмена, чтобы начать заново."
            ),
            "reply_markup": self._cancel_only_keyboard(),
        }

    # Диспетчер: за pending["status"] вирішує, який з методів нижче обробить
    # відповідь користувача (тут була знайдена й виправлена пастка з "Нет"
    # у меню підозрілої кількості — див. код нижче по файлу).
    def _handle_pending_operation(self, text, store, context, pending):
        if pending["operation_type"] == "calculator":
            if pending["status"] == "wait_calculation":
                escape_reply = self._calculator_menu_escape_reply(text, store, context)
                if escape_reply is not None:
                    return escape_reply
                reply = self._calculator_reply(text)
                if self._is_calculator_retry_reply(reply):
                    return reply
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return reply
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

        if pending["operation_type"] == "stock_report":
            return self._continue_stock_report(text, store, context, pending)

        if pending["operation_type"] == "sales_report":
            return self._continue_sales_report(text, store, context, pending)

        if pending["operation_type"] == "antiseptic_report":
            return self._continue_antiseptic_report(text, store, context, pending)

        if pending["operation_type"] == "sales_by_client_report":
            return self._continue_sales_by_client_report(text, store, context, pending)

        if pending["operation_type"] == "low_stock_report":
            return self._continue_low_stock_report(text, store, context, pending)

        if pending["operation_type"] == "stock_browse":
            return self._continue_stock_browse(text, store, context, pending)

        if pending["operation_type"] == "antiseptic_service":
            return self._continue_antiseptic_operation(text, store, context, pending)

        if pending["operation_type"] == "custom_menu":
            return self._continue_custom_menu(text, store, context, pending)

        if pending["operation_type"] == "stock_writeoff":
            return self._continue_writeoff_operation(text, store, context, pending)

        if pending["operation_type"] not in {"add_income", "stock_sale"}:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

        operation_type = pending["operation_type"]
        continue_operation = (
            self._continue_sale_operation
            if operation_type == "stock_sale"
            else self._continue_income_operation
        )
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()
        service_reply = self._pending_service_word_reply(answer, operation_type, status, payload, store)
        if service_reply:
            return service_reply

        if status == "sale_all_in_one":
            # Користувач написав текст замість натискання кнопки форми -
            # найпростіша, безпечна відповідь: показати кнопку ще раз, а не
            # намагатись розібрати вільний текст на майже порожньому payload.
            return self._start_sale_all_in_one_reply(store, context)

        if status == "income_all_in_one":
            return self._start_income_all_in_one_reply(store, context)

        if status == "choose_sale_payment_method":
            payment_method = self._match_payment_method_token(answer, store)
            if not payment_method:
                return {
                    "type": "message",
                    "text": 'Не понял способ оплаты. Выберите один из вариантов ниже.',
                    "reply_markup": self._payment_method_keyboard(store),
                }
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "choose_category",
                {"payment_method": payment_method, "completed_positions": []},
            )
            return {
                "type": "message",
                "text": store.get_message_template(
                    "start_sale_category_prompt", BOT_MESSAGE_DEFAULTS["start_sale_category_prompt"]
                ),
                "reply_markup": self._category_keyboard(store, "start_sale"),
            }

        if status == "choose_category":
            # "Назад"/"Главное меню" тут перехоплюються раніше (_is_back_request/
            # _is_cancel_request у _build_reply), до виклику цієї функції — сюди
            # не доходять.
            parent_action_code = self._parent_action_code_for_operation_type(operation_type)
            category = self._category_from_text(store, parent_action_code, answer)
            if category is None:
                # Цей крок тепер досяжний і одразу ПІСЛЯ завершення операції
                # (повертає сюди замість головного меню) — тому досвідчений
                # користувач, який хоче одразу оформити ще одну повну продажу/
                # приход одним реченням (як і з головного меню), не повинен
                # спершу натискати кнопку категорії. Якщо текст розпізнається
                # як реальні дані (є хоч один рядок розміру/кількості) —
                # обробляємо як нову операцію; інакше (порожньо/гібрідиш) —
                # звичайне "не понял категорию".
                probe_text = answer
                probe_extra_fields = {}
                if operation_type == "stock_sale":
                    probe_text, probe_extra_fields = self._extract_sale_fields(probe_text, store)
                probe_text = self._strip_sale_command_words(probe_text) if operation_type == "stock_sale" else probe_text
                probe_payload, _ = self._parse_income_message(probe_text)
                if probe_payload.get("rows"):
                    probe_payload.update(
                        self._new_sale_payload(answer, context)
                        if operation_type == "stock_sale"
                        else self._new_income_payload(answer, context)
                    )
                    probe_payload.update(probe_extra_fields)
                    if operation_type == "stock_sale":
                        self._carry_over_sale_accumulation(payload, probe_payload)
                    return continue_operation(store, context, probe_payload)
                return {
                    "type": "message",
                    "text": "Не понял категорию. Выберите одну из кнопок ниже.",
                    "reply_markup": self._category_keyboard(store, parent_action_code),
                }
            if category == "antiseptic":
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                carry_over = payload if operation_type == "stock_sale" else None
                return self._start_antiseptic_operation(store, context, carry_over=carry_over)
            product, condition = category
            new_payload = (
                self._new_sale_payload(answer, context)
                if operation_type == "stock_sale"
                else self._new_income_payload(answer, context)
            )
            new_payload["product"] = product
            if condition:
                new_payload["condition"] = condition
            if operation_type == "stock_sale":
                self._carry_over_sale_accumulation(payload, new_payload)
            return continue_operation(store, context, new_payload)

        if status == "collect_income_missing":
            # "Изменить" тут — не команда редагування (те, що можна міняти,
            # ще навіть не сформовано, редагування є лише на етапі
            # confirm_write/preview) — раніше воно мовчки трактувалось як
            # звичайний текст і повертало ТЕ САМЕ повідомлення без пояснення.
            if self._is_edit_request(answer):
                missing_fields = self._income_missing_fields(
                    store, payload, kind="sale" if operation_type == "stock_sale" else "income"
                )
                missing_prompt = (
                    self._sale_missing_prompt(missing_fields, payload, store=store)
                    if operation_type == "stock_sale"
                    else self._income_missing_prompt(missing_fields, payload, store=store)
                )
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "collect_income_missing",
                    "Напишите новое значение — я заменю только то, что вы укажете.\n\n" + missing_prompt,
                )
            # "1"/"2".. одразу після списку "Похожие позиции" — швидкий вибір
            # за номером рядка замість передруку розміру вручну. candidates
            # завжди очищується тут (незалежно від того, чи відповідь була
            # номером), щоб не лишався "живим" для наступної, вже непов'язаної
            # відповіді (напр. кількості).
            had_similar_candidates = bool(payload.get("similar_candidates"))
            resolved_candidate = self._resolve_similar_candidate_answer(payload, answer)
            payload.pop("similar_candidates", None)
            if resolved_candidate is not None:
                # Список показує рядки, чия порода/тип МОЖУТЬ відрізнятись
                # від того, що вже є в payload (score враховує збіг, але не
                # вимагає його) — вибір за номером мусить застосувати ВЕСЬ
                # рядок (товар+порода+тип), а не лише розмір, інакше пошук
                # далі йде зі старою породою і "не знаходить" те, що явно
                # видно в списку.
                payload["product"] = resolved_candidate.get("product") or payload.get("product")
                payload["breed"] = resolved_candidate.get("breed") or payload.get("breed")
                payload["condition"] = resolved_candidate.get("condition")
                size_text = income_item_size(resolved_candidate)
                # Користувач бачить лише голе число ("3") у своєму
                # повідомленні — без цієї нотатки наступна відповідь бота
                # ("Не хватает данных: Количество...") нічим не підтверджує,
                # що номер взагалі був правильно розпізнаний і застосований
                # (включно з тим, яку саме породу/тип він щойно застосував).
                label_parts = [part for part in [payload.get("product"), payload.get("breed"), size_text] if part]
                payload.setdefault("info_notes", []).append(f"Выбрано: {' / '.join(label_parts)}.")
                answer = size_text
            category_reply = self._handle_category_reselection(
                answer, store, context, payload, operation_type, continue_operation
            )
            if category_reply is not None:
                return category_reply
            plain_number = self._parse_plain_positive_number(answer)
            # Реальний баг з аудиту: тут порівнювалось з ЛІТЕРАЛЬНИМ рядком
            # "Количество шт или м3", тож для площинного товару (Вагонка,
            # мітка "...шт или м2") умова НІКОЛИ не спрацьовувала — голе
            # число після повного розміру мовчки губилось (не бралось як
            # площа, і взагалі нікуди не застосовувалось).
            missing_now = self._income_missing_fields(
                store, payload, kind="sale" if operation_type == "stock_sale" else "income"
            )
            # Аудит коду: раніше тут порівнювалось з ВІДОБРАЖУВАНИМ (і
            # редагованим через "Дії") текстом мітки ("Количество") — її
            # перейменування мовчки ламало цю умову. _income_amount_missing_
            # row_index — та сама СЕМАНТИЧНА перевірка, якою вже користується
            # сама _handle_plain_amount_value нижче, незалежна від тексту мітки.
            if (
                plain_number is not None
                and len(missing_now) == 1
                and self._income_amount_missing_row_index(payload) is not None
            ):
                return self._handle_plain_amount_value(store, context, payload, answer, plain_number)
            extra_fields = {}
            source_text = answer
            if operation_type == "stock_sale":
                source_text, extra_fields = self._extract_sale_fields(source_text, store)
            source_text = self._strip_sale_command_words(source_text) if operation_type == "stock_sale" else source_text
            incoming_payload, _ = self._parse_income_message(source_text)
            unresolved_dimension_candidate = incoming_payload.get("single_dimension_candidate")
            self._merge_income_payload(payload, incoming_payload)
            payload.update(extra_fields)
            # Свіжий пере-аудит (New-Minor #4): голе число, що НЕ застосувалось
            # нікуди (жоден рядок не бракує виміру, який міг би його прийняти
            # — розміри вже повні) раніше мовчки зникало: continue_operation
            # просто перепитував те, що й так уже бракує, без жодного знаку,
            # що число взагалі побачили. Перевірка через "is" (тотожність
            # об'єкта, не значення) відрізняє "цей самий, щойно нерозв'язаний
            # кандидат повернувся назад" від "тут випадково лежить СТАРИЙ,
            # не пов'язаний із цим повідомленням кандидат" — інакше хибно
            # спрацьовувала б і на непричетних повідомленнях.
            # Регресія, знайдена повним прогоном (test_gap_fill_merge.py):
            # без "not missing_now" це так само хибно спрацьовувало на
            # ЗОВСІМ НОВІЙ, ще не розпочатій позиції (rows==[], усі виміри
            # ще бракують) — там "26" це перше введене число, а не "нікуди
            # не прикласти", і `_apply_single_dimension_to_missing` природньо
            # повертає False лише тому, що рядка ще НЕМАЄ, а не тому, що
            # рядок уже повний. Справжня ціль фіксу — саме "усе вже заповнено,
            # число зайве", тобто missing_now мусить бути порожнім.
            # Друга регресія, знайдена тим самим повним прогоном
            # (test_similar_numselect.py, TEST 6): розмір може бути ПОВНИЙ
            # (missing_now порожній), але позиція ще НЕ підтверджена на
            # складі — саме зараз показано "Похожие позиции" і номер поза
            # діапазоном (напр. "9" з 5 варіантів) НЕ мав вибрати кандидата.
            # Це вже наявний, окремий і коректний under-resolution сценарій
            # (не "все зайве, нікуди дівати") — тут голе число має провалитись
            # у звичайний continue_operation, що природньо повторить "позиція
            # не найдена" з тим самим списком, а не заявляти "не понял".
            if (
                plain_number is not None
                and not missing_now
                and not had_similar_candidates
                and unresolved_dimension_candidate is not None
                and payload.get("single_dimension_candidate") is unresolved_dimension_candidate
            ):
                missing_prompt = (
                    self._sale_missing_prompt(missing_now, payload, store=store)
                    if operation_type == "stock_sale"
                    else self._income_missing_prompt(missing_now, payload, store=store)
                )
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "collect_income_missing",
                    f"Не понял, к чему отнести число {answer.strip()}. Уточните, пожалуйста.\n\n" + missing_prompt,
                )
            payload["original_text"] = "\n".join(
                part for part in [payload.get("original_text", ""), answer] if part
            )
            return continue_operation(store, context, payload)

        if status == "confirm_category_change":
            decision = self._yes_no(answer)
            if decision is None:
                return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
            request = payload.pop("category_change_request", {})
            if not decision:
                return continue_operation(store, context, payload)
            return self._apply_category_change(
                answer, context, request, operation_type, continue_operation, store, payload
            )

        if status == "confirm_single_thickness":
            decision = self._yes_no(answer)
            if decision is None:
                return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
            candidate = payload.pop("single_dimension_candidate", {})
            if decision:
                row = self._first_or_new_income_row(payload)
                row["thickness"] = _number_value(candidate.get("value"))
            return continue_operation(store, context, payload)

        if status == "confirm_quantity_typo":
            decision = self._yes_no(answer)
            if decision is None:
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "confirm_quantity_typo",
                    self._quantity_typo_prompt(payload, payload.get("quantity_typo_issue") or {}),
                )
            typo_issue = payload.pop("quantity_typo_issue", {})
            if decision:
                rows = payload.get("rows") or []
                row_index = typo_issue.get("row_index")
                if row_index is not None and 0 <= row_index < len(rows):
                    row = rows[row_index]
                    row["quantity"] = typo_issue.get("guessed_quantity")
                    row["quantity_provided"] = True
                    row["quantity_typo_candidate"] = None
                return continue_operation(store, context, payload)

            # "Нет" — з розпізнаним одруком рядком продовжувати немає сенсу
            # (незрозуміло, що саме мав на увазі користувач), тож скидаємо
            # ЛИШЕ поточний рядок і пропонуємо спробувати ще раз або вийти в
            # головне меню ("Отмена" тут теж працює — універсальний синонім,
            # перевіряється ще до диспетчера pending-операцій). Реальний баг
            # з аудиту: раніше тут зберігався ПОРОЖНІЙ payload {} — якщо
            # продавець уже мав completed_positions/client/payment_method
            # від попередніх позицій цієї ж продажі, усе це губилось разом
            # із поточним рядком. Тепер зберігаємо лише перенесені поля (та
            # сама _carry_over_sale_accumulation, що вже рятує їх при зміні
            # категорії/детурі в антисептирование).
            preserved = {}
            if operation_type == "stock_sale":
                self._carry_over_sale_accumulation(payload, preserved)
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "confirm_retry_after_cancel",
                preserved,
            )
            return {
                "type": "message",
                "text": "Введенные данные отменены.\nХотите попробовать снова ввести данные?",
                "reply_markup": self._retry_after_cancel_keyboard(),
            }

        if status == "confirm_retry_after_cancel":
            normalized = _normalize_phrase(answer)
            if normalized in {"попробовать снова", "попробовать еще раз", "еще раз", "снова", "спробувати знову", "спробувати ще раз"}:
                if operation_type == "stock_sale":
                    return self._start_sale_category_menu(store, context, carry_over=payload)
                return self._start_income_category_menu(store, context)
            return {
                "type": "message",
                "text": "Не понял. Нажмите «Попробовать снова» или «Отмена».",
                "reply_markup": self._retry_after_cancel_keyboard(),
            }

        if status == "confirm_insufficient_stock":
            issue = payload.get("stock_issue") or {}
            rows = payload.get("rows") or []
            row_index = issue.get("row_index", 0)
            use_available_label = (
                f"использовать {_display_bot_number(issue['balance_qty'])} шт"
                if issue.get("kind") == "quantity"
                else f"использовать {_display_bot_number(issue.get('balance_measure'))} {issue.get('measure_unit')}"
            )
            if _normalize_phrase(answer) == _normalize_phrase(use_available_label):
                if row_index is not None and 0 <= row_index < len(rows):
                    row = rows[row_index]
                    if issue.get("kind") == "quantity":
                        row["quantity"] = issue.get("balance_qty")
                        row["quantity_provided"] = True
                    else:
                        row[issue["kind"]] = issue.get("balance_measure")
                        row[f"{issue['kind']}_provided"] = True
                        row["quantity"] = None
                        row["quantity_provided"] = False
                    # Виміри, крім щойно застосованого, скидаються — нехай
                    # _prepare_income_amounts перерахує їх заново з нової
                    # (доступної) кількості/об'єму/площі, а не тягне застарілі
                    # значення від запиту, який якраз не влазив у залишок.
                    if issue.get("kind") == "quantity":
                        row["volume"] = None
                        row["volume_provided"] = False
                        row["area"] = None
                        row["area_provided"] = False
                        row["linear"] = None
                        row["linear_provided"] = False
                payload.pop("stock_issue", None)
                return continue_operation(store, context, payload)

            if self._apply_item_amount_answer(payload, {"row_index": row_index}, answer):
                payload.pop("stock_issue", None)
                return continue_operation(store, context, payload)

            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_insufficient_stock",
                {
                    "type": "message",
                    "text": "Не понял количество. Нажмите кнопку ниже или напишите число (например: 40 шт).",
                    "reply_markup": self._insufficient_stock_keyboard(issue),
                },
            )

        if status in {"ask_breed", "ask_condition", "ask_product"}:
            if self._is_edit_request(answer):
                return (
                    self._reopen_sale_collection(store, context, payload)
                    if operation_type == "stock_sale"
                    else self._reopen_income_collection(store, context, payload)
                )
            field = status[len("ask_"):]
            payload[field] = self._answer_value(answer, field)
            return continue_operation(store, context, payload)

        if status == "ask_dimension":
            validation = payload.get("dimension_request", {})
            number = self._parse_income_dimension_answer(answer, validation)
            if number <= 0:
                return self._save_income_question(
                    store, context, payload, "ask_dimension", "Напишите корректное числовое значение."
                )
            self._apply_validated_value(payload, validation, number)
            payload.pop("dimension_request", None)
            return continue_operation(store, context, payload)

        if status == "ask_item_amount":
            request = payload.get("amount_request", {})
            plain_number = self._parse_plain_positive_number(answer)
            if plain_number is not None:
                return self._handle_plain_amount_value(store, context, payload, answer, plain_number, request)
            if not self._apply_item_amount_answer(payload, request, answer):
                return self._save_income_question(
                    store, context, payload, "ask_item_amount", self._amount_prompt(payload, request)
                )
            payload.pop("amount_request", None)
            return continue_operation(store, context, payload)

        # Клиент/цена/способ оплаты (ТЗ п.4/п.8) — голу відповідь без мітки
        # трактуємо напряму як значення саме цього поля (той самий принцип,
        # що й ask_item_amount вище), а не через спільний _parse_income_message
        # (той знає лише про product/breed/condition/розмір/кількість).
        if status == "ask_sale_client":
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            client = answer.strip()
            if not client:
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "ask_sale_client",
                    {
                        "type": "message",
                        "text": self._sale_mandatory_fields_prompt(store, payload),
                        "reply_markup": self._client_entry_keyboard(),
                    },
                )
            payload["client"] = client
            return continue_operation(store, context, payload)

        if status == "ask_sale_price":
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            extracted_text, extra_fields = self._extract_sale_fields(answer, store)
            payload.update(extra_fields)
            if "price_per_unit" not in extra_fields:
                price_fields = self._parse_sale_price_answer(extracted_text.strip())
                if price_fields is not None:
                    payload.update(price_fields)
                else:
                    unit_label = self._MEASURE_KIND_UNIT.get(self._payload_measure_kind(payload), "шт")
                    return self._save_income_question(
                        store,
                        context,
                        payload,
                        "ask_sale_price",
                        f"Не понял цену. Напишите число (например: 6000 — цена за {unit_label}).",
                    )
            return continue_operation(store, context, payload)

        if status == "ask_sale_address":
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            # На відміну від ask_sale_client (проста строка) — спершу
            # пропускаємо через _extract_sale_fields, як і ask_sale_price:
            # відповідь може містити ОДРАЗУ кілька мічених полів (напр.
            # "Адрес выгрузки: ...\nЦена: ..."), а не лише голу адресу.
            extracted_text, extra_fields = self._extract_sale_fields(answer, store)
            payload.update(extra_fields)
            if "address" not in extra_fields:
                address = extracted_text.strip()
                if not address:
                    return self._save_income_question(
                        store,
                        context,
                        payload,
                        "ask_sale_address",
                        self._sale_mandatory_fields_prompt(store, payload),
                    )
                payload["address"] = address
            return continue_operation(store, context, payload)

        if status == "ask_sale_payment_method":
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            payment_method = answer.strip()
            if not payment_method:
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "ask_sale_payment_method",
                    {
                        "type": "message",
                        "text": self._sale_mandatory_fields_prompt(store, payload),
                        "reply_markup": self._payment_method_keyboard(store),
                    },
                )
            payload["payment_method"] = payment_method
            return continue_operation(store, context, payload)

        if status == "choose_amount_unit":
            unit = self._amount_unit_choice(answer)
            if unit is None:
                request = payload.get("amount_unit_request", {})
                row_index = request.get("row_index")
                rows = payload.get("rows") or []
                measure_kind = (
                    self._row_measure_kind(payload, rows[row_index])
                    if row_index is not None and 0 <= row_index < len(rows)
                    else self._payload_measure_kind(payload)
                )
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "choose_amount_unit",
                    self._amount_unit_prompt(request, measure_kind),
                )
            request = payload.pop("amount_unit_request", {})
            self._apply_plain_amount_unit(payload, request, unit)
            return continue_operation(store, context, payload)

        if status == "ask_field_mapping":
            field = self._field_from_mapping_answer(answer)
            if not field:
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "ask_field_mapping",
                    self._field_mapping_question(store, payload.get("field_mapping", {})),
                )
            field_mapping = payload.pop("field_mapping", {})
            payload[field] = field_mapping.get("value", "")
            return continue_operation(store, context, payload)

        if status == "confirm_volume_conflict":
            decision = self._yes_no(answer)
            if decision is None:
                return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
            conflict = payload.get("volume_conflict", {})
            row_index = conflict.get("row_index")
            if row_index is None or row_index >= len(payload.get("rows", [])):
                payload.pop("volume_conflict", None)
                return continue_operation(store, context, payload)
            if decision:
                item = payload["rows"][row_index]
                measure_key = conflict.get("measure_kind", "volume")
                measure_provided_key = f"{measure_key}_provided"
                item[measure_key] = conflict.get("calculated_volume")
                item[measure_provided_key] = False
                payload.pop("volume_conflict", None)
                return continue_operation(store, context, payload)

            request = {"row_index": row_index}
            payload.pop("volume_conflict", None)
            payload["amount_request"] = request
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_item_amount",
                self._amount_prompt(payload, request),
            )

        if status == "choose_quantity_option":
            options = payload.get("quantity_options", {})
            if not self._apply_quantity_option_answer(payload, options, answer):
                return self._save_income_question(
                    store, context, payload, "choose_quantity_option", self._quantity_options_prompt(payload, options)
                )
            payload.pop("quantity_options", None)
            return continue_operation(store, context, payload)

        if status == "confirm_suggestion":
            validation = payload.get("validation", {})
            if validation.get("field") == "client":
                # Клієнт (на відміну від dimension/product/breed суджестій
                # нижче) — тут ТРИ дії, не два: прийняти й запам'ятати цей
                # одрук назавжди (client_name_aliases), прийняти лише цей
                # раз, чи відхилити/редагувати. Природна мова теж працює.
                decision = self._parse_client_suggestion_decision(answer)
                if decision is None:
                    return self._validation_suggestion_prompt(validation, payload)
                if decision == "edit":
                    payload.pop("validation", None)
                    return self._reopen_sale_collection(store, context, payload)
                if decision == "remember":
                    store.remember_client_alias(validation.get("value"), validation.get("suggestion"))
                if decision in ("remember", "accept"):
                    self._apply_validated_value(payload, validation, validation.get("suggestion"))
                    payload.pop("validation", None)
                    return continue_operation(store, context, payload)
                # decision == "reject" ("Нет") падає нижче — та сама гілка
                # "це НЕ той клієнт", що й раніше.
            else:
                decision = self._yes_no(answer)
                if decision is None:
                    return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
                if decision:
                    self._apply_validated_value(payload, validation, validation.get("suggestion"))
                    payload.pop("validation", None)
                    return continue_operation(store, context, payload)

            # Клієнт — не "позиція складу", тому "Нет" (це не той клієнт) веде
            # до confirm_new_value (додати як нового), а не sale_not_found.
            if operation_type == "stock_sale" and validation.get("field") != "client":
                payload.pop("validation", None)
                return self._save_sale_not_found_question(store, context, payload)

            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "confirm_new_value",
                payload,
            )
            label = validation.get("label", "Значение")
            value = validation.get("value", "")
            display_value = _display_bot_number(value)
            return self._yes_no_reply(
                f'{label} "{display_value}" не найдено.\n'
                f'Добавить новое значение "{display_value}"?\n'
                "Да / Нет"
            )

        if status == "confirm_new_value":
            decision = self._yes_no(answer)
            if decision is None:
                return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
            validation = payload.get("validation", {})
            if operation_type == "stock_sale" and validation.get("field") != "client":
                payload.pop("validation", None)
                return self._save_sale_not_found_question(store, context, payload)
            if not decision:
                payload.pop("validation", None)
                if operation_type == "stock_sale":
                    payload[validation.get("field")] = None
                    return continue_operation(store, context, payload)
                return self._reject_new_income_value(store, context, payload, validation)

            self._mark_new_value_confirmed(payload, validation)
            payload.pop("validation", None)
            return continue_operation(store, context, payload)

        if status == "confirm_client_candidate":
            candidate = payload.get("client_candidate", "")
            if self._is_edit_request(answer):
                payload.pop("client_candidate", None)
                return continue_operation(store, context, payload)
            decision = self._yes_no(answer)
            if decision is None:
                return self._confirmation_reply("Ответьте, пожалуйста: Да, Нет или Редактировать.")
            payload.pop("client_candidate", None)
            if decision:
                payload["client"] = self._format_new_income_text(candidate)
                return continue_operation(store, context, payload)
            payload["_ambiguous_free_value"] = candidate
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "choose_free_value_role",
                payload,
            )
            return {
                "type": "message",
                "text": f'Хорошо, "{candidate}" — не клиент.\nЧто это?',
                "reply_markup": self._free_value_role_keyboard(),
            }

        if status == "choose_free_value_role":
            value = payload.get("_ambiguous_free_value", "")
            normalized = _normalize_phrase(answer)
            if self._is_edit_request(answer):
                payload.pop("_ambiguous_free_value", None)
                return continue_operation(store, context, payload)
            if normalized == "порода":
                payload.pop("_ambiguous_free_value", None)
                payload["breed"] = self._format_new_income_text(value)
                return continue_operation(store, context, payload)
            if normalized in {"тип", "тип продукта"}:
                payload.pop("_ambiguous_free_value", None)
                payload["condition"] = value
                return continue_operation(store, context, payload)
            if normalized == "товар":
                payload.pop("_ambiguous_free_value", None)
                payload["product"] = value
                return continue_operation(store, context, payload)
            return {
                "type": "message",
                "text": "Выберите один из вариантов: Порода, Тип продукта, Товар или Редактировать.",
                "reply_markup": self._free_value_role_keyboard(),
            }

        if status == "confirm_price_candidate":
            candidate_row = payload.pop("price_candidate", None) or {}
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            decision = self._yes_no(answer)
            if decision is None:
                payload["price_candidate"] = candidate_row
                return self._confirmation_reply("Ответьте, пожалуйста: Да, Нет или Редактировать.")
            if decision:
                payload["price_per_unit"] = self._income_row_amount_value(candidate_row)
                return continue_operation(store, context, payload)
            # "Нет" — це справді окрема позиція без розміру (не ціна), як і
            # раніше до цього фіксу: додаємо рядком-фантомом з тим самим
            # поясненням, що й для конфлікту розмірів.
            payload.setdefault("rows", []).append(candidate_row)
            note = (
                "Хорошо, добавил как отдельную позицию без размера. Если это была "
                "ошибка, укажите размер с подписями (например: ширина 150, длина 5000)."
            )
            info_notes = payload.setdefault("info_notes", [])
            if note not in info_notes:
                info_notes.append(note)
            return continue_operation(store, context, payload)

        if status == "confirm_bare_price_candidate":
            candidate_value = payload.pop("bare_price_candidate", None)
            if self._is_edit_request(answer):
                return self._reopen_sale_collection(store, context, payload)
            decision = self._yes_no(answer)
            if decision is None:
                payload["bare_price_candidate"] = candidate_value
                return self._confirmation_reply("Ответьте, пожалуйста: Да, Нет или Редактировать.")
            if decision:
                payload["price_per_unit"] = self._parse_number_with_thousands_separator(candidate_value)
                return continue_operation(store, context, payload)
            # "Нет" — насправді не ціна. Питання про клієнта (confirm_client_
            # candidate) перевіряється РАНІШЕ цього в черзі, тож на момент
            # цього "Нет" клієнт майже напевно вже підтверджений — дописувати
            # число туди більше нема куди (це створило б заплутаний стан:
            # клієнт вже є, а поряд ще й "кандидат"). Просто відкидаємо число
            # з поясненням, а не вигадуємо йому нове (можливо так само
            # невірне) місце.
            note = (
                f"Не понял, что означает «{_display_bot_number(self._parse_number_with_thousands_separator(candidate_value))}» — "
                "оно проигнорировано. Если это важное значение, укажите его явно "
                "(например: Цена: ...)."
            )
            info_notes = payload.setdefault("info_notes", [])
            if note not in info_notes:
                info_notes.append(note)
            return continue_operation(store, context, payload)

        if status == "confirm_payment_method_candidate":
            candidate = payload.get("payment_method_candidate") or {}
            if self._is_edit_request(answer):
                payload.pop("payment_method_candidate", None)
                return self._reopen_sale_collection(store, context, payload)
            chosen = self._match_payment_method_token(answer, store)
            if chosen:
                payload["payment_method"] = chosen
                payload.pop("payment_method_candidate", None)
                return continue_operation(store, context, payload)
            return {
                "type": "message",
                "text": "Не понял выбор. Выберите один из вариантов ниже.",
                "reply_markup": self._payment_method_candidate_keyboard(candidate.get("candidates", [])),
            }

        if status == "confirm_sale_similar_positions":
            return self._handle_sale_not_found_action(answer, store, context, payload)

        if status == "stock_filter_collect":
            return self._handle_stock_filter_collect(answer, store, context, payload)

        if status == "stock_filter_confirm_replace":
            return self._handle_stock_filter_replace(answer, store, context, payload)

        if status == "confirm_suspicious_quantity":
            return self._handle_suspicious_quantity_choice(answer, store, context, payload, continue_operation)

        if status == "ask_suspicious_quantity_value":
            return self._handle_suspicious_quantity_value(answer, store, context, payload)

        if status == "confirm_suspicious_quantity_value":
            return self._handle_suspicious_quantity_value_confirmation(answer, store, context, payload, continue_operation)

        if status == "choose_edit_row":
            return self._handle_edit_row_choice(answer, store, context, payload, continue_operation)

        if status == "edit_operation_data":
            return self._handle_operation_edit(answer, store, context, payload, operation_type, continue_operation)

        if status == "confirm_edit_length_value":
            return self._handle_edit_length_value_confirmation(
                answer, store, context, payload, operation_type, continue_operation
            )

        if status == "confirm_write":
            from_webapp_form = bool(payload.get("_from_webapp_form"))
            # Задача користувача: "додай ще повернення в форму... знизу
            # третя кнопка" - на відміну від старого "Редактировать" (веде в
            # текстовий чат-едит), ця кнопка знову відкриває ТУ САМУ форму
            # (_reopen_webapp_form_reply) - виправлення лишається у формі,
            # не перескакує в чат.
            if from_webapp_form and _normalize_phrase(answer) == _normalize_phrase(self._WEBAPP_FORM_RETURN_LABEL):
                return self._reopen_webapp_form_reply(store, context, payload)
            # Задача користувача: "якщо вже в форму зайшов - при виході з
            # нього ЛИШЕ інформування в чаті про результат... до кінця -
            # бот". "Редактировать"/"Продолжить" (нижче) обидва ведуть у
            # СТАРИЙ текстовий діалог (покроковий edit/вибір категорії) -
            # саме та "пастка" з двох UI-режимів одразу, про яку йдеться.
            # Кнопки вже прибрані з клавіатури (_sale_confirm_keyboard/
            # _confirmation_reply, allow_edit/from_webapp_form вище) - тут
            # блокуємо й голий текстовий обхід (людина набрала слово руками),
            # щоб форма-режим не мав жодного виходу в чат, окрім Да/Нет.
            if not from_webapp_form:
                category_reply = self._handle_category_reselection(
                    answer, store, context, payload, operation_type, continue_operation
                )
                if category_reply is not None:
                    return category_reply
                if self._is_edit_request(answer):
                    store.save_pending_operation(
                        context["chat_id"],
                        context["user_id"],
                        operation_type,
                        "edit_operation_data",
                        payload,
                    )
                    return self._edit_prompt_reply()

                # "Продовжити" (Задача користувача) — лише для stock_sale:
                # архівує ПОТОЧНУ позицію в completed_positions і одразу
                # повертає до вибору категорії НАСТУПНОЇ, не записуючи нічого
                # на склад. add_income не має цього шляху взагалі — там ця
                # гілка просто не спрацьовує (operation_type перевіряється
                # першим), і решта коду для приходу лишається без змін.
                if operation_type == "stock_sale" and self._is_sale_continue_request(answer):
                    reset_payload = self._archive_current_sale_position_and_reset(payload)
                    store.save_pending_operation(
                        context["chat_id"], context["user_id"], "stock_sale", "choose_category", reset_payload,
                    )
                    return {
                        "type": "message",
                        "text": "Позиция добавлена. Выберите категорию следующей позиции:",
                        "reply_markup": self._category_keyboard(store, "start_sale"),
                    }

            decision = self._yes_no(answer)
            # "Оформить продажу"/"Здійснити реалізацію" — новий явний
            # синонім "Да" лише для stock_sale (легасі "Да" й далі працює,
            # щоб не ламати одно-позиційні продажі й купу вже існуючих
            # тестів).
            if decision is None and operation_type == "stock_sale" and self._is_sale_finish_request(answer):
                decision = True
            if decision is None:
                if operation_type == "stock_sale":
                    text = (
                        f"Ответьте, пожалуйста: Оформить продажу, Отмена или {self._WEBAPP_FORM_RETURN_LABEL}."
                        if from_webapp_form
                        else "Ответьте, пожалуйста: Оформить продажу, Продолжить, Редактировать или Отмена."
                    )
                    return {
                        "type": "message",
                        "text": text,
                        "reply_markup": self._sale_confirm_keyboard(from_webapp_form=from_webapp_form),
                    }
                text = (
                    f"Ответьте, пожалуйста: Да, Нет или {self._WEBAPP_FORM_RETURN_LABEL}."
                    if from_webapp_form
                    else "Ответьте, пожалуйста: Да, Нет или Редактировать."
                )
                return self._confirmation_reply(
                    text, allow_edit=not from_webapp_form, show_form_return=from_webapp_form
                )
            if not decision:
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._cancelled_reply(
                    "Операция продажи отменена." if operation_type == "stock_sale" else "Операция прихода отменена.",
                    store,
                )

            sync_mode = self._excel_sync_mode()
            # "Оформить продажу"/запис приходу архівує ОСТАННЮ (поточну)
            # позицію так само, як і "Продовжити" — apply_sale_operation/
            # apply_income_operation (warehouse_data.py) читають ВСІ позиції
            # з payload["completed_positions"], жодної окремої "поточної"
            # позиції там уже немає. Задача користувача (2026-08-14): "щоб
            # міг продовжувати приход і внести кілька різних позицій" -
            # прихід тепер архівується так само безумовно, як і продаж - для
            # звичайного одно-позиційного приходу це дає completed_positions
            # РІВНО з одним елементом, що apply_income_operation (через
            # _income_positions) читає ідентично до старої поведінки.
            write_payload = (
                self._archive_current_sale_position_and_reset(payload)
                if operation_type == "stock_sale"
                else self._archive_current_income_position_and_reset(payload)
            )
            result = (
                apply_sale_operation(store, write_payload, sync_mode, self)
                if operation_type == "stock_sale"
                else apply_income_operation(store, write_payload, sync_mode, self)
            )
            if not result.get("ok"):
                # Аудит коду: раніше цей "хвіст" нічого не робив із pending -
                # застарілий (уже архівований/над-кількісний) payload лишався
                # на статусі "confirm_write" назавжди, чекаючи ще одну
                # відповідь на вже неактуальний екран. Ані запис на склад
                # не відбувся (result["ok"] is False - нічого не зіпсовано),
                # ані ЧІТКОГО завершення немає - саме той "ні відміна, ні
                # успіх" стан, який задача користувача прямо забороняє.
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._with_main_menu(f"⚠️ {result['message']}", store)
            # Реальна знахідка (аудит коду, 2026-08-16): продаж/приход уже
            # записаний і закомічений вище (apply_sale_operation/
            # apply_income_operation - власна атомарна транзакція) - але
            # статус "confirm_write" знімався лише НАБАГАТО пізніше,
            # усередині _start_sale_category_menu/_webapp_form_terminal_
            # reply, ПІСЛЯ циклу запису антисептирования нижче (окрема,
            # повільніша транзакція на кожну позицію). Якщо між записом
            # товару і оновленням pending-статусу траплялась помилка (напр.
            # "database is locked" - реальний, уже визнаний ризик цього
            # проєкту, GUI+бот пишуть в один файл БД одночасно), бот казав
            # "спробуйте ще раз", хоча продаж УЖЕ записаний - повторне "Так"
            # від користувача викликало б apply_sale_operation ЗНОВУ з тим
            # самим payload, записуючи продаж ВДРУГЕ. Знімаємо pending-
            # статус одразу тут, найближче до самого запису - подальші
            # виклики save_pending_operation/delete_pending_operation нижче
            # (у category_menu/webapp_form_terminal_reply) лишаються без
            # змін і безпечно перезаписують/видаляють уже видалений рядок
            # (UPSERT/DELETE WHERE - обидва ідемпотентні).
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            # Задача користувача: "антисептирование - це додаткова послуга" -
            # реальний баг був у тому, що вибір "категорії" АНТИСЕПТИРОВАНИЕ
            # ПОВНІСТЮ замінював кошик товару. Тепер це чекбокс у webapp/
            # app.js (position["antiseptic"] = {volume, price_per_unit}) -
            # товар записується як завжди ВИЩЕ (apply_sale_operation), а тут,
            # ПІСЛЯ успішного запису товару, ОКРЕМО пишемо послугу в
            # АНТИСЕПТИРОВАНИЕ (apply_antiseptic_operation - той самий
            # виклик, що й у звичайному потоці антисептирования), без
            # жодного впливу на залишок складу.
            #
            # Задача користувача: "чому розпізнало лише 1 антисептирование,
            # якщо я 2 антисептіровав?" - "antiseptic" тепер входить у
            # _SALE_POSITION_FIELDS, тож _archive_current_sale_position_and_
            # reset вище вже перенесла й останню позицію в write_payload
            # ["completed_positions"] разом із її власним antiseptic (якщо
            # був) - пишемо ОКРЕМУ послугу для КОЖНОЇ позиції, що має
            # antiseptic, а не лише для однієї, "верхньої".
            # Задача користувача: "Дополнительная услуга: Антисептирование"
            # тепер показується ПІД кожною позицією прямо у "Продажа
            # записана" (apply_sale_operation, warehouse_data.py), тож
            # окремі "Услуга записана:" повідомлення-хвости тут більше НЕ
            # додаються до result["message"] - лише саме реальне списання/
            # запис у лист АНТИСЕПТИРОВАНИЕ (antiseptic_write_errors нижче)
            # лишається живим і потрібним.
            antiseptic_write_errors = []
            if operation_type == "stock_sale":
                for position in write_payload.get("completed_positions") or []:
                    antiseptic_addon = position.get("antiseptic")
                    if not (isinstance(antiseptic_addon, dict) and antiseptic_addon.get("volume") and antiseptic_addon.get("price_per_unit")):
                        continue
                    antiseptic_payload = {
                        "client": write_payload.get("client"),
                        "address": write_payload.get("address"),
                        "payment_method": write_payload.get("payment_method"),
                        "volume": antiseptic_addon.get("volume"),
                        "price_per_unit": antiseptic_addon.get("price_per_unit"),
                    }
                    antiseptic_result = apply_antiseptic_operation(store, antiseptic_payload, sync_mode)
                    if not antiseptic_result.get("ok"):
                        antiseptic_write_errors.append(
                            "⚠️ Одну из позиций не удалось записать как антисептирование: "
                            + antiseptic_result["message"]
                        )
            if antiseptic_write_errors:
                result = dict(result)
                result["message"] = result["message"] + "\n\n" + "\n\n".join(antiseptic_write_errors)
            # Задача користувача (2026-08-17): дубль звіту в окрему групу -
            # покриває і продаж, і прихід (обидва проходять через цю саму
            # гілку), разом з будь-яким дописаним вище "antiseptic"-хвостом.
            self._notify_report_broadcast(context, result["message"])
            # Реальний баг зі скріна: форма → успіх → бот ЗАВЖДИ повертав у
            # СТАРИЙ покроковий вибір категорії (нижче) - людина, що більше
            # ніколи не торкалась чату вручну, раптом опинялась у чужому
            # UI-режимі без кнопки форми - "пастка", з якої лише Отмена.
            # Форма-режим завершується ЛИШЕ інформуванням про результат.
            if from_webapp_form:
                return self._webapp_form_terminal_reply(store, context, result["message"], parse_mode="HTML")
            # Не викидаємо в головне меню — повертаємо до вибору категорії
            # ТІЄЇ Ж операції (ПРИХОД/РЕАЛИЗАЦИЯ), щоб можна було одразу
            # оформити ще одну. Головне меню — лише за явним натисканням
            # "Главное меню".
            next_category_menu = (
                self._start_sale_category_menu(store, context)
                if operation_type == "stock_sale"
                else self._start_income_category_menu(store, context)
            )
            return self._prepend_reply_text(result["message"], next_category_menu, parse_mode="HTML")

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu(
            "Не удалось продолжить операцию. Отправьте продажу заново."
            if operation_type == "stock_sale"
            else "Не удалось продолжить операцию. Отправьте приход заново.",
            store,
        )

    # Обгортка: жодна інформаційна нотатка (одрук-виправлення "шь" -> шт,
    # "Выбрано: 25x150x6000" після вибору за номером тощо), накопичена під
    # час парсингу/злиття, не повинна губитись — приліплюється до ТОЇ
    # відповіді, яку зрештою поверне _continue_income_operation_impl (яка б
    # це не була: ще одне запитання, "не хватает данных" чи вже
    # підтвердження), і при цьому показується РІВНО ОДИН РАЗ (див. коментар
    # на _apply_info_notes).
    def _continue_income_operation(self, store, context, payload):
        notes = payload.pop("info_notes", None)
        reply = self._continue_income_operation_impl(store, context, payload)
        return self._apply_info_notes({"info_notes": notes}, reply)

    def _continue_income_operation_impl(self, store, context, payload):
        self._apply_income_free_values(store, payload)
        self._canonicalize_income_values(store, payload)

        if payload.get("unknown_fields"):
            unknown_fields = payload.get("unknown_fields") or []
            field_mapping = unknown_fields.pop(0)
            payload["unknown_fields"] = unknown_fields
            payload["field_mapping"] = field_mapping
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_field_mapping",
                self._field_mapping_question(store, field_mapping),
            )

        if payload.get("single_dimension_candidate") and not payload.get("rows"):
            candidate = payload.get("single_dimension_candidate") or {}
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_single_thickness",
                self._yes_no_reply(
                    f"Вы ввели толщину {_display_bot_number(candidate.get('value'))}?\nДа / Нет"
                ),
            )

        if payload.get("income_free_candidate"):
            candidate = payload.pop("income_free_candidate")
            payload["_ambiguous_free_value"] = candidate
            return self._save_income_question(
                store,
                context,
                payload,
                "choose_free_value_role",
                {
                    "type": "message",
                    "text": f'Не распознал «{candidate}». Что это?',
                    "reply_markup": self._free_value_role_keyboard(),
                },
            )

        missing_fields = self._income_missing_fields(store, payload, kind="income")
        if missing_fields:
            typo_issue = self._income_quantity_typo_issue(payload)
            if typo_issue:
                payload["quantity_typo_issue"] = typo_issue
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "confirm_quantity_typo",
                    self._quantity_typo_prompt(payload, typo_issue),
                )
            return self._save_income_question(
                store,
                context,
                payload,
                "collect_income_missing",
                self._income_missing_prompt(missing_fields, payload, store=store),
            )

        # Форма-режим: жодних Так/Ні-запитань усередині чату (задача
        # користувача, повторена явно) - прихід уже й так трактує нове
        # поєднання порода/розмір як легітимне (restrict_to_existing_combos
        # немає), тож у формі просто приймаємо ТОЧНО ТЕ, що ввели, замість
        # питати підтвердження чи пропонувати "можливо, малось на увазі".
        validation = self._next_income_validation_issue(store, payload)
        while validation and payload.get("_from_webapp_form") and validation.get("field") != "schema":
            self._mark_new_value_confirmed(payload, validation)
            validation = self._next_income_validation_issue(store, payload)
        if validation:
            if validation.get("field") == "schema":
                if payload.get("_from_webapp_form"):
                    return self._webapp_form_terminal_reply(
                        store,
                        context,
                        "Не могу принять приход: в листе СКЛАД не найдены нужные колонки: "
                        f"{validation.get('value')}.",
                    )
                return (
                    "Не могу принять приход: в листе СКЛАД не найдены нужные колонки: "
                    f"{validation.get('value')}."
                )
            payload["validation"] = validation
            status = "confirm_suggestion" if validation.get("suggestion") is not None else "confirm_new_value"
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                status,
                payload,
            )
            label = validation["label"]
            value = validation["value"]
            if validation.get("suggestion") is not None:
                return self._validation_suggestion_prompt(validation, payload)
            return self._yes_no_reply(
                f'{label} "{_display_bot_number(value)}" не найдено.\n'
                f'Добавить новое значение "{_display_bot_number(value)}"?\n'
                "Да / Нет"
            )

        amount_issue = self._prepare_income_amounts(payload)
        if amount_issue:
            payload[amount_issue["payload_key"]] = amount_issue["payload"]
            return self._save_income_question(
                store,
                context,
                payload,
                amount_issue["status"],
                amount_issue["message"],
            )

        quantity_limit_issue = self._income_quantity_limit_issue(payload)
        if quantity_limit_issue:
            payload["suspicious_quantity"] = quantity_limit_issue
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                "confirm_suspicious_quantity",
                payload,
            )
            return self._suspicious_quantity_reply(payload, quantity_limit_issue)

        match_issue = self._resolve_income_rows(store, payload)
        if match_issue:
            return self._save_income_question(store, context, payload, "ask_product", match_issue)

        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "add_income",
            "confirm_write",
            payload,
        )
        return self._confirmation_reply(
            self._income_preview(payload),
            allow_edit=not payload.get("_from_webapp_form"),
            show_form_return=bool(payload.get("_from_webapp_form")),
        )

    def _continue_sale_operation(self, store, context, payload):
        notes = payload.pop("info_notes", None)
        reply = self._continue_sale_operation_impl(store, context, payload)
        return self._apply_info_notes({"info_notes": notes}, reply)

    def _continue_sale_operation_impl(self, store, context, payload):
        self._apply_income_free_values(store, payload)
        self._canonicalize_income_values(store, payload)

        if payload.get("unknown_fields"):
            unknown_fields = payload.get("unknown_fields") or []
            field_mapping = unknown_fields.pop(0)
            payload["unknown_fields"] = unknown_fields
            payload["field_mapping"] = field_mapping
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_field_mapping",
                self._field_mapping_question(store, field_mapping),
            )

        if payload.get("single_dimension_candidate") and not payload.get("rows"):
            candidate = payload.get("single_dimension_candidate") or {}
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_single_thickness",
                self._yes_no_reply(
                    f"Вы ввели толщину {_display_bot_number(candidate.get('value'))}?\nДа / Нет"
                ),
            )

        if payload.get("client_candidate") and not payload.get("client"):
            candidate = payload["client_candidate"]
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_client_candidate",
                self._confirmation_reply(f'"{candidate}" — это клиент?\nДа / Нет / Редактировать'),
            )

        if payload.get("price_candidate") is not None:
            candidate_value = self._income_row_amount_value(payload["price_candidate"])
            unit_label = self._MEASURE_KIND_UNIT.get(self._payload_measure_kind(payload), "шт")
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_price_candidate",
                self._confirmation_reply(
                    f"Похоже на цену за {unit_label} ({_display_bot_number(candidate_value)}), "
                    "а не на новую позицию без размера.\nВерно?\nДа / Нет / Редактировать"
                ),
            )

        # Той самий клас багу, що й price_candidate вище, але для голого
        # числа БЕЗ жодної одиниці ("6200" замість "6200 м3"/"6200 мдл") —
        # раніше воно мовчки дописувалось до кандидата в клієнти ("ACME SRL
        # 6200"), знайдено при аудиті. _assign_income_free_value вирізає
        # його окремо (payload["bare_price_candidate"]) замість домішування.
        if payload.get("bare_price_candidate") is not None:
            unit_label = self._MEASURE_KIND_UNIT.get(self._payload_measure_kind(payload), "шт")
            candidate_value = self._parse_number_with_thousands_separator(payload["bare_price_candidate"])
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_bare_price_candidate",
                self._confirmation_reply(
                    f"Похоже на цену за {unit_label} ({_display_bot_number(candidate_value)}).\n"
                    "Верно?\nДа / Нет / Редактировать"
                ),
            )

        # Реальний баг зі скріна: "ЕФАКТУРА" (без уточнення Б/Н чи Н) не
        # збігається ТОЧНО із жодним способом оплати — раніше мовчки падало
        # у вільний текст і зливалось із клієнтом ("ACOPERIS PENTRU FIECARE
        # ЕФАКТУРА"). Загальний запобіжник (не лише для цього одного слова):
        # будь-яке слово, що є ПОЧАТКОМ (префіксом) відомого способу оплати,
        # вирізається окремо в _extract_unlabeled_sale_markers і сюди
        # приходить як payload["payment_method_candidate"] — питаємо
        # уточнення замість мовчазного злиття.
        if payload.get("payment_method_candidate"):
            candidate = payload["payment_method_candidate"]
            options = candidate.get("candidates", [])
            typed = candidate.get("typed", "")
            return self._save_income_question(
                store,
                context,
                payload,
                "confirm_payment_method_candidate",
                {
                    "type": "message",
                    "text": (
                        f'Способ оплаты "{typed}" указан не полностью.\n'
                        "Уточните, какой именно вариант:"
                    ),
                    "reply_markup": self._payment_method_candidate_keyboard(options),
                },
            )

        # Задача користувача: "сам бот в режим (форма) не має нічого в чаті
        # дозапитувати. все має бути в формі" - подання з мега-форми/webapp-
        # кнопки вже пройшло клієнтську валідацію required-полів (JS не
        # дає натиснути "Отправить" з порожнім обов'язковим полем) - якщо
        # чек-лист УСЕ ОДНО щось знайшов (розбіжність конфігурації полів,
        # рідкісний edge case), це вже НЕ "запитати ще раз", а термінальна
        # відмова - людина повертається до форми, а не в чатовий діалог.
        missing_fields = self._income_missing_fields(store, payload, kind="sale")
        if missing_fields:
            if payload.get("_from_webapp_form"):
                return self._webapp_form_terminal_reply(
                    store, context, self._sale_missing_prompt(missing_fields, payload, store=store)
                )
            typo_issue = self._income_quantity_typo_issue(payload)
            if typo_issue:
                payload["quantity_typo_issue"] = typo_issue
                return self._save_income_question(
                    store,
                    context,
                    payload,
                    "confirm_quantity_typo",
                    self._quantity_typo_prompt(payload, typo_issue),
                )
            return self._save_income_question(
                store,
                context,
                payload,
                "collect_income_missing",
                self._sale_missing_prompt(missing_fields, payload, store=store),
            )

        validation = self._next_sale_validation_issue(store, payload)
        if validation:
            if validation.get("field") == "schema":
                return (
                    "Не могу оформить продажу: в листе СКЛАД не найдены нужные колонки: "
                    f"{validation.get('value')}."
                )
            if validation.get("suggestion") is not None:
                if payload.get("_from_webapp_form"):
                    value = validation.get("value", "")
                    label = validation.get("label", "Значение")
                    return self._webapp_form_terminal_reply(
                        store,
                        context,
                        f'{label} "{_display_bot_number(value)}" не найдено на складе. Начните продажу заново через форму.',
                    )
                payload["validation"] = validation
                store.save_pending_operation(
                    context["chat_id"],
                    context["user_id"],
                    "stock_sale",
                    "confirm_suggestion",
                    payload,
                )
                return self._validation_suggestion_prompt(validation, payload)
            if validation.get("field") == "client":
                # Новий клієнт без схожого збігу — це не "позиція не знайдена
                # на складе", а звичайне поповнення клієнтської бази. Це
                # питання лишається інтерактивним НАВІТЬ у режимі (форма) -
                # межа розділення (form-mode boundary) стосується недостачі
                # складських даних, а не цього вже готового, окремого
                # механізму підтвердження нового клієнта.
                payload["validation"] = validation
                store.save_pending_operation(
                    context["chat_id"],
                    context["user_id"],
                    "stock_sale",
                    "confirm_new_value",
                    payload,
                )
                value = validation.get("value", "")
                return self._yes_no_reply(
                    f'Клиент "{value}" не найден в истории продаж.\n'
                    f'Добавить нового клиента "{value}"?\n'
                    "Да / Нет"
                )
            if payload.get("_from_webapp_form"):
                value = validation.get("value", "")
                label = validation.get("label", "Значение")
                return self._webapp_form_terminal_reply(
                    store,
                    context,
                    f'{label} "{_display_bot_number(value)}" не найдено на складе. Начните продажу заново через форму.',
                )
            return self._save_sale_not_found_question(store, context, payload)

        amount_issue = self._prepare_income_amounts(payload)
        if amount_issue:
            if payload.get("_from_webapp_form"):
                message = amount_issue["message"]
                text = message if isinstance(message, str) else message.get(
                    "text", "Не удалось рассчитать количество/объём. Начните продажу заново через форму."
                )
                return self._webapp_form_terminal_reply(store, context, text)
            payload[amount_issue["payload_key"]] = amount_issue["payload"]
            return self._save_income_question(
                store,
                context,
                payload,
                amount_issue["status"],
                amount_issue["message"],
            )

        match_issue = self._resolve_sale_rows(store, payload)
        if match_issue:
            if payload.get("_from_webapp_form"):
                # На відміну від дефіциту складу/бракуючих полів (нижче) - тут
                # НЕ завершуємо операцію терміновою відповіддю. Розмір, якого
                # немає на складі, можна виправити тією ж самою формою (той
                # самий "row replace", а не append, механізм _merge_webapp_
                # submission) без втрати вже введених клиента/адреси/ціни/
                # оплати - тому pending лишається живим, а не видаляється.
                item = match_issue.get("item")
                text = match_issue.get("message") or (
                    f"Не найдено на складе: {sale_position_text(payload, item)}."
                    if item is not None
                    else "Не найдено на складе. Проверьте размер и породу."
                )
                text += "\n\nИсправьте размер в той же форме и отправьте её заново."
                return self._save_income_question(store, context, payload, "collect_income_missing", text)
            if match_issue.get("message"):
                # Аудит коду: раніше тут повертався голий словник-відповідь
                # БЕЗ store.save_pending_operation — payload цього ходу (уже
                # об'єднані розміри/порода) губився, а в БД лишався
                # попередній (застарілий) стан. Наступна відповідь
                # користувача (назва товару, щоб зняти неоднозначність)
                # оцінювалась проти застарілого кроку — та сама точка входу
                # ("ask_product"), що вже коректно працює для приходу.
                return self._save_income_question(store, context, payload, "ask_product", match_issue["message"])
            return self._save_sale_not_found_question(
                store,
                context,
                payload,
                match_issue.get("item"),
                match_issue.get("row_index"),
            )

        stock_issue = self._sale_stock_issue(store, payload)
        if stock_issue:
            if payload.get("_from_webapp_form"):
                text = self._sale_stock_issue_text(payload, stock_issue)
                text += "\n\nУменьшите количество в форме и отправьте её заново."
                return self._webapp_form_terminal_reply(store, context, text)
            return self._sale_stock_issue_reply(store, context, payload, stock_issue)

        # Клиент/цена/способ оплаты (ТЗ, розділ 4 і 8) — обов'язкові для
        # продажу, на відміну від приходу, тому НЕ входять у спільний
        # _income_missing_fields. Питаються ПІСЛЯ того, як позиція(ї) вже
        # підтверджені і є на складі в потрібній кількості — немає сенсу
        # питати "хто покупець і за скільки", поки ще не ясно, ЩО саме
        # продається (розмір міг виявитись відсутнім/неоднозначним і
        # потребувати "Похожие позиции"/"Использовать N шт" тощо). Гола
        # відповідь без мітки трактується як значення КОНКРЕТНО поточного
        # поля (той самий принцип, що й ask_item_amount/ask_dimension) — але
        # текст запрошення показує ВЕСЬ список того, чого ще не вистачає
        # (_sale_mandatory_fields_prompt), а не лише поточне поле.
        # Крок 3+ "Дії": раніше ці 3 гейти вимагали Клиент/Цена/Способ
        # оплаты БЕЗУМОВНО, незалежно від того, чи налаштоване відповідне
        # поле-запит у bot_operation_fields — видалення поля через "Дії"
        # НІЧОГО не міняло в реальній розмові (бот однаково зупинявся й
        # питав, той самий баг класу, що й раніше з "Клиент" на приході).
        # Задача користувача: "якщо дані ніде не записуються - так видали
        # їх" — тепер, як і решта чек-листа, кожен гейт спершу перевіряє,
        # чи поле взагалі налаштоване; для товару ПОЗА 4 категоріями (де
        # resolve_operation_for_payload не знаходить операцію) лишається
        # стара безумовна поведінка — там немає конфігурації, яку можна
        # було б звірити.
        sale_operation_id = resolve_operation_for_payload(store, "start_sale", "sale", payload)
        configured_sale_fields = (
            {field[2] for field in store.list_operation_fields(sale_operation_id)}
            if sale_operation_id is not None
            else None
        )

        def sale_field_required(field_key):
            return configured_sale_fields is None or field_key in configured_sale_fields

        missing_mandatory = (
            (sale_field_required("client") and not payload.get("client"))
            or (sale_field_required("address") and not payload.get("address"))
            or (
                sale_field_required("price_per_unit")
                and not (
                    _number_value(payload.get("price_per_unit")) > 0
                    or _number_value(payload.get("total_amount")) > 0
                )
            )
            or (sale_field_required("payment_method") and not payload.get("payment_method"))
        )
        if missing_mandatory and payload.get("_from_webapp_form"):
            return self._webapp_form_terminal_reply(store, context, self._sale_mandatory_fields_prompt(store, payload))

        if sale_field_required("client") and not payload.get("client"):
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_sale_client",
                {
                    "type": "message",
                    "text": self._sale_mandatory_fields_prompt(store, payload),
                    "reply_markup": self._client_entry_keyboard(),
                },
            )
        # Свіжий пере-аудит (2026-08-02, New-Important #7): "Адрес выгрузки"
        # уже показувалась у чек-листі "Не хватает данных", але без цього
        # гейту нічого не блокувало завершення продажу без неї (напр. одне
        # повідомлення з усіма даними одразу) — на відміну від client/price/
        # payment_method, кожне з яких завжди мало власний гейт нижче.
        if sale_field_required("address") and not payload.get("address"):
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_sale_address",
                self._sale_mandatory_fields_prompt(store, payload),
            )
        has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
        if sale_field_required("price_per_unit") and not has_price:
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_sale_price",
                self._sale_mandatory_fields_prompt(store, payload),
            )
        if sale_field_required("payment_method") and not payload.get("payment_method"):
            return self._save_income_question(
                store,
                context,
                payload,
                "ask_sale_payment_method",
                {
                    "type": "message",
                    "text": self._sale_mandatory_fields_prompt(store, payload),
                    "reply_markup": self._payment_method_keyboard(store),
                },
            )

        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_sale",
            "confirm_write",
            payload,
        )
        return {
            "type": "message",
            "text": self._sale_preview(payload),
            "reply_markup": self._sale_confirm_keyboard(from_webapp_form=payload.get("_from_webapp_form", False)),
        }

    def _save_income_question(self, store, context, payload, status, text):
        operation_type = "stock_sale" if payload.get("operation_kind") == "sale" else "add_income"
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            operation_type,
            status,
            payload,
        )
        if isinstance(text, str):
            # Голий рядок означає "чекаю вільний текст" (розмір/кількість/
            # порода тощо) — жодна попередня клавіатура (напр. Да/Нет після
            # підтвердження зміни категорії — саме це сплутало користувача)
            # тут не доречна, але й без жодної кнопки лишати теж не можна —
            # вихід (Отмена) має бути видимим завжди, а поруч — "Редактировать"
            # (ТЗ: кнопка редагування для ВСІХ випадків ручного вводу). Дикти
            # (напр. з _yes_no_reply) вже несуть свою правильну клавіатуру і
            # йдуть без змін.
            keyboard = self._cancel_and_edit_keyboard()
            # Форма (Telegram Mini App) — лише на самому чек-листі бракуючих
            # полів, не на кожному транзитному питанні через цю саму функцію
            # (напр. "confirm_single_thickness" — Да/Нет, форма там не
            # доречна). Текст чек-листа лишається як є — форма ДОДАЄ кнопку,
            # не замінює вільнотекстовий шлях.
            if status == "collect_income_missing":
                operation_id = resolve_operation_for_payload(
                    store,
                    "start_sale" if operation_type == "stock_sale" else "start_income",
                    payload.get("operation_kind", "income"),
                    payload,
                )
                title = "Продажа" if operation_type == "stock_sale" else "Приход"
                field_keys = self._WEBAPP_IDENTITY_DIMENSION_KEYS
                if operation_type == "stock_sale":
                    field_keys = field_keys + self._WEBAPP_SALE_FLAT_KEYS
                # Задача користувача: продаж - лише РЕАЛЬНІ комбінації
                # (не можна продати розмір, якого нема); приход - навпаки,
                # нова комбінація - нормальний сценарій, дропдауни без
                # звуження.
                keyboard = self._webapp_keyboard(
                    store, operation_id, field_keys, payload, title, keyboard,
                    restrict_to_existing_combos=(operation_type == "stock_sale"),
                )
            return {"type": "message", "text": text, "reply_markup": keyboard}
        return text

    # "Редактировать" на кроках, де гола відповідь інакше стала б буквальним
    # значенням поточного поля (ask_sale_client/ask_sale_price/
    # ask_sale_payment_method) — замість цього повертаємось до
    # collect_income_missing: той самий загальний парсер вільного тексту
    # (_apply_income_free_values через continue_operation) вже вміє приймати
    # виправлення БУДЬ-якого поля (порода/розмір/клієнт/ціна/оплата), а не
    # лише того, що саме зараз запитувалось.
    def _reopen_sale_collection(self, store, context, payload):
        missing_fields = self._income_missing_fields(store, payload, kind="sale")
        prompt = "Напишите новое значение — я заменю только то, что вы укажете.\n\n" + self._sale_missing_prompt(
            missing_fields, payload, store=store
        )
        return self._save_income_question(store, context, payload, "collect_income_missing", prompt)

    def _reopen_income_collection(self, store, context, payload):
        missing_fields = self._income_missing_fields(store, payload, kind="income")
        prompt = "Напишите новое значение — я заменю только то, что вы укажете.\n\n" + self._income_missing_prompt(
            missing_fields, payload, store=store
        )
        return self._save_income_question(store, context, payload, "collect_income_missing", prompt)

    # --- "Підозріла" кількість: підтвердження перед збереженням операції ---
    def _handle_suspicious_quantity_choice(self, answer, store, context, payload, continue_operation):
        choice = self._quantity_issue_choice(answer)
        if choice == "edit_quantity":
            issue = payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload)
            payload["suspicious_quantity"] = issue
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                "ask_suspicious_quantity_value",
                payload,
            )
            return self._suspicious_quantity_value_prompt(payload, issue)
        if choice == "keep":
            issue = payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload)
            payload["quantity_limit_confirmed_total"] = issue.get("total_quantity") if issue else None
            payload.pop("suspicious_quantity", None)
            return continue_operation(store, context, payload)
        if choice == "no":
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                "edit_operation_data",
                payload,
            )
            return self._prepend_reply_text(
                "Хорошо, тогда отредактируем данные.",
                self._edit_prompt_reply(),
            )
        return self._suspicious_quantity_reply(
            payload,
            payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload),
        )

    def _handle_suspicious_quantity_value(self, answer, store, context, payload):
        issue = payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload)
        number = self._parse_income_quantity(answer)
        if number is None:
            number = self._parse_plain_positive_number(answer)
        if number is None or number <= 0:
            return self._suspicious_quantity_value_prompt(payload, issue)
        row_index = issue.get("row_index", 0)
        item = payload.get("rows", [{}])[row_index]
        payload["pending_quantity_change"] = {
            "row_index": row_index,
            "old_quantity": item.get("quantity"),
            "new_quantity": int(round(number)) if self._is_whole_number(number) else number,
        }
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "add_income",
            "confirm_suspicious_quantity_value",
            payload,
        )
        return self._quantity_change_confirmation_reply(payload)

    def _handle_suspicious_quantity_value_confirmation(self, answer, store, context, payload, continue_operation):
        choice = self._quantity_change_choice(answer)
        if choice == "change_digit":
            issue = payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload)
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                "ask_suspicious_quantity_value",
                payload,
            )
            return self._suspicious_quantity_value_prompt(payload, issue)
        if choice == "no":
            payload.pop("pending_quantity_change", None)
            issue = payload.get("suspicious_quantity") or self._income_quantity_limit_issue(payload)
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "add_income",
                "confirm_suspicious_quantity",
                payload,
            )
            return self._suspicious_quantity_reply(
                payload,
                issue,
                prefix="Хорошо, цифру не меняю. Что делаем с этой строкой?",
            )
        if choice != "yes":
            return self._quantity_change_confirmation_reply(payload)

        change = payload.pop("pending_quantity_change", {})
        row_index = change.get("row_index")
        if row_index is None or row_index >= len(payload.get("rows", [])):
            return {
                "type": "message",
                "text": "Не удалось найти строку для изменения. Введите данные заново.",
                "reply_markup": self._cancel_only_keyboard(),
            }
        payload["_edit_before"] = self._operation_snapshot(payload)
        item = payload["rows"][row_index]
        item["quantity"] = change.get("new_quantity")
        item["quantity_provided"] = True
        # Аудит коду: тут скидався лише volume — для Вагонки (площа)/мп-
        # розміру стара area/linear лишалась застарілою, і бот плутався сам
        # із собою, зайве перепитуючи "Использовать вычисленное значение?".
        # Той самий патерн, що вже є в _apply_plain_amount_unit (три
        # взаємовиключні виміри скидаються РАЗОМ).
        item["volume"] = None
        item["volume_provided"] = False
        item["area"] = None
        item["area_provided"] = False
        item["linear"] = None
        item["linear_provided"] = False
        payload.pop("suspicious_quantity", None)
        return continue_operation(store, context, payload)

    def _quantity_issue_choice(self, answer):
        normalized = _normalize_phrase(answer)
        if normalized in {"да", "yes", "y", "так", "da", "1", "изменить количество", "изменить цифру"}:
            return "edit_quantity"
        if normalized in {
            "нет", "no", "n", "ні", "net", "2",
            "общее редактирование", "общая правка", "редактировать", "ред",
        }:
            return "no"
        if normalized in {"оставить как есть", "оставить", "продолжить", "оставить данные", "3"}:
            return "keep"
        return None

    def _quantity_change_choice(self, answer):
        normalized = _normalize_phrase(answer)
        if normalized in {"да", "yes", "y", "так", "da", "1"}:
            return "yes"
        if normalized in {"нет", "no", "n", "ні", "net", "2"}:
            return "no"
        if normalized in {"изменить цифру", "другая цифра", "поменять цифру", "3"}:
            return "change_digit"
        return None

    def _suspicious_quantity_reply(self, payload, issue, prefix=None):
        if not issue:
            return {
                "type": "message",
                "text": "Указано подозрительное количество. Что нужно отредактировать?",
                "reply_markup": self._cancel_only_keyboard(),
            }
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.extend(
            [
                (
                    "Указано подозрительное количество: "
                    f"{_display_bot_number(issue.get('total_quantity'))} шт / "
                    f"{_display_bot_number(issue.get('total_volume'))} м3."
                ),
                "",
                "Похоже, ошибка в строке:",
                self._issue_row_text(payload, issue),
                "",
                "Изменить только количество в этой строке?",
                "Да / Нет / Оставить как есть",
            ]
        )
        return {
            "type": "message",
            "text": "\n".join(lines),
            "reply_markup": self._quantity_issue_keyboard(),
        }

    def _suspicious_quantity_value_prompt(self, payload, issue):
        item = self._issue_item(payload, issue)
        return {
            "type": "message",
            "text": (
                f"Введите новое количество для позиции {income_item_size(item)}.\n"
                "Можно просто цифру, я приму ее как штуки."
            ),
            "reply_markup": self._cancel_only_keyboard(),
        }

    def _quantity_change_confirmation_reply(self, payload):
        change = payload.get("pending_quantity_change") or {}
        item = self._issue_item(payload, {"row_index": change.get("row_index", 0)})
        return {
            "type": "message",
            "text": (
                f"В позиции {income_item_size(item)} меняем "
                f"{_display_bot_number(change.get('old_quantity'))} шт на "
                f"{_display_bot_number(change.get('new_quantity'))} шт?\n"
                "Да / Нет / Изменить цифру"
            ),
            "reply_markup": self._quantity_change_keyboard(),
        }

    def _issue_item(self, payload, issue):
        row_index = (issue or {}).get("row_index", 0)
        rows = payload.get("rows") or [{}]
        if row_index is None or row_index >= len(rows):
            return rows[0]
        return rows[row_index]

    def _issue_row_text(self, payload, issue):
        row_index = (issue or {}).get("row_index", 0)
        item = self._issue_item(payload, issue)
        measure_key = item_measure_kind(item)
        if measure_key is None:
            return f"{row_index + 1}. {income_item_size(item)} — {_display_bot_number(item.get('quantity'))} шт"
        return (
            f"{row_index + 1}. {income_item_size(item)} — "
            f"{_display_bot_number(item.get('quantity'))} шт — "
            f"{_display_bot_number(item.get(measure_key))} {ITEM_MEASURE_UNIT[measure_key]}"
        )

    # --- Редагування вже введених даних операції (вільний текст або по рядку) ---
    def _handle_operation_edit(self, answer, store, context, payload, operation_type, continue_operation):
        if self._is_edit_request(answer):
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "edit_operation_data",
                payload,
            )
            return self._prepend_reply_text(
                "Да, сейчас редактируем.",
                self._edit_prompt_reply(),
            )

        target_request = self._edit_target_request_from_answer(answer, payload)
        if target_request:
            if target_request["field"] == "length":
                payload["pending_edit_length_value"] = {"value": target_request["value"]}
                store.save_pending_operation(
                    context["chat_id"],
                    context["user_id"],
                    operation_type,
                    "confirm_edit_length_value",
                    payload,
                )
                return self._edit_length_confirmation_reply(payload)
            payload["_edit_before"] = self._operation_snapshot(payload)
            payload["edit_target_request"] = target_request
            if len(payload.get("rows") or []) > 1:
                store.save_pending_operation(
                    context["chat_id"],
                    context["user_id"],
                    operation_type,
                    "choose_edit_row",
                    payload,
                )
                return self._edit_row_choice_reply(payload, target_request)
            self._apply_edit_target(payload, target_request, [0])
            return continue_operation(store, context, payload)

        extra_fields = {}
        source_text = answer
        if operation_type == "stock_sale":
            source_text, extra_fields = self._extract_sale_fields(source_text, store)
        source_text = self._strip_sale_command_words(source_text) if operation_type == "stock_sale" else source_text
        incoming_payload, _ = self._parse_income_message(source_text)
        if not self._parsed_payload_has_data(incoming_payload) and not extra_fields:
            return {
                "type": "message",
                "text": "Не нашел новых данных для редактирования.\nВведите новые данные.",
                "reply_markup": self._cancel_only_keyboard(),
            }

        payload["_edit_before"] = self._operation_snapshot(payload)
        self._apply_operation_edit(store, payload, incoming_payload)
        payload.update(extra_fields)
        payload["original_text"] = "\n".join(
            part
            for part in [payload.get("original_text", ""), f"Правка: {answer}"]
            if part
        )
        pending_length_value = payload.pop("_pending_length_confirmation", None)
        if pending_length_value is not None:
            payload["pending_edit_length_value"] = {"value": pending_length_value}
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "confirm_edit_length_value",
                payload,
            )
            return self._edit_length_confirmation_reply(payload)
        return continue_operation(store, context, payload)

    def _edit_target_request_from_answer(self, answer, payload):
        quantity = self._parse_income_quantity(answer)
        volume = self._parse_income_volume(answer)
        area = self._parse_income_area(answer)
        linear = self._parse_income_linear(answer)
        measures = [value for value in (volume, area, linear) if value is not None]
        plain = self._parse_plain_positive_number(answer)
        if quantity is not None and not measures:
            return {"field": "quantity", "value": quantity}
        if len(measures) == 1 and quantity is None:
            if volume is not None:
                return {"field": "volume", "value": volume}
            if area is not None:
                return {"field": "area", "value": area}
            return {"field": "linear", "value": linear}
        if plain is None:
            return None
        # Аудит коду: для len(rows) > 1 маленьке число завжди трактувалось
        # як кількість — але для одно-позиційної продажі та сама логіка
        # чомусь не застосовувалась (лише довжина >= 1000, інакше None) —
        # правка кількості голим числом мовчки ігнорувалась ("Не нашел
        # новых данных"). Тепер обидва випадки рахують поле однаково.
        field = "length" if plain >= 1000 else "quantity"
        return {"field": field, "value": plain}

    def _edit_length_confirmation_reply(self, payload):
        pending = payload.get("pending_edit_length_value") or {}
        value = pending.get("value")
        return {
            "type": "message",
            "text": (
                f"Меняем длину товара на {_display_bot_number(value)}? "
                "Если вы имели в виду количество, а не длину — нажмите Нет.\n"
                "Да / Нет / Изменить цифру"
            ),
            "reply_markup": self._quantity_change_keyboard(),
        }

    # Важлива знахідка нового аудиту (28.07.2026, #1): голе число ≥1000 при
    # "Редактировать" раніше застосовувалось як ДОВЖИНА одразу, без жодного
    # підтвердження — той самий клас двозначності, що вже захищений для
    # "підозрілої кількості" (_quantity_change_keyboard/_quantity_change_choice,
    # _handle_suspicious_quantity_value_confirmation вище) — тут
    # перевикористовується той самий, уже наявний UI-патерн.
    def _handle_edit_length_value_confirmation(
        self, answer, store, context, payload, operation_type, continue_operation
    ):
        choice = self._quantity_change_choice(answer)
        pending = payload.get("pending_edit_length_value") or {}
        if choice == "change_digit":
            payload.pop("pending_edit_length_value", None)
            store.save_pending_operation(
                context["chat_id"], context["user_id"], operation_type, "edit_operation_data", payload
            )
            return self._prepend_reply_text("Хорошо, введите новое значение.", self._edit_prompt_reply())
        if choice == "no":
            payload.pop("pending_edit_length_value", None)
            store.save_pending_operation(
                context["chat_id"], context["user_id"], operation_type, "edit_operation_data", payload
            )
            return self._prepend_reply_text("Хорошо, не меняю длину.", self._edit_prompt_reply())
        if choice != "yes":
            return self._edit_length_confirmation_reply(payload)

        payload.pop("pending_edit_length_value", None)
        target_request = {"field": "length", "value": pending.get("value")}
        payload["_edit_before"] = self._operation_snapshot(payload)
        payload["edit_target_request"] = target_request
        if len(payload.get("rows") or []) > 1:
            store.save_pending_operation(
                context["chat_id"], context["user_id"], operation_type, "choose_edit_row", payload
            )
            return self._edit_row_choice_reply(payload, target_request)
        self._apply_edit_target(payload, target_request, [0])
        return continue_operation(store, context, payload)

    def _edit_row_choice_reply(self, payload, request):
        field = request.get("field")
        value = request.get("value")
        labels = {
            "length": "длину",
            "quantity": "количество",
            "volume": "объем",
            "area": "площадь",
            "linear": "метраж",
        }
        units = {
            "length": "",
            "quantity": " шт",
            "volume": " м3",
            "area": " м2",
            "linear": " мп",
        }
        lines = [
            f"В какой позиции изменить {labels.get(field, 'значение')} на {_display_bot_number(value)}{units.get(field, '')}?",
            "",
        ]
        for index, item in enumerate(payload.get("rows") or [], start=1):
            lines.append(
                f"{index}. {income_item_size(item)} — "
                f"{_display_bot_number(item.get('quantity'))} шт — "
                f"{_display_bot_number(item.get('volume'))} м3"
            )
        lines.append(f"{len(payload.get('rows') or []) + 1}. Для всех")
        lines.append("")
        lines.append("Напишите номер позиции или 'для всех'.")
        return {
            "type": "message",
            "text": "\n".join(lines),
            "reply_markup": self._cancel_only_keyboard(),
        }

    def _handle_edit_row_choice(self, answer, store, context, payload, continue_operation):
        request = payload.get("edit_target_request") or {}
        rows = payload.get("rows") or []
        normalized = _normalize_phrase(answer)
        if normalized in {"для всех", "все", "all"}:
            indexes = list(range(len(rows)))
        elif normalized.isdigit():
            choice = int(normalized)
            if choice == len(rows) + 1:
                indexes = list(range(len(rows)))
            elif 1 <= choice <= len(rows):
                indexes = [choice - 1]
            else:
                return self._edit_row_choice_reply(payload, request)
        else:
            return self._edit_row_choice_reply(payload, request)
        self._apply_edit_target(payload, request, indexes)
        payload.pop("edit_target_request", None)
        return continue_operation(store, context, payload)

    def _apply_edit_target(self, payload, request, row_indexes):
        field = request.get("field")
        value = request.get("value")
        for row_index in row_indexes:
            if row_index < 0 or row_index >= len(payload.get("rows") or []):
                continue
            item = payload["rows"][row_index]
            if field in {"thickness", "width", "length"}:
                item[field] = _number_value(value)
                for measure_field in self._MEASURE_FIELDS:
                    if not item.get(f"{measure_field}_provided"):
                        item[measure_field] = None
            elif field == "quantity":
                item["quantity"] = int(round(_number_value(value))) if self._is_whole_number(value) else _number_value(value)
                item["quantity_provided"] = True
                for measure_field in self._MEASURE_FIELDS:
                    item[measure_field] = None
                    item[f"{measure_field}_provided"] = False
            elif field in self._MEASURE_FIELDS:
                item[field] = _number_value(value)
                item[f"{field}_provided"] = True
                item["quantity"] = None
                item["quantity_provided"] = False

    def _parsed_payload_has_data(self, payload):
        if not payload:
            return False
        if any(payload.get(field) for field in ("product", "breed", "condition")):
            return True
        if payload.get("free_values") or payload.get("unknown_fields") or payload.get("single_dimension_candidate"):
            return True
        for row in payload.get("rows") or []:
            if any(
                _number_value(row.get(field)) > 0
                for field in ("thickness", "width", "length", "quantity", "volume", "area", "linear")
            ):
                return True
        return False

    def _apply_operation_edit(self, store, payload, incoming):
        for key in (
            "validation",
            "dimension_request",
            "amount_request",
            "amount_unit_request",
            "quantity_options",
            "volume_conflict",
            "field_mapping",
            "sale_not_found_item",
        ):
            payload.pop(key, None)

        for field in ("product", "breed", "condition"):
            if incoming.get(field):
                payload[field] = incoming[field]

        if incoming.get("free_values"):
            self._apply_edit_free_values(store, payload, incoming.get("free_values") or [])

        if incoming.get("unknown_fields"):
            payload.setdefault("unknown_fields", [])
            payload["unknown_fields"].extend(incoming.get("unknown_fields") or [])

        incoming_rows = incoming.get("rows") or []
        if incoming_rows:
            if len(incoming_rows) > 1 or not payload.get("rows"):
                payload["rows"] = [self._copy_income_row(row) for row in incoming_rows]
            else:
                self._apply_edit_row(payload["rows"][0], incoming_rows[0])

        single_candidate = incoming.get("single_dimension_candidate")
        if single_candidate and payload.get("rows"):
            value = _number_value(single_candidate.get("value"))
            if value >= 1000:
                # Важлива знахідка нового аудиту (28.07.2026, #1): той самий
                # клас двозначності, що й у _edit_target_request_from_answer
                # (голе число >=1000 -> довжина) — тут НЕ застосовуємо
                # одразу, а сигналізуємо виклику (_handle_operation_edit)
                # завести те саме підтвердження через pending_edit_length_value.
                payload["_pending_length_confirmation"] = value
            elif not self._apply_single_dimension_to_missing(payload, value):
                payload["single_dimension_candidate"] = single_candidate

    # Реальний баг з аудиту: area/linear тут узагалі не копіювались — площа
    # (Вагонка) чи мп губились при "Редактировать"/конфлікті кількості,
    # хоча самі значення й НЕ null у вихідному рядку.
    def _copy_income_row(self, row):
        copied = self._empty_income_row()
        copied.update(
            {
                key: row.get(key)
                for key in (
                    "thickness",
                    "width",
                    "length",
                    "quantity",
                    "volume",
                    "area",
                    "linear",
                    "quantity_provided",
                    "volume_provided",
                    "area_provided",
                    "linear_provided",
                )
            }
        )
        return copied

    # area/linear (не лише volume) — той самий реальний баг з аудиту:
    # редагування рядка з площею чи мп мовчки губило це значення, бо ця
    # функція перевіряла ЛИШЕ "volume".
    _MEASURE_FIELDS = ("volume", "area", "linear")

    def _apply_edit_row(self, target, incoming):
        changed_dimension = False
        for field in ("thickness", "width", "length"):
            if _number_value(incoming.get(field)) > 0:
                new_value = _number_value(incoming.get(field))
                changed_dimension = changed_dimension or not self._number_equal(target.get(field), new_value)
                target[field] = new_value

        if _number_value(incoming.get("quantity")) > 0:
            target["quantity"] = incoming.get("quantity")
            target["quantity_provided"] = incoming.get("quantity_provided", True)
            for measure_field in self._MEASURE_FIELDS:
                if not incoming.get(f"{measure_field}_provided"):
                    target[measure_field] = None
                    target[f"{measure_field}_provided"] = False

        for measure_field in self._MEASURE_FIELDS:
            if _number_value(incoming.get(measure_field)) > 0:
                target[measure_field] = incoming.get(measure_field)
                target[f"{measure_field}_provided"] = incoming.get(f"{measure_field}_provided", True)
                if not incoming.get("quantity_provided"):
                    target["quantity"] = None
                    target["quantity_provided"] = False

        if changed_dimension:
            for measure_field in self._MEASURE_FIELDS:
                if not target.get(f"{measure_field}_provided"):
                    target[measure_field] = None

    def _apply_edit_free_values(self, store, payload, free_values):
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error as exc:
            # Реальний ризик з аудиту: раніше мовчки "return" - виправлення
            # користувача ("заменить на Х") просто зникало без жодного
            # сліду в журналі, при "database is locked" (бот і GUI пишуть у
            # той самий файл одночасно). add_action_log сам пише в ту саму
            # БД, яка щойно кинула помилку - тому в окремому try/except (той
            # самий принцип, що й у finally-блоці _build_reply_pipeline
            # вище): якщо лишилась заблокованою, просто мовчимо, як і
            # раніше, а не падаємо вдруге на спробі залогувати.
            try:
                store.add_action_log(
                    "edit_free_values_failed",
                    {"error": str(exc), "free_values": free_values},
                )
            except sqlite3.Error:
                pass
            return

        condition_values = self._existing_product_type_values(rows, columns.get("product"))
        existing = {
            "product": self._existing_product_values(rows, columns.get("product"), condition_values),
            "breed": self._existing_values(rows, columns.get("breed"), False),
            "condition": condition_values,
        }
        ignored = {"на", "в", "с", "з", "из", "to", "заменить", "замени", "изменить", "измени", "поменять", "поменяй"}
        for value in free_values:
            normalized = _normalize_phrase(value)
            if not normalized or normalized in ignored or self._is_income_command_line(value):
                continue
            if self._is_osb_product(value):
                payload["product"] = "ОСБ"
                payload["breed"] = "Другое"
                payload["condition"] = None
                continue

            product, condition = self._split_product_condition(value, existing.get("condition"))
            applied = False
            if product and self._value_exists(product, existing["product"], False):
                payload["product"] = product
                applied = True
            if condition:
                payload["condition"] = condition
                applied = True
            if applied:
                continue
            if self._value_exists(value, existing["product"], False):
                payload["product"] = value
            else:
                # Аудит коду (той самий гандж, що вже виправлений у
                # _assign_income_free_value, лише не поширений на цю
                # сусідню функцію редагування): "орех"/"тіс" тощо
                # транслітеруються в ≤4 літери за _looks_like_condition_code
                # — довіряти цьому "здогаду" лише коли value НЕ схожа на
                # жодну відому породу, інакше редагування (Редагувати ->
                # вписати породу вручну одним словом) записувало б "орех"
                # як condition, а не breed.
                looks_like_code_not_a_known_breed = self._looks_like_condition_code(value) and not (
                    self._value_exists(value, existing["breed"], False)
                    or self._suggest_value(value, existing["breed"], False) is not None
                )
                if (
                    self._value_exists(value, existing["condition"], False)
                    or self._suggest_value(value, existing["condition"], False) is not None
                    or looks_like_code_not_a_known_breed
                ):
                    payload["condition"] = value
                else:
                    payload["breed"] = value

    def _operation_snapshot(self, payload):
        return {
            "product": payload.get("product"),
            "breed": payload.get("breed"),
            "condition": payload.get("condition"),
            "client": payload.get("client"),
            "price_per_unit": payload.get("price_per_unit"),
            "payment_method": payload.get("payment_method"),
            "document_type": payload.get("document_type"),
            "manager": payload.get("manager"),
            "comment": payload.get("comment"),
            "rows": [
                {
                    "thickness": row.get("thickness"),
                    "width": row.get("width"),
                    "length": row.get("length"),
                    "quantity": row.get("quantity"),
                    "volume": row.get("volume"),
                    "area": row.get("area"),
                    "linear": row.get("linear"),
                }
                for row in payload.get("rows") or []
            ],
        }

    def _edit_change_lines(self, payload):
        before = payload.get("_edit_before")
        if not before:
            return []
        after = self._operation_snapshot(payload)
        lines = ["Изменения:"]
        labels = {
            "product": "Продукт",
            "breed": "Порода",
            "condition": "Тип продукта",
            "thickness": "Толщина",
            "width": "Ширина",
            "length": "Длина",
            "quantity": "Количество",
            "volume": "Объем",
            "area": "Площадь",
            "linear": "Метраж",
            "client": "Клиент",
            "price_per_unit": "Цена за ед.",
            "payment_method": "Способ оплаты",
            "document_type": "Тип документа",
            "manager": "Ответственный",
            "comment": "Комментарий",
        }
        for field in ("product", "breed", "condition", "client", "price_per_unit", "payment_method", "document_type", "manager", "comment"):
            if self._snapshot_value(before.get(field)) != self._snapshot_value(after.get(field)):
                lines.append(
                    f"- {labels[field]}: {self._snapshot_value(before.get(field))} -> "
                    f"{self._snapshot_value(after.get(field))}"
                )

        before_rows = before.get("rows") or []
        after_rows = after.get("rows") or []
        max_rows = max(len(before_rows), len(after_rows))
        for index in range(max_rows):
            if index >= len(before_rows):
                lines.append(f"- Позиция {index + 1}: добавлена {self._snapshot_row_text(after_rows[index])}")
                continue
            if index >= len(after_rows):
                lines.append(f"- Позиция {index + 1}: удалена")
                continue
            before_row = before_rows[index]
            after_row = after_rows[index]
            for field in ("thickness", "width", "length", "quantity", "volume", "area", "linear"):
                if self._snapshot_value(before_row.get(field)) != self._snapshot_value(after_row.get(field)):
                    unit = {"quantity": " шт", "volume": " м3", "area": " м2", "linear": " мп"}.get(field, "")
                    lines.append(
                        f"- Позиция {index + 1}, {labels[field]}: "
                        f"{self._snapshot_value(before_row.get(field))}{unit} -> "
                        f"{self._snapshot_value(after_row.get(field))}{unit}"
                    )

        if len(lines) == 1:
            lines.append("- изменений не найдено")
        return lines

    def _snapshot_value(self, value):
        if value in (None, ""):
            return "пусто"
        return _display_bot_number(value)

    def _snapshot_row_text(self, row):
        measure_key = item_measure_kind(row)
        prefix = (
            f"{self._snapshot_value(row.get('thickness'))}x"
            f"{self._snapshot_value(row.get('width'))}x"
            f"{self._snapshot_value(row.get('length'))} — "
            f"{self._snapshot_value(row.get('quantity'))} шт"
        )
        if measure_key is None:
            return prefix
        return f"{prefix} — {self._snapshot_value(row.get(measure_key))} {ITEM_MEASURE_UNIT[measure_key]}"

    # --- Кількість і одиниці виміру: штуки чи м3 ---
    def _handle_plain_amount_value(self, store, context, payload, answer, number, request=None):
        row_index = (request or {}).get("row_index")
        if row_index is None:
            row_index = self._income_amount_missing_row_index(payload)
        if row_index is None:
            return None

        payload["original_text"] = "\n".join(
            part for part in [payload.get("original_text", ""), answer] if part
        )
        payload.pop("amount_request", None)
        measure_key = self._row_measure_kind(payload, payload["rows"][row_index])
        if measure_key is None:
            # ОСБ (і будь-який інший товар без фізичного виміру): голе
            # число завжди й одразу кількість, без питання "шт чи м3?" —
            # немає альтернативного виміру, яким воно могло б бути.
            self._apply_plain_amount_unit(payload, {"row_index": row_index, "value": number}, "quantity")
            next_reply = (
                self._continue_sale_operation(store, context, payload)
                if payload.get("operation_kind") == "sale"
                else self._continue_income_operation(store, context, payload)
            )
            return self._prepend_reply_text(f"Принял {_display_bot_number(number)} как шт.", next_reply)
        if not self._is_whole_number(number):
            # Реальний баг з аудиту: дробове число тут завжди йшло в "volume",
            # навіть для площинного товару (Вагонка) — площа мовчки писалась
            # у невірну колонку.
            self._apply_plain_amount_unit(payload, {"row_index": row_index, "value": number}, "measure")
            next_reply = (
                self._continue_sale_operation(store, context, payload)
                if payload.get("operation_kind") == "sale"
                else self._continue_income_operation(store, context, payload)
            )
            unit_label = self._MEASURE_KIND_UNIT[measure_key]
            return self._prepend_reply_text(f"Принял {_display_bot_number(number)} как {unit_label}.", next_reply)

        request = {"row_index": row_index, "value": number}
        payload["amount_unit_request"] = request
        return self._save_income_question(
            store,
            context,
            payload,
            "choose_amount_unit",
            self._amount_unit_prompt(request, measure_key),
        )

    def _income_amount_missing_row_index(self, payload):
        for row_index, row in enumerate(payload.get("rows") or []):
            if not self._income_row_has_amount(row):
                return row_index
        return None

    # measure_kind ("volume"/"area"/"linear") показує "2 — м2"/"2 — мп"
    # замість "2 — м3" — реальний баг з аудиту: раніше завжди показувало
    # м3, навіть для площинного товару (Вагонка), де ця одиниця взагалі не
    # використовується. Вимір рядка ВЖЕ визначений (за товаром чи
    # розміром) — тут нема вибору МІЖ м3/м2/мп, лише "штук чи {той самий
    # єдиний вимір, що підходить цьому рядку}".
    def _amount_unit_prompt(self, request, measure_kind="volume"):
        return (
            f"{_display_bot_number(request.get('value'))} — это что?\n"
            "1 — шт\n"
            f"2 — {self._MEASURE_KIND_UNIT[measure_kind]}"
        )

    def _amount_unit_choice(self, answer):
        normalized = _normalize_phrase(str(answer or "").replace("³", "3").replace("²", "2"))
        if normalized in {"1", "шт", "штук", "sht", "stuk", "pieces", "pcs"}:
            return "quantity"
        if normalized in {
            "2",
            "м3", "m3", "куб", "кубы", "кубов", "kub", "kuby",
            "м2", "m2", "кв", "квм", "квадрат", "kv", "kvm",
            "мп", "м п", "погм", "пог м", "погонных", "погонный", "погонные",
        }:
            return "measure"
        return None

    # unit="measure" пише в area/linear/volume залежно від рядка
    # (_row_measure_kind) — раніше тут було жорстко "volume", тож для
    # Вагонки площа мовчки писалась у невірну колонку (реальний баг з
    # аудиту). Скидає ОБИДВА інших виміри (не лише один) — тепер їх три,
    # взаємовиключні.
    def _apply_plain_amount_unit(self, payload, request, unit):
        row_index = request.get("row_index")
        if row_index is None or row_index >= len(payload.get("rows", [])):
            return False
        item = payload["rows"][row_index]
        value = _number_value(request.get("value"))
        if unit == "quantity":
            item["quantity"] = int(round(value)) if self._is_whole_number(value) else value
            item["quantity_provided"] = True
            item["volume"] = None
            item["volume_provided"] = False
            item["area"] = None
            item["area_provided"] = False
            item["linear"] = None
            item["linear_provided"] = False
            return True
        if unit == "measure":
            measure_key = self._row_measure_kind(payload, item)
            item[measure_key] = value
            item[f"{measure_key}_provided"] = True
            for other_key in ("volume", "area", "linear"):
                if other_key != measure_key:
                    item[other_key] = None
                    item[f"{other_key}_provided"] = False
            item["quantity"] = None
            item["quantity_provided"] = False
            return True
        return False

    def _payment_method_keyboard(self, store):
        return {
            "keyboard": [[{"text": label}] for _id, label, _kind in store.list_payment_method_options()]
            + [[{"text": "Отмена"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _quantity_typo_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Да"}, {"text": "Нет"}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # "Нет" на _quantity_typo_prompt скидає всю операцію (з розпізнаним
    # одруком рядком продовжувати немає сенсу) і пропонує спробувати ще раз
    # (наново з вибору категорії) або вийти в головне меню через "Отмена"
    # (тут вона теж працює — універсальний синонім, перевіряється ще до
    # диспетчера pending-операцій).
    def _retry_after_cancel_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Попробовать снова"}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _apply_quantity_option_answer(self, payload, options, answer):
        row_index = options.get("row_index")
        if row_index is None or row_index >= len(payload.get("rows", [])):
            return False
        measure_key = options.get("measure_kind", "volume")
        measure_provided_key = f"{measure_key}_provided"
        normalized = _normalize_phrase(answer)
        option_rows = options.get("options", [])
        if normalized.isdigit():
            option_index = int(normalized) - 1
            if 0 <= option_index < len(option_rows):
                item = payload["rows"][row_index]
                option = option_rows[option_index]
                item["quantity"] = int(option["quantity"])
                item[measure_key] = option[measure_key]
                item["quantity_provided"] = False
                item[measure_provided_key] = False
                return True
        return self._apply_item_amount_answer(payload, {"row_index": row_index}, answer)

    def _apply_item_amount_answer(self, payload, request, answer):
        row_index = request.get("row_index")
        if row_index is None or row_index >= len(payload.get("rows", [])):
            return False
        item = payload["rows"][row_index]
        measure_key = self._row_measure_kind(payload, item)
        if measure_key is None:
            # Немає фізичного виміру, яким могла б виявитись відповідь —
            # будь-яке розпізнане число це кількість (шт).
            quantity = self._parse_income_quantity(answer)
            if quantity is None:
                number = self._parse_number_with_thousands_separator(answer)
                quantity = number if number > 0 else None
            if quantity is None:
                return False
            item["quantity"] = quantity
            item["quantity_provided"] = True
            return True
        measure_provided_key = f"{measure_key}_provided"
        parse_measure_fn = {
            "area": self._parse_income_area,
            "linear": self._parse_income_linear,
        }.get(measure_key, self._parse_income_volume)

        quantity = self._parse_income_quantity(answer)
        measure_value = parse_measure_fn(answer)
        if quantity is None and measure_value is None:
            number = self._parse_number_with_thousands_separator(answer)
            if number > 0:
                quantity = number
        if quantity is None and measure_value is None:
            return False

        if quantity is not None:
            item["quantity"] = quantity
            item["quantity_provided"] = True
            item[measure_key] = None
            item[measure_provided_key] = False
        if measure_value is not None:
            item[measure_key] = measure_value
            item[measure_provided_key] = True
            if quantity is None:
                item["quantity"] = None
                item["quantity_provided"] = False
        return True

    def _is_edit_request(self, text):
        normalized = _normalize_phrase(text)
        return normalized in {
            "редактировать",
            "ред",
            "изменить",
            "измени",
            "исправить",
            "исправь",
            "правка",
            "поправить",
            "поправь",
            "редагувати",
            "змінити",
            "зміни",
            "виправити",
            "виправ",
        }

    # "Продовжити" (Задача користувача: кілька видів пиломатеріалу в одну
    # продажу) — кнопка на кроці підтвердження stock_sale, що додає ще одну
    # товарну позицію замість запису на склад.
    def _is_sale_continue_request(self, text):
        normalized = _normalize_phrase(text)
        return normalized in {
            "продолжить",
            "добавить позицию",
            "добавить",
            "продовжити",
            "додати позицію",
            "додати",
        }

    # Явний синонім "Да" на кроці підтвердження stock_sale — кнопка
    # "Оформить продажу" (Задача користувача: "здійснити реалізацію").
    def _is_sale_finish_request(self, text):
        normalized = _normalize_phrase(text)
        return normalized in {
            "оформить продажу",
            "оформить",
            "осуществить продажу",
            "осуществить реализацию",
            "завершить продажу",
            "завершить реализацию",
            "завершить",
            "здійснити реалізацію",
            "здійснити",
        }

    # Реальний баг зі скріншота користувача: "175x225x6500" з'явилось на
    # складі ТРЬОМА окремими рядками (10/22/33 шт) замість одного
    # просумованого (65 шт). Причина: якщо в ОДИН прихід/продаж потрапляє
    # кілька позицій з ОДНАКОВИМ розміром (кілька партій того самого товару
    # в одному повідомленні/формі), кожна звіряється зі складом окремо
    # (rows нижче — ЗАСТИГЛИЙ знімок складу, зчитаний ОДИН раз на початку
    # _resolve_income_rows) — жодна щойно введена позиція ще не встигає
    # потрапити в цей знімок, тож кожна незалежно вирішує "такого розміру
    # немає" і apply_income_operation створює ОКРЕМИЙ фізичний рядок на
    # кожну. Об'єднуємо позиції з ідентичним розміром в ОДНУ (сумуючи
    # кількість і той вимір, що заповнений) ще ДО пошуку по складу — це
    # закриває обидва випадки, про які просив користувач: розмір, що ВЖЕ Є
    # на складі (об'єднана позиція знайде й оновить один наявний рядок), і
    # розмір, якого ЩЕ НЕМА (створиться рівно один новий рядок із сумарною
    # кількістю, а не кілька однакових).
    def _merge_duplicate_size_rows(self, rows):
        merged = []
        index_by_key = {}
        for item in rows:
            key = (
                _number_value(item.get("thickness")),
                _number_value(item.get("width")),
                _number_value(item.get("length")),
            )
            existing_index = index_by_key.get(key)
            if existing_index is None:
                index_by_key[key] = len(merged)
                merged.append(dict(item))
                continue
            target = merged[existing_index]
            target["quantity"] = _number_value(target.get("quantity")) + _number_value(item.get("quantity"))
            for measure_key in ("volume", "area", "linear"):
                if item.get(measure_key) is not None:
                    target[measure_key] = _number_value(target.get(measure_key)) + _number_value(item.get(measure_key))
            for provided_key in ("quantity_provided", "volume_provided", "area_provided", "linear_provided"):
                if item.get(provided_key):
                    target[provided_key] = True
            if target.get("quantity_typo_candidate") is None and item.get("quantity_typo_candidate"):
                target["quantity_typo_candidate"] = item["quantity_typo_candidate"]
        return merged

    # --- Пошук рядків складу під позиції операції, превʼю приходу/продажу ---
    def _resolve_income_rows(self, store, payload):
        payload["rows"] = self._merge_duplicate_size_rows(payload["rows"])
        headers, columns, rows = warehouse_rows(store)
        for item in payload["rows"]:
            item["row_id"] = None
            item["create_new"] = False

        for row_index, item in enumerate(payload["rows"]):
            matches = []
            for row_id, row in rows:
                if not self._warehouse_row_matches(row, columns, payload, item):
                    continue
                matches.append((row_id, row))

            if len(matches) == 1:
                item["row_id"] = matches[0][0]
                continue

            if len(matches) > 1:
                if not payload.get("product"):
                    size = income_item_size(item)
                    return (
                        f"Найдено несколько позиций для {size} / {payload['breed']}.\n"
                        "Укажите Продукт для этой операции."
                    )
                products = ", ".join(
                    sorted({str(row[columns["product"]]) for _, row in matches if columns["product"] < len(row)})
                )
                return f"Позиция найдена неоднозначно. Возможные продукты: {products}."

            if not payload.get("product"):
                size = income_item_size(item)
                return (
                    f"Позиция {size} / {payload['breed']} не найдена.\n"
                    "Укажите Продукт для новой позиции."
                )
            item["create_new"] = True
        return None

    def _resolve_sale_rows(self, store, payload):
        # Той самий фікс, що й _resolve_income_rows вище — продаж не
        # створює нових рядків складу, але кілька позицій з ОДНАКОВИМ
        # розміром у ОДНІЙ продажі й без об'єднання незалежно перевіряли б
        # достатність залишку (_sale_stock_issue, викликається одразу
        # після цього методу) кожна проти ТОГО САМОГО залишку, не
        # враховуючи одночасне списання іншою позицією — ризик хибного
        # "залишку вистачає" при фактичному перевищенні.
        payload["rows"] = self._merge_duplicate_size_rows(payload["rows"])
        headers, columns, rows = warehouse_rows(store)
        for item in payload["rows"]:
            item["row_id"] = None
            item["create_new"] = False

        for index, item in enumerate(payload["rows"]):
            matches = [
                (row_id, row)
                for row_id, row in rows
                if self._warehouse_row_matches(row, columns, payload, item)
            ]
            if len(matches) == 1:
                item["row_id"] = matches[0][0]
                continue
            if len(matches) > 1:
                products = ", ".join(
                    sorted({str(row[columns["product"]]) for _, row in matches if columns["product"] < len(row)})
                )
                return {"message": f"Позиция найдена неоднозначно. Возможные продукты: {products}."}
            return {"item": item, "row_index": index}
        return None

    # row_index — який саме рядок payload["rows"] не знайдено на складі.
    # Потрібен, щоб наступна відповідь користувача (виправлений розмір після
    # "Похожие позиции") ЗАМІНЮВАЛА саме цей рядок (_merge_income_payload),
    # а не додавалась новим — інакше _resolve_sale_rows і надалі застрягає на
    # старому невірному рядку, який у списку стоїть раніше виправленого.
    def _save_sale_not_found_question(self, store, context, payload, item=None, row_index=None):
        if item is None:
            rows = payload.get("rows") or []
            item = rows[0] if rows else {}
            row_index = 0 if rows else None
        payload["sale_not_found_item"] = item
        payload["correcting_row_index"] = row_index
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_sale",
            "confirm_sale_similar_positions",
            payload,
        )
        return {
            "type": "message",
            "text": "\n".join(
                [
                    "Не могу оформить продажу: позиция не найдена на складе.",
                    "",
                    "Позиция:",
                    sale_position_text(payload, item),
                    "",
                    "Проверьте продукт, породу, тип продукта или размер.",
                    "Хотите проверить похожие позиции или просмотреть весь остаток?",
                ]
            ),
            "reply_markup": self._sale_not_found_keyboard(),
        }

    def _handle_sale_not_found_action(self, answer, store, context, payload):
        # Реальний баг, помічений користувачем на скріні: тут не було
        # видимої "Редактировать" — лише Похожие позиции/Весь остаток/
        # Отмена, тож самостійно виправити помилку вручну (без перегляду
        # схожих позицій чи всього складу) було неможливо. correcting_row_
        # index/sale_not_found_item НЕ чіпаємо тут (лишаються в payload) —
        # саме вони змушують наступний введений розмір ЗАМІНИТИ цей
        # відхилений рядок, а не додати новий (_merge_income_payload).
        if self._is_edit_request(answer):
            return self._reopen_sale_collection(store, context, payload)
        normalized = _normalize_phrase(answer)
        if normalized in {"похожие позиции", "похожие", "схожие позиции", "схожие", "да", "yes", "так", "1"}:
            return self._sale_similar_positions_reply(store, context, payload)
        if normalized in {"весь остаток", "остаток", "показать остаток", "просмотреть весь остаток", "все", "2"}:
            # Інший шлях відновлення (перегляд усього складу, а не виправлення
            # конкретного рядка) — correcting_row_index тут не має сенсу
            # і не повинен лишитись висіти до наступного collect_income_missing.
            payload.pop("correcting_row_index", None)
            payload.pop("sale_not_found_item", None)
            payload["stock_filter"] = {}
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "stock_filter_collect",
                payload,
            )
            return self._stock_filter_prompt(payload)
        if normalized in {"отмена", "отменить", "стоп", "скасувати", "відміна"}:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._cancelled_reply("Операция продажи отменена.", store)
        if normalized in {"нет", "no", "net"}:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "collect_income_missing",
                payload,
            )
            return (
                "Хорошо, похожие позиции не показываю.\n"
                "Уточните данные продажи. Операция не отменена.\n"
                "Если хотите отменить полностью, напишите: Отмена."
            )

        return {
            "type": "message",
            "text": "Выберите действие: Похожие позиции, Весь остаток или Отмена.",
            "reply_markup": self._sale_not_found_keyboard(),
        }

    def _sale_similar_positions_reply(self, store, context, payload):
        similar = self._similar_sale_rows(store, payload, payload.get("sale_not_found_item") or {})
        if not similar:
            payload.pop("similar_candidates", None)
            return self._save_income_question(
                store,
                context,
                payload,
                "collect_income_missing",
                "Похожих позиций не нашел.\n"
                "Уточните данные продажи и отправьте недостающую или исправленную информацию.",
            )
        lines = ["Похожие позиции на складе:"]
        for index, candidate in enumerate(similar, start=1):
            lines.append(f"{index}. {candidate['summary']}")
        # Без цього пояснення користувач не знав, що можна швидко обрати
        # позицію просто номером рядка, а не передруковувати весь розмір
        # вручну (реальний запит користувача).
        lines.extend(
            [
                "",
                "Уточните данные продажи.",
                f"Чтобы быстро выбрать позицию из списка, напишите просто её номер (от 1 до {len(similar)}).",
                f"Например: 1 — это {income_item_size(similar[0])}.",
            ]
        )
        payload["similar_candidates"] = [
            {
                "product": c["product"],
                "breed": c["breed"],
                "condition": c["condition"],
                "thickness": c["thickness"],
                "width": c["width"],
                "length": c["length"],
            }
            for c in similar
        ]
        return self._save_income_question(store, context, payload, "collect_income_missing", "\n".join(lines))

    # "1"/"2".. після "Похожие позиции" — швидкий вибір за номером рядка
    # замість передруку всього розміру. Повертає ПОВНИЙ кандидат (товар,
    # порода, тип, розмір) — не лише розмір: список показує рядки, у яких
    # порода/тип МОЖУТЬ відрізнятись від того, що вже є в payload (score
    # враховує збіг, але не вимагає його), тож вибір за номером мусить
    # застосувати ВЕСЬ вибраний рядок, а не тільки розмір — інакше пошук
    # продовжує йти зі старою породою і "не знаходить" те, що явно видно в
    # списку (реальний баг: "Ель" з payload лишалась, хоча обрано рядок
    # породи "Сосна"). None — якщо зараз список не показувався, або
    # відповідь не є номером зі списку (тоді відповідь лишається як є).
    def _resolve_similar_candidate_answer(self, payload, answer):
        candidates = payload.get("similar_candidates")
        if not candidates:
            return None
        if not re.fullmatch(r"\d+", answer.strip()):
            return None
        index = int(answer.strip()) - 1
        if not (0 <= index < len(candidates)):
            return None
        return candidates[index]

    def _similar_sale_rows(self, store, payload, item):
        headers, columns, rows = warehouse_rows(store)
        # Задача продуктивності (аудит коду, 2026-08-16): condition_values
        # не залежить від конкретного рядка - рахуємо один раз ДО цикла,
        # а не на кожній ітерації (було O(rows^2) на весь список складу).
        condition_values = self._existing_product_type_values(rows, columns["product"])
        scored = []
        for row_id, row in rows:
            if not self._warehouse_row_has_balance(row, columns):
                continue
            score = 0
            row_product, product_suffix_type = self._split_product_condition(
                row_value(row, columns["product"]),
                condition_values,
            )
            # Реальний баг (2026-08-17, живий продакшн, знайдено паралельно з
            # _warehouse_row_matches): тут раніше бралось ЛИШЕ product_suffix_
            # type (суфікс з тексту "Продукт") - для рядків НОВОГО формату
            # (Состояние - окрема колонка, без суфіксу в "Продукт") це завжди
            # порожній рядок, а не None. Обраний за номером кандидат тоді
            # записував payload["condition"] = "" (не None) - "" не є None,
            # тож щойно виправлений guard у _warehouse_row_matches (пропуск
            # перевірки, коли condition None) НЕ спрацьовував, і повторний
            # запис тієї самої, щойно вибраної користувачем позиції знову
            # падав з "Не найдено на складе". Той самий фолбек, що вже має
            # _warehouse_row_matches/_existing_dimension_values/_existing_
            # dimension_combos - реальна колонка "Состояние", а не лише
            # текстовий суфікс - і None (не ""), коли й вона порожня.
            row_condition = row_value(row, columns.get("condition")) or product_suffix_type or None
            if payload.get("product") and self._text_equal(row_product, payload.get("product")):
                score += 3
            if payload.get("breed") and self._text_equal(row_value(row, columns["breed"]), payload.get("breed")):
                score += 2
            if payload.get("condition") and self._text_equal(row_condition, payload.get("condition")):
                score += 2
            for field in ("thickness", "width", "length"):
                if self._number_equal(row_value(row, columns[field]), item.get(field)):
                    score += 1
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    {
                        "product": row_product,
                        "breed": row_value(row, columns["breed"]),
                        "condition": row_condition,
                        "thickness": row_value(row, columns["thickness"]),
                        "width": row_value(row, columns["width"]),
                        "length": row_value(row, columns["length"]),
                        "summary": self._warehouse_row_summary(row, columns),
                    },
                )
            )
        scored.sort(key=lambda row: row[0], reverse=True)
        return [candidate for _, candidate in scored[:5]]

    # Реальний баг (2026-08-14): "чому не просумовані і Тип пустий?" -
    # порода/розмір збігались, а рядки все одно не зливались в один, бо тут
    # "Тип" визначався розбором ТЕКСТУ "Продукт" (напр. "Доска AD" ->
    # "Доска"+"AD") - хоча за задачею користувача "ніяких КД АД в продукті,
    # лише в состоянии" (див. коментар над display_product_name,
    # warehouse_data.py) продукт уже давно пишеться БЕЗ суфікса стану. Тож
    # row_type тут завжди виходив порожнім для будь-якого нового рядка, і
    # збіг спрацьовував лише коли payload.get("condition") теж порожній -
    # кожен прихід з непорожнім "Тип" створював НОВИЙ рядок замість
    # додавання до вже існуючого. Тепер читаємо реальну колонку
    # "Состояние" (columns["condition"]) напряму - text-split лишається
    # лише як фолбек для СТАРИХ рядків, які ще можуть мати суфікс у тексті.
    def _warehouse_row_matches(self, row, columns, payload, item):
        if not self._text_equal(row_value(row, columns["breed"]), payload["breed"]):
            return False
        row_product, product_suffix_type = self._split_product_condition(
            row_value(row, columns["product"]),
            self._existing_product_type_values([(None, row)], columns["product"]),
        )
        if not self._text_equal(row_product, payload.get("product")):
            return False
        # Реальний баг (живий продакшн, 2026-08-17): "Не найдено на складе"
        # для Вагонки/ОСБ навіть на щойно відкритій формі - _existing_
        # dimension_combos/_existing_dimension_values (telegram_dialog_core.
        # py) уже давно пропускають цю перевірку, коли payload["condition"]
        # is None (категорія без виміру типу - ОСБ/Вагонка), саме тому
        # розмір показувався у формі; ТУТ, на етапі запису, тієї самої
        # умови не було - будь-яке непорожнє "Состояние" в рядку складу
        # (сміттєве значення/суфікс старого формату) відхиляло коректний
        # розмір. Той самий принцип, що вже описаний у коментарі над
        # _existing_dimension_values.
        if payload.get("condition") is not None:
            row_condition = row_value(row, columns.get("condition")) or product_suffix_type
            if not self._text_equal(row_condition, payload.get("condition")):
                return False
        return (
            self._number_equal(row_value(row, columns["thickness"]), item["thickness"])
            and self._number_equal(row_value(row, columns["width"]), item["width"])
            and self._number_equal(row_value(row, columns["length"]), item["length"])
        )

    # Задача користувача (2026-08-14): "щоб міг продовжувати приход і
    # внести кілька різних позицій" - раніше цей попередній перегляд
    # показував ЛИШЕ поточну/останню позицію, повністю ігноруючи вже
    # накопичені payload["completed_positions"] (на відміну від _sale_
    # preview, яка їх показує) - людина не бачила, що взагалі-то збирається
    # відправити кілька товарів одразу, лише останній. Кожна вже завершена
    # позиція тепер теж показується, своїм блоком "Позиция: Товар / Порода".
    def _income_preview(self, payload):
        lines = []
        edit_lines = self._edit_change_lines(payload)
        if edit_lines:
            lines.extend(edit_lines)
            lines.append("")
        lines.append("Я распознал приход:")
        completed_positions = payload.get("completed_positions") or []
        multi_position = bool(completed_positions)
        index = 0
        lines.append("")
        for position in completed_positions:
            lines.append(f"Позиция: {display_product_name(position)} / {position.get('breed')}")
            for item in position.get("rows") or []:
                index += 1
                marker = " новая позиция" if item.get("create_new") else ""
                measure_key = self._row_measure_kind(position, item)
                if measure_key is None:
                    lines.append(
                        f"  {index}. {income_item_size(item)} — "
                        f"{_display_bot_number(item['quantity'])} шт{marker}"
                    )
                    continue
                measure_value = item.get(measure_key)
                measure_unit = self._MEASURE_KIND_UNIT[measure_key]
                lines.append(
                    f"  {index}. {income_item_size(item)} — "
                    f"{_display_bot_number(item['quantity'])} шт — "
                    f"{_display_bot_number(measure_value)} {measure_unit}{marker}"
                )
            lines.append("")
        if multi_position:
            lines.append(f"Позиция: {display_product_name(payload)} / {payload.get('breed')}")
        else:
            lines.extend([
                f"Продукт: {display_product_name(payload)}",
                f"Порода: {payload['breed']}",
            ])
        lines.append("")
        for item in payload["rows"]:
            index += 1
            marker = " новая позиция" if item.get("create_new") else ""
            measure_key = self._row_measure_kind(payload, item)
            row_prefix = "  " if multi_position else ""
            if measure_key is None:
                lines.append(
                    f"{row_prefix}{index}. {income_item_size(item)} — "
                    f"{_display_bot_number(item['quantity'])} шт{marker}"
                )
                continue
            measure_value = item.get(measure_key)
            measure_unit = self._MEASURE_KIND_UNIT[measure_key]
            lines.append(
                f"{row_prefix}{index}. {income_item_size(item)} — "
                f"{_display_bot_number(item['quantity'])} шт — "
                f"{_display_bot_number(measure_value)} {measure_unit}{marker}"
            )
        if payload.get("_from_webapp_form"):
            lines.extend(["", "Подтвердить запись?", "Да / Нет"])
        else:
            lines.extend(["", "Подтвердить запись?", "Да / Нет / Редактировать"])
        return "\n".join(lines)

    # Один по-товарний блок продажі - ОДНАКОВИЙ і для вже завершеної позиції
    # (completed_positions), і для поточної/останньої (payload). Раніше
    # поточна позиція рендерилась геть інакше - розкидані окремі рядки
    # "Продукт:"/"Порода:", нумерований список розмірів БЕЗ відступу, потім
    # окремо "Цена:"/"Сумма:", потім ще окремий, ширший блок антисептирования
    # - на скріні 2-позиційної продажі це виглядало як "хаос, ніяких
    # розділень логічних" (пряма скарга користувача). Тепер обидва типи
    # позицій дають ту саму нумеровану шапку "N. Товар / Порода" з
    # відступленими рядками розмірів і компактним "Антисептировано: X м3 —
    # Y MDL" - як уже й було в completed_positions.
    # _sale_total_amount читає payload.get("total_amount") ПЕРШИМ (готове
    # число) і рахує через price_per_unit лише як фолбек - тому працює
    # однаково і для completed-позиції (де total_amount уже прораховане й
    # збережене при архівації), і для живого payload (де total_amount ще
    # не існує).
    def _sale_position_lines(self, index, position):
        lines = [f"{index}. {display_product_name(position)} / {position.get('breed')}"]
        for item in position.get("rows") or []:
            measure_key = self._row_measure_kind(position, item)
            if measure_key is None:
                lines.append(f"   {income_item_size(item)} — {_display_bot_number(item['quantity'])} шт")
                continue
            measure_value = item.get(measure_key)
            measure_unit = self._MEASURE_KIND_UNIT[measure_key]
            lines.append(
                f"   {income_item_size(item)} — "
                f"{_display_bot_number(item['quantity'])} шт — "
                f"{_display_bot_number(measure_value)} {measure_unit}"
            )
        position_total = self._sale_total_amount(position)
        if position_total:
            lines.append(f"   Сумма: {_display_bot_number(position_total)} MDL")
        position_antiseptic_sum = 0
        position_antiseptic = position.get("antiseptic")
        if isinstance(position_antiseptic, dict) and position_antiseptic.get("volume") and position_antiseptic.get("price_per_unit"):
            position_antiseptic_volume = _number_value(position_antiseptic.get("volume"))
            position_antiseptic_price = _number_value(position_antiseptic.get("price_per_unit"))
            position_antiseptic_sum = round(position_antiseptic_volume * position_antiseptic_price, 2)
            lines.append(
                f"   Антисептировано: {_display_bot_number(position_antiseptic_volume)} м3 — "
                f"{_display_bot_number(position_antiseptic_sum)} MDL"
            )
        return lines, _number_value(position_total), position_antiseptic_sum

    def _sale_preview(self, payload):
        # Задача користувача: "'Я распознал продажу:' - десь загубилось
        # посередині, вверх перенеси" - цей рядок тепер ЗАГОЛОВОК усього
        # повідомлення (перед уже доданими позиціями, а не між ними й
        # поточною) - так само, як і сам заголовок екрана підтвердження у
        # webapp-формі. "Уже добавлено в эту продажу:" прибрано за прямим
        # проханням - самого нумерованого списку позицій достатньо.
        sections = []
        edit_lines = self._edit_change_lines(payload)
        if edit_lines:
            sections.append(edit_lines)
        sections.append(["Я распознал продажу:"])

        completed_positions = payload.get("completed_positions") or []
        total_goods_sum = 0
        total_antiseptic_sum = 0
        index = 0
        # Задача користувача: "чому розпізнало лише 1 антисептирование,
        # якщо я 2 антисептіровав?" - кожна вже додана позиція може мати
        # ВЛАСНИЙ antiseptic (тепер частина _SALE_POSITION_FIELDS) - сума
        # рахується по КОЖНІЙ, а не лише по поточній/останній.
        for position in completed_positions:
            index += 1
            position_lines, position_total, position_antiseptic_sum = self._sale_position_lines(index, position)
            sections.append(position_lines)
            total_goods_sum += position_total
            total_antiseptic_sum += position_antiseptic_sum

        index += 1
        position_lines, position_total, position_antiseptic_sum = self._sale_position_lines(index, payload)
        sections.append(position_lines)
        total_goods_sum += position_total
        total_antiseptic_sum += position_antiseptic_sum

        # Задача користувача: "зроби все симетрично" - спільні на ВСЮ
        # продажу поля (клієнт/адреса/оплата) тепер ОДИН блок ПІСЛЯ усіх
        # позицій, а не розкидані між ними чи прив'язані лише до поточної.
        # Крок 3+ "Дії": жодне з них не гарантовано зібране (адмін міг
        # видалити відповідне поле-запит) - рядок показуємо лише коли є
        # реальне значення, а не буквальний "Клиент: None".
        common_lines = []
        if payload.get("client"):
            common_lines.append(f"Клиент: {payload['client']}")
        if payload.get("address"):
            common_lines.append(f"Адрес выгрузки: {payload['address']}")
        if payload.get("payment_method"):
            common_lines.append(f"Оплата: {payload['payment_method']}")
        if common_lines:
            sections.append(common_lines)
        # Задача користувача (скріншот): "перед останнє Итого зроби пробіл" -
        # окрема секція (не хвіст common_lines) - "\n\n".join(sections)
        # нижче дає порожній рядок-відступ саме перед ним. Задача
        # користувача: "хочу бачити загалом за антисепт і загалом за товар,
        # а вже в кінці итог" - ті самі total_goods_sum/total_antiseptic_sum,
        # що вже рахувались вище для кожної позиції, тепер показані окремими
        # рядками в ЦІЙ ЖЕ секції (не окремий пробіл між ними й Итого).
        # "Сумма за Антисептирование" лише коли вона реально є десь у
        # продажу - інакше "0 MDL" був би шумом для звичайної продажі без
        # антисептирования.
        if index > 1 or total_antiseptic_sum:
            grand_total = round(total_goods_sum + total_antiseptic_sum, 2)
            totals_lines = []
            if total_antiseptic_sum:
                totals_lines.append(f"Сумма за Антисептирование: {_display_bot_number(round(total_antiseptic_sum, 2))} MDL")
            totals_lines.append(f"Сумма за товар: {_display_bot_number(round(total_goods_sum, 2))} MDL")
            totals_lines.append(f"Итого: {_display_bot_number(grand_total)} MDL")
            sections.append(totals_lines)

        if payload.get("_from_webapp_form"):
            sections.append(["Будет списано с остатка.", "Подтвердить запись?", "Оформить продажу / Отмена"])
        else:
            sections.append([
                "Будет списано с остатка.",
                "Подтвердить запись?",
                "Оформить продажу / Продолжить / Редактировать / Отмена",
            ])
        return "\n\n".join("\n".join(section) for section in sections)

    # Повертає СТРУКТУРОВАНІ дані про нестачу (не готовий текст) — виклик
    # (_continue_sale_operation_impl) передає їх у _sale_stock_issue_reply,
    # яка будує повідомлення з кнопкою "Использовать N шт/м3/м2" і зберігає
    # interactive pending-статус, замість того, щоб повертати голий рядок
    # без reply_markup (був "мертвий кінець" — лишалась стара клавіатура,
    # єдиний вихід — Отмена, без способу скоригувати кількість кнопкою).
    _MEASURE_KIND_UNIT = {"volume": "м3", "area": "м2", "linear": "мп"}
    _MEASURE_KIND_BALANCE_COLUMN = {
        "volume": "balance_volume",
        "area": "balance_area",
        "linear": "balance_linear",
    }

    def _sale_stock_issue(self, store, payload):
        headers, columns, _ = warehouse_rows(store)
        for row_index, item in enumerate(payload["rows"]):
            measure_key = self._row_measure_kind(payload, item)
            row_values = store.get_row(item.get("row_id"))
            balance_qty = _number_value(row_value(row_values, columns["balance_qty"]))
            if measure_key is None:
                # ОСБ (і будь-який інший товар без фізичного виміру): нема
                # чого звіряти, крім кількості — balance_measure/measure_unit
                # лишаються None, _sale_stock_issue_reply вже коректно
                # пропускає їх для kind="quantity".
                measure_unit = None
                balance_measure = None
            else:
                measure_unit = self._MEASURE_KIND_UNIT[measure_key]
                measure_column = self._MEASURE_KIND_BALANCE_COLUMN[measure_key]
                balance_measure = _number_value(row_value(row_values, columns.get(measure_column)))
            if _number_value(item.get("quantity")) > balance_qty + INCOME_QUANTITY_TOLERANCE:
                return {
                    "kind": "quantity",
                    "row_index": row_index,
                    "requested": _number_value(item.get("quantity")),
                    "requested_unit": "шт",
                    "balance_qty": balance_qty,
                    "balance_measure": balance_measure,
                    "measure_unit": measure_unit,
                }
            if measure_key is not None and _number_value(item.get(measure_key)) > balance_measure + INCOME_VOLUME_TOLERANCE:
                return {
                    "kind": measure_key,
                    "row_index": row_index,
                    "requested": _number_value(item.get(measure_key)),
                    "requested_unit": measure_unit,
                    "balance_qty": balance_qty,
                    "balance_measure": balance_measure,
                    "measure_unit": measure_unit,
                }
        return None

    _STOCK_ISSUE_KIND_LABELS = {
        "quantity": "штук",
        "volume": "объема",
        "area": "площади",
        "linear": "погонных метров",
    }

    # Задача користувача: "вказувати що максимальна кількість недопустима,
    # ви вводите стільки, а є тільки" - винесено з _sale_stock_issue_reply,
    # щоб те саме "Запрошено X / Доступно Y" повідомлення могла показати і
    # термінальна (форм-режим) гілка нижче, без інтерактивної клавіатури.
    def _sale_stock_issue_text(self, payload, issue):
        rows = payload.get("rows") or []
        row_index = issue["row_index"]
        item = rows[row_index] if 0 <= row_index < len(rows) else {}
        lines = [
            f"Не могу оформить продажу: на складе недостаточно {self._STOCK_ISSUE_KIND_LABELS[issue['kind']]}.",
            "",
            f"Позиция: {sale_position_text(payload, item)}",
            f"Запрошено: {_display_bot_number(issue['requested'])} {issue['requested_unit']}",
        ]
        available_parts = [f"{_display_bot_number(issue['balance_qty'])} шт"]
        if issue["kind"] != "quantity" or issue.get("balance_measure") not in (None, 0):
            available_parts.append(f"{_display_bot_number(issue['balance_measure'])} {issue['measure_unit']}")
        lines.append(f"Доступно: {' / '.join(available_parts)}")
        return "\n".join(lines)

    def _sale_stock_issue_reply(self, store, context, payload, issue):
        text = self._sale_stock_issue_text(payload, issue) + (
            "\n\nИспользовать доступное количество (кнопка ниже) или напишите другое количество."
        )
        payload["stock_issue"] = issue
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "stock_sale", "confirm_insufficient_stock", payload
        )
        return {
            "type": "message",
            "text": text,
            "reply_markup": self._insufficient_stock_keyboard(issue),
        }

    def _insufficient_stock_keyboard(self, issue):
        if issue["kind"] == "quantity":
            label = f"Использовать {_display_bot_number(issue['balance_qty'])} шт"
        else:
            label = f"Использовать {_display_bot_number(issue['balance_measure'])} {issue['measure_unit']}"
        return {
            "keyboard": [
                [{"text": label}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
