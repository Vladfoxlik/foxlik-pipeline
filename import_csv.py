# -*- coding: utf-8 -*-
"""Импорт выгрузки Meta Business Suite в листы ПУБЛИКАЦИИ и МЕТРИКИ.

🔴 Зачем этот модуль существует. Приемка 28.08 показала, что памяти у петли нет
и взяться ей неоткуда: словарь механик читает колонку «Репосты/1000», которую
заполняет только замер через Instagram API, а **подписки API не отдает вовсе** -
такой метрики у него нет. Единственный источник подписок - ручная выгрузка CSV,
и импортера этой выгрузки не было ни одного. Квартал из 190 роликов, на котором
построены все наши выводы, в петлю не попадал.

Отсюда две работы, которые делает этот файл:

1. **Заливает историю.** Ролики, которых у нас нет, заводятся в ПУБЛИКАЦИЯХ
   и МЕТРИКАХ с пустой механикой. Механику проставляет человек - машина ее
   не угадывает (ТЗ §5: разметка это работа человека). Угадывающая машина
   отравила бы словарь собственными догадками, и отличить их потом было бы нечем.
2. **Дополняет наши ролики подписками.** Ролик, опубликованный конвейером, уже
   лежит в ПУБЛИКАЦИЯХ с нашим ID плана и с механикой. Импорт узнает его
   по «Медиа ID» и дописывает подписки в существующую строку МЕТРИК, а не заводит
   двойника с чужим ID - иначе один ролик считался бы дважды.

Как снимается выгрузка: Business Suite → Статистика → Контент → вкладка Instagram
→ Экспортировать данные. Кодировка UTF-8 с меткой BOM, поля в кавычках, описания
многострочные - поэтому читаем модулем csv, а не построчно.

    python import_csv.py ../data/business_suite/ig_export_2026-05-28_2026-08-25.csv
    python import_csv.py --selftest

⚠️ Связь «наш ролик ↔ строка выгрузки» идет по «Медиа ID». То, что идентификатор
медиа из Graph API совпадает с колонкой «ID публикации» в выгрузке, **живьем
не проверено** - у нас еще не было ни одной публикации через конвейер. Если
окажется, что не совпадает, запасная связь - постоянная ссылка, она пишется
в ПУБЛИКАЦИИ в колонку «Ссылка».
"""
import csv
import io
import os
import sys

# Заголовки выгрузки. Сняты с живого файла 28.08, не переписывать по памяти:
# промах здесь молчаливый - колонка просто не найдется, и цифра станет пустой.
C_ID = "ID публикации"
C_ACCOUNT = "Имя пользователя аккаунта"
C_DESCR = "Описание"
C_LEN = "Длительность (с.)"
C_LINK = "Постоянная ссылка"
C_KIND = "Тип публикации"
C_DATE = "Дата"
# 🔴 Аудит 02.09: в живой выгрузке колонка «Дата» несет «За всё время» у ВСЕХ
# строк - настоящая дата лежит здесь, по-американски (MM/DD/YYYY HH:MM).
C_PUBLISHED = "Время публикации"
C_VIEWS = "Просмотры"
C_REACH = "Охват"
C_SHARES = "Репосты"
C_FOLLOWS = "Подписки"
C_COMMENTS = "Комментарии"
C_SAVED = "Сохранения"

# В выгрузке лежат и фото, и карусели. Механику мы меряем на роликах:
# смешать их в одну медиану значит сравнивать несравнимое.
VIDEO_KINDS = ("видео", "reel", "рил")


def _published(value):
    """Дата публикации из «Время публикации» (MM/DD/YYYY HH:MM) - ISO или пусто."""
    from lib import dates
    d = dates.as_date(str(value or "").strip()[:10], us=True)
    return d.isoformat() if d else ""


def _number(value):
    """Число или None. Пустое и мусор дают None, а не ноль.

    Разница существенная: ноль - это измеренное отсутствие, None - неизвестность.
    Ноль в просмотрах поднял бы показатель до бесконечности, ноль в подписках
    занизил бы итог механики.
    """
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number == int(number) else number


from lib.gate import per_1000, verdict as _verdict_canon  # noqa: E402


