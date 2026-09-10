(function () {
  var tg = window.Telegram ? window.Telegram.WebApp : null;
  // Задача користувача (2026-08-14): спроба ховати "Открыть в браузере"
  // лише поза Telegram (за tg.initData) НЕ спрацювала на реальному пристрої
  // користувача - initData на практиці лишався порожнім навіть усередині
  // справжнього Telegram (скріншот: кнопка зникла, лишилось саме
  // посилання - зворотне до бажаного). Без надійного способу перевірити
  // цю різницю наживо на реальному клієнті - повертаємось до простої,
  // завжди однакової поведінки: кнопка завжди показана, посилання завжди
  // сховане (як було до цього заходу).

  // Реальний ризик (аудит коду, 2026-08-14): жоден запит цієї сторінки
  // (перше завантаження, кнопка "Обновить", збереження порогу низького
  // остатка) не мав жодного тайм-ауту - при зависанні тунелю/сервера
  // сторінка лишалась би "вантажиться" назавжди, без жодної помилки чи
  // індикатора. postTemplateAction - ЄДИНА точка, через яку йдуть усі ці
  // запити, тож один AbortController тут закриває це одразу для всіх.
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
          throw new Error((data && data.error) || "Не удалось загрузить данные.");
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

  function applyPageBackground(style) {
    if (style && style.page_bg_color) {
      document.documentElement.style.setProperty("--custom-page-bg", style.page_bg_color);
    }
  }

  var state = {
    rows: [],
    categories: [],
    activeCategories: null,
    sortKey: null,
    sortDir: 1,
    sizeFilter: { thickness: null, width: null, length: null },
    valueFilter: { breed: null, condition: null, product: null, unit: null },
    movementRows: [],
    movementPeriod: { key: "month", from: null, to: null },
    movementCategory: null,
    movementDate: null,
    tabs: [],
    tabLabels: {},
    activeTab: "stock",
    salesRows: [],
    antisepticRows: [],
    lowStockRows: [],
    lowStockThreshold: null,
    canEditLowStockThreshold: false,
    contextToken: null,
    lowStockHideZero: false,
    salesPeriod: { key: "all", from: null, to: null },
    antisepticPeriod: { key: "all", from: null, to: null },
    clientsPeriod: { key: "all", from: null, to: null },
    clientsFilter: null,
    clientsSortKey: null,
    clientsSortDir: 1,
    salesClientFilter: null,
    salesSortKey: null,
    salesSortDir: 1,
    // Задача користувача (2026-08-14, скріншот "Позиция"): "не має бути
    // такого злиття - і тип, і ширина, і товщина, все в одному" - замість
    // одного "Позиция" (generic value-modal по всьому рядку) тепер окремі
    // Продукт/Порода/Тип (той самий generic value-modal, що й Клиент) +
    // Размер (справжній size-modal, той самий принцип, що вже мають
    // Списание/Приход/Низкий остаток нижче).
    salesProductFilter: null,
    salesBreedFilter: null,
    salesConditionFilter: null,
    salesSizeFilter: { thickness: null, width: null, length: null },
    // Задача користувача (скріншот "Автор"): "додай скрізь фільтр
    // вибірковий" - той самий generic value-modal, що й Продукт/Порода/Тип,
    // тепер і для "Автор" в усіх 4 вкладках, де ця колонка є.
    salesAuthorFilter: null,
    antisepticClientFilter: null,
    antisepticAuthorFilter: null,
    antisepticSortKey: null,
    antisepticSortDir: 1,
    writeoffRows: [],
    writeoffPeriod: { key: "all", from: null, to: null },
    writeoffProductFilter: null,
    writeoffSortKey: null,
    writeoffSortDir: 1,
    // Задача користувача: "Списание" - Порода/Причина того самого типу, що
    // вже є в інших вкладках (value-modal), Размер - той самий модал
    // розміру, що вже має СКЛАД (thickness/width/length тепер приходять і
    // для writeoff_rows, warehouse_data.py).
    writeoffBreedFilter: null,
    // Задача користувача (2026-08-14): той самий split, що й Продажи -
    // "Тип" (AD/KD) тепер окрема колонка/фільтр, а не злитий з "Продукт".
    writeoffConditionFilter: null,
    writeoffSizeFilter: { thickness: null, width: null, length: null },
    writeoffReasonFilter: null,
    writeoffAuthorFilter: null,
    // Задача користувача: "Низкий остаток" не мав УЗАГАЛІ жодного фільтра/
    // сортування - Продукт/Порода/Тип/Размер той самий принцип, що й СКЛАД,
    // Остаток сортується як "Штук" деінде (клік по заголовку).
    lowStockProductFilter: null,
    lowStockBreedFilter: null,
    lowStockConditionFilter: null,
    lowStockSizeFilter: { thickness: null, width: null, length: null },
    lowStockSortKey: null,
    lowStockSortDir: 1,
    // Задача користувача (2026-08-14): "Приход" - нова вкладка, той самий
    // принцип фільтрів/сортування, що вже має "Списание" (найближчий
    // структурно сусід - дата + розмір + кількість), лише без Причини.
    incomeRows: [],
    incomePeriod: { key: "all", from: null, to: null },
    incomeSizeFilter: { thickness: null, width: null, length: null },
    incomeSortKey: null,
    incomeSortDir: 1,
    incomeAuthorFilter: null,
    // Реальний ризик (аудит коду, 2026-08-14): "Приход" мала колонки
    // Продукт/Порода/Тип НЕІНТЕРАКТИВНИМИ - той самий фільтр, що вже має
    // "Списание" (найближчий структурний сусід) для тих самих полів.
    incomeProductFilter: null,
    incomeBreedFilter: null,
    incomeConditionFilter: null,
  };

  // Задача користувача (2026-08-14): "прибери запам'ятовування. криво
  // працює" - фільтри/сортування/вкладка більше НЕ зберігаються між
  // відкриттями "Данные": кожне відкриття завжди починається з чистого
  // стану (як і до 2026-08-13). Раніше тут жив auto-save/restore механізм
  // (serializePrefs/applyPrefs/savePrefsNow/flushPrefsOnTeardown) - прибраний
  // повністю разом із серверною стороною (webapp_server.py, warehouse_data.py).
  var pageLoadedOk = false;

  function numberValue(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var parsed = parseFloat(String(value).replace(",", "."));
    return isNaN(parsed) ? null : parsed;
  }

  // Реальний баг (аудит коду, 2026-08-14): ця функція форматує КОЖНЕ
  // нецiле число на КОЖНІЙ вкладці цієї сторінки (Штук/М3/М2/МП/MDL і
  // розмір на Остатках) - але лишалась зі своєю власною крапкою й
  // округленням до 2 знаків, тоді як увесь інший текст на цій самій
  // сторінці (для тих самих полів, з інших вкладок) приходить готовим із
  // сервера через _display_bot_number (utils.py) - кома, округлення до 4
  // знаків. Той самий фізичний розмір виглядав по-різному залежно від
  // того, на якій вкладці на нього дивитись (напр. "18.33x120x3000" на
  // Остатках проти "18,3333x120x3000" деінде). formatServerNumber
  // (webapp/app.js) вже вирішує це саме так - той самий прийом тут.
  function formatNumber(value) {
    var num = numberValue(value);
    if (num === null) {
      return "";
    }
    if (Number.isInteger(num)) {
      return String(num);
    }
    var rounded = Math.round(num * 10000) / 10000;
    return String(rounded).replace(".", ",");
  }

  // Реальна знахідка (аудит коду, 2026-08-16): усі рядки таблиць нижче
  // раніше будувались через tr.innerHTML = "<td>" + значення + "</td>" -
  // жодного екранування. "Клиент"/"Порода" - вільний текст, який людина
  // вводить сама (app.js:1326-1327 - "введені текстом... ніколи не як
  // HTML"); значення типу "<img src=x onerror=...>" виконалось би як код
  // у браузері КОЖНОГО, хто відкрив "Дані". Той самий безпечний прийом,
  // що вже й сам tr (document.createElement) - textContent замість
  // конкатенації HTML-рядків, без потреби в окремій функції екранування.
  function appendRowCells(tr, values) {
    values.forEach(function (text) {
      var td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
  }

  function visibleRows() {
    return state.rows.filter(function (row) {
      // Задача користувача: чипи Продукту тепер ВИМКНЕНІ по замовчуванню -
      // порожній вибір означає "показати все" (а не "нічого не показувати",
      // як було б при звичайному inclusive-фільтрі). Фільтрація застосовується
      // лише коли користувач РЕАЛЬНО щось увімкнув.
      if (state.activeCategories.size > 0 && !state.activeCategories.has(row.product)) {
        return false;
      }
      if (!matchesSizeFilter(row, state.sizeFilter)) {
        return false;
      }
      if (state.valueFilter.breed !== null && !state.valueFilter.breed.has(row.breed || "")) {
        return false;
      }
      if (state.valueFilter.condition !== null && !state.valueFilter.condition.has(row.condition || "")) {
        return false;
      }
      if (state.valueFilter.product !== null && !state.valueFilter.product.has(row.product || "")) {
        return false;
      }
      if (state.valueFilter.unit !== null && !state.valueFilter.unit.has(row.unit || "")) {
        return false;
      }
      return true;
    });
  }

  // Рядки складу після всіх фільтрів, КРІМ одного (except): з них беруться
  // значення для випадного списку цього фільтра - «якщо застосований інший
  // фільтр в іншому стовпці, це враховується» (рішення користувача 2026-09-06).
  function visibleRowsExcept(except) {
    return state.rows.filter(function (row) {
      if (state.activeCategories.size > 0 && !state.activeCategories.has(row.product)) {
        return false;
      }
      if (except !== "size" && !matchesSizeFilter(row, state.sizeFilter)) {
        return false;
      }
      var fields = ["breed", "condition", "product", "unit"];
      for (var i = 0; i < fields.length; i++) {
        var field = fields[i];
        if (field !== except && state.valueFilter[field] !== null && !state.valueFilter[field].has(row[field] || "")) {
          return false;
        }
      }
      return true;
    });
  }

  function rowSizeText(row) {
    return [row.thickness, row.width, row.length]
      .filter(function (v) { return v !== null && v !== undefined && v !== ""; })
      .map(formatNumber)
      .join("x");
  }

  // Задача користувача: "фільтри мають бути всі схожими за їх типами
  // інформації" - той самий трипольний збіг товщина/ширина/довжина, що вже
  // inline перевіряє visibleRows() вище (лише для СКЛАД), тут окремим
  // хелпером для "Списание"/"Низкий остаток".
  function matchesSizeFilter(row, sizeFilterState) {
    // Розмір цілими «TxWxL» (кілька одразу) - рішення користувача 2026-09-06.
    if (sizeFilterState.sizes && sizeFilterState.sizes.size > 0) {
      return sizeFilterState.sizes.has(rowSizeText(row));
    }
    if (sizeFilterState.thickness !== null && numberValue(row.thickness) !== sizeFilterState.thickness) {
      return false;
    }
    if (sizeFilterState.width !== null && numberValue(row.width) !== sizeFilterState.width) {
      return false;
    }
    if (sizeFilterState.length !== null && numberValue(row.length) !== sizeFilterState.length) {
      return false;
    }
    return true;
  }

  function renderChips() {
    var container = document.getElementById("chips");
    container.innerHTML = "";
    state.categories.forEach(function (category) {
      var chip = document.createElement("span");
      chip.className = "data-chip" + (state.activeCategories.has(category) ? " active" : "");
      chip.textContent = category;
      chip.addEventListener("click", function () {
        if (state.activeCategories.has(category)) {
          state.activeCategories.delete(category);
        } else {
          state.activeCategories.add(category);
        }
        renderChips();
        renderStockPanel();
      });
      container.appendChild(chip);
    });
  }

  function hasActiveSizeFilter(sizeFilterState) {
    if (sizeFilterState.sizes && sizeFilterState.sizes.size > 0) {
      return true;
    }
    return sizeFilterState.thickness !== null || sizeFilterState.width !== null || sizeFilterState.length !== null;
  }

  function activeSizeFilterText(sizeFilterState) {
    if (sizeFilterState.sizes && sizeFilterState.sizes.size > 0) {
      return Array.from(sizeFilterState.sizes).map(function (s) { return s.replace(/x/g, "×"); }).join(", ");
    }
    var parts = [];
    if (sizeFilterState.thickness !== null) parts.push("Толщина " + formatNumber(sizeFilterState.thickness));
    if (sizeFilterState.width !== null) parts.push("Ширина " + formatNumber(sizeFilterState.width));
    if (sizeFilterState.length !== null) parts.push("Длина " + formatNumber(sizeFilterState.length));
    return parts.join(", ");
  }

  // Задача користувача: фільтр розміру реально звужував список до нуля, і
  // ЖОДНОГО видимого слідy цього не лишалось - "Ничего не найдено" виглядало
  // як зламані чипи категорій, хоча вони й так усі активні. Видимий бейдж +
  // кнопка скидання - єдиний спосіб зрозуміти й виправити причину.
  //
  // Задача користувача: "фільтри мають бути всі схожими за їх типами
  // інформації" - параметризована (badgeId/sizeFilterState/onClear), той
  // самий принцип, що вже має openSizeModal вище, щоб "Списание" й "Низкий
  // остаток" могли показувати свій власний бейдж розміру.
  function renderSizeBadge(badgeId, sizeFilterState, onClear) {
    var badge = document.getElementById(badgeId);
    if (!hasActiveSizeFilter(sizeFilterState)) {
      badge.style.display = "none";
      badge.innerHTML = "";
      return;
    }
    badge.style.display = "flex";
    badge.textContent = "";
    var label = document.createElement("span");
    label.textContent = "Размер: " + activeSizeFilterText(sizeFilterState);
    badge.appendChild(label);
    var clear = document.createElement("span");
    clear.className = "badge-clear";
    clear.textContent = "×";
    clear.addEventListener("click", function () {
      sizeFilterState.thickness = null;
      sizeFilterState.width = null;
      sizeFilterState.length = null;
      sizeFilterState.sizes = null;
      onClear();
    });
    badge.appendChild(clear);
  }

  var VALUE_FIELDS = {
    product: { label: "Продукт" },
    breed: { label: "Порода" },
    condition: { label: "Тип" },
    unit: { label: "Ед. измерения" },
  };

  function hasActiveValueFilter() {
    return Object.keys(VALUE_FIELDS).some(function (field) { return state.valueFilter[field] !== null; });
  }

  function activeValueFilterText() {
    var parts = [];
    Object.keys(VALUE_FIELDS).forEach(function (field) {
      var selected = state.valueFilter[field];
      if (selected !== null) {
        var labels = Array.from(selected).map(function (value) { return value || VALUE_BLANK_LABEL; });
        parts.push(VALUE_FIELDS[field].label + ": " + labels.join(", "));
      }
    });
    return parts.join("; ");
  }

  // Той самий бейдж-принцип, що й у розміру вище - фільтр породи/типу теж
  // здатен звузити список до нуля без пояснення, чому.
  function renderValueBadge() {
    var badge = document.getElementById("value-filter-badge");
    if (!hasActiveValueFilter()) {
      badge.style.display = "none";
      badge.innerHTML = "";
      return;
    }
    badge.style.display = "flex";
    badge.textContent = "";
    var label = document.createElement("span");
    label.textContent = activeValueFilterText();
    badge.appendChild(label);
    var clear = document.createElement("span");
    clear.className = "badge-clear";
    clear.textContent = "×";
    clear.addEventListener("click", function () {
      state.valueFilter = { breed: null, condition: null, product: null, unit: null };
      renderStockPanel();
    });
    badge.appendChild(clear);
  }

  var VALUE_BLANK_LABEL = "(не указано)";

  var UNIT_LABELS = [
    { value: "м3", label: "М3" },
    { value: "м2", label: "М2" },
    { value: "мп", label: "МП" },
  ];
  var DEFAULT_MEASURE_HEADER = "М3/М2/МП";

  // Задача користувача: заголовок колонки має показувати рівно ті одиниці,
  // що зараз обрані у фільтрі - не статичний "М3/М2/МП" завжди.
  function measureHeaderText() {
    var filter = state.valueFilter.unit;
    if (filter === null) {
      return DEFAULT_MEASURE_HEADER;
    }
    var labels = UNIT_LABELS.filter(function (unit) { return filter.has(unit.value); })
      .map(function (unit) { return unit.label; });
    return labels.length ? labels.join("/") : DEFAULT_MEASURE_HEADER;
  }

  // Порожнє значення теж має лишатись фільтрованим окремим пунктом
  // (напр. рядок без заповненого "Тип") - інакше вибір лише частини
  // значень тихо ховав би такі рядки без жодного чекбоксу, що на це вказує.
  // Реальний баг (аудит "фільтри перестали працювати" на СКЛАД): раніше ця
  // функція звалась так само, як і generic distinctFieldValues(rows, field)
  // нижче (додана пізніше для "Списание") - ОБИДВІ function-декларації в
  // ОДНІЙ області видимості, тож друга (2 аргументи) тихо перекривала цю
  // (1 аргумент) У ВСЬОМУ файлі. openValueModal нижче й далі кликав як
  // distinctFieldValues(field) - фактично викликаючи 2-аргументну версію з
  // рядком замість масиву, де .forEach одразу кидав TypeError. Перейменована
  // на distinctStockFieldValues, щоб колізії більше не було - ОБИДВІ
  // поведінки (ця з hasBlank, generic без) лишаються потрібні окремо.
  function distinctStockFieldValues(field, rows) {
    var seen = {};
    var values = [];
    var hasBlank = false;
    (rows || state.rows).forEach(function (row) {
      var value = row[field] || "";
      if (!value) {
        hasBlank = true;
        return;
      }
      if (!seen[value]) {
        seen[value] = true;
        values.push(value);
      }
    });
    values.sort();
    if (hasBlank) {
      values.push("");
    }
    return values;
  }

  // Задача користувача: "Тип" (AD/KD/N/A) має фільтруватись так само
  // вибірково, як і "Порода" - один загальний чекбокс-модал (клік по
  // заголовку колонки, той самий патерн, що й у "Размер") на обидва поля,
  // замість дублювання розмітки. openGenericValueModal - той самий модал,
  // ПАРАМЕТРИЗОВАНИЙ (не завʼязаний на state.rows/VALUE_FIELDS), тому
  // реюзається й для "Клиент" на вкладці "Клиенты" (інше джерело даних).
  // Рішення користувача (2026-09-06): жодних списків із галочками - закритий
  // випадний список «Выберите…», під ним «Добавить», нижче вибране рядками
  // з ✕. Поки список не відкрити, нічого зайвого не видно. Порожній вибір =
  // усі значення (null).
  function buildChoiceList(container, values, current, displayFor) {
    displayFor = displayFor || function (value) { return value === "" ? VALUE_BLANK_LABEL : String(value); };
    container.innerHTML = "";
    var chosen = current ? values.filter(function (value) { return current.has(value); }) : [];
    var select = document.createElement("select");
    select.className = "choice-select";
    var addButton = document.createElement("button");
    addButton.type = "button";
    addButton.className = "choice-add";
    addButton.textContent = "Добавить";
    var rows = document.createElement("div");
    rows.className = "choice-rows";
    var hint = document.createElement("p");
    hint.className = "choice-hint";
    hint.textContent = "Ничего не добавлено — показаны все.";

    function rebuild() {
      select.innerHTML = "";
      var placeholder = document.createElement("option");
      placeholder.value = "__none__";
      placeholder.textContent = "Выберите…";
      select.appendChild(placeholder);
      values.forEach(function (value, index) {
        if (chosen.indexOf(value) !== -1) {
          return;
        }
        var option = document.createElement("option");
        option.value = String(index);
        option.textContent = displayFor(value);
        select.appendChild(option);
      });
      rows.innerHTML = "";
      chosen.forEach(function (value) {
        var row = document.createElement("div");
        row.className = "choice-row";
        var text = document.createElement("span");
        text.textContent = displayFor(value);
        row.appendChild(text);
        var remove = document.createElement("span");
        remove.className = "choice-remove";
        remove.textContent = "✕";
        remove.addEventListener("click", function () {
          chosen = chosen.filter(function (v) { return v !== value; });
          rebuild();
        });
        row.appendChild(remove);
        rows.appendChild(row);
      });
      hint.style.display = chosen.length ? "none" : "";
    }

    addButton.addEventListener("click", function () {
      if (select.value === "__none__") {
        return;
      }
      var value = values[Number(select.value)];
      if (value === undefined || chosen.indexOf(value) !== -1) {
        return;
      }
      chosen.push(value);
      rebuild();
    });
    container.appendChild(select);
    container.appendChild(addButton);
    container.appendChild(rows);
    container.appendChild(hint);
    rebuild();
    return {
      result: function () {
        // Вибране у списку, але ще не додане кнопкою, теж рахується
        // (рішення користувача 2026-09-07).
        if (select.value !== "__none__") {
          var pending = values[Number(select.value)];
          if (pending !== undefined && chosen.indexOf(pending) === -1) {
            chosen.push(pending);
          }
        }
        return chosen.length ? new Set(chosen) : null;
      },
    };
  }

  function openGenericValueModal(title, values, current, onApply) {
    document.getElementById("value-modal-title").textContent = "Фильтр: " + title;
    var chooser = buildChoiceList(document.getElementById("value-modal-body"), values, current);
    document.getElementById("value-modal-apply").onclick = function () {
      document.getElementById("value-modal").style.display = "none";
      onApply(chooser.result());
    };
    document.getElementById("value-modal").style.display = "flex";
  }

  function openValueModal(field) {
    var config = VALUE_FIELDS[field];
    openGenericValueModal(config.label, distinctStockFieldValues(field, visibleRowsExcept(field)), state.valueFilter[field], function (result) {
      state.valueFilter[field] = result;
      renderStockPanel();
    });
  }

  function updateSortArrows() {
    document.querySelectorAll("th[data-sort]").forEach(function (th) {
      var arrow = th.querySelector(".sort-arrow");
      if (!arrow) {
        return;
      }
      if (th.getAttribute("data-sort") === state.sortKey) {
        arrow.textContent = state.sortDir === 1 ? "↑" : "↓";
      } else {
        arrow.textContent = "";
      }
    });
  }

  // Підсумок рахується з ТОГО САМОГО масиву, який щойно намалював
  // таблицю - він уже пройшов усі фільтри (чипи продукту, розмір,
  // одиниця). Окремої "фільтруючої" логіки тут навмисно немає: два
  // незалежні проходи по фільтрах рано чи пізно розійшлися б, і підсумок
  // почав би тихо брехати.
  //
  // Одиниці не змішуються: у колонці М3/М2/МП поруч живуть 0,645 м3 і
  // 2112 мп, тож кожна одиниця підсумовується окремо й показується своїм
  // рядком. Порядок сталий (м3, м2, мп), щоб число не стрибало з місця
  // на місце між перемальовками.

  function renderStockTotals(rows) {
    var footer = document.getElementById("stock-totals");
    if (!footer) {
      return;
    }
    var totalQuantity = 0;
    var byUnit = {};
    rows.forEach(function (row) {
      totalQuantity += numberValue(row.quantity) || 0;
      var measure = numberValue(row.measure) || 0;
      if (!measure) {
        return;
      }
      var unit = row.unit || "";
      byUnit[unit] = (byUnit[unit] || 0) + measure;
    });
    renderTableTotals("stock-totals", {
      "stock-total-quantity": formatNumber(totalQuantity) + " шт",
      "stock-total-measure": measureTotalLines(byUnit),
    });
  }

  function renderStockPanel() {
    renderSizeBadge("size-filter-badge", state.sizeFilter, renderStockPanel);
    renderValueBadge();
    document.getElementById("measure-filter-trigger").textContent = measureHeaderText();
    // Реальний ризик (аудит коду, 2026-08-14): "Остатки" мала власну копію
    // sortedRows(rows) - той самий алгоритм, що вже узагальнений у
    // sortedByKey(rows, key, dir, valueGetter) для решти 5 вкладок.
    var rows = sortedByKey(visibleRows(), state.sortKey, state.sortDir);
    var tbody = document.getElementById("stock-rows");
    var empty = document.getElementById("stock-empty");
    var emptyHint = document.getElementById("stock-empty-hint");
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      var emptyFooter = document.getElementById("stock-totals");
      if (emptyFooter) {
        emptyFooter.style.display = "none";
      }
      var reasons = [];
      // Реальний баг (аудит коду, 2026-08-14): обидві функції давно
      // перероблені під параметр sizeFilterState (щоб їх могли
      // перевикористовувати й інші вкладки через renderSizeBadge), але
      // саме цей виклик - для "чому порожньо" на Остатках - забули
      // оновити. Викликані без аргументу, вони кидали TypeError (читання
      // .thickness з undefined) якраз у момент, коли підказка мала
      // з'явитись і пояснити причину.
      if (hasActiveSizeFilter(state.sizeFilter)) {
        reasons.push("активен фильтр размера (" + activeSizeFilterText(state.sizeFilter) + ")");
      }
      if (hasActiveValueFilter()) {
        reasons.push("активен фильтр " + activeValueFilterText());
      }
      if (state.activeCategories.size > 0) {
        reasons.push("выбраны не все категории продукта (" + Array.from(state.activeCategories).join(", ") + ")");
      }
      if (reasons.length) {
        emptyHint.style.display = "block";
        emptyHint.textContent = "Причина: " + reasons.join(", ") + ".";
      } else {
        emptyHint.style.display = "none";
      }
      updateSortArrows();
      return;
    }
    empty.style.display = "none";
    emptyHint.style.display = "none";
    renderStockTotals(rows);
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      var size = [row.thickness, row.width, row.length]
        .filter(function (v) { return v !== null && v !== undefined && v !== ""; })
        .map(formatNumber)
        .join("x");
      var measureText = row.measure !== null && row.measure !== undefined && row.measure !== ""
        ? formatNumber(row.measure) + (row.unit ? " " + row.unit : "")
        : "";
      appendRowCells(tr, [
        index + 1,
        row.product || "",
        row.breed || "",
        row.condition || "",
        size,
        formatNumber(row.quantity),
        measureText,
      ]);
      tbody.appendChild(tr);
    });
    updateSortArrows();
  }

  var SIZE_FIELDS = [
    { key: "thickness", label: "Толщина" },
    { key: "width", label: "Ширина" },
    { key: "length", label: "Длина" },
  ];

  // Задача користувача: 18/120/3000 кожне окремо реально є на складі, але
  // РАЗОМ такого рядка нема - "Ничего не найдено" без пояснення. Товщина/
  // ширина/довжина мають звірятись одна з одною (той самий принцип, що й
  // каскад розмірів у формі webapp/app.js) - варіанти в кожному "Выбрать"
  // звужуються до того, що РЕАЛЬНО співіснує з уже обраними двома іншими
  // полями серед рядків джерела (rows - увесь набір цієї вкладки, незалежно
  // від чипів категорій чи інших фільтрів, як і раніше).
  //
  // Задача користувача: "фільтри мають бути всі схожими за їх типами
  // інформації" - раніше hardcoded на state.rows/state.sizeFilter/
  // renderStockPanel (лише СКЛАД); тепер приймає rows/sizeFilterState/
  // onApply параметрами, той самий принцип, що вже має openGenericValueModal,
  // щоб "Списание" й "Низкий остаток" могли переиспользувати той самий
  // модал розміру.
  function distinctValuesFor(rows, fieldKey, otherValues) {
    var seen = {};
    var values = [];
    rows.forEach(function (row) {
      for (var key in otherValues) {
        if (otherValues[key] !== null && numberValue(row[key]) !== otherValues[key]) {
          return;
        }
      }
      var num = numberValue(row[fieldKey]);
      if (num !== null && !seen[num]) {
        seen[num] = true;
        values.push(num);
      }
    });
    return values.sort(function (a, b) { return a - b; });
  }

  function distinctSizes(rows) {
    var seen = {};
    var items = [];
    rows.forEach(function (row) {
      var text = rowSizeText(row);
      if (!text || seen[text]) {
        return;
      }
      seen[text] = true;
      items.push({ text: text, t: numberValue(row.thickness) || 0, w: numberValue(row.width) || 0, l: numberValue(row.length) || 0 });
    });
    items.sort(function (a, b) { return (a.t - b.t) || (a.w - b.w) || (a.l - b.l); });
    return items.map(function (item) { return item.text; });
  }

  // Рішення користувача (2026-09-06): замість трьох полів Толщина/Ширина/
  // Длина - випадний список цілих розмірів «TxWxL» з наявних рядків
  // (після інших фільтрів), «Добавить», вибрані рядками з ✕ - можна кілька.
  function openSizeModal(rows, sizeFilterState, onApply) {
    var chooser = buildChoiceList(document.getElementById("size-modal-body"), distinctSizes(rows), sizeFilterState.sizes || null,
      function (value) { return String(value).replace(/x/g, "×"); });
    var applyButton = document.getElementById("size-modal-apply");
    applyButton.disabled = false;
    applyButton.onclick = function () {
      sizeFilterState.sizes = chooser.result();
      sizeFilterState.thickness = null;
      sizeFilterState.width = null;
      sizeFilterState.length = null;
      document.getElementById("size-modal").style.display = "none";
      onApply();
    };
    document.getElementById("size-modal").style.display = "flex";
  }

  // ==== Вкладки Продажи/Антисептирование/Клиенты/Низкий остаток ====
  // Задача користувача: "додай збоку назви вкладок... має показувати рівно
  // те що і в чат-боті" - назви (state.tabs) приходять з бекенду буквально
  // тими самими, що й custom_menu_buttons під "ДАННЫЕ" (переживають
  // перейменування адміністратором). Дані кожної вкладки - ТІ САМІ функції,
  // що вже формують повідомлення бота (_sales_report_rows/
  // _antiseptic_report_rows/low_stock_warehouse_items), лише період тут
  // фільтрується на клієнті (весь набір рядків завантажується одразу,
  // "весь период"), щоб клік по "Неделя"/"Месяц" не робив зайвий round-trip.

  // Дата у вигляді «дд.мм.гггг» - той самий формат, у якому приходять рядки.
  function formatRuDate(value) {
    if (!value) {
      return "";
    }
    var d = value instanceof Date ? value : parseRuDate(value);
    if (!d) {
      return String(value);
    }
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear();
  }

  function parseRuDate(text) {
    var match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text || "");
    if (!match) {
      return null;
    }
    return new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]));
  }

  function parseDateInputValue(value) {
    var parts = String(value || "").split("-");
    if (parts.length !== 3) {
      return null;
    }
    return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  }

  function startOfDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function addDays(d, n) {
    var x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
  }

  // Той самий набір фраз, що й _sales_period_keyboard (telegram_dialog_
  // reports.py) - "Неделя"/"Месяц" тут рухоме вікно (останні 7/30 днів),
  // "Прошлая неделя"/"Прошлый месяц" - календарні межі; та сама відмінність,
  // що вже діє в самому боті.
  var PERIOD_OPTIONS = [
    { key: "today", label: "Сегодня" },
    { key: "yesterday", label: "Вчера" },
    { key: "week", label: "Неделя" },
    { key: "month", label: "Месяц" },
    { key: "last_week", label: "Прошлая неделя" },
    { key: "last_month", label: "Прошлый месяц" },
    { key: "all", label: "Весь период" },
    { key: "custom", label: "Свой период" },
  ];

  function periodRange(periodState) {
    var today = startOfDay(new Date());
    var key = periodState.key;
    if (key === "today") {
      return { from: today, to: today };
    }
    if (key === "yesterday") {
      var yesterday = addDays(today, -1);
      return { from: yesterday, to: yesterday };
    }
    if (key === "week") {
      return { from: addDays(today, -6), to: today };
    }
    if (key === "month") {
      return { from: addDays(today, -29), to: today };
    }
    if (key === "last_week") {
      var weekday = (today.getDay() + 6) % 7;
      var thisMonday = addDays(today, -weekday);
      var start = addDays(thisMonday, -7);
      return { from: start, to: addDays(start, 6) };
    }
    if (key === "last_month") {
      var firstThisMonth = new Date(today.getFullYear(), today.getMonth(), 1);
      var end = addDays(firstThisMonth, -1);
      return { from: new Date(end.getFullYear(), end.getMonth(), 1), to: end };
    }
    if (key === "custom") {
      return { from: periodState.from || null, to: periodState.to || null };
    }
    return { from: null, to: null };
  }

  function rowInPeriod(row, periodState) {
    var range = periodRange(periodState);
    if (!range.from && !range.to) {
      return true;
    }
    var rowDate = parseRuDate(row.date);
    if (!rowDate) {
      return false;
    }
    if (range.from && rowDate < range.from) {
      return false;
    }
    if (range.to && rowDate > range.to) {
      return false;
    }
    return true;
  }

  function openRangeModal(onApply) {
    var fromInput = document.getElementById("range-modal-from");
    var toInput = document.getElementById("range-modal-to");
    fromInput.value = "";
    toInput.value = "";
    document.getElementById("range-modal-apply").onclick = function () {
      onApply(parseDateInputValue(fromInput.value), parseDateInputValue(toInput.value));
      document.getElementById("range-modal").style.display = "none";
    };
    document.getElementById("range-modal").style.display = "flex";
  }

  function renderPeriodChips(containerId, periodState, onChange) {
    var container = document.getElementById(containerId);
    container.innerHTML = "";
    PERIOD_OPTIONS.forEach(function (option) {
      var chip = document.createElement("span");
      chip.className = "data-chip" + (periodState.key === option.key ? " active" : "");
      chip.textContent = option.label;
      chip.addEventListener("click", function () {
        if (option.key === "custom") {
          openRangeModal(function (from, to) {
            periodState.key = "custom";
            periodState.from = from;
            periodState.to = to;
            renderPeriodChips(containerId, periodState, onChange);
            onChange();
          });
          return;
        }
        periodState.key = option.key;
        periodState.from = null;
        periodState.to = null;
        renderPeriodChips(containerId, periodState, onChange);
        onChange();
      });
      container.appendChild(chip);
    });
  }

  function measureCellText(row) {
    var parts = [];
    if (row.volume) {
      parts.push(formatNumber(row.volume) + " м3");
    }
    if (row.area) {
      parts.push(formatNumber(row.area) + " м2");
    }
    if (row.linear) {
      parts.push(formatNumber(row.linear) + " мп");
    }
    return parts.join(", ");
  }

  // Сортування "Объём" на "Продажи" - один рядок несе лише ОДИН з трьох
  // вимірів (м3/м2/мп, залежно від товару), тому для сортування береться
  // те з них, що реально заповнене в цьому рядку.
  function primaryMeasureValue(row) {
    if (row.volume !== null && row.volume !== undefined) {
      return numberValue(row.volume);
    }
    if (row.area !== null && row.area !== undefined) {
      return numberValue(row.area);
    }
    if (row.linear !== null && row.linear !== undefined) {
      return numberValue(row.linear);
    }
    return null;
  }

  function sortedByKey(rows, key, dir, valueGetter) {
    if (!key) {
      return rows;
    }
    return rows.slice().sort(function (a, b) {
      var av = valueGetter ? valueGetter(a) : numberValue(a[key]);
      var bv = valueGetter ? valueGetter(b) : numberValue(b[key]);
      av = av === null ? -Infinity : av;
      bv = bv === null ? -Infinity : bv;
      return dir * (av - bv);
    });
  }

  // Один вигляд підсумку на ВСІХ вкладках (вимога користувача
  // 2026-08-21: "потрібно щоб однаково все виглядало у всіх вкладках").
  // Значення розкладаються по клітинках, тобто кожне число стоїть під
  // своєю колонкою, а сам рядок прилипає до низу області прокрутки.
  function renderTableTotals(footId, valuesByCellId) {
    var foot = document.getElementById(footId);
    if (!foot) {
      return;
    }
    Object.keys(valuesByCellId).forEach(function (cellId) {
      var cell = document.getElementById(cellId);
      if (!cell) {
        return;
      }
      cell.textContent = "";
      var value = valuesByCellId[cellId];
      var lines = Array.isArray(value) ? value : [value];
      lines.forEach(function (line) {
        if (line === null || line === undefined || line === "") {
          return;
        }
        var div = document.createElement("div");
        div.textContent = line;
        cell.appendChild(div);
      });
    });
    foot.style.display = "";
  }

  function hideTableTotals(footId) {
    var foot = document.getElementById(footId);
    if (foot) {
      foot.style.display = "none";
    }
  }

  // Одиниці не змішуються НІДЕ: в одній колонці поруч живуть 0,645 м3 і
  // 2112 мп, тож кожна одиниця підсумовується окремо й показується своїм
  // рядком. Порядок сталий, щоб число не стрибало між перемальовками.
  var TOTAL_UNIT_ORDER = ["м3", "м2", "мп"];

  function measureTotalLines(byUnit) {
    var units = TOTAL_UNIT_ORDER.filter(function (unit) { return byUnit[unit]; });
    Object.keys(byUnit).forEach(function (unit) {
      if (byUnit[unit] && units.indexOf(unit) === -1) {
        units.push(unit);
      }
    });
    return units.map(function (unit) {
      return formatNumber(byUnit[unit]) + (unit ? " " + unit : "");
    });
  }

  function renderSalesPanel() {
    var rows = state.salesRows.filter(function (row) {
      return rowInPeriod(row, state.salesPeriod)
        && (state.salesClientFilter === null || state.salesClientFilter.has(row.client || ""))
        && (state.salesProductFilter === null || state.salesProductFilter.has(row.product || ""))
        && (state.salesBreedFilter === null || state.salesBreedFilter.has(row.breed || ""))
        && (state.salesConditionFilter === null || state.salesConditionFilter.has(row.condition || ""))
        && (state.salesAuthorFilter === null || state.salesAuthorFilter.has(row.author || ""))
        && matchesSizeFilter(row, state.salesSizeFilter);
    });
    if (state.salesSortKey === "measure") {
      rows = sortedByKey(rows, state.salesSortKey, state.salesSortDir, primaryMeasureValue);
    } else {
      rows = sortedByKey(rows, state.salesSortKey, state.salesSortDir);
    }
    renderFilterBadge("sales-filter-badge", "Клиент", state.salesClientFilter, function () {
      state.salesClientFilter = null;
      renderSalesPanel();
    });
    renderFilterBadge("sales-product-filter-badge", "Продукт", state.salesProductFilter, function () {
      state.salesProductFilter = null;
      renderSalesPanel();
    });
    renderFilterBadge("sales-breed-filter-badge", "Порода", state.salesBreedFilter, function () {
      state.salesBreedFilter = null;
      renderSalesPanel();
    });
    renderFilterBadge("sales-condition-filter-badge", "Тип", state.salesConditionFilter, function () {
      state.salesConditionFilter = null;
      renderSalesPanel();
    });
    renderSizeBadge("sales-size-filter-badge", state.salesSizeFilter, renderSalesPanel);
    renderFilterBadge("sales-author-filter-badge", "Автор", state.salesAuthorFilter, function () {
      state.salesAuthorFilter = null;
      renderSalesPanel();
    });
    var tbody = document.getElementById("sales-rows");
    var empty = document.getElementById("sales-empty");
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      hideTableTotals("sales-totals-row");
      updateSortArrowsFor("data-sales-sort", state.salesSortKey, state.salesSortDir);
      return;
    }
    empty.style.display = "none";
    var totalQuantity = 0, totalVolume = 0, totalArea = 0, totalLinear = 0, totalAmount = 0;
    var clients = {};
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        row.date || "",
        row.client || "",
        row.product || "",
        row.breed || "",
        row.condition || "",
        row.size || "",
        formatNumber(row.quantity),
        measureCellText(row),
        formatNumber(row.total_amount) + " MDL",
        row.author || "",
      ]);
      tbody.appendChild(tr);
      totalQuantity += numberValue(row.quantity) || 0;
      totalVolume += numberValue(row.volume) || 0;
      totalArea += numberValue(row.area) || 0;
      totalLinear += numberValue(row.linear) || 0;
      totalAmount += numberValue(row.total_amount) || 0;
      if (row.client) {
        clients[row.client] = true;
      }
    });
    var average = rows.length ? Math.round((totalAmount / rows.length) * 100) / 100 : 0;
    // Середня сума й кількість клієнтів не лягають у жодну колонку, тож
    // лишаються другим рядком у клітинці "Итого" - викидати їх заради
    // симетрії було б гірше, ніж лишити на місці.
    document.getElementById("sales-total-note").textContent =
      "средняя " + formatNumber(average) + " MDL · клиентов " + Object.keys(clients).length;
    renderTableTotals("sales-totals-row", {
      "sales-total-quantity": formatNumber(totalQuantity) + " шт",
      "sales-total-measure": measureTotalLines({
        "м3": totalVolume, "м2": totalArea, "мп": totalLinear,
      }),
      "sales-total-amount": formatNumber(totalAmount) + " MDL",
    });
    updateSortArrowsFor("data-sales-sort", state.salesSortKey, state.salesSortDir);
  }

  function renderAntisepticPanel() {
    var rows = state.antisepticRows.filter(function (row) {
      return rowInPeriod(row, state.antisepticPeriod)
        && (state.antisepticClientFilter === null || state.antisepticClientFilter.has(row.client || ""))
        && (state.antisepticAuthorFilter === null || state.antisepticAuthorFilter.has(row.author || ""));
    });
    rows = sortedByKey(rows, state.antisepticSortKey, state.antisepticSortDir);
    renderFilterBadge("antiseptic-filter-badge", "Клиент", state.antisepticClientFilter, function () {
      state.antisepticClientFilter = null;
      renderAntisepticPanel();
    });
    renderFilterBadge("antiseptic-author-filter-badge", "Автор", state.antisepticAuthorFilter, function () {
      state.antisepticAuthorFilter = null;
      renderAntisepticPanel();
    });
    var tbody = document.getElementById("antiseptic-rows");
    var empty = document.getElementById("antiseptic-empty");
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      hideTableTotals("antiseptic-totals-row");
      updateSortArrowsFor("data-antiseptic-sort", state.antisepticSortKey, state.antisepticSortDir);
      return;
    }
    empty.style.display = "none";
    var totalVolume = 0, totalAmount = 0;
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        row.date || "",
        row.client || "",
        formatNumber(row.volume) + " м3",
        formatNumber(row.total_amount) + " MDL",
        row.author || "",
      ]);
      tbody.appendChild(tr);
      totalVolume += numberValue(row.volume) || 0;
      totalAmount += numberValue(row.total_amount) || 0;
    });
    renderTableTotals("antiseptic-totals-row", {
      // Антисептирование завжди рахується кубом, незалежно від того, як
      // продається сам товар.
      "antiseptic-total-measure": measureTotalLines({ "м3": totalVolume }),
      "antiseptic-total-amount": formatNumber(totalAmount) + " MDL",
    });
    updateSortArrowsFor("data-antiseptic-sort", state.antisepticSortKey, state.antisepticSortDir);
  }

  // Задача користувача: "додай вкладку списання... будемо бачити що і
  // коли списали і чому" - той самий принцип, що вже мають Продажи/
  // Антисептирование (період-чіпи + фільтр-модал + сортування), лише без
  // клієнта/суми (списання їх не має) - Продукт замість Клієнта, Штук
  // замість Об'єму/Суми.
  function renderWriteoffPanel() {
    var rows = state.writeoffRows.filter(function (row) {
      return rowInPeriod(row, state.writeoffPeriod)
        && (state.writeoffProductFilter === null || state.writeoffProductFilter.has(row.product || ""))
        && (state.writeoffBreedFilter === null || state.writeoffBreedFilter.has(row.breed || ""))
        && (state.writeoffConditionFilter === null || state.writeoffConditionFilter.has(row.condition || ""))
        && (state.writeoffReasonFilter === null || state.writeoffReasonFilter.has(row.reason || ""))
        && (state.writeoffAuthorFilter === null || state.writeoffAuthorFilter.has(row.author || ""))
        && matchesSizeFilter(row, state.writeoffSizeFilter);
    });
    rows = sortedByKey(rows, state.writeoffSortKey, state.writeoffSortDir);
    renderFilterBadge("writeoff-filter-badge", "Продукт", state.writeoffProductFilter, function () {
      state.writeoffProductFilter = null;
      renderWriteoffPanel();
    });
    renderFilterBadge("writeoff-breed-filter-badge", "Порода", state.writeoffBreedFilter, function () {
      state.writeoffBreedFilter = null;
      renderWriteoffPanel();
    });
    renderFilterBadge("writeoff-condition-filter-badge", "Тип", state.writeoffConditionFilter, function () {
      state.writeoffConditionFilter = null;
      renderWriteoffPanel();
    });
    renderFilterBadge("writeoff-reason-filter-badge", "Причина", state.writeoffReasonFilter, function () {
      state.writeoffReasonFilter = null;
      renderWriteoffPanel();
    });
    renderSizeBadge("writeoff-size-filter-badge", state.writeoffSizeFilter, renderWriteoffPanel);
    renderFilterBadge("writeoff-author-filter-badge", "Автор", state.writeoffAuthorFilter, function () {
      state.writeoffAuthorFilter = null;
      renderWriteoffPanel();
    });
    var tbody = document.getElementById("writeoff-rows");
    var empty = document.getElementById("writeoff-empty");
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      hideTableTotals("writeoff-totals-row");
      updateSortArrowsFor("data-writeoff-sort", state.writeoffSortKey, state.writeoffSortDir);
      return;
    }
    empty.style.display = "none";
    var totalQuantity = 0;
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        row.date || "",
        row.product || "",
        row.breed || "",
        row.condition || "",
        row.size || "",
        formatNumber(row.quantity),
        row.reason || "",
        row.author || "",
      ]);
      tbody.appendChild(tr);
      totalQuantity += numberValue(row.quantity) || 0;
    });
    renderTableTotals("writeoff-totals-row", {
      "writeoff-total-quantity": formatNumber(totalQuantity) + " шт",
    });
    updateSortArrowsFor("data-writeoff-sort", state.writeoffSortKey, state.writeoffSortDir);
  }

  // Задача користувача (2026-08-14): "Приход" - нова вкладка (той самий
  // принцип, що вже мають Продажи/Антисептирование/Списание: період-чіпи +
  // фільтр розміру + сортування), Автор - хто провів прихід.
  function renderIncomePanel() {
    var rows = state.incomeRows.filter(function (row) {
      return rowInPeriod(row, state.incomePeriod)
        && matchesSizeFilter(row, state.incomeSizeFilter)
        && (state.incomeAuthorFilter === null || state.incomeAuthorFilter.has(row.author || ""))
        && (state.incomeProductFilter === null || state.incomeProductFilter.has(row.product || ""))
        && (state.incomeBreedFilter === null || state.incomeBreedFilter.has(row.breed || ""))
        && (state.incomeConditionFilter === null || state.incomeConditionFilter.has(row.condition || ""));
    });
    rows = sortedByKey(rows, state.incomeSortKey, state.incomeSortDir);
    renderSizeBadge("income-size-filter-badge", state.incomeSizeFilter, renderIncomePanel);
    renderFilterBadge("income-author-filter-badge", "Автор", state.incomeAuthorFilter, function () {
      state.incomeAuthorFilter = null;
      renderIncomePanel();
    });
    renderFilterBadge("income-product-filter-badge", "Продукт", state.incomeProductFilter, function () {
      state.incomeProductFilter = null;
      renderIncomePanel();
    });
    renderFilterBadge("income-breed-filter-badge", "Порода", state.incomeBreedFilter, function () {
      state.incomeBreedFilter = null;
      renderIncomePanel();
    });
    renderFilterBadge("income-condition-filter-badge", "Тип", state.incomeConditionFilter, function () {
      state.incomeConditionFilter = null;
      renderIncomePanel();
    });
    var tbody = document.getElementById("income-rows");
    var empty = document.getElementById("income-empty");
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      hideTableTotals("income-totals-row");
      updateSortArrowsFor("data-income-sort", state.incomeSortKey, state.incomeSortDir);
      return;
    }
    empty.style.display = "none";
    var totalQuantity = 0;
    // Вимір приходу рахує сервер (income_report_rows) - у самому листі
    // ПРИХОД МАТЕРИАЛА його немає, а рахувати тут означало б завести ще
    // одну копію правила "25x50 - це погонні метри".
    var incomeVolume = 0, incomeArea = 0, incomeLinear = 0;
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        row.date || "",
        row.product || "",
        row.breed || "",
        row.condition || "",
        row.size || "",
        formatNumber(row.quantity),
        measureCellText(row),
        row.author || "",
      ]);
      tbody.appendChild(tr);
      totalQuantity += numberValue(row.quantity) || 0;
      incomeVolume += numberValue(row.volume) || 0;
      incomeArea += numberValue(row.area) || 0;
      incomeLinear += numberValue(row.linear) || 0;
    });
    renderTableTotals("income-totals-row", {
      "income-total-quantity": formatNumber(totalQuantity) + " шт",
      "income-total-measure": measureTotalLines({
        "м3": incomeVolume, "м2": incomeArea, "мп": incomeLinear,
      }),
    });
    updateSortArrowsFor("data-income-sort", state.incomeSortKey, state.incomeSortDir);
  }

  // Задача користувача: "Клиент" - вибірковий фільтр (той самий чекбокс-
  // модал, що й Продукт/Порода/Тип, з "Выделить всё"/"Снять выделение"
  // зверху) - скрізь, де є колонка "Клиент" (Продажи/Антисептирование/
  // Клиенты), не лише на вкладці "Клиенты". Список клієнтів для фільтра -
  // з УСІХ рядків відповідної вкладки, незалежно від активного періоду,
  // щоб варіанти не зникали/з'являлись при зміні періоду (той самий
  // принцип, що й distinctFieldValues/distinctValuesFor нижче).
  //
  // Реальний ризик (аудит коду, 2026-08-14): тут раніше жила ОКРЕМА копія
  // distinctFieldValues нижче, буквально та сама логіка з "client" замість
  // параметра field - прибрано, виклики нижче тепер напряму йдуть через
  // distinctFieldValues(rows, "client").
  //
  // Задача користувача (вкладка "Списание"): той самий принцип, лише
  // параметризований під БУДЬ-яке поле (тут - "Продукт"), щоб не плодити
  // окремий хелпер на кожну колонку.
  // Реальний ризик (аудит коду, 2026-08-14): на відміну від
  // distinctStockFieldValues вище (Остатки), ця функція ніколи не включала
  // порожнє значення в список - openGenericValueModal і так уміє показати
  // для нього чекбокс "(не указано)" (VALUE_BLANK_LABEL), але лише якщо
  // "" реально є серед values. Без цього чекбокса зняття хоч однієї
  // галочки й натискання "Применить" НАЗАВЖДИ ховало рядки з порожнім
  // полем (напр. без вказаної причини списання) без жодного способу їх
  // повернути - filterSet.has(row.field || "") просто ніколи не збігався.
  function distinctFieldValues(rows, field) {
    var seen = {};
    var values = [];
    var hasBlank = false;
    rows.forEach(function (row) {
      var value = row[field];
      if (!value) {
        hasBlank = true;
        return;
      }
      if (!seen[value]) {
        seen[value] = true;
        values.push(value);
      }
    });
    values.sort();
    if (hasBlank) {
      values.push("");
    }
    return values;
  }

  // Загальний рендер бейджа "активний фільтр" - той самий принцип, що й
  // renderSizeBadge/renderValueBadge вище, лише параметризований під
  // конкретне поле стану/бейдж-елемент кожної вкладки.
  function renderFilterBadge(badgeId, label, filterSet, onClear) {
    var badge = document.getElementById(badgeId);
    if (filterSet === null) {
      badge.style.display = "none";
      badge.innerHTML = "";
      return;
    }
    badge.style.display = "flex";
    badge.textContent = "";
    var labelEl = document.createElement("span");
    labelEl.textContent = label + ": " + Array.from(filterSet).join(", ");
    badge.appendChild(labelEl);
    var clear = document.createElement("span");
    clear.className = "badge-clear";
    clear.textContent = "×";
    clear.addEventListener("click", onClear);
    badge.appendChild(clear);
  }

  // Загальне сортування "більше/менше" по кліку на заголовок - той самий
  // принцип, що вже діяв лише для "Клиенты" (state.clientsSortKey), тепер
  // параметризований під attrName ("data-XXX-sort") і два ключі стану.
  function updateSortArrowsFor(attrName, sortKey, sortDir) {
    document.querySelectorAll("th[" + attrName + "]").forEach(function (th) {
      var arrow = th.querySelector(".sort-arrow");
      if (!arrow) {
        return;
      }
      if (th.getAttribute(attrName) === sortKey) {
        arrow.textContent = sortDir === 1 ? "↑" : "↓";
      } else {
        arrow.textContent = "";
      }
    });
  }

  function wireSortHeaders(attrName, sortKeyProp, sortDirProp, rerender) {
    document.querySelectorAll("th[" + attrName + "]").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute(attrName);
        if (state[sortKeyProp] === key) {
          state[sortDirProp] = state[sortDirProp] * -1;
        } else {
          state[sortKeyProp] = key;
          state[sortDirProp] = 1;
        }
        rerender();
      });
    });
  }

  // "Дата" - завжди календар (не чекбокс-список значень, які тут ні до
  // чого) - клік по заголовку відкриває ТОЙ САМИЙ range-modal, що й чип
  // "Свой период", лише як другий вхід до нього прямо з таблиці.
  function openDateHeaderCalendar(periodState, containerId, onChange) {
    openRangeModal(function (from, to) {
      periodState.key = "custom";
      periodState.from = from;
      periodState.to = to;
      renderPeriodChips(containerId, periodState, onChange);
      onChange();
    });
  }

  function openClientsFilterModal() {
    openGenericValueModal("Клиент", distinctFieldValues(state.salesRows, "client"), state.clientsFilter, function (result) {
      state.clientsFilter = result;
      renderClientsPanel();
    });
  }

  function sortedClientRows(rows) {
    if (!state.clientsSortKey) {
      return rows.slice().sort(function (a, b) { return b.total_amount - a.total_amount; });
    }
    var key = state.clientsSortKey;
    var dir = state.clientsSortDir;
    return rows.slice().sort(function (a, b) { return dir * (a[key] - b[key]); });
  }

  function renderClientsPanel() {
    var rows = state.salesRows.filter(function (row) {
      return rowInPeriod(row, state.clientsPeriod)
        && (state.clientsFilter === null || state.clientsFilter.has(row.client || ""));
    });
    var groups = {};
    var order = [];
    rows.forEach(function (row) {
      if (!row.client) {
        return;
      }
      if (!groups[row.client]) {
        groups[row.client] = { client: row.client, count: 0, quantity: 0, total_amount: 0 };
        order.push(row.client);
      }
      var bucket = groups[row.client];
      bucket.count += 1;
      bucket.quantity += numberValue(row.quantity) || 0;
      bucket.total_amount += numberValue(row.total_amount) || 0;
    });
    var grouped = sortedClientRows(order.map(function (client) { return groups[client]; }));

    renderFilterBadge("clients-filter-badge", "Клиент", state.clientsFilter, function () {
      state.clientsFilter = null;
      renderClientsPanel();
    });
    var tbody = document.getElementById("clients-rows");
    var empty = document.getElementById("clients-empty");
    tbody.innerHTML = "";
    if (!grouped.length) {
      empty.style.display = "block";
      hideTableTotals("clients-totals-row");
      updateSortArrowsFor("data-clients-sort", state.clientsSortKey, state.clientsSortDir);
      return;
    }
    empty.style.display = "none";
    var totalCount = 0, totalAmount = 0, totalQuantity = 0;
    grouped.forEach(function (bucket, index) {
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        bucket.client,
        bucket.count,
        formatNumber(bucket.quantity),
        formatNumber(bucket.total_amount) + " MDL",
      ]);
      tbody.appendChild(tr);
      totalCount += bucket.count;
      totalAmount += bucket.total_amount;
      totalQuantity += numberValue(bucket.quantity) || 0;
    });
    renderTableTotals("clients-totals-row", {
      // "Продаж" тут - кількість продажів, а не штук: колонка називається
      // так само, як у рядках, тож число має стояти саме під нею.
      "clients-total-count": formatNumber(totalCount),
      "clients-total-quantity": formatNumber(totalQuantity),
      "clients-total-amount": formatNumber(totalAmount) + " MDL",
    });
    updateSortArrowsFor("data-clients-sort", state.clientsSortKey, state.clientsSortDir);
  }

  // Задача користувача: чекбокс "Скрыть нулевые остатки" ховає рядки, де
  // штук фактично 0. Порівняння через formatNumber (не голе === 0) -
  // balance_qty/volume в БД часто зберігається як float-шум на кшталт
  // -6.9e-18 замість точного 0 (той самий клас похибки округлення, що вже
  // трапляється по всій програмі), тож рядок все одно ПОКАЗУЄ "0 шт" -
  // саме за цим візуальним нулем і звіряємось, а не за сирим числом.
  function isZeroLowStockRow(row) {
    return formatNumber(row.quantity) === "0";
  }

  function renderLowStockPanel() {
    var tbody = document.getElementById("low-stock-rows");
    var empty = document.getElementById("low-stock-empty");
    var hint = document.getElementById("low-stock-hint");
    hint.textContent = "Позиций с низким остатком: " + state.lowStockRows.length
      + " (порог: " + formatNumber(state.lowStockThreshold) + " шт).";
    var rows = state.lowStockHideZero
      ? state.lowStockRows.filter(function (row) { return !isZeroLowStockRow(row); })
      : state.lowStockRows;
    rows = rows.filter(function (row) {
      return (state.lowStockProductFilter === null || state.lowStockProductFilter.has(row.product || ""))
        && (state.lowStockBreedFilter === null || state.lowStockBreedFilter.has(row.breed || ""))
        && (state.lowStockConditionFilter === null || state.lowStockConditionFilter.has(row.condition || ""))
        && matchesSizeFilter(row, state.lowStockSizeFilter);
    });
    rows = sortedByKey(rows, state.lowStockSortKey, state.lowStockSortDir);
    renderFilterBadge("low-stock-product-filter-badge", "Продукт", state.lowStockProductFilter, function () {
      state.lowStockProductFilter = null;
      renderLowStockPanel();
    });
    renderFilterBadge("low-stock-breed-filter-badge", "Порода", state.lowStockBreedFilter, function () {
      state.lowStockBreedFilter = null;
      renderLowStockPanel();
    });
    renderFilterBadge("low-stock-condition-filter-badge", "Тип", state.lowStockConditionFilter, function () {
      state.lowStockConditionFilter = null;
      renderLowStockPanel();
    });
    renderSizeBadge("low-stock-size-filter-badge", state.lowStockSizeFilter, renderLowStockPanel);
    tbody.innerHTML = "";
    if (!rows.length) {
      empty.style.display = "block";
      updateSortArrowsFor("data-lowstock-sort", state.lowStockSortKey, state.lowStockSortDir);
      return;
    }
    empty.style.display = "none";
    rows.forEach(function (row, index) {
      var balanceParts = [];
      if (row.quantity !== null && row.quantity !== undefined && row.quantity !== "") {
        balanceParts.push(formatNumber(row.quantity) + " шт");
      }
      if (row.volume) {
        balanceParts.push(formatNumber(row.volume) + " м3");
      }
      if (row.area) {
        balanceParts.push(formatNumber(row.area) + " м2");
      }
      var tr = document.createElement("tr");
      appendRowCells(tr, [
        index + 1,
        row.product || "",
        row.breed || "",
        row.condition || "",
        row.size || "",
        balanceParts.join(", "),
      ]);
      tbody.appendChild(tr);
    });
    updateSortArrowsFor("data-lowstock-sort", state.lowStockSortKey, state.lowStockSortDir);
  }

  // Задача користувача: поріг "Низкий остаток" міняти може ТІЛЬКИ
  // адміністратор, і зміна має застосовуватись одразу. can_edit_low_stock_
  // threshold приходить з бекенду вже за реальною роллю користувача, що
  // натиснув кнопку в чаті (webapp_server.py однаково перевіряє роль ще
  // раз на самому запису - клієнтський прапорець лише ховає поле для
  // решти).
  function initLowStockThresholdEdit() {
    var wrap = document.getElementById("low-stock-threshold-edit");
    if (!state.canEditLowStockThreshold) {
      wrap.style.display = "none";
      return;
    }
    wrap.style.display = "flex";
    var input = document.getElementById("low-stock-threshold-input");
    var errorEl = document.getElementById("low-stock-threshold-error");
    input.value = state.lowStockThreshold;
    document.getElementById("low-stock-threshold-apply").addEventListener("click", function () {
      var raw = input.value.trim();
      var value = parseInt(raw, 10);
      if (raw === "" || isNaN(value) || value < 0) {
        errorEl.textContent = "Введите неотрицательное целое число.";
        return;
      }
      errorEl.textContent = "";
      postTemplateAction({ action: "update_low_stock_threshold", token: state.contextToken, threshold: value })
        .then(function (data) {
          state.lowStockThreshold = data.threshold;
          state.lowStockRows = data.rows || [];
          input.value = state.lowStockThreshold;
          renderLowStockPanel();
        })
        .catch(function (error) {
          errorEl.textContent = error.message || "Не удалось применить изменение.";
        });
    });
  }

  // Задача користувача: "додай вкладку списання" - позиція в масиві МАЄ
  // збігатись із порядком _DATA_BROWSER_TAB_KEYS (telegram_dialog_core.py) -
  // TAB_KEYS.forEach(function(key, index) { tabLabels[key] = ctx.tabs[index] })
  // нижче зіставляє мітки ПОЗИЦІЙНО, не за назвою.
  // Задача користувача (2026-08-14): "Приход" - НОВА вкладка, додана в
  // кінець (той самий порядок позиційно відповідає _DATA_BROWSER_TAB_KEYS,
  // telegram_dialog_core.py - там теж додана в кінець списку).
  var TAB_KEYS = ["stock", "movement", "sales", "antiseptic", "writeoff", "clients", "low_stock", "income"];
  var TAB_PANEL_IDS = {
    stock: "panel-stock",
    movement: "panel-movement",
    sales: "panel-sales",
    antiseptic: "panel-antiseptic",
    writeoff: "panel-writeoff",
    clients: "panel-clients",
    low_stock: "panel-low-stock",
    income: "panel-income",
  };
  // ---- Вкладка «Движение» (ТЗ п.8.4/8.5) ----
  // Залишок на дату рахується НАЗАД від поточного: беремо теперішній
  // залишок і віднімаємо всі рухи після цієї дати. Тому цифри правдиві
  // рівно настільки, наскільки сягає історія рухів.
  var MEASURE_UNITS = { volume: "м3", area: "м2", linear: "мп" };
  var MOVEMENT_LINES = [
    { key: "income", label: "Приход" },
    { key: "sale", label: "Продажи" },
    { key: "writeoff", label: "Списание" },
    { key: "exchange_in", label: "Получено по обмену" },
    { key: "exchange_out", label: "Отдано по обмену" },
    { key: "correction", label: "Коррекция" },
    { key: "rollback", label: "Откат" },
  ];

  // Категорія = продукт, і лише коли продукт СПРАВДІ ділиться сухістю
  // (у складі є два різні стани) - «Доска AD» / «Доска KD». «N/A» та інші
  // заглушки станом не рахуються, тож ОСБ лишається «ОСБ», а Рейка
  // розділиться сама, щойно з'явиться KD.
  var CONDITION_PLACEHOLDERS = { "": 1, "n/a": 1, "-": 1, "—": 1, "–": 1, "нет": 1 };

  function meaningfulCondition(row) {
    var value = String(row.condition || "").trim();
    return CONDITION_PLACEHOLDERS[value.toLowerCase()] ? "" : value;
  }

  function splitByConditionMap() {
    var byProduct = {};
    state.rows.concat(state.movementRows).forEach(function (row) {
      var product = row.product || "";
      var condition = meaningfulCondition(row);
      if (!product || !condition) {
        return;
      }
      if (!byProduct[product]) {
        byProduct[product] = {};
      }
      byProduct[product][condition] = true;
    });
    var split = {};
    Object.keys(byProduct).forEach(function (product) {
      if (Object.keys(byProduct[product]).length > 1) {
        split[product] = true;
      }
    });
    return split;
  }

  function movementCategory(row, split) {
    var product = row.product || "";
    var condition = meaningfulCondition(row);
    var map = split || state._movementSplit || {};
    return map[product] && condition ? product + " " + condition : product;
  }

  function movementCategories() {
    var seen = {};
    var list = [];
    state.rows.concat(state.movementRows).forEach(function (row) {
      var key = movementCategory(row);
      if (key && !seen[key]) {
        seen[key] = true;
        list.push(key);
      }
    });
    list.sort();
    return list;
  }

  function currentTotals() {
    var totals = {};
    state.rows.forEach(function (row) {
      var key = movementCategory(row);
      if (!key) {
        return;
      }
      if (!totals[key]) {
        totals[key] = { quantity: 0, measure: 0, unit: row.unit || "" };
      }
      totals[key].quantity += numberValue(row.quantity) || 0;
      totals[key].measure += numberValue(row.measure) || 0;
      if (!totals[key].unit && row.unit) {
        totals[key].unit = row.unit;
      }
    });
    return totals;
  }

  // Дві різні межі, і плутати їх не можна:
  // • «після дня» - для залишку на КІНЕЦЬ періоду; без верхньої межі
  //   віднімати нема чого, тож день null означає нуль;
  // • «від дня» - для залишку на ПОЧАТОК періоду; без нижньої межі це
  //   початок історії, тож віднімаються ВСІ рухи.
  function movementSum(category, predicate) {
    var sum = { quantity: 0, measure: 0 };
    state.movementRows.forEach(function (row) {
      if (movementCategory(row) !== category) {
        return;
      }
      var rowDate = parseRuDate(row.date);
      if (!rowDate || !predicate(rowDate)) {
        return;
      }
      sum.quantity += numberValue(row.quantity) || 0;
      sum.measure += numberValue(row.measure) || 0;
    });
    return sum;
  }

  function movementsAfterDay(category, day) {
    if (!day) {
      return { quantity: 0, measure: 0 };
    }
    return movementSum(category, function (rowDate) { return rowDate > day; });
  }

  function movementsFromDay(category, day) {
    return movementSum(category, function (rowDate) { return !day || rowDate >= day; });
  }

  function movementsWithin(category, from, to) {
    var byType = {};
    state.movementRows.forEach(function (row) {
      if (movementCategory(row) !== category) {
        return;
      }
      var rowDate = parseRuDate(row.date);
      if (!rowDate || (from && rowDate < from) || (to && rowDate > to)) {
        return;
      }
      if (!byType[row.type]) {
        byType[row.type] = { quantity: 0, measure: 0 };
      }
      byType[row.type].quantity += numberValue(row.quantity) || 0;
      byType[row.type].measure += numberValue(row.measure) || 0;
    });
    return byType;
  }

  function movementAmountText(totals, unit) {
    var parts = [];
    if (unit && Math.abs(totals.measure) > 1e-9) {
      parts.push(formatNumber(Math.round(totals.measure * 1000) / 1000) + " " + unit);
    }
    parts.push(formatNumber(Math.round(totals.quantity * 1000) / 1000) + " шт");
    return parts.join(" · ");
  }

  function signedAmountText(totals, unit) {
    var text = movementAmountText({ quantity: Math.abs(totals.quantity), measure: Math.abs(totals.measure) }, unit);
    return (totals.quantity < 0 || totals.measure < 0 ? "−" : "+") + text;
  }

  function movementCardElement(category, totals) {
    var card = document.createElement("div");
    card.className = "movement-card";
    var head = document.createElement("div");
    head.className = "movement-card-head";
    head.textContent = category;
    card.appendChild(head);
    totals.forEach(function (line) {
      var row = document.createElement("div");
      row.className = "movement-line" + (line.strong ? " strong" : "");
      var label = document.createElement("span");
      label.textContent = line.label;
      var value = document.createElement("b");
      value.textContent = line.value;
      if (line.sign > 0) {
        value.className = "movement-plus";
      } else if (line.sign < 0) {
        value.className = "movement-minus";
      }
      row.appendChild(label);
      row.appendChild(value);
      card.appendChild(row);
    });
    return card;
  }

  function renderMovementPanel() {
    state._movementSplit = splitByConditionMap();
    renderPeriodChips("movement-period-chips", state.movementPeriod, renderMovementPanel);

    var modes = document.getElementById("movement-mode-chips");
    modes.innerHTML = "";
    [{ key: "period", label: "Движение за период" }, { key: "date", label: "Остаток на дату…" }].forEach(function (mode) {
      var chip = document.createElement("span");
      var active = (mode.key === "date") === !!state.movementDate;
      chip.className = "data-chip" + (active ? " active" : "");
      chip.textContent = mode.key === "date" && state.movementDate ? "На дату: " + formatRuDate(state.movementDate) : mode.label;
      chip.addEventListener("click", function () {
        if (mode.key === "period") {
          state.movementDate = null;
          renderMovementPanel();
          return;
        }
        openRangeModal(function (from) {
          state.movementDate = from || null;
          renderMovementPanel();
        });
      });
      modes.appendChild(chip);
    });

    var categories = movementCategories();
    var chips = document.getElementById("movement-category-chips");
    chips.innerHTML = "";
    [null].concat(categories).forEach(function (category) {
      var chip = document.createElement("span");
      chip.className = "data-chip" + (state.movementCategory === category ? " active" : "");
      chip.textContent = category === null ? "Все" : category;
      chip.addEventListener("click", function () {
        state.movementCategory = category;
        renderMovementPanel();
      });
      chips.appendChild(chip);
    });

    var body = document.getElementById("movement-body");
    body.innerHTML = "";
    var totals = currentTotals();
    var shown = categories.filter(function (category) {
      return state.movementCategory === null || state.movementCategory === category;
    });
    var range = periodRange(state.movementPeriod);
    shown.forEach(function (category) {
      var current = totals[category] || { quantity: 0, measure: 0, unit: "" };
      var unit = current.unit;
      if (state.movementDate) {
        var after = movementsAfterDay(category, state.movementDate);
        body.appendChild(movementCardElement(category, [{
          label: "Остаток на " + formatRuDate(state.movementDate),
          value: movementAmountText({ quantity: current.quantity - after.quantity, measure: current.measure - after.measure }, unit),
          strong: true,
        }]));
        return;
      }
      var afterEnd = movementsAfterDay(category, range.to);
      var afterStart = movementsFromDay(category, range.from);
      var within = movementsWithin(category, range.from, range.to);
      var lines = [{
        label: "Остаток на начало" + (range.from ? " " + formatRuDate(range.from) : " истории"),
        value: movementAmountText({ quantity: current.quantity - afterStart.quantity, measure: current.measure - afterStart.measure }, unit),
        strong: true,
      }];
      MOVEMENT_LINES.forEach(function (line) {
        var sums = within[line.key];
        if (!sums || (Math.abs(sums.quantity) < 1e-9 && Math.abs(sums.measure) < 1e-9)) {
          return;
        }
        lines.push({ label: line.label, value: signedAmountText(sums, unit), sign: sums.quantity >= 0 ? 1 : -1 });
      });
      lines.push({
        label: "Остаток на конец" + (range.to ? " " + formatRuDate(range.to) : " (сегодня)"),
        value: movementAmountText({ quantity: current.quantity - afterEnd.quantity, measure: current.measure - afterEnd.measure }, unit),
        strong: true,
      });
      body.appendChild(movementCardElement(category, lines));
    });
    document.getElementById("movement-empty").style.display = shown.length ? "none" : "";
  }

  function switchTab(key) {
    state.activeTab = key;
    TAB_KEYS.forEach(function (tabKey) {
      // "flex", не "block": інлайновий стиль б'є CSS, а панель мусить
      // лишатись flex-колонкою - інакше таблиця не розтягнеться на
      // залишок висоти й прокрутка знову дістанеться всій сторінці.
      document.getElementById(TAB_PANEL_IDS[tabKey]).style.display = tabKey === key ? "flex" : "none";
    });
    renderSidebar();
  }

  // Задача користувача (2026-08-13): раніше рядок вкладок можна було
  // перемістити зверху/збоку (видимі кнопки + ПКМ/long-press) - прибрано
  // назавжди, тепер завжди горизонтальний рядок під заголовком (те, що
  // раніше було станом "top").
  function renderSidebar() {
    var container = document.getElementById("tab-sidebar");
    container.innerHTML = "";
    TAB_KEYS.forEach(function (key) {
      var item = document.createElement("div");
      item.className = "tab-item" + (state.activeTab === key ? " active" : "");
      item.textContent = state.tabLabels[key] || key;
      item.addEventListener("click", function () {
        switchTab(key);
      });
      container.appendChild(item);
    });
  }

  // Задача користувача: довгий список у попапі-фільтрі раніше не давав
  // закрити себе (заголовок/кнопка виїжджали за екран - див. CSS-фікс
  // .modal-card/.modal-body вище). Тепер БУДЬ-який фільтровий попап
  // (Размер/Продукт/Порода/Тип/Клиент/Ед.измерения/Свой период) закривається
  // ще двома способами: кліком по затемненому фону ПОЗА карткою і клавішею
  // Esc - незалежно від того, який саме з трьох попапів зараз відкритий.
  var MODAL_OVERLAY_IDS = ["size-modal", "value-modal", "range-modal"];

  function closeModal(id) {
    document.getElementById(id).style.display = "none";
  }

  function closeAllModals() {
    MODAL_OVERLAY_IDS.forEach(closeModal);
  }

  function wireModalDismiss() {
    MODAL_OVERLAY_IDS.forEach(function (id) {
      var overlay = document.getElementById(id);
      overlay.addEventListener("click", function (event) {
        if (event.target === overlay) {
          closeModal(id);
        }
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeAllModals();
      }
    });
  }

  // Задача користувача (2026-08-13): скопіювати адресу ЦІЄЇ Ж сторінки
  // (window.location.href вже містить ?t=токен) - щоб відкрити "Остатки"
  // окремою вкладкою в звичайному браузері на ноутбуці паралельно з
  // Telegram (в самому Telegram - лише одна форма відкрита одночасно).
  function copyLinkFallback(url) {
    var textarea = document.createElement("textarea");
    textarea.value = url;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
      document.execCommand("copy");
    } catch (e) {
      // Мовчки ігноруємо - адреса й так видима в рядку поруч, можна
      // виділити й скопіювати вручну.
    }
    document.body.removeChild(textarea);
  }

  function copyLinkToClipboard() {
    var url = window.location.href;
    var button = document.getElementById("copy-link-button");
    var hint = document.getElementById("link-copied-hint");

    function showCopied() {
      button.classList.add("is-pressed");
      setTimeout(function () {
        button.classList.remove("is-pressed");
      }, 150);
      hint.classList.add("is-visible");
      setTimeout(function () {
        hint.classList.remove("is-visible");
      }, 2000);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(showCopied).catch(function () {
        copyLinkFallback(url);
        showCopied();
      });
    } else {
      copyLinkFallback(url);
      showCopied();
    }
  }

  // Задача користувача (2026-08-14): "Открыть в браузере" - одразу
  // відкриває ЦЮ Ж сторінку (той самий URL, що й "Копировать") у
  // ЗОВНІШНЬОМУ браузері, поза Telegram. tg.openLink() - офіційний спосіб
  // Telegram WebApp SDK саме для цього (на відміну від window.open(), який
  // усередині Telegram міг би відкрити лише внутрішній оглядач); working
  // fallback на звичайний window.open лишається на випадок відкриття поза
  // Telegram (тестування в звичайному браузері, де tg == null).
  function openLinkInBrowser() {
    var url = window.location.href;
    if (tg && tg.openLink) {
      tg.openLink(url);
    } else {
      window.open(url, "_blank");
    }
  }

  function wireEvents() {
    var linkUrlEl = document.getElementById("link-url-text");
    linkUrlEl.textContent = window.location.href;
    linkUrlEl.href = window.location.href;
    document.getElementById("copy-link-button").addEventListener("click", copyLinkToClipboard);
    document.getElementById("open-browser-button").addEventListener("click", openLinkInBrowser);
    document.getElementById("refresh-data-button").addEventListener("click", refreshDataOnly);
    document.querySelectorAll("th[data-sort]").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        if (key === "index") {
          return;
        }
        if (state.sortKey === key) {
          state.sortDir = state.sortDir * -1;
        } else {
          state.sortKey = key;
          state.sortDir = 1;
        }
        renderStockPanel();
      });
    });
    document.getElementById("size-filter-trigger").addEventListener("click", function () {
      openSizeModal(visibleRowsExcept("size"), state.sizeFilter, renderStockPanel);
    });
    document.getElementById("size-modal-close").addEventListener("click", function () {
      closeModal("size-modal");
    });
    document.getElementById("product-filter-trigger").addEventListener("click", function () {
      openValueModal("product");
    });
    document.getElementById("breed-filter-trigger").addEventListener("click", function () {
      openValueModal("breed");
    });
    document.getElementById("condition-filter-trigger").addEventListener("click", function () {
      openValueModal("condition");
    });
    document.getElementById("measure-filter-trigger").addEventListener("click", function () {
      openValueModal("unit");
    });
    document.getElementById("value-modal-close").addEventListener("click", function () {
      closeModal("value-modal");
    });
    document.getElementById("range-modal-close").addEventListener("click", function () {
      closeModal("range-modal");
    });
    wireModalDismiss();
    document.getElementById("clients-client-filter-trigger").addEventListener("click", openClientsFilterModal);
    document.getElementById("low-stock-hide-zero").addEventListener("change", function (event) {
      state.lowStockHideZero = event.target.checked;
      renderLowStockPanel();
    });
    wireSortHeaders("data-clients-sort", "clientsSortKey", "clientsSortDir", renderClientsPanel);
    wireSortHeaders("data-sales-sort", "salesSortKey", "salesSortDir", renderSalesPanel);
    wireSortHeaders("data-antiseptic-sort", "antisepticSortKey", "antisepticSortDir", renderAntisepticPanel);
    wireSortHeaders("data-writeoff-sort", "writeoffSortKey", "writeoffSortDir", renderWriteoffPanel);
    wireSortHeaders("data-income-sort", "incomeSortKey", "incomeSortDir", renderIncomePanel);
    document.getElementById("sales-client-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Клиент", distinctFieldValues(state.salesRows, "client"), state.salesClientFilter, function (result) {
        state.salesClientFilter = result;
        renderSalesPanel();
      });
    });
    document.getElementById("antiseptic-client-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Клиент", distinctFieldValues(state.antisepticRows, "client"), state.antisepticClientFilter, function (result) {
        state.antisepticClientFilter = result;
        renderAntisepticPanel();
      });
    });
    document.getElementById("antiseptic-author-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Автор", distinctFieldValues(state.antisepticRows, "author"), state.antisepticAuthorFilter, function (result) {
        state.antisepticAuthorFilter = result;
        renderAntisepticPanel();
      });
    });
    document.getElementById("sales-date-filter-trigger").addEventListener("click", function () {
      openDateHeaderCalendar(state.salesPeriod, "sales-period-chips", renderSalesPanel);
    });
    document.getElementById("antiseptic-date-filter-trigger").addEventListener("click", function () {
      openDateHeaderCalendar(state.antisepticPeriod, "antiseptic-period-chips", renderAntisepticPanel);
    });
    document.getElementById("writeoff-product-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Продукт", distinctFieldValues(state.writeoffRows, "product"), state.writeoffProductFilter, function (result) {
        state.writeoffProductFilter = result;
        renderWriteoffPanel();
      });
    });
    document.getElementById("writeoff-date-filter-trigger").addEventListener("click", function () {
      openDateHeaderCalendar(state.writeoffPeriod, "writeoff-period-chips", renderWriteoffPanel);
    });
    // Задача користувача (2026-08-14, скріншот "Позиция"): "не має бути
    // такого злиття" - замість одного модала по всьому рядку тепер окремі
    // Продукт/Порода/Тип (generic value-modal) + Размер (справжній
    // size-modal, той самий принцип, що вже мають Списание/Приход нижче).
    document.getElementById("sales-product-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Продукт", distinctFieldValues(state.salesRows, "product"), state.salesProductFilter, function (result) {
        state.salesProductFilter = result;
        renderSalesPanel();
      });
    });
    document.getElementById("sales-breed-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Порода", distinctFieldValues(state.salesRows, "breed"), state.salesBreedFilter, function (result) {
        state.salesBreedFilter = result;
        renderSalesPanel();
      });
    });
    document.getElementById("sales-condition-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Тип", distinctFieldValues(state.salesRows, "condition"), state.salesConditionFilter, function (result) {
        state.salesConditionFilter = result;
        renderSalesPanel();
      });
    });
    document.getElementById("sales-size-filter-trigger").addEventListener("click", function () {
      openSizeModal(state.salesRows, state.salesSizeFilter, renderSalesPanel);
    });
    // Задача користувача (скріншот "Автор"): "додай скрізь фільтр вибірковий"
    // - той самий generic value-modal, тепер і для "Автор" в усіх 4
    // вкладках, де ця колонка є (Продажи/Антисептирование/Списание/Приход).
    document.getElementById("sales-author-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Автор", distinctFieldValues(state.salesRows, "author"), state.salesAuthorFilter, function (result) {
        state.salesAuthorFilter = result;
        renderSalesPanel();
      });
    });
    document.getElementById("writeoff-breed-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Порода", distinctFieldValues(state.writeoffRows, "breed"), state.writeoffBreedFilter, function (result) {
        state.writeoffBreedFilter = result;
        renderWriteoffPanel();
      });
    });
    document.getElementById("writeoff-condition-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Тип", distinctFieldValues(state.writeoffRows, "condition"), state.writeoffConditionFilter, function (result) {
        state.writeoffConditionFilter = result;
        renderWriteoffPanel();
      });
    });
    document.getElementById("writeoff-reason-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Причина", distinctFieldValues(state.writeoffRows, "reason"), state.writeoffReasonFilter, function (result) {
        state.writeoffReasonFilter = result;
        renderWriteoffPanel();
      });
    });
    document.getElementById("writeoff-size-filter-trigger").addEventListener("click", function () {
      openSizeModal(state.writeoffRows, state.writeoffSizeFilter, renderWriteoffPanel);
    });
    document.getElementById("writeoff-author-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Автор", distinctFieldValues(state.writeoffRows, "author"), state.writeoffAuthorFilter, function (result) {
        state.writeoffAuthorFilter = result;
        renderWriteoffPanel();
      });
    });
    document.getElementById("income-date-filter-trigger").addEventListener("click", function () {
      openDateHeaderCalendar(state.incomePeriod, "income-period-chips", renderIncomePanel);
    });
    document.getElementById("income-size-filter-trigger").addEventListener("click", function () {
      openSizeModal(state.incomeRows, state.incomeSizeFilter, renderIncomePanel);
    });
    document.getElementById("income-author-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Автор", distinctFieldValues(state.incomeRows, "author"), state.incomeAuthorFilter, function (result) {
        state.incomeAuthorFilter = result;
        renderIncomePanel();
      });
    });
    // Реальний ризик (аудит коду, 2026-08-14): "Приход" не мала цих трьох
    // фільтрів, хоча найближчий структурний сусід "Списание" вже мав їх -
    // той самий openGenericValueModal-принцип тут.
    document.getElementById("income-product-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Продукт", distinctFieldValues(state.incomeRows, "product"), state.incomeProductFilter, function (result) {
        state.incomeProductFilter = result;
        renderIncomePanel();
      });
    });
    document.getElementById("income-breed-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Порода", distinctFieldValues(state.incomeRows, "breed"), state.incomeBreedFilter, function (result) {
        state.incomeBreedFilter = result;
        renderIncomePanel();
      });
    });
    document.getElementById("income-condition-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Тип", distinctFieldValues(state.incomeRows, "condition"), state.incomeConditionFilter, function (result) {
        state.incomeConditionFilter = result;
        renderIncomePanel();
      });
    });
    document.getElementById("low-stock-product-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Продукт", distinctFieldValues(state.lowStockRows, "product"), state.lowStockProductFilter, function (result) {
        state.lowStockProductFilter = result;
        renderLowStockPanel();
      });
    });
    document.getElementById("low-stock-breed-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Порода", distinctFieldValues(state.lowStockRows, "breed"), state.lowStockBreedFilter, function (result) {
        state.lowStockBreedFilter = result;
        renderLowStockPanel();
      });
    });
    document.getElementById("low-stock-condition-filter-trigger").addEventListener("click", function () {
      openGenericValueModal("Тип", distinctFieldValues(state.lowStockRows, "condition"), state.lowStockConditionFilter, function (result) {
        state.lowStockConditionFilter = result;
        renderLowStockPanel();
      });
    });
    document.getElementById("low-stock-size-filter-trigger").addEventListener("click", function () {
      openSizeModal(state.lowStockRows, state.lowStockSizeFilter, renderLowStockPanel);
    });
    wireSortHeaders("data-lowstock-sort", "lowStockSortKey", "lowStockSortDir", renderLowStockPanel);
  }

  function startBrowser(ctx) {
    if (tg) {
      tg.ready();
      tg.expand();
    }
    applyPageBackground(ctx.style);
    state.rows = ctx.rows || [];
    state.categories = ctx.categories || [];
    // Задача користувача: чипи Продукту по замовчуванню ВИМКНЕНІ (порожній
    // Set - жоден чип не підсвічений) - показуємо ВЕСЬ залишок, поки
    // користувач сам не увімкне конкретні продукти для фільтрації.
    state.activeCategories = new Set();
    state.tabs = ctx.tabs || [];
    state.tabLabels = {};
    TAB_KEYS.forEach(function (key, index) {
      state.tabLabels[key] = state.tabs[index] || key;
    });
    state.salesRows = ctx.sales_rows || [];
    state.antisepticRows = ctx.antiseptic_rows || [];
    state.writeoffRows = ctx.writeoff_rows || [];
    state.incomeRows = ctx.income_rows || [];
    state.movementRows = ctx.movement_rows || [];
    state.lowStockRows = ctx.low_stock_rows || [];
    state.lowStockThreshold = ctx.low_stock_threshold;
    state.canEditLowStockThreshold = !!ctx.can_edit_low_stock_threshold;
    state.canRefresh = ctx.can_refresh !== false;
    updateRefreshButtonVisibility();
    state.salesPeriod = { key: "all", from: null, to: null };
    state.antisepticPeriod = { key: "all", from: null, to: null };
    state.writeoffPeriod = { key: "all", from: null, to: null };
    state.incomePeriod = { key: "all", from: null, to: null };
    state.clientsPeriod = { key: "all", from: null, to: null };
    state.activeTab = "stock";
    pageLoadedOk = true;
    document.getElementById("title").textContent = ctx.title || "Данные";
    wireEvents();
    renderChips();
    renderStockPanel();
    renderPeriodChips("sales-period-chips", state.salesPeriod, renderSalesPanel);
    renderPeriodChips("antiseptic-period-chips", state.antisepticPeriod, renderAntisepticPanel);
    renderPeriodChips("writeoff-period-chips", state.writeoffPeriod, renderWriteoffPanel);
    renderPeriodChips("income-period-chips", state.incomePeriod, renderIncomePanel);
    renderPeriodChips("clients-period-chips", state.clientsPeriod, renderClientsPanel);
    renderSalesPanel();
    renderAntisepticPanel();
    renderWriteoffPanel();
    renderIncomePanel();
    renderMovementPanel();
    renderClientsPanel();
    renderLowStockPanel();
    initLowStockThresholdEdit();
    renderSidebar();
    if (state.activeTab !== "stock" && TAB_PANEL_IDS[state.activeTab]) {
      switchTab(state.activeTab);
    }
  }

  // Задача користувача (2026-08-14): "оновлює дані, але не має скидувати
  // фільтри... НІ ФІЛЬТРИ НІ ВКЛАДКИ, НІЧОГО НЕ МАЄ СКИДАТИСЬ ОКРІМ
  // ОНОВЛЕННЯ ДАНИХ В ТАБЛИЦІ". get_context (main()/startBrowser() нижче)
  // свідомо НЕ підходить для цього - той токен віддає статичний знімок,
  // збережений в пам'яті сервера в момент створення кнопки в Telegram
  // (webapp_server.register_context), а не живий запит до бази. Тому
  // окрема дія "refresh_data_browser" на сервері, яка щоразу читає базу
  // наново. На відміну від startBrowser() - НЕ чіпає activeTab,
  // activeCategories, жоден sortKey/sortDir/sizeFilter/valueFilter, жоден
  // *Period, saved_prefs - лише самі рядки/категорії/пороги.
  function applyFreshCtxData(ctx) {
    state.rows = ctx.rows || [];
    state.categories = ctx.categories || [];
    state.tabs = ctx.tabs || [];
    TAB_KEYS.forEach(function (key, index) {
      state.tabLabels[key] = state.tabs[index] || key;
    });
    state.salesRows = ctx.sales_rows || [];
    state.antisepticRows = ctx.antiseptic_rows || [];
    state.writeoffRows = ctx.writeoff_rows || [];
    state.incomeRows = ctx.income_rows || [];
    state.movementRows = ctx.movement_rows || [];
    state.lowStockRows = ctx.low_stock_rows || [];
    state.lowStockThreshold = ctx.low_stock_threshold;
    state.canEditLowStockThreshold = !!ctx.can_edit_low_stock_threshold;
    state.canRefresh = ctx.can_refresh !== false;
    updateRefreshButtonVisibility();
    renderChips();
    renderStockPanel();
    renderSalesPanel();
    renderAntisepticPanel();
    renderWriteoffPanel();
    renderIncomePanel();
    renderMovementPanel();
    renderClientsPanel();
    renderLowStockPanel();
    initLowStockThresholdEdit();
    renderSidebar();
  }

  // Задача користувача (2026-08-16): другий реальний баг поруч із
  // can_edit_low_stock_threshold (той самий скрін) - refresh_data_browser
  // на сервері вимагає РЕАЛЬНИЙ telegram_id усередині ctx (webapp_server.py
  // шукає роль наново в базі за ним) - десктопний перегляд (client_app.py,
  // "Открыть в браузере") такого telegram_id не має, кнопка завжди
  // повертала б "Ссылка устарела" - хибне, незрозуміле повідомлення. Ховаємо
  // кнопку цілком замість спроби й помилки (can_refresh - той самий прапорець
  // із ctx, що вже й can_edit_low_stock_threshold поруч).
  function updateRefreshButtonVisibility() {
    var button = document.getElementById("refresh-data-button");
    if (button) {
      button.style.display = state.canRefresh ? "" : "none";
    }
  }

  function showRefreshError(message) {
    var hint = document.getElementById("link-copied-hint");
    var originalText = hint.textContent;
    hint.textContent = message;
    hint.classList.add("is-visible", "is-error");
    setTimeout(function () {
      hint.classList.remove("is-visible", "is-error");
      hint.textContent = originalText;
    }, 2500);
  }

  function refreshDataOnly() {
    if (!pageLoadedOk || !state.canRefresh) {
      return;
    }
    var button = document.getElementById("refresh-data-button");
    button.classList.add("is-spinning");
    postTemplateAction({ action: "refresh_data_browser", token: state.contextToken })
      .then(function (data) {
        applyFreshCtxData(data.ctx);
      })
      .catch(function (err) {
        showRefreshError((err && err.message) || "Не удалось обновить данные.");
      })
      .then(function () {
        button.classList.remove("is-spinning");
      });
  }

  function main() {
    var token = new URLSearchParams(window.location.search).get("t");
    if (!token) {
      document.getElementById("title").textContent = "Ошибка загрузки данных";
      return;
    }
    state.contextToken = token;
    postTemplateAction({ action: "get_context", token: token })
      .then(function (data) {
        startBrowser(data.ctx);
      })
      .catch(function () {
        document.getElementById("title").textContent = "Ошибка загрузки данных";
      });
  }

  main();
})();
