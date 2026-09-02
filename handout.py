# -*- coding: utf-8 -*-
u"""Выдача плана креаторам: их таблица, которую они читают и в которой отмечают.

    python handout.py --push data/план_партии_1.tsv   залить план в таблицу креаторов
    python handout.py --pull                          забрать отметки «снято»
    python handout.py --show data/план_партии_1.tsv   показать, что уйдет людям
    python handout.py --selftest

🔴 **Почему таблица, а не карточки в Telegram.** Прямой ответ Спартака 31.08:
план от Анны приходил в Google-таблице, отмечать снятое было негде, и отмечать
в таблице ему удобно. Плюс его ритм: план на следующую неделю - к концу текущей,
съемка на выходных.

🔴 **Таблица ОТДЕЛЬНАЯ от рабочей.** Google дает доступ на весь файл: пустив
креаторов в рабочую, мы отдадим им СДАЧИ, МЕТРИКИ, СЛОВАРЬ и НАСТРОЙКИ. Скрытый
лист не спасает, его видно снятием скрытия. Id - в `.env`, ключ `CREATORS_SHEET_ID`.

🔴 **Служебные колонки сюда не переезжают.** Гипотеза, факт-источник и ожидаемый
сигнал - наши. Креатор, знающий, какого результата мы ждем, начинает подыгрывать,
и замер перестает мерить то, ради чего затевался (то же правило в `tools/cards.py`).
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
GOOGLE_EPOCH = datetime.date(1899, 12, 30)

# 🔴 Нейтральное имя (02.09): лист живет дольше любой партии, а «ПАРТИЯ 1»
# при плане W36 читалось как чужой номер.
ЛИСТ = u"ПЛАН НЕДЕЛИ"

# Колонки таблицы креаторов.
# 🔴 «Снято» стоит ВТОРОЙ, а не в конце. Замер глазами 31.08: в конце она уехала
# за край экрана, и чтобы отметить, надо прокрутить вправо мимо сцены в 700
# знаков. Ровно на это Спартак и жаловался - «отмечать вроде нельзя».
# 🔴 Одна дата вместо двух (требование владельца 02.09): «нужно чтобы была
# указана дата, когда этот ролик креатор должен прислать. Все, остальное нас
# не волнует, когда он это снимет». Дата выхода - наша кухня: креатору она
# ничего не говорит, а две даты рядом заставляют выбирать, какая главная.
КОЛОНКИ = [u"№", u"Снято", u"Прислать до", u"Кто снимает", u"Товар",
           u"Кто в кадре", u"Что снимаем", u"Первые 3 секунды",
           u"Надпись на экране", u"Можно менять",
           u"Комментарий"]

# Колонки, которые заполняет креатор. Перезаливка их не трогает.
ЕГО = [u"Снято", u"Комментарий"]

# 🔴 Колонки, которые были нашими и ушли из схемы. Без этого списка живой лист
# со старым заголовком считается ЧУЖИМ, и заливка останавливается со словами
# «проверьте CREATORS_SHEET_ID» - то есть правка схемы ломает выдачу на ровном
# месте. Найдено при переходе на одну дату 02.09.
# «Чего не делать» убрана решением владельца 02.09: «не нужно вообще это
# контролировать». Осталась в устаревших, чтобы живой лист с ней мигрировал.
УСТАРЕВШИЕ = [u"Дата выхода", u"Снять до", u"Чего не делать"]


ФОРМА = (u"https://docs.google.com/forms/d/e/"
         u"1FAIpQLSfsng0DaZQnI9w8RIteBRr6q_zt_6pVw9epQTu1RcmjKQvNRw/viewform")

# Лист с правилами. 🔴 Заведен 02.09 после ролевого прогона: в таблице негде
# было узнать, куда сдавать готовый ролик. Человек снял бы и не понял, что дальше.
ЛИСТ_ПАМЯТКИ = u"КАК СДАВАТЬ"


def памятка():
    u"""Блоки памятки: (стиль, текст). Стиль решает, как ячейка выглядит.

    🔴 Живет в коде, а не набирается в таблице руками: таблица пересобирается
    под каждую партию, и набранное мышкой пропадет молча. Требования к файлу -
    из `управление/ПОЛЯ_ПУБЛИКАЦИИ.md` §5 (дока сервиса и площадок).

    🔴 Блоки, а не строки (02.09, замечание владельца на первую версию: «нет
    никакого форматирования, все просто вставлено в ячейки»). Текст пункта идет
    ОДНОЙ ячейкой с переносом - разрезанный на строки, он читается обрывками.
    """
    return [
        (u"h1", u"Как снимать и как сдавать"),
        (u"lead", u"Все, что нужно знать помимо самой строки задания. "
                  u"Задание - на соседней вкладке «ПЛАН НЕДЕЛИ»."),

        (u"h2", u"1. Где ваши ролики"),
        (u"text", u"Каждая строка на листе «ПЛАН НЕДЕЛИ» - один ролик. "
                  u"Ваши строки помечены вашим именем в колонке «Кто снимает». "
                  u"Номер из первой колонки «№» (например P1-01) понадобится "
                  u"при сдаче - по нему ролик находит свое задание."),

        (u"h2", u"2. К какому сроку"),
        (u"text", u"Готовый ролик должен быть у нас до даты в колонке «Прислать до». "
                  u"Когда именно вы его снимете - ваше дело. План на следующую "
                  u"неделю появляется здесь к концу текущей, чтобы можно было "
                  u"снять все за выходные."),

        (u"h2", u"3. Как отметить, что сняли"),
        (u"text", u"Поставьте галочку «Снято» - она во второй колонке, сразу "
                  u"после номера. По ней мы видим, что готово, и не дергаем "
                  u"лишний раз."),

        (u"h2", u"4. Куда присылать готовый ролик"),
        (u"text", u"Через форму - файлом. Не в чат: из чата видео приходит "
                  u"пересжатым, и это видно в эфире."),
        (u"link", ФОРМА),
        (u"warn", u"В форме обязательно поставьте галочку «Указать в моем ответе "
                  u"адрес электронной почты». Без нее форма не отправляется, "
                  u"и ролик до нас не дойдет."),
        (u"text", u"В форме выберите номер строки из таблицы (например P1-01) - "
                  u"по нему ролик связывается с заданием."),

        (u"h2", u"5. Требования к файлу"),
        (u"list", u"Вертикальное видео 9:16, до 1080×1920"),
        (u"list", u"MP4, размер до 300 МБ"),
        (u"list", u"Длина от 3 секунд, лучше до 60"),
        (u"list", u"Оригинал из редактора, а не пересланный через мессенджер"),

        (u"h2", u"6. Если строка не подходит"),
        (u"text", u"Напишите в группу ДО съемки - поменяем. Снимать «как "
                  u"получится» нельзя: по этой строке считается результат, "
                  u"и подмена его портит."),

        (u"h2", u"7. Если отступили по ходу съемки"),
        (u"text", u"Напишите об этом в колонке «Комментарий». Это не претензия, "
                  u"а важная для нас информация: без нее мы посчитаем результат "
                  u"не за то, что вы сняли."),

        (u"h2", u"Что означают колонки задания"),
        (u"list", u"«Что снимаем» - сцена: что происходит в кадре и чем кончается"),
        (u"list", u"«Первые 3 секунды» - что зритель видит, пока не пролистнул. "
                  u"Это смысл, а не реплика: его надо показать действием"),
        (u"list", u"«Надпись на экране» - текст, который набирается в редакторе "
                  u"поверх видео"),
        (u"list", u"«Можно менять» - что остается на ваше усмотрение в этой сцене"),
    ]


def deadline(дата_эфира, сегодня=None):
    u"""Срок присылки: канун эфира, у каждого ролика своя дата.

    🔴 Решение владельца 02.09 поздним вечером: «пиши нормально на каждый
    день - он снимет в своем темпе, а присылать будет каждый день». Прежнее
    правило «все к воскресенью перед неделей» собирало 8 роликов на одну
    дату 06.09 и отменено. Параметр «сегодня» оставлен для совместимости
    вызовов, дате он больше не нужен.
    """
    return дата_эфира - datetime.timedelta(days=1)


def as_date(value):
    """Дата из таблицы или файла. Единый разбор - lib/dates (аудит 02.09)."""
    from lib import dates
    return dates.as_date(value)


def _кто_в_кадре(строка):
    части = [(строка.get(u"Взрослый в кадре") or u"").strip(),
             (строка.get(u"Ребенок в кадре") or u"").strip()]
    return u" + ".join(p for p in части if p)


def handout_rows(план, сегодня=None, подсказки=None):
    сегодня = сегодня or datetime.date.today()
    u"""То, что увидит креатор. Служебные колонки не переносятся принципиально.

    `подсказки` - {ID: (список свобод, список запретов)}, считает `tools/cards.py`
    из тех же справочников, по которым запрет проверяет `grounding.py`. Своего
    расчета тут нет намеренно: вторая копия разойдется на первой правке.
    """
    подсказки = подсказки or {}
    out = []
    for строка in план:
        # 🔴 Аудит 02.09: черновик не уезжает креаторам - он не утвержден.
        if u"ЧЕРНОВИК" in str(строка.get(u"Статус") or u"").upper():
            continue
        эфир = as_date(строка.get(u"Дата в эфир"))
        # запреты (второй элемент пары) больше не едут: колонка «Чего не
        # делать» убрана владельцем 02.09, формат подсказок оставлен парным -
        # его считает общий с карточками код
        свобода, _запреты = подсказки.get(строка.get(u"ID"), ([], []))
        out.append({
            u"Можно менять": u" · ".join(свобода),
            u"№": строка.get(u"ID", u""),
            # 🔴 Дата уезжает целиком, с годом. «04.09» Google принимает за дату
            # текущего года и показывает креатору «46269» (найдено глазами 31.08).
            # Короткий вид с днем недели делает формат ячейки, а не мы.
            u"Прислать до": (deadline(эфир, сегодня).strftime(u"%d.%m.%Y")
                             if эфир else u""),
            u"Кто снимает": строка.get(u"Креатор", u""),
            u"Товар": строка.get(u"Товар", u""),
            u"Кто в кадре": _кто_в_кадре(строка),
            u"Что снимаем": строка.get(u"Что в кадре", u""),
            u"Первые 3 секунды": строка.get(u"Хук", u""),
            u"Надпись на экране": строка.get(u"Текст на экране", u""),
            u"Снято": u"",
            u"Комментарий": u"",
        })
    return out


def overdue(план, сегодня=None):
    u"""ID строк, чей срок присылки уже прошел на момент выдачи.

    С 02.09 срок - канун эфира (deadline), и просрочка - это «канун позади»:
    в сам день срока строка еще не просрочена. Об опоздавших надо сказать
    вслух при выдаче: дата в ячейке выглядит как дата, глазом не ловится.
    """
    сегодня = сегодня or datetime.date.today()
    беда = []
    for строка in план:
        эфир = as_date(строка.get(u"Дата в эфир"))
        if эфир is None:
            continue
        if deadline(эфир) < сегодня:
            беда.append(строка.get(u"ID"))
    return беда


def push(лист, строки):
    u"""Переписывает задание, сохраняя то, что креатор уже отметил.

    🔴 Отметка «снято» живет в его таблице и обязана пережить перезаливку:
    иначе правка одной запятой в плане заставит человека снимать второй раз.
    """
    заголовок = list(getattr(лист, "header", None) or [])
    # 🔴 Колонка, которой в нашей схеме нет, - признак чужого листа. Переписать
    # его заголовок значит стереть чужую работу, поэтому останавливаемся.
    чужие = [k for k in заголовок if k not in КОЛОНКИ and k not in УСТАРЕВШИЕ]
    if чужие:
        raise SystemExit(
            u"в листе есть посторонние колонки: %s. Это не наш лист - "
            u"проверьте CREATORS_SHEET_ID" % u", ".join(чужие))
    было = dict((r.get(u"№"), r) for r in лист.read() if r.get(u"№"))
    # 🔴 Заголовок живого листа догоняет код ПОСЛЕ чтения отметок: поменяли
    # состав или порядок колонок - в таблице остался старый, и правка не дошла
    # до человека. Тот же класс, что docx, разошедшийся с `.md`.
    if заголовок != КОЛОНКИ and hasattr(лист, "set_header"):
        лист.set_header(КОЛОНКИ)
    лист.clear(keep_header=True)
    for строка in строки:
        готово = dict(строка)
        старое = было.get(строка[u"№"])
        if старое:
            for кол in ЕГО:
                готово[кол] = старое.get(кол, u"") or u""
        лист.append(готово)
    return len(строки)


def новые_номера(лист_строки, выдача):
    u"""Номера строк выдачи, которых еще не было в листе.

    🔴 Решение владельца 03.09: «отправку контент-плана должна делать сама
    система, в общий чат креаторов». Анонс уходит при появлении НОВЫХ строк:
    технический перепуш тех же номеров группу не спамит.
    """
    были = set((r.get(u"№") or u"").strip() for r in лист_строки)
    return [r[u"№"] for r in выдача if r.get(u"№") and r[u"№"] not in были]


def announce_text(новые, выдача, url):
    u"""Текст анонса в группу: что появилось, первый срок, где лежит."""
    по_номеру = dict((r.get(u"№"), r) for r in выдача)
    сроки = {}
    for n in новые:
        срок = (по_номеру.get(n, {}).get(u"Прислать до") or u"")[:10]
        if срок:
            сроки.setdefault(срок, []).append(n)
    строки = [u"📋 Новые задания в таблице контент-плана: %s"
              % (u"%s - %s" % (новые[0], новые[-1]) if len(новые) > 2
                 else u", ".join(новые)),
              url]
    if сроки:
        # «Прислать до» уже в человеческом виде дд.мм.гггг; порядок - по дате
        первый = sorted(сроки, key=lambda t: as_date(t) or datetime.date.max)[0]
        кто = u", ".join(u"%s (%s)" % (n, по_номеру[n].get(u"Кто снимает", u""))
                         for n in сроки[первый])
        строки.append(u"Первый срок сдачи: %s - %s" % (первый[:5], кто))
    строки.append(u"Как снимать и куда сдавать - вкладка «КАК СДАВАТЬ».")
    return u"\n".join(строки)


ПУСТО = (u"", u"false", u"нет", u"0")


def pull(лист):
    u"""Что креатор отметил: {ID: комментарий}.

    🔴 «Снято» - галочка, и снятую Google отдает строкой «FALSE». Наивная
    проверка на непустоту засчитала бы ее как снятое: партия выглядела бы
    отснятой целиком в первый же день, ни разу не пикнув.
    """
    out = {}
    for строка in лист.read():
        отметка = (строка.get(u"Снято") or u"").strip()
        if отметка.lower() in ПУСТО:
            continue
        out[строка.get(u"№")] = (строка.get(u"Комментарий") or u"").strip()
    return out


# ---------------------------------------------------------------- живая часть

def read_env():
    env = {}
    путь = ROOT / ".env"
    if путь.exists():
        for line in путь.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def make_book():
    from lib import google_auth, sheets
    env = read_env()
    os.environ.update(env)
    sid = env.get("CREATORS_SHEET_ID") or os.environ.get("CREATORS_SHEET_ID")
    if not sid:
        raise SystemExit(u"нет CREATORS_SHEET_ID в .env - таблица креаторов не задана")
    return sheets.Book(google_auth.ServiceAccount.load(), sid)


def make_sheet(book=None):
    book = book or make_book()
    book.add(ЛИСТ)
    лист = book.sheet(ЛИСТ)
    _ensure_header(лист)
    return лист


def _ensure_header(лист):
    u"""Ставит заголовки, если их нет. Заполненную строку не трогает."""
    from lib import sheets
    строки = лист._get(лист.title)
    if строки and any(str(c).strip() for c in строки[0]):
        лист.header = [str(c).strip() for c in строки[0]]
        return False
    диапазон = u"%s!A1:%s1" % (лист.title, sheets.a1_column(len(КОЛОНКИ) - 1))
    лист._put(диапазон, [КОЛОНКИ])
    лист.header = list(КОЛОНКИ)
    return True


# Ширина колонок в пикселях. 🔴 Оформление живет в коде, а не в руках: таблица
# пересобирается при новой партии, и настроенное мышкой пропадет молча.
# 🔴 Ширин ровно столько, сколько КОЛОНОК: лишняя (хвост убранной «Чего не
# делать») сдвигала оформление правым колонкам (аудит 02.09).
ШИРИНА = [55, 70, 110, 95, 130, 100, 400, 240, 210, 170, 240]
ФОРМАТ_ДАТЫ = u"dd.MM ddd"     # «04.09 пт» - день недели креатору нужнее года


def format_sheet(лист, строк=0):
    u"""Приводит лист к виду, который человек может читать.

    🔴 Найдено глазами 31.08 на живой таблице: без этого даты показываются
    числами Google («46269»), а сцена в 700 знаков обрезана шириной колонки.
    Значения при этом верные - дефект видно только глазами.
    """
    import json
    from lib import google_auth, http
    sa = лист.sa
    sid = лист.sid
    h = sa.headers([google_auth.SCOPE_SHEETS])
    h["Content-Type"] = "application/json"
    meta = http.request("https://sheets.googleapis.com/v4/spreadsheets/" + sid, headers=h)
    номера = dict((s["properties"]["title"], s["properties"]["sheetId"])
                  for s in meta["sheets"])
    if лист.title not in номера:
        raise SystemExit(u"нет листа «%s»" % лист.title)
    n = номера[лист.title]
    столбец = dict((имя, i) for i, имя in enumerate(КОЛОНКИ))
    запросы = [
        # 🔴 Шапка закреплена И первые две колонки: сцена в 700 знаков уводит
        # вправо, и без закрепления человек теряет, к какой строке смотрит.
        {"updateSheetProperties": {
            "properties": {"sheetId": n, "gridProperties": {
                "frozenRowCount": 1, "frozenColumnCount": 2}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        # 🔴 Тело таблицы - ОБЫЧНЫМ начертанием. Замечание владельца 02.09: было
        # жирным целиком, и глазу не за что зацепиться - выделено все, значит ничего.
        {"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "wrapStrategy": "WRAP", "verticalAlignment": "TOP",
                "textFormat": {"bold": False, "fontSize": 10,
                               "foregroundColor": _цвет("202124")},
                "backgroundColor": _цвет("ffffff"),
                "padding": {"left": 8, "right": 8, "top": 6, "bottom": 6}}},
            "fields": "userEnteredFormat"}},
        # шапка: темная плашка, белый текст, по центру
        {"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 10,
                               "foregroundColor": _цвет("ffffff")},
                "backgroundColor": _цвет(ТЕМНЫЙ),
                "verticalAlignment": "MIDDLE",
                "horizontalAlignment": "LEFT",
                "wrapStrategy": "WRAP",
                "padding": {"left": 8, "right": 8}}},
            "fields": "userEnteredFormat"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": n, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 40}, "fields": "pixelSize"}},
        # зебра: строку легче вести глазом на широкой таблице
        {"addBanding": {"bandedRange": {
            "range": {"sheetId": n, "startRowIndex": 1,
                      "endRowIndex": max(строк + 1, 2),
                      "startColumnIndex": 0, "endColumnIndex": len(КОЛОНКИ)},
            "rowProperties": {"firstBandColor": _цвет("ffffff"),
                              "secondBandColor": _цвет(СВЕТЛЫЙ)}}}},
        # номер строки - единственное, что выделено в теле: по нему сдают ролик
        {"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 10,
                               "foregroundColor": _цвет(ТЕМНЫЙ)},
                "verticalAlignment": "TOP", "padding": {"left": 8, "top": 6}}},
            "fields": "userEnteredFormat(textFormat,verticalAlignment,padding)"}},
        # галочка и даты - по центру колонки
        {"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 1,
                      "startColumnIndex": 1, "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}},
        # 🔴 Галочки ставятся ТОЛЬКО на строки с заданием. На весь столбец -
        # и под планом висит хвост пустых квадратиков до конца листа: человек
        # не понимает, где кончается его работа (найдено глазами 31.08).
        {"setDataValidation": {
            "range": {"sheetId": n, "startRowIndex": 1,
                      "endRowIndex": max(строк + 1, 2),
                      "startColumnIndex": столбец[u"Снято"],
                      "endColumnIndex": столбец[u"Снято"] + 1},
            "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}}},
        # ниже последней строки задания валидации быть не должно
        {"setDataValidation": {
            "range": {"sheetId": n, "startRowIndex": max(строк + 1, 2),
                      "startColumnIndex": столбец[u"Снято"],
                      "endColumnIndex": столбец[u"Снято"] + 1}}},
    ]
    for имя in (u"Прислать до",):
        запросы.append({"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 1,
                      "startColumnIndex": столбец[имя], "endColumnIndex": столбец[имя] + 1},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "DATE", "pattern": ФОРМАТ_ДАТЫ},
                "horizontalAlignment": "LEFT"}},
            "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"}})
    for i, ширина in enumerate(ШИРИНА):
        запросы.append({"updateDimensionProperties": {
            "range": {"sheetId": n, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": ширина}, "fields": "pixelSize"}})
    # 🔴 Высота строк - автоматом под текст, и ПОСЛЕДНИМ запросом: ширины колонок
    # должны быть уже выставлены, иначе Google посчитает высоту по старой ширине.
    # Без этого сцена в 700 знаков обрезается на середине фразы (найдено глазами
    # 02.09) - и человек не знает, что видит не все.
    if строк:
        запросы.append({"autoResizeDimensions": {"dimensions": {
            "sheetId": n, "dimension": "ROWS",
            "startIndex": 1, "endIndex": строк + 1}}})
    http.request("https://sheets.googleapis.com/v4/spreadsheets/%s:batchUpdate" % sid,
                 method="POST", headers=h,
                 raw_body=json.dumps({"requests": запросы}).encode("utf-8"))
    return len(запросы)


def подсказки_из_плана(план):
    u"""{ID: (свободы, запреты)} тем же кодом, что и карточки.

    🔴 Своего расчета тут нет намеренно. Запрет обязан совпадать с тем, что
    проверяет `grounding.py` по `data/ценности.tsv`: 29.08 карточка чуть не
    ушла с выдуманным запретом вместо настоящего. Две копии разойдутся.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import cards
    values = cards.read_values(open(str(ROOT / "data" / "ценности.tsv"),
                                    encoding="utf-8"))
    профили = cards.read_profiles(open(str(ROOT / "data" / "креаторы.tsv"),
                                       encoding="utf-8"))
    out = {}
    for строка in план:
        профиль = профили.get(строка.get(u"Креатор", ""))
        if not профиль:
            continue
        сцена = строка.get(u"Что в кадре", u"")
        в_кадре = cards.дети_в_кадре(строка, профиль)
        взрослый = cards.взрослый_в_кадре(строка)
        еще = cards.кто_еще_в_кадре(сцена, профиль, взрослый)
        свобода, _ = cards.свобода_для(сцена, оператор_занят=bool(еще))
        out[строка.get(u"ID")] = (свобода, cards.запреты(строка, values, в_кадре))
    return out


