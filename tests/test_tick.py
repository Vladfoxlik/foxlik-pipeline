# -*- coding: utf-8 -*-
"""Проверка такта целиком: вся машина состояний на подставных площадках.

Сеть не нужна. Проверяется не «код не упал», а то, чем конвейер портит партию:
двойная публикация, потерянное нажатие, зависшая строка, промах кнопки по чужому
ролику, секрет в тексте ошибки.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tick as T  # noqa: E402

TODAY = datetime.date(2026, 9, 4)


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows                 # список словарей без _row
        self.writes = []

    def read(self):
        out = []
        for i, row in enumerate(self.rows):
            item = dict(row)
            item["_row"] = i + 2
            out.append(item)
        return out

    def set(self, row, column, value):
        self.rows[row - 2][column] = value
        self.writes.append((row, column, value))

    def set_many(self, row, pairs):
        for column, value in pairs.items():
            self.set(row, column, value)

    def append(self, values):
        self.rows.append(dict(values))
        self.writes.append(("append", values))


class FakeBot:
    def __init__(self, presses=()):
        self.presses = list(presses)
        self.cards, self.notes, self.locks = [], [], []
        self.confirmed = 0
        self.order = []

    def get_presses(self):
        self.order.append("read")
        return self.presses

    def confirm(self):
        self.order.append("confirm")
        self.confirmed += 1
        return 1

    def ask_review(self, row_id, title, file_url, comment="", chat_id=None):
        self.cards.append({"row_id": row_id, "title": title, "url": file_url})
        return 100 + len(self.cards)

    def notify(self, text, chat_id=None):
        self.notes.append(text)
        return 1

    def lock(self, chat_id, message_id, verdict):
        self.locks.append(verdict)


class FakeDrive:
    def __init__(self, fail=None):
        self.fail = fail
        self.fetched = []

    def fetch(self, link, max_mb=None):
        if self.fail:
            raise RuntimeError(self.fail)
        self.fetched.append(link)
        return "ролик.mp4", b"\x00" * 100


class FakeCloud:
    def __init__(self):
        self.uploaded, self.destroyed = [], []

    def upload(self, name, content, public_id=None, now=None):
        self.uploaded.append(name)
        return "pid1", "https://res.cloudinary.com/foxlik/pid1.mp4"

    def destroy(self, public_id, now=None):
        self.destroyed.append(public_id)
        return "ok"


class FakeIG:
    def __init__(self, result=("M1", "https://instagram.com/reel/M1")):
        self.result = result
        self.posted = []

    def post_reel(self, url, caption=""):
        self.posted.append((url, caption))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeVK:
    def __init__(self):
        self.posted = []

    # подпись повторяет настоящую vk.Vk.publish - иначе проверка ничего не стоит
    def publish(self, filename, content, name="", message=""):
        self.posted.append(message)
        return "https://vk.com/wall-777_1"


def row(status="", plan="P26-03 · папа собирает столик", date="", file_="link1",
        time_="2026-09-03 14:22", comment=""):
    return {T.COL_TIME: time_, T.COL_PLAN: plan, T.COL_FILE: file_,
            T.COL_COMMENT: comment, T.COL_STATUS: status, T.COL_DATE: date,
            T.COL_REASON: ""}


def build(rows, presses=(), ig=None, disk=None, today=TODAY):
    sheet = FakeSheet(rows)
    pipe = T.Pipeline(bot=FakeBot(presses), sheet=sheet, pubs=FakeSheet([]),
                      disk=disk or FakeDrive(), ig=ig if ig is not None else FakeIG(),
                      vkontakte=FakeVK(), cloud=FakeCloud(), today=today)
    return pipe, sheet


def press(key, action="ok"):
    return {"update_id": 1, "callback_id": "c1", "action": action,
            "row_id": key, "chat_id": 1, "message_id": 500}


def selftest():
    # --- 1. новая сдача уходит владельцу и встает на приемку ---
    pipe, sheet = build([row()])
    pipe.run()
    assert len(pipe.bot.cards) == 1, "новая сдача обязана уйти владельцу"
    assert sheet.rows[0][T.COL_STATUS] == T.ON_REVIEW
    assert pipe.bot.cards[0]["title"].startswith("P26-03")

    # --- 2. пустая строка формы не тревожит владельца ---
    pipe, sheet = build([row(file_="")])
    pipe.run()
    assert not pipe.bot.cards, "строка без файла - форма еще дописывает"

    # --- 3. нажатие «годен» одобряет и ставит дату ---
    r = row(status=T.ON_REVIEW)
    pipe, sheet = build([r])
    key = T.Pipeline.row_key(dict(r, _row=2))
    pipe.bot.presses = [press(key)]
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED, sheet.rows[0][T.COL_STATUS]
    assert pipe.bot.locks == ["✅ Годен"]

    # --- 4. нажатие «переснять» не публикует ---
    r = row(status=T.ON_REVIEW)
    pipe, sheet = build([r])
    pipe.bot.presses = [press(T.Pipeline.row_key(dict(r, _row=2)), "no")]
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.RESHOOT
    assert not pipe.ig.posted, "отклоненное не публикуется"

    # --- 5. 🔴 подтверждение нажатий идет ПОСЛЕ записи статусов ---
    assert pipe.bot.order == ["read", "confirm"], pipe.bot.order
    assert pipe.bot.confirmed == 1

    # --- 6. 🔴 одна публикация за такт, даже если созрело три ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="A"),
                         row(status=T.APPROVED, date="2026-09-02", plan="B"),
                         row(status=T.APPROVED, date="2026-09-03", plan="C")])
    pipe.run()
    published = [x[T.COL_STATUS] for x in sheet.rows]
    assert published == [T.PUBLISHED, T.APPROVED, T.APPROVED], published
    assert len(pipe.ig.posted) == 1

    # --- 7. 🔴 второй такт не публикует то же самое повторно ---
    pipe2 = T.Pipeline(bot=FakeBot(), sheet=sheet, pubs=FakeSheet([]),
                       disk=FakeDrive(), ig=FakeIG(), vkontakte=FakeVK(),
                       cloud=FakeCloud(), today=TODAY)
    pipe2.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED
    assert len(pipe2.ig.posted) == 1 and pipe2.ig.posted[0][1] == "B", \
        "второй такт обязан взять следующую, а не ту же"

    # --- 8. дата в будущем ждет своего дня ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-10")])
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.APPROVED
    assert not pipe.ig.posted
    assert "публиковать нечего" in " ".join(pipe.log)

    # --- 9. 🔴 зависшее в ПУБЛИКУЕТСЯ подбирается и не молчит ---
    pipe, sheet = build([row(status=T.PUBLISHING, plan="завис")])
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.FAILED
    assert "завис" in " ".join(pipe.bot.notes), pipe.bot.notes
    assert not pipe.ig.posted, "зависшее нельзя публиковать вслепую"

    # --- 10. 🔴 сдвиг листа: кнопка не смеет попасть в чужой ролик ---
    r = row(status=T.ON_REVIEW, plan="настоящий")
    pipe, sheet = build([r])
    stale = T.Pipeline.row_key(dict(row(time_="другое время"), _row=2))
    pipe.bot.presses = [press(stale)]
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.ON_REVIEW, "статус трогать нельзя"
    assert any("сдвинули" in n for n in pipe.bot.notes), pipe.bot.notes

    # --- 11. повторное нажатие по уже опубликованной строке ничего не откатывает ---
    r = row(status=T.PUBLISHED)
    pipe, sheet = build([r])
    pipe.bot.presses = [press(T.Pipeline.row_key(dict(r, _row=2)))]
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED

    # --- 12. 🔴 ошибка публикации: статус, причина, сигнал, такт не падает ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01")],
                        disk=FakeDrive(fail="Диск не отдал файл"))
    log = pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.FAILED
    assert "Диск не отдал файл" in sheet.rows[0][T.COL_REASON]
    assert any("Не опубликовалось" in n for n in pipe.bot.notes)
    assert log, "такт обязан вернуть журнал, а не умереть"

    # --- 13. 🔴 секрет не попадает ни в таблицу, ни в сообщение владельцу ---
    leak = "HTTP 400 на https://api.telegram.org/bot123456:AAHsecretTOKENvalue/x"
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01")],
                        disk=FakeDrive(fail=leak))
    log = pipe.run()
    assert "AAHsecretTOKENvalue" not in sheet.rows[0][T.COL_REASON], "секрет утек в таблицу"
    assert "AAHsecretTOKENvalue" not in " ".join(pipe.bot.notes), "секрет утек владельцу"
    # 🔴 журнал такта уходит в лог GitHub Actions - там секрета быть тоже не должно.
    # Эту дыру проверка сначала пропустила, ее увидели глазами в выводе.
    assert "AAHsecretTOKENvalue" not in " ".join(log), "секрет утек в лог такта"

    # --- 19. 🔴 репозиторий публичный: в лог не идут ни тексты ошибок, ни названия роликов ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01",
                             plan="P1-04 · папа рисует кашу на световом столе")],
                        disk=FakeDrive(fail="Диск вернул 403 для файла отчет_клиента.mp4"))
    log = " ".join(pipe.run())
    assert "403" not in log and "отчет_клиента" not in log, "тело ошибки не должно идти в лог"
    assert "папа рисует кашу" not in log, "название ролика не должно идти в лог"
    assert "ОШИБКА" in log, "но сам факт отказа в логе быть обязан"
    # владелец и таблица получают полный текст - туда чужой не смотрит
    assert "отчет_клиента" in " ".join(pipe.bot.notes)
    assert "403" in sheet.rows[0][T.COL_REASON]

    # успешная публикация тоже не называет ролик в логе
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01",
                             plan="P1-09 · вопрос про сад")])
    log = " ".join(pipe.run())
    assert "вопрос про сад" not in log, "название ролика не должно идти в лог"
    assert "опубликована" in log

    # --- 14. 🔴 перевалка Cloudinary стирается даже когда Instagram упал ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01")],
                        ig=FakeIG(result=RuntimeError("контейнер ERROR")))
    pipe.run()
    assert pipe.cloud.destroyed == ["pid1"], "файл на перевалке нельзя оставлять"
    assert sheet.rows[0][T.COL_STATUS] == T.FAILED

    # --- 15. недозревший контейнер - тоже отказ, а не тихий успех ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01")],
                        ig=FakeIG(result=None))
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.FAILED
    assert "не дозрел" in sheet.rows[0][T.COL_REASON]

    # --- 16. успешная публикация пишет ссылки в лист ПУБЛИКАЦИИ ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="P26-07")])
    pipe.run()
    platforms = {x["Площадка"]: x["Ссылка"] for x in pipe.pubs.rows}
    assert set(platforms) == {"instagram", "vk"}, platforms
    assert pipe.pubs.rows[0]["ID"] == "P26-07"
    assert any("Опубликовано" in n for n in pipe.bot.notes)

    # --- 17. разбор даты в тех видах, что отдает Google ---
    assert T._as_date("2026-09-04") == TODAY
    assert T._as_date("04.09.2026") == TODAY
    assert T._as_date("2026-09-04 10:00:00") == TODAY
    assert T._as_date("") is None and T._as_date("скоро") is None

    # --- 18. отпечаток строки меняется вместе с файлом ---
    a = T.Pipeline.row_key(dict(row(), _row=2))
    b = T.Pipeline.row_key(dict(row(file_="link2"), _row=2))
    assert a != b and a.startswith("2:") and len(a) < 20

    # --- 19. имена колонок сходятся с тем, что реально создала форма ---
    # 🔴 Замер 27.08: связали форму с таблицей и прочитали строку заголовков.
    # Google назвал колонку почты «Адрес электронной почты», а код ждал
    # «Электронная почта» - и промахнулся бы молча: `row.get` вернул бы пустоту,
    # креатор в ПУБЛИКАЦИЯХ остался бы пустым, и вопрос «у кого лучше заходит»
    # снова остался бы без ответа. Список ниже - дословный, не переписывать
    # по памяти: он верен ровно настолько, насколько снят с живой формы.
    FORM_HEADER = ["Отметка времени", "Адрес электронной почты",
                   "Строка плана", "Файл", "Комментарий"]
    for name in (T.COL_TIME, T.COL_EMAIL, T.COL_PLAN, T.COL_FILE, T.COL_COMMENT):
        assert name in FORM_HEADER, (
            "код ждет колонку %r, а форма создала %s" % (name, FORM_HEADER))

    # --- 20. почта сдающего доезжает до ПУБЛИКАЦИЙ как креатор ---
    # Без этого поле «Креатор» молча пустое, и петля теряет свой главный срез.
    pipe, sheet = build([dict(row(status=T.APPROVED, date="2026-09-01",
                                  plan="P26-09"),
                              **{T.COL_EMAIL: "ksenia@gmail.com"})])
    pipe.run()
    assert pipe.pubs.rows, "ничего не опубликовалось"
    assert all(x["Креатор"] == "ksenia@gmail.com" for x in pipe.pubs.rows),         pipe.pubs.rows

    print("tick selftest OK: 21 проверка - полный путь, одна публикация за такт, "
          "идемпотентность, зависшее, сдвиг листа, ошибки, секреты, перевалка, "
          "публичный лог не выдает содержание, имена колонок сняты с живой формы")


if __name__ == "__main__":
    selftest()
