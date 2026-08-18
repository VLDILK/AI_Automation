"""Публікація й перевірка оновлень client_app.py через GitHub Releases.

Задача користувача (2026-08-16): "готове оновлення, щоб не в момент увімкненого
серверу це було... якщо клієнт вимкнений, а вранці увімкнув - отримав свіже
оновлення". Push-через-тунель (remote_control_client.push_client_update)
вимагає, щоб ОБИДВІ сторони були онлайн одночасно - не підходить для "ліг
спати, вимкнув ПК". OneDrive-посилання теж не підійшло (перевірено наживо,
2026-08-16): сучасний OneDrive Personal не віддає файл без входу в акаунт,
навіть за посиланням "будь-хто може переглядати".

GitHub Releases - публічний, завжди доступний хостинг (сервери GitHub, не
моя чи клієнтська машина): я публікую реліз, коли МЕНІ зручно (потрібен лише
PAT-токен для запису), а client_app.py перевіряє й качає його коли ЙОМУ
зручно - GET /releases/latest і сам файл-asset ПУБЛІЧНІ, без токена, без
входу в жоден акаунт. "Стакання" пропущених версій виходить безкоштовно:
кожен реліз - повний перезбирений пакет (не патч), тож клієнт, що пропустив
10 версій, просто ставить ОСТАННЮ - усі проміжні йому не потрібні.

Тег релізу: "client-v{version}" (напр. "client-v0.2.10") - префікс "client-"
на випадок, якщо колись цей самий репозиторій використовуватиметься і для
gui.py.
"""

import json
import mimetypes
import shutil
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

API_ROOT = "https://api.github.com"
_USER_AGENT = "AI-Automation-Updater"
# Задача користувача (2026-08-16): "щоб домашня программа не заважала
# процесам" - gui.py тепер теж публікує СЕБЕ через цей самий репозиторій
# (VLDILK/AI_Automation), не лише client_app.py. Два різні префікси
# розрізняють, чий це реліз - критично, бо /releases/latest у GitHub
# повертає ОСТАННІЙ реліз РЕПОЗИТОРІЮ загалом, незалежно від префіксу:
# без фільтрації за префіксом публікація gui-v після client-v зробила б
# "останнім" для client_app.py реліз gui.py (і навпаки) - обидві сторони
# перевірки/завантаження тоді просто перестали б бачити СВОЇ реальні
# оновлення. get_latest_release нижче тому працює зі списком релізів
# (не /releases/latest) і фільтрує сама.
CLIENT_TAG_PREFIX = "client-v"
GUI_TAG_PREFIX = "gui-v"

# Задача користувача (2026-08-16): "додай ліміт кешу оновлень. 10. все що
# старе - безповоротно видаляється." - кожен успішний download_*/push_*
# (унікальна підтека updates/ - фікс WinError 5 вище) лишає ПОВНУ копію
# розпакованого клієнта (~60-150МБ) НАЗАВЖДИ - без цього ліміту тека
# зростала б без обмеження на кожному оновленні, скільки б їх не було.
UPDATES_CACHE_LIMIT = 10