def _цвет(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return {"red": r, "green": g, "blue": b}


# Палитра. Спокойный сине-серый: таблицу читают с телефона между делами,
# и цвет тут служит навигации, а не украшению.
ТЕМНЫЙ = "1f3a5f"      # шапка таблицы
СВЕТЛЫЙ = "eef2f7"     # зебра строк и фон заголовков разделов
ТРЕВОГА = "b3261e"     # то, без чего работа не дойдет до нас
ФОН_ТРЕВОГИ = "fce8e6"
СЕРЫЙ = "5f6368"


def _стиль_блока(стиль):
    u"""Как выглядит ячейка памятки. Иерархия делается размером и цветом,
    а не жирностью всего подряд - жирное целиком не читается вовсе."""
    if стиль == "h1":
        return {"textFormat": {"bold": True, "fontSize": 18,
                               "foregroundColor": _цвет(ТЕМНЫЙ)},
                "verticalAlignment": "MIDDLE"}
    if стиль == "lead":
        return {"textFormat": {"fontSize": 11, "italic": True,
                               "foregroundColor": _цвет(СЕРЫЙ)},
                "wrapStrategy": "WRAP"}
    if стиль == "h2":
        return {"textFormat": {"bold": True, "fontSize": 12,
                               "foregroundColor": _цвет(ТЕМНЫЙ)},
                "backgroundColor": _цвет(СВЕТЛЫЙ),
                "verticalAlignment": "MIDDLE",
                "padding": {"left": 8}}
    if стиль == "warn":
        return {"textFormat": {"bold": True, "fontSize": 11,
                               "foregroundColor": _цвет(ТРЕВОГА)},
                "backgroundColor": _цвет(ФОН_ТРЕВОГИ),
                "wrapStrategy": "WRAP", "padding": {"left": 8, "top": 4, "bottom": 4}}
    if стиль == "link":
        return {"textFormat": {"fontSize": 11, "underline": True},
                "wrapStrategy": "WRAP", "padding": {"left": 8}}
    if стиль == "list":
        return {"textFormat": {"fontSize": 11}, "wrapStrategy": "WRAP",
                "padding": {"left": 20}}
    return {"textFormat": {"fontSize": 11}, "wrapStrategy": "WRAP",
            "padding": {"left": 8}}


def push_памятку(book):
    u"""Кладет памятку отдельным листом. Переписывает целиком: она одна на всех.

    🔴 Лист заводится ПЕРВЫМ по порядку не случайно: человек открывает таблицу
    и видит вкладки внизу. Памятка, лежащая второй, читается только тем,
    кто уже понял, что чего-то не хватает.
    """
    import json
    from lib import google_auth, http
    book.add(ЛИСТ_ПАМЯТКИ)
    лист = book.sheet(ЛИСТ_ПАМЯТКИ)
    блоки = памятка()
    # пустая строка перед каждым заголовком раздела - воздух, без него список слипается
    строки = []
    for стиль, текст in блоки:
        if стиль == "h2" and строки:
            строки.append(("spacer", u""))
        строки.append((стиль, u"• " + текст if стиль == "list" else текст))

    # 🔴 Ссылка кладется формулой HYPERLINK, а не голым адресом: адрес формы
    # длиной в сто знаков переносится и обрывается на середине (видно глазами
    # 02.09). Человеку нужна кнопка, а не строка символов.
    значения = []
    for стиль, текст in строки:
        if стиль == "link":
            значения.append([u'=HYPERLINK("%s"; "Открыть форму сдачи →")' % текст])
        else:
            значения.append([текст])
    лист._put(u"%s!A1:A%s" % (ЛИСТ_ПАМЯТКИ, len(строки) + 20),
              значения + [[u""]] * 20)

    h = book.sa.headers([google_auth.SCOPE_SHEETS])
    h["Content-Type"] = "application/json"
    meta = http.request("https://sheets.googleapis.com/v4/spreadsheets/" + book.sid,
                        headers=h)
    n = dict((s["properties"]["title"], s["properties"]["sheetId"])
             for s in meta["sheets"])[ЛИСТ_ПАМЯТКИ]
    запросы = [
        {"updateDimensionProperties": {
            "range": {"sheetId": n, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 720}, "fields": "pixelSize"}},
        # 🔴 Сначала гасим все: лист мог остаться от прежней версии, где стояли
        # чужие цвета и жирность. Иначе новые стили лягут поверх старых пятнами.
        {"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": 0, "endRowIndex": len(строки) + 40},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": False, "italic": False, "fontSize": 11,
                               "foregroundColor": _цвет("000000")},
                "backgroundColor": _цвет("ffffff"),
                "wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": n, "index": 0,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "index,gridProperties.frozenRowCount"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": n, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 48}, "fields": "pixelSize"}},
    ]
    for i, (стиль, _) in enumerate(строки):
        if стиль == "spacer":
            запросы.append({"updateDimensionProperties": {
                "range": {"sheetId": n, "dimension": "ROWS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": 10}, "fields": "pixelSize"}})
            continue
        запросы.append({"repeatCell": {
            "range": {"sheetId": n, "startRowIndex": i, "endRowIndex": i + 1,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": _стиль_блока(стиль)},
            "fields": "userEnteredFormat"}})
        if стиль == "h2":
            запросы.append({"updateDimensionProperties": {
                "range": {"sheetId": n, "dimension": "ROWS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": 32}, "fields": "pixelSize"}})
    # сетку убираем: это текст, а не таблица
    запросы.append({"updateSheetProperties": {
        "properties": {"sheetId": n, "gridProperties": {"hideGridlines": True}},
        "fields": "gridProperties.hideGridlines"}})
    # высота абзацев - под текст. Заголовки и отбивки уже получили свою выше,
    # поэтому подгоняем только строки с текстом.
    for i, (стиль, _) in enumerate(строки):
        if стиль in ("text", "lead", "warn", "list", "link"):
            запросы.append({"autoResizeDimensions": {"dimensions": {
                "sheetId": n, "dimension": "ROWS",
                "startIndex": i, "endIndex": i + 1}}})
    http.request("https://sheets.googleapis.com/v4/spreadsheets/%s:batchUpdate" % book.sid,
                 method="POST", headers=h,
                 raw_body=json.dumps({"requests": запросы}).encode("utf-8"))
    return len(строки)


def read_plan(path):
    u"""План партии из TSV: первая строка - заголовки."""
    текст = Path(path).read_text(encoding="utf-8")
    строки = [s for s in текст.split(u"\n") if s.strip()]
    заголовок = строки[0].split(u"\t")
    out = []
    for s in строки[1:]:
        поля = s.split(u"\t")
        if len(поля) != len(заголовок):
            raise SystemExit(u"строка плана не той ширины: %s" % поля[:1])
        out.append(dict(zip(заголовок, поля)))
    return out


def main(argv):
    if "--selftest" in argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
        import test_handout
        return test_handout.selftest()
    if "--show" in argv:
        план = read_plan(argv[argv.index("--show") + 1])
        for строка in handout_rows(план, подсказки=подсказки_из_плана(план)):
            print(u"%s · выход %s · снять до %s · %s · %s"
                  % (строка[u"№"], строка[u"Прислать до"],
                     строка[u"Кто снимает"], строка[u"Товар"]))
        просрочка = overdue(план)
        if просрочка:
            print(u"⚠️ срок этих строк уже прошел: %s" % u", ".join(просрочка))
        return
    if "--sheet" in argv:
        лист = make_sheet()
        format_sheet(лист, len(лист.read()))
        print(u"лист «%s» готов, колонок %s, оформление применено"
              % (ЛИСТ, len(лист.header or [])))
        return
    if "--push" in argv:
        план = read_plan(argv[argv.index("--push") + 1])
        book = make_book()
        # 🔴 Памятка едет вместе с планом, а не отдельной командой: иначе
        # партия уйдет людям, а «куда сдавать» останется в репозитории.
        print(u"памятка: строк %s" % push_памятку(book))
        лист = make_sheet(book)
        было_строк = лист.read()
        было = pull(лист)
        выдача = handout_rows(план, подсказки=подсказки_из_плана(план))
        n = push(лист, выдача)
        # 🔴 оформление применяется КАЖДЫЙ раз, а не при заведении листа: новые
        # строки приходят без формата, и даты в них снова станут числами.
        format_sheet(лист, n)
        print(u"залито строк: %s, сохранено отметок: %s" % (n, len(было)))
        # 🔴 Анонс в группу шлет САМА система (владелец 03.09): при новых
        # строках - всегда, флагом --announce - принудительно (первая выдача).
        новые = новые_номера(было_строк, выдача)
        if (новые or "--announce" in argv) and os.environ.get("TELEGRAM_BOT_TOKEN") \
                and os.environ.get("TELEGRAM_GROUP_ID"):
            from lib import telegram
            url = ("https://docs.google.com/spreadsheets/d/%s/edit"
                   % os.environ.get("CREATORS_SHEET_ID", ""))
            бот = telegram.Bot(os.environ["TELEGRAM_BOT_TOKEN"],
                               os.environ.get("TELEGRAM_OWNER_ID") or 0)
            бот.notify(announce_text(новые or [r[u"№"] for r in выдача],
                                     выдача, url),
                       chat_id=os.environ["TELEGRAM_GROUP_ID"])
            print(u"анонс отправлен в группу креаторов (%s строк)"
                  % (len(новые) or len(выдача)))
        просрочка = overdue(план)
        if просрочка:
            print(u"⚠️ срок этих строк уже прошел: %s" % u", ".join(просрочка))
        return
    if "--pull" in argv:
        отмечено = pull(make_sheet())
        if not отмечено:
            print(u"снятого пока не отмечено")
        for ident, коммент in sorted(отмечено.items()):
            print(u"%s снято%s" % (ident, (u" · " + коммент) if коммент else u""))
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
