# -*- coding: utf-8 -*-
"""Генератор плана следующей партии - последний недостающий кусок петли.

**Зачем.** Словарь помнит, что сработало, но без генератора это знание никуда
не идет: план следующей партии снова писался бы руками в сессии, то есть ровно
так, как работал продюсер - «отдали и забыли».

**Где проходит граница машины и человека.** Машина расставляет каркас: какие
механики брать, сколько роликов на гипотезу, кто снимает, в какой день выходит,
на каком факте это основано. **Содержание она не выдумывает**: «что в кадре»
и «смысл хука» остаются пустыми - [ТЗ §9](../управление/ТЗ_ГЕНЕРАТОР_ПЛАНА.md)
прямо исключает полные сценарии из системы. Заполняет их человек.

**Что берется из словаря:**

| Статус механики | Что делает генератор |
|---|---|
| подтверждена | берет: работает, надо пользоваться |
| опровергнута | не берет |
| копим, N из 4 | добирает до четырех - иначе решение принять не на чем |
| не решено | добирает |
| выгорела | 🔴 **не берет сам** - снятие с плана решает владелец |

🔴 **Два правила расстановки, без которых замер портится молча.** Оба записаны
не из красоты, а из замеров:

1. **Креаторы перемешаны внутри каждой гипотезы.** Отдать гипотезу целиком одному -
   и разницу механик не отличить от разницы людей. Замер 26.08: из аккаунта
   креатора пересылают вчетверо чаще, 2,11 против 0,50 при n=5.
2. **Гипотезы чередуются по дням, а не по неделям.** Отдать гипотезу целиком
   первой неделе - и разницу не отличить от дрейфа раздачи, сезона и тонуса
   аккаунта, которые меняются сами.

**План - черновик, а не приказ.** Утверждает его владелец ([петля §6](../управление/ПЕТЛЯ_ПРОЦЕДУРА.md)),
и до утверждения из него ничего не публикуется: публикация идет от сдач креаторов,
а не от строк плана.
"""
import datetime
import re

BATCH_SIZE = 15               # ТЗ: партия две недели, темп 1 публикация в день
MIN_PER_HYPOTHESIS = 4        # ТЗ §4: меньше четырех - решать не на чем
MAX_HYPOTHESES = 3            # ТЗ §4: одновременно активных 2-3
# 🔴 В ТЗ §4 две РАЗНЫЕ цифры квоты: «один слот из трех всегда за ними» и тут же
# «квота стоит один ролик из десяти». Это 5 роликов из 15 против полутора.
# Молча выбрать нельзя - это решение владельца, а не деталь реализации.
# По умолчанию берется явно сформулированное правило «один из трех»;
# поменять - одним числом здесь, после его слова.
CREATOR_QUOTA = 3             # знаменатель: каждый N-й слот за креаторами
CREATOR_SLOT = "гипотеза креатора"

COLUMNS = ["ID", "Креатор", "Механика", "Что в кадре", "Смысл хука",
           "Гипотеза", "Факт-источник", "Ожидаемый сигнал", "Дата в эфир"]

# Содержание не выдумывается - эти колонки уходят человеку пустыми
HUMAN_COLUMNS = ("Что в кадре", "Смысл хука")


def usable(dictionary):
    """Механики, которые можно ставить в план, и почему.

    Возвращает список пар (механика, основание) в порядке убывания пользы:
    сперва то, что подтверждено, потом то, что не досчитано.
    """
    confirmed, collecting = [], []
    for mech, d in dictionary.items():
        status = d.get("статус по метрикам", "")
        if "опровергнута" in status or "выгорела" in status:
            continue
        fact = "медиана %.2f реп/1000 на %d роликах" % (d.get("медиана") or 0,
                                                        d.get("применений") or 0)
        if "подтверждена" in status:
            confirmed.append((mech, "подтверждена ранее: " + fact))
        else:
            need = MIN_PER_HYPOTHESIS - (d.get("применений") or 0)
            collecting.append((mech, "не решено, добираем %d до четырех: %s"
                               % (max(need, 1), fact)))
    confirmed.sort(key=lambda x: -(dictionary[x[0]].get("медиана") or 0))
    collecting.sort(key=lambda x: -(dictionary[x[0]].get("применений") or 0))
    return confirmed + collecting


def next_batch_number(plan):
    """Номер партии продолжает прошлую: столкновение ID испортило бы учет."""
    best = 0
    for row in plan:
        m = re.match(r"^P(\d+)-", str(row.get("ID") or "").strip())
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def _dates(start, count):
    d = datetime.date(*[int(x) for x in str(start)[:10].split("-")])
    return [(d + datetime.timedelta(days=i)).isoformat() for i in range(count)]


