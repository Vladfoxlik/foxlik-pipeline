# -*- coding: utf-8 -*-
"""Разовая настройка таблицы: завести листы, проставить заголовки, залить план партии.

Запускается один раз после того, как появился сервисный аккаунт и таблица
расшарена на его почту. Повторный запуск безопасен: существующие листы
не трогает, заголовки не перезаписывает, план не дублирует.

    python setup.py --sheets                 завести листы и заголовки
    python setup.py --plan путь/к/плану.tsv  залить строки партии в лист ПЛАН
    python setup.py --form путь/к/плану.tsv  напечатать список для формы

🔴 **Плана партии в этом репозитории нет и быть не должно.** Репозиторий публичный,
а план - содержание работы. Файл лежит в приватном `Контент_Завод_v2` и передается
путем. Скрипт возит данные, но не хранит их.

🔴 **Лист СДАЧИ создает форма, а не мы.** Google называет колонки по-своему, и заранее
угадать их нельзя. Поэтому по СДАЧАМ скрипт только **дописывает недостающие** колонки
статуса справа от того, что уже есть, и вслух говорит, что увидел.
"""
import os
import sys

from lib import google_auth, sheets

# Листы, которые заводим сами. СДАЧИ в списке нет - его делает форма.
LAYOUT = {
    # 🔴 «Статус» - признак черновика. Без него предложенные генератором строки
    # неотличимы от утвержденных владельцем, а из этого листа форма сдачи берет
    # выпадающий список: креатор снял бы то, что никто не утверждал.
    "ПЛАН": ["ID", "Креатор", "Механика", "Что в кадре", "Смысл хука",
             "Гипотеза", "Факт-источник", "Ожидаемый сигнал", "Дата в эфир",
             "Статус"],
    "ПУБЛИКАЦИИ": ["ID", "Дата", "Площадка", "Ссылка", "Медиа ID", "Креатор",
                   "Механика", "Длина", "Разрешение"],
    "МЕТРИКИ": ["ID", "Дата замера", "Просмотры", "Охват", "Репосты", "Репосты/1000",
                "Сохранения", "Комментарии", "Подписки", "Вердикт",
                "Пропуск первых 3 сек", "Среднее время"],
    "СЛОВАРЬ": ["Механика", "Применений", "Медиана репостов/1000", "Подписки",
                "Статус решения", "Статус по метрикам", "На каких роликах",
                "Дата снятия с плана"],
    "НАСТРОЙКИ": ["Ключ", "Значение"],
}

# Колонки, которые конвейер дописывает в лист формы
SUBMISSION_EXTRA = ["Статус", "Дата публикации", "Причина отказа"]
SHEET_SUBMISSIONS = "СДАЧИ"


def ensure_headers(sheet, names):
    """Ставит заголовки, если строка заголовков пуста. Заполненную не трогает."""
    sheet.read()
    if sheet.header:
        return False
    last = sheets.a1_column(len(names) - 1)
    sheet._put("%s!A1:%s1" % (sheet.title, last), [names])
    return True


def ensure_extra_columns(sheet, names):
    """Дописывает недостающие колонки справа от тех, что уже есть."""
    sheet.read()
    if not sheet.header:
        raise SystemExit("лист %s пуст - сначала соберите форму и свяжите ее "
                         "с таблицей, тогда Google создаст заголовки" % sheet.title)
    missing = [n for n in names if n not in sheet.header]
    if not missing:
        return []
    start = sheets.a1_column(len(sheet.header))
    end = sheets.a1_column(len(sheet.header) + len(missing) - 1)
    sheet._put("%s!%s1:%s1" % (sheet.title, start, end), [missing])
    return missing


def read_plan(path):
    """План партии из TSV: первая строка - заголовки, дальше строки.

    🔴 **Число полей в строке сверяется с заголовком.** Замер 27.08: строки шли
    с восемью полями при девяти колонках, `zip` молча обрезал по короткому,
    все съехало на колонку влево - и заливка отчиталась «добавлено 15».
    Испорченные данные с отчетом об успехе хуже отказа: отказ виден сразу.
    """
    with open(path, encoding="utf-8-sig") as f:
        rows = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    if not rows:
        raise SystemExit("файл плана пуст: %s" % path)
    head = [c.strip() for c in rows[0]]
    unknown = [c for c in head if c not in LAYOUT["ПЛАН"]]
    if unknown:
        raise SystemExit("в плане колонки, которых нет в листе ПЛАН: %s.\nОжидаются: %s"
                         % (", ".join(unknown), ", ".join(LAYOUT["ПЛАН"])))
    bad = [(i, len(r)) for i, r in enumerate(rows[1:], 2) if len(r) != len(head)]
    if bad:
        raise SystemExit(
            "в плане строки не той ширины: колонок в заголовке %s, а в строках - "
            "%s. Значения съедут на соседние колонки, и это не будет видно."
            % (len(head), "; ".join("строка %s: %s" % b for b in bad[:5])))
    return [dict(zip(head, r)) for r in rows[1:]]


