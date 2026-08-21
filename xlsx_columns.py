# -*- coding: utf-8 -*-
"""Дописування колонок у .xlsx БЕЗ перезапису книги.

Навіщо окремий модуль, коли є openpyxl
--------------------------------------
Виміряно 2026-08-21 на реальній таблиці користувача: openpyxl.save()
викидає КЕШОВАНІ значення формул. Просте "відкрити й зберегти, нічого не
міняючи" перетворило "Остаток, шт" = 1033 на None. Формула лишається,
число - ні: його рахує Excel, а openpyxl не вміє його зберегти.

Для цієї програми це смертельно, бо вона читає Excel з data_only=True -
тобто бачить САМЕ кешовані числа, а не формули. У книзі користувача ~14 000
формул у 12 листах (СКЛАД 1039, КАССА 3944, АНТИСЕПТИРОВАНИЕ 3496). Одне
збереження через openpyxl знеструмило б їх усі, і програма читала б порожні
залишки, поки людина не відкриє файл в Excel і не збереже його заново.

Тому дописування колонок зроблено правкою самого архіву .xlsx: міняються
рівно ті шматки XML, яких стосується зміна, решта байтів переноситься як є.
Формули, їхні значення, стилі, зведені таблиці, умовне форматування -
недоторкані.

Що саме правиться
-----------------
  xl/worksheets/sheetN.xml   dimension, spans рядків, нові клітинки <c>
  xl/tables/tableN.xml       ref, autoFilter/@ref, tableColumn + count

Нативна таблиця Excel описує свої межі ДВІЧІ (ref і список tableColumns).
Розширити лист, не чіпаючи їх, - колонки опиняться поза таблицею; змінити
ref і забути tableColumns - Excel вважатиме файл пошкодженим. Тут міняється
все узгоджено; саме на цій неузгодженості 2026-08-14 постраждали чотири
нативні таблиці.

Нові заголовки пишуться як inline-рядки (t="inlineStr"), а не через
xl/sharedStrings.xml: інакше довелось би переписувати спільну таблицю
рядків і всі індекси, що на неї посилаються, - зайвий ризик заради тексту
чотирьох заголовків.
"""
import re
import zipfile
from io import BytesIO

_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def column_letter(index):
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_number(value):
    # Excel не любить ані наукову нотацію, ані хвіст із нулів. Ціле пишемо
    # цілим (2112, а не 2112.0), дробове - без зайвих нулів у кінці.
    number = float(value)
    if number == int(number):
        return str(int(number))
    return ("%.6f" % number).rstrip("0").rstrip(".")


def _attribute(element_xml, name):
    match = re.search(r'\b%s="([^"]*)"' % re.escape(name), element_xml)
    return match.group(1) if match else None


def _sheet_part_name(archive, sheet_name):
    # Порядок атрибутів у XML не фіксований: Excel пише Id перед Target,
    # openpyxl - навпаки. Тому кожен елемент розбираємо по атрибутах, а не
    # одним регексом "спершу те, потім це" (на цьому вже спіткнулись:
    # книга, створена openpyxl, не знаходила власного листа).
    workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
    wanted = _escape(sheet_name)
    relation_id = None
    for element in re.findall(r"<sheet\b[^>]*/?>", workbook_xml):
        if _attribute(element, "name") == wanted:
            relation_id = _attribute(element, "r:id")
            break
    if relation_id is None:
        return None
    rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    for element in re.findall(r"<Relationship\b[^>]*/?>", rels_xml):
        if _attribute(element, "Id") != relation_id:
            continue
        target = _attribute(element, "Target")
        if not target:
            return None
        if target.startswith("/"):
            return target.lstrip("/")
        return "xl/" + target.lstrip("./")
    return None