def _rebalance(counts):
    """Не дает одной гипотезе занять больше половины партии.

    🔴 Замер на своем же коде 27.08: при наивном чередовании единственная
    полезная механика получила 10 слотов из 15 и легла **пятью подряд** в конце.
    Для замера это терпимо, для ленты - нет: зритель видит пять почти одинаковых
    роликов, а креатор снимает их под копирку.

    Математика простая: чтобы ни одна пара соседей не совпала, самая большая
    группа не может быть больше половины (с округлением вверх). Лишнее уходит
    в слоты креаторов - их гипотезы разные по определению, это самое безопасное
    место для остатка.
    """
    counts = {k: v for k, v in counts.items() if v > 0}
    dropped = 0
    while counts:
        total = sum(counts.values())
        limit = (total + 1) // 2
        big = max(counts, key=lambda k: counts[k])
        if counts[big] <= limit:
            break
        excess = counts[big] - limit
        counts[big] = limit
        if None in counts and big is not None:
            counts[None] += excess          # остаток - под гипотезы креаторов
        else:
            dropped += excess               # девать некуда: партия станет короче
    return counts, dropped


def _arrange(counts):
    """Порядок, в котором одинаковые гипотезы стоят как можно дальше друг от друга.

    На каждом шаге берется гипотеза, которой осталось больше всех и которая
    не стоит прямо перед этим местом. Это и дает максимальный разнос: наивный
    круг разваливается, как только группы не равны.
    """
    left = dict(counts)
    order, prev = [], object()
    while sum(left.values()) > 0:
        options = [k for k in left if left[k] > 0 and k != prev]
        if not options:                     # выбора нет - повтор вынужденный
            options = [k for k in left if left[k] > 0]
        pick = max(options, key=lambda k: (left[k], str(k)))
        order.append(pick)
        left[pick] -= 1
        prev = pick
    return order


def longest_run(labels):
    """Самая длинная серия одинаковых подряд. Нужна и коду, и проверкам."""
    best = run = 0
    prev = object()
    for x in labels:
        run = run + 1 if x == prev else 1
        prev = x
        best = max(best, run)
    return best