def _clean(row):
    """Заголовки без метки BOM и без краевых пробелов.

    Файл читается как utf-8-sig, но `parse` могут позвать и с обычным потоком -
    тогда первый заголовок приезжает как «﻿ID публикации» и не находится
    по имени. Отказ был бы молчаливым: колонка просто пустая.
    """
    # Метка стоит ПЕРЕД кавычкой: «﻿"ID публикации"». Из-за нее csv не считает
    # поле закавыченным и отдает имя вместе с кавычками - снимаем и их.
    return {(k or "").replace("﻿", "").strip().strip('"'): v
            for k, v in row.items()}


def parse(stream):
    """Строки выгрузки в наш вид. Только ролики, только с идентификатором."""
    rows = []
    for raw in (_clean(r) for r in csv.DictReader(stream)):
        kind = (raw.get(C_KIND) or "").strip().lower()
        if kind and not any(k in kind for k in VIDEO_KINDS):
            continue
        views = _number(raw.get(C_VIEWS))
        shares = _number(raw.get(C_SHARES))
        rows.append({
            "media_id": (raw.get(C_ID) or "").strip(),
            "account": (raw.get(C_ACCOUNT) or "").strip(),
            "link": (raw.get(C_LINK) or "").strip(),
            "date": (raw.get(C_DATE) or "").strip(),
            "published": _published(raw.get(C_PUBLISHED)),
            "descr": (raw.get(C_DESCR) or "").strip(),
            "length": _number(raw.get(C_LEN)),
            "views": views,
            "reach": _number(raw.get(C_REACH)),
            "shares": shares,
            "follows": _number(raw.get(C_FOLLOWS)),
            "comments": _number(raw.get(C_COMMENTS)),
            "saved": _number(raw.get(C_SAVED)),
            "rate": per_1000(shares, views),
        })
    return rows


def read_file(path):
    # utf-8-sig: выгрузка приходит с меткой BOM, без нее первая колонка
    # называется «﻿ID публикации» и не находится по имени
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return parse(f)


def _verdict(rate):
    # 🔴 Аудит 02.09: слова вердикта - канон lib/gate, второй словарь удален.
    return _verdict_canon(rate)


def main_account(rows):
    """Наш аккаунт - тот, из которого в выгрузке больше всего роликов.

    Угадывание здесь безопаснее списка в коде: каналов у нас два, и добавление
    третьего не должно требовать правки файла. Выбранное имя печатается в сводке,
    чтобы догадка была видна человеку, а не молчала.
    """
    counts = {}
    for item in rows:
        name = item.get("account") or ""
        if name:
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def load(rows, pubs, metrics, report=False, account=None):
    """Заливка в листы. Возвращает число новых роликов либо сводку.

    Идемпотентно: импорт делается руками, значит его запустят дважды. Дубль
    в ПУБЛИКАЦИЯХ удвоил бы применения механики, дубль в МЕТРИКАХ перетер бы замер.

    🔴 Коллабы отсекаются. В выгрузке лежат и ролики, опубликованные из аккаунтов
    креаторов: замер 26.08 дал по ним 2,11 репоста на 1000 против наших 0,50,
    вчетверо больше. Свалить их в одну медиану значит завысить показатель механики
    тем сильнее, чем больше коллабов в партии, и приписать эту разницу приему.
    Сколько отсеяно - сказано в сводке.
    """
    account = account or main_account(rows)
    stats = {"новых": 0, "обновлено": 0, "без id": 0, "без показателя": 0,
             "коллабы": 0, "аккаунт": account}

    known_pub = {}          # медиа-ID -> строка ПУБЛИКАЦИЙ
    id_by_media = {}        # медиа-ID -> наш ID ролика
    known_link = {}         # постоянная ссылка -> строка ПУБЛИКАЦИЙ
    for row in pubs.read():
        media = (row.get("Медиа ID") or "").strip()
        if media:
            known_pub[media] = row
            id_by_media[media] = (row.get("ID") or "").strip() or media
        link = (row.get("Ссылка") or "").strip().rstrip("/")
        if link:
            known_link[link] = row

    known_met = {}          # наш ID -> строка МЕТРИК
    for row in metrics.read():
        key = (row.get("ID") or "").strip()
        if key:
            known_met[key] = row

    for item in rows:
        media = item["media_id"]
        if not media:
            stats["без id"] += 1
            continue
        if account and item.get("account") and item["account"] != account:
            stats["коллабы"] += 1
            continue
        if item["rate"] is None:
            stats["без показателя"] += 1

        our_id = id_by_media.get(media, media)
        if media not in known_pub:
            # 🔴 Аудит 02.09: конвейер Postmypost пишет «Медиа ID» = pmp:<id>,
            # а выгрузка несет настоящий id Instagram - по id они не совпадут
            # НИКОГДА, и каждый ролик конвейера плодил двойника без механики:
            # словарь получал ноль. Запасная связь - постоянная ссылка поста
            # (заявлена в шапке модуля с 28.08, написана только сейчас).
            наш = known_link.get((item.get("link") or "").strip().rstrip("/"))
            if наш is not None:
                our_id = (наш.get("ID") or "").strip() or media
                known_pub[media] = наш
                id_by_media[media] = our_id
                # настоящий id дозаписывается: дальше связь пойдет по нему
                if "_row" in наш and hasattr(pubs, "set"):
                    pubs.set(наш["_row"], "Медиа ID", media)
                наш["Медиа ID"] = media
        if media not in known_pub:
            # 🔴 Механика пустая намеренно: ее ставит человек. Импорт не угадывает.
            pubs.append({"ID": our_id,
                         "Дата": item.get("published") or item["date"],
                         "Площадка": "instagram",
                         "Ссылка": item["link"],
                         "Медиа ID": media,
                         "Креатор": "",
                         "Механика": "",
                         "Длина": item["length"] if item["length"] is not None else ""})
            known_pub[media] = True
            id_by_media[media] = our_id
            stats["новых"] += 1

        values = {"ID": our_id,
                  "Дата замера": item["date"],
                  "Просмотры": item["views"] if item["views"] is not None else "",
                  "Охват": item["reach"] if item["reach"] is not None else "",
                  "Репосты": item["shares"] if item["shares"] is not None else "",
                  "Репосты/1000": "" if item["rate"] is None else item["rate"],
                  "Сохранения": item["saved"] if item["saved"] is not None else "",
                  "Комментарии": item["comments"] if item["comments"] is not None else "",
                  "Подписки": item["follows"] if item["follows"] is not None else "",
                  "Вердикт": _verdict(item["rate"])}
        old = known_met.get(our_id)
        if old is None:
            metrics.append(values)
            known_met[our_id] = values
        else:
            # Выгрузку снимают раз в две недели, показатели за это время растут -
            # строку обновляем, а не заводим вторую.
            if "_row" in old:
                metrics.set_many(old["_row"], values)
            old.update(values)
            stats["обновлено"] += 1

    return stats if report else stats["новых"]


