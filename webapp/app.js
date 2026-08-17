(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  var SELECT_PLACEHOLDER_TEXT = "— выберите —";

  function confirmWithTelegram(text, onConfirmed) {
    if (tg && tg.showConfirm) {
      try {
        tg.showConfirm(text, function (confirmed) {
          if (confirmed) {
            onConfirmed();
          }
        });
        return;
      } catch (error) {
        // Старые клиенты Telegram (< 6.2) бросают WebAppMethodUnsupported -
        // тихо переходим на обычный window.confirm вместо падения флоу.
      }
    }
    if (window.confirm(text)) {
      onConfirmed();
    }
  }

  // Задача користувача: "чи є якийсь інший шлях?" - initData порожній на
  // реальних пристроях (не з'ясовано чому), тож будь-яка дія, що потребує
  // підтвердити ОСОБУ користувача (збереження/видалення шаблону), знову йде
  // через sendData() (гарантовано працює - Telegram сам авторизує звичайне
  // повідомлення через chat_id). Цей fetch() лишається лише для дій, що НЕ
  // потребують особи - завантаження самих даних форми за токеном
  // (webapp_server.py:/api/template, action=get_context).
  // Реальний ризик (аудит коду, 2026-08-14): жоден запит цієї форми (у т.ч.
  // саме перше завантаження - get_context) не мав тайм-ауту - при
  // зависанні тунелю/сервера сторінка лишалась би "вантажиться" назавжди,
  // без жодної помилки. Той самий фікс, що вже застосований у data.js.
  var _REQUEST_TIMEOUT_MS = 20000;

  function postTemplateAction(payload) {
    var body = JSON.stringify(Object.assign({ init_data: tg ? tg.initData : "" }, payload));
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, _REQUEST_TIMEOUT_MS);
    return fetch("/api/template", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      signal: controller.signal,
    }).then(function (response) {
      clearTimeout(timeoutId);
      return response.json().catch(function () {
        return null;
      }).then(function (data) {
        if (!response.ok || !data || !data.ok) {
          throw new Error((data && data.error) || "Не удалось выполнить действие.");
        }
        return data;
      });
    }).catch(function (error) {
      clearTimeout(timeoutId);
      if (error && error.name === "AbortError") {
        throw new Error("Сервер не отвечает. Проверьте соединение и попробуйте ещё раз.");
      }
      throw error;
    });
  }

  function decodeContext() {
    var params = new URLSearchParams(window.location.search);
    var raw = params.get("ctx");
    if (!raw) {
      return null;
    }
    try {
      var b64 = raw.replace(/-/g, "+").replace(/_/g, "/");
      var pad = b64.length % 4;
      if (pad) {
        b64 += "=".repeat(4 - pad);
      }
      var binStr = atob(b64);
      var bytes = new Uint8Array(binStr.length);
      for (var i = 0; i < binStr.length; i++) {
        bytes[i] = binStr.charCodeAt(i);
      }
      var json = new TextDecoder("utf-8").decode(bytes);
      return JSON.parse(json);
    } catch (err) {
      return null;
    }
  }

  function applyTheme() {
    if (!tg || !tg.themeParams) {
      return;
    }
    var root = document.documentElement;
    var map = {
      bg_color: "--tg-bg",
      text_color: "--tg-text",
      hint_color: "--tg-hint",
      link_color: "--tg-link",
      button_color: "--tg-button",
      button_text_color: "--tg-button-text",
      secondary_bg_color: "--tg-secondary-bg",
    };
    Object.keys(map).forEach(function (key) {
      var value = tg.themeParams[key];
      if (value) {
        root.style.setProperty(map[key], value);
      }
    });
  }

  // Задача користувача: "хочу мати змогу вибрати колір фону, колір тексту,
  // розмір тексту... який жирний, який ні" (екран "Проверьте данные") -
  // ctx.style (об'єкт із значеннями з display_settings_<user>.json,
  // telegram_dialog_core.py's _webapp_style_ctx) стає CSS-змінними на
  // document.documentElement. Порожній/відсутній колір - НЕ встановлюємо
  // змінну взагалі, тож CSS-фолбек (var(--custom-X, var(--tg-hint))) сам
  // підхоплює колір теми Telegram - той самий принцип, що вже застосований
  // для applyTheme() нижче.
  var CUSTOM_STYLE_VAR_MAP = {
    title_color: "--custom-title-color",
    title_size: "--custom-title-size",
    title_bold: "--custom-title-weight",
    category_color: "--custom-category-color",
    category_size: "--custom-category-size",
    category_bold: "--custom-category-weight",
    body_color: "--custom-body-color",
    body_size: "--custom-body-size",
    body_bold: "--custom-body-weight",
    common_color: "--custom-common-color",
    common_size: "--custom-common-size",
    common_bold: "--custom-common-weight",
    card_bg_color: "--custom-card-bg",
    entry_bg_color: "--custom-entry-bg",
    page_bg_color: "--custom-page-bg",
    group1_text_color: "--custom-g1-text",
    group1_border_color: "--custom-g1-border",
    group1_fill_color: "--custom-g1-fill",
    group2_text_color: "--custom-g2-text",
    group2_border_color: "--custom-g2-border",
    group2_fill_color: "--custom-g2-fill",
    group3_text_color: "--custom-g3-text",
    group3_border_color: "--custom-g3-border",
    group3_fill_color: "--custom-g3-fill",
  };

  function applyCustomStyle(style) {
    if (!style) {
      return;
    }
    var root = document.documentElement;
    Object.keys(CUSTOM_STYLE_VAR_MAP).forEach(function (key) {
      var value = style[key];
      if (value === undefined || value === null || value === "") {
        return;
      }
      var cssVar = CUSTOM_STYLE_VAR_MAP[key];
      if (key.indexOf("_size") !== -1) {
        root.style.setProperty(cssVar, value + "px");
      } else if (key.indexOf("_bold") !== -1) {
        root.style.setProperty(cssVar, value ? "700" : "400");
      } else {
        root.style.setProperty(cssVar, value);
      }
    });
  }

  // Задача користувача: "щоб я окремо міг кожному заголовку [поля форми]
  // міг вибрати колір, товщину, розмір" - на відміну від applyCustomStyle
  // вище (4 агреговані групи на екрані "Проверьте данные"), тут кожен
  // РЕАЛЬНИЙ field.key (порода/товщина/ширина/довжина/кількість/ціна/
  // клієнт/адреса/спосіб оплати/причина/об'єм/category) має ВЛАСНИЙ,
  // незалежний перевизначений стиль - словник ctx.field_label_styles,
  // ключ = field.key, значення = {color,size,bold}. Пряме встановлення
  // inline-стилю (не CSS-змінна), бо назви полів динамічні й не можна
  // заздалегідь перелічити CSS-класи для кожного можливого ключа.
  var currentFieldLabelStyles = {};

  function applyFieldLabelStyle(labelEl, fieldKey) {
    var override = currentFieldLabelStyles[fieldKey];
    if (!override) {
      return;
    }
    if (override.color) {
      labelEl.style.color = override.color;
    }
    if (override.size) {
      labelEl.style.fontSize = override.size + "px";
    }
    if (override.bold === true) {
      labelEl.style.fontWeight = "700";
    } else if (override.bold === false) {
      labelEl.style.fontWeight = "400";
    }
  }

  function inputTypeFor(field) {
    if (field.type === "number") {
      return "number";
    }
    return "text";
  }

  // Дзеркало utils.py _display_bot_number - потрібне, щоб JS-побудовані
  // значення для пошуку в dimension_combos буквально співпадали з
  // серверними рядками (кома як десятковий роздільник, ціле число без ".0").
  function formatServerNumber(value) {
    var num = Number(value);
    if (isNaN(num)) {
      return String(value);
    }
    if (Number.isInteger(num)) {
      return String(num);
    }
    var rounded = Math.round(num * 10000) / 10000;
    return String(rounded).replace(".", ",");
  }

  // Задача користувача: "523,9755 MDL - прибери їх взагалі" - гроші (MDL)
  // не повинні показувати ту саму 4-знакову точність, що потрібна для
  // фізичних вимірів (м3/м2/мп) - formatServerNumber лишається як є для
  // вимірів, а суми MDL скрізь округлюються до 2 знаків.
  function formatMoney(value) {
    var num = Number(value);
    if (isNaN(num)) {
      return String(value);
    }
    var rounded = Math.round(num * 100) / 100;
    if (Number.isInteger(rounded)) {
      return String(rounded);
    }
    return String(rounded).replace(".", ",");
  }

  // Дзеркало utils.py row_measure_kind/piece_measure/is_area_based_product/
  // is_quantity_only_product/is_linear_meter_size - Задача користувача
  // (скріншот екрана прихід-форми): "потрібно щоб відразу рахувало і
  // показувало одиницю вимірювання.. тобто скільки це м3, чи м2, чи мп".
  // Реальний ризик (аудит коду, 2026-08-14): раніше ці 3 списки були
  // окремою РУЧНОЮ копією серверної класифікації (utils.py) - наступний
  // товар, доданий лише по один бік, тихо розсинхронізував би одиниці
  // виміру на екрані підтвердження з тим, що бот реально записав.
  // Значення нижче - лише FALLBACK (якщо ctx з якоїсь причини старий/без
  // measure_classification) - applyMeasureClassification (startForm)
  // перезаписує їх свіжими даними з сервера при кожному відкритті форми.
  var AREA_BASED_PRODUCTS = ["вагонка"];
  var QUANTITY_ONLY_PRODUCTS = ["осб"];
  var LINEAR_METER_SIZES = [[25, 50], [30, 50], [50, 50]];

  function applyMeasureClassification(ctx) {
    var data = ctx && ctx.measure_classification;
    if (!data) {
      return;
    }
    if (Array.isArray(data.area_based_products)) {
      AREA_BASED_PRODUCTS = data.area_based_products;
    }
    if (Array.isArray(data.quantity_only_products)) {
      QUANTITY_ONLY_PRODUCTS = data.quantity_only_products;
    }
    if (Array.isArray(data.linear_meter_sizes)) {
      LINEAR_METER_SIZES = data.linear_meter_sizes;
    }
  }
  var MEASURE_UNIT_BY_KIND = { volume: "м3", area: "м2", linear: "мп" };

  // Реальний ризик (аудит коду, 2026-08-14): раніше лише .trim().
  // toLowerCase() - слабше за серверний _normalize_phrase (utils.py:
  // casefold + "ё"->"е" + залишає ЛИШЕ буквено-цифрові токени, прибираючи
  // будь-яку пунктуацію й схлопуючи внутрішні пробіли). AREA_BASED_
  // PRODUCTS/QUANTITY_ONLY_PRODUCTS (measure_classification_data,
  // webapp_server.py) - ті самі рядки, які проходять якраз ЦЕЙ Python
  // _normalize_phrase на сервері; category-мітки тут теж адміністративно
  // редаговані (Дії, custom-категорії), тож зайвий пробіл/пунктуація в
  // назві товару - не гіпотетичний випадок. Дзеркалимо ту саму логіку, щоб
  // порівняння тут і на сервері завжди узгоджувались.
  function normalizeProductPhrase(value) {
    var text = String(value === null || value === undefined ? "" : value)
      .toLowerCase()
      .replace(/ё/g, "е");
    var matches = text.match(/[0-9a-zа-яіїєґ]+/g);
    return matches ? matches.join(" ") : "";
  }

  function numberOrZero(value) {
    if (value === null || value === undefined || value === "") {
      return 0;
    }
    var num = Number(String(value).replace(",", "."));
    return isNaN(num) ? 0 : num;
  }

  function isLinearMeterSize(thickness, width) {
    var t = numberOrZero(thickness);
    var w = numberOrZero(width);
    if (t <= 0 || w <= 0) {
      return false;
    }
    var sorted = [t, w].sort(function (a, b) { return a - b; });
    return LINEAR_METER_SIZES.some(function (pair) {
      return pair[0] === sorted[0] && pair[1] === sorted[1];
    });
  }

  function rowMeasureKind(product, thickness, width) {
    var normalized = normalizeProductPhrase(product);
    if (QUANTITY_ONLY_PRODUCTS.indexOf(normalized) !== -1) {
      return null;
    }
    if (AREA_BASED_PRODUCTS.indexOf(normalized) !== -1) {
      return "area";
    }
    if (isLinearMeterSize(thickness, width)) {
      return "linear";
    }
    return "volume";
  }

  function pieceMeasure(thickness, width, length, kind) {
    var t = numberOrZero(thickness) / 1000;
    var w = numberOrZero(width) / 1000;
    var l = numberOrZero(length) / 1000;
    if (kind === "area") {
      return w * l;
    }
    if (kind === "linear") {
      return l;
    }
    return t * w * l;
  }

  function computeMeasureText(product, thickness, width, length, quantity) {
    var kind = rowMeasureKind(product, thickness, width);
    if (!kind) {
      return null;
    }
    var qty = numberOrZero(quantity);
    if (qty <= 0) {
      return null;
    }
    var total = pieceMeasure(thickness, width, length, kind) * qty;
    if (!isFinite(total) || total <= 0) {
      return null;
    }
    return formatServerNumber(total) + " " + MEASURE_UNIT_BY_KIND[kind];
  }

  // Ширина поля залежить від того, що в ньому реально буде - декілька цифр
  // (толщина/ширина/довжина/кількість) не мають розтягуватись на весь
  // рядок; слова (спосіб оплати) чи вільний текст (порода/клієнт/адреса)
  // потребують більше місця. За замовчуванням (без класу) - вузько.
  function widthClassFor(field) {
    if (field.key === "address") {
      return "field-address";
    }
    if (field.type === "text") {
      return "field-wide";
    }
    if (field.type === "select" && !field.numeric) {
      return "field-medium";
    }
    if (field.decimal) {
      return "field-medium";
    }
    return null;
  }

  function buildManualValueInput(field, widthClass) {
    var manual = document.createElement("input");
    manual.type = field.numeric ? "number" : "text";
    if (field.numeric) {
      manual.inputMode = field.decimal ? "decimal" : "numeric";
      manual.step = field.decimal ? "any" : "1";
      manual.min = "0";
    } else {
      manual.autocomplete = "off";
    }
    manual.className = "manual-value-input";
    if (widthClass) {
      manual.classList.add(widthClass);
    }
    return manual;
  }

  // За проханням користувача (ескіз): dropdown і поле вводу вручну - ОБИДВА
  // одразу видимі й доступні, не одне "приховане позаду" вибору "Другое
  // значение..." в самому select-і. Обирає людина сама: тапнути список чи
  // просто написати. Взаємовиключність (обрано select -> стирається manual,
  // і навпаки) - лише щоб не виникало питання "яке з двох значень рахувати".
  function buildFieldElement(field, container) {
    var wrap = document.createElement("div");
    wrap.className = "field";
    wrap.dataset.key = field.key;

    var label = document.createElement("label");
    label.textContent = field.label + (field.required === false ? "" : " *");
    applyFieldLabelStyle(label, field.key);
    wrap.appendChild(label);

    var widthClass = widthClassFor(field);
    var input;
    if (field.type === "select") {
      input = document.createElement("select");
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = SELECT_PLACEHOLDER_TEXT;
      input.appendChild(placeholder);
      (field.options || []).forEach(function (opt) {
        var option = document.createElement("option");
        option.value = opt;
        option.textContent = opt;
        input.appendChild(option);
      });
      if (widthClass) {
        input.classList.add(widthClass);
      }
      input.name = field.key;

      if (field.allow_custom) {
        var manualInput = buildManualValueInput(field, widthClass);
        manualInput.name = field.key + "__manual";
        input.manualInput = manualInput;
        input.addEventListener("change", function () {
          if (input.value !== "") {
            manualInput.value = "";
          }
        });
        manualInput.addEventListener("input", function () {
          if (manualInput.value !== "") {
            input.value = "";
          }
        });

        var controls = document.createElement("div");
        controls.className = "field-controls";
        controls.appendChild(manualInput);
        controls.appendChild(input);
        wrap.appendChild(controls);
        container.appendChild(wrap);
        return input;
      }
    } else {
      input = document.createElement("input");
      input.type = inputTypeFor(field);
      if (field.type === "number") {
        input.inputMode = field.decimal ? "decimal" : "numeric";
        input.step = field.decimal ? "any" : "1";
        input.min = "0";
      } else {
        input.autocomplete = "off";
      }
      input.name = field.key;
      if (widthClass) {
        input.classList.add(widthClass);
      }
    }
    wrap.appendChild(input);
    container.appendChild(wrap);
    return input;
  }

  // Реальний баг користувача ("???" на скріншоті): товщина/ширина/довжина -
  // 3 ОКРЕМІ select-и, кожен зі своїм повним списком значень, тож можна
  // обрати товщину і ширину, які САМІ ПО СОБІ реальні, але РАЗОМ на складі
  // не існують (25мм існує лише з шириною 120, ширина 150 - лише з
  // товщиною 47). combos - реальні трійки [товщина,ширина,довжина]
  // (ctx.dimension_combos/cat.dimension_combos, telegram_dialog_core.py) -
  // звужуємо ширину під обрану товщину, довжину під товщину+ширину, щоб
  // неможливу комбінацію не можна було навіть вибрати. Торкається ЛИШЕ
  // самих select-ів - ручне поле (allow_custom) лишається без обмежень,
  // бо для приходу нового розміру такої комбінації на складі ще й не МАЄ
  // бути.
  function rebuildSelectOptions(select, values, keepValue) {
    select.innerHTML = "";
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = SELECT_PLACEHOLDER_TEXT;
    select.appendChild(placeholder);
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    select.value = keepValue && values.indexOf(keepValue) !== -1 ? keepValue : "";
  }

  // combo - [порода, товщина, ширина, довжина, залишок] (telegram_dialog_
  // core.py's _existing_dimension_combos) - індекси 0..3 для distinctValuesAt/
  // filters нижче ЗАВЖДИ відповідають цьому порядку.
  function distinctValuesAt(combos, index, filters) {
    var values = [];
    combos.forEach(function (combo) {
      for (var i = 0; i < filters.length; i++) {
        if (filters[i] && combo[i] !== filters[i]) {
          return;
        }
      }
      if (values.indexOf(combo[index]) === -1) {
        values.push(combo[index]);
      }
    });
    return values;
  }

  function findComboBalance(combos, breed, thickness, width, length) {
    for (var i = 0; i < combos.length; i++) {
      var c = combos[i];
      if (c[0] === breed && c[1] === thickness && c[2] === width && c[3] === length) {
        return c[4];
      }
    }
    return null;
  }

  // Задача користувача (скріншот): (1) товщина/ширина/довжина мають
  // звужуватись не лише РАЗОМ (як уже було), а й під ОБРАНУ породу - розмір
  // ховається з дропдауна, якщо саме для цієї породи його залишок 0; (2)
  // поле "Количество, шт" показує поруч клікабельну цифру поточного
  // залишку САМЕ для обраної породи+розміру - тап відразу підставляє це
  // число в поле. breedInput - select+manual породи (може бути відсутній,
  // якщо порода вже "відома" з чату і в формі взагалі не рендериться).
  function wireDimensionCascade(rowInputs, combos, breedInput) {
    var thicknessSelect = rowInputs.thickness;
    var widthSelect = rowInputs.width;
    var lengthSelect = rowInputs.length;
    var quantityInput = rowInputs.quantity;
    if (!combos || !combos.length || !thicknessSelect || !widthSelect || !lengthSelect) {
      return;
    }

    var balanceHint = null;
    if (quantityInput) {
      balanceHint = document.createElement("button");
      balanceHint.type = "button";
      balanceHint.className = "stock-balance-hint";
      balanceHint.style.display = "none";
      balanceHint.addEventListener("click", function () {
        if (balanceHint.dataset.value !== undefined) {
          quantityInput.value = balanceHint.dataset.value;
        }
      });
      // Задача користувача (скріншот): цифра залишку - ЛІВОРУЧ, поле вводу
      // кількості - ПРАВОРУЧ (insertBefore, не appendChild).
      var quantityWrap = quantityInput.closest(".field");
      if (quantityWrap) {
        quantityWrap.insertBefore(balanceHint, quantityInput);
      }
    }

    function currentBreed() {
      return breedInput ? readFieldValue(breedInput) : "";
    }
    function refreshThickness() {
      var breed = currentBreed();
      rebuildSelectOptions(
        thicknessSelect,
        distinctValuesAt(combos, 1, [breed || null, null, null, null]),
        thicknessSelect.value
      );
    }
    function refreshWidth() {
      var breed = currentBreed();
      var thickness = readFieldValue(thicknessSelect);
      rebuildSelectOptions(
        widthSelect,
        distinctValuesAt(combos, 2, [breed || null, thickness, null, null]),
        widthSelect.value
      );
    }
    function refreshLength() {
      var breed = currentBreed();
      var thickness = readFieldValue(thicknessSelect);
      var width = readFieldValue(widthSelect);
      rebuildSelectOptions(
        lengthSelect,
        distinctValuesAt(combos, 3, [breed || null, thickness, width, null]),
        lengthSelect.value
      );
    }
    // Задача користувача: "коли в продажі вибираю через шаблон... не
    // показує реальну кількість штук" - шаблон/недавні можуть виставити
    // розмір, якого НЕМАЄ серед поточних <option> (setFieldValue тоді кладе
    // значення в manualInput, а не в сам select) - пряме читання .value тут
    // бачило порожній рядок і ховало підказку. readFieldValue() вже вміє
    // правильно брати значення з manualInput, коли він реально заповнений.
    function refreshBalanceHint() {
      if (!balanceHint) {
        return;
      }
      var breed = currentBreed();
      var thickness = readFieldValue(thicknessSelect);
      var width = readFieldValue(widthSelect);
      var length = readFieldValue(lengthSelect);
      if (!breed || !thickness || !width || !length) {
        balanceHint.style.display = "none";
        return;
      }
      var balance = findComboBalance(combos, breed, thickness, width, length);
      if (balance === null) {
        balanceHint.style.display = "none";
        return;
      }
      var formatted = formatServerNumber(balance);
      balanceHint.textContent = "На складе: " + formatted + " шт";
      balanceHint.dataset.value = formatted.replace(",", ".");
      balanceHint.style.display = "";
    }

    if (breedInput) {
      var onBreedChange = function () {
        refreshThickness();
        refreshWidth();
        refreshLength();
        refreshBalanceHint();
      };
      breedInput.addEventListener("change", onBreedChange);
      breedInput.addEventListener("input", onBreedChange);
      if (breedInput.manualInput) {
        breedInput.manualInput.addEventListener("input", onBreedChange);
      }
    }
    thicknessSelect.addEventListener("change", function () {
      refreshWidth();
      refreshLength();
      refreshBalanceHint();
    });
    widthSelect.addEventListener("change", function () {
      refreshLength();
      refreshBalanceHint();
    });
    lengthSelect.addEventListener("change", refreshBalanceHint);
  }

  // Для select+allow_custom - справжнє значення бере поле, яке РЕАЛЬНО
  // заповнене (взаємовиключність у buildFieldElement гарантує, що заповнене
  // лише одне з двох). Для решти полів - просто саме значення input/select.
  function readFieldValue(input) {
    if (input.manualInput && input.manualInput.value.trim() !== "") {
      return input.manualInput.value.trim();
    }
    return input.value.trim();
  }

  // Задача користувача (скріншот екрана "Списание одной формой"): поруч із
  // "Количество" - ЖИВИЙ перерахунок у м3/м2/мп, щоб людина відразу бачила,
  // скільки саме вона списує/приходує/продає у фізичних одиницях, ще ДО
  // натискання "Отправить" (не лише на екрані підтвердження, куди цей
  // перерахунок вже додано раніше). computeMeasureText - та сама, вже
  // наявна класифікація (product -> м3/area/linear/None для ОСБ-подібних
  // товарів). На відміну від wireDimensionCascade (яка виходить одразу, якщо
  // dimension_combos порожній - приход навмисно без обмеження вибору) - цей
  // хук працює завжди, незалежно від наявності комбінацій залишку.
  function wireMeasureHint(rowInputs, product) {
    var thicknessInput = rowInputs.thickness;
    var widthInput = rowInputs.width;
    var lengthInput = rowInputs.length;
    var quantityInput = rowInputs.quantity;
    if (!thicknessInput || !widthInput || !lengthInput || !quantityInput) {
      return;
    }
    var hint = document.createElement("span");
    hint.className = "measure-hint";
    hint.style.display = "none";
    var quantityWrap = quantityInput.closest(".field");
    if (quantityWrap) {
      quantityWrap.appendChild(hint);
    }
    function refresh() {
      var text = computeMeasureText(
        product,
        readFieldValue(thicknessInput),
        readFieldValue(widthInput),
        readFieldValue(lengthInput),
        quantityInput.value
      );
      if (!text) {
        hint.style.display = "none";
        return;
      }
      hint.textContent = "= " + text;
      hint.style.display = "";
    }
    [thicknessInput, widthInput, lengthInput, quantityInput].forEach(function (input) {
      input.addEventListener("input", refresh);
      input.addEventListener("change", refresh);
      if (input.manualInput) {
        input.manualInput.addEventListener("input", refresh);
        input.manualInput.addEventListener("change", refresh);
      }
    });
    refresh();
  }

  // Задача користувача ("Антисептирование (форма)"): "скільки штук, відразу
  // рахує кубатуру" - той самий живий хук, що й wireMeasureHint вище, але
  // ЗАВЖДИ фізичний м3 (т*ш*д/1e9*штук), незалежно від того, як саме
  // продається сам товар (м2 для Вагонки, шт для ОСБ і т.д.) - той самий
  // принцип, що вже давно рахує currentAntisepticVolume() для антисептик-
  // доповнення всередині форми продажі (webapp/app.js, "Антисептирование"-
  // чекбокс), лише тут це єдине призначення поля "Штук", не доповнення.
  function wireAntisepticVolumeHint(rowInputs) {
    var thicknessInput = rowInputs.thickness;
    var widthInput = rowInputs.width;
    var lengthInput = rowInputs.length;
    var quantityInput = rowInputs.quantity;
    if (!thicknessInput || !widthInput || !lengthInput || !quantityInput) {
      return;
    }
    var hint = document.createElement("span");
    hint.className = "measure-hint";
    hint.style.display = "none";
    var quantityWrap = quantityInput.closest(".field");
    if (quantityWrap) {
      quantityWrap.appendChild(hint);
    }
    function refresh() {
      // numberOrZero (не parseLocaleNumber - той живе усередині mainAllInOne,
      // за межами області видимості цієї, зовнішньої функції) - порожнє чи
      // нечисле поле стає 0, а volume<=0 нижче й так ховає підказку, поки
      // НЕ ВСІ чотири поля реально заповнені.
      var t = numberOrZero(readFieldValue(thicknessInput));
      var w = numberOrZero(readFieldValue(widthInput));
      var l = numberOrZero(readFieldValue(lengthInput));
      var q = numberOrZero(readFieldValue(quantityInput));
      var volume = (t * w * l) / 1e9 * q;
      if (volume <= 0) {
        hint.style.display = "none";
        return;
      }
      hint.textContent = "Объём: " + formatServerNumber(volume) + " м3";
      hint.style.display = "";
    }
    [thicknessInput, widthInput, lengthInput, quantityInput].forEach(function (input) {
      input.addEventListener("input", refresh);
      input.addEventListener("change", refresh);
      if (input.manualInput) {
        input.manualInput.addEventListener("input", refresh);
        input.manualInput.addEventListener("change", refresh);
      }
    });
    refresh();
  }

  // "Реализация (форма)" - категорія (і, за замовчуванням, лист товару)
  // обирається ПРЯМО в самій формі (select "Категория" зверху), решта
  // полів реактивно показує/ховає ту саму розмітку, що для однокатегорійної
  // форми будує buildFieldElement (row-block для thickness/width/length/
  // quantity, звичайні поля для breed чи "Объем, м3" в антисептируванні) -
  // жодного дубльованого правила рендеру, лише інший спосіб компонувати
  // вже готові поля з ctx.categories/ctx.common_fields. На сервері
  // (_continue_sale_all_in_one_submission) довіряємо ЛИШЕ обраному
  // category_operation_id - product/condition НІКОЛИ не йдуть з форми.
  // Задача користувача (скріншот "Продажа одной формой"): "об'єм спусти
  // нижче до суми... скрізь де є подібні значення - став їх разом і
  // знизу", уточнення "Способ оплаты - це можна наверх" - числові поля
  // "скільки"/"по чому" (толщина/ширина/довжина/кількість чи об'єм услуги
  // + ціна) групуються РАЗОМ УНИЗУ форми; ідентифікуючі поля (порода/
  // клієнт/адреса/спосіб оплати) - зверху. MEASURE_FIELD_KEYS - той самий
  // фіксований, стабільний перелік ключів по всій формі (thickness/width/
  // length/quantity - завжди разом як один row-block; volume/price_per_unit
  // - самостійні поля тієї самої групи).
  var MEASURE_FIELD_KEYS = ["thickness", "width", "length", "quantity", "volume", "price_per_unit"];

  function isMeasureField(field) {
    return MEASURE_FIELD_KEYS.indexOf(field.key) !== -1;
  }

  function mainAllInOne(ctx) {
    var categories = ctx.categories || [];
    // Задача користувача: "антисептирование - це додаткова послуга", не
    // окрема категорія товару - раніше "АНТИСЕПТИРОВАНИЕ" була ЩЕ ОДНИМ
    // варіантом того самого select "Категория", і вибір її ПОВНІСТЮ
    // замінював поточні дані форми (реальний баг: 3 позиції в кошику
    // губились, коли людина перемикала категорію на антисептик). Тепер
    // service-категорія взагалі не потрапляє в select - лише її поля
    // (мітки "Объём"/"Цена") використовуються нижче для чекбокса-доповнення.
    var saleCategories = categories.filter(function (c) { return c.kind !== "service"; });
    var antisepticCategory = categories.filter(function (c) { return c.kind === "service"; })[0];
    var commonFields = ctx.common_fields || [];
    var identityCommonFields = commonFields.filter(function (f) {
      return !isMeasureField(f);
    });
    var measureCommonFields = commonFields.filter(isMeasureField);

    var categoryWrap = document.createElement("div");
    categoryWrap.className = "field";
    var categoryLabel = document.createElement("label");
    categoryLabel.textContent = "Категория *";
    applyFieldLabelStyle(categoryLabel, "category");
    categoryWrap.appendChild(categoryLabel);
    var categorySelect = document.createElement("select");
    categorySelect.className = "field-wide";
    saleCategories.forEach(function (cat) {
      var option = document.createElement("option");
      option.value = String(cat.key);
      option.textContent = cat.label;
      categorySelect.appendChild(option);
    });
    categoryWrap.appendChild(categorySelect);

    var measureContainer = document.getElementById("rows");
    var identityContainer = document.createElement("div");
    identityContainer.id = "identity-fields";
    measureContainer.parentNode.insertBefore(categoryWrap, measureContainer);
    measureContainer.parentNode.insertBefore(identityContainer, measureContainer);

    var categoryState = {};
    saleCategories.forEach(function (cat) {
      var identityBlock = document.createElement("div");
      identityBlock.className = "category-group";
      identityBlock.dataset.categoryKey = String(cat.key);
      var measureBlock = document.createElement("div");
      measureBlock.className = "category-group";
      measureBlock.dataset.categoryKey = String(cat.key);

      var fields = cat.fields || [];
      var identityFields = fields.filter(function (f) {
        return !isMeasureField(f);
      });
      var perRow = fields.filter(function (f) {
        return f.per_row && isMeasureField(f);
      });
      var flatMeasure = fields.filter(function (f) {
        return !f.per_row && isMeasureField(f);
      });

      var rowInputs = {};
      var rowBlock = null;
      if (perRow.length) {
        rowBlock = document.createElement("div");
        rowBlock.className = "row-block";
        perRow.forEach(function (field) {
          rowInputs[field.key] = buildFieldElement(field, rowBlock);
        });
        measureBlock.appendChild(rowBlock);
      }
      var flatInputs = {};
      identityFields.forEach(function (field) {
        flatInputs[field.key] = buildFieldElement(field, identityBlock);
      });
      flatMeasure.forEach(function (field) {
        flatInputs[field.key] = buildFieldElement(field, measureBlock);
      });
      // Породу будуємо лише ЩОЙНО ВИЩЕ (identityFields) - тому викликаємо
      // wireDimensionCascade лише ТЕПЕР, коли flatInputs.breed уже існує.
      if (perRow.length) {
        wireDimensionCascade(rowInputs, cat.dimension_combos, flatInputs.breed);
        if (cat.kind === "antiseptic") {
          wireAntisepticVolumeHint(rowInputs);
        } else {
          wireMeasureHint(rowInputs, cat.product);
        }
      }

      identityContainer.appendChild(identityBlock);
      measureContainer.appendChild(measureBlock);
      categoryState[String(cat.key)] = {
        fields: fields,
        rowInputs: rowInputs,
        flatInputs: flatInputs,
        identityBlock: identityBlock,
        measureBlock: measureBlock,
        rowBlock: rowBlock,
        kind: cat.kind,
      };
    });

    // Задача користувача: "змісти кнопку антисептирование вище ціни, між
    // штуками і ціною" - antisepticWrap (єдиний, спільний елемент, не по
    // категорії) переносимо ВСЕРЕДИНУ активного measureBlock, одразу після
    // rowBlock (товщина/ширина/довжина/штук) і ПЕРЕД полем ціни - функція
    // ще не існує на момент першого showCategory (antisepticWrap будується
    // нижче), тому це просто заглушка, яку перевизначаємо після побудови.
    var relocateAntisepticWrap = function () {};
    function showCategory(key) {
      Object.keys(categoryState).forEach(function (k) {
        var display = k === key ? "" : "none";
        categoryState[k].identityBlock.style.display = display;
        categoryState[k].measureBlock.style.display = display;
      });
      relocateAntisepticWrap(key);
    }
    categorySelect.addEventListener("change", function () {
      showCategory(categorySelect.value);
    });
    var firstKey = saleCategories.length ? String(saleCategories[0].key) : null;
    if (firstKey) {
      categorySelect.value = firstKey;
      showCategory(firstKey);
    }

    // Способ оплаты (Задача користувача: "це можна наверх") - разом з
    // клієнтом/адресою, ще ПЕРЕД (не після) полями розміру/об'єму/ціни.
    var commonInputs = {};
    identityCommonFields.forEach(function (field) {
      commonInputs[field.key] = buildFieldElement(field, identityContainer);
    });
    var singleContainer = document.getElementById("single-fields");
    measureCommonFields.forEach(function (field) {
      commonInputs[field.key] = buildFieldElement(field, singleContainer);
    });

    var errorEl = document.getElementById("error");

    // Задача користувача (скріншот 2): "між бот аі та продажа одной
    // форми... має поміщатись 5 рядків із шаблонами" - панель ЗВЕРХУ форми,
    // ДО вибору категорії: ліворуч власні шаблони (зберігає сам користувач),
    // праворуч 5 останніх реально відправлених - клік на будь-який рядок
    // одразу вибирає категорію+заповнює розмір/породу(+клієнт/оплату), а
    // ціну/кількість людина й далі вводить сама.
    // Реальний баг (аудит коду, 2026-08-14): у файлі було ДВІ функції з
    // однаковою назвою setFieldValue - друге оголошення (нижче, ~1730)
    // "перекривало" ПЕРШЕ (hoisting) у ВСЬОМУ файлі, тож ЖОДЕН виклик з
    // назвою setFieldValue насправді не діставався сюди - навіть виклики
    // ВИЩЕ за текстом у файлі (applyTemplateEntry, застосування шаблону/
    // недавньої операції). Це саме ТА версія, що диспетчерізує change/
    // input - без неї підказка "скільки м3" і залишок на складі не
    // оновлювались, доки людина не торкалась поля вручну. Перейменована в
    // setFieldValueAndNotify і застосована ЯВНО там, де вона реально
    // потрібна (applyTemplateEntry нижче) - решта викликів (populateCategoryFields,
    // відновлення "Вернуться в форму") лишаються на тихій версії, як і
    // раніше працювали, щоб не міняти поведінку, яку ніхто не просив міняти.
    function setFieldValueAndNotify(input, value) {
      if (!input || value === null || value === undefined || value === "") {
        return;
      }
      var strValue = String(value);
      if (input.tagName === "SELECT") {
        var hasOption = false;
        for (var i = 0; i < input.options.length; i++) {
          if (input.options[i].value === strValue) {
            hasOption = true;
            break;
          }
        }
        if (hasOption) {
          input.value = strValue;
          if (input.manualInput) {
            input.manualInput.value = "";
          }
        } else if (input.manualInput) {
          input.manualInput.value = strValue;
          input.value = "";
        }
      } else {
        input.value = strValue;
      }
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    // Задача користувача: "додай все необхідне, якщо є сорт КД чи АД...
    // все основне що було введено (окрім довгих назв, типу клієнт,
    // адреса)" - категорія (сорт) і спосіб оплати короткі, тому теж у
    // рядку; клієнт/адреса свідомо не показуються (довгі назви).
    function templateRowText(entry, index) {
      var size = [entry.thickness, entry.width, entry.length].filter(function (v) {
        return v !== null && v !== undefined && v !== "";
      }).join("x");
      var parts = [entry.category_label, size, entry.breed, entry.payment_method].filter(function (v) {
        return v !== null && v !== undefined && v !== "";
      });
      return (index + 1) + ". " + parts.join(" | ");
    }

    // Задача користувача: "виділимо на шаблон і недавние не по 1 рядку, а по
    // 2, щоб влізло більше інфи. зверху через палочки все що буквами, знизу
    // розміри" - верхній рядок: категорія/порода/оплата (текстові поля);
    // нижній рядок: розмір (товщина x ширина x довжина).
    function templateRowLines(entry, index) {
      var size = [entry.thickness, entry.width, entry.length].filter(function (v) {
        return v !== null && v !== undefined && v !== "";
      }).join("x");
      var topParts = [entry.category_label, entry.breed, entry.payment_method].filter(function (v) {
        return v !== null && v !== undefined && v !== "";
      });
      return {
        top: (index + 1) + ". " + topParts.join(" | "),
        bottom: size,
      };
    }

    function applyTemplateEntry(entry) {
      var key = String(entry.category_operation_id);
      var state = categoryState[key];
      if (!state) {
        return;
      }
      var doApply = function () {
        categorySelect.value = key;
        showCategory(key);
        setFieldValueAndNotify(state.flatInputs.breed, entry.breed);
        setFieldValueAndNotify(state.rowInputs.thickness, entry.thickness);
        setFieldValueAndNotify(state.rowInputs.width, entry.width);
        setFieldValueAndNotify(state.rowInputs.length, entry.length);
        if (commonInputs.client) {
          setFieldValueAndNotify(commonInputs.client, entry.client);
        }
        if (commonInputs.address) {
          setFieldValueAndNotify(commonInputs.address, entry.address);
        }
        if (commonInputs.payment_method) {
          setFieldValueAndNotify(commonInputs.payment_method, entry.payment_method);
        }
      };
      var confirmText = "Использовать эти данные: " + (entry.category_label || "") + ", " +
        [entry.thickness, entry.width, entry.length].filter(function (v) { return v; }).join("x") +
        (entry.breed ? " (" + entry.breed + ")" : "") + "?";
      confirmWithTelegram(confirmText, doApply);
    }

    // Задача користувача: "зберіг шаблон, викинуло до бота, відразу питання
    // повернутись - так і погнали" - initData порожній на реальних
    // пристроях (не з'ясовано чому), тихий fetch() без sendData тому
    // ненадійний. Повертаємось на sendData() (гарантовано працює - Telegram
    // сам авторизує повідомлення через звичайний chat_id, initData не
    // потрібен) - бот одразу відповідає "Шаблон удалён."/"...сохранён." +
    // кнопка "Заполнить форму" (telegram_dialog_core.py:
    // _delete_operation_template_reply/_reopen_operation_all_in_one_form) -
    // один тап замість нуля, але без загадкового initData.
    function deleteTemplateEntry(entry) {
      var state = categoryState[String(entry.category_operation_id)];
      var kind = state ? state.kind : null;
      var isRecent = entry.source === "recent";
      var confirmText = (isRecent ? "Удалить запись из истории: " : "Удалить шаблон: ") +
        templateRowText(entry, 0).replace(/^1\.\s*/, "") + "?";
      confirmWithTelegram(confirmText, function () {
        var payload = { kind: kind };
        if (isRecent) {
          payload.delete_recent = true;
          payload.recent_id = entry.id;
        } else {
          payload.delete_template = true;
          payload.template_id = entry.id;
        }
        // Реальний ризик (аудит коду, 2026-08-14): на відміну від
        // sendPayload/actuallySendSinglePayload, тут не було перевірки "чи
        // взагалі є tg" - при відкритті сторінки поза Telegram (локальний
        // перегляд/тест) клік кидав би непіймане TypeError, кнопка мовчки
        // "не працювала б" без жодного пояснення в інтерфейсі.
        if (tg) {
          tg.sendData(JSON.stringify(payload));
        } else {
          window.alert(JSON.stringify(payload));
        }
      });
    }

    function buildTemplateColumn(title, entries) {
      var column = document.createElement("div");
      column.className = "template-column";
      var heading = document.createElement("div");
      heading.className = "template-column-title";
      heading.textContent = title;
      column.appendChild(heading);
      if (!entries.length) {
        var empty = document.createElement("div");
        empty.className = "template-row template-row-empty";
        empty.textContent = "Пусто";
        column.appendChild(empty);
      }
      entries.slice(0, 5).forEach(function (entry, index) {
        var row = document.createElement("div");
        row.className = "template-row";
        var textWrap = document.createElement("span");
        textWrap.className = "template-row-text";
        var lines = templateRowLines(entry, index);
        var topLine = document.createElement("span");
        topLine.className = "template-row-line-top";
        topLine.textContent = lines.top;
        textWrap.appendChild(topLine);
        if (lines.bottom) {
          var bottomLine = document.createElement("span");
          bottomLine.className = "template-row-line-bottom";
          bottomLine.textContent = lines.bottom;
          textWrap.appendChild(bottomLine);
        }
        row.appendChild(textWrap);
        var deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.className = "template-row-delete";
        deleteBtn.textContent = "×";
        deleteBtn.addEventListener("click", function (event) {
          event.stopPropagation();
          deleteTemplateEntry(entry);
        });
        row.appendChild(deleteBtn);
        row.addEventListener("click", function () {
          applyTemplateEntry(entry);
        });
        column.appendChild(row);
      });
      return column;
    }

    // "Сохранить как шаблон" перенесено донизу форми (перед "Продолжить
    // продажу") - фактична вставка в DOM відбувається нижче, поруч з
    // addPositionButton; тут кнопка лише створюється.
    var saveTemplateButton = document.createElement("button");
    saveTemplateButton.type = "button";
    saveTemplateButton.className = "save-template-button";
    saveTemplateButton.textContent = "Сохранить как шаблон";

    // Задача користувача: "коли я беру зберегти шаблон і мене викидує з
    // операції - жах, прибери це... той шаблон відразу має бути у
    // відповідній строці" - панель перемальовується НА МІСЦІ (без sendData,
    // без закриття Mini App) щоразу, коли з'являється/зникає перший/
    // останній рядок; порожня панель взагалі не займає місця в формі.
    // Панель шаблонів лишається зверху (біля статусу "Сохранено") - вона
    // потрібна для швидкого заповнення форми ДО введення даних, на відміну
    // від самої кнопки збереження, яка природньо йде в кінці.
    var templatePanel = document.createElement("div");
    templatePanel.className = "template-panel";
    var templatePanelInserted = false;
    function renderTemplatePanel(templates, recent) {
      templates = templates || [];
      recent = recent || [];
      templatePanel.innerHTML = "";
      var hasAny = templates.length || recent.length;
      if (hasAny) {
        templatePanel.appendChild(buildTemplateColumn("Шаблоны", templates));
        templatePanel.appendChild(buildTemplateColumn("Недавние", recent));
      }
      if (hasAny && !templatePanelInserted) {
        categoryWrap.parentNode.insertBefore(templatePanel, categoryWrap);
        templatePanelInserted = true;
      } else if (!hasAny && templatePanelInserted) {
        templatePanel.parentNode.removeChild(templatePanel);
        templatePanelInserted = false;
      }
    }
    // Шаблони/історія знову вбудовуються прямо в ctx (при відкритті) - тепер
    // ctx йде через короткий токен (register_context), а не base64 в самій
    // адресі, тож роздування URL більше не загрожує навіть з templates/
    // recent усередині.
    renderTemplatePanel(ctx.templates || [], ctx.recent || []);

    saveTemplateButton.addEventListener("click", function () {
      var key = categorySelect.value;
      var state = categoryState[key];
      if (!state || state.kind === "service") {
        errorEl.textContent = "Шаблоны недоступны для антисептирования.";
        return;
      }
      var breed = state.flatInputs.breed ? readFieldValue(state.flatInputs.breed) : "";
      var thickness = state.rowInputs.thickness ? readFieldValue(state.rowInputs.thickness) : "";
      var width = state.rowInputs.width ? readFieldValue(state.rowInputs.width) : "";
      var length = state.rowInputs.length ? readFieldValue(state.rowInputs.length) : "";
      if (!thickness || !width || !length) {
        errorEl.textContent = "Заполните размер (толщина/ширина/длина), прежде чем сохранять шаблон.";
        return;
      }
      errorEl.textContent = "";
      var templatePayload = {
        save_template: true,
        kind: state.kind,
        category_operation_id: Number(key),
        breed: breed,
        thickness: thickness,
        width: width,
        length: length,
      };
      if (commonInputs.client) {
        templatePayload.client = readFieldValue(commonInputs.client);
      }
      if (commonInputs.address) {
        templatePayload.address = readFieldValue(commonInputs.address);
      }
      if (commonInputs.payment_method) {
        templatePayload.payment_method = readFieldValue(commonInputs.payment_method);
      }
      // Задача користувача: "зберіг шаблон, викинуло до бота, відразу
      // питання повернутись - так і погнали" - sendData() гарантовано
      // працює (Telegram сам авторизує через chat_id, не потребує initData,
      // який виявився порожнім на реальних пристроях). Бот одразу відповідає
      // "Шаблон сохранён." + кнопкою "Заполнить форму" (уже наявний,
      // перевірений код: _save_operation_template_reply→
      // _reopen_operation_all_in_one_form).
      // Той самий guard "чи взагалі є tg", що вже має sendPayload вище
      // (аудит коду, 2026-08-14) - без нього поза Telegram кнопка кидала б
      // непіймане TypeError замість зрозумілого fallback.
      if (tg) {
        tg.sendData(JSON.stringify(templatePayload));
      } else {
        window.alert(JSON.stringify(templatePayload));
      }
    });

    // Задача користувача (реальний скріншот): "тут коли натискаю, має не
    // переходити назад у чат, а має видати спливаюче вікно підтвердження...
    // редактировать прямо повертає в застосунку у попереднє вікно для
    // редагування, щоб не було пригання між чатом і програмою". Перший
    // клік по "Отправить" (нативна Telegram MainButton) валідує й БУДУЄ
    // payload, як і раніше, але замість sendData - показує підсумок ПРЯМО
    // тут (form ховається, #confirm-view показується); ДРУГИЙ клік по тій
    // самій кнопці (стан confirmPayload вже не null) реально надсилає.
    // "Редактировать" - звичайна кнопка всередині сторінки, повертає до
    // form (нічого не втрачено - поля лишались у DOM, лише сховані).
    var formEl = document.getElementById("form");
    // Реальний баг (аудит коду, 2026-08-14): усі поля лежать усередині
    // голого <form> (index.html) без жодного обробника відправки - Enter у
    // БУДЬ-ЯКОМУ текстовому полі (порода/товщина/ширина/довжина/кількість/
    // клієнт/адреса/ціна) викликав стандартну відправку форми браузером
    // (перезавантаження сторінки), миттєво стираючи весь накопичений
    // кошик позицій без жодного попередження. Відправка й так завжди йде
    // через MainButton/fallback-submit нижче - справжньому <form>-submit
    // тут узагалі нема чого робити.
    formEl.addEventListener("submit", function (event) {
      event.preventDefault();
    });
    var confirmView = document.getElementById("confirm-view");
    var confirmSummaryEl = document.getElementById("confirm-summary");
    var confirmEditButton = document.getElementById("confirm-edit-button");
    var confirmPayload = null;

    // Задача користувача (скріншот екрана підтвердження): "Толщина/Ширина/
    // Длина" трьома окремими рядками замінити на компактний "50x100x6000"
    // (той самий формат, у якому розмір і так показується скрізь по боту).
    var DIMENSION_FIELD_KEYS = ["thickness", "width", "length"];

    function describePosition(key, values) {
      var state = categoryState[key];
      var cat = categories.filter(function (c) {
        return String(c.key) === key;
      })[0];
      var lines = [];
      if (cat) {
        lines.push(cat.label);
      }
      if (!state) {
        return lines;
      }
      var row = values.rows && values.rows[0];
      var dimensionLineAdded = false;
      // Задача користувача (скріншот екрана прихід-форми): "потрібно щоб
      // відразу рахувало і показувало одиницю вимірювання.. тобто скільки
      // це м3, чи м2, чи мп" - dimValues винесено в зовнішню (для forEach)
      // область видимості, щоб гілка "quantity" нижче могла порахувати
      // фізичний вимір по тим самим товщині/ширині/довжині.
      var dimValues = null;
      var quantityRawValue = null;
      state.fields.forEach(function (field) {
        if (DIMENSION_FIELD_KEYS.indexOf(field.key) !== -1) {
          if (dimensionLineAdded) {
            return;
          }
          dimensionLineAdded = true;
          dimValues = DIMENSION_FIELD_KEYS.map(function (dimKey) {
            var dimValue = row && field.per_row ? row[dimKey] : values[dimKey];
            return dimValue !== undefined && dimValue !== null && dimValue !== "" ? dimValue : null;
          });
          if (dimValues.every(function (v) { return v !== null; })) {
            lines.push("Размер: " + dimValues.join("x"));
          } else {
            DIMENSION_FIELD_KEYS.forEach(function (dimKey, index) {
              if (dimValues[index] === null) {
                return;
              }
              var dimField = state.fields.filter(function (f) { return f.key === dimKey; })[0];
              lines.push((dimField ? dimField.label : dimKey) + ": " + dimValues[index]);
            });
          }
          return;
        }
        var value = row && field.per_row ? row[field.key] : values[field.key];
        if (value !== undefined && value !== null && value !== "") {
          var lineText = field.label + ": " + value;
          if (field.key === "quantity") {
            quantityRawValue = value;
            if (dimValues && dimValues.every(function (v) { return v !== null; })) {
              var measureText = computeMeasureText(cat && cat.product, dimValues[0], dimValues[1], dimValues[2], value);
              if (measureText) {
                lineText += " — " + measureText;
              }
            }
          }
          if (field.key === "price_per_unit") {
            var priceUnit;
            var totalAmount = null;
            if (cat && cat.kind === "service") {
              priceUnit = "м3";
              totalAmount = numberOrZero(values.volume);
            } else {
              var priceKind = dimValues && dimValues.every(function (v) { return v !== null; })
                ? rowMeasureKind(cat && cat.product, dimValues[0], dimValues[1])
                : null;
              priceUnit = priceKind ? MEASURE_UNIT_BY_KIND[priceKind] : "шт";
              if (priceKind && dimValues && dimValues.every(function (v) { return v !== null; })) {
                totalAmount = pieceMeasure(dimValues[0], dimValues[1], dimValues[2], priceKind) * numberOrZero(quantityRawValue);
              } else if (quantityRawValue !== null) {
                totalAmount = numberOrZero(quantityRawValue);
              }
            }
            lineText += " MDL/" + priceUnit;
            var priceNum = numberOrZero(value);
            if (totalAmount !== null && priceNum > 0 && totalAmount > 0) {
              lineText += " — Сумма: " + formatMoney(priceNum * totalAmount) + " MDL";
            }
          }
          lines.push(lineText);
        }
      });
      return lines;
    }

    function describeCommon(values) {
      var lines = [];
      identityCommonFields.forEach(function (field) {
        var value = values[field.key];
        if (value !== undefined && value !== null && value !== "") {
          lines.push(field.label + ": " + value);
        }
      });
      return lines;
    }

    // Задача користувача (скріншот екрана підтвердження): назва категорії
    // (ДОСКА KD/ДОСКА AD/ОСБ...) - жирним, клієнт/адреса/спосіб оплати -
    // ненав'язливо (менший розмір + верхня риска-розділювач, той самий
    // прийом, що вже є в кошику). Раніше #confirm-summary заповнювався через
    // textContent (голий текст, без жодної розмітки) - тому побудова тепер
    // через реальні DOM-елементи з CSS-класами (.confirm-position-title/
    // .confirm-common), а не рядок - textContent на кожному рядку і так
    // природньо безпечний від XSS (значення клієнта/адреси - введені текстом
    // через людину, ніколи не як HTML).
    // Задача користувача: "антисептирование - додаткова послуга... має
    // відображатись весь список переліченого товару і знизу дані про
    // антисептирование... зверху цифри і дані про товар загалом, свій
    // підсумок, далі антисептик, свій підсумок, і в кінці загальний
    // підсумок" - antisepticAddon (необов'язковий) додає ОКРЕМИЙ блок після
    // усіх товарних позицій, не замінюючи їх (та сама причина, чому це
    // взагалі виправляється - раніше кошик просто губився).
    function computePositionTotal(key, values) {
      var cat = categories.filter(function (c) { return String(c.key) === key; })[0];
      var row = values.rows && values.rows[0];
      var thickness = row ? row.thickness : values.thickness;
      var width = row ? row.width : values.width;
      var length = row ? row.length : values.length;
      var quantity = row ? row.quantity : values.quantity;
      var price = numberOrZero(values.price_per_unit);
      if (price <= 0) {
        return 0;
      }
      if (thickness == null || width == null) {
        return price * numberOrZero(quantity);
      }
      var kind = rowMeasureKind(cat && cat.product, thickness, width);
      if (!kind) {
        return price * numberOrZero(quantity);
      }
      return pieceMeasure(thickness, width, length, kind) * numberOrZero(quantity) * price;
    }

    // Задача користувача: "чому розпізнало лише 1 антисептирование, якщо я
    // 2 антисептіровав?" - antisepticAddon-параметр (єдиний, глобальний)
    // видалено - тепер КОЖНА position може нести ВЛАСНИЙ position.antiseptic
    // (записується в addPositionButton/submit нижче), тож рахуємо/показуємо
    // антисептирование по КОЖНІЙ позиції окремо, а не лише по останній.
    function buildSummaryElement(positions, commonValues) {
      var container = document.createElement("div");
      var goodsTotal = 0;
      var antisepticTotal = 0;
      var anyAntiseptic = false;
      positions.forEach(function (position) {
        goodsTotal += computePositionTotal(String(position.category_operation_id), position);
        var lines = describePosition(String(position.category_operation_id), position);
        if (!lines.length) {
          return;
        }
        var block = document.createElement("div");
        block.className = "confirm-position";
        lines.forEach(function (text, index) {
          var row = document.createElement("div");
          row.className = index === 0 ? "confirm-position-title" : "confirm-position-line";
          row.textContent = text;
          block.appendChild(row);
        });
        var addon = position.antiseptic;
        if (addon && addon.volume && addon.price_per_unit) {
          var positionAntisepticSum = addon.volume * addon.price_per_unit;
          antisepticTotal += positionAntisepticSum;
          anyAntiseptic = true;
          var addonRow = document.createElement("div");
          addonRow.className = "confirm-position-line";
          addonRow.textContent = "Антисептировано: " + formatServerNumber(addon.volume) + " м3 — " + formatMoney(positionAntisepticSum) + " MDL";
          block.appendChild(addonRow);
        }
        container.appendChild(block);
      });
      // Задача користувача (скріншот екрана підтвердження): "клієнт/адреса/
      // оплата - завжди над сумами, сума по антисептированию - завжди над
      // сумою по товару, сума по товару - завжди над Итого, Итого - в
      // самому кінці" - фіксований порядок блоків, а не той, у якому вони
      // історично додавались у код.
      var commonLines = describeCommon(commonValues);
      if (commonLines.length) {
        var commonBlock = document.createElement("div");
        commonBlock.className = "confirm-common";
        commonLines.forEach(function (text) {
          var row = document.createElement("div");
          row.textContent = text;
          commonBlock.appendChild(row);
        });
        container.appendChild(commonBlock);
      }
      var showTotals = positions.length > 1 && goodsTotal > 0;
      if (anyAntiseptic) {
        var antisepticTotalRow = document.createElement("div");
        antisepticTotalRow.className = "confirm-common";
        antisepticTotalRow.textContent = "Сумма по антисептированию: " + formatMoney(antisepticTotal) + " MDL";
        container.appendChild(antisepticTotalRow);
      }
      if (showTotals) {
        var goodsTotalRow = document.createElement("div");
        goodsTotalRow.className = "confirm-common";
        goodsTotalRow.textContent = "Сумма по товару: " + formatMoney(goodsTotal) + " MDL";
        container.appendChild(goodsTotalRow);

        var grandTotalRow = document.createElement("div");
        grandTotalRow.className = "confirm-common";
        grandTotalRow.textContent = "Итого: " + formatMoney(goodsTotal + antisepticTotal) + " MDL";
        container.appendChild(grandTotalRow);
      }
      return container;
    }

    // Задача користувача: "фінальне вікно перевірки, де всі інформація про
    // антисепт буде красиво препіднесена" - ОКРЕМА (не buildSummaryElement
    // вище, яка побудована навколо "товар+необов'язковий антисептик-
    // доповнення") функція, бо тут ціна/сума рахуються від фізичного об'єму,
    // а не від рядкового product measure kind (buildSummaryElement/
    // describePosition/computePositionTotal читають ціну як MDL/шт чи MDL/
    // (м3 залежно від товару) - для антисептирования ціна завжди MDL/м3,
    // незалежно від того, як саме продається сам товар).
    function buildAntisepticSummaryElement(position, commonValues, volume) {
      var container = document.createElement("div");
      var block = document.createElement("div");
      block.className = "confirm-position";

      var cat = categories.filter(function (c) {
        return String(c.key) === String(position.category_operation_id);
      })[0];
      var titleRow = document.createElement("div");
      titleRow.className = "confirm-position-title";
      titleRow.textContent = "Антисептирование" + (cat ? " — " + cat.label : "");
      block.appendChild(titleRow);

      if (position.breed) {
        var breedRow = document.createElement("div");
        breedRow.className = "confirm-position-line";
        breedRow.textContent = "Порода: " + position.breed;
        block.appendChild(breedRow);
      }
      var row = position.rows && position.rows[0];
      if (row) {
        var sizeRow = document.createElement("div");
        sizeRow.className = "confirm-position-line";
        sizeRow.textContent = "Размер: " + [row.thickness, row.width, row.length].join("x") +
          " — Штук: " + row.quantity;
        block.appendChild(sizeRow);
      }
      var volumeRow = document.createElement("div");
      volumeRow.className = "confirm-position-line";
      volumeRow.textContent = "Объём: " + formatServerNumber(volume) + " м3";
      block.appendChild(volumeRow);

      // Задача користувача: "додай змогу ще додавати доски до продажі
      // послуги" - ціна переїхала НА позицію (не спільна), тож тепер
      // читається з position, а не з commonValues.
      var price = numberOrZero(position.price_per_unit);
      if (price > 0) {
        var priceRow = document.createElement("div");
        priceRow.className = "confirm-position-line";
        priceRow.textContent = "Цена: " + formatServerNumber(price) + " MDL/м3";
        block.appendChild(priceRow);
      }
      container.appendChild(block);

      // Задача користувача (скріншот): "сумма має бути завжди знизу,
      // скрізь" - той самий порядок, що вже узгоджено для продажу: спільні
      // поля (клієнт/адреса/оплата) ПЕРЕД сумою, сама сума - останньою.
      var commonLines = describeCommon(commonValues);
      if (commonLines.length) {
        var commonBlock = document.createElement("div");
        commonBlock.className = "confirm-common";
        commonLines.forEach(function (text) {
          var lineRow = document.createElement("div");
          lineRow.textContent = text;
          commonBlock.appendChild(lineRow);
        });
        container.appendChild(commonBlock);
      }

      if (price > 0) {
        var sumRow = document.createElement("div");
        sumRow.className = "confirm-common";
        sumRow.textContent = "Сумма: " + formatMoney(price * volume) + " MDL";
        container.appendChild(sumRow);
      }
      return container;
    }

    // Задача користувача: "додай змогу ще додавати для одного клієнта
    // доски до продажі послуги, так як це реалізовано в продажі
    // пиломатеріалу" - кілька дощок одному клієнту, кожна зі своєю ціною/
    // сумою, спільна лише Клиент/Адрес/Оплата, і загальна сума в кінці -
    // той самий порядок, що вже узгоджено для продажу й одиночного
    // антисептирования вище.
    function buildAntisepticMultiSummaryElement(positions, commonValues) {
      var container = document.createElement("div");
      var totalVolume = 0;
      var totalSum = 0;
      positions.forEach(function (position) {
        var block = document.createElement("div");
        block.className = "confirm-position";

        var cat = categories.filter(function (c) {
          return String(c.key) === String(position.category_operation_id);
        })[0];
        var titleRow = document.createElement("div");
        titleRow.className = "confirm-position-title";
        titleRow.textContent = (cat ? cat.label : "Антисептирование") + (position.breed ? " / " + position.breed : "");
        block.appendChild(titleRow);

        var row = position.rows && position.rows[0];
        var volume = antisepticVolumeFor(position);
        totalVolume += volume;
        if (row) {
          var sizeRow = document.createElement("div");
          sizeRow.className = "confirm-position-line";
          sizeRow.textContent = [row.thickness, row.width, row.length].join("x") +
            " — " + row.quantity + " шт — " + formatServerNumber(volume) + " м3";
          block.appendChild(sizeRow);
        }
        var price = numberOrZero(position.price_per_unit);
        if (price > 0) {
          var positionSum = price * volume;
          totalSum += positionSum;
          var priceRow = document.createElement("div");
          priceRow.className = "confirm-position-line";
          priceRow.textContent = "Цена: " + formatServerNumber(price) + " MDL/м3 — Сумма: " + formatMoney(positionSum) + " MDL";
          block.appendChild(priceRow);
        }
        container.appendChild(block);
      });

      var commonLines = describeCommon(commonValues);
      if (commonLines.length) {
        var commonBlock = document.createElement("div");
        commonBlock.className = "confirm-common";
        commonLines.forEach(function (text) {
          var lineRow = document.createElement("div");
          lineRow.textContent = text;
          commonBlock.appendChild(lineRow);
        });
        container.appendChild(commonBlock);
      }

      if (totalSum > 0) {
        var sumRow = document.createElement("div");
        sumRow.className = "confirm-common";
        sumRow.textContent = "Сумма: " + formatMoney(totalSum) + " MDL";
        container.appendChild(sumRow);
      }
      return container;
    }

    function showConfirm(payload, summaryElement) {
      confirmPayload = payload;
      confirmSummaryEl.innerHTML = "";
      confirmSummaryEl.appendChild(summaryElement);
      errorEl.textContent = "";
      formEl.style.display = "none";
      confirmView.style.display = "";
    }

    function hideConfirm() {
      confirmPayload = null;
      confirmView.style.display = "none";
      formEl.style.display = "";
      updateAddPositionVisibility();
    }

    confirmEditButton.addEventListener("click", hideConfirm);

    function collectField(field, input) {
      var value = readFieldValue(input);
      var fieldWrap = input.closest(".field");
      fieldWrap.classList.remove("invalid");
      if (value === "") {
        if (field.required !== false) {
          fieldWrap.classList.add("invalid");
          return { ok: false, missing: true };
        }
        return { ok: true };
      }
      return { ok: true, value: field.numeric ? Number(String(value).replace(",", ".")) : value };
    }

    // Задача користувача ("продовжити оплату/продажу/списання... щоб
    // зібрати в кучу через зручність, а не змішувати це зі старим"):
    // застосунок сам накопичує кілька позицій - жодного контакту з ботом
    // до фінального "Отправить". collectCategoryFields повертає значення
    // ОДНІЄЇ категорії (розмір+кількість чи об'єм, +ціна) - те саме, що
    // раніше йшло напряму в payload, тепер - будівельний блок як для
    // однієї "позиції" кошика, так і для антисептирования (яке кошик не
    // підтримує, лишається одноразовим поданням).
    function collectCategoryFields(key) {
      var state = categoryState[key];
      if (!state) {
        return { ok: false };
      }
      var values = {};
      var missingAny = false;
      var filledAny = false;
      var perRowKeys = Object.keys(state.rowInputs);
      if (perRowKeys.length) {
        var row = {};
        perRowKeys.forEach(function (rowKey) {
          var field = state.fields.filter(function (f) {
            return f.key === rowKey;
          })[0];
          var result = collectField(field, state.rowInputs[rowKey]);
          if (result.value !== undefined) {
            filledAny = true;
            row[rowKey] = result.value;
          } else if (result.missing) {
            missingAny = true;
          }
        });
        if (filledAny) {
          values.rows = [row];
        }
      }
      Object.keys(state.flatInputs).forEach(function (flatKey) {
        var field = state.fields.filter(function (f) {
          return f.key === flatKey;
        })[0];
        var result = collectField(field, state.flatInputs[flatKey]);
        if (result.value !== undefined) {
          filledAny = true;
          values[flatKey] = result.value;
        } else if (result.missing) {
          missingAny = true;
        }
      });
      if (!filledAny) {
        return { ok: true, empty: true, values: values };
      }
      return { ok: !missingAny, empty: false, values: values };
    }

    function clearCategoryInputs(key) {
      var state = categoryState[key];
      if (!state) {
        return;
      }
      Object.keys(state.rowInputs).forEach(function (rowKey) {
        var input = state.rowInputs[rowKey];
        input.value = "";
        if (input.manualInput) {
          input.manualInput.value = "";
        }
      });
      Object.keys(state.flatInputs).forEach(function (flatKey) {
        var input = state.flatInputs[flatKey];
        input.value = "";
        if (input.manualInput) {
          input.manualInput.value = "";
        }
      });
    }

    function buildPosition(key, values) {
      var numericKey = Number(key);
      var position = { category_operation_id: isNaN(numericKey) ? key : numericKey };
      Object.keys(values).forEach(function (valueKey) {
        position[valueKey] = values[valueKey];
      });
      return position;
    }

    // Задача користувача: "якщо на складі недостатньо кількості для
    // операції - не дозволяти відправити, вказувати що максимальна
    // кількість недопустима, ви вводите стільки, а є тільки" - остання
    // лінія захисту ПЕРЕД відправкою (сервер усе одно перевіряє те саме
    // жорстко, це лише щоб людина побачила проблему одразу в формі, а не
    // після відправки). Залишок читається з cat.dimension_combos (5-й
    // елемент кожної трійки-тепер-п'ятірки [порода,товщина,ширина,довжина,
    // залишок], telegram_dialog_core.py) - окремої "stock_balances"-мапи
    // більше нема (об'єднано в один compact-структурований масив, щоб не
    // роздувати розмір web_app-URL). Невідома (ручний allow_custom розмір)
    // комбінація -> {ok:true} - остаточне слово однаково лишається за
    // сервером.
    function stockSufficiencyCheck(key, values) {
      var cat = categories.filter(function (c) {
        return String(c.key) === key;
      })[0];
      var combos = cat && cat.dimension_combos;
      var row = values.rows && values.rows[0];
      if (!combos || !combos.length || !row || row.quantity === undefined || row.quantity === null) {
        return { ok: true };
      }
      // collectField уже перетворила числові поля на справжні JS Number
      // (Number("12,5".replace(",", "."))) - серверні combos ключуються
      // РЯДКАМИ у форматі _display_bot_number (кома, без .0 для цілих) -
      // formatServerNumber повертає це саме форматування, щоб значення
      // співпали буквально.
      var available = findComboBalance(
        combos,
        values.breed,
        formatServerNumber(row.thickness),
        formatServerNumber(row.width),
        formatServerNumber(row.length)
      );
      if (available === null) {
        return { ok: true };
      }
      if (Number(row.quantity) > available) {
        return { ok: false, available: available };
      }
      return { ok: true };
    }

    function positionSummaryText(key, values) {
      var cat = categories.filter(function (c) {
        return String(c.key) === key;
      })[0];
      var label = cat ? cat.label : key;
      var sizeText = "";
      if (values.rows && values.rows[0]) {
        var row = values.rows[0];
        var dims = [row.thickness, row.width, row.length].filter(function (v) {
          return v !== undefined && v !== null;
        });
        sizeText = dims.join("x");
        if (row.quantity) {
          sizeText += " × " + row.quantity;
        }
        // Задача користувача (скріншот): "ні в антисептировании ні в
        // продажі немає сумми так як на 3 скріні" - той самий вимір
        // (м3/м2/мп), що вже показує кошик антисептирования, тепер і в
        // кошику продажу (measure-aware computeMeasureText - коректний і
        // для площинних/погонних товарів, не лише кубатури).
        var measureText = computeMeasureText(cat && cat.product, row.thickness, row.width, row.length, row.quantity);
        if (measureText) {
          sizeText += " — " + measureText;
        }
      } else if (values.volume) {
        sizeText = values.volume + " м3";
      }
      return label + (sizeText ? ", " + sizeText : "");
    }

    function categoryKind(key) {
      var cat = categories.filter(function (c) {
        return String(c.key) === key;
      })[0];
      return cat ? cat.kind : null;
    }

    // Задача користувача (реагуючи на "Добавить позицию" на реальному
    // скріншоті): "не логічніше продолжить продажу?" - той самий термін,
    // яким уже названо аналогічну дію в старому покроковому чат-флоу
    // (кнопка "Продолжить" на екрані підтвердження) - тут це та сама дія
    // ("зберегти цю позицію, дати наступну"), лише в мега-формі.
    // Задача користувача: "все має бути компактно... цей напись разом із
    // знаками має бути десь зверху" - кошик тепер ЗАВЖДИ перший блок форми
    // (над "Категория"), а не десь усередині між полями - як звичний
    // "кошик покупок" зверху сторінки. Приховано (display:none) поки
    // кошик порожній - не займає місця, доки нема жодної позиції.
    var cart = [];
    var cartSection = document.createElement("div");
    cartSection.className = "cart-section";
    cartSection.style.display = "none";
    var cartHeaderEl = document.createElement("div");
    cartHeaderEl.className = "cart-header";
    cartHeaderEl.textContent = "Добавлено:";
    var cartListEl = document.createElement("div");
    cartListEl.className = "cart-list";
    cartSection.appendChild(cartHeaderEl);
    cartSection.appendChild(cartListEl);
    measureContainer.parentNode.insertBefore(cartSection, categoryWrap);

    // setFieldValue - обернена дія до readFieldValue: повертає збережене
    // значення позиції НАЗАД у поле форми (select чи звичайний input) -
    // потрібно для "✎" (повернутись і змінити дані вже доданої позиції).
    function setFieldValue(input, value) {
      if (value === undefined || value === null || value === "") {
        return;
      }
      var stringValue = String(value);
      if (input.tagName === "SELECT") {
        var matched = false;
        for (var i = 0; i < input.options.length; i++) {
          if (input.options[i].value === stringValue) {
            input.value = stringValue;
            matched = true;
            break;
          }
        }
        if (matched) {
          if (input.manualInput) {
            input.manualInput.value = "";
          }
          return;
        }
        if (input.manualInput) {
          input.value = "";
          input.manualInput.value = stringValue;
          return;
        }
      }
      input.value = stringValue;
    }

    function populateCategoryFields(key, position) {
      var state = categoryState[key];
      if (!state) {
        return;
      }
      var row = (position.rows && position.rows[0]) || {};
      Object.keys(state.rowInputs).forEach(function (rowKey) {
        setFieldValue(state.rowInputs[rowKey], row[rowKey]);
      });
      Object.keys(state.flatInputs).forEach(function (flatKey) {
        setFieldValue(state.flatInputs[flatKey], position[flatKey]);
      });
    }

    // Реальний баг (живий продакшн): "жму продолжить, потім повертаюсь
    // назад - скидається вибране антисептирование". populateCategoryFields
    // вище відновлює лише row/flat-поля (товщина/ширина/довжина/кількість/
    // ціна) - чекбокс і поля антисептирования лишались у тому скинутому
    // стані, в якому їх залишило "Продолжить" (рядки 2321-2323 нижче), тож
    // "✎" на позиції З антисептированием показувала чекбокс НЕзнятим.
    // Якщо людина натискала "Продолжить" ще раз, не помітивши цього,
    // collectAntisepticAddon() повертав null - антисептирование тихо
    // губилось із уже доданої позиції. position.antiseptic несе лише
    // ПІДСУМКОВИЙ volume/price_per_unit (не сирий текст поля "штук") -
    // кількість, яку антисептировали, відновлюємо назад діленням об'єму на
    // об'єм одиниці; якщо вона дорівнює всій кількості рядка, лишаємо поле
    // порожнім (те саме "пусто = все", що й при першому введенні).
    function restoreAntisepticAddon(position) {
      var addon = position.antiseptic;
      if (!addon) {
        antisepticCheckbox.checked = false;
        antisepticPriceInput.value = "";
        antisepticQtyInput.value = "";
        refreshAntisepticBlock();
        return;
      }
      antisepticCheckbox.checked = true;
      antisepticPriceInput.value = addon.price_per_unit != null ? String(addon.price_per_unit) : "";
      antisepticQtyInput.value = "";
      var row = (position.rows && position.rows[0]) || {};
      var t = parseLocaleNumber(String(row.thickness));
      var w = parseLocaleNumber(String(row.width));
      var l = parseLocaleNumber(String(row.length));
      var q = parseLocaleNumber(String(row.quantity));
      if (t && w && l && q && addon.volume) {
        var unitVolume = (t * w * l) / 1e9;
        if (unitVolume > 0) {
          var treatQty = addon.volume / unitVolume;
          if (Math.abs(treatQty - q) > 0.001) {
            antisepticQtyInput.value = String(Math.round(treatQty * 1000) / 1000);
          }
        }
      }
      refreshAntisepticBlock();
    }

    function editCartItem(index) {
      var item = cart[index];
      if (!item) {
        return;
      }
      cart.splice(index, 1);
      var key = String(item.position.category_operation_id);
      categorySelect.value = key;
      categorySelect.dispatchEvent(new Event("change", { bubbles: true }));
      populateCategoryFields(key, item.position);
      restoreAntisepticAddon(item.position);
      errorEl.textContent = "";
      renderCart();
    }

    function removeCartItem(index) {
      cart.splice(index, 1);
      renderCart();
    }

    function renderCart() {
      cartListEl.innerHTML = "";
      cartSection.style.display = cart.length ? "" : "none";
      cart.forEach(function (item, index) {
        var row = document.createElement("div");
        row.className = "cart-item";
        var textWrap = document.createElement("div");
        textWrap.className = "cart-item-text-wrap";
        var text = document.createElement("span");
        text.className = "cart-item-text";
        text.textContent = (index + 1) + ". " + item.summary;
        textWrap.appendChild(text);
        // Задача користувача (скріншот): "суму окремо після кубатури,
        // акуратненько" - другий, приглушений рядок ПІД основним текстом
        // (антисептирование-позиції несуть sumText, cart.push вище).
        if (item.sumText) {
          var sumLine = document.createElement("span");
          sumLine.className = "cart-item-sum";
          sumLine.textContent = item.sumText;
          textWrap.appendChild(sumLine);
        }
        row.appendChild(textWrap);

        var antisepticAddon = item.position && item.position.antiseptic;
        if (antisepticAddon) {
          var badge = document.createElement("span");
          badge.className = "cart-item-badge";
          badge.textContent = "Антисептировано";
          row.appendChild(badge);
        }

        var actions = document.createElement("div");
        actions.className = "cart-item-actions";

        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "cart-item-btn";
        editBtn.textContent = "✎";
        editBtn.addEventListener("click", function () {
          editCartItem(index);
        });
        actions.appendChild(editBtn);

        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "cart-item-btn cart-item-btn-remove";
        removeBtn.textContent = "✕";
        removeBtn.addEventListener("click", function () {
          removeCartItem(index);
        });
        actions.appendChild(removeBtn);

        row.appendChild(actions);
        cartListEl.appendChild(row);
      });
    }

    // "Продолжить продажу" - лише для категорій виду "sale" (антисептик
    // мультипозиційність не підтримує - завжди одна, самодостатня заявка).
    var addPositionButton = document.createElement("button");
    addPositionButton.type = "button";
    addPositionButton.className = "add-position-button";
    addPositionButton.textContent = "Продолжить продажу";
    measureContainer.parentNode.insertBefore(addPositionButton, measureContainer.nextSibling);
    // "Сохранить как шаблон" тепер одразу ПЕРЕД "Продолжить продажу" (за
    // проханням користувача перенести кнопку донизу форми).
    measureContainer.parentNode.insertBefore(saveTemplateButton, addPositionButton);

    // Задача користувача: "змісти кнопку антисептирование вище ціни, між
    // штуками і ціною" - antisepticWrap (чекбокс + розкривний блок) тепер
    // живе ВСЕРЕДИНІ активного measureBlock (переноситься relocateAntiseptic
    // Wrap нижче при зміні категорії), одразу після rowBlock (товщина/
    // ширина/довжина/штук) і ПЕРЕД полем ціни. Об'єм рахується автоматично
    // з тих самих товщини/ширини/довжини/кількості, що вже введені для
    // товару - лише ціна за м3 послуги (і, за новим проханням, кількість
    // штук ДЛЯ антисептирования, якщо не всі) питаються окремо.
    var antisepticWrap = document.createElement("div");
    antisepticWrap.className = "field antiseptic-wrap";
    var antisepticLabel = document.createElement("label");
    antisepticLabel.className = "antiseptic-checkbox-label";
    var antisepticCheckbox = document.createElement("input");
    antisepticCheckbox.type = "checkbox";
    antisepticLabel.appendChild(antisepticCheckbox);
    var antisepticLabelText = document.createElement("span");
    antisepticLabelText.textContent = "Антисептирование";
    antisepticLabel.appendChild(antisepticLabelText);
    antisepticWrap.appendChild(antisepticLabel);

    var antisepticBlock = document.createElement("div");
    antisepticBlock.className = "antiseptic-block";
    antisepticBlock.style.display = "none";
    var antisepticVolumeLine = document.createElement("div");
    antisepticVolumeLine.className = "antiseptic-volume-line";
    antisepticBlock.appendChild(antisepticVolumeLine);
    // Задача користувача: "додай можливість вибрати скільки з продукції
    // антисептировать штук, чи всі що вказані в продажі" - порожнє поле =
    // "всі" (повна кількість рядка), число = скільки саме штук обробити
    // (не більше, ніж загальна кількість - обмежуємо в refreshAntisepticBlock).
    var antisepticQtyRow = document.createElement("div");
    antisepticQtyRow.className = "antiseptic-price-row";
    var antisepticQtyLabel = document.createElement("span");
    antisepticQtyLabel.textContent = "Штук на антисептирование (пусто = все):";
    var antisepticQtyInput = document.createElement("input");
    antisepticQtyInput.type = "text";
    antisepticQtyInput.inputMode = "numeric";
    antisepticQtyInput.placeholder = "все";
    antisepticQtyRow.appendChild(antisepticQtyLabel);
    antisepticQtyRow.appendChild(antisepticQtyInput);
    antisepticBlock.appendChild(antisepticQtyRow);
    var antisepticPriceRow = document.createElement("div");
    antisepticPriceRow.className = "antiseptic-price-row";
    var antisepticPriceLabel = document.createElement("span");
    var antisepticPriceFieldLabel = ((antisepticCategory && antisepticCategory.fields) || []).filter(
      function (f) { return f.key === "price_per_unit"; }
    )[0];
    antisepticPriceLabel.textContent = (antisepticPriceFieldLabel ? antisepticPriceFieldLabel.label : "Цена антисептирования, MDL/м3") + ":";
    var antisepticPriceInput = document.createElement("input");
    antisepticPriceInput.type = "text";
    antisepticPriceInput.inputMode = "decimal";
    antisepticPriceRow.appendChild(antisepticPriceLabel);
    antisepticPriceRow.appendChild(antisepticPriceInput);
    antisepticBlock.appendChild(antisepticPriceRow);
    var antisepticSumLine = document.createElement("div");
    antisepticSumLine.className = "antiseptic-sum-line";
    antisepticBlock.appendChild(antisepticSumLine);
    antisepticWrap.appendChild(antisepticBlock);

    // Задача користувача: "потім ціна за товар, яка сума за товар,
    // підкреслено і все загалом" - "Сумма за товар" (лише товар, завжди
    // видима, коли ціна вже введена) і "Итого" (товар+антисептик, з
    // підкресленням-роздільником, лише коли антисептик увімкнено) - обидві
    // лишаються поза measureBlock (звичайні сиблінги після #rows), бо
    // фізично опиняються одразу під ціною (останнім полем видимого
    // measureBlock) незалежно від того, яка категорія зараз активна.
    var goodsSumLine = document.createElement("div");
    goodsSumLine.className = "goods-sum-line";
    goodsSumLine.style.display = "none";
    var positionTotalLine = document.createElement("div");
    positionTotalLine.className = "position-total-line";
    positionTotalLine.style.display = "none";
    measureContainer.parentNode.insertBefore(goodsSumLine, saveTemplateButton);
    measureContainer.parentNode.insertBefore(positionTotalLine, saveTemplateButton);

    relocateAntisepticWrap = function (key) {
      var state = categoryState[key];
      if (!state) {
        return;
      }
      if (state.rowBlock) {
        state.measureBlock.insertBefore(antisepticWrap, state.rowBlock.nextSibling);
      } else {
        state.measureBlock.insertBefore(antisepticWrap, state.measureBlock.firstChild);
      }
    };
    relocateAntisepticWrap(categorySelect.value);

    function parseLocaleNumber(raw) {
      if (raw === undefined || raw === null || raw === "") {
        return null;
      }
      var num = parseFloat(String(raw).replace(",", "."));
      return isNaN(num) ? null : num;
    }

    // Об'єм для антисептирования - завжди фізичний м3 (товщина×ширина×
    // довжина×кількість), незалежно від того, у чому вимірюється сам товар
    // (продаж може йти в м2/мп) - антисептирование обробляє реальний об'єм
    // дерева, а не одиницю, у якій його продають. Задача користувача: "щоб
    // можна було вибрати скільки штук антисептировать, чи всі" - порожнє
    // antisepticQtyInput = вся кількість рядка; введене число обрізається
    // до цієї кількості (не можна обробити більше, ніж продано).
    function currentAntisepticVolume() {
      var key = categorySelect.value;
      var state = categoryState[key];
      if (!state) {
        return null;
      }
      var t = state.rowInputs.thickness ? parseLocaleNumber(readFieldValue(state.rowInputs.thickness)) : null;
      var w = state.rowInputs.width ? parseLocaleNumber(readFieldValue(state.rowInputs.width)) : null;
      var l = state.rowInputs.length ? parseLocaleNumber(readFieldValue(state.rowInputs.length)) : null;
      var q = state.rowInputs.quantity ? parseLocaleNumber(readFieldValue(state.rowInputs.quantity)) : null;
      if (t === null || w === null || l === null || q === null) {
        return null;
      }
      var treatQty = q;
      var treatRaw = antisepticQtyInput.value.trim();
      if (treatRaw !== "") {
        var parsedTreat = parseLocaleNumber(treatRaw);
        if (parsedTreat !== null && parsedTreat >= 0) {
          treatQty = Math.min(parsedTreat, q);
        }
      }
      return (t * w * l) / 1e9 * treatQty;
    }

    function currentGoodsPositionTotal() {
      var key = categorySelect.value;
      var state = categoryState[key];
      if (!state) {
        return 0;
      }
      var t = state.rowInputs.thickness ? parseLocaleNumber(readFieldValue(state.rowInputs.thickness)) : null;
      var w = state.rowInputs.width ? parseLocaleNumber(readFieldValue(state.rowInputs.width)) : null;
      var l = state.rowInputs.length ? parseLocaleNumber(readFieldValue(state.rowInputs.length)) : null;
      var q = state.rowInputs.quantity ? parseLocaleNumber(readFieldValue(state.rowInputs.quantity)) : null;
      var priceRaw = state.flatInputs.price_per_unit ? readFieldValue(state.flatInputs.price_per_unit) : null;
      return computePositionTotal(key, {
        rows: [{ thickness: t, width: w, length: l, quantity: q }],
        price_per_unit: priceRaw,
      });
    }

    // Задача користувача: "потім ціна за товар, яка сума за товар" - ця
    // сума показується ЗАВЖДИ (незалежно від чекбокса антисептирования),
    // одразу під ціною - на відміну від "Итого", яка з'являється лише
    // разом з антисептированием (без нього "Итого" дублювало б цю ж суму).
    function refreshGoodsSumLine() {
      var goodsTotal = currentGoodsPositionTotal();
      if (goodsTotal > 0) {
        goodsSumLine.style.display = "";
        goodsSumLine.textContent = "Сумма за товар: " + formatMoney(goodsTotal) + " MDL";
      } else {
        goodsSumLine.style.display = "none";
        goodsSumLine.textContent = "";
      }
      return goodsTotal;
    }

    function refreshAntisepticBlock() {
      var goodsTotal = refreshGoodsSumLine();
      if (!antisepticCheckbox.checked) {
        antisepticBlock.style.display = "none";
        antisepticBlock.classList.remove("antiseptic-block-visible");
        positionTotalLine.style.display = "none";
        return;
      }
      var wasHidden = antisepticBlock.style.display === "none";
      antisepticBlock.style.display = "";
      positionTotalLine.style.display = "";
      if (wasHidden) {
        // Спершу зсунутий вниз/прозорий стан (щоб було ЩО анімувати), і лише
        // на наступному кадрі - реальний стан "видимо" - інакше браузер
        // застосує обидва класи за один раз, і transition просто не встигне
        // спрацювати (стрибок замість плавної появи).
        antisepticBlock.classList.add("antiseptic-block-enter");
        antisepticBlock.classList.remove("antiseptic-block-visible");
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            antisepticBlock.classList.remove("antiseptic-block-enter");
            antisepticBlock.classList.add("antiseptic-block-visible");
          });
        });
      }
      var volume = currentAntisepticVolume();
      if (volume === null) {
        antisepticVolumeLine.textContent = "Заполните толщину/ширину/длину/количество товара выше.";
        antisepticSumLine.textContent = "";
        positionTotalLine.textContent = "";
        return;
      }
      antisepticVolumeLine.textContent = "Объём для антисептирования: " + formatServerNumber(volume) + " м3";
      var price = parseLocaleNumber(antisepticPriceInput.value);
      var antisepticSum = price !== null && price > 0 ? volume * price : 0;
      antisepticSumLine.textContent = antisepticSum > 0
        ? "Сумма антисептирования: " + formatMoney(antisepticSum) + " MDL"
        : "";
      positionTotalLine.textContent = "Итого: " + formatMoney(goodsTotal + antisepticSum) + " MDL";
    }

    antisepticCheckbox.addEventListener("change", refreshAntisepticBlock);
    antisepticPriceInput.addEventListener("input", refreshAntisepticBlock);
    antisepticQtyInput.addEventListener("input", refreshAntisepticBlock);
    categorySelect.addEventListener("change", refreshAntisepticBlock);
    ["thickness", "width", "length", "quantity"].forEach(function (dimKey) {
      Object.keys(categoryState).forEach(function (key) {
        var input = categoryState[key].rowInputs[dimKey];
        if (!input) {
          return;
        }
        input.addEventListener("input", refreshAntisepticBlock);
        input.addEventListener("change", refreshAntisepticBlock);
        if (input.manualInput) {
          input.manualInput.addEventListener("input", refreshAntisepticBlock);
          input.manualInput.addEventListener("change", refreshAntisepticBlock);
        }
      });
    });
    // "Сумма за товар"/"Итого" рахуються від ціни товару (price_per_unit) -
    // без цього листенера вони оновлювались би лише на зміну розмірів/
    // кількості, але не самої ціни.
    Object.keys(categoryState).forEach(function (key) {
      var priceInput = categoryState[key].flatInputs.price_per_unit;
      if (!priceInput) {
        return;
      }
      priceInput.addEventListener("input", refreshAntisepticBlock);
      priceInput.addEventListener("change", refreshAntisepticBlock);
      if (priceInput.manualInput) {
        priceInput.manualInput.addEventListener("input", refreshAntisepticBlock);
        priceInput.manualInput.addEventListener("change", refreshAntisepticBlock);
      }
    });

    // "Антисептирование" видиме лише для категорій виду "sale" (списання/
    // прихід - інша логіка, без клієнта/оплати, антисептик як доп. послуга
    // там не має сенсу).
    // Задача користувача: "антисептируеться може лише доска. Вагонка і ОСБ -
    // не антисептируются" - доповнення видиме лише для sale-категорій
    // ТОВАРУ "Доска" (AD/KD), той самий normalizeProductPhrase/порівняння,
    // що вже класифікує товар по всьому файлу (AREA_BASED_PRODUCTS і т.д.).
    function categoryProduct(key) {
      var cat = categories.filter(function (c) {
        return String(c.key) === key;
      })[0];
      return cat ? cat.product : null;
    }
    function updateAntisepticVisibility() {
      var visible = categoryKind(categorySelect.value) === "sale"
        && normalizeProductPhrase(categoryProduct(categorySelect.value)) === "доска";
      antisepticWrap.style.display = visible ? "" : "none";
      if (!visible) {
        antisepticCheckbox.checked = false;
        refreshAntisepticBlock();
      }
    }

    // Читає поточний стан чекбокса/об'єму/ціни без валідації (на відміну
    // від collectCategoryFields) - викликається лише в момент submit(),
    // повертає null, якщо чекбокс не позначений чи дані невалідні (тоді
    // антисептирование просто не додається до подання, без блокування
    // самого продажу).
    function collectAntisepticAddon() {
      if (!antisepticCheckbox.checked) {
        return null;
      }
      var volume = currentAntisepticVolume();
      var price = parseLocaleNumber(antisepticPriceInput.value);
      if (volume === null || volume <= 0 || price === null || price <= 0) {
        return null;
      }
      return { volume: volume, price_per_unit: price };
    }

    // Об'єм дошки для кошика антисептирования (та сама формула, що й
    // wireAntisepticVolumeHint/currentAntisepticVolume) - потрібен ОКРЕМО
    // тут, бо ціна тепер НА КОЖНІЙ позиції (не спільна), тож суму позиції
    // можна порахувати одразу в момент додавання в кошик.
    // Аудит коду (minor, 2026-08-14): та сама формула (товщина*ширина*
    // довжина/1e9*кількість), що й utils.piece_measure(measure_kind=
    // "volume") у Python - продубльована навмисно, не помилково: цей
    // об'єм показується НАЖИВО, поки користувач вводить розміри, тож
    // мережевий round-trip на кожне натискання клавіші зробив би форму
    // помітно повільною. Якщо ЦЯ формула колись зміниться - обов'язково
    // перевір і оновити ту саму формулу в utils.py::piece_measure.
    function antisepticVolumeFor(values) {
      var row = values.rows && values.rows[0];
      if (!row) {
        return 0;
      }
      return (numberOrZero(row.thickness) * numberOrZero(row.width) * numberOrZero(row.length))
        / 1e9 * numberOrZero(row.quantity);
    }

    // Задача користувача (скріншот): "суму окремо після кубатури" -
    // окрема від positionSummaryText функція (та лишається буквально
    // такою ж для продажу), щоб не чіпати вже перевірений формат sale.
    function antisepticPositionSummaryText(key, values) {
      var cat = categories.filter(function (c) { return String(c.key) === key; })[0];
      var label = cat ? cat.label : key;
      var row = values.rows && values.rows[0];
      var sizeText = "";
      if (row) {
        var dims = [row.thickness, row.width, row.length].filter(function (v) {
          return v !== undefined && v !== null;
        });
        sizeText = dims.join("x");
        if (row.quantity) {
          sizeText += " × " + row.quantity;
        }
      }
      var volume = antisepticVolumeFor(values);
      if (volume > 0) {
        sizeText += (sizeText ? " — " : "") + formatServerNumber(volume) + " м3";
      }
      return label + (sizeText ? ", " + sizeText : "");
    }

    addPositionButton.addEventListener("click", function () {
      errorEl.textContent = "";
      var key = categorySelect.value;
      var result = collectCategoryFields(key);
      if (!result.ok) {
        errorEl.textContent = "Заполните все отмеченные поля позиции.";
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      if (result.empty) {
        errorEl.textContent = "Заполните размер позиции перед добавлением.";
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      // Задача користувача: "додай змогу ще додавати для одного клієнта
      // доски до продажі послуги, так як це реалізовано в продажі
      // пиломатеріалу" - антисептирование НЕ споживає залишок складу
      // (жодного stockSufficiencyCheck), тож окрема, коротша гілка ДО
      // sale-специфічної логіки нижче.
      if (categoryKind(key) === "antiseptic") {
        var antisepticVolume = antisepticVolumeFor(result.values);
        if (antisepticVolume <= 0) {
          errorEl.textContent = "Проверьте толщину, ширину, длину и штук.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        var antisepticPrice = numberOrZero(result.values.price_per_unit);
        var antisepticPosition = buildPosition(key, result.values);
        var cartItem = { position: antisepticPosition, summary: antisepticPositionSummaryText(key, result.values) };
        if (antisepticPrice > 0) {
          cartItem.sumText = "Сумма: " + formatMoney(antisepticVolume * antisepticPrice) + " MDL";
        }
        cart.push(cartItem);
        clearCategoryInputs(key);
        renderCart();
        return;
      }
      // Задача користувача (2026-08-14): "щоб міг продовжувати приход і
      // внести кілька різних позицій" - той самий принцип, що й антисептик
      // вище: БЕЗ stockSufficiencyCheck (прихід ДОДАЄ на склад, немає чого
      // "не вистачати" - навпаки, нового розміру/породи на складі ще може
      // взагалі не бути, і це нормально).
      if (categoryKind(key) === "income") {
        var incomePosition = buildPosition(key, result.values);
        cart.push({ position: incomePosition, summary: positionSummaryText(key, result.values) });
        clearCategoryInputs(key);
        renderCart();
        return;
      }
      var stockCheck = stockSufficiencyCheck(key, result.values);
      if (!stockCheck.ok) {
        errorEl.textContent = "На складе только " + stockCheck.available + " шт. Уменьшите количество.";
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      // Задача користувача: "чому розпізнало лише 1 антисептирование, якщо
      // я 2 антисептіровав?" - раніше чекбокс/поля антисептирования були
      // ОДНІ на всю форму і читались лише в момент фінального "Отправить",
      // тож антисептирование, позначене для позиції №1, губилось, коли
      // людина переходила до позиції №2. Тепер знімок читається ТУТ, у
      // момент архівування САМЕ ЦІЄЇ позиції, і кладеться В НЕЇ - а сам
      // чекбокс/поля скидаються, щоб позиція №2 не почала з чужими даними.
      var position = buildPosition(key, result.values);
      var antisepticAddon = collectAntisepticAddon();
      if (antisepticAddon) {
        position.antiseptic = antisepticAddon;
      }
      var salePositionTotal = computePositionTotal(key, result.values);
      var saleCartItem = { position: position, summary: positionSummaryText(key, result.values) };
      if (salePositionTotal > 0) {
        saleCartItem.sumText = "Сумма: " + formatMoney(salePositionTotal) + " MDL";
      }
      cart.push(saleCartItem);
      clearCategoryInputs(key);
      antisepticCheckbox.checked = false;
      antisepticPriceInput.value = "";
      antisepticQtyInput.value = "";
      refreshAntisepticBlock();
      renderCart();
    });

    // Задача користувача (2026-08-14): "щоб міг продовжувати приход і
    // внести кілька різних позицій. так же як це реалізовано в реалізації" -
    // той самий кошик, що вже має продаж, тепер і для приходу.
    function updateAddPositionVisibility() {
      var currentKind = categoryKind(categorySelect.value);
      addPositionButton.style.display =
        (currentKind === "sale" || currentKind === "antiseptic" || currentKind === "income") ? "" : "none";
      addPositionButton.textContent = currentKind === "antiseptic"
        ? "Продолжить"
        : currentKind === "income"
        ? "Продолжить приход"
        : "Продолжить продажу";
    }
    var showCategoryOriginal = showCategory;
    showCategory = function (key) {
      showCategoryOriginal(key);
      updateAddPositionVisibility();
      updateAntisepticVisibility();
    };
    updateAddPositionVisibility();
    updateAntisepticVisibility();

    // Задача користувача (скріншот "Вернуться в форму"): "має заходити з
    // уже внесеною інформацією до цього, навіть якщо продаж було декілька,
    // все має зберегти" - ctx.resume (telegram_dialog_core.py, будується з
    // уже збереженого pending_operation payload у момент "Вернуться в
    // форму") несе ГОТОВІ позиції кошика - лишається лише повторити той
    // самий cart.push(...)/renderCart(), що вже робить звичайне "Продолжить
    // продажу", жодного нового шляху збереження позиції не винаходимо.
    if (ctx.resume && Array.isArray(ctx.resume.cart) && ctx.resume.cart.length) {
      ctx.resume.cart.forEach(function (entry) {
        var key = String(entry.category_operation_id);
        if (!categoryState[key]) {
          return;
        }
        var position = { category_operation_id: Number(key) };
        ["breed", "rows", "price_per_unit", "antiseptic"].forEach(function (field) {
          if (entry[field] !== undefined) {
            position[field] = entry[field];
          }
        });
        var resumeCartItem = { position: position, summary: positionSummaryText(key, entry) };
        var resumePositionTotal = computePositionTotal(key, entry);
        if (resumePositionTotal > 0) {
          resumeCartItem.sumText = "Сумма: " + formatMoney(resumePositionTotal) + " MDL";
        }
        cart.push(resumeCartItem);
      });
      renderCart();
    } else if (ctx.resume && ctx.resume.category_operation_id) {
      // Антисептирование (і будь-яка інша однопозиційна форма без кошика) -
      // немає куди "додати" позицію, тож просто підставляємо категорію +
      // розмір напряму в поля, той самий populateCategoryFields, що вже
      // виконує "✎" у кошику продажу.
      var resumeKey = String(ctx.resume.category_operation_id);
      if (categoryState[resumeKey]) {
        categorySelect.value = resumeKey;
        categorySelect.dispatchEvent(new Event("change", { bubbles: true }));
        populateCategoryFields(resumeKey, { breed: ctx.resume.breed, rows: ctx.resume.rows });
      }
    }
    if (ctx.resume && ctx.resume.common) {
      if (commonInputs.client && ctx.resume.common.client) {
        setFieldValue(commonInputs.client, ctx.resume.common.client);
      }
      if (commonInputs.address && ctx.resume.common.address) {
        setFieldValue(commonInputs.address, ctx.resume.common.address);
      }
      if (commonInputs.payment_method && ctx.resume.common.payment_method) {
        setFieldValue(commonInputs.payment_method, ctx.resume.common.payment_method);
      }
      if (commonInputs.price_per_unit && ctx.resume.common.price_per_unit) {
        setFieldValue(commonInputs.price_per_unit, ctx.resume.common.price_per_unit);
      }
      if (commonInputs.comment && ctx.resume.common.comment) {
        setFieldValue(commonInputs.comment, ctx.resume.common.comment);
      }
    }

    // Реальний баг, знайдений під час додавання "Антисептирование (форма)":
    // ця функція раніше збирала ЛИШЕ identityCommonFields - для sale/income/
    // writeoff це завжди було ОК (їхні спільні поля - client/address/
    // payment_method/comment, жодне не "вимірне"), але "Антисептирование"
    // ПЕРШИЙ раз кладе price_per_unit у common_fields (ціна одна на всю
    // форму, не по кожній категорії, як у sale) - measureCommonFields
    // рендерився (buildFieldElement нижче), але значення НІКОЛИ не читалось,
    // тож ціна мовчки губилась ще ДО відправки на сервер.
    function collectCommonFields() {
      var values = {};
      var missingAny = false;
      identityCommonFields.concat(measureCommonFields).forEach(function (field) {
        var result = collectField(field, commonInputs[field.key]);
        if (result.value !== undefined) {
          values[field.key] = result.value;
        } else if (result.missing) {
          missingAny = true;
        }
      });
      return { ok: !missingAny, values: values };
    }

    function applyCommon(payload, common) {
      Object.keys(common.values).forEach(function (key) {
        payload[key] = common.values[key];
      });
      return payload;
    }

    // Реальний ризик (аудит коду, 2026-08-14): Telegram.WebApp.sendData()
    // жорстко обмежений ~4096 байтами (офіційний ліміт Bot API) - раніше
    // жодної перевірки тут не було, тож великий багатопозиційний кошик
    // (саме той сценарій, заради якого кошик і зробили) міг перевищити
    // ліміт і мовчки не відправитись - Telegram кидає виняток усередині
    // sendData, а екран підтвердження просто "зависав" без жодного
    // пояснення, чому нічого не відбувається.
    var _SEND_DATA_MAX_BYTES = 4096;

    function payloadTooLargeToSend(json) {
      // .length рахує UTF-16 code units, не байти - new Blob дає точний
      // байтовий розмір UTF-8 рядка (той самий, що й реально піде по
      // мережі), кирилиця (клієнт/адреса/коментар) інакше применшила б
      // реальний розмір удвічі-втричі.
      return new Blob([json]).size > _SEND_DATA_MAX_BYTES;
    }

    // Реальний ризик (аудит коду, 2026-08-14): tg.MainButton - нативна
    // кнопка Telegram, не звичайний DOM-елемент, тож ніщо не заважало
    // натиснути її ще раз ПОКИ триває сама відправка - швидкий подвійний
    // тап на сенсорному екрані (основний спосіб взаємодії з Mini App)
    // цілком міг встигнути викликати sendData() двічі до того, як Telegram
    // реально закриє застосунок. Прапорець isSendingPayload - справжній
    // захист (перевіряється ПЕРШИМ); MainButton.showProgress/disable -
    // лише видимий сигнал користувачу, не єдиний захист.
    var isSendingPayload = false;

    function sendPayload(payload) {
      if (isSendingPayload) {
        return;
      }
      var json = JSON.stringify(payload);
      if (tg) {
        if (payloadTooLargeToSend(json)) {
          window.alert(
            "Слишком много позиций для одной отправки - Telegram не пропустит такой большой " +
            "объём данных. Разделите на 2 отправки (например, отправьте часть позиций сейчас, " +
            "а остальные - отдельным подтверждением)."
          );
          return;
        }
        isSendingPayload = true;
        if (tg.MainButton && tg.MainButton.showProgress) {
          tg.MainButton.showProgress(false);
        }
        tg.sendData(json);
      } else {
        window.alert(json);
      }
    }

    function submit() {
      // Другий клік по тій самій кнопці "Отправить" (уже показано
      // підтвердження) - це і є реальна відправка.
      if (confirmPayload) {
        sendPayload(confirmPayload);
        return;
      }

      errorEl.textContent = "";
      var key = categorySelect.value;
      var kind = categoryKind(key);
      var common = collectCommonFields();

      // "Антисептирование (форма)" - окремий, самодостатній розділ (не
      // просто ще один варіант однорядового kind нижче): товщина/ширина/
      // довжина/штук ідуть у форму лише як КАЛЬКУЛЯТОР об'єму (та сама
      // формула, що й wireAntisepticVolumeHint/currentAntisepticVolume
      // вище) - сам запис не має колонок товару/породи/розміру взагалі
      // (antiseptic_sheet_values, warehouse_data.py), тож stockSufficiency
      // Check/buildPosition-based total тут не застосовні буквально так,
      // як для sale/income/writeoff/service.
      if (kind === "antiseptic") {
        // Задача користувача: "додай змогу ще додавати для одного клієнта
        // доски до продажі послуги" - той самий кошик-принцип, що вже має
        // продаж (cart + поточна, ще не додана позиція) - лише БЕЗ
        // stockSufficiencyCheck (антисептирование не споживає залишок).
        var antisepticCurrentResult = collectCategoryFields(key);
        var antisepticPositions = cart.map(function (item) {
          return item.position;
        });
        if (!antisepticCurrentResult.ok) {
          errorEl.textContent = antisepticPositions.length
            ? 'Заполните все поля текущей позиции или нажмите "Продолжить".'
            : "Заполните все отмеченные поля.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        if (!antisepticCurrentResult.empty) {
          var antisepticCurrentVolume = antisepticVolumeFor(antisepticCurrentResult.values);
          if (antisepticCurrentVolume <= 0) {
            errorEl.textContent = "Проверьте толщину, ширину, длину и штук.";
            if (tg && tg.HapticFeedback) {
              tg.HapticFeedback.notificationOccurred("error");
            }
            return;
          }
          antisepticPositions.push(buildPosition(key, antisepticCurrentResult.values));
        }
        if (!antisepticPositions.length) {
          errorEl.textContent = "Заполните хотя бы одну позицию.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        if (!common.ok) {
          errorEl.textContent = "Заполните все отмеченные поля.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        // Явний маркер для сервера (_continue_direct_open_webapp_submission,
        // telegram_dialog_core.py) - category_operation_id тут веде на
        // РЕАЛЬНУ sale-категорію (operation[2]=="sale"), тож без цього
        // прапорця подання виглядало б як звичайна продажа.
        if (antisepticPositions.length === 1 && !cart.length) {
          // Одна позиція (не торкались "Продолжить") - буквально той самий
          // payload/екран, що вже перевірено раніше (без positions[]).
          var antisepticSinglePosition = antisepticPositions[0];
          var antisepticSingleVolume = antisepticVolumeFor(antisepticSinglePosition);
          var antisepticPayload = applyCommon(antisepticSinglePosition, common);
          antisepticPayload.volume = antisepticSingleVolume;
          antisepticPayload.antiseptic_form = true;
          showConfirm(
            antisepticPayload,
            buildAntisepticSummaryElement(antisepticSinglePosition, common.values, antisepticSingleVolume)
          );
          return;
        }
        var antisepticFinalPayload = applyCommon({ positions: antisepticPositions }, common);
        antisepticFinalPayload.antiseptic_form = true;
        showConfirm(antisepticFinalPayload, buildAntisepticMultiSummaryElement(antisepticPositions, common.values));
        return;
      }

      // Списання (як і антисептирование-доповнення до продажі) - завжди
      // одноразове подання, без кошика: немає кількох незалежних товарних
      // позицій, які варто накопичувати (одна операція = один розмір/
      // порода за раз). Прихід (2026-08-14) переїхав у власну гілку нижче -
      // тепер теж підтримує кошик, "так же як це реалізовано в реалізації".
      if (kind === "service" || kind === "writeoff") {
        var singleResult = collectCategoryFields(key);
        if (!singleResult.ok || singleResult.empty || !common.ok) {
          errorEl.textContent = "Заполните все отмеченные поля.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        var singleStockCheck = stockSufficiencyCheck(key, singleResult.values);
        if (!singleStockCheck.ok) {
          errorEl.textContent = "На складе только " + singleStockCheck.available + " шт. Уменьшите количество.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        var singlePosition = buildPosition(key, singleResult.values);
        var singlePayload = applyCommon(singlePosition, common);
        showConfirm(singlePayload, buildSummaryElement([singlePosition], common.values));
        return;
      }

      // Задача користувача (2026-08-14): "щоб міг продовжувати приход і
      // внести кілька різних позицій. так же як це реалізовано в
      // реалізації" - той самий кошик-принцип, що й продаж нижче, БЕЗ
      // stockSufficiencyCheck (прихід ДОДАЄ на склад) і з явним
      // positions_kind, щоб бот не переплутав ці позиції з продажем.
      if (kind === "income") {
        var currentIncomeResult = collectCategoryFields(key);
        var incomePositions = cart.map(function (item) {
          return item.position;
        });
        if (!currentIncomeResult.ok) {
          errorEl.textContent = 'Заполните все поля текущей позиции или нажмите "Продолжить приход".';
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        if (!currentIncomeResult.empty) {
          incomePositions.push(buildPosition(key, currentIncomeResult.values));
        }
        if (!incomePositions.length) {
          errorEl.textContent = "Заполните хотя бы одну позицию.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        if (!common.ok) {
          errorEl.textContent = "Заполните все отмеченные поля.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        var incomeFinalPayload = applyCommon({ positions: incomePositions, positions_kind: "income" }, common);
        showConfirm(incomeFinalPayload, buildSummaryElement(incomePositions, common.values));
        return;
      }

      // Продажа: поточні (ще не додані кнопкою "Добавить позицию") поля -
      // це, можливо, ОСТАННЯ позиція; порожні поля тут означають "більше
      // нічого додавати" (не помилка), якщо в кошику вже щось є.
      var currentResult = collectCategoryFields(key);
      var positions = cart.map(function (item) {
        return item.position;
      });
      if (!currentResult.ok) {
        errorEl.textContent = 'Заполните все поля текущей позиции или нажмите "Добавить позицию".';
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      if (!currentResult.empty) {
        var currentStockCheck = stockSufficiencyCheck(key, currentResult.values);
        if (!currentStockCheck.ok) {
          errorEl.textContent = "На складе только " + currentStockCheck.available + " шт. Уменьшите количество.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        // Задача користувача: "чому розпізнало лише 1 антисептирование,
        // якщо я 2 антисептіровав?" - ця, ОСТАННЯ позиція (ще не додана
        // кнопкою "Продолжить продажу") несе СВІЙ власний знімок
        // антисептирования так само, як і кожна вже архівована в cart -
        // жодного окремого "глобального" antisepticAddon більше немає.
        var currentPosition = buildPosition(key, currentResult.values);
        var currentAntisepticAddon = collectAntisepticAddon();
        if (currentAntisepticAddon) {
          currentPosition.antiseptic = currentAntisepticAddon;
        }
        positions.push(currentPosition);
      }
      if (!positions.length) {
        errorEl.textContent = "Заполните хотя бы одну позицию.";
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      if (!common.ok) {
        errorEl.textContent = "Заполните все отмеченные поля.";
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      var finalPayload = applyCommon({ positions: positions }, common);
      showConfirm(finalPayload, buildSummaryElement(positions, common.values));
    }

    if (tg && tg.MainButton) {
      tg.MainButton.setText("Отправить");
      tg.MainButton.show();
      tg.MainButton.onClick(submit);
    } else {
      var fallback = document.getElementById("fallback-submit");
      fallback.style.display = "block";
      fallback.onclick = submit;
    }
  }

  function main() {
    var token = new URLSearchParams(window.location.search).get("t");
    if (token) {
      // Задача користувача: "чи є якийсь інший шлях?" - замість роздутого
      // ?ctx=<величезний base64> URL кнопка тепер несе лише короткий
      // токен, форма підвантажує самі дані одним тихим запитом при
      // відкритті (webapp_server.py:/api/template, action=get_context).
      postTemplateAction({ action: "get_context", token: token })
        .then(function (data) {
          startForm(data.ctx);
        })
        .catch(function () {
          document.getElementById("title").textContent = "Ошибка загрузки формы";
        });
      return;
    }
    var ctx = decodeContext();
    if (!ctx) {
      document.getElementById("title").textContent = "Ошибка загрузки формы";
      return;
    }
    startForm(ctx);
  }

  function startForm(ctx) {
    applyMeasureClassification(ctx);
    var app = document.getElementById("app");

    if (tg) {
      tg.ready();
      tg.expand();
      // requestFullscreen() пробувався для "зроби ширше вікно" на ПК, але
      // користувач надалі вирішив: компактне спливаюче вікно (як було
      // раніше і як воно й зараз виглядає на телефоні) - саме той вигляд,
      // що потрібен, fullscreen на весь екран НЕ потрібен ні на ПК, ні де-
      // інде. tg.expand() (розгортання в межах уже наявного компактного
      // вікна) лишається єдиним викликом розміру - те, що було ДО фічі
      // fullscreen.
      applyTheme();
    }
    applyCustomStyle(ctx.style);
    currentFieldLabelStyles = ctx.field_label_styles || {};

    document.getElementById("title").textContent = ctx.title || "Данные";
    // Задача користувача: текст заголовка "Проверьте данные" — редагований
    // з Налаштувань (webapp_confirm_heading_text) — той самий елемент
    // існує в DOM незалежно від mode (all_in_one чи однокатегорійна форма),
    // тож досить встановити його тут один раз.
    var confirmHeadingEl = document.querySelector("#confirm-view .title");
    if (confirmHeadingEl) {
      confirmHeadingEl.textContent = ctx.confirm_heading_text || "Проверьте данные";
    }

    if (ctx.mode === "all_in_one") {
      mainAllInOne(ctx);
      return;
    }

    var knownEl = document.getElementById("known");
    var knownEntries = Object.keys(ctx.known || {});
    if (knownEntries.length) {
      knownEl.innerHTML = knownEntries
        .map(function (label) {
          return escapeHtml(label) + ": <strong>" + escapeHtml(String(ctx.known[label])) + "</strong>";
        })
        .join("<br>");
    }

    var fields = ctx.fields || [];
    var perRowFields = fields.filter(function (f) {
      return f.per_row;
    });
    var singleFields = fields.filter(function (f) {
      return !f.per_row;
    });

    // За проханням користувача - без "+ Добавить размер": рівно один,
    // статичний набір per-row полів (thickness/width/length/quantity),
    // не список блоків, що можна додавати/прибирати. payload.rows лишається
    // масивом (з одним елементом) - формат, який бекенд і так очікує.
    var rowsContainer = document.getElementById("rows");
    var rowInputs = {};
    if (perRowFields.length) {
      var block = document.createElement("div");
      block.className = "row-block";
      perRowFields.forEach(function (field) {
        rowInputs[field.key] = buildFieldElement(field, block);
      });
      rowsContainer.appendChild(block);
    }

    var singleContainer = document.getElementById("single-fields");
    var singleInputs = {};
    singleFields.forEach(function (field) {
      singleInputs[field.key] = buildFieldElement(field, singleContainer);
    });
    // Порода (якщо взагалі рендериться - інакше вона вже "відома" з чату,
    // singleInputs.breed тоді просто undefined) будується ЩОЙНО ВИЩЕ -
    // тому кличемо каскад лише тепер.
    if (perRowFields.length) {
      wireDimensionCascade(rowInputs, ctx.dimension_combos, singleInputs.breed);
      wireMeasureHint(rowInputs, ctx.product);
    }

    var errorEl = document.getElementById("error");

    // Задача користувача: "кожна неіснуюча позиция може додати нову
    // позицию, якщо користувач це підтвердить" - ручний (allow_custom)
    // ввід розміру/породи сигналізує "цього значення нема в готовому
    // списку" (те саме, що дропдаун і так показує - лише відомі варіанти).
    // Прапорець зовнішній до collectPayload, бо submit() читає його ПІСЛЯ.
    var usedManualValueForNewPosition = false;

    function collectPayload() {
      errorEl.textContent = "";
      var payload = { operation_id: ctx.operation_id };
      var invalid = false;
      usedManualValueForNewPosition = false;

      if (perRowFields.length) {
        var row = {};
        var filledAny = false;
        var missingAny = false;
        perRowFields.forEach(function (field) {
          var input = rowInputs[field.key];
          var value = readFieldValue(input);
          var fieldWrap = input.closest(".field");
          fieldWrap.classList.remove("invalid");
          if (value !== "") {
            filledAny = true;
            row[field.key] = field.numeric ? Number(value) : value;
            if (input.manualInput && input.manualInput.value.trim() !== "") {
              usedManualValueForNewPosition = true;
            }
          } else if (field.required !== false) {
            missingAny = true;
            fieldWrap.classList.add("invalid");
          }
        });
        if (!filledAny) {
          invalid = true;
          errorEl.textContent = "Заполните размер.";
        } else if (missingAny) {
          invalid = true;
        }
        payload.rows = filledAny ? [row] : [];
      }

      singleFields.forEach(function (field) {
        var input = singleInputs[field.key];
        var value = readFieldValue(input);
        var fieldWrap = input.closest(".field");
        fieldWrap.classList.remove("invalid");
        if (value === "") {
          if (field.required !== false) {
            invalid = true;
            fieldWrap.classList.add("invalid");
          }
          return;
        }
        payload[field.key] = field.numeric ? Number(value) : value;
        if (input.manualInput && input.manualInput.value.trim() !== "") {
          usedManualValueForNewPosition = true;
        }
      });

      if (invalid) {
        if (!errorEl.textContent) {
          errorEl.textContent = "Заполните все отмеченные поля.";
        }
        return null;
      }
      return payload;
    }

    // Той самий захист від подвійного тапу по нативній MainButton посеред
    // самої відправки, що вже має sendPayload у формі приходу/продажу
    // вище (аудит коду, 2026-08-14).
    var isSendingSinglePayload = false;

    function actuallySendSinglePayload(payload) {
      if (isSendingSinglePayload) {
        return;
      }
      var json = JSON.stringify(payload);
      if (tg) {
        // Той самий захист від переповнення ліміту sendData (~4096 байт),
        // що вже має sendPayload вище (аудит коду, 2026-08-14).
        if (payloadTooLargeToSend(json)) {
          window.alert(
            "Слишком много позиций для одной отправки - Telegram не пропустит такой большой " +
            "объём данных. Разделите на 2 отправки."
          );
          return;
        }
        isSendingSinglePayload = true;
        if (tg.MainButton && tg.MainButton.showProgress) {
          tg.MainButton.showProgress(false);
        }
        tg.sendData(json);
      } else {
        window.alert(json);
      }
    }

    function submit() {
      var payload = collectPayload();
      if (!payload) {
        if (tg && tg.HapticFeedback) {
          tg.HapticFeedback.notificationOccurred("error");
        }
        return;
      }
      // Той самий захист, що й у мега-формі - тут ctx.dimension_combos на
      // верхньому рівні (одна категорія на всю форму, не масив). Якщо
      // порода вже "відома" (ctx.known, не рендериться як поле) - ключ
      // побудувати нема з чого, мовчки пропускаємо: серверний backstop
      // (_sale_stock_issue) усе одно перевірить це жорстко.
      var row = payload.rows && payload.rows[0];
      if (ctx.dimension_combos && ctx.dimension_combos.length && row && payload.breed) {
        var available = findComboBalance(
          ctx.dimension_combos,
          payload.breed,
          formatServerNumber(row.thickness),
          formatServerNumber(row.width),
          formatServerNumber(row.length)
        );
        if (available !== null && Number(row.quantity) > available) {
          errorEl.textContent = "На складе только " + available + " шт. Уменьшите количество.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
      }
      // Задача користувача: "кожна неіснуюча позиция може додати нову
      // позицию, якщо користувач це підтвердить" - лише для приходу
      // (ctx.allow_new_positions), і лише коли людина реально написала
      // значення вручну (не обрала з готового списку). tg.showConfirm -
      // рідний Telegram-діалог (theme-aware); window.confirm - фолбек для
      // перегляду поза Telegram (main() без tg).
      if (ctx.allow_new_positions && usedManualValueForNewPosition) {
        var confirmText = "Такого размера/породы нет в списке. Добавить как новую позицию?";
        confirmWithTelegram(confirmText, function () {
          actuallySendSinglePayload(payload);
        });
        return;
      }
      actuallySendSinglePayload(payload);
    }

    if (tg && tg.MainButton) {
      tg.MainButton.setText("Отправить");
      tg.MainButton.show();
      tg.MainButton.onClick(submit);
    } else {
      var fallback = document.getElementById("fallback-submit");
      fallback.style.display = "block";
      fallback.onclick = submit;
    }
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  document.addEventListener("DOMContentLoaded", main);
})();
