# -*- coding: utf-8 -*-
"""Проверка генератора плана партии.

Правила из ТЗ §4 и процедуры петли §5, ни одно не выдумано:
15 роликов на партию · минимум **4 ролика** на гипотезу · 2-3 активных гипотезы ·
**квота креаторов - один слот из трех** · креаторы перемешаны **внутри** каждой
гипотезы · гипотезы идут по кругу **по дням**.

🔴 Две проверки здесь важнее остальных, потому что ловят молчаливую порчу замера:

**Перемешивание креаторов.** Отдать гипотезу целиком одному человеку - и разницу
механик уже не отличить от разницы людей. Замер 26.08: из аккаунта креатора
пересылают вчетверо чаще, 2,11 против 0,50.

**Чередование по дням.** Отдать гипотезу целиком первой неделе - и разницу
не отличить от дрейфа раздачи и сезона.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generator as G  # noqa: E402


class FakeSheet:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]
        for i, r in enumerate(self.rows):
            r.setdefault("_row", i + 2)
        self.appended = []

    def read(self):
        return [dict(r) for r in self.rows]

    def append(self, values):
        self.appended.append(dict(values))
        row = dict(values)
        row["_row"] = len(self.rows) + 2
        self.rows.append(row)


def word(mech, count, median, status):
    return {mech: {"применений": count, "медиана": median, "подписки": 0,
                   "на каких роликах": "", "статус по метрикам": status}}


def dictionary(*parts):
    out = {}
    for p in parts:
        out.update(p)
    return out


CREATORS = ["Ксения", "Спартак"]


def selftest():
    # --- 1. опровергнутое в план не попадает, подтвержденное попадает ---
    d = dictionary(word("папа", 4, 3.0, "подтверждена"),
                   word("название", 4, 0.2, "опровергнута"),
                   word("вопрос", 2, 1.0, "копим, 2 из 4"))
    rows = G.propose(d, plan=[], creators=CREATORS, start="2026-09-22")
    mechs = {r["Механика"] for r in rows}
    assert "название" not in mechs, "опровергнутая механика снова в плане: %s" % mechs
    assert "папа" in mechs and "вопрос" in mechs, mechs

    # --- 2. ровно 15 строк, по одной на день ---
    assert len(rows) == G.BATCH_SIZE == 15, len(rows)
    dates = [r["Дата в эфир"] for r in rows]
    assert len(set(dates)) == 15, "две публикации в один день"
    assert dates == sorted(dates), "даты вразнобой"

    # --- 3. на каждую гипотезу не меньше четырех роликов ---
    # меньше - решение принять нельзя, и слоты потрачены впустую
    from collections import Counter
    per_hyp = Counter(r["Гипотеза"] for r in rows)
    assert all(n >= G.MIN_PER_HYPOTHESIS for n in per_hyp.values()), per_hyp
    assert 2 <= len(per_hyp) <= 3, "активных гипотез должно быть 2-3: %s" % per_hyp

    # --- 4-5. 🔴 ГЛАВНОЕ: проверяем на ВСЕХ формах партии, а не на удобной ---
    #
    # Замер на своем же коде 27.08: обе проверки ниже проходили, пока гоняли
    # только сбалансированный случай из трех равных групп. На одной механике
    # вылезли сразу две дыры - пять роликов подряд в конце и склеенная четность,
    # из-за которой каждая гипотеза досталась одному человеку целиком.
    shapes = {
        "одна механика": dictionary(word("папа", 9, 3.0, "подтверждена")),
        "две, разной силы": dictionary(word("папа", 9, 3.0, "подтверждена"),
                                       word("вопрос", 2, 1.0, "копим, 2 из 4")),
        "три, все копятся": dictionary(word("а", 1, 1.0, "копим, 1 из 4"),
                                       word("б", 2, 1.0, "копим, 2 из 4"),
                                       word("в", 3, 1.0, "копим, 3 из 4")),
        "исходная": d,
    }
    for name, shape in shapes.items():
        got = G.propose(shape, plan=[], creators=CREATORS, start="2026-09-22")
        assert len(got) == G.BATCH_SIZE, (name, len(got))
        seq = [r["Гипотеза"] for r in got]

        # 🔴 ни одна гипотеза не стоит два дня подряд - иначе креаторы снимут
        # похожее подряд, а лента станет однообразной
        assert G.longest_run(seq) == 1, (
            "«%s»: гипотеза идет %d дня подряд" % (name, G.longest_run(seq)))

        # 🔴 внутри КАЖДОЙ гипотезы оба креатора, иначе разницу механик
        # не отличить от разницы людей
        for hyp in set(seq):
            who = {r["Креатор"] for r in got if r["Гипотеза"] == hyp}
            assert who == set(CREATORS), (
                "«%s»: гипотеза «%s» целиком у %s" % (name, hyp, who))

        # даты по-прежнему по одной на день
        assert len({r["Дата в эфир"] for r in got}) == G.BATCH_SIZE, name

    # длиннейшая серия считается честно - проверка самой проверки
    assert G.longest_run(["а", "б", "б", "б", "а"]) == 3
    assert G.longest_run([]) == 0 and G.longest_run(["а"]) == 1

    order = [r["Гипотеза"] for r in rows]

    # --- 6. квота креаторов: один слот из трех ---
    own = [r for r in rows if r["Гипотеза"] == G.CREATOR_SLOT]
    assert len(own) >= len(rows) // 3, "квота креаторов меньше трети: %d" % len(own)

    # --- 7. 🔴 машина не выдумывает содержание ---
    for r in rows:
        assert r["Что в кадре"] == "", "машина сочинила кадр: %r" % r["Что в кадре"]
        assert r["Смысл хука"] == "", "машина сочинила хук: %r" % r["Смысл хука"]
        assert r["Факт-источник"], "строка без основания: %s" % r
        assert r["Ожидаемый сигнал"], r

    # --- 8. выгоревшая механика сама в план НЕ возвращается ---
    d2 = dictionary(word("папа", 6, 1.0, "не решено · выгорела: 3 подряд ниже базовой"),
                    word("вопрос", 2, 1.0, "копим, 2 из 4"),
                    word("новая", 1, 1.0, "копим, 1 из 4"))
    rows2 = G.propose(d2, plan=[], creators=CREATORS, start="2026-09-22")
    assert "папа" not in {r["Механика"] for r in rows2}, \
        "выгоревшая механика вернулась без решения владельца"

    # --- 9. номера продолжают прошлую партию, а не начинают с нуля ---
    prev = [{"ID": "P1-%02d" % i} for i in range(1, 16)]
    rows3 = G.propose(d, plan=prev, creators=CREATORS, start="2026-09-22")
    ids = [r["ID"] for r in rows3]
    assert ids[0] == "P2-01" and ids[-1] == "P2-15", ids[:2] + ids[-1:]
    assert not (set(ids) & {r["ID"] for r in prev}), "номера столкнулись с прошлой партией"

    # --- 10. запись идемпотентна: повтор не плодит строки ---
    sheet = FakeSheet(prev)
    G.write(sheet, rows3)
    G.write(sheet, rows3)
    assert len(sheet.appended) == 15, "повторный запуск удвоил план: %d" % len(sheet.appended)

    # --- 11. 🔴 сводка предупреждает про пустую механику у слотов креатора ---
    # Без механики ролик не попадет в словарь: dictionary.build пропускает
    # пустые. То есть результат слотов креатора был бы невидим для петли,
    # и заметить это было бы нечем - строки в плане есть, цифры считаются.
    text = G.summary(rows)
    assert "механик" in text.lower() and "словар" in text.lower(), text

    # --- 12. пустой словарь не роняет, а честно говорит, что решать не на чем ---
    empty = G.propose({}, plan=[], creators=CREATORS, start="2026-09-22")
    assert empty == [], "из пустого словаря сочинился план"

    # --- 13. один креатор - вырожденный случай, но не падение ---
    solo = G.propose(d, plan=[], creators=["Ксения"], start="2026-09-22")
    assert len(solo) == 15 and {r["Креатор"] for r in solo} == {"Ксения"}

    # --- 14. 🔴 недомеренная партия НЕ порождает следующую ---
    # Собрать план по половине партии - принять решение на половине данных,
    # а потом уже не отличить, чем оно было вызвано. Лучше промолчать.
    class FakeBook:
        def __init__(self, plan, metrics):
            self.sheets = {"ПЛАН": FakeSheet(plan), "МЕТРИКИ": FakeSheet(metrics)}

        def sheet(self, title):
            return self.sheets[title]

    plan_rows = [{"ID": "P1-%02d" % i, "Креатор": CREATORS[i % 2],
                  "Дата в эфир": "2026-09-%02d" % (3 + i)} for i in range(1, 16)]
    half = [{"ID": "P1-%02d" % i} for i in range(1, 8)]
    rows4, why = G.maybe_propose(FakeBook(plan_rows, half), d, "2026-09-22")
    assert rows4 == [] and "не вся" in why, (rows4, why)

    # замерена целиком - предложение появляется, и стартует со следующего дня
    full = [{"ID": "P1-%02d" % i} for i in range(1, 16)]
    rows5, why5 = G.maybe_propose(FakeBook(plan_rows, full), d, "2026-09-22")
    assert rows5 and not why5, why5
    assert rows5[0]["ID"].startswith("P2-"), rows5[0]
    last_air = max(r["Дата в эфир"] for r in plan_rows)
    assert rows5[0]["Дата в эфир"] > last_air, "новая партия наезжает на старую"

    # пустой план - молчим, а не сочиняем партию из ничего
    rows6, why6 = G.maybe_propose(FakeBook([], []), d, "2026-09-22")
    assert rows6 == [] and "плана нет" in why6, why6

    print("generator selftest OK: опровергнутое не берется, 15 строк по дням, "
          "минимум 4 на гипотезу, на ВСЕХ формах партии: ноль повторов подряд "
          "и оба креатора внутри каждой гипотезы, "
          "квота креаторов, содержание не выдумывается, выгоревшее не возвращается, "
          "номера продолжают партию, повтор не плодит строк, "
          "сводка предупреждает про пустую механику, "
          "недомеренная партия не порождает следующую")


if __name__ == "__main__":
    selftest()