def summary(stats):
    if not isinstance(stats, dict):
        stats = {"новых": stats}
    lines = ["📥 Импорт выгрузки Business Suite"]
    if stats.get("аккаунт"):
        lines.append("аккаунт: %s" % stats["аккаунт"])
    lines += ["новых роликов: %s" % stats.get("новых", 0),
              "обновлено: %s" % stats.get("обновлено", 0)]
    if stats.get("коллабы"):
        # Не ошибка, а устройство: ролики из аккаунтов креаторов меряются отдельно,
        # у них показатель вчетверо выше нашего (замер 26.08).
        lines.append("отложено коллабов (чужие аккаунты): %s" % stats["коллабы"])
    if stats.get("без id"):
        lines.append("⚠️ пропущено без идентификатора: %s" % stats["без id"])
    if stats.get("без показателя"):
        lines.append("⚠️ без показателя репостов (ноль просмотров или мусор): %s"
                     % stats["без показателя"])
    lines.append("")
    lines.append("🔴 У новых роликов колонка «Механика» в ПУБЛИКАЦИЯХ пустая - "
                 "словарь такие строки пропускает. Разметьте их руками, иначе "
                 "история в память петли не попадет.")
    return "\n".join(lines)


def run(path):
    """Боевой запуск: живая таблица из окружения."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lib import google_auth, sheets
    sa = google_auth.ServiceAccount.load()
    sid = os.environ["SHEET_ID"]
    rows = read_file(path)
    stats = load(rows, sheets.Sheet(sa, sid, "ПУБЛИКАЦИИ"),
                 sheets.Sheet(sa, sid, "МЕТРИКИ"), report=True)
    print("прочитано роликов: %s" % len(rows))
    print(summary(stats))
    return stats


def main():
    args = [a for a in sys.argv[1:] if a != "--selftest"]
    if "--selftest" in sys.argv or not args:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "tests"))
        import test_import_csv
        test_import_csv.selftest()
        return
    run(args[0])


if __name__ == "__main__":
    main()
