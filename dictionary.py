# -*- coding: utf-8 -*-
"""Словарь механик - память петли.

**Зачем.** Без него петля меряет партию и забывает ее: когда партия кончится,
план следующей снова придется писать руками. Правило из процедуры петли §4.5
прямое: **вывод, не попавший в словарь, считается не сделанным.**

Считается из того, что уже собрано: `ПУБЛИКАЦИИ` дают механику каждого ролика,
`МЕТРИКИ` - репосты на 1000 и подписки. Ничего нового вводить не нужно.

Правила взяты из [ТЗ §4](../управление/ТЗ_ГЕНЕРАТОР_ПЛАНА.md), не выдуманы:

| Что | Значение |
|---|---|
| Минимум для решения | **4 ролика** с этой механикой |
| Подтверждена | медиана группы выше базовой на 20%+ |
| Опровергнута | ниже базовой на 20%+ |
| Иначе | «не решено», копим дальше |
| Выгорание | три применения **подряд** ниже базовой |

**Базовая медиана** - по всем замеренным роликам разом, а не среднее по механикам:
иначе одна многочисленная механика перетягивала бы точку отсчета на себя.

🔴 **Машина не трогает колонки человека.** «Статус решения» и «Дата снятия с плана» -
это решения владельца, и пересчет их не переписывает. Иначе петля начала бы молча
отменять его решения, а заметить это было бы нечем: цифры остались бы правильными.

🔴 **Выгорание не снимает механику с плана само.** Оно записывается в «Статус
по метрикам» и попадает во владельцеву сводку, а снимает - человек. Утверждение,
из-за которого вариант выбывает, требует его решения, даже когда посчитано верно.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SHEET = "СЛОВАРЬ"
MIN_FOR_DECISION = 4          # ТЗ §4: меньше четырех - копим, а не решаем
SPREAD = 0.20                 # ТЗ §4: ±20% от базовой
BURNOUT_IN_A_ROW = 3          # ТЗ §4: три подряд ниже базовой

COL_MECHANIC = "Механика"
COL_COUNT = "Применений"
COL_MEDIAN = "Медиана репостов/1000"
COL_SUBS = "Подписки"
COL_METRIC_STATUS = "Статус по метрикам"
COL_VIDEOS = "На каких роликах"
# 🔴 Колонки человека. Список нужен целиком: он и есть граница между тем,
# что считает машина, и тем, что решает владелец.
HUMAN_COLUMNS = ("Статус решения", "Дата снятия с плана")


def median(values):
    """Медиана, а не среднее: одна вирусная выброска не должна красить механику."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    if len(xs) % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def _number(value):
    """Число из ячейки таблицы. Google отдает и «1,5», и «1.5», и пустоту."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".").replace(" ", "").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build(publications, measurements):
    """Собирает словарь: {механика: {применений, медиана, подписки, ...}}.

    На входе - строки листов как есть. Возвращает готовые значения, не трогая
    таблицу: так расчет проверяется без сети и без листов.
    """
    # 🔴 У одного ролика в ПУБЛИКАЦИЯХ строка на каждую площадку. Без сведения
    # по ID каждое применение считалось бы дважды, и порог «4 ролика» брался бы
    # вдвое раньше, чем на самом деле.
    video = {}
    for row in publications:
        vid = (row.get("ID") or "").strip()
        if not vid or vid in video:
            continue
        video[vid] = {"механика": (row.get(COL_MECHANIC) or "").strip(),
                      "дата": (row.get("Дата") or "").strip()}

    measured = {}
    for row in measurements:
        vid = (row.get("ID") or "").strip()
        rep = _number(row.get("Репосты/1000"))
        if vid and rep is not None:
            measured[vid] = {"репосты": rep, "подписки": _number(row.get("Подписки")) or 0}

    # ролик без замера решать не на чем - он в счет не идет
    pairs = [(vid, video[vid], measured[vid]) for vid in video if vid in measured]
    base = median([m["репосты"] for _, _, m in pairs])

    groups = {}
    for vid, info, m in pairs:
        groups.setdefault(info["механика"], []).append((vid, info["дата"], m))

    out = {}
    for mech, items in groups.items():
        if not mech:
            continue
        # порядок по дате публикации: выгорание - это «подряд во времени»,
        # а не «подряд в таблице». Строки лист отдает в порядке дописывания.
        items.sort(key=lambda x: (x[1], x[0]))
        reps = [m["репосты"] for _, _, m in items]
        out[mech] = {
            "применений": len(items),
            "медиана": median(reps),
            "подписки": int(sum(m["подписки"] for _, _, m in items)),
            "на каких роликах": ", ".join(vid for vid, _, _ in items),
            "статус по метрикам": _status(len(items), median(reps), reps, base),
        }
    return out


def _status(count, group_median, reps_in_order, base):
    """Словами, что говорят цифры. Решение - за человеком, это только чтение."""
    if count < MIN_FOR_DECISION:
        return "копим, %d из %d" % (count, MIN_FOR_DECISION)
    verdict = "не решено"
    if base:
        if group_median >= base * (1 + SPREAD):
            verdict = "подтверждена"
        elif group_median <= base * (1 - SPREAD):
            verdict = "опровергнута"
    # 🔴 Выгорание помечается только там, где механика НЕ опровергнута. Иначе
    # прием, который никогда не работал, получал бы ярлык «выгорела» - а это
    # про спад, а не про стабильно плохое. Владелец прочел бы «выгорела» как
    # «раньше работало» и стал бы ждать возврата того, чего не было.
    tail = reps_in_order[-BURNOUT_IN_A_ROW:]
    if (verdict != "опровергнута" and base and len(tail) == BURNOUT_IN_A_ROW
            and all(r < base for r in tail)):
        verdict += " · выгорела: %d подряд ниже базовой" % BURNOUT_IN_A_ROW
    return verdict


def write(sheet, data):
    """Кладет посчитанное в лист, оставляя колонки человека нетронутыми."""
    have = {}
    for row in sheet.read():
        name = (row.get(COL_MECHANIC) or "").strip()
        if name:
            have[name] = row

    for mech in sorted(data):
        d = data[mech]
        values = [(COL_COUNT, d["применений"]),
                  (COL_MEDIAN, round(d["медиана"], 2) if d["медиана"] is not None else ""),
                  (COL_SUBS, d["подписки"]),
                  (COL_METRIC_STATUS, d["статус по метрикам"]),
                  (COL_VIDEOS, d["на каких роликах"])]
        if mech in have:
            for column, value in values:
                sheet.set(have[mech]["_row"], column, value)
        else:
            row = {COL_MECHANIC: mech}
            row.update(dict(values))
            sheet.append(row)


def rebuild(book):
    """Пересчет целиком: три листа на входе, обновленный СЛОВАРЬ на выходе."""
    pubs = book.sheet("ПУБЛИКАЦИИ").read()
    mets = book.sheet("МЕТРИКИ").read()
    data = build(pubs, mets)
    write(book.sheet(SHEET), data)
    return data


def summary(data):
    """Короткая сводка владельцу. Молчащий пересчет не отличить от несделанного."""
    if not data:
        return "словарь: считать пока нечего, замеров нет"
    lines = ["Словарь механик обновлен, строк: %d" % len(data)]
    for mech in sorted(data, key=lambda m: -(data[m]["медиана"] or 0)):
        d = data[mech]
        lines.append("· %s: %d прим., медиана %.2f, %s"
                     % (mech, d["применений"], d["медиана"] or 0, d["статус по метрикам"]))
    burnt = [m for m in data if "выгорела" in data[m]["статус по метрикам"]]
    if burnt:
        lines.append("")
        lines.append("Просели три раза подряд: %s. Снимать с плана или нет - решение Ваше."
                     % ", ".join(burnt))
    return "\n".join(lines)


def selftest():
    from tests.test_dictionary import selftest as run
    run()


if __name__ == "__main__":
    selftest()
