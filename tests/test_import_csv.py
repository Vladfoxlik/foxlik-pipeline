# -*- coding: utf-8 -*-
"""Проверка импортера выгрузки Business Suite.

Зачем модуль вообще: приемка 28.08 нашла, что памяти у петли нет и взяться ей
неоткуда. Словарь читает колонку «Репосты/1000», которую заполняет только замер
через Instagram API, а подписки API не отдает вовсе - их дает лишь ручной экспорт
CSV. Импортера этого экспорта не было ни одного, и квартал из 190 размеченных
роликов в петлю не попадал.

Проверяется не «код не упал», а то, чем импорт портит учет: дубли строк при
повторном запуске, подмена ручной разметки, молчаливая потеря роликов на кривых
строках, неверный счет показателя.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import import_csv as I  # noqa: E402

OUR = "myplayroom_shop"

HEAD = ('"ID публикации","ID аккаунта","Имя пользователя аккаунта",'
        '"Название аккаунта",Описание,"Длительность (с.)","Время публикации",'
        '"Постоянная ссылка","Тип публикации","Комментарий к данным",Дата,'
        'Просмотры,Охват,"Отметки ""Нравится""",Репосты,Подписки,Комментарии,'
        'Сохранения\n')


def line(pid, views, shares, follows, saved=0, comments=0, date="2026-08-01",
         descr="Обычный ролик", kind="Видео Instagram", account=OUR):
    return ('"%s","1","%s","FOXLIK","%s","15","2026-08-01 03:00",'
            '"https://www.instagram.com/reel/%s/","%s","",%s,%s,%s,10,%s,%s,%s,%s\n'
            % (pid, account, descr, pid, kind, date, views, views, shares, follows,
               comments, saved))


class FakeSheet:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]
        self.appends = 0

    def read(self):
        return [dict(r, _row=i + 2) for i, r in enumerate(self.rows)]

    def append(self, values):
        self.rows.append(dict(values))
        self.appends += 1

    def set_many(self, row, pairs):
        self.rows[row - 2].update(pairs)

    def set(self, row, column, value):
        self.rows[row - 2][column] = value


def read(text):
    return I.parse(io.StringIO(text))