def prune_update_cache(updates_dir, limit=UPDATES_CACHE_LIMIT):
    """Лишає лише `limit` найновіших підтек updates/ (download_*/push_*),
    старіші - shutil.rmtree, БЕЗ кошика (той самий принцип, що вже й
    warehouse_data._rotate_db_snapshots/client_app.py._mirror_backup_to_
    onedrive - старе просто зникає, не архівується деінде). Викликається
    ПІСЛЯ успішної розпаковки - найсвіжіша (щойно розпакована) тека сама
    по собі найновіша за mtime, тож ніколи не потрапляє під видалення."""
    updates_dir = Path(updates_dir)
    if not updates_dir.exists():
        return
    folders = sorted(
        (path for path in updates_dir.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    )
    excess = len(folders) - limit
    for old_folder in folders[:max(excess, 0)]:
        shutil.rmtree(old_folder, ignore_errors=True)
    # Нитпік з аудиту коду (2026-08-16): webapp_server.py's push-шлях пише
    # тимчасовий "_push_incoming.zip.tmp" ПРЯМО в updates_dir (не в
    # підтеку) - за звичайних обставин він сам себе прибирає (finally/
    # except уже є на боці webapp_server.py), лишається лише при
    # справжньому крашу процесу посеред запису. Цикл вище рахує тільки
    # теки, тож такий файл нічим не обмежений - підмітаємо його тут теж.
    for stray_file in updates_dir.glob("*.tmp"):
        if stray_file.is_file():
            stray_file.unlink(missing_ok=True)


def _request(url, token=None, method="GET", data=None, extra_headers=None, timeout=30):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Реальний баг (2026-08-17, живий продакшн, "баг з текстом"): 5xx від
        # шлюзу GitHub (502/503/504) повертає HTML-сторінку помилки Fastly,
        # не JSON - сирий <html><body>... дослівно потрапляв у повідомлення
        # користувачу замість людського тексту. Content-Type - надійніший
        # сигнал, ніж сам текст (HTML-сторінка не завжди починається рівно
        # з "<" після урізання), тож перевіряємо обидва.
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        if "html" in content_type.lower() or detail.lstrip().startswith("<"):
            raise RuntimeError(
                f"GitHub временно недоступен (ошибка {exc.code}), попробуйте позже."
            ) from exc
        raise RuntimeError(f"GitHub API {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        # Реальний баг (2026-08-16, живий продакшн, "поперше ніякої
        # Української в програмі, там ніхто її не розуміє"): усі
        # повідомлення нижче - виключно для client_app.py (сирий текст без
        # перекладу, той самий принцип, що й решта повідомлень у ньому) -
        # мають бути РОСІЙСЬКОЮ, як і сам застосунок, незалежно від того,
        # якою мовою написані коментарі в самому коді.
        #
        # Реальна знахідка (аудит коду, 2026-08-16, "це потрібно реально
        # фіксити"): раніше тут ловились лише URLError/OSError - обірваний
        # зв'язок посеред відповіді кидає http.client.IncompleteRead, який
        # НЕ є підкласом жодного з них - виняток пролітав повз увесь
        # ланцюжок обробки прямо в потік воркера, перш ніж _run_on_main_
        # thread(...) встигав спрацювати. Кнопка "Загрузка обновления..."
        # лишалась активною НАЗАВЖДИ (жоден код після цього виклику вже не
        # виконувався), а автоматична періодична перевірка теж помирала
        # мовчки (той самий клас "таймер має ЗАВЖДИ переплановуватись", що
        # сьогодні вже фіксили - тут пролом був не в guard'і, а в самому
        # необробленому винятку РАНІШЕ за той guard). except Exception -
        # свідомо широкий: мережеві й парсинг-помилки непередбачувані за
        # своєю природою, і ЄДИНИЙ контракт, який усі виклики цієї функції
        # вже й так очікують - "будь-яка проблема стає RuntimeError".
        raise RuntimeError(f"Не удалось соединиться с GitHub: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8")) if body else None
    except Exception as exc:
        raise RuntimeError(f"GitHub вернул повреждённый ответ: {exc}") from exc


# ---------- Перевірка/завантаження (публічне, БЕЗ токена - викликає client_app.py) ----------

def get_latest_release(owner, repo, tag_prefix, timeout=15):
    """Список релізів (НЕ /releases/latest - див. коментар над
    CLIENT_TAG_PREFIX вище про чому) - повертає найновіший, чий тег
    підходить під tag_prefix. None, якщо релізів такого типу ще немає
    (порожній репозиторій АБО є лише релізи іншої программи) - той самий
    контракт, що й update_check.check_for_update: "оновлень немає" і "ще
    не налаштовано" виглядають однаково для викликача.

    Реальна знахідка (2026-08-16, живий продакшн): "НЕ довіряти теоретичному
    багу без відтворення" спрацювало і тут навпаки - спершу здавалось, що
    /releases просто повільно "наздоганяє" щойно опублікований реліз
    (той самий клас lag, що вже й у _request), але після 30+ секунд
    очікування те саме прямим запитом підтвердило: реліз ПОВНІСТЮ готовий
    (є id, published_at, asset), просто НЕ на початку списку. Причина -
    цей репозиторій НІКОЛИ не отримує нових source-комітів (лише релізи-
    біти), тож усі релізи діляться ОДНИМ і тим самим created_at (тег без
    target_commitish вказує на єдиний існуючий коміт) - GitHub сортує
    /releases за created_at, і при повному збігу порядок серед "рівних"
    ненадійний і довго не "вирівнюється". Це СТРУКТУРНА властивість САМЕ
    цього репозиторію (жодного нового коміта ніколи не буде), тож сортуємо
    самі - за published_at (реальний момент публікації релізу, завжди
    унікальний), а не покладаємось на порядок відповіді GitHub.

    Реальна знахідка (аудит коду, 2026-08-16): раніше тягнувся лише ОДИН
    per_page=30 запит - на момент аудиту репозиторій уже мав 33 релізи
    (10 gui + 23 client, обидва префікси в ОДНОМУ спільному списку) -
    перша сторінка вже не вміщала все, і збіг обставин лише випадково
    рятував правильну відповідь (найстаріші релізи впали на сторінку 2).
    При достатньо нерівномірній частоті публікацій (напр. багато client-
    релізів підряд без жодного нового gui) усі gui-релізи могли б повністю
    "випасти" за межі однієї сторінки - функція мовчки повернула б None
    ("оновлень немає"), хоча вони є. Тепер - повна пагінація (усі сторінки,
    поки GitHub не поверне порожню) перед фільтрацією/сортуванням."""
    releases = []
    page = 1
    while True:
        try:
            page_releases = _request(
                f"{API_ROOT}/repos/{owner}/{repo}/releases?per_page=100&page={page}", timeout=timeout,
            )
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        if not isinstance(page_releases, list) or not page_releases:
            break
        releases.extend(page_releases)
        if len(page_releases) < 100:
            break
        page += 1
    matching = [r for r in releases if r.get("tag_name", "").startswith(tag_prefix)]
    if not matching:
        return None
    return max(matching, key=lambda r: r.get("published_at") or "")


def list_recent_releases(owner, repo, limit=15, timeout=15):
    """Задача користувача (2026-08-19): "журнал оновлень... до кожної
    версії буде прикріплено такий файл з даними. чи просто дані" - окреме
    сховище не потрібне: compose_release_notes() (gui.py) вже пише повні
    нотатки (коміти + очищений diff) у ТІЛО кожного релізу під час
    публікації - GitHub Releases сам є архівом. Ця функція лише читає
    його назад: повна пагінація (та сама причина, що й get_latest_release
    вище - один per_page=30/100 запит не покриє репозиторій із десятками
    релізів), фільтр на ОБИДВА відомі префікси (ігнорує чужі/ручні
    релізи), сортування за published_at (не порядком відповіді GitHub -
    та сама причина ненадійного сортування, що описана в get_latest_
    release), обрізка до `limit` найновіших.

    Повертає список словників {kind, version, tag_name, published_at,
    notes}, найновіші перші. Публічний GET, токен не потрібен."""
    releases = []
    page = 1
    while True:
        try:
            page_releases = _request(
                f"{API_ROOT}/repos/{owner}/{repo}/releases?per_page=100&page={page}", timeout=timeout,
            )
        except RuntimeError as exc:
            if "404" in str(exc):
                return []
            raise
        if not isinstance(page_releases, list) or not page_releases:
            break
        releases.extend(page_releases)
        if len(page_releases) < 100:
            break
        page += 1

    entries = []
    for release in releases:
        tag = release.get("tag_name", "")
        if tag.startswith(GUI_TAG_PREFIX):
            kind, prefix = "gui", GUI_TAG_PREFIX
        elif tag.startswith(CLIENT_TAG_PREFIX):
            kind, prefix = "client", CLIENT_TAG_PREFIX
        else:
            continue
        entries.append(
            {
                "kind": kind,
                "version": tag[len(prefix):],
                "tag_name": tag,
                "published_at": release.get("published_at") or "",
                "notes": release.get("body") or "",
            }
        )
    entries.sort(key=lambda e: e["published_at"], reverse=True)
    return entries[:limit]


def release_version(release, tag_prefix):
    """"client-v0.2.10" -> "0.2.10". None, якщо тег не має очікуваного префіксу
    (напр. хтось вручну створив реліз без коду - краще проігнорувати, ніж
    впасти на порівнянні версій)."""
    tag = (release or {}).get("tag_name", "")
    if not tag.startswith(tag_prefix):
        return None
    return tag[len(tag_prefix):]


def find_asset_url(release, name_suffix=".zip"):
    for asset in (release or {}).get("assets", []):
        if asset.get("name", "").endswith(name_suffix):
            return asset.get("browser_download_url")
    return None


def download_asset(url, destination_path, timeout=300, on_progress=None):
    """Стрімом, без токена - той самий publicly-fetchable browser_download_url,
    що працює в будь-якому браузері без входу.

    on_progress(fraction: float) - опційний колбек для смужки прогресу в
    client_app.py (задача користувача, 2026-08-16: "покажи прогрес
    завантаження полоскою"). Викликається лише коли сервер віддав
    Content-Length (завжди так для GitHub release-assets) - без нього
    справжній відсоток порахувати неможливо, тож просто мовчки нічого не
    повідомляємо, а не вигадуємо фальшиве значення."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = response.headers.get("Content-Length")
            total = int(total) if total else None
            downloaded = 0
            with open(destination_path, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total:
                        on_progress(downloaded / total)
    except Exception as exc:
        # Той самий ширший except, що й у _request вище - будь-яка мережева
        # помилка стає RuntimeError, а не пролітає повз обробку необробленою.
        raise RuntimeError(f"Не удалось загрузить файл обновления: {exc}") from exc
    # Реальна знахідка (аудит коду, 2026-08-16, "це потрібно реально
    # фіксити" - перевірено НАЖИВО справжнім обірваним з'єднанням, а не
    # припущенням): response.read(1024*1024) у циклі, коли зв'язок
    # обривається посеред передачі, НЕ кидає http.client.IncompleteRead -
    # наступний read() просто повертає порожній chunk, цикл виходить
    # НОРМАЛЬНО, exception взагалі не виникає. Без цієї перевірки файл
    # мовчки лишався б обрізаним (напр. 1КБ замість 60-150МБ) - помилка
    # спливла б лише пізніше, на розпакуванні zip (BadZipFile), незрозуміло
    # для користувача, чому саме. Порівнюємо факт із обіцянкою (Content-
    # Length) одразу після завантаження - чіткіше повідомлення в точці
    # реальної причини.
    if total is not None and downloaded < total:
        raise RuntimeError(
            f"Загрузка прервана: получено {downloaded} из {total} байт. "
            "Проверьте интернет-соединение и попробуйте снова."
        )


def download_and_extract_release(release, updates_dir, target_name="AI_Automation_Client", on_progress=None):
    """Завантажує .zip-asset релізу й розпаковує в УНІКАЛЬНУ підтеку
    updates_dir - той самий кінцевий вигляд (тека з {target_name}.exe
    всередині), що вже й _handle_push_update_upload (webapp_server.py)
    виробляє для _install_downloaded_update. Повертає Path до розпакованої
    теки.

    Реальний баг (2026-08-16, живий продакшн, "WinError 5 Access is
    denied"): раніше розпаковувалось ЗАВЖДИ в те саме ім'я (updates_dir/
    target_name), з rmtree() старого вмісту ПЕРЕД розпакуванням - якщо
    попередня спроба лишила там заблокований файл (той самий клас бага, що
    вже виправлений у webapp_server.py._handle_push_update_upload, тут
    просто забутий), КОЖНЕ наступне завантаження падало назавжди без
    жодного самостійного відновлення. Кожен виклик тепер розпаковує у
    ВЛАСНУ, унікальну підтеку - видаляти чужий попередній вміст більше не
    треба, конфлікт структурно неможливий."""
    asset_url = find_asset_url(release)
    if not asset_url:
        raise RuntimeError("В релизе не найден .zip-файл с обновлением.")
    updates_dir = Path(updates_dir)
    updates_dir.mkdir(parents=True, exist_ok=True)
    extraction_root = updates_dir / f"download_{uuid.uuid4().hex[:8]}"
    extraction_root.mkdir(parents=True, exist_ok=True)
    tmp_zip_path = extraction_root / "_github_release_download.zip.tmp"
    download_asset(asset_url, tmp_zip_path, on_progress=on_progress)
    target_dir = extraction_root / target_name
    try:
        with zipfile.ZipFile(tmp_zip_path) as archive:
            archive.extractall(extraction_root)
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(extraction_root, ignore_errors=True)
        raise RuntimeError(f"Не удалось распаковать обновление: {exc}") from exc
    finally:
        tmp_zip_path.unlink(missing_ok=True)
    if not (target_dir / f"{target_name}.exe").exists():
        shutil.rmtree(extraction_root, ignore_errors=True)
        raise RuntimeError("В распакованном пакете не найден .exe.")
    prune_update_cache(updates_dir)
    return target_dir


# ---------- Публікація (потребує PAT-токена - викликає лише gui.py) ----------

def create_release(token, owner, repo, tag_prefix, version, notes="", timeout=30):
    tag = f"{tag_prefix}{version}"
    payload = json.dumps({
        "tag_name": tag, "name": tag, "body": notes, "draft": False, "prerelease": False,
    }).encode("utf-8")
    return _request(
        f"{API_ROOT}/repos/{owner}/{repo}/releases", token=token, method="POST", data=payload,
        extra_headers={"Content-Type": "application/json"}, timeout=timeout,
    )


def upload_release_asset(token, upload_url, file_path, timeout=600):
    # upload_url з create_release() - шаблон RFC 6570
    # ("...assets{?name,label}") - фігурні дужки з параметрами прибираються,
    # ім'я файлу підставляється напряму в query string.
    base_url = upload_url.split("{")[0]
    file_path = Path(file_path)
    content_type, _ = mimetypes.guess_type(str(file_path))
    with open(file_path, "rb") as handle:
        data = handle.read()
    return _request(
        f"{base_url}?name={file_path.name}", token=token, method="POST", data=data,
        extra_headers={"Content-Type": content_type or "application/octet-stream"}, timeout=timeout,
    )


def publish_release(token, owner, repo, tag_prefix, version, zip_path, notes=""):
    """Один виклик: створити реліз + завантажити .zip. Спільна реалізація
    для publish_client_release/publish_gui_release нижче - розрізняються
    лише префіксом тегу."""
    release = create_release(token, owner, repo, tag_prefix, version, notes=notes)
    upload_url = release.get("upload_url")
    if not upload_url:
        raise RuntimeError("GitHub не вернул ссылку для загрузки файла.")
    upload_release_asset(token, upload_url, zip_path)
    return release


def publish_client_release(token, owner, repo, version, zip_path, notes=""):
    """Те, що реально викликає gui.py при публікації оновлення client_app.py."""
    return publish_release(token, owner, repo, CLIENT_TAG_PREFIX, version, zip_path, notes=notes)


# Задача користувача (2026-08-16): "стосовно домашньої версії, щоб вона не
# заважала процесам" - gui.py тепер публікує й ОНОВЛЮЄ САМУ СЕБЕ тим самим
# шляхом, що вже перевірений на client_app.py - жодного ручного закриття/
# перезбирання dist/AI_Automation_Home більше не потрібно для доставки
# оновлення: публікація йде зі свіжозібраної release/AI_Automation_Home/
# (окрема від dist/, тому не блокується запущеним .exe), а встановлення -
# той самий перевірений .bat-механізм (_install_downloaded_update), що вже
# є в gui.py.
def publish_gui_release(token, owner, repo, version, zip_path, notes=""):
    """Те, що реально викликає gui.py при публікації оновлення для самої себе."""
    return publish_release(token, owner, repo, GUI_TAG_PREFIX, version, zip_path, notes=notes)