def _table_part_names(archive, sheet_part_name):
    # Лист посилається на свої таблиці через власний файл зв'язків.
    prefix, _slash, base = sheet_part_name.rpartition("/")
    rels_name = "%s/_rels/%s.rels" % (prefix, base)
    if rels_name not in archive.namelist():
        return []
    rels_xml = archive.read(rels_name).decode("utf-8")
    names = []
    for relation in re.findall(r"<Relationship\b[^>]*/?>", rels_xml):
        if "/table" not in relation:
            continue
        target = _attribute(relation, "Target")
        if not target:
            continue
        if target.startswith("/"):
            names.append(target.lstrip("/"))
        elif target.startswith("../"):
            names.append("xl/" + target[3:])
        else:
            names.append("%s/%s" % (prefix, target))
    return names


def _cell_style(row_xml, cell_reference):
    match = re.search(
        r'<c\b[^>]*\br="%s"[^>]*?\bs="(\d+)"' % re.escape(cell_reference), row_xml
    )
    return match.group(1) if match else None


def _build_cells(row_xml, row_number, columns, header_row):
    parts = []
    for column in columns:
        reference = "%s%d" % (column_letter(column["index"]), row_number)
        style = None
        if column.get("style_from_index"):
            style = _cell_style(
                row_xml,
                "%s%d" % (column_letter(column["style_from_index"]), row_number),
            )
        style_attribute = ' s="%s"' % style if style else ""
        if row_number == header_row:
            parts.append(
                '<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>'
                % (reference, style_attribute, _escape(column["header"]))
            )
            continue
        value = column.get("values", {}).get(row_number)
        if value is None:
            continue
        parts.append(
            '<c r="%s"%s><v>%s</v></c>'
            % (reference, style_attribute, _format_number(value))
        )
    return "".join(parts)


def _patch_sheet(sheet_xml, header_row, columns, previous_last_column):
    new_last_column = max(column["index"] for column in columns)
    new_last_letter = column_letter(new_last_column)
    touched_rows = {header_row}
    for column in columns:
        touched_rows.update(column.get("values", {}))

    def replace_dimension(match):
        start, end = match.group(1), match.group(2)
        end_row = re.sub(r"^[A-Z]+", "", end)
        return '<dimension ref="%s:%s%s"/>' % (start, new_last_letter, end_row)

    sheet_xml = re.sub(
        r'<dimension ref="([A-Z]+\d+):([A-Z]+\d+)"/>', replace_dimension, sheet_xml, count=1
    )

    def replace_row(match):
        row_xml = match.group(0)
        row_number = int(match.group(1))
        # spans - підказка Excel про зайняті колонки рядка; лишити стару
        # ширину означало б, що дописані клітинки поза оголошеним діапазоном.
        row_xml = re.sub(
            r'\bspans="(\d+):\d+"',
            lambda m: 'spans="%s:%d"' % (m.group(1), new_last_column),
            row_xml,
            count=1,
        )
        if row_number not in touched_rows:
            return row_xml
        cells = _build_cells(row_xml, row_number, columns, header_row)
        if not cells:
            return row_xml
        if row_xml.endswith("/>"):
            # Порожній рядок (<row .../>) доводиться розгорнути у пару тегів,
            # інакше дописати в нього клітинку нікуди.
            return row_xml[:-2] + ">" + cells + "</row>"
        return row_xml[: -len("</row>")] + cells + "</row>"

    sheet_xml = re.sub(
        r'<row r="(\d+)"[^>]*?(?:/>|>.*?</row>)', replace_row, sheet_xml, flags=re.S
    )

    # Рядка може не бути в XML взагалі - у щойно створеному листі немає
    # жодного <row>, і дописувати заголовки не було б куди (перевірено:
    # порожній лист лишався з однією колонкою). Тоді рядок створюється.
    existing_rows = {int(number) for number in re.findall(r'<row r="(\d+)"', sheet_xml)}
    missing_rows = sorted(row for row in touched_rows if row not in existing_rows)
    if missing_rows:
        if "<sheetData/>" in sheet_xml:
            sheet_xml = sheet_xml.replace("<sheetData/>", "<sheetData></sheetData>", 1)
        for row_number in missing_rows:
            cells = _build_cells("", row_number, columns, header_row)
            if not cells:
                continue
            new_row = '<row r="%d" spans="1:%d">%s</row>' % (
                row_number, new_last_column, cells
            )
            # Рядки в sheetData мусять іти за зростанням номера - інакше
            # Excel вважає файл пошкодженим.
            following = None
            for match in re.finditer(r'<row r="(\d+)"', sheet_xml):
                if int(match.group(1)) > row_number:
                    following = match.start()
                    break
            if following is None:
                sheet_xml = sheet_xml.replace("</sheetData>", new_row + "</sheetData>", 1)
            else:
                sheet_xml = sheet_xml[:following] + new_row + sheet_xml[following:]
    return sheet_xml