def selftest():
    # --- 1. разбор: строки читаются, числа становятся числами ---
    rows = read(HEAD + line("aaa", 1000, 3, 2))
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["media_id"] == "aaa" and r["views"] == 1000 and r["shares"] == 3
    assert r["follows"] == 2 and r["rate"] == 3.0, r

    # --- 2. показатель считается как репосты на 1000 просмотров ---
    assert read(HEAD + line("b", 2000, 3, 0))[0]["rate"] == 1.5
    # ноль просмотров: делить не на что - показателя нет, но ролик не теряется
    z = read(HEAD + line("c", 0, 0, 0))
    assert len(z) == 1 and z[0]["rate"] is None, z

    # --- 3. мусор в числовой колонке не роняет импорт и не выдумывает ноль ---
    bad = HEAD + ('"d","1","u","F","текст","15","2026-08-01 03:00",'
                  '"https://x/","Видео Instagram","",2026-08-01,н/д,100,10,,,,\n')
    got = read(bad)
    assert len(got) == 1 and got[0]["views"] is None and got[0]["rate"] is None, got

    # --- 4. многострочное описание не рвет строку ---
    multi = HEAD + ('"e","1","u","F","первая строка\nвторая строка","15",'
                    '"2026-08-01 03:00","https://x/","Видео Instagram","",'
                    '2026-08-01,100,100,10,1,0,0,0\n')
    assert len(read(multi)) == 1

    # --- 5. BOM в начале файла не превращает первую колонку в мусор ---
    assert read("﻿" + HEAD + line("f", 100, 1, 0))[0]["media_id"] == "f"

    # --- 6. не-видео в замер механик не идет ---
    only_video = read(HEAD + line("g", 100, 1, 0, kind="Фото Instagram")
                      + line("h", 100, 1, 0))
    assert [x["media_id"] for x in only_video] == ["h"], only_video

    # --- 7. заливка истории: строки появляются в обоих листах ---
    pubs, mets = FakeSheet(), FakeSheet()
    n = I.load(read(HEAD + line("aaa", 1000, 3, 2) + line("bbb", 500, 0, 0)),
               pubs, mets)
    assert n == 2 and len(pubs.rows) == 2 and len(mets.rows) == 2, (n, pubs.rows)
    assert mets.rows[0]["Подписки"] == 2 and mets.rows[0]["Репосты/1000"] == 3.0

    # --- 8. 🔴 механику импорт НЕ выдумывает ---
    # Разметка приема - работа человека (ТЗ §5). Машина, угадывающая механику
    # по тексту описания, отравит словарь собственными догадками.
    assert pubs.rows[0]["Механика"] == "", pubs.rows[0]

    # --- 9. 🔴 повторный запуск не плодит дубли ---
    # Импорт руками, значит его запустят дважды. Дубль в ПУБЛИКАЦИЯХ удвоил бы
    # применения механики, дубль в МЕТРИКАХ перетер бы замер.
    same = read(HEAD + line("aaa", 1000, 3, 2) + line("bbb", 500, 0, 0))
    n2 = I.load(same, pubs, mets)
    assert n2 == 0 and len(pubs.rows) == 2 and len(mets.rows) == 2, (n2, pubs.rows)

    # --- 10. 🔴 ручная разметка переживает повторный импорт ---
    pubs.rows[0]["Механика"] = "папа"
    I.load(same, pubs, mets)
    assert pubs.rows[0]["Механика"] == "папа", "импорт затер разметку человека"

    # --- 11. свежие цифры по уже известному ролику обновляются, а не дублируются ---
    # Выгрузку снимают раз в две недели, показатели ролика за это время растут.
    grown = read(HEAD + line("aaa", 4000, 20, 9))
    I.load(grown, pubs, mets)
    assert len(mets.rows) == 2, mets.rows
    assert mets.rows[0]["Просмотры"] == 4000 and mets.rows[0]["Подписки"] == 9
    assert mets.rows[0]["Репосты/1000"] == 5.0

    # --- 12. 🔴 ролик, опубликованный нашим конвейером, узнается по Медиа ID ---
    # У него уже есть строка в ПУБЛИКАЦИЯХ с нашим ID плана и с механикой.
    # Импорт обязан подставить подписки ЕМУ, а не завести второй ролик-двойник.
    pubs = FakeSheet([{"ID": "P1-04", "Медиа ID": "zzz", "Механика": "папа",
                       "Площадка": "instagram", "Ссылка": "", "Дата": "2026-09-04"}])
    mets = FakeSheet([{"ID": "P1-04", "Просмотры": 900, "Репосты/1000": 1.1,
                       "Подписки": ""}])
    I.load(read(HEAD + line("zzz", 2000, 4, 6)), pubs, mets)
    assert len(pubs.rows) == 1, "импорт завел двойника вместо обновления"
    assert len(mets.rows) == 1 and mets.rows[0]["ID"] == "P1-04"
    assert mets.rows[0]["Подписки"] == 6 and mets.rows[0]["Репосты/1000"] == 2.0
    assert pubs.rows[0]["Механика"] == "папа"

    # --- 12а. 🔴 коллабы не смешиваются с нашими роликами ---
    # Замер 26.08: из аккаунта креатора пересылают вчетверо чаще (2,11 против 0,50).
    # Свалить их в одну медиану значит завысить показатель механики тем сильнее,
    # чем больше коллабов в партии, и приписать эту разницу приему.
    mixed = read(HEAD + line("our1", 1000, 1, 0)
                 + line("col1", 1000, 20, 5, account="mommyksy"))
    assert [x["media_id"] for x in mixed] == ["our1", "col1"], mixed
    pubs, mets = FakeSheet(), FakeSheet()
    st = I.load(mixed, pubs, mets, report=True, account=OUR)
    assert len(pubs.rows) == 1 and pubs.rows[0]["ID"] == "our1", pubs.rows
    assert st["коллабы"] == 1, st
    assert "коллаб" in I.summary(st).lower(), I.summary(st)

    # аккаунт не задан - берется самый частый в файле, и это сказано вслух
    st = I.load(read(HEAD + line("a", 100, 1, 0) + line("b", 100, 1, 0)
                     + line("c", 100, 1, 0, account="chuzhoy")),
                FakeSheet(), FakeSheet(), report=True)
    assert st["коллабы"] == 1 and st["аккаунт"] == OUR, st

    # --- 13. отчет называет числа, а не молчит ---
    pubs, mets = FakeSheet(), FakeSheet()
    text = I.summary(I.load(read(HEAD + line("q", 100, 1, 0)), pubs, mets, report=True))
    assert "1" in text and ("добав" in text or "новых" in text), text

    # --- 14. 🔴 отброшенные строки названы вслух ---
    # Молча потерянный ролик - это молча испорченный замер механики.
    stats = I.load(read(HEAD + line("w", 100, 1, 0)
                        + ('"","1","u","F","без id","15","2026-08-01 03:00",'
                           '"https://x/","Видео Instagram","",2026-08-01,'
                           '100,100,10,1,0,0,0\n')),
                   FakeSheet(), FakeSheet(), report=True)
    assert stats["без id"] == 1, stats
    assert "без" in I.summary(stats).lower(), I.summary(stats)

    print("import selftest OK: разбор с BOM и переносами, показатель, мусор "
          "не роняет, только видео, идемпотентность, разметка человека цела, "
          "рост цифр обновляется, наш ролик узнан по медиа-ID, потери названы")


if __name__ == "__main__":
    selftest()
