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
      destructive_text_color: "--error",
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
  var QUANTITY_ONLY_PRODUCTS = [];
  // ОСБ (2026-09-06): облік у мп, ціна за лист.
  var LINEAR_PRODUCTS = ["осб"];
  var PIECE_PRICED_PRODUCTS = ["осб"];
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
    if (Array.isArray(data.linear_products)) {
      LINEAR_PRODUCTS = data.linear_products;
    }
    if (Array.isArray(data.piece_priced_products)) {
      PIECE_PRICED_PRODUCTS = data.piece_priced_products;
    }
  }
  // Кольори операцій журналу з налаштувань клієнта (2026-09-06): сервер
  // дає обидві теми, форма бере за темою Telegram - читабельність у
  // світлій і темній однакова (контраст перевірено на сервері).
  function applyJournalColors(ctx) {
    var sets = ctx && ctx.journal_colors;
    if (!sets) {
      return;
    }
    var scheme = (tg && tg.colorScheme) || "light";
    var set = sets[scheme] || sets.light;
    if (!set) {
      return;
    }
    var root = document.documentElement;
    ["income", "sale", "writeoff", "exchange", "antiseptic", "correction", "rollback"].forEach(function (key) {
      var pair = set[key];
      if (!pair || pair.length < 2) {
        return;
      }
      root.style.setProperty("--jc-" + key + "-bg", pair[0]);
      root.style.setProperty("--jc-" + key + "-fg", pair[1]);
    });
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
    if (LINEAR_PRODUCTS.indexOf(normalized) !== -1 || isLinearMeterSize(thickness, width)) {
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
  // Рішення користувача (2026-09-05): «Адрес выгрузки» - одне поле зі
  // стрілкою справа. ▾ - під час набору знизу адреси, де якесь слово
  // починається на введене; ▴ - підказок нема. Стан стрілки - свій у кожного
  // (хмарне сховище Telegram, запасний варіант - памʼять форми на цьому
  // пристрої), типово ▾. Нова адреса просто лишається в полі - бот її не
  // перепитує, вона йде в продаж і наступного разу вже в підказках.
  var SUGGEST_FIELD_KEYS = ["client", "address"];

  function suggestStateKey(field) {
    return field.key + "_suggest_enabled";
  }

  function cloudStorageAvailable() {
    return !!(tg && tg.CloudStorage && typeof tg.isVersionAtLeast === "function" && tg.isVersionAtLeast("6.9"));
  }

  function readSuggestState(stateKey, callback) {
    var local = null;
    try {
      local = window.localStorage.getItem(stateKey);
    } catch (err) {
      local = null;
    }
    callback(local === null ? true : local === "1");
    if (!cloudStorageAvailable()) {
      return;
    }
    try {
      tg.CloudStorage.getItem(stateKey, function (error, value) {
        if (error || value === undefined || value === null || value === "") {
          return;
        }
        var enabled = value === "1";
        try {
          window.localStorage.setItem(stateKey, enabled ? "1" : "0");
        } catch (err) {}
        callback(enabled);
      });
    } catch (err) {}
  }

  function writeSuggestState(stateKey, enabled) {
    var text = enabled ? "1" : "0";
    try {
      window.localStorage.setItem(stateKey, text);
    } catch (err) {}
    if (!cloudStorageAvailable()) {
      return;
    }
    try {
      tg.CloudStorage.setItem(stateKey, text, function () {});
    } catch (err) {}
  }

  function buildSuggestField(field, wrap, container) {
    wrap.classList.add("suggest-field");
    var control = document.createElement("div");
    control.className = "suggest-control";
    var input = document.createElement("input");
    input.type = "text";
    input.name = field.key;
    input.autocomplete = "off";
    input.className = "suggest-input";
    var arrow = document.createElement("button");
    arrow.type = "button";
    arrow.className = "suggest-arrow";
    var list = document.createElement("div");
    list.className = "suggest-list";
    list.style.display = "none";
    control.appendChild(input);
    control.appendChild(arrow);
    wrap.appendChild(control);
    wrap.appendChild(list);
    container.appendChild(wrap);

    var options = (field.options || []).map(function (value) { return String(value); });
    var enabled = true;

    function matches(text) {
      var needle = text.trim().toLowerCase();
      if (!needle) {
        return [];
      }
      return options.filter(function (option) {
        var lower = option.toLowerCase();
        if (lower.indexOf(needle) === 0) {
          return true;
        }
        return lower.split(/[\s,.;:/\-]+/).some(function (word) {
          return word !== "" && word.indexOf(needle) === 0;
        });
      }).slice(0, 8);
    }

    function render() {
      arrow.textContent = enabled ? "\u25BE" : "\u25B4";
      arrow.classList.toggle("suggest-arrow-off", !enabled);
      arrow.title = enabled ? "Подсказки включены" : "Подсказки выключены";
      if (!enabled || document.activeElement !== input) {
        list.style.display = "none";
        return;
      }
      var found = matches(input.value);
      list.innerHTML = "";
      found.forEach(function (option) {
        var item = document.createElement("div");
        item.className = "suggest-item";
        item.textContent = option;
        item.addEventListener("mousedown", function (event) {
          event.preventDefault();
        });
        item.addEventListener("click", function () {
          input.value = option;
          list.style.display = "none";
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
          list.style.display = "none";
        });
        list.appendChild(item);
      });
      list.style.display = found.length ? "" : "none";
    }

    input.addEventListener("input", render);
    input.addEventListener("focus", render);
    input.addEventListener("blur", function () {
      setTimeout(function () {
        if (document.activeElement !== input) {
          list.style.display = "none";
        }
      }, 150);
    });
    var stateKey = suggestStateKey(field);
    arrow.addEventListener("click", function () {
      enabled = !enabled;
      writeSuggestState(stateKey, enabled);
      render();
    });
    readSuggestState(stateKey, function (state) {
      enabled = state;
      render();
    });
    return input;
  }

  function buildFieldElement(field, container) {
    var wrap = document.createElement("div");
    wrap.className = "field";
    wrap.dataset.key = field.key;

    var label = document.createElement("label");
    label.textContent = field.label + (field.required === false ? "" : " *");
    applyFieldLabelStyle(label, field.key);
    wrap.appendChild(label);

    if (SUGGEST_FIELD_KEYS.indexOf(field.key) !== -1) {
      return buildSuggestField(field, wrap, container);
    }

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
  // Розмір, набраний рукою («12.5»), у тому самому вигляді, що й у combos
  // з сервера («12,5»), інакше пошук залишку його не знайде.
  function sameDimensionText(value) {
    var text = String(value === undefined || value === null ? "" : value).trim();
    if (text === "") {
      return text;
    }
    var number = Number(text.replace(",", "."));
    return isNaN(number) ? text : formatServerNumber(number);
  }

  // options.noStock - рішення користувача (2026-09-06): у продажу, списанні
  // та «отдаём» обміну, де залишок має реально списатись, замість «Всего: 0»
  // або порожнього місця - дрібним червоним «нет остатка» (варіант 01). У
  // приході, антисептику, «получаем» і таблицях цього напису нема.
  function wireDimensionCascade(rowInputs, combos, breedInput, options) {
    var showNoStock = !!(options && options.noStock);
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
      var balance = findComboBalance(combos, breed, sameDimensionText(thickness), sameDimensionText(width), sameDimensionText(length));
      if (balance === null || Number(balance) <= 0) {
        if (showNoStock) {
          balanceHint.textContent = "нет остатка";
          balanceHint.classList.add("no-stock");
          delete balanceHint.dataset.value;
          balanceHint.style.display = "";
        } else {
          balanceHint.style.display = "none";
        }
        return;
      }
      balanceHint.classList.remove("no-stock");
      var formatted = formatServerNumber(balance);
      balanceHint.textContent = "Всего: " + formatted + " шт";
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
    // Rozmir, nabranyi rukoiu (allow_custom), tezh onovliuie pidkazku - inakshe
    // pislia ruchnoho vvodu vona lyshalas zastariloiu (skrin korystuvacha
    // 2026-09-06: OSB bez pidkazky pry ruchnykh rozmirakh).
    [thicknessSelect, widthSelect, lengthSelect].forEach(function (select) {
      if (select.manualInput) {
        select.manualInput.addEventListener("input", refreshBalanceHint);
      }
    });
  }

  // Для select+allow_custom - справжнє значення бере поле, яке РЕАЛЬНО
  // заповнене (взаємовиключність у buildFieldElement гарантує, що заповнене
  // лише одне з двох). Для решти полів - просто саме значення input/select.
  // Рішення користувача (2026-09-06): довжина «3», «6», «4» - це метри,
  // переписується на 3000/6000/4000. Поріг той самий, що й на сервері
  // (utils.LENGTH_METERS_MAX = 1000): коротшої за 1000 мм у продажу не буває.
  var LENGTH_METERS_MAX = 1000;
  function normalizeLengthMm(value) {
    var text = String(value === undefined || value === null ? "" : value).trim().replace(",", ".");
    if (text === "") {
      return text;
    }
    var number = Number(text);
    if (!isFinite(number) || number <= 0 || number >= LENGTH_METERS_MAX) {
      return text;
    }
    var result = number * 1000;
    return String(Number.isInteger(result) ? result : Math.round(result * 1000) / 1000);
  }
  // Поле довжини (select з ручним введенням або звичайне число) після
  // виходу з нього показує вже переписане значення - людина бачить 6000.
  document.addEventListener("change", function (event) {
    var element = event.target;
    if (!element || (element.name !== "length" && element.name !== "length__manual")) {
      return;
    }
    if (element.tagName === "SELECT") {
      return;
    }
    var fixed = normalizeLengthMm(element.value);
    if (fixed !== element.value.trim()) {
      element.value = fixed;
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }, true);

  function readFieldValue(input) {
    var raw;
    if (input.manualInput && input.manualInput.value.trim() !== "") {
      raw = input.manualInput.value.trim();
    } else {
      raw = input.value.trim();
    }
    return input.name === "length" ? normalizeLengthMm(raw) : raw;
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

  // KD за номіналом (ТЗ пункт 2, 2026-09-05). Рішення користувача: жодної
  // таблиці відповідностей - фактичний складський розмір обирається щоразу;
  // галочка "Продать как введённый размер" повертає звичайну поведінку, і
  // поки вона стоїть, рядка "Списать со склада" нема взагалі. Список - усі
  // розміри складу для обраної породи (dimension_combos: [порода, товщина,
  // ширина, довжина, залишок]). Якщо введеного розміру на складі нема -
  // галочка знімається сама, щоб список зʼявився одразу.
  function buildStockChooser(container, rowInputs, breedInput, combos) {
    var wrap = document.createElement("div");
    wrap.className = "field stock-chooser";
    var checkLabel = document.createElement("label");
    checkLabel.className = "stock-chooser-check";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkLabel.appendChild(checkbox);
    var checkText = document.createElement("span");
    checkText.textContent = "Продать как введённый размер";
    checkLabel.appendChild(checkText);
    wrap.appendChild(checkLabel);
    var selectLabel = document.createElement("div");
    selectLabel.className = "stock-chooser-label";
    selectLabel.textContent = "Списать со склада";
    wrap.appendChild(selectLabel);
    var select = document.createElement("select");
    select.className = "field-wide";
    wrap.appendChild(select);
    var remain = document.createElement("div");
    remain.className = "stock-chooser-remain";
    wrap.appendChild(remain);
    var hint = document.createElement("div");
    hint.className = "stock-chooser-hint";
    wrap.appendChild(hint);
    container.appendChild(wrap);

    var lastEnteredKey = null;
    var userChose = false;

    function entered() {
      return [rowInputs.thickness, rowInputs.width, rowInputs.length].map(function (input) {
        var value = input ? readFieldValue(input) : "";
        return value === "" || value === null || value === undefined ? "" : formatServerNumber(value);
      });
    }

    function optionsForBreed() {
      var breed = breedInput ? readFieldValue(breedInput) : null;
      return (combos || []).filter(function (combo) {
        return !breed || combo[0] === breed;
      });
    }

    function numberOf(value) {
      return Number(String(value === undefined || value === null ? "" : value).replace(",", ".")) || 0;
    }

    // Рішення користувача (2026-09-05): у списку лише розміри з тією самою
    // довжиною, що введена; рівно введеного нема (для нього галочка); зверху
    // найближчі - спершу та сама ширина, далі найменша різниця товщини.
    function choosable(options, dims, enteredKey) {
      var length = dims[2];
      var list = options.filter(function (combo) {
        if (combo[1] + "|" + combo[2] + "|" + combo[3] === enteredKey) {
          return false;
        }
        return !length || String(combo[3]) === String(length);
      });
      var thickness = numberOf(dims[0]);
      var width = numberOf(dims[1]);
      list.sort(function (a, b) {
        var aWidth = numberOf(a[2]) === width ? 0 : 1;
        var bWidth = numberOf(b[2]) === width ? 0 : 1;
        if (aWidth !== bWidth) {
          return aWidth - bWidth;
        }
        var aThick = Math.abs(numberOf(a[1]) - thickness);
        var bThick = Math.abs(numberOf(b[1]) - thickness);
        if (aThick !== bThick) {
          return aThick - bThick;
        }
        return Math.abs(numberOf(a[2]) - width) - Math.abs(numberOf(b[2]) - width);
      });
      return list;
    }

    function refresh() {
      var dims = entered();
      var enteredKey = dims.join("|");
      var stockOptions = optionsForBreed();
      var exists = stockOptions.some(function (combo) {
        return combo[1] + "|" + combo[2] + "|" + combo[3] === enteredKey;
      });
      var options = choosable(stockOptions, dims, enteredKey);
      var complete = dims.every(function (value) { return value !== ""; });
      if (complete && enteredKey !== lastEnteredKey) {
        checkbox.checked = exists;
        lastEnteredKey = enteredKey;
        userChose = false;
      }
      var previous = select.value;
      select.innerHTML = "";
      options.forEach(function (combo) {
        var option = document.createElement("option");
        option.value = combo[1] + "|" + combo[2] + "|" + combo[3];
        option.textContent = combo[1] + "\u00d7" + combo[2] + "\u00d7" + combo[3] + " \u2014 " + combo[4] + " \u0448\u0442";
        select.appendChild(option);
      });
      var values = options.map(function (combo) { return combo[1] + "|" + combo[2] + "|" + combo[3]; });
      if (userChose && values.indexOf(previous) !== -1) {
        select.value = previous;
      } else if (options.length) {
        select.selectedIndex = 0;
      }
      var show = !checkbox.checked;
      selectLabel.style.display = show ? "" : "none";
      select.style.display = show ? "" : "none";
      if (show && !options.length && stockOptions.length && dims[2] !== "") {
        hint.textContent = "На складе нет других размеров с длиной " + dims[2] + ".";
      } else if (show && !options.length) {
        hint.textContent = "На складе нет позиций этой категории для выбранной породы.";
      } else if (show && complete && !exists) {
        hint.textContent = "\u0412\u0432\u0435\u0434\u0451\u043d\u043d\u043e\u0433\u043e \u0440\u0430\u0437\u043c\u0435\u0440\u0430 \u043d\u0430 \u0441\u043a\u043b\u0430\u0434\u0435 \u043d\u0435\u0442 \u2014 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435, \u0447\u0442\u043e \u0441\u043f\u0438\u0441\u0430\u0442\u044c.";
      } else {
        hint.textContent = "";
      }
      hint.style.display = hint.textContent ? "" : "none";
      var quantity = rowInputs.quantity ? numberOf(readFieldValue(rowInputs.quantity)) : 0;
      var chosen = options.filter(function (combo) {
        return combo[1] + "|" + combo[2] + "|" + combo[3] === select.value;
      })[0];
      if (show && chosen && quantity > 0) {
        var left = numberOf(chosen[4]) - quantity;
        remain.textContent = left >= 0
          ? "Останется " + formatServerNumber(left) + " шт"
          : "Не хватает " + formatServerNumber(-left) + " шт";
        remain.classList.toggle("stock-chooser-short", left < 0);
        remain.style.display = "";
      } else {
        remain.style.display = "none";
      }
    }

    function value() {
      if (checkbox.checked || !select.value) {
        return null;
      }
      var parts = select.value.split("|").map(function (part) {
        return Number(String(part).replace(",", "."));
      });
      return { thickness: parts[0], width: parts[1], length: parts[2] };
    }

    function restore(row) {
      lastEnteredKey = null;
      userChose = false;
      refresh();
      if (row && row.stock_thickness !== undefined && row.stock_thickness !== null) {
        checkbox.checked = false;
        lastEnteredKey = entered().join("|");
        userChose = true;
        refresh();
        select.value = [row.stock_thickness, row.stock_width, row.stock_length].map(formatServerNumber).join("|");
        refresh();
      }
    }

    function reset() {
      lastEnteredKey = null;
      userChose = false;
      checkbox.checked = true;
      refresh();
    }

    checkbox.addEventListener("change", refresh);
    select.addEventListener("change", function () {
      userChose = true;
      refresh();
    });
    [rowInputs.thickness, rowInputs.width, rowInputs.length, rowInputs.quantity].forEach(function (input) {
      if (!input) { return; }
      input.addEventListener("change", refresh);
      input.addEventListener("input", refresh);
      if (input.manualInput) {
        input.manualInput.addEventListener("input", refresh);
      }
    });
    if (breedInput) {
      breedInput.addEventListener("change", refresh);
    }
    refresh();
    return { value: value, restore: restore, reset: reset, refresh: refresh };
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
    // Рішення користувача (2026-09-05): «Клиент» і «Адрес выгрузки» - зверху,
    // на власному фоні (лише фон, без ліній і заголовків); решта - як була.
    var topContainer = document.createElement("div");
    topContainer.id = "top-fields";
    topContainer.className = "top-block";
    var identityContainer = document.createElement("div");
    identityContainer.id = "identity-fields";
    measureContainer.parentNode.insertBefore(topContainer, measureContainer);
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
      var stockChooser = null;
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
        wireDimensionCascade(rowInputs, cat.dimension_combos, flatInputs.breed, { noStock: ctx.kind === "sale" || ctx.kind === "writeoff" });
        if (cat.kind === "antiseptic") {
          wireAntisepticVolumeHint(rowInputs);
        } else {
          wireMeasureHint(rowInputs, cat.product);
          if (cat.kind === "sale" && cat.condition === "KD") {
            stockChooser = buildStockChooser(measureBlock, rowInputs, flatInputs.breed, cat.dimension_combos);
          }
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
        stockChooser: stockChooser,
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
      var target = SUGGEST_FIELD_KEYS.indexOf(field.key) !== -1 ? topContainer : identityContainer;
      commonInputs[field.key] = buildFieldElement(field, target);
    });
    if (!topContainer.childNodes.length) {
      topContainer.parentNode.removeChild(topContainer);
    }
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
            // Рейка (2026-09-06): у блоці «Добавлено» позиція з перерізом
            // рейки підписується «(рейка)», як у боті й у таблиці.
            if (cat && lines[0] === cat.label && rowMeasureKind(cat.product, dimValues[0], dimValues[1]) === "linear") {
              lines[0] = cat.label + " (рейка)";
            }
            if (row && row.stock_thickness !== undefined && row.stock_thickness !== null) {
              lines.push("Списывается: " + row.stock_thickness + "x" + row.stock_width + "x" + row.stock_length);
            }
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
        var recalcText = null;
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
              var piecePriced = PIECE_PRICED_PRODUCTS.indexOf(normalizeProductPhrase(cat && cat.product)) !== -1;
              priceUnit = priceKind && !piecePriced ? MEASURE_UNIT_BY_KIND[priceKind] : "шт";
              if (priceKind && !piecePriced && dimValues && dimValues.every(function (v) { return v !== null; })) {
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
            // KD за номіналом: різниця між сумою за введений і за списаний розмір.
            if (row && row.stock_thickness !== undefined && row.stock_thickness !== null && priceKind && priceNum > 0 && totalAmount !== null) {
              var factMeasure = pieceMeasure(row.stock_thickness, row.stock_width, row.stock_length, priceKind) * numberOrZero(quantityRawValue);
              var recalcIncome = priceNum * (totalAmount - factMeasure);
              if (Math.abs(recalcIncome) > 0.005) {
                recalcText = "Доход по пересчету: " + (recalcIncome < 0 ? "-" : "+") + formatMoney(Math.abs(recalcIncome)) + " MDL";
              }
            }
          }
          lines.push(lineText);
          if (recalcText) {
            lines.push(recalcText);
          }
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
    // KD за номіналом: дохід по перерахунку позиції = ціна × (вимір введеного
    // розміру − вимір списаного) × штук, по всіх рядках із підміною.
    function computePositionRecalc(key, values) {
      var cat = categories.filter(function (c) { return String(c.key) === key; })[0];
      var price = numberOrZero(values.price_per_unit);
      if (price <= 0 || !values.rows) {
        return 0;
      }
      var total = 0;
      values.rows.forEach(function (row) {
        if (!row || row.stock_thickness === undefined || row.stock_thickness === null) {
          return;
        }
        var kind = rowMeasureKind(cat && cat.product, row.thickness, row.width);
        if (!kind) {
          return;
        }
        var nominal = pieceMeasure(row.thickness, row.width, row.length, kind);
        var fact = pieceMeasure(row.stock_thickness, row.stock_width, row.stock_length, kind);
        total += price * (nominal - fact) * numberOrZero(row.quantity);
      });
      return total;
    }

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
      // Для антисептирования ціна ЗАВЖДИ MDL/м3 - незалежно від того, як
      // продається сам товар. Реальний випадок (2026-08-21, скріншот):
      // 50x50x6000, 153 шт, 350 MDL - живий рядок під ціною показував
      // "Сумма за товар: 321300 MDL" (правило погонних метрів: 918 мп x
      // 350) замість 803,25 MDL (2,295 м3 x 350). Підказка над ним у ТІЙ
      // САМІЙ формі чесно писала "Объём: 2,295 м3", а поле звалось "Цена
      // за м3" - тобто форма сама собі суперечила.
      //
      // Екран підтвердження (buildAntisepticSummaryElement) і сам бот
      // (telegram_dialog_antiseptic: total_amount = price_per_unit *
      // volume) рахували правильно від початку - розходився лише цей
      // рядок, бо йшов через СПІЛЬНУ computePositionTotal, побудовану
      // навколо "товар + необов'язкове антисептик-доповнення".
      //
      // Антисептик-ДОПОВНЕННЯ до продажу сюди не потрапляє: там cat - це
      // категорія самого товару, і його вимір (мп/м2/м3) лишається своїм.
      if (cat && cat.kind === "antiseptic") {
        return pieceMeasure(thickness, width, length, "volume") * numberOrZero(quantity) * price;
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
      var recalcTotal = 0;
      positions.forEach(function (position) {
        goodsTotal += computePositionTotal(String(position.category_operation_id), position);
        recalcTotal += computePositionRecalc(String(position.category_operation_id), position);
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
        if (Math.abs(recalcTotal) > 0.005) {
          var factTotalRow = document.createElement("div");
          factTotalRow.className = "confirm-common";
          factTotalRow.textContent = "Сумма по факту: " + formatMoney(goodsTotal - recalcTotal) + " MDL";
          container.appendChild(factTotalRow);
          var recalcTotalRow = document.createElement("div");
          recalcTotalRow.className = "confirm-common";
          recalcTotalRow.textContent = "Доход по пересчету: " + (recalcTotal < 0 ? "-" : "+") + formatMoney(Math.abs(recalcTotal)) + " MDL";
          container.appendChild(recalcTotalRow);
        }
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
          if (state.stockChooser) {
            var stock = state.stockChooser.value();
            if (stock && (stock.thickness !== Number(row.thickness) || stock.width !== Number(row.width) || stock.length !== Number(row.length))) {
              row.stock_thickness = stock.thickness;
              row.stock_width = stock.width;
              row.stock_length = stock.length;
            }
          }
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
      if (state.stockChooser) {
        state.stockChooser.reset();
      }
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
        formatServerNumber(row.stock_thickness !== undefined ? row.stock_thickness : row.thickness),
        formatServerNumber(row.stock_width !== undefined ? row.stock_width : row.width),
        formatServerNumber(row.stock_length !== undefined ? row.stock_length : row.length)
      );
      if (available === null) {
        return { ok: true };
      }
      if (Number(row.quantity) > available) {
        return { ok: false, available: available };
      }
      return { ok: true };
    }

    function stockSuffix(row) {
      if (!row || row.stock_thickness === undefined || row.stock_thickness === null) {
        return "";
      }
      return " (списывается " + row.stock_thickness + "x" + row.stock_width + "x" + row.stock_length + ")";
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
        sizeText += stockSuffix(row);
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
    // Рішення користувача (2026-09-06): блок «Добавлено» - з самого верху.
    formEl.insertBefore(cartSection, formEl.firstChild);
    // Після «Сохранить и продолжить» форма згортається: видно лише блок
    // «Добавлено» і кнопку «Добавить позицию» (CSS #form.collapsed); клієнт,
    // адреса й оплата лишаються заповненими всередині. «Добавить позицию»,
    // ✎ у рядку і ✕ останнього рядка повертають звичайне меню.
    var addMoreButton = document.createElement("button");
    addMoreButton.type = "button";
    addMoreButton.className = "add-position-button add-more-button";
    addMoreButton.textContent = "Добавить позицию";
    formEl.insertBefore(addMoreButton, cartSection.nextSibling);
    function setFormCollapsed(state) {
      var collapsed = !!state && cart.length > 0;
      formEl.classList.toggle("collapsed", collapsed);
    }
    addMoreButton.addEventListener("click", function () {
      errorEl.textContent = "";
      setFormCollapsed(false);
    });

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
      if (state.stockChooser) {
        state.stockChooser.restore(row);
      }
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
      setFormCollapsed(false);
    }

    function removeCartItem(index) {
      cart.splice(index, 1);
      renderCart();
      if (!cart.length) {
        setFormCollapsed(false);
      }
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
    addPositionButton.textContent = "Сохранить и продолжить";
    measureContainer.parentNode.insertBefore(addPositionButton, measureContainer.nextSibling);
    // "Сохранить как шаблон" тепер одразу ПЕРЕД "Продолжить продажу" (за
    // проханням користувача перенести кнопку донизу форми).

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
    measureContainer.parentNode.insertBefore(goodsSumLine, addPositionButton);
    measureContainer.parentNode.insertBefore(positionTotalLine, addPositionButton);

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
        setFormCollapsed(true);
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
        setFormCollapsed(true);
        return;
      }
      if (categoryKind(key) === "writeoff") {
        var writeoffStockCheck = stockSufficiencyCheck(key, result.values);
        if (!writeoffStockCheck.ok) {
          errorEl.textContent = "На складе только " + writeoffStockCheck.available + " шт. Уменьшите количество.";
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        var writeoffPosition = buildPosition(key, result.values);
        cart.push({ position: writeoffPosition, summary: positionSummaryText(key, result.values) });
        clearCategoryInputs(key);
        renderCart();
        setFormCollapsed(true);
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
      setFormCollapsed(true);
    });

    // Задача користувача (2026-08-14): "щоб міг продовжувати приход і
    // внести кілька різних позицій. так же як це реалізовано в реалізації" -
    // той самий кошик, що вже має продаж, тепер і для приходу.
    function updateAddPositionVisibility() {
      var currentKind = categoryKind(categorySelect.value);
      // Рішення користувача (2026-09-06): та сама кнопка і хід для продажу,
      // приходу, списання й антисептика.
      addPositionButton.style.display =
        (currentKind === "sale" || currentKind === "antiseptic" || currentKind === "income" || currentKind === "writeoff")
          ? "" : "none";
      addPositionButton.textContent = "Сохранить и продолжить";
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
            ? 'Заполните все поля текущей позиции или нажмите "Сохранить и продолжить".'
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
      if (kind === "writeoff") {
        var currentWriteoffResult = collectCategoryFields(key);
        var writeoffPositions = cart.map(function (item) {
          return item.position;
        });
        if (!currentWriteoffResult.ok) {
          errorEl.textContent = 'Заполните все поля текущей позиции или нажмите "Сохранить и продолжить".';
          if (tg && tg.HapticFeedback) {
            tg.HapticFeedback.notificationOccurred("error");
          }
          return;
        }
        if (!currentWriteoffResult.empty) {
          var currentWriteoffStock = stockSufficiencyCheck(key, currentWriteoffResult.values);
          if (!currentWriteoffStock.ok) {
            errorEl.textContent = "На складе только " + currentWriteoffStock.available + " шт. Уменьшите количество.";
            if (tg && tg.HapticFeedback) {
              tg.HapticFeedback.notificationOccurred("error");
            }
            return;
          }
          writeoffPositions.push(buildPosition(key, currentWriteoffResult.values));
        }
        if (!writeoffPositions.length) {
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
        var writeoffFinalPayload = applyCommon({ positions: writeoffPositions, positions_kind: "writeoff" }, common);
        showConfirm(writeoffFinalPayload, buildSummaryElement(writeoffPositions, common.values));
        return;
      }
      if (kind === "service") {
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
          errorEl.textContent = 'Заполните все поля текущей позиции или нажмите "Сохранить и продолжить".';
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
        errorEl.textContent = 'Заполните все поля текущей позиции или нажмите "Сохранить и продолжить".';
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

  // --- Обмін (ТЗ пункт 1, 2026-09-05) ---
  // Задача користувача: кнопка "ОБМЕН", два блоки "Отдаём" і "Получаем",
  // кілька позицій у кожному, "Добавить ещё позицию" в кожному окремо,
  // завершити лише коли обидва заповнені. Робота лише через форму.
  // Обраний варіант 01 із пʼяти: два блоки один під одним, у кожного свій
  // кошик і своя кнопка "Добавить". Поля, кошик, чипи ✎/✕, підказки
  // залишку й м3/мп - ті самі помічники, що й у mainAllInOne, тож обмін
  // не вчить людину нового.
  //
  // Відповіді користувача: "Получаем" може створити новий розмір (мітка
  // "новая"); порожній блок - помилка, яка НЕ перериває й НІЧОГО не стирає.
  function exchangeAllInOne(ctx) {
    var formEl = document.getElementById("form");
    var rowsContainer = document.getElementById("rows");
    var singleContainer = document.getElementById("single-fields");
    var errorEl = document.getElementById("error");
    var confirmView = document.getElementById("confirm-view");
    var confirmSummaryEl = document.getElementById("confirm-summary");
    var confirmEditButton = document.getElementById("confirm-edit-button");
    var confirmPayload = null;

    function haptic(kind) {
      if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred(kind);
      }
    }

    function fail(text) {
      errorEl.textContent = text;
      haptic("error");
    }

    function pluralPositions(count) {
      var mod10 = count % 10;
      var mod100 = count % 100;
      if (mod10 === 1 && mod100 !== 11) {
        return count + " позиция";
      }
      if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
        return count + " позиции";
      }
      return count + " позиций";
    }

    function setValueInto(input, value) {
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
          input.dispatchEvent(new Event("change", { bubbles: true }));
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

    function pickRow(row) {
      var picked = {};
      ["thickness", "width", "length", "quantity"].forEach(function (key) {
        if (row && row[key] !== undefined && row[key] !== null && row[key] !== "") {
          picked[key] = row[key];
        }
      });
      return picked;
    }

    // Блок однієї сторони заміни. side="give" - список позицій зі своїм
    // кошиком і «+ Добавить» (як було); side="take" (single=true) - рівно
    // один розмір, поля завжди відкриті, без кошика (рішення користувача
    // 2026-09-06: «на те, що міняємо, - лише 1»).
    function buildBlock(side, title, addLabel, cats, single) {
      var block = document.createElement("div");
      block.className = "exchange-block exchange-block-" + side;

      var head = document.createElement("div");
      head.className = "exchange-block-head";
      var titleEl = document.createElement("span");
      titleEl.className = "exchange-block-title";
      titleEl.textContent = title;
      var countEl = document.createElement("span");
      countEl.className = "exchange-block-count";
      head.appendChild(titleEl);
      if (!single) {
        head.appendChild(countEl);
      }
      block.appendChild(head);

      var cartSection = document.createElement("div");
      cartSection.className = "cart-section";
      cartSection.style.display = "none";
      var cartList = document.createElement("div");
      cartList.className = "cart-list";
      cartSection.appendChild(cartList);
      if (!single) {
        block.appendChild(cartSection);
      }

      var categoryWrap = document.createElement("div");
      categoryWrap.className = "field";
      var categoryLabel = document.createElement("label");
      categoryLabel.textContent = "Категория *";
      applyFieldLabelStyle(categoryLabel, "category");
      categoryWrap.appendChild(categoryLabel);
      var select = document.createElement("select");
      select.className = "field-wide";
      cats.forEach(function (cat) {
        var option = document.createElement("option");
        option.value = String(cat.key);
        option.textContent = cat.label;
        select.appendChild(option);
      });
      categoryWrap.appendChild(select);

      var fieldsWrap = document.createElement("div");
      fieldsWrap.className = "exchange-fields";
      fieldsWrap.style.display = single ? "" : "none";
      fieldsWrap.appendChild(categoryWrap);
      var identityContainer = document.createElement("div");
      var measureContainer = document.createElement("div");
      fieldsWrap.appendChild(identityContainer);
      fieldsWrap.appendChild(measureContainer);
      block.appendChild(fieldsWrap);

      var state = {};
      cats.forEach(function (cat) {
        var identityBlock = document.createElement("div");
        identityBlock.className = "category-group";
        var measureBlock = document.createElement("div");
        measureBlock.className = "category-group";
        var fields = cat.fields || [];
        var identityFields = fields.filter(function (f) { return !isMeasureField(f); });
        var perRow = fields.filter(function (f) { return f.per_row && isMeasureField(f); });
        var flatMeasure = fields.filter(function (f) { return !f.per_row && isMeasureField(f); });
        var rowInputs = {};
        if (perRow.length) {
          var rowBlock = document.createElement("div");
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
        if (perRow.length) {
          wireDimensionCascade(rowInputs, cat.dimension_combos, flatInputs.breed, { noStock: side === "give" });
          wireMeasureHint(rowInputs, cat.product);
        }
        identityContainer.appendChild(identityBlock);
        measureContainer.appendChild(measureBlock);
        state[String(cat.key)] = {
          fields: fields,
          rowInputs: rowInputs,
          flatInputs: flatInputs,
          identityBlock: identityBlock,
          measureBlock: measureBlock,
        };
      });

      function showCategory(key) {
        Object.keys(state).forEach(function (k) {
          var display = k === key ? "" : "none";
          state[k].identityBlock.style.display = display;
          state[k].measureBlock.style.display = display;
        });
      }
      select.addEventListener("change", function () {
        showCategory(select.value);
      });
      if (cats.length) {
        select.value = String(cats[0].key);
        showCategory(select.value);
      }

      var cancelButton = null;
      var commitButton = null;
      var openButton = null;
      if (!single) {
        var actions = document.createElement("div");
        actions.className = "exchange-fields-actions";
        cancelButton = document.createElement("button");
        cancelButton.type = "button";
        cancelButton.className = "exchange-fields-btn";
        cancelButton.textContent = "Отмена";
        commitButton = document.createElement("button");
        commitButton.type = "button";
        commitButton.className = "exchange-fields-btn primary";
        commitButton.textContent = "Добавить";
        actions.appendChild(cancelButton);
        actions.appendChild(commitButton);
        fieldsWrap.appendChild(actions);

        openButton = document.createElement("button");
        openButton.type = "button";
        openButton.className = "add-position-button";
        openButton.textContent = addLabel;
        block.appendChild(openButton);
      }

      function fieldsOpen() {
        return fieldsWrap.style.display !== "none";
      }
      function openFields() {
        fieldsWrap.style.display = "";
        if (openButton) {
          openButton.style.display = "none";
        }
      }
      function closeFields() {
        if (single) {
          return;
        }
        fieldsWrap.style.display = "none";
        openButton.style.display = "";
      }

      var cart = [];

      function category(key) {
        return cats.filter(function (c) { return String(c.key) === key; })[0];
      }

      function fieldByKey(categoryFields, key) {
        return categoryFields.filter(function (f) { return f.key === key; })[0];
      }

      function collectOne(field, input) {
        var value = readFieldValue(input);
        var wrap = input.closest(".field");
        if (wrap) {
          wrap.classList.remove("invalid");
        }
        if (value === "") {
          if (field.required !== false) {
            if (wrap) {
              wrap.classList.add("invalid");
            }
            return { ok: false, missing: true };
          }
          return { ok: true };
        }
        return { ok: true, value: field.numeric ? Number(String(value).replace(",", ".")) : value };
      }

      function collect(key) {
        var st = state[key];
        if (!st) {
          return { ok: false };
        }
        var values = {};
        var missingAny = false;
        var filledAny = false;
        var rowKeys = Object.keys(st.rowInputs);
        if (rowKeys.length) {
          var row = {};
          rowKeys.forEach(function (rowKey) {
            var result = collectOne(fieldByKey(st.fields, rowKey), st.rowInputs[rowKey]);
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
        Object.keys(st.flatInputs).forEach(function (flatKey) {
          var result = collectOne(fieldByKey(st.fields, flatKey), st.flatInputs[flatKey]);
          if (result.value !== undefined) {
            filledAny = true;
            values[flatKey] = result.value;
          } else if (result.missing) {
            missingAny = true;
          }
        });
        if (!filledAny) {
          Object.keys(st.rowInputs).concat(Object.keys(st.flatInputs)).forEach(function (k) {
            var input = st.rowInputs[k] || st.flatInputs[k];
            var wrap = input.closest(".field");
            if (wrap) {
              wrap.classList.remove("invalid");
            }
          });
          return { ok: true, empty: true, values: values };
        }
        return { ok: !missingAny, empty: false, values: values };
      }

      function clearInputs(key) {
        var st = state[key];
        if (!st) {
          return;
        }
        [st.rowInputs, st.flatInputs].forEach(function (group) {
          Object.keys(group).forEach(function (k) {
            var input = group[k];
            input.value = "";
            if (input.manualInput) {
              input.manualInput.value = "";
            }
            var wrap = input.closest(".field");
            if (wrap) {
              wrap.classList.remove("invalid");
            }
          });
        });
      }

      function populate(key, position) {
        var st = state[key];
        if (!st) {
          return;
        }
        Object.keys(st.flatInputs).forEach(function (k) {
          setValueInto(st.flatInputs[k], position[k]);
        });
        var row = (position.rows && position.rows[0]) || {};
        Object.keys(st.rowInputs).forEach(function (k) {
          setValueInto(st.rowInputs[k], row[k]);
        });
      }

      function hasCombos(key) {
        var cat = category(key);
        return !!(cat && cat.dimension_combos && cat.dimension_combos.length);
      }

      function balanceFor(key, values) {
        var cat = category(key);
        var combos = cat && cat.dimension_combos;
        var row = values.rows && values.rows[0];
        if (!combos || !combos.length || !row) {
          return null;
        }
        return findComboBalance(
          combos,
          values.breed,
          formatServerNumber(row.thickness),
          formatServerNumber(row.width),
          formatServerNumber(row.length)
        );
      }

      function measureOf(key, values) {
        var cat = category(key);
        var row = values.rows && values.rows[0];
        if (!row) {
          return null;
        }
        var kind = rowMeasureKind(cat && cat.product, row.thickness, row.width);
        if (!kind) {
          return null;
        }
        var qty = numberOrZero(row.quantity);
        if (qty <= 0) {
          return null;
        }
        return { kind: kind, amount: pieceMeasure(row.thickness, row.width, row.length, kind) * qty };
      }

      function summaryText(key, values) {
        var cat = category(key);
        var text = cat ? cat.label : key;
        var row = values.rows && values.rows[0];
        if (row) {
          var dims = [row.thickness, row.width, row.length].filter(function (v) {
            return v !== undefined && v !== null && v !== "";
          }).join("x");
          if (dims) {
            text += ", " + dims;
          }
          if (row.quantity) {
            text += " × " + row.quantity + " шт";
          }
          var measureText = computeMeasureText(cat && cat.product, row.thickness, row.width, row.length, row.quantity);
          if (measureText) {
            text += " — " + measureText;
          }
        }
        if (values.breed) {
          text += " (" + values.breed + ")";
        }
        return text;
      }

      function makeItem(key, values) {
        var numericKey = Number(key);
        var position = { category_operation_id: isNaN(numericKey) ? key : numericKey };
        Object.keys(values).forEach(function (k) {
          position[k] = values[k];
        });
        return {
          key: key,
          position: position,
          summary: summaryText(key, values),
          isNew: side === "take" && hasCombos(key) && balanceFor(key, values) === null,
          measure: measureOf(key, values),
        };
      }

      function itemFromEntry(entry) {
        var key = String(entry.category_operation_id);
        if (!state[key]) {
          return null;
        }
        var values = {};
        if (entry.breed) {
          values.breed = entry.breed;
        }
        if (entry.rows && entry.rows.length) {
          values.rows = [pickRow(entry.rows[0])];
        }
        return makeItem(key, values);
      }

      function refreshCount() {
        if (single) {
          return;
        }
        if (!cart.length) {
          countEl.textContent = "пусто";
          return;
        }
        var quantity = 0;
        var byUnit = {};
        cart.forEach(function (item) {
          var row = item.position.rows && item.position.rows[0];
          quantity += numberOrZero(row && row.quantity);
          if (item.measure) {
            byUnit[item.measure.kind] = (byUnit[item.measure.kind] || 0) + item.measure.amount;
          }
        });
        var parts = [pluralPositions(cart.length), formatServerNumber(quantity) + " шт"];
        Object.keys(byUnit).forEach(function (kind) {
          parts.push(formatServerNumber(byUnit[kind]) + " " + MEASURE_UNIT_BY_KIND[kind]);
        });
        countEl.textContent = parts.join(" · ");
      }

      function renderCart() {
        if (single) {
          return;
        }
        cartList.innerHTML = "";
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
          row.appendChild(textWrap);
          var actions = document.createElement("div");
          actions.className = "cart-item-actions";
          var editBtn = document.createElement("button");
          editBtn.type = "button";
          editBtn.className = "cart-item-btn";
          editBtn.textContent = "✎";
          editBtn.addEventListener("click", function () {
            editItem(index);
          });
          actions.appendChild(editBtn);
          var removeBtn = document.createElement("button");
          removeBtn.type = "button";
          removeBtn.className = "cart-item-btn cart-item-btn-remove";
          removeBtn.textContent = "✕";
          removeBtn.addEventListener("click", function () {
            removeItem(index);
          });
          actions.appendChild(removeBtn);
          row.appendChild(actions);
          cartList.appendChild(row);
        });
        refreshCount();
      }

      function stockProblem(key, values) {
        if (side !== "give") {
          return null;
        }
        var available = balanceFor(key, values);
        var row = values.rows && values.rows[0];
        if (available !== null && row && Number(row.quantity) > available) {
          return "На складе только " + available + " шт. Уменьшите количество в блоке «" + title + "».";
        }
        return null;
      }

      function addCurrent() {
        errorEl.textContent = "";
        var key = select.value;
        var result = collect(key);
        if (!result.ok || result.empty) {
          fail("Заполните все поля позиции в блоке «" + title + "».");
          return;
        }
        var problem = stockProblem(key, result.values);
        if (problem) {
          fail(problem);
          return;
        }
        cart.push(makeItem(key, result.values));
        clearInputs(key);
        closeFields();
        renderCart();
        haptic("success");
      }

      function editItem(index) {
        var item = cart[index];
        if (!item) {
          return;
        }
        cart.splice(index, 1);
        openFields();
        select.value = item.key;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        populate(item.key, item.position);
        errorEl.textContent = "";
        renderCart();
        block.scrollIntoView({ behavior: "smooth", block: "start" });
      }

      function removeItem(index) {
        cart.splice(index, 1);
        renderCart();
      }

      if (!single) {
        openButton.addEventListener("click", function () {
          errorEl.textContent = "";
          openFields();
          fieldsWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        commitButton.addEventListener("click", addCurrent);
        cancelButton.addEventListener("click", function () {
          clearInputs(select.value);
          errorEl.textContent = "";
          closeFields();
        });
      }

      // Усі позиції сторони: кошик + те, що в полях. null - помилка (текст
      // уже показано). Порожні поля не заважають.
      function finalize() {
        if (!fieldsOpen()) {
          return cart.slice();
        }
        var key = select.value;
        var result = collect(key);
        if (!result.ok) {
          fail("Заполните все поля позиции в блоке «" + title + "» или очистите их.");
          return null;
        }
        var items = cart.slice();
        if (!result.empty) {
          var problem = stockProblem(key, result.values);
          if (problem) {
            fail(problem);
            return null;
          }
          items.push(makeItem(key, result.values));
        }
        return items;
      }

      function isEmpty() {
        if (cart.length) {
          return false;
        }
        if (!fieldsOpen()) {
          return true;
        }
        var result = collect(select.value);
        return !!result.empty;
      }

      // Повернути позиції у блок: для «Отдаём» - у кошик, для «Получаем» -
      // у поля (він один).
      function restore(entries) {
        var items = (entries || []).map(itemFromEntry).filter(function (item) { return item; });
        if (single) {
          var item = items[0];
          if (item) {
            select.value = item.key;
            select.dispatchEvent(new Event("change", { bubbles: true }));
            populate(item.key, item.position);
          }
          return;
        }
        items.forEach(function (item) {
          cart.push(item);
        });
        renderCart();
      }

      function reset() {
        cart = [];
        clearInputs(select.value);
        if (cats.length) {
          select.value = String(cats[0].key);
          showCategory(select.value);
        }
        closeFields();
        renderCart();
      }

      return {
        element: block, finalize: finalize, restore: restore, reset: reset, isEmpty: isEmpty,
        itemsFromEntries: function (entries) {
          return (entries || []).map(itemFromEntry).filter(function (item) { return item; });
        },
        title: title,
      };
    }

    var giveBlock = buildBlock("give", "Отдаём", "+ Добавить в «Отдаём»", ctx.give_categories || [], false);
    var takeBlock = buildBlock("take", "Получаем", "", ctx.take_categories || [], true);
    rowsContainer.appendChild(giveBlock.element);
    rowsContainer.appendChild(takeBlock.element);

    var commonInputs = {};
    (ctx.common_fields || []).forEach(function (field) {
      commonInputs[field.key] = buildFieldElement(field, singleContainer);
    });

    // Список замін (рішення користувача 2026-09-06): блок «Добавлено» з самого
    // верху, «Сохранить и продолжить» згортає поточну заміну в рядок, далі
    // видно лише список і «Добавить обмен» (CSS #form.collapsed).
    var exchanges = [];
    var exchangesSection = document.createElement("div");
    exchangesSection.className = "cart-section";
    exchangesSection.style.display = "none";
    var exchangesHeader = document.createElement("div");
    exchangesHeader.className = "cart-header";
    exchangesHeader.textContent = "Добавлено:";
    var exchangesList = document.createElement("div");
    exchangesList.className = "cart-list";
    exchangesSection.appendChild(exchangesHeader);
    exchangesSection.appendChild(exchangesList);
    formEl.insertBefore(exchangesSection, formEl.firstChild);

    var addMoreButton = document.createElement("button");
    addMoreButton.type = "button";
    addMoreButton.className = "add-position-button add-more-button";
    addMoreButton.textContent = "Добавить обмен";
    formEl.insertBefore(addMoreButton, exchangesSection.nextSibling);

    var saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "add-position-button";
    saveButton.textContent = "Сохранить и продолжить";
    rowsContainer.parentNode.insertBefore(saveButton, rowsContainer.nextSibling);

    function setFormCollapsed(state) {
      formEl.classList.toggle("collapsed", !!state && exchanges.length > 0);
    }


    function renderExchanges() {
      exchangesList.innerHTML = "";
      exchangesSection.style.display = exchanges.length ? "" : "none";
      exchanges.forEach(function (block, index) {
        var row = document.createElement("div");
        row.className = "cart-item";
        var textWrap = document.createElement("div");
        textWrap.className = "cart-item-text-wrap";
        // Обраний вигляд (2026-09-09): рядок на КОЖНУ позицію, «−» - зі
        // складу, «+» - на склад. Раніше обидва боки склеювались через «; »
        // в один рядок і обрізались - другий розмір людина просто не бачила,
        // хоча він відправлявся.
        function exchangeCartLine(sign, item) {
          var line = document.createElement("div");
          line.className = "exchange-cart-line";
          var mark = document.createElement("span");
          mark.className = "exchange-cart-sign " + (sign === "-" ? "give" : "take");
          mark.textContent = sign === "-" ? "−" : "+";
          line.appendChild(mark);
          var body = document.createElement("span");
          body.textContent = item.summary + (item.isNew ? " — новая позиция" : "");
          line.appendChild(body);
          textWrap.appendChild(line);
        }
        block.give.forEach(function (item) { exchangeCartLine("-", item); });
        block.take.forEach(function (item) { exchangeCartLine("+", item); });
        row.appendChild(textWrap);
        var actions = document.createElement("div");
        actions.className = "cart-item-actions";
        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "cart-item-btn";
        editBtn.textContent = "✎";
        editBtn.addEventListener("click", function () {
          editExchange(index);
        });
        actions.appendChild(editBtn);
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "cart-item-btn cart-item-btn-remove";
        removeBtn.textContent = "✕";
        removeBtn.addEventListener("click", function () {
          exchanges.splice(index, 1);
          renderExchanges();
          if (!exchanges.length) {
            setFormCollapsed(false);
          }
        });
        actions.appendChild(removeBtn);
        row.appendChild(actions);
        exchangesList.appendChild(row);
      });
    }

    // Поточна заміна з обох блоків: {give, take}; {empty:true}, якщо обидва
    // порожні; null - помилка (текст уже показано).
    function collectCurrentExchange() {
      var giveItems = giveBlock.finalize();
      if (giveItems === null) {
        return null;
      }
      var takeItems = takeBlock.finalize();
      if (takeItems === null) {
        return null;
      }
      if (!giveItems.length && !takeItems.length) {
        return { empty: true };
      }
      if (!giveItems.length) {
        fail("В блоке «Отдаём» пока пусто — добавьте хотя бы одну позицию. Введённое сохранено.");
        return null;
      }
      if (!takeItems.length) {
        fail("В блоке «Получаем» пока пусто — укажите размер. Введённое сохранено.");
        return null;
      }
      return { give: giveItems, take: takeItems };
    }

    function editExchange(index) {
      var block = exchanges[index];
      if (!block) {
        return;
      }
      if (!giveBlock.isEmpty() || !takeBlock.isEmpty()) {
        fail("Сначала сохраните или очистите текущий обмен.");
        return;
      }
      exchanges.splice(index, 1);
      giveBlock.reset();
      takeBlock.reset();
      giveBlock.restore(block.give.map(function (item) { return item.position; }));
      takeBlock.restore(block.take.map(function (item) { return item.position; }));
      errorEl.textContent = "";
      renderExchanges();
      setFormCollapsed(false);
      giveBlock.element.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    saveButton.addEventListener("click", function () {
      errorEl.textContent = "";
      var block = collectCurrentExchange();
      if (block === null) {
        return;
      }
      if (block.empty) {
        fail("Заполните обмен, прежде чем сохранять.");
        return;
      }
      exchanges.push(block);
      giveBlock.reset();
      takeBlock.reset();
      renderExchanges();
      setFormCollapsed(true);
      haptic("success");
    });

    addMoreButton.addEventListener("click", function () {
      errorEl.textContent = "";
      setFormCollapsed(false);
    });

    function buildSummary(blocks, comment) {
      var wrap = document.createElement("div");
      var multi = blocks.length > 1;
      blocks.forEach(function (block, index) {
        if (multi) {
          var blockHeading = document.createElement("div");
          blockHeading.className = "exchange-summary-title";
          blockHeading.textContent = "Замена " + (index + 1);
          wrap.appendChild(blockHeading);
        }
        function section(title, cls, items) {
          var heading = document.createElement("div");
          heading.className = "exchange-summary-title " + cls;
          heading.textContent = title;
          wrap.appendChild(heading);
          items.forEach(function (item, itemIndex) {
            var line = document.createElement("div");
            line.textContent = (itemIndex + 1) + ". " + item.summary + (item.isNew ? " — новая позиция" : "");
            wrap.appendChild(line);
          });
        }
        section("Отдаём", "give", block.give);
        section("Получаем", "take", block.take);
      });
      if (comment) {
        var commentHeading = document.createElement("div");
        commentHeading.className = "exchange-summary-title comment";
        commentHeading.textContent = "Комментарий";
        wrap.appendChild(commentHeading);
        var commentLine = document.createElement("div");
        commentLine.className = "exchange-summary-comment";
        commentLine.textContent = comment;
        wrap.appendChild(commentLine);
      }
      return wrap;
    }

    function showConfirm(payload, summary) {
      confirmPayload = payload;
      confirmSummaryEl.innerHTML = "";
      confirmSummaryEl.appendChild(summary);
      errorEl.textContent = "";
      formEl.style.display = "none";
      confirmView.style.display = "";
    }

    function hideConfirm() {
      confirmPayload = null;
      confirmView.style.display = "none";
      formEl.style.display = "";
    }
    confirmEditButton.addEventListener("click", hideConfirm);

    var isSending = false;
    function send(payload) {
      if (isSending) {
        return;
      }
      var json = JSON.stringify(payload);
      if (!tg) {
        window.alert(json);
        return;
      }
      if (new TextEncoder().encode(json).length > 4000) {
        window.alert("Слишком много позиций для одной отправки — разделите обмен на два.");
        return;
      }
      isSending = true;
      if (tg.MainButton && tg.MainButton.showProgress) {
        tg.MainButton.showProgress(false);
      }
      tg.sendData(json);
    }

    function submit() {
      if (confirmPayload) {
        send(confirmPayload);
        return;
      }
      errorEl.textContent = "";
      var blocks = exchanges.slice();
      var current = collectCurrentExchange();
      if (current === null) {
        return;
      }
      if (!current.empty) {
        blocks.push(current);
      }
      if (!blocks.length) {
        fail("Добавьте хотя бы один обмен: что отдаём и что получаем.");
        return;
      }
      var comment = commonInputs.comment ? String(readFieldValue(commonInputs.comment) || "").trim() : "";
      var payload = {
        positions_kind: "exchange",
        exchanges: blocks.map(function (block) {
          return {
            give: block.give.map(function (item) { return item.position; }),
            take: block.take.map(function (item) { return item.position; }),
          };
        }),
      };
      if (comment) {
        payload.comment = comment;
      }
      showConfirm(payload, buildSummary(blocks, comment));
    }

    if (ctx.resume) {
      var resumeBlocks = ctx.resume.exchanges;
      if (!resumeBlocks && (ctx.resume.give || ctx.resume.take)) {
        resumeBlocks = [{ give: ctx.resume.give || [], take: ctx.resume.take || [] }];
      }
      (resumeBlocks || []).forEach(function (entry) {
        var give = giveBlock.itemsFromEntries(entry.give);
        var take = takeBlock.itemsFromEntries(entry.take);
        if (give.length || take.length) {
          exchanges.push({ give: give, take: take });
        }
      });
      renderExchanges();
      if (exchanges.length) {
        setFormCollapsed(true);
      }
      if (ctx.resume.common && commonInputs.comment) {
        setValueInto(commonInputs.comment, ctx.resume.common.comment);
      }
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

  // ---------------- Адмін-форма (2026-09-06) ----------------
  // Меню з двох кнопок -> «Журнал операций» (стрічка з фільтрами) і
  // «Коррекция остатков» (людина вводить лише «Стало, шт», ± і одиниці рахує
  // програма; «Сохранить и продолжить» збирає список, «Отправить» - у бот).
  function adminForm(ctx) {
    var app = document.getElementById("app");
    var titleEl = document.getElementById("title");
    var formEl = document.getElementById("form");
    var rowsContainer = document.getElementById("rows");
    var singleContainer = document.getElementById("single-fields");
    var errorEl = document.getElementById("error");
    var confirmView = document.getElementById("confirm-view");
    var confirmSummaryEl = document.getElementById("confirm-summary");
    var confirmEditButton = document.getElementById("confirm-edit-button");
    var fallback = document.getElementById("fallback-submit");
    var token = window.__formToken || null;
    var labels = ctx.operation_labels || {};
    var confirmPayload = null;

    function haptic(kind) {
      if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred(kind);
      }
    }
    function fail(text) {
      errorEl.textContent = text;
      haptic("error");
    }
    function signed(value, digits) {
      var rounded = Math.round(Number(value) * 10000) / 10000;
      return (rounded < 0 ? "−" : "+") + formatServerNumber(Math.abs(rounded));
    }

    // ---- шапка з кнопкою «назад» (закріплена), Esc ----
    var topBar = document.createElement("div");
    topBar.className = "admin-top";
    var backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "admin-back";
    backButton.textContent = "‹ Назад";
    var topTitle = document.createElement("span");
    topTitle.className = "admin-top-title";
    topBar.appendChild(backButton);
    topBar.appendChild(topTitle);
    app.insertBefore(topBar, titleEl);
    titleEl.style.display = "none";

    var menuScreen = document.createElement("div");
    menuScreen.className = "admin-screen";
    var journalScreen = document.createElement("div");
    journalScreen.className = "admin-screen";
    // Відкат операції (рішення користувача, 2026-09-10): окремий екран
    // «Откат операции» з карткою операції та полем коментаря.
    var rollbackScreen = document.createElement("div");
    rollbackScreen.className = "admin-screen";
    app.insertBefore(menuScreen, formEl);
    app.insertBefore(journalScreen, formEl);
    app.insertBefore(rollbackScreen, formEl);

    var SCREEN_TITLES = {
      menu: "Админ", journal: "Журнал операций", correction: "Коррекция остатков", rollback: "Откат операции",
    };
    var current = "menu";
    var journalLoaded = false;
    function showScreen(name) {
      current = name;
      menuScreen.hidden = name !== "menu";
      journalScreen.hidden = name !== "journal";
      rollbackScreen.hidden = name !== "rollback";
      formEl.style.display = name === "correction" ? "" : "none";
      confirmView.style.display = "none";
      confirmPayload = null;
      errorEl.textContent = "";
      backButton.style.visibility = name === "menu" ? "hidden" : "";
      topTitle.textContent = SCREEN_TITLES[name] || "Админ";
      var sends = name === "correction" || name === "rollback";
      if (tg && tg.MainButton) {
        if (sends) {
          tg.MainButton.setText("Отправить");
          tg.MainButton.show();
        } else {
          tg.MainButton.hide();
        }
      } else {
        fallback.style.display = sends ? "block" : "none";
      }
      if (name === "journal" && !journalLoaded) {
        loadJournal(true);
      }
      window.scrollTo(0, 0);
    }
    function goBack() {
      if (confirmView.style.display !== "none") {
        hideConfirm();
        return;
      }
      // Esc/«Назад» з екрана відкату - один крок назад, у журнал.
      if (current === "rollback") {
        showScreen("journal");
        return;
      }
      if (current !== "menu") {
        showScreen("menu");
      }
    }
    backButton.addEventListener("click", goBack);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        goBack();
      }
    });

    // ---- меню ----
    function menuButton(text, target) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "admin-menu-button";
      button.textContent = text;
      button.addEventListener("click", function () {
        showScreen(target);
      });
      return button;
    }
    menuScreen.appendChild(menuButton("📒 Журнал операций", "journal"));
    menuScreen.appendChild(menuButton("✏️ Коррекция остатков", "correction"));
    var firstEntry = ctx.journal && ctx.journal.entries && ctx.journal.entries[0];
    if (firstEntry) {
      var menuHint = document.createElement("div");
      menuHint.className = "admin-menu-hint";
      menuHint.textContent = "Последняя запись: " + firstEntry.time + " · " + (firstEntry.document || firstEntry.type_label) + (firstEntry.who ? " · " + firstEntry.who : "");
      menuScreen.appendChild(menuHint);
    }

    // ---- журнал ----
    var PAGE = 50;
    var journalOffset = 0;
    var filtersBar = document.createElement("div");
    filtersBar.className = "journal-filters";
    function makeSelect(options) {
      var select = document.createElement("select");
      select.className = "journal-select";
      options.forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        select.appendChild(option);
      });
      return select;
    }
    // Рішення користувача (2026-09-06): фільтр за операціями - кольорові
    // прапорці-чипи («Приход», «Обмен»…) з «Все» / «Ничего»; бачити лише
    // приходи чи всі обміни - один дотик.
    var groups = ctx.journal_groups || [];
    var chipsRow = document.createElement("div");
    chipsRow.className = "journal-chips";
    var chipState = {};
    var chipButtons = {};
    groups.forEach(function (pair) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "journal-chip journal-chip-" + baseType(pair[1][0]) + " on";
      chip.textContent = pair[0];
      chipState[pair[0]] = true;
      chip.addEventListener("click", function () {
        chipState[pair[0]] = !chipState[pair[0]];
        chip.classList.toggle("on", chipState[pair[0]]);
        loadJournal(true);
      });
      chipButtons[pair[0]] = chip;
      chipsRow.appendChild(chip);
    });
    function setAllChips(value) {
      groups.forEach(function (pair) {
        chipState[pair[0]] = value;
        chipButtons[pair[0]].classList.toggle("on", value);
      });
    }
    var allChip = document.createElement("button");
    allChip.type = "button";
    allChip.className = "journal-mini";
    allChip.textContent = "Все";
    allChip.addEventListener("click", function () { setAllChips(true); loadJournal(true); });
    var noneChip = document.createElement("button");
    noneChip.type = "button";
    noneChip.className = "journal-mini";
    noneChip.textContent = "Ничего";
    noneChip.addEventListener("click", function () { setAllChips(false); loadJournal(true); });
    chipsRow.appendChild(allChip);
    chipsRow.appendChild(noneChip);
    function baseType(type) {
      return String(type || "").indexOf("exchange") === 0 ? "exchange" : String(type || "");
    }
    function groupLabelFor(type) {
      for (var i = 0; i < groups.length; i++) {
        if (groups[i][1].indexOf(type) !== -1) {
          return groups[i][0];
        }
      }
      return labels[type] || type;
    }
    // Рішення користувача (2026-09-06, живий тест): замість списку
    // продуктів - період «с» і «до» з вибором дати, завжди видно; швидкі
    // періоди лишаються і заповнюють дати; усе застосовується одразу.
    // Рішення користувача (2026-09-06): період як в антисептируванні -
    // швидкі кнопки та окрема «Свой период…» з вікном «С даты / По дату /
    // Показать результат»; усе застосовується одразу.
    var PERIOD_PRESETS = [["today", "Сегодня"], ["yesterday", "Вчера"], ["week", "Неделя"], ["month", "Месяц"], ["all", "Весь период"]];
    var period = { key: "all", from: "", to: "" };
    var periodRow = document.createElement("div");
    periodRow.className = "journal-chips journal-periods";
    var periodButtons = {};
    PERIOD_PRESETS.forEach(function (pair) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "journal-period";
      button.textContent = pair[1];
      button.addEventListener("click", function () {
        period.key = pair[0];
        applyPreset();
        renderPeriods();
        loadJournal(true);
      });
      periodButtons[pair[0]] = button;
      periodRow.appendChild(button);
    });
    var customButton = document.createElement("button");
    customButton.type = "button";
    customButton.className = "journal-period journal-period-custom";
    customButton.textContent = "Свой период…";
    customButton.addEventListener("click", openPeriodModal);
    periodRow.appendChild(customButton);
    function shortDate(iso) {
      if (!iso) {
        return "…";
      }
      var parts = iso.split("-");
      return parts.length === 3 ? parts[2] + "." + parts[1] + "." + parts[0].slice(2) : iso;
    }
    function renderPeriods() {
      Object.keys(periodButtons).forEach(function (key) {
        periodButtons[key].classList.toggle("on", period.key === key);
      });
      var custom = period.key === "custom";
      customButton.classList.toggle("on", custom);
      customButton.textContent = custom ? "Свой период: " + shortDate(period.from) + " — " + shortDate(period.to) : "Свой период…";
    }
    function applyPreset() {
      var today = new Date();
      if (period.key === "today") {
        period.from = isoDate(today);
        period.to = isoDate(today);
      } else if (period.key === "yesterday") {
        var yesterday = new Date(today.getTime() - 86400000);
        period.from = isoDate(yesterday);
        period.to = isoDate(yesterday);
      } else if (period.key === "week") {
        period.from = isoDate(new Date(today.getTime() - 6 * 86400000));
        period.to = isoDate(today);
      } else if (period.key === "month") {
        period.from = isoDate(new Date(today.getTime() - 29 * 86400000));
        period.to = isoDate(today);
      } else if (period.key === "all") {
        period.from = "";
        period.to = "";
      }
    }
    // Вікно «Свой период» - те саме, що в антисептируванні.
    var periodModal = document.createElement("div");
    periodModal.className = "journal-modal-overlay";
    periodModal.style.display = "none";
    var periodCard = document.createElement("div");
    periodCard.className = "journal-modal";
    var periodHead = document.createElement("div");
    periodHead.className = "journal-modal-head";
    var periodTitle = document.createElement("span");
    periodTitle.textContent = "Свой период";
    var periodClose = document.createElement("span");
    periodClose.className = "journal-modal-close";
    periodClose.textContent = "×";
    periodClose.addEventListener("click", function () { periodModal.style.display = "none"; });
    periodHead.appendChild(periodTitle);
    periodHead.appendChild(periodClose);
    periodCard.appendChild(periodHead);
    var fromLabel = document.createElement("p");
    fromLabel.className = "journal-modal-label";
    fromLabel.textContent = "С даты";
    var dateFrom = document.createElement("input");
    dateFrom.type = "date";
    dateFrom.className = "journal-date";
    var toLabel = document.createElement("p");
    toLabel.className = "journal-modal-label";
    toLabel.textContent = "По дату";
    var dateTo = document.createElement("input");
    dateTo.type = "date";
    dateTo.className = "journal-date";
    var periodApply = document.createElement("button");
    periodApply.type = "button";
    periodApply.className = "add-position-button journal-modal-apply";
    periodApply.textContent = "Показать результат";
    periodApply.addEventListener("click", function () {
      period.key = "custom";
      period.from = dateFrom.value || "";
      period.to = dateTo.value || "";
      if (period.from && period.to && period.from > period.to) {
        var swap = period.from;
        period.from = period.to;
        period.to = swap;
      }
      periodModal.style.display = "none";
      renderPeriods();
      loadJournal(true);
    });
    [fromLabel, dateFrom, toLabel, dateTo, periodApply].forEach(function (el) {
      periodCard.appendChild(el);
    });
    periodModal.appendChild(periodCard);
    periodModal.addEventListener("click", function (event) {
      if (event.target === periodModal) {
        periodModal.style.display = "none";
      }
    });
    document.body.appendChild(periodModal);
    function openPeriodModal() {
      dateFrom.value = period.from || "";
      dateTo.value = period.to || "";
      periodModal.style.display = "flex";
    }
    var searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.className = "journal-search";
    searchInput.placeholder = "Размер, порода, № документа";
    [chipsRow, periodRow, searchInput].forEach(function (el) {
      filtersBar.appendChild(el);
    });
    renderPeriods();
    var searchTimer = null;
    searchInput.addEventListener("input", function () {
      if (searchTimer) {
        clearTimeout(searchTimer);
      }
      searchTimer = setTimeout(function () {
        searchTimer = null;
        loadJournal(true);
      }, 400);
    });
    var journalList = document.createElement("div");
    journalList.className = "journal-list";
    var moreButton = document.createElement("button");
    moreButton.type = "button";
    moreButton.className = "add-position-button";
    moreButton.textContent = "Показать ещё";
    moreButton.style.display = "none";
    journalScreen.appendChild(filtersBar);
    journalScreen.appendChild(journalList);
    journalScreen.appendChild(moreButton);

    function isoDate(date) {
      var y = date.getFullYear();
      var m = String(date.getMonth() + 1);
      var d = String(date.getDate());
      return y + "-" + (m.length < 2 ? "0" + m : m) + "-" + (d.length < 2 ? "0" + d : d);
    }
    function currentFilters() {
      var chosenTypes = [];
      var allOn = true;
      groups.forEach(function (pair) {
        if (chipState[pair[0]]) {
          chosenTypes = chosenTypes.concat(pair[1]);
        } else {
          allOn = false;
        }
      });
      if (!groups.length) {
        allOn = true;
      }
      var filters = {
        types: allOn ? [] : (chosenTypes.length ? chosenTypes : ["__none__"]),
        product: "",
        search: searchInput.value.trim(),
        date_from: period.from || "",
        date_to: period.to || "",
      };
      return filters;
    }
    function filtersAreDefault(filters) {
      return !filters.types.length && !filters.product && !filters.search && !filters.date_from && !filters.date_to;
    }
    // Рішення користувача (2026-09-06): один документ - одна картка (обмін
    // «отдаём + получаем» разом), смужка кольору операції зліва, кольорова
    // позначка в шапці. Рухи одного документа йдуть підряд (той самий час),
    // тож групуємо сусідні записи з однаковим документом.
    var loadedEntries = [];
    var ROLE_LABELS = { exchange_out: "отдаём", exchange_in: "получаем" };
    function lineFor(entry) {
      var line = document.createElement("div");
      line.className = "journal-line";
      if (ROLE_LABELS[entry.type]) {
        var role = document.createElement("span");
        role.className = "journal-role";
        role.textContent = ROLE_LABELS[entry.type];
        line.appendChild(role);
      }
      var what = [entry.product, entry.breed, entry.condition && entry.condition !== entry.product ? entry.condition : "", entry.size].filter(function (v) { return v; }).join(" ");
      var whatEl = document.createElement("span");
      whatEl.textContent = what + " ";
      line.appendChild(whatEl);
      // Антисептик (2026-09-06): без «± шт / ± м3», лише дохід; продаж - рух
      // і сума. quantity === null означає «не рух складу».
      if (entry.quantity !== null && entry.quantity !== undefined) {
        var qty = Number(entry.quantity) || 0;
        var delta = document.createElement("span");
        delta.className = qty < 0 ? "journal-minus" : "journal-plus";
        var deltaText = signed(qty) + " шт";
        if (entry.measure !== null && entry.measure !== undefined && entry.unit) {
          deltaText += " · " + signed(entry.measure) + " " + entry.unit;
        }
        delta.textContent = deltaText;
        line.appendChild(delta);
      }
      if (entry.amount !== null && entry.amount !== undefined && entry.amount !== "") {
        var money = document.createElement("span");
        money.className = "journal-money";
        // Відкат (2026-09-10) уперше дає відʼємну суму - мінус той самий,
        // що й у штук/вимірів («−», не дефіс).
        money.textContent = formatMoney(entry.amount).replace(/^-/, "−") + " MDL";
        line.appendChild(money);
      }
      return line;
    }
    // Відкат (2026-09-10): картка відкату - вигляд 01 «як усі картки»:
    // тег «↶ Откат», рядок «откачена: [Продажа] час · хто», позиції зі
    // знаком, рядок коментаря (порожнє місце, якщо коментаря немає) і БЕЗ
    // кнопки відкату. Кнопка «↶ Откатить» - на всіх інших картках, крім
    // корекції (рішення користувача: корекцію не відкатують).
    var ROLLBACK_VERB = {
      sale: "откачена", income: "откачен", writeoff: "откачено",
      exchange_out: "откачен", exchange_in: "откачен", antiseptic: "откачено",
    };
    var NO_ROLLBACK_TYPES = { rollback: true, correction: true };
    function documentElement(group, preview) {
      var first = group[0];
      var base = baseType(first.type);
      var card = document.createElement("div");
      card.className = "journal-doc journal-doc-" + base;
      var head = document.createElement("div");
      head.className = "journal-doc-head";
      var tag = document.createElement("span");
      tag.className = "journal-tag journal-tag-" + base;
      tag.textContent = base === "rollback" ? "↶ " + groupLabelFor(first.type) : (first.document || groupLabelFor(first.type));
      head.appendChild(tag);
      var time = document.createElement("span");
      time.className = "journal-meta";
      time.textContent = first.time;
      head.appendChild(time);
      if (first.who) {
        var who = document.createElement("b");
        who.textContent = first.who;
        head.appendChild(who);
      }
      card.appendChild(head);
      var rolled = base === "rollback" ? (first.rollback_of || null) : null;
      if (base === "rollback") {
        var ref = document.createElement("div");
        ref.className = "journal-ref";
        var verb = (rolled && ROLLBACK_VERB[rolled.type]) || "откачено";
        ref.appendChild(document.createTextNode(verb + ": "));
        var refTag = document.createElement("span");
        refTag.className = "journal-tag journal-tag-" + baseType(rolled ? rolled.type : "");
        refTag.textContent = rolled ? (rolled.document || rolled.type_label || "") : "";
        ref.appendChild(refTag);
        var refMeta = [rolled ? rolled.time : "", rolled ? rolled.who : ""].filter(function (v) { return v; }).join(" · ");
        if (refMeta) {
          ref.appendChild(document.createTextNode(" " + refMeta));
        }
        card.appendChild(ref);
      }
      group.slice().sort(function (a, b) {
        return (a.type === "exchange_in" ? 1 : 0) - (b.type === "exchange_in" ? 1 : 0);
      }).forEach(function (entry) {
        card.appendChild(lineFor(entry));
      });
      var balances = [];
      var reasons = [];
      group.forEach(function (entry) {
        if (entry.balance_after !== null && entry.balance_after !== undefined) {
          balances.push(formatServerNumber(entry.balance_after));
        }
        if (base !== "rollback" && entry.reason && reasons.indexOf(entry.reason) === -1) {
          reasons.push(entry.reason);
        }
      });
      var tail = [];
      if (balances.length) {
        tail.push("остаток → " + balances.join(" · ") + " шт");
      }
      if (reasons.length) {
        tail.push(reasons.join(" · "));
      }
      if (tail.length) {
        var tailEl = document.createElement("div");
        tailEl.className = "journal-meta journal-doc-tail";
        tailEl.textContent = tail.join(" · ");
        card.appendChild(tailEl);
      }
      if (base === "rollback") {
        // Є коментар - показується; немає - місце лишається порожнім
        // (слова користувача: «пусто на тому місці коментаря»).
        var commentEl = document.createElement("div");
        if (first.reason) {
          commentEl.className = "journal-comment";
          commentEl.textContent = first.reason;
        } else {
          commentEl.className = "journal-comment-empty";
        }
        card.appendChild(commentEl);
      } else if (!preview && !NO_ROLLBACK_TYPES[base]) {
        var act = document.createElement("div");
        act.className = "journal-doc-act";
        var rollbackButton = document.createElement("button");
        rollbackButton.type = "button";
        rollbackButton.className = "journal-rollback-button";
        rollbackButton.textContent = "↶ Откатить";
        rollbackButton.addEventListener("click", function () {
          openRollback(group);
        });
        act.appendChild(rollbackButton);
        card.appendChild(act);
      }
      return card;
    }
    // Екран «Откат операции»: картка (без кнопки), що зміниться, коментар.
    var rollbackTarget = null;
    var rollbackCommentInput = null;
    function rollbackSummary(group) {
      var wrap = document.createElement("div");
      wrap.className = "rollback-summary";
      var first = group[0];
      var base = baseType(first.type);
      var headline = document.createElement("div");
      if (base === "antiseptic") {
        headline.textContent = "Запись услуги исчезнет из журнала и листа АНТИСЕПТИРОВАНИЕ.";
        wrap.appendChild(headline);
        return wrap;
      }
      headline.textContent = base === "sale" || base === "writeoff" ? "Вернётся на склад:" : "Изменится на складе:";
      wrap.appendChild(headline);
      group.forEach(function (entry) {
        if (entry.quantity === null || entry.quantity === undefined) {
          return;
        }
        var line = document.createElement("div");
        var what = [entry.product, entry.breed, entry.condition && entry.condition !== entry.product ? entry.condition : "", entry.size].filter(function (v) { return v; }).join(" ");
        var text = "• " + what + ": " + signed(-(Number(entry.quantity) || 0)) + " шт";
        if (entry.measure !== null && entry.measure !== undefined && entry.unit) {
          text += " · " + signed(-(Number(entry.measure) || 0)) + " " + entry.unit;
        }
        line.textContent = text;
        wrap.appendChild(line);
      });
      var sheetLine = document.createElement("div");
      sheetLine.textContent = "Строки этой операции исчезнут из её листа. Точный остаток покажет подтверждение в чате.";
      wrap.appendChild(sheetLine);
      return wrap;
    }
    function openRollback(group) {
      rollbackTarget = group;
      rollbackScreen.innerHTML = "";
      rollbackScreen.appendChild(documentElement(group, true));
      rollbackScreen.appendChild(rollbackSummary(group));
      var fieldWrap = document.createElement("div");
      rollbackScreen.appendChild(fieldWrap);
      rollbackCommentInput = buildFieldElement({ key: "rollback_comment", label: "Комментарий", type: "text", required: false }, fieldWrap);
      showScreen("rollback");
    }
    function groupEntries(entries) {
      var result = [];
      var current = null;
      var currentKey = null;
      entries.forEach(function (entry) {
        // Старі записи без номера документа (один прихід на кілька позицій)
        // тримаються разом за типом, часом і людиною. Час - до секунди
        // (created_at): одна операція = одна секунда запису, а хвилина
        // зліплювала б дві продажі поспіль (і два відкати) в одну картку.
        var base = baseType(entry.type);
        var key = (entry.document || ("~" + (entry.created_at || entry.time) + "|" + (entry.who || ""))) + "|" + base;
        if (base === "rollback" && entry.rollback_of) {
          key += "|" + (entry.rollback_of.created_at || "") + "|" + (entry.rollback_of.document || "");
        }
        if (current && key === currentKey) {
          current.push(entry);
          return;
        }
        current = [entry];
        currentKey = key;
        result.push(current);
      });
      return result;
    }
    function renderEntries(entries, append) {
      loadedEntries = append ? loadedEntries.concat(entries) : entries.slice();
      journalList.innerHTML = "";
      if (!loadedEntries.length) {
        var empty = document.createElement("div");
        empty.className = "journal-meta";
        empty.textContent = "Записей нет.";
        journalList.appendChild(empty);
        return;
      }
      groupEntries(loadedEntries).forEach(function (group) {
        journalList.appendChild(documentElement(group));
      });
    }
    function loadJournal(reset) {
      errorEl.textContent = "";
      if (reset) {
        journalOffset = 0;
      }
      var filters = currentFilters();
      if (reset && filtersAreDefault(filters) && ctx.journal && ctx.journal.entries) {
        renderEntries(ctx.journal.entries, false);
        journalOffset = ctx.journal.entries.length;
        moreButton.style.display = ctx.journal.has_more ? "" : "none";
        journalLoaded = true;
        return;
      }
      if (!token) {
        fail("Фильтры недоступны: откройте форму из чата бота.");
        return;
      }
      filters.limit = PAGE;
      filters.offset = journalOffset;
      moreButton.disabled = true;
      postTemplateAction({ action: "journal", token: token, filters: filters })
        .then(function (data) {
          var entries = data.entries || [];
          renderEntries(entries, !reset);
          journalOffset += entries.length;
          moreButton.style.display = data.has_more ? "" : "none";
          journalLoaded = true;
        })
        .catch(function (error) {
          fail(error && error.message ? error.message : "Не удалось загрузить журнал.");
        })
        .then(function () {
          moreButton.disabled = false;
        });
    }
    moreButton.addEventListener("click", function () {
      loadJournal(false);
    });

    // ---- корекція ----
    var categories = ctx.categories || [];
    var cart = [];
    var cartSection = document.createElement("div");
    cartSection.className = "cart-section";
    cartSection.style.display = "none";
    var cartHeader = document.createElement("div");
    cartHeader.className = "cart-header";
    cartHeader.textContent = "Добавлено:";
    var cartList = document.createElement("div");
    cartList.className = "cart-list";
    cartSection.appendChild(cartHeader);
    cartSection.appendChild(cartList);
    formEl.insertBefore(cartSection, formEl.firstChild);
    var addMoreButton = document.createElement("button");
    addMoreButton.type = "button";
    addMoreButton.className = "add-position-button add-more-button";
    addMoreButton.textContent = "Добавить позицию";
    formEl.insertBefore(addMoreButton, cartSection.nextSibling);

    var categoryWrap = document.createElement("div");
    categoryWrap.className = "field";
    var categoryLabel = document.createElement("label");
    categoryLabel.textContent = "Категория *";
    applyFieldLabelStyle(categoryLabel, "category");
    categoryWrap.appendChild(categoryLabel);
    var categorySelect = document.createElement("select");
    categorySelect.className = "field-wide";
    categories.forEach(function (cat) {
      var option = document.createElement("option");
      option.value = String(cat.key);
      option.textContent = cat.label;
      categorySelect.appendChild(option);
    });
    categoryWrap.appendChild(categorySelect);
    formEl.insertBefore(categoryWrap, rowsContainer);

    var state = {};
    categories.forEach(function (cat) {
      var block = document.createElement("div");
      block.className = "category-group";
      var fields = cat.fields || [];
      var identityFields = fields.filter(function (f) { return !isMeasureField(f); });
      var perRow = fields.filter(function (f) { return f.per_row && isMeasureField(f); });
      var flatInputs = {};
      identityFields.forEach(function (field) {
        flatInputs[field.key] = buildFieldElement(field, block);
      });
      var rowInputs = {};
      if (perRow.length) {
        var rowBlock = document.createElement("div");
        rowBlock.className = "row-block";
        perRow.forEach(function (field) {
          rowInputs[field.key] = buildFieldElement(field, rowBlock);
        });
        block.appendChild(rowBlock);
        wireDimensionCascade(rowInputs, cat.dimension_combos, flatInputs.breed);
      }
      var calc = document.createElement("div");
      calc.className = "correction-calc";
      calc.style.display = "none";
      block.appendChild(calc);
      rowsContainer.appendChild(block);
      state[String(cat.key)] = { cat: cat, block: block, fields: fields, rowInputs: rowInputs, flatInputs: flatInputs, calc: calc };

      function refreshCalc() {
        var info = calcFor(String(cat.key));
        if (!info) {
          calc.style.display = "none";
          return;
        }
        calc.style.display = "";
        calc.className = "correction-calc" + (info.missing ? " correction-calc-missing" : "");
        calc.textContent = info.text;
      }
      Object.keys(rowInputs).concat(Object.keys(flatInputs)).forEach(function (key) {
        var input = rowInputs[key] || flatInputs[key];
        input.addEventListener("input", refreshCalc);
        input.addEventListener("change", refreshCalc);
        if (input.manualInput) {
          input.manualInput.addEventListener("input", refreshCalc);
        }
      });
    });
    function showCategory(key) {
      Object.keys(state).forEach(function (k) {
        state[k].block.style.display = k === key ? "" : "none";
      });
    }
    categorySelect.addEventListener("change", function () {
      showCategory(categorySelect.value);
    });
    if (categories.length) {
      categorySelect.value = String(categories[0].key);
      showCategory(categorySelect.value);
    }

    var commentInput = buildFieldElement({ key: "comment", label: "Причина", type: "text", required: false }, singleContainer);

    var saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "add-position-button";
    saveButton.textContent = "Сохранить и продолжить";
    rowsContainer.parentNode.insertBefore(saveButton, rowsContainer.nextSibling);

    function readDims(st) {
      var dims = {};
      ["thickness", "width", "length", "quantity"].forEach(function (key) {
        dims[key] = st.rowInputs[key] ? readFieldValue(st.rowInputs[key]) : "";
      });
      dims.breed = st.flatInputs.breed ? readFieldValue(st.flatInputs.breed) : "";
      return dims;
    }
    // Сейчас / Стало / різниця для введеного розміру; null - ще нічого не введено.
    function calcFor(key) {
      var st = state[key];
      if (!st) {
        return null;
      }
      var dims = readDims(st);
      if (!dims.thickness || !dims.width || !dims.length) {
        return null;
      }
      var combos = st.cat.dimension_combos || [];
      var current = findComboBalance(combos, dims.breed, formatServerNumber(dims.thickness), formatServerNumber(dims.width), formatServerNumber(dims.length));
      if (current === null) {
        return { missing: true, text: "Такого размера нет на складе — коррекция только для существующих позиций." };
      }
      var currentNumber = numberOrZero(current);
      if (dims.quantity === "") {
        return { current: currentNumber, text: "Сейчас: " + formatServerNumber(currentNumber) + " шт" };
      }
      var newQty = numberOrZero(dims.quantity);
      var delta = newQty - currentNumber;
      var kind = rowMeasureKind(st.cat.product, dims.thickness, dims.width);
      var text = "Сейчас: " + formatServerNumber(currentNumber) + " шт → станет " + formatServerNumber(newQty) + " шт: " + signed(delta) + " шт";
      if (kind) {
        text += " · " + signed(pieceMeasure(dims.thickness, dims.width, dims.length, kind) * delta) + " " + MEASURE_UNIT_BY_KIND[kind];
      }
      return { current: currentNumber, newQty: newQty, delta: delta, kind: kind, text: text };
    }
    function collectCurrent() {
      var key = categorySelect.value;
      var st = state[key];
      if (!st) {
        return { ok: false };
      }
      var dims = readDims(st);
      var filled = dims.thickness || dims.width || dims.length || dims.quantity;
      if (!filled) {
        return { ok: true, empty: true };
      }
      if (!dims.thickness || !dims.width || !dims.length || dims.quantity === "" || !dims.breed) {
        fail("Заполните породу, размер и «Стало, шт».");
        return { ok: false };
      }
      var info = calcFor(key);
      if (!info || info.missing) {
        fail("Такого размера нет на складе — коррекция только для существующих позиций.");
        return { ok: false };
      }
      var numericKey = Number(key);
      var position = {
        category_operation_id: isNaN(numericKey) ? key : numericKey,
        breed: dims.breed,
        rows: [{ thickness: dims.thickness, width: dims.width, length: dims.length, new_quantity: info.newQty }],
      };
      var summary = st.cat.label + " " + [dims.thickness, dims.width, dims.length].join("×") + " (" + dims.breed + "): " +
        formatServerNumber(info.current) + " → " + formatServerNumber(info.newQty) + " шт";
      var deltaText = signed(info.delta) + " шт" + (info.kind ? " · " + signed(pieceMeasure(dims.thickness, dims.width, dims.length, info.kind) * info.delta) + " " + MEASURE_UNIT_BY_KIND[info.kind] : "");
      return { ok: true, empty: false, item: { key: key, position: position, summary: summary, deltaText: deltaText, delta: info.delta } };
    }
    function clearCurrent(key) {
      var st = state[key];
      if (!st) {
        return;
      }
      [st.rowInputs, st.flatInputs].forEach(function (group) {
        Object.keys(group).forEach(function (k) {
          var input = group[k];
          input.value = "";
          if (input.manualInput) {
            input.manualInput.value = "";
          }
          var wrap = input.closest(".field");
          if (wrap) {
            wrap.classList.remove("invalid");
          }
        });
      });
      st.calc.style.display = "none";
    }
    function setInto(input, value) {
      if (value === undefined || value === null || value === "") {
        return;
      }
      var stringValue = String(value);
      if (input.tagName === "SELECT") {
        for (var i = 0; i < input.options.length; i++) {
          if (input.options[i].value === stringValue) {
            input.value = stringValue;
            if (input.manualInput) {
              input.manualInput.value = "";
            }
            input.dispatchEvent(new Event("change", { bubbles: true }));
            return;
          }
        }
        if (input.manualInput) {
          input.value = "";
          input.manualInput.value = stringValue;
          input.manualInput.dispatchEvent(new Event("input", { bubbles: true }));
          return;
        }
      }
      input.value = stringValue;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    function populate(item) {
      var st = state[item.key];
      if (!st) {
        return;
      }
      categorySelect.value = item.key;
      showCategory(item.key);
      setInto(st.flatInputs.breed, item.position.breed);
      var row = item.position.rows[0];
      setInto(st.rowInputs.thickness, row.thickness);
      setInto(st.rowInputs.width, row.width);
      setInto(st.rowInputs.length, row.length);
      setInto(st.rowInputs.quantity, row.new_quantity);
    }
    function setFormCollapsed(collapsed) {
      formEl.classList.toggle("collapsed", !!collapsed && cart.length > 0);
    }
    function renderCart() {
      cartList.innerHTML = "";
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
        var sum = document.createElement("span");
        sum.className = "cart-item-sum " + (item.delta < 0 ? "journal-minus" : "journal-plus");
        sum.textContent = item.deltaText;
        textWrap.appendChild(sum);
        row.appendChild(textWrap);
        var actions = document.createElement("div");
        actions.className = "cart-item-actions";
        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "cart-item-btn";
        editBtn.textContent = "✎";
        editBtn.addEventListener("click", function () {
          cart.splice(index, 1);
          renderCart();
          populate(item);
          setFormCollapsed(false);
        });
        actions.appendChild(editBtn);
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "cart-item-btn cart-item-btn-remove";
        removeBtn.textContent = "✕";
        removeBtn.addEventListener("click", function () {
          cart.splice(index, 1);
          renderCart();
          if (!cart.length) {
            setFormCollapsed(false);
          }
        });
        actions.appendChild(removeBtn);
        row.appendChild(actions);
        cartList.appendChild(row);
      });
    }
    saveButton.addEventListener("click", function () {
      errorEl.textContent = "";
      var result = collectCurrent();
      if (!result.ok) {
        return;
      }
      if (result.empty) {
        fail("Заполните позицию, прежде чем сохранять.");
        return;
      }
      cart.push(result.item);
      clearCurrent(result.item.key);
      renderCart();
      setFormCollapsed(true);
      haptic("success");
    });
    addMoreButton.addEventListener("click", function () {
      errorEl.textContent = "";
      setFormCollapsed(false);
    });

    function buildSummary(items, comment) {
      var wrap = document.createElement("div");
      items.forEach(function (item, index) {
        var line = document.createElement("div");
        line.className = "confirm-position-line";
        line.textContent = (index + 1) + ". " + item.summary + " (" + item.deltaText + ")";
        wrap.appendChild(line);
      });
      if (comment) {
        var commentLine = document.createElement("div");
        commentLine.className = "confirm-common";
        commentLine.textContent = "Причина: " + comment;
        wrap.appendChild(commentLine);
      }
      return wrap;
    }
    function showConfirm(payload, summary) {
      confirmPayload = payload;
      confirmSummaryEl.innerHTML = "";
      confirmSummaryEl.appendChild(summary);
      errorEl.textContent = "";
      formEl.style.display = "none";
      confirmView.style.display = "";
    }
    function hideConfirm() {
      confirmPayload = null;
      confirmView.style.display = "none";
      formEl.style.display = "";
    }
    confirmEditButton.addEventListener("click", hideConfirm);

    var isSending = false;
    // Відкат (2026-09-10): без проміжного confirm-view - підтвердження
    // одне, кнопкою в чаті, як у корекції.
    function submitRollback() {
      if (!rollbackTarget || isSending) {
        return;
      }
      var ids = rollbackTarget.map(function (entry) { return entry.id; }).filter(function (value) {
        return value !== null && value !== undefined;
      });
      if (!ids.length) {
        fail("Не удалось определить записи операции. Обновите журнал.");
        return;
      }
      var comment = rollbackCommentInput ? String(readFieldValue(rollbackCommentInput) || "").trim() : "";
      var payload = { positions_kind: "rollback", movement_ids: ids };
      if (comment) {
        payload.comment = comment;
      }
      var json = JSON.stringify(payload);
      if (!tg) {
        window.alert(json);
        return;
      }
      isSending = true;
      if (tg.MainButton && tg.MainButton.showProgress) {
        tg.MainButton.showProgress(false);
      }
      tg.sendData(json);
    }
    function submit() {
      if (current === "rollback") {
        submitRollback();
        return;
      }
      if (current !== "correction") {
        return;
      }
      if (confirmPayload) {
        if (isSending) {
          return;
        }
        var json = JSON.stringify(confirmPayload);
        if (!tg) {
          window.alert(json);
          return;
        }
        isSending = true;
        if (tg.MainButton && tg.MainButton.showProgress) {
          tg.MainButton.showProgress(false);
        }
        tg.sendData(json);
        return;
      }
      errorEl.textContent = "";
      var items = cart.slice();
      var result = collectCurrent();
      if (!result.ok) {
        return;
      }
      if (!result.empty) {
        items.push(result.item);
      }
      if (!items.length) {
        fail("Добавьте хотя бы одну позицию: размер и «Стало, шт».");
        return;
      }
      var comment = commentInput ? String(readFieldValue(commentInput) || "").trim() : "";
      var payload = {
        positions_kind: "correction",
        positions: items.map(function (item) { return item.position; }),
      };
      if (comment) {
        payload.comment = comment;
      }
      showConfirm(payload, buildSummary(items, comment));
    }
    if (tg && tg.MainButton) {
      tg.MainButton.onClick(submit);
    } else {
      fallback.onclick = submit;
    }

    showScreen("menu");
  }

  // --- Калькулятор (ТЗ п.6) ------------------------------------------
  // Обраний вигляд (показ 2026-09-07): поля, як у решті форми, плюс
  // випадний список розмірів, які вже були в таблиці складу - цілим
  // рядком «25×50×4000». Ручний ввід лишається під списком; правка поля
  // руками перемикає список на «свой размер», нічого не стираючи.
  var CALC_MODES = [
    { key: "volume", label: "Кубатура", input: "Количество, шт", unit: "м³" },
    { key: "pieces", label: "Штуки", input: "Объём, м³", unit: "шт" },
    { key: "linear", label: "Пог. м", input: "Количество, шт", unit: "пог. м" },
    { key: "area", label: "м²", input: "Количество, шт", unit: "м²" }
  ];
  var CALC_KIND_MARK = { linear: "пог. м", area: "м²" };

  function calcNumber(value) {
    if (!isFinite(value)) { return "—"; }
    var rounded = Math.round(value * 10000) / 10000;
    var text = String(rounded);
    if (text.indexOf("e") >= 0) { text = rounded.toFixed(4); }
    return text.replace(".", ",");
  }

  function calcParse(value) {
    var text = String(value == null ? "" : value).replace(",", ".").trim();
    if (!text) { return 0; }
    var number = parseFloat(text);
    return isFinite(number) ? number : 0;
  }

  function buildCalculator(sizes, standalone) {
    var overlay = document.createElement("div");
    overlay.className = standalone ? "calc-overlay calc-standalone" : "calc-overlay";
    overlay.hidden = true;

    var panel = document.createElement("div");
    panel.className = "calc-panel";
    overlay.appendChild(panel);

    var head = document.createElement("div");
    head.className = "calc-head";
    var headTitle = document.createElement("div");
    headTitle.className = "calc-title";
    headTitle.textContent = "Калькулятор";
    var closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "calc-close";
    closeButton.textContent = "✕";
    head.appendChild(headTitle);
    head.appendChild(closeButton);
    panel.appendChild(head);

    var body = document.createElement("div");
    body.className = "calc-body";
    panel.appendChild(body);

    // Режими
    var mode = CALC_MODES[0];
    var tabs = document.createElement("div");
    tabs.className = "calc-tabs";
    var tabEls = {};
    CALC_MODES.forEach(function (item) {
      var tab = document.createElement("button");
      tab.type = "button";
      tab.className = "calc-tab";
      tab.textContent = item.label;
      tab.addEventListener("click", function () {
        mode = item;
        Object.keys(tabEls).forEach(function (key) {
          tabEls[key].classList.toggle("on", key === item.key);
        });
        amountLabel.textContent = item.input;
        recalc();
      });
      tabEls[item.key] = tab;
      tabs.appendChild(tab);
    });
    tabEls[mode.key].classList.add("on");
    body.appendChild(tabs);

    // Список розмірів зі складу
    var pickWrap = document.createElement("div");
    pickWrap.className = "calc-field";
    var pickLabel = document.createElement("label");
    pickLabel.textContent = "Размер из таблицы";
    pickWrap.appendChild(pickLabel);
    // Рядок-селект: видно, що це вибір зі списку, а не поле для набору.
    var pickEmptyText = sizes.length ? "Выберите размер" : "В таблице пока нет размеров";
    var pickButton = document.createElement("div");
    pickButton.className = "calc-select";
    pickButton.tabIndex = 0;
    var pickText = document.createElement("span");
    pickText.className = "calc-select-text calc-select-empty";
    pickText.textContent = pickEmptyText;
    var pickCaret = document.createElement("span");
    pickCaret.className = "calc-select-caret";
    pickCaret.textContent = "▾";
    pickButton.appendChild(pickText);
    pickButton.appendChild(pickCaret);
    pickWrap.appendChild(pickButton);

    function setPick(value) {
        pickText.textContent = value || pickEmptyText;
        pickText.classList.toggle("calc-select-empty", !value);
    }

    function pickValue() {
        return pickText.classList.contains("calc-select-empty") ? "" : pickText.textContent;
    }
    var list = document.createElement("div");
    list.className = "calc-list";
    list.hidden = true;
    // Пошук лишається (розмірів десятки), але живе ВСЕРЕДИНІ списку - сам
    // селект від цього полем вводу не стає.
    var search = document.createElement("input");
    search.type = "text";
    search.className = "calc-search";
    search.placeholder = "Поиск";
    search.autocomplete = "off";
    list.appendChild(search);
    var listItems = document.createElement("div");
    listItems.className = "calc-list-items";
    list.appendChild(listItems);
    pickWrap.appendChild(list);
    body.appendChild(pickWrap);

    var separator = document.createElement("div");
    separator.className = "calc-sep";
    separator.textContent = "или вручную";
    body.appendChild(separator);

    // Ручний ввід
    var sizeRow = document.createElement("div");
    sizeRow.className = "calc-row3";
    function sizeField(caption) {
      var wrap = document.createElement("div");
      wrap.className = "calc-field";
      var label = document.createElement("label");
      label.textContent = caption;
      var input = document.createElement("input");
      input.type = "text";
      input.inputMode = "decimal";
      input.className = "calc-input";
      wrap.appendChild(label);
      wrap.appendChild(input);
      sizeRow.appendChild(wrap);
      return input;
    }
    var thicknessInput = sizeField("Толщина");
    var widthInput = sizeField("Ширина");
    var lengthInput = sizeField("Длина");
    body.appendChild(sizeRow);

    var amountWrap = document.createElement("div");
    amountWrap.className = "calc-field";
    var amountLabel = document.createElement("label");
    amountLabel.textContent = mode.input;
    var amountInput = document.createElement("input");
    amountInput.type = "text";
    amountInput.inputMode = "decimal";
    amountInput.className = "calc-input";
    amountWrap.appendChild(amountLabel);
    amountWrap.appendChild(amountInput);
    body.appendChild(amountWrap);

    var mainResult = document.createElement("div");
    mainResult.className = "calc-result";
    body.appendChild(mainResult);
    var extraResult = document.createElement("div");
    extraResult.className = "calc-result calc-result-extra";
    body.appendChild(extraResult);

    // Звичайна арифметика - окремим рядком унизу, щоб «25 умножить на 64»
    // не вимагало виходу з калькулятора.
    var plainWrap = document.createElement("div");
    plainWrap.className = "calc-field calc-plain";
    var plainLabel = document.createElement("label");
    plainLabel.textContent = "Обычный счёт";
    var plainInput = document.createElement("input");
    plainInput.type = "text";
    plainInput.className = "calc-input";
    plainInput.placeholder = "25 * 64";
    plainInput.autocomplete = "off";
    var plainOut = document.createElement("div");
    plainOut.className = "calc-plain-out";
    plainWrap.appendChild(plainLabel);
    plainWrap.appendChild(plainInput);
    plainWrap.appendChild(plainOut);
    body.appendChild(plainWrap);

    function renderList(filter) {
      listItems.innerHTML = "";
      var needle = String(filter || "").toLowerCase().replace(",", ".");
      var shown = 0;
      var lastProduct = null;
      sizes.forEach(function (size) {
        var hay = (size.product + " " + size.label).toLowerCase();
        if (needle && hay.indexOf(needle) < 0) { return; }
        if (shown >= 120) { return; }
        if (size.product !== lastProduct) {
          var group = document.createElement("div");
          group.className = "calc-group";
          group.textContent = size.product;
          listItems.appendChild(group);
          lastProduct = size.product;
        }
        var item = document.createElement("div");
        item.className = "calc-item";
        var text = document.createElement("span");
        text.textContent = size.label;
        item.appendChild(text);
        var mark = CALC_KIND_MARK[size.kind];
        if (mark) {
          var tail = document.createElement("span");
          tail.className = "calc-item-tail";
          tail.textContent = mark;
          item.appendChild(tail);
        }
        item.addEventListener("mousedown", function (event) { event.preventDefault(); });
        item.addEventListener("click", function () {
          setPick(size.label);
          thicknessInput.value = String(size.thickness).replace(".", ",");
          widthInput.value = String(size.width).replace(".", ",");
          lengthInput.value = String(size.length).replace(".", ",");
          closeList();
          if (size.kind === "linear") { tabEls.linear.click(); }
          else if (size.kind === "area") { tabEls.area.click(); }
          else { recalc(); }
          amountInput.focus();
        });
        listItems.appendChild(item);
        shown += 1;
      });
      if (!shown) {
        var empty = document.createElement("div");
        empty.className = "calc-group";
        empty.textContent = "Ничего не найдено";
        listItems.appendChild(empty);
      }
    }

    function openList() {
      if (!sizes.length) { return; }
      search.value = "";
      renderList("");
      list.hidden = false;
      pickCaret.textContent = "▴";
    }

    function closeList() {
      list.hidden = true;
      pickCaret.textContent = "▾";
    }

    function toggleList() {
      if (list.hidden) { openList(); } else { closeList(); }
    }

    pickButton.addEventListener("click", toggleList);
    pickButton.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleList();
      }
    });
    search.addEventListener("input", function () { renderList(search.value); });
    // Натиск поза списком згортає його - як і належить випадному списку.
    document.addEventListener("click", function (event) {
      if (list.hidden) { return; }
      if (pickWrap.contains(event.target)) { return; }
      closeList();
    });

    function markManual() {
      // Правка руками не стирає нічого - лише знімає позначку «зі списку».
      if (pickValue() && pickValue() !== "свой размер") {
        setPick("свой размер");
      }
    }

    function recalc() {
      var thickness = calcParse(thicknessInput.value);
      var width = calcParse(widthInput.value);
      var length = calcParse(lengthInput.value);
      var amount = calcParse(amountInput.value);
      var onePieceVolume = thickness / 1000 * width / 1000 * length / 1000;
      var onePieceLinear = length / 1000;
      var onePieceArea = width / 1000 * length / 1000;
      if (onePieceVolume <= 0) {
        mainResult.textContent = "Укажите размер";
        mainResult.className = "calc-result calc-result-empty";
        extraResult.hidden = true;
        return;
      }
      mainResult.className = "calc-result";
      extraResult.hidden = false;
      var head = "";
      var big = "";
      var extra = "";
      if (mode.key === "pieces") {
        head = "1 шт — " + calcNumber(onePieceVolume) + " м³";
        if (amount <= 0) {
          big = "— шт";
        } else {
          var exact = amount / onePieceVolume;
          var rounded = Math.round(exact);
          if (Math.abs(exact - rounded) < 0.0001) {
            big = calcNumber(rounded) + " шт";
          } else {
            big = calcNumber(exact) + " шт";
            extra = "целыми: " + Math.floor(exact) + " шт = " + calcNumber(Math.floor(exact) * onePieceVolume)
              + " м³ · " + (Math.floor(exact) + 1) + " шт = " + calcNumber((Math.floor(exact) + 1) * onePieceVolume) + " м³";
          }
        }
      } else if (mode.key === "linear") {
        head = "1 шт — " + calcNumber(onePieceLinear) + " пог. м";
        big = calcNumber(onePieceLinear * amount) + " пог. м";
      } else if (mode.key === "area") {
        head = "1 шт — " + calcNumber(onePieceArea) + " м²";
        big = calcNumber(onePieceArea * amount) + " м²";
      } else {
        // Зауваження користувача (2026-09-07): «в кожній вкладці свій
        // розрахунок» - вкладка кубатури показує куби, і тільки їх.
        head = "1 шт — " + calcNumber(onePieceVolume) + " м³";
        big = calcNumber(onePieceVolume * amount) + " м³";
      }
      mainResult.innerHTML = "";
      var headEl = document.createElement("div");
      headEl.className = "calc-result-head";
      headEl.textContent = head;
      var bigEl = document.createElement("div");
      bigEl.className = "calc-result-big";
      bigEl.textContent = big;
      mainResult.appendChild(headEl);
      mainResult.appendChild(bigEl);
      extraResult.textContent = extra;
      extraResult.hidden = !extra;
    }

    [thicknessInput, widthInput, lengthInput].forEach(function (input) {
      input.addEventListener("input", function () { markManual(); recalc(); });
    });
    amountInput.addEventListener("input", recalc);

    plainInput.addEventListener("input", function () {
      var raw = plainInput.value.replace(/,/g, ".");
      if (!raw.trim()) { plainOut.textContent = ""; return; }
      if (!/^[0-9+\-*/(). %]+$/.test(raw)) {
        plainOut.textContent = "Только числа и + − × ÷";
        return;
      }
      try {
        /* eslint-disable no-new-func */
        var value = Function('"use strict";return (' + raw + ")")();
        plainOut.textContent = (typeof value === "number" && isFinite(value)) ? "= " + calcNumber(value) : "—";
      } catch (err) {
        plainOut.textContent = "—";
      }
    });

    function close() {
      // Окремим екраном калькулятор закривається разом із самою формою:
      // під ним нічого немає.
      if (standalone) {
        if (tg && tg.close) { tg.close(); }
        return;
      }
      overlay.hidden = true;
    }
    closeButton.addEventListener("click", close);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay && !standalone) { close(); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !overlay.hidden) { close(); }
    });

    document.body.appendChild(overlay);
    recalc();
    return {
      open: function () {
        // Список розмірів при відкритті ЗАКРИТИЙ (зауваження користувача
        // 2026-09-07). Раніше панель ставила курсор у поле пошуку, а фокус
        // розкриває список - через це він вискакував сам.
        overlay.hidden = false;
        closeList();
      }
    };
  }

  function attachCalculator(ctx) {
    // Єдиний вхід у калькулятор - кнопка «КАЛЬКУЛЯТОР (форма)» в меню бота
    // (вимога користувача 2026-09-07: «калькулятор ОДИН лише має вхід,
    // через кнопку і все»). У формах операцій калькулятора немає взагалі.
    if (ctx.mode !== "calculator") { return null; }
    var sizes = ctx.calculator_sizes;
    if (!Array.isArray(sizes)) { return null; }
    return buildCalculator(sizes, true);
  }

  function main() {
    var token = new URLSearchParams(window.location.search).get("t");
    window.__formToken = token;
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
    applyJournalColors(ctx);
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
    // Кнопка калькулятора - у шапці форми (вимога користувача 2026-09-05:
    // «для форми - має бути кнопка обов'язково, а для чату - ні»).
    var calculator = attachCalculator(ctx);
    if (ctx.mode === "calculator") {
      // Кнопка бота «КАЛЬКУЛЯТОР (форма)»: полів операції немає взагалі,
      // панель відкривається одразу й на весь екран.
      var operationForm = document.getElementById("form");
      if (operationForm) { operationForm.style.display = "none"; }
      var knownEl = document.getElementById("known");
      if (knownEl) { knownEl.style.display = "none"; }
      if (tg && tg.MainButton) { tg.MainButton.hide(); }
      if (calculator) { calculator.open(); }
      return;
    }
    // Задача користувача: текст заголовка "Проверьте данные" — редагований
    // з Налаштувань (webapp_confirm_heading_text) — той самий елемент
    // існує в DOM незалежно від mode (all_in_one чи однокатегорійна форма),
    // тож досить встановити його тут один раз.
    var confirmHeadingEl = document.querySelector("#confirm-view .title");
    if (confirmHeadingEl) {
      confirmHeadingEl.textContent = ctx.confirm_heading_text || "Проверьте данные";
    }

    if (ctx.mode === "admin") {
      adminForm(ctx);
      return;
    }
    if (ctx.mode === "all_in_one") {
      // Обмін - окрема форма з двома блоками (exchangeAllInOne), решта
      // операцій - одна мега-форма з одним кошиком.
      if (ctx.kind === "exchange") {
        exchangeAllInOne(ctx);
      } else {
        mainAllInOne(ctx);
      }
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
      wireDimensionCascade(rowInputs, ctx.dimension_combos, singleInputs.breed, { noStock: ctx.kind === "sale" || ctx.kind === "writeoff" });
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
