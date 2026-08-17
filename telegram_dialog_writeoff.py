"""Потік "Списание" (списання товару зі складу) - новий, 4-й вид операції
config-driвen системи "Дії" (income/sale/service/writeoff). Мірне повторення
telegram_dialog_antiseptic.py: власний, повністю ізольований ланцюжок
статусів (не розширення _handle_pending_operation/_continue_income_operation_impl),
бо це новий, окремий вид операції, а не варіація приходу/продажу.

Структурно ближче до ПРИХОДУ, ніж до антисептирования (та сама ідентифікація
рядка складу - порода/товщина/ширина/довжина - і той самий рушій розпізнавання
розміру/кількості), лише БЕЗ клієнта/ціни/оплати, БЕЗ гілки "рядок не знайдено
-> створити новий" (не можна списати те, чого ніколи не було на складі) і БЕЗ
"схожих позицій"-підказок (свідомо спрощено для v1). Причина списання -
необов'язкове вільне поле (той самий alias-механізм ключа "comment", що вже
є в _SALE_FIELD_ALIASES/_extract_sale_fields), не окремий обов'язковий крок.

Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти.
"""

import permissions as perm
from utils import _display_bot_number, _normalize_phrase
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
    apply_writeoff_operation,
    display_product_name,
    income_item_size,
    resolve_operation_for_payload,
    sale_position_text,
)