def _patch_table(table_xml, previous_last_column, columns):
    new_last_letter = column_letter(max(column["index"] for column in columns))
    ref_match = re.search(r'\bref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', table_xml)
    if ref_match is None:
        return None
    if ref_match.group(3) != column_letter(previous_last_column):
        # Таблиця не сягала краю даних - до дописаних колонок стосунку не
        # має, її межі чіпати не можна.
        return None
    new_ref = "%s%s:%s%s" % (
        ref_match.group(1), ref_match.group(2), new_last_letter, ref_match.group(4)
    )
    old_ref = ref_match.group(0)
    # ref зустрічається і в самої таблиці, і в її autoFilter - обидва мають
    # описувати ОДИН діапазон.
    table_xml = table_xml.replace(old_ref, 'ref="%s"' % new_ref)

    existing_ids = [int(value) for value in re.findall(r'<tableColumn id="(\d+)"', table_xml)]
    next_id = (max(existing_ids) if existing_ids else 0) + 1
    additions = []
    for column in sorted(columns, key=lambda item: item["index"]):
        additions.append(
            '<tableColumn id="%d" name="%s"/>' % (next_id, _escape(column["header"]))
        )
        next_id += 1

    count_match = re.search(r'<tableColumns count="(\d+)">', table_xml)
    if count_match is None:
        return None
    new_count = int(count_match.group(1)) + len(additions)
    table_xml = table_xml.replace(
        count_match.group(0), '<tableColumns count="%d">' % new_count, 1
    )
    return table_xml.replace("</tableColumns>", "".join(additions) + "</tableColumns>", 1)


def append_columns(data, sheet_name, header_row, columns, previous_last_column):
    """Повертає НОВІ байти книги з дописаними колонками.

    columns - список словників:
        index             номер нової колонки (1-based, у порядку зростання)
        header            текст заголовка
        style_from_index  з якої наявної колонки взяти оформлення (щоб
                          дописане виглядало "під формат", а не голим)
        values            {номер рядка: число}

    previous_last_column - остання зайнята колонка ДО дописування: за нею
    впізнається саме та нативна таблиця, яку треба розширити.
    """
    source = zipfile.ZipFile(BytesIO(data))
    try:
        sheet_part = _sheet_part_name(source, sheet_name)
        if sheet_part is None:
            raise ValueError("Лист %r не знайдено у книзі" % sheet_name)
        table_parts = set(_table_part_names(source, sheet_part))

        patched = {}
        sheet_xml = source.read(sheet_part).decode("utf-8")
        patched[sheet_part] = _patch_sheet(
            sheet_xml, header_row, columns, previous_last_column
        ).encode("utf-8")

        for table_part in table_parts:
            table_xml = source.read(table_part).decode("utf-8")
            new_table_xml = _patch_table(table_xml, previous_last_column, columns)
            if new_table_xml is not None:
                patched[table_part] = new_table_xml.encode("utf-8")

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                payload = patched.get(item.filename)
                if payload is None:
                    payload = source.read(item.filename)
                # Зберігаємо власний ZipInfo кожного запису: дата, спосіб
                # стиснення й прапорці лишаються тими самими, що були.
                info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                info.compress_type = item.compress_type
                info.external_attr = item.external_attr
                info.internal_attr = item.internal_attr
                info.create_system = item.create_system
                target.writestr(info, payload)
        return buffer.getvalue()
    finally:
        source.close()