def propose(dictionary, plan, creators, start, size=BATCH_SIZE):
    """Черновик плана: каркас строк без содержания.

    Пустой словарь дает пустой план - сочинять партию не на чем, и молча
    выдумать ее было бы хуже, чем не выдать ничего.
    """
    picks = usable(dictionary)
    if not picks:
        return []
    creators = list(creators) or ["не назначен"]

    # 🔴 Один слот из трех - под гипотезу креатора. Она не соревнуется с гипотезой
    # из данных: у той всегда есть цифра в обосновании, и креатор проигрывает
    # всегда. Через два цикла креаторы перестанут предлагать.
    creator_slots = size // CREATOR_QUOTA
    data_slots = size - creator_slots

    # сколько гипотез уместится, чтобы на каждую было не меньше четырех
    room = max(1, data_slots // MIN_PER_HYPOTHESIS)
    chosen = picks[:min(len(picks), MAX_HYPOTHESES - 1, room)]
    if not chosen:
        chosen = picks[:1]

    # раздаем слоты поровну, остаток - первым по полезности
    per = {mech: data_slots // len(chosen) for mech, _ in chosen}
    for i in range(data_slots - sum(per.values())):
        per[chosen[i % len(chosen)][0]] += 1

    counts = {mech: per[mech] for mech, _ in chosen}
    facts = {mech: fact for mech, fact in chosen}
    counts[None] = creator_slots
    facts[None] = "квота креаторов: их гипотеза проверяется только в плане"

    counts, dropped = _rebalance(counts)
    order = [(mech, facts[mech]) for mech in _arrange(counts)]

    number = next_batch_number(plan)
    dates = _dates(start, len(order))
    # 🔴 Счетчик СВОЙ у каждой гипотезы, а не общий по дням.
    #
    # Замер на своем же коде 27.08: при общем счетчике и чередовании гипотез
    # четность склеивается - «папа» целиком достался Ксении, слоты креатора
    # целиком Спартаку. Это ровно тот confound, ради которого все затевалось:
    # разницу механик стало бы не отличить от разницы людей (из аккаунта
    # креатора пересылают вчетверо чаще, 2,11 против 0,50).
    seen = {}
    rows = []
    for i, ((mech, fact), date) in enumerate(zip(order, dates)):
        key = mech or CREATOR_SLOT
        seen[key] = seen.get(key, 0) + 1
        row = {c: "" for c in COLUMNS}
        row.update({
            "ID": "P%d-%02d" % (number, i + 1),
            "Креатор": creators[(seen[key] - 1) % len(creators)],
            "Механика": mech or "",
            "Гипотеза": mech or CREATOR_SLOT,
            "Факт-источник": fact,
            "Ожидаемый сигнал": "репосты на 1000 выше базовой медианы",
            "Дата в эфир": date,
        })
        rows.append(row)
    return rows


def write(sheet, rows):
    """Дописывает черновик в лист ПЛАН. Повтор безопасен: ID не дублируются."""
    have = {str(r.get("ID") or "").strip() for r in sheet.read()}
    added = 0
    for row in rows:
        if row["ID"] in have:
            continue
        sheet.append(row)
        have.add(row["ID"])
        added += 1
    return added


def summary(rows):
    """Что предложено - словами. Молчащий генератор не отличить от несделанного."""
    if not rows:
        return ("План следующей партии собрать не из чего: в словаре нет механик, "
                "по которым можно принимать решение. Нужны замеры.")
    from collections import Counter
    per = Counter(r["Гипотеза"] for r in rows)
    lines = ["📋 Черновик плана партии: %d роликов, %s - %s"
             % (len(rows), rows[0]["Дата в эфир"], rows[-1]["Дата в эфир"]), ""]
    for hyp, n in per.most_common():
        who = ", ".join(sorted({r["Креатор"] for r in rows if r["Гипотеза"] == hyp}))
        lines.append("· %s - %d роликов (%s)" % (hyp, n, who))
    lines += ["",
              "🔴 Механика внутри гипотезы повторяется НАМЕРЕННО: без четырех "
              "роликов решение принять не на чем. Но кадр и хук у них обязаны "
              "быть разными - замер 26.08: один и тот же текст поста дает разброс "
              "×50, план задается кадром, а не текстом. Одинаковые кадры внутри "
              "гипотезы сделают замер бессмысленным и ленту однообразной.",
              "",
              "Колонки «что в кадре» и «смысл хука» пустые намеренно: содержание "
              "машина не выдумывает."]

    # 🔴 Однообразие партии - это не сбой раскладки, а состояние словаря.
    # Молчать о нем нельзя: владелец увидит скучную ленту и не поймет, почему.
    top, top_n = per.most_common(1)[0]
    if top_n * 3 > len(rows):
        lines += ["",
                  "⚠️ На «%s» приходится %d роликов из %d - партия выйдет "
                  "однообразной. Причина не в расписании, а в словаре: пригодных "
                  "механик мало. Нужна новая гипотеза - Ваша или от креаторов."
                  % (top, top_n, len(rows))]
    runs = longest_run([r["Гипотеза"] for r in rows])
    if runs > 1:
        lines += ["",
                  "⚠️ Одна гипотеза стоит %d дня подряд - развести не удалось, "
                  "слотов не хватило." % runs]
    # 🔴 Предупреждение обязательное, а не вежливое. У слотов креатора механика
    # пустая, а словарь пустые механики пропускает - результат этих роликов
    # не попал бы в память петли вообще, и заметить это было бы нечем:
    # строки в плане есть, цифры по остальным считаются.
    if any(r["Гипотеза"] == CREATOR_SLOT for r in rows):
        lines.append("🔴 У слотов креатора механика пустая. Проставьте ее при "
                     "утверждении: без нее ролик не попадет в словарь механик, "
                     "и его результат петля не запомнит.")
    lines.append("План черновой и ничего не публикует, пока Вы его не утвердите.")
    return "\n".join(lines)


def maybe_propose(book, dictionary_data, today):
    """Предлагает следующую партию, если предыдущая полностью замерена.

    🔴 Условие «полностью замерена» строгое намеренно. Собрать план по половине
    партии - значит принять решение на половине данных, а потом уже не отличить,
    чем оно было вызвано. Лучше не предложить ничего, чем предложить рано.

    Возвращает (строки, причина). Строки пустые - причина словами, зачем-то же
    генератор промолчал.
    """
    plan = book.sheet("ПЛАН").read()
    if not plan:
        return [], "плана нет - предлагать следующую партию не от чего"

    measured = {str(r.get("ID") or "").strip() for r in book.sheet("МЕТРИКИ").read()
                if str(r.get("ID") or "").strip()}
    ids = [str(r.get("ID") or "").strip() for r in plan if str(r.get("ID") or "").strip()]
    current = "P%d-" % max_batch(ids)
    batch = [i for i in ids if i.startswith(current)]
    left = [i for i in batch if i not in measured]
    if left:
        return [], "партия %s замерена не вся: осталось %d из %d" % (
            current.rstrip("-"), len(left), len(batch))

    last_date = max((str(r.get("Дата в эфир") or "") for r in plan
                     if str(r.get("ID") or "").startswith(current)), default="")
    start = _next_day(last_date or today)
    creators = sorted({str(r.get("Креатор") or "").strip() for r in plan
                       if str(r.get("Креатор") or "").strip()})
    rows = propose(dictionary_data, plan, creators or ["не назначен"], start)
    if not rows:
        return [], "в словаре нет механик, по которым можно принимать решение"
    return rows, ""


def max_batch(ids):
    best = 0
    for i in ids:
        m = re.match(r"^P(\d+)-", i)
        if m:
            best = max(best, int(m.group(1)))
    return best


def _next_day(value):
    try:
        d = datetime.date(*[int(x) for x in str(value)[:10].split("-")])
    except Exception:
        d = datetime.date.today()
    return (d + datetime.timedelta(days=1)).isoformat()


def selftest():
    from tests.test_generator import selftest as run
    run()


if __name__ == "__main__":
    selftest()