class WriteoffDialogMixin:

    def _start_writeoff_operation(self, store, context):
        denied = self._require_permission(store, context, perm.WRITEOFF)
        if denied:
            return denied
        store.save_pending_operation(context["chat_id"], context["user_id"], "stock_writeoff", "choose_category", {})
        return {
            "type": "message",
            "text": store.get_message_template("start_writeoff", BOT_MESSAGE_DEFAULTS["start_writeoff"]),
            "reply_markup": self._category_keyboard(store, "start_writeoff"),
        }

    # "СПИСАНИЕ (форма)" - fallback-вхід (текстова кнопка з web_app-посиланням
    # усередині повідомлення), той самий патерн, що й _start_sale_all_in_one_reply:
    # використовується лише коли тунель ще не піднявся на момент рендеру
    # головного меню (_button_dict_for_node не зміг вбудувати посилання
    # прямо в кнопку) - тап цього тимчасового повідомлення знову перевіряє
    # тунель і, якщо він уже готовий, показує ту саму форму.
    def _start_writeoff_all_in_one_reply(self, store, context, resume_payload=None):
        denied = self._require_permission(store, context, perm.WRITEOFF)
        if denied:
            return denied
        web_app = self._writeoff_all_in_one_webapp_button(store, resume_payload=resume_payload)
        if web_app is None:
            return self._with_main_menu(
                "Списание одной формой сейчас недоступно (форма не подключена). "
                "Используйте обычное «СПИСАНИЕ».",
                store,
            )
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "stock_writeoff", "writeoff_all_in_one", {},
        )
        keyboard = {
            "keyboard": [
                [{"text": "Заполнить форму списания", **web_app}],
                [{"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
        }
        return {
            "type": "message",
            "text": store.get_message_template("start_writeoff_form", BOT_MESSAGE_DEFAULTS["start_writeoff_form"]),
            "reply_markup": keyboard,
        }

    def _save_writeoff_question(self, store, context, payload, status, text):
        store.save_pending_operation(context["chat_id"], context["user_id"], "stock_writeoff", status, payload)
        if isinstance(text, str):
            keyboard = self._cancel_and_edit_keyboard()
            if status == "collect_writeoff_missing":
                operation_id = resolve_operation_for_payload(store, "start_writeoff", "writeoff", payload)
                # Задача користувача: "списание має мати справу ТІЛЬКИ з
                # існуючими цифрами" - той самий default restrict_to_
                # existing_combos=True, що вже й так у _webapp_keyboard.
                keyboard = self._webapp_keyboard(
                    store, operation_id, self._WEBAPP_IDENTITY_DIMENSION_KEYS, payload, "Списание", keyboard
                )
            return {"type": "message", "text": text, "reply_markup": keyboard}
        return text

    # "Редактировать"/чек-лист — той самий _recognized_data_lines/
    # _income_missing_fields, що й прихід, лише без store/kind (kind
    # "writeoff" не входить у резолв "income"/"sale"/"service" всередині
    # _recognized_data_lines/_operation_header_text — обидві функції
    # безпечно деградують до типових міток замість адмін-перейменованих;
    # немає окремого вільного заголовка для списання в v1).
    def _writeoff_missing_prompt(self, missing_fields, payload):
        lines = []
        recognized = self._recognized_data_lines(payload)
        if recognized:
            lines.extend(recognized)
            lines.append("")
        if missing_fields:
            lines.append("Не хватает данных для списания:")
            lines.extend(f"- {field}" for field in missing_fields)
        return "\n".join(lines)

    def _reopen_writeoff_collection(self, store, context, payload):
        missing_fields = self._income_missing_fields(store, payload, kind="writeoff")
        prompt = (
            "Напишите новое значение — я заменю только то, что вы укажете.\n\n"
            + self._writeoff_missing_prompt(missing_fields, payload)
        )
        return self._save_writeoff_question(store, context, payload, "collect_writeoff_missing", prompt)

    # Задача користувача: "якщо на складі недостатньо кількості для операції,
    # то сама програма не має випустити із неіснуючим залишком" - списання
    # раніше не мало ЖОДНОЇ перевірки залишку до фінального Так/Ні (лише
    # backstop УСЕРЕДИНІ apply_writeoff_operation, вже ПІСЛЯ підтвердження).
    # _sale_stock_issue (income_sale_flow.py) повністю kind-агностична -
    # звіряє payload["rows"] проти реального balance_qty/balance_* незалежно
    # від того, продаж це чи списання - переиспользуємо як є, лише текст
    # повідомлення тут свій ("списать", не "оформить продажу").
    def _writeoff_stock_issue_text(self, payload, issue):
        rows = payload.get("rows") or []
        row_index = issue["row_index"]
        item = rows[row_index] if 0 <= row_index < len(rows) else {}
        lines = [
            f"Не могу списать: на складе недостаточно {self._STOCK_ISSUE_KIND_LABELS[issue['kind']]}.",
            "",
            f"Позиция: {sale_position_text(payload, item)}",
            f"Запрошено: {_display_bot_number(issue['requested'])} {issue['requested_unit']}",
        ]
        available_parts = [f"{_display_bot_number(issue['balance_qty'])} шт"]
        if issue["kind"] != "quantity" or issue.get("balance_measure") not in (None, 0):
            available_parts.append(f"{_display_bot_number(issue['balance_measure'])} {issue['measure_unit']}")
        lines.append(f"Доступно: {' / '.join(available_parts)}")
        return "\n".join(lines)

    def _writeoff_stock_issue_reply(self, store, context, payload, issue):
        text = self._writeoff_stock_issue_text(payload, issue) + (
            "\n\nИспользовать доступное количество (кнопка ниже) или напишите другое количество."
        )
        payload["stock_issue"] = issue
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "stock_writeoff", "confirm_writeoff_insufficient_stock", payload
        )
        return {
            "type": "message",
            "text": text,
            "reply_markup": self._insufficient_stock_keyboard(issue),
        }

    # Мірне повторення _income_preview (кожен рядок за своїм виміром) -
    # _recognized_data_lines НЕ підходить тут: вона будує ПРОМІЖНИЙ
    # чек-лист-текст (без кількості/виміру за рядком), не фінальне прев'ю.
    def _writeoff_preview(self, payload):
        lines = [
            "Списание:",
            f"Товар: {display_product_name(payload)}",
            f"Порода: {payload.get('breed')}",
            "",
        ]
        for index, item in enumerate(payload["rows"], start=1):
            measure_key = self._row_measure_kind(payload, item)
            if measure_key is None:
                lines.append(f"{index}. {income_item_size(item)} — {_display_bot_number(item['quantity'])} шт")
                continue
            measure_value = item.get(measure_key)
            measure_unit = self._MEASURE_KIND_UNIT[measure_key]
            lines.append(
                f"{index}. {income_item_size(item)} — "
                f"{_display_bot_number(item['quantity'])} шт — "
                f"{_display_bot_number(measure_value)} {measure_unit}"
            )
        if payload.get("comment"):
            lines.append("")
            lines.append(f"Причина: {payload['comment']}")
        lines.append("")
        lines.append("Списать со склада?")
        return "\n".join(lines)

    # Той самий info_notes-обгортка, що й _continue_income_operation/
    # _continue_sale_operation (не рекурсивна - _impl нижче ніколи не
    # викликає сама себе, лише повертає рано через _save_writeoff_question/
    # _confirmation_reply, тож pop/re-apply тут безпечний).
    def _continue_writeoff_operation_impl(self, store, context, payload):
        notes = payload.pop("info_notes", None)
        reply = self._continue_writeoff_operation_step(store, context, payload)
        return self._apply_info_notes({"info_notes": notes}, reply)

    def _continue_writeoff_operation_step(self, store, context, payload):
        self._apply_income_free_values(store, payload)
        # income_free_candidate — механізм приходу для СЛІВ, що не збіглись
        # ЖОДНИМ відомим товаром/породою/типом складу (звичайно веде до
        # питання "що це?", choose_free_value_role - навмисно не реюзаного
        # тут, бо для списання нема сенсу "додати нову породу": шукаємо
        # ЩОСЬ конкретне на складі, і якщо не знайдено - _resolve_sale_rows
        # нижче й так коректно скаже "не найдено". Тому нерозпізнане слово
        # одразу йде в породу напряму, без проміжного уточнення.
        if payload.get("income_free_candidate") and not payload.get("breed"):
            payload["breed"] = payload.pop("income_free_candidate")
        self._canonicalize_income_values(store, payload)

        # Задача користувача: "сам бот в режим (форма) не має нічого в чаті
        # дозапитувати" - webapp-подання вже пройшло клієнтську валідацію
        # required-полів; якщо чек-лист УСЕ ОДНО щось знайшов, це термінальна
        # відмова (форма заново), а не інтерактивний чек-лист.
        missing_fields = self._income_missing_fields(store, payload, kind="writeoff")
        if missing_fields:
            if payload.get("_from_webapp_form"):
                return self._webapp_form_terminal_reply(
                    store, context, self._writeoff_missing_prompt(missing_fields, payload)
                )
            return self._save_writeoff_question(
                store,
                context,
                payload,
                "collect_writeoff_missing",
                self._writeoff_missing_prompt(missing_fields, payload),
            )

        amount_issue = self._prepare_income_amounts(payload)
        if amount_issue:
            if payload.get("_from_webapp_form"):
                message = amount_issue["message"]
                text = message if isinstance(message, str) else message.get(
                    "text", "Не удалось рассчитать количество. Начните списание заново через форму."
                )
                return self._webapp_form_terminal_reply(store, context, text)
            payload[amount_issue["payload_key"]] = amount_issue["payload"]
            return self._save_writeoff_question(
                store, context, payload, amount_issue["status"], amount_issue["message"]
            )

        # Реюз повністю узагальненого _resolve_sale_rows (жодної sale-
        # специфічної логіки всередині) — на відміну від _resolve_income_rows,
        # тут НЕМАЄ гілки "не знайдено -> створити новий рядок": не можна
        # списати позицію, якої ніколи не було на складі.
        match_issue = self._resolve_sale_rows(store, payload)
        if match_issue:
            message = match_issue.get("message")
            if not message:
                item = match_issue["item"]
                message = (
                    f"Не найдено на складе: {sale_position_text(payload, item)}.\n"
                    "Проверьте продукт, породу, тип продукта или размер."
                )
            # Той самий, вже виправлений принцип, що й у продажу: розмір,
            # якого нема на складі, можна виправити ТІЄЮ САМОЮ формою (row
            # replace через _merge_webapp_submission) без втрати породи -
            # тому pending лишається живим, а не термінально видаляється.
            if payload.get("_from_webapp_form"):
                message = message + "\n\nИсправьте размер в той же форме и отправьте её заново."
            return self._save_writeoff_question(store, context, payload, "collect_writeoff_missing", message)

        # Задача користувача: "якщо на складі недостатньо кількості для
        # операції - не дозволяти відправити" - той самий kind-агностичний
        # _sale_stock_issue, реюзаний тут для списання.
        stock_issue = self._sale_stock_issue(store, payload)
        if stock_issue:
            if payload.get("_from_webapp_form"):
                text = self._writeoff_stock_issue_text(payload, stock_issue)
                text += "\n\nУменьшите количество в форме и отправьте её заново."
                return self._webapp_form_terminal_reply(store, context, text)
            return self._writeoff_stock_issue_reply(store, context, payload, stock_issue)

        store.save_pending_operation(
            context["chat_id"], context["user_id"], "stock_writeoff", "confirm_writeoff_write", payload
        )
        return self._confirmation_reply(
            self._writeoff_preview(payload),
            allow_edit=not payload.get("_from_webapp_form"),
            show_form_return=bool(payload.get("_from_webapp_form")),
        )

    def _continue_writeoff_operation(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()

        if status == "writeoff_all_in_one":
            # Користувач написав текст замість натискання кнопки форми -
            # той самий підхід, що й у sale_all_in_one: показати кнопку ще
            # раз, а не намагатись розібрати вільний текст на порожньому
            # payload.
            return self._start_writeoff_all_in_one_reply(store, context)

        if status == "choose_category":
            category = self._category_from_text(store, "start_writeoff", answer)
            if category is None:
                return {
                    "type": "message",
                    "text": "Не понял категорию. Выберите одну из кнопок ниже.",
                    "reply_markup": self._category_keyboard(store, "start_writeoff"),
                }
            product, condition = category
            new_payload = {
                "operation_kind": "writeoff",
                "original_text": answer,
                "user": {
                    "id": context["user_id"],
                    "username": context["username"],
                    "full_name": context["full_name"],
                },
                "confirmed_new": [],
                "product": product,
            }
            if condition:
                new_payload["condition"] = condition
            return self._continue_writeoff_operation_impl(store, context, new_payload)

        if status == "confirm_writeoff_write":
            from_webapp_form = bool(payload.get("_from_webapp_form"))
            # Задача користувача: "додай ще повернення в форму... знизу
            # третя кнопка" - знову відкриває ТУ САМУ форму замість старого
            # текстового "Редактировать".
            if from_webapp_form and _normalize_phrase(answer) == _normalize_phrase(self._WEBAPP_FORM_RETURN_LABEL):
                return self._reopen_webapp_form_reply(store, context, payload)
            # Той самий принцип, що й у sale/income (_continue_operation
            # income_sale_flow.py): форма-режим не має виходу в старий
            # текстовий "Редактировать" - кнопки нема, і голий текст теж
            # ігнорується, лишаючи лише Да/Нет.
            if not from_webapp_form and self._is_edit_request(answer):
                return self._reopen_writeoff_collection(store, context, payload)
            decision = self._yes_no(answer)
            if decision is None:
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
                # Реальний баг зі скріна: форма → "Нет" завжди повертав у
                # СТАРИЙ вибір категорії (choose_category) - та сама "пастка"
                # двох UI-режимів одразу, що й на успішному записі нижче.
                if from_webapp_form:
                    return self._webapp_form_terminal_reply(store, context, "Списание отменено.")
                return self._start_writeoff_operation(store, context)
            sync_mode = self._excel_sync_mode()
            result = apply_writeoff_operation(store, payload, sync_mode, self)
            if not result.get("ok"):
                # Той самий фікс, що й для продажу: нічого не записано, тож
                # НЕ лишаємо застарілий payload на "confirm_writeoff_write"
                # чекати ще одну відповідь на вже неактуальний екран.
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._with_main_menu(f"⚠️ {result['message']}", store)
            # Задача користувача (2026-08-17): дубль звіту в окрему групу.
            self._notify_report_broadcast(context, result["message"])
            # Реальний баг зі скріна користувача: форма → успіх → бот ЗАВЖДИ
            # повертав у СТАРИЙ покроковий вибір категорії - людина, що
            # більше не торкалась чату вручну, опинялась у чужому UI-режимі
            # без кнопки форми (лише Отмена як вихід). Форма-режим
            # завершується ЛИШЕ інформуванням про результат.
            if from_webapp_form:
                return self._webapp_form_terminal_reply(store, context, result["message"], parse_mode="HTML")
            return self._prepend_reply_text(
                result["message"], self._start_writeoff_operation(store, context), parse_mode="HTML"
            )

        # Мірне повторення "confirm_insufficient_stock" (income_sale_flow.py) -
        # той самий вибір "Использовать N шт/м3" чи вільне число, лише
        # завершує через continue_writeoff_operation_impl, а не generic
        # continue_operation (списання - власний, ізольований ланцюжок).
        if status == "confirm_writeoff_insufficient_stock":
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
                    if issue.get("kind") == "quantity":
                        row["volume"] = None
                        row["volume_provided"] = False
                        row["area"] = None
                        row["area_provided"] = False
                        row["linear"] = None
                        row["linear_provided"] = False
                payload.pop("stock_issue", None)
                return self._continue_writeoff_operation_impl(store, context, payload)

            if self._apply_item_amount_answer(payload, {"row_index": row_index}, answer):
                payload.pop("stock_issue", None)
                return self._continue_writeoff_operation_impl(store, context, payload)

            return self._save_writeoff_question(
                store,
                context,
                payload,
                "confirm_writeoff_insufficient_stock",
                {
                    "type": "message",
                    "text": "Не понял количество. Нажмите кнопку ниже или напишите число (например: 40 шт).",
                    "reply_markup": self._insufficient_stock_keyboard(issue),
                },
            )

        if status == "choose_writeoff_amount_unit":
            unit = self._amount_unit_choice(answer)
            if unit is None:
                request = payload.get("amount_unit_request", {})
                row_index = request.get("row_index")
                rows = payload.get("rows") or []
                measure_kind = (
                    self._row_measure_kind(payload, rows[row_index])
                    if row_index is not None and 0 <= row_index < len(rows)
                    else None
                )
                return self._save_writeoff_question(
                    store,
                    context,
                    payload,
                    "choose_writeoff_amount_unit",
                    self._amount_unit_prompt(request, measure_kind),
                )
            request = payload.pop("amount_unit_request", {})
            self._apply_plain_amount_unit(payload, request, unit)
            return self._continue_writeoff_operation_impl(store, context, payload)

        if status == "collect_writeoff_missing":
            if self._is_edit_request(answer):
                missing_fields = self._income_missing_fields(store, payload, kind="writeoff")
                return self._save_writeoff_question(
                    store,
                    context,
                    payload,
                    "collect_writeoff_missing",
                    "Напишите новое значение — я заменю только то, что вы укажете.\n\n"
                    + self._writeoff_missing_prompt(missing_fields, payload),
                )

            # Той самий "голе число заповнює єдине, що бракує" ярлик, що й у
            # приходу/продажу (_handle_plain_amount_value) — не переиспользуємо
            # ЙОГО напряму, бо він жорстко викликає _continue_sale_operation/
            # _continue_income_operation, а не наш власний continue.
            plain_number = self._parse_plain_positive_number(answer)
            missing_now = self._income_missing_fields(store, payload, kind="writeoff")
            amount_row_index = self._income_amount_missing_row_index(payload)
            if plain_number is not None and len(missing_now) == 1 and amount_row_index is not None:
                measure_key = self._row_measure_kind(payload, payload["rows"][amount_row_index])
                if measure_key is None:
                    self._apply_plain_amount_unit(
                        payload, {"row_index": amount_row_index, "value": plain_number}, "quantity"
                    )
                    return self._prepend_reply_text(
                        f"Принял {_display_bot_number(plain_number)} как шт.",
                        self._continue_writeoff_operation_impl(store, context, payload),
                    )
                if not self._is_whole_number(plain_number):
                    self._apply_plain_amount_unit(
                        payload, {"row_index": amount_row_index, "value": plain_number}, "measure"
                    )
                    unit_label = self._MEASURE_KIND_UNIT[measure_key]
                    return self._prepend_reply_text(
                        f"Принял {_display_bot_number(plain_number)} как {unit_label}.",
                        self._continue_writeoff_operation_impl(store, context, payload),
                    )
                request = {"row_index": amount_row_index, "value": plain_number}
                payload["amount_unit_request"] = request
                return self._save_writeoff_question(
                    store,
                    context,
                    payload,
                    "choose_writeoff_amount_unit",
                    self._amount_unit_prompt(request, measure_key),
                )

            # Причина списання — необов'язкове вільне поле: перевикористовує
            # вже наявний alias-механізм ключа "comment" (_SALE_FIELD_ALIASES/
            # _extract_sale_fields), не нову логіку розпізнавання тексту.
            # Клиент/адреса/ціна/оплата з тієї самої функції (якщо їх раптом
            # хтось напише) свідомо ігноруються — писання нижче не читає їх.
            extracted_text, extra_fields = self._extract_sale_fields(answer, store)
            if extra_fields.get("comment"):
                payload["comment"] = extra_fields["comment"]
            incoming_payload, _ = self._parse_income_message(extracted_text)
            self._merge_income_payload(payload, incoming_payload)
            return self._continue_writeoff_operation_impl(store, context, payload)

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Не удалось продолжить операцию. Отправьте списание заново.", store)