def form_options(plan):
    """Строки для выпадающего списка формы.

    Берется **смысл хука**, а не описание кадра: хук короткий и опознается целиком,
    а кадр - это абзац, который обрывается на полуслове и в списке нечитаем.
    Имя креатора идет вторым, чтобы каждый находил свои строки глазами.
    """
    out = []
    for row in plan:
        hook = (row.get("Смысл хука") or row.get("Что в кадре") or "").strip()
        if len(hook) > 60:
            hook = hook[:57].rstrip() + "..."
        out.append("%s · %s · %s" % (row.get("ID", "?"),
                                     row.get("Креатор", "?"), hook))
    return out


def make_book():
    sa = google_auth.ServiceAccount.load()
    sid = os.environ.get("SHEET_ID")
    if not sid:
        raise SystemExit("нет переменной SHEET_ID")
    return sheets.Book(sa, sid)


def do_sheets():
    book = make_book()
    for title, header in LAYOUT.items():
        created = book.add(title)
        filled = ensure_headers(book.sheet(title), header)
        print("%-12s %s %s" % (title,
                               "заведен" if created else "уже был",
                               "· заголовки поставлены" if filled else "· заголовки на месте"))
    if SHEET_SUBMISSIONS in book.titles():
        added = ensure_extra_columns(book.sheet(SHEET_SUBMISSIONS), SUBMISSION_EXTRA)
        print("%-12s дописано колонок: %s" % (SHEET_SUBMISSIONS, ", ".join(added) or "нечего"))
    else:
        print("%-12s ⚠️ листа нет. Он появится, когда форму свяжут с таблицей "
              "(и его надо переименовать в СДАЧИ)" % SHEET_SUBMISSIONS)


def do_plan(path):
    plan = read_plan(path)
    sheet = make_book().sheet("ПЛАН")
    have = {r.get("ID") for r in sheet.read()}
    added = 0
    for row in plan:
        if row.get("ID") in have:
            continue        # повторный запуск не должен плодить дубли
        sheet.append(row)
        added += 1
    print("строк в файле: %s, добавлено новых: %s, уже было: %s"
          % (len(plan), added, len(plan) - added))


def do_form(path):
    print("Варианты для вопроса «Строка плана» в форме - скопируйте целиком:\n")
    for line in form_options(read_plan(path)):
        print(line)


