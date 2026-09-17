# -*- coding: utf-8 -*-
"""Проверки отчетов владельцу и напоминаний креатору (решение В48, 17.09).

Запуск из pipeline/:  python tests/test_reports.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reports as R  # noqa: E402
from tick import Pipeline, MSK  # noqa: E402


class FakeSheet:
    def __init__(self, rows=(), fail=False):
        self.rows = [dict(r) for r in rows]
        self.fail = fail

    def read(self):
        if self.fail:
            raise RuntimeError("Google не ответил")
        for i, r in enumerate(self.rows):
            r["_row"] = i + 2
        return [dict(r) for r in self.rows]

    def set(self, row, column, value):
        self.rows[row - 2][column] = value

    def append(self, values):
        self.rows.append(dict(values))


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []          # (chat_id, текст)
        self.fail = fail

    def notify(self, text, chat_id=None):
        if self.fail:
            raise RuntimeError("Telegram не ответил")
        self.sent.append((chat_id, text))
        return 1


GROUP = "-100500"


def plan_row(pid, day, creator="Ксения", status="УТВЕРЖДЕН"):
    return {"ID": pid, "Креатор": creator, "Дата в эфир": day, "Статус": status}


def sub(pid, status, date=""):
    return {"Строка плана": "%s · Ксения · сюжет" % pid, "Статус": status,
            "Дата публикации": date, "Файл": "f"}


def make(now, plan, subs=(), pubs=(), settings=(), bot=None, plan_fail=False):
    rep = R.Reports(plan=FakeSheet(plan, fail=plan_fail), subs=FakeSheet(subs),
                    pubs=FakeSheet(pubs), settings=FakeSheet(settings),
                    bot=bot or FakeBot(), group_chat_id=GROUP,
                    key_of=Pipeline.plan_key, now=now)
    return rep


def at(day, hour, minute=0):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=MSK)


def owner_texts(rep):
    return [t for c, t in rep.bot.sent if c is None]


def group_texts(rep):
    return [t for c, t in rep.bot.sent if c == GROUP]


def selftest():
    план = [plan_row("W37-11", "2026-09-18"), plan_row("W37-12", "2026-09-18"),
            plan_row("W37-14", "2026-09-19"), plan_row("W37-07", "2026-09-15")]

    # --- 1. вечерняя сводка: что завтра выходит и чего не хватает ---
    rep = make(at(17, 20, 3), план,
               subs=[sub("W37-11", "ОДОБРЕН", "2026-09-18"),
                     sub("W37-07", "ОДОБРЕН", "2026-09-17")])
    rep.run()
    сводки = [t for t in owner_texts(rep) if u"Завтра" in t]
    assert len(сводки) == 1, owner_texts(rep)
    s = сводки[0]
    assert u"18.09" in s, s
    assert u"W37-11" in s and u"W37-12" in s, s
    строка_12 = [x for x in s.splitlines() if u"W37-12" in x]
    assert строка_12 and u"не сдан" in строка_12[0], s
    assert u"W37-07" in s, u"опоздавшие, ждущие окна, должны быть видны: %s" % s
    assert u"W37-14" not in s, u"послезавтра в сводку на завтра не входит: %s" % s

    # --- 2. сводка приходит один раз в день, а не каждый такт ---
    настройки = rep.settings.rows
    rep2 = make(at(17, 20, 8), план, settings=настройки)
    rep2.run()
    assert not [t for t in owner_texts(rep2) if u"Завтра" in t], owner_texts(rep2)
    # и назавтра приходит снова
    rep3 = make(at(18, 20, 1), план, settings=настройки)
    rep3.run()
    assert [t for t in owner_texts(rep3) if u"Завтра" in t], owner_texts(rep3)

    # --- 3. до 20:00 сводки нет ---
    rep = make(at(17, 19, 55), план)
    rep.run()
    assert not [t for t in owner_texts(rep) if u"Завтра" in t], owner_texts(rep)

    # --- 4. 🔴 на завтра в плане пусто - это тревога, а не тишина ---
    rep = make(at(19, 20, 0), план)
    rep.run()
    s = [t for t in owner_texts(rep) if u"Завтра" in t]
    assert s and u"🔴" in s[0] and u"ничего" in s[0], s

    # --- 5. напоминание креатору в полдень накануне эфира, только о несданном ---
    rep = make(at(17, 12, 2), план, subs=[sub("W37-11", "ОДОБРЕН", "2026-09-18")])
    rep.run()
    г = group_texts(rep)
    assert len(г) == 1, rep.bot.sent
    assert u"W37-12" in г[0] and u"W37-11" not in г[0], г[0]
    assert u"Ксения" in г[0], г[0]
    assert u" ты " not in (" " + г[0].lower() + " "), u"к креатору на «Вы»: %s" % г[0]
    # повтор в том же дне не шлется
    rep2 = make(at(17, 12, 7), план, settings=rep.settings.rows,
                subs=[sub("W37-11", "ОДОБРЕН", "2026-09-18")])
    rep2.run()
    assert not group_texts(rep2), rep2.bot.sent
    # все сдано - напоминать нечего
    rep = make(at(17, 12, 2), план, subs=[sub("W37-11", "ОДОБРЕН"),
                                          sub("W37-12", "ОПУБЛИКОВАН")])
    rep.run()
    assert not group_texts(rep), rep.bot.sent
    # ДУБЛЬ сдачей не считается
    rep = make(at(17, 12, 2), план, subs=[sub("W37-11", "ОДОБРЕН"), sub("W37-12", "ДУБЛЬ")])
    rep.run()
    assert group_texts(rep) and u"W37-12" in group_texts(rep)[0], rep.bot.sent

    # --- 6. итог недели: воскресенье 10:00, за неделю плана вс-сб, что кончилась ---
    неделя = [plan_row("W37-%02d" % i, "2026-09-%02d" % (13 + (i - 1) // 2))
              for i in range(1, 15)]            # 13-19.09 (вс-сб), по два в день
    сдачи = [sub("W37-01", "ОПУБЛИКОВАН", "2026-09-13"),
             sub("W37-02", "ОПУБЛИКОВАН", "2026-09-16"),    # опоздал
             sub("W37-03", "ОДОБРЕН", "2026-09-19")]
    учет = [{"ID": "W37-01", "Дата": "2026-09-13", "Площадка": "instagram",
             "Ссылка": "https://www.instagram.com/reel/A/"},
            {"ID": "W37-02", "Дата": "2026-09-16", "Площадка": "instagram",
             "Ссылка": "https://www.instagram.com/reel/B/"}]
    rep = make(at(20, 10, 4), неделя, subs=сдачи, pubs=учет)
    rep.run()
    итог = [t for t in owner_texts(rep) if u"Итог недели" in t]
    assert len(итог) == 1, owner_texts(rep)
    t = итог[0]
    assert u"13.09" in t and u"19.09" in t, u"неделя плана вс-сб: %s" % t
    assert u"вышло 2 из 14" in t, t
    assert u"опоздал" in t.lower() and u"W37-02" in t, t
    assert u"reel/A/" in t, u"ссылки на вышедшие: %s" % t
    assert u"не сдано" in t.lower() and u"W37-14" in t, u"несданные видны: %s" % t
    # в субботу итога нет
    rep = make(at(19, 20, 4), неделя, subs=сдачи, pubs=учет)
    rep.run()
    assert not [x for x in owner_texts(rep) if u"Итог недели" in x]

    # --- 7. 🔴 сбой отправки: отметка снимается, следующий такт пробует снова ---
    rep = make(at(17, 20, 3), план, bot=FakeBot(fail=True))
    rep.run()
    rep2 = make(at(17, 20, 8), план, settings=rep.settings.rows)
    rep2.run()
    assert [t for t in owner_texts(rep2) if u"Завтра" in t], \
        u"упавшая отправка съела сводку дня: %s" % rep.settings.rows

    # --- 8. 🔴 план не прочитался - молчим и не ставим отметку (не врем «пусто») ---
    rep = make(at(17, 20, 3), план, plan_fail=True)
    rep.run()
    assert not owner_texts(rep), u"при сбое чтения нельзя слать «ничего нет»: %s" % rep.bot.sent
    assert not rep.settings.rows, rep.settings.rows

    # --- 9. черновики плана в отчеты не попадают ---
    rep = make(at(17, 20, 3), [plan_row("W38-01", "2026-09-18", status="ЧЕРНОВИК")])
    rep.run()
    s = [t for t in owner_texts(rep) if u"Завтра" in t]
    assert s and u"W38-01" not in s[0], s

    print("reports selftest OK: 9 проверок - сводка на завтра раз в день после 20:00, "
          "пустой завтрашний день - тревога, напоминание креатору в полдень только "
          "о несданном и на «Вы», итог недели по воскресеньям со ссылками, сбой "
          "отправки не съедает отчет, сбой чтения не выдается за «пусто», черновики мимо")


if __name__ == "__main__":
    selftest()