def selftest():
    import tempfile

    header = "\t".join(LAYOUT["ПЛАН"])
    row1 = "\t".join(["P1-01", "Спартак", "товар в развязке",
                      "папа собирается на работу, дочь не отпускает, он сажает ее "
                      "за световой стол и уходит, а на песке нарисован он сам",
                      "взрослый мужчина не может выйти из дома", "Г1", "Папинство 1,79",
                      "подписки выше базовой", "2026-09-04", "ЧЕРНОВИК"])
    row2 = "\t".join(["P1-02", "Ксения", "товар в развязке", "мама на созвоне",
                      "как досидеть созвон", "Г1", "Сохраняйте лайфхак", "подписки",
                      "2026-09-05", ""])
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False,
                                     encoding="utf-8") as f:
        f.write("\n".join([header, row1, row2]) + "\n")
        path = f.name

    plan = read_plan(path)
    assert len(plan) == 2 and plan[0]["ID"] == "P1-01"
    assert plan[0]["Креатор"] == "Спартак"

    options = form_options(plan)
    # 🔴 в списке идет смысл хука, а не описание кадра: кадр обрывается на полуслове
    assert options[0] == "P1-01 · Спартак · взрослый мужчина не может выйти из дома", options[0]
    assert options[1] == "P1-02 · Ксения · как досидеть созвон", options[1]
    assert all(len(o) <= 90 for o in options), "вариант должен читаться, а не быть простыней"

    # длинный хук обязан обрезаться, а не разъезжаться
    long_hook = form_options([{"ID": "P1-99", "Креатор": "Ксения",
                               "Смысл хука": "очень " * 20}])[0]
    assert long_hook.endswith("...") and len(long_hook) <= 90, long_hook

    # 🔴 лишняя колонка в файле должна ловиться до записи, а не после
    with open(path, "w", encoding="utf-8") as f:
        f.write("ID\tЧужая колонка\nP1-01\tx\n")
    try:
        read_plan(path)
        raise AssertionError("неизвестная колонка должна отвергаться")
    except SystemExit as e:
        assert "Чужая колонка" in str(e), e

    # 🔴 строка уже той ширины - иначе zip молча обрежет и все съедет влево.
    # Замер 27.08: так в таблицу легли 15 строк со сдвигом, и заливка отчиталась
    # об успехе. Испорченные данные с отчетом «готово» хуже честного отказа.
    with open(path, "w", encoding="utf-8") as f:
        f.write("ID\tКреатор\tМеханика\nP1-01\tСпартак\n")
    try:
        read_plan(path)
        raise AssertionError("узкая строка должна отвергаться")
    except SystemExit as e:
        assert "не той ширины" in str(e) and "строка 2" in str(e), e
    os.unlink(path)

    # --- заголовки ставятся один раз ---
    class FakeSheet:
        def __init__(self, header):
            self.title = "ПЛАН"
            self.header = list(header)
            self.puts = []

        def read(self):
            return []

        def _put(self, rng, values):
            self.puts.append((rng, values))

    empty = FakeSheet([])
    assert ensure_headers(empty, ["A", "B", "C"]) is True
    assert empty.puts[0][0] == "ПЛАН!A1:C1"
    filled = FakeSheet(["A"])
    assert ensure_headers(filled, ["A", "B"]) is False, "заполненные заголовки не трогаем"
    assert not filled.puts

    # --- недостающие колонки дописываются справа, существующие не дублируются ---
    sub = FakeSheet(["Отметка времени", "Строка плана", "Файл", "Статус"])
    sub.title = "СДАЧИ"
    added = ensure_extra_columns(sub, SUBMISSION_EXTRA)
    assert added == ["Дата публикации", "Причина отказа"], added
    assert sub.puts[0][0] == "СДАЧИ!E1:F1", sub.puts[0][0]

    # 🔴 набор колонок кода и листов обязан сходиться, иначе промах вылезет в бою
    import tick
    for name in (tick.COL_STATUS, tick.COL_DATE, tick.COL_REASON):
        assert name in SUBMISSION_EXTRA, "tick.py ждет колонку %s, а setup ее не создает" % name
    for name in ("ID", "Дата", "Площадка", "Ссылка", "Медиа ID", "Креатор"):
        assert name in LAYOUT["ПУБЛИКАЦИИ"], name
    import metrics
    assert metrics.COL_MEDIA in LAYOUT["ПУБЛИКАЦИИ"]
    assert "Подписки" in LAYOUT["МЕТРИКИ"] and "Вердикт" in LAYOUT["МЕТРИКИ"]
    # 🔴 словарь пишет в лист по именам колонок - промах вылезет только в бою
    import dictionary
    for name in (dictionary.COL_MECHANIC, dictionary.COL_COUNT, dictionary.COL_MEDIAN,
                 dictionary.COL_SUBS, dictionary.COL_METRIC_STATUS, dictionary.COL_VIDEOS):
        assert name in LAYOUT["СЛОВАРЬ"], "dictionary ждет колонку %s" % name
    # и колонки человека обязаны существовать: иначе решать будет негде
    for name in dictionary.HUMAN_COLUMNS:
        assert name in LAYOUT["СЛОВАРЬ"], name
    assert "Механика" in LAYOUT["ПУБЛИКАЦИИ"], "без нее словарь не на чем строить"
    print("setup selftest OK: разбор плана, варианты для формы, чужая колонка отвергнута, "
          "заголовки один раз, дописывание справа, колонки сходятся с tick и metrics")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
    elif "--sheets" in args:
        do_sheets()
    elif "--plan" in args:
        do_plan(args[args.index("--plan") + 1])
    elif "--form" in args:
        do_form(args[args.index("--form") + 1])
    else:
        print(__doc__)
