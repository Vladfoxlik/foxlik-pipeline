# -*- coding: utf-8 -*-
"""Проверка такта целиком: вся машина состояний на подставных площадках.

Сеть не нужна. Проверяется не «код не упал», а то, чем конвейер портит партию:
двойная публикация, потерянное нажатие, зависшая строка, промах кнопки по чужому
ролику, секрет в тексте ошибки.
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tick as T  # noqa: E402

TODAY = datetime.date(2026, 9, 4)


class FakeSheet:
    def __init__(self, rows, header=None):
        self.rows = rows                 # список словарей без _row
        self.header = header             # None - лист без схемы (ПЛАН в проверках)
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
        # 🔴 Настоящий лист берет ТОЛЬКО колонки из своего заголовка
        # (lib/sheets.Sheet.append: row = [values.get(n) for n in header]).
        # Пока заглушка складывала весь словарь, проверки проходили на значении,
        # которого в живой таблице не появилось бы: колонки «Соответствие» в листе
        # ПУБЛИКАЦИИ нет, и отметка об отступлении пропала бы молча. Найдено 30.08.
        if self.header is not None:
            values = dict((n, values.get(n, "")) for n in self.header)
        self.rows.append(dict(values))
        self.writes.append(("append", values))


class FakeBot:
    def __init__(self, presses=()):
        self.presses = list(presses)
        self.cards, self.notes, self.locks = [], [], []
        self.chats = []
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
        self.chats.append(chat_id)
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


class FakePmp:
    """Подставной Postmypost. Подписи повторяют lib/postmypost.Postmypost."""

    def __init__(self, fail=None):
        self.fail = fail
        self.posted = []          # (имя файла, подпись, аккаунты, время)
        self.posts = None         # чем ответит get_publication_posts, если задано

    def post_video_bytes(self, content_bytes, filename, content, account_ids, post_at,
                         черновик=False, details=None):
        if self.fail:
            raise self.fail
        self.posted.append((filename, content, list(account_ids), post_at, черновик,
                            details))
        return 31879606           # id публикации в сервисе, снят живым прогоном 31.08

    def get_publication_posts(self, pub_id):
        if self.posts is not None:
            return self.posts
        # форма как у lib/postmypost.get_publication_posts (поля - по доке)
        return [{"account_id": 2248551, "post_status": 1,
                 "link": "https://www.instagram.com/reel/LIVE%s/" % pub_id},
                {"account_id": 2248535, "post_status": 1,
                 "url": "https://vk.com/wall-1_%s" % pub_id}]


# Аккаунты сняты живьем 31.08. 🔴 Поле называется `chanel_id` - с одной «n»:
# так в их API, и справочник наш это писал иначе. Промах был бы молчаливым.
PMP_ACCOUNTS = [{"id": 2248535, "chanel_id": 2, "name": "FOXLIK"},
                {"id": 2248551, "chanel_id": 1, "name": "myplayroom_shop"}]


def row(status="", plan="P26-09 · папа собирает столик", date="", file_="link1",
        time_="2026-09-03 14:22", comment=""):
    return {T.COL_TIME: time_, T.COL_PLAN: plan, T.COL_FILE: file_,
            T.COL_COMMENT: comment, T.COL_STATUS: status, T.COL_DATE: date,
            T.COL_REASON: ""}


def build(rows, presses=(), ig=None, disk=None, today=TODAY, plan=False,
          pmp=None, accounts=(), offline=(), pubs_rows=None, вхолостую=False):
    sheet = FakeSheet(rows)
    if plan is False:
        # 🔴 С 31.08 такт берет подпись к посту из листа ПЛАН и без нее публикацию
        # откладывает. Поэтому умолчание собирается из ТЕХ ЖЕ строк, что и сдачи:
        # в жизни план всегда есть и всегда согласован, а тест без него проверял бы
        # отказ вместо публикации и молча ослаб бы.
        ids = []
        for r in rows:
            key = T.Pipeline.plan_key(r.get(T.COL_PLAN))
            if key and key not in ids:
                ids.append(key)
        plan = FakeSheet([{"ID": x, "Механика": "папа",
                           "Описание к посту": "текст поста"} for x in ids]
                         or [{"ID": "P26-09", "Механика": "папа",
                              "Описание к посту": "текст поста"}])
    # 🔴 Лист ПУБЛИКАЦИИ берется со схемой из setup.LAYOUT, а не пустым: он и
    # в жизни создан по ней, и колонка, которой там нет, молча пропадает.
    import setup as S
    pipe = T.Pipeline(bot=FakeBot(presses), sheet=sheet,
                      pubs=FakeSheet(list(pubs_rows or []),
                                     header=list(S.LAYOUT["ПУБЛИКАЦИИ"])),
                      disk=disk or FakeDrive(), ig=ig if ig is not None else FakeIG(),
                      vkontakte=FakeVK(), cloud=FakeCloud(), today=today, plan=plan,
                      pmp=pmp, pmp_accounts=accounts, pmp_offline=offline,
                      вхолостую=вхолостую)
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
    assert pipe.bot.cards[0]["title"].startswith("P26-09")

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
    трое = FakeSheet([{"ID": x, "Механика": "папа", "Описание к посту": "текст " + x}
                      for x in ("A", "B", "C")])
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="A"),
                         row(status=T.APPROVED, date="2026-09-02", plan="B"),
                         row(status=T.APPROVED, date="2026-09-03", plan="C")],
                        plan=трое)
    pipe.run()
    published = [x[T.COL_STATUS] for x in sheet.rows]
    assert published == [T.PUBLISHED, T.APPROVED, T.APPROVED], published
    assert len(pipe.ig.posted) == 1

    # --- 7. 🔴 второй такт не публикует то же самое повторно ---
    pipe2 = T.Pipeline(bot=FakeBot(), sheet=sheet, pubs=FakeSheet([]),
                       disk=FakeDrive(), ig=FakeIG(), vkontakte=FakeVK(),
                       cloud=FakeCloud(), today=TODAY, plan=трое)
    pipe2.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED
    assert len(pipe2.ig.posted) == 1 and pipe2.ig.posted[0][1] == "текст B", \
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
                        plan=FakeSheet([{"ID": "P1-04", "Механика": "папа",
                                         "Описание к посту": "текст поста"}]),
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
                             plan="P1-09 · вопрос про сад")],
                        plan=FakeSheet([{"ID": "P1-09", "Механика": "папа",
                                         "Описание к посту": "текст поста"}]))
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
    # Дополнено 30.08: добавлен вопрос об отступлениях, заголовки листа СДАЧИ
    # перечитаны сервисным аккаунтом. Google вставил колонку ШЕСТОЙ - перед
    # нашими служебными, поэтому чтение по позиции сломалось бы молча.
    FORM_HEADER = ["Отметка времени", "Адрес электронной почты",
                   "Строка плана", "Файл", "Комментарий",
                   "Сняли по сцене из задания?"]
    for name in (T.COL_TIME, T.COL_EMAIL, T.COL_PLAN, T.COL_FILE, T.COL_COMMENT,
                 T.COL_MATCH):
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

    # --- 21. механика ролика доезжает до ПУБЛИКАЦИЙ ---
    # 🔴 Приемка 28.08 нашла здесь разрыв всей петли: словарь берет механику
    # ТОЛЬКО из листа ПУБЛИКАЦИИ (dictionary.py) и строки с пустой механикой
    # пропускает, а такт ее не писал вовсе. Итог: после партии система докладывает
    # «замеров нет» ПРИ снятых замерах и называет неверную причину - владелец идет
    # чинить метрики, а сломана разметка. Механику берем из строки ПЛАНА по ее ID.
    plan = FakeSheet([{"ID": "P26-09", "Механика": "папа", "Креатор": "Спартак",
                       "Описание к посту": "текст поста"}])
    pipe, sheet = build([dict(row(status=T.APPROVED, date="2026-09-01",
                                  plan="P26-09"),
                              **{T.COL_EMAIL: "ksenia@gmail.com"})], plan=plan)
    pipe.run()
    assert pipe.pubs.rows, "ничего не опубликовалось"
    assert all(x.get("Механика") == "папа" for x in pipe.pubs.rows), pipe.pubs.rows

    # --- 21б. 🔴 в ПУБЛИКАЦИИ едет не одна ось, а все пять (Ш2, 29.08) ---
    # Механику переносили с 28.08, а тему, товар, ценность и тип хука - нет.
    # Значит четыре новые колонки плана оставались текстом в таблице, которого
    # петля не видит: словарь умеет считать по любой оси, но данных по ним
    # в ПУБЛИКАЦИЯХ не появлялось.
    plan = FakeSheet([{"ID": "P26-09", "Механика": "папа", "Креатор": "Спартак",
                       "Тема": "А1 гаджет-вина", "Товар": "световой планшет",
                       "Ценность": "анти-гаджет", "Тип хука": "вопрос",
                       "Роль товара": "развязка", "Ситуация": "А1-2",
                       "Описание к посту": "текст поста"}])
    pipe, sheet = build([dict(row(status=T.APPROVED, date="2026-09-01",
                                  plan="P26-09"),
                              **{T.COL_EMAIL: "ksenia@gmail.com"})], plan=plan)
    pipe.run()
    assert pipe.pubs.rows, "ничего не опубликовалось"
    for оси in pipe.pubs.rows:
        assert оси.get("Тема") == "А1 гаджет-вина", оси
        assert оси.get("Товар") == "световой планшет", оси
        assert оси.get("Ценность") == "анти-гаджет", оси
        assert оси.get("Тип хука") == "вопрос", оси
        # 🔴 оси 02.09: без них не замерить «роль товара -> подписки» на своих
        # данных, а анти-дубль по коду ситуации слепнет между неделями
        assert оси.get("Роль товара") == "развязка", оси
        assert оси.get("Ситуация") == "А1-2", оси

    # --- 22. 🔴 строки нет в ПЛАНЕ - публикации не будет, и это слышно (31.08) ---
    # До 31.08 такт предупреждал про механику и публиковал: считалось, что дыра
    # в замере дешевле задержки. С появлением подписи из ПЛАНА цена изменилась:
    # у строки, которой нет в плане, нет и текста поста, и ролик ушел бы в эфир
    # с идентификатором «P26-09» в подписи. Публиковать такое хуже, чем ждать.
    plan = FakeSheet([{"ID": "P26-01", "Механика": "папа", "Описание к посту": "текст поста"}])
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="P26-09")],
                        plan=plan)
    pipe.run()
    assert not pipe.pubs.rows, "строки нет в плане, а ролик ушел в эфир"
    assert sheet.rows[0][T.COL_STATUS] == T.APPROVED, sheet.rows[0][T.COL_STATUS]
    сказано = " ".join(pipe.bot.notes) + " ".join(str(x) for x in pipe.log)
    assert "описан" in сказано.lower(), ("про пропажу описания надо сказать: %s" % сказано)

    # --- 23. листа ПЛАН нет вовсе - тоже предупреждение, а не тихая пустота ---
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="P26-09")],
                        plan=None)
    pipe.run()
    assert any("механик" in n.lower() for n in pipe.bot.notes), pipe.bot.notes

    # --- 24. план читается один раз на такт, а не на каждую площадку ---
    # Лист ПЛАН - сетевой запрос. Публикация идет на две площадки, и если читать
    # его в цикле, такт удвоит обращения на ровном месте.
    plan = FakeSheet([{"ID": "P26-09", "Механика": "папа", "Описание к посту": "текст поста"}])
    plan.reads = 0
    origin = plan.read

    def counted():
        plan.reads += 1
        return origin()
    plan.read = counted
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="P26-09")],
                        plan=plan)
    pipe.run()
    assert plan.reads <= 1, "лист ПЛАН прочитан %d раз за такт" % plan.reads

    # --- 25. 🔴 креатор снял по сцене - оси едут в учет подтвержденными ---
    # Разрыв А9 (29.08): оси переносятся из ПЛАНА в ПУБЛИКАЦИИ автоматически,
    # то есть учет описывает **замысел**, а не то, что попало в кадр. Если
    # креатор снял иначе, а места сказать об этом нет, словарь через месяц
    # научится приему, которого не было, и ошибка не подаст ни одного признака.
    # В подставе - **ответ формы дословно**, а не канон из кода: канон подложить
    # легко, и тогда проверка не тронет разбор ответа вовсе.
    ОТВЕТ_ДА = "Да, снял по сцене"
    ОТВЕТ_НЕТ = "Нет, отступил - опишу в комментарии"
    plan = FakeSheet([{"ID": "P1-01", "Механика": "папа", "Ценность": "занят сам", "Описание к посту": "текст поста"}])
    pipe, sheet = build([dict(row(status=T.APPROVED, date="2026-09-01", plan="P1-01"),
                              **{T.COL_MATCH: ОТВЕТ_ДА})], plan=plan)
    pipe.run()
    assert pipe.pubs.rows, "ничего не опубликовалось"
    assert all(x.get("Соответствие") == T.MATCH_OK for x in pipe.pubs.rows), pipe.pubs.rows
    assert all(x.get("Ценность") == "занят сам" for x in pipe.pubs.rows), pipe.pubs.rows
    # 🔴 доказательство, что проверка выше вообще способна поймать пропажу:
    # лист обязан отбрасывать колонку, которой нет в его заголовке. Без этой
    # строки предыдущая проверяет заглушку, а не поведение живого листа.
    pipe.pubs.append({"ID": "X", "Соответствие": "по сцене", "Выдуманная": "вот"})
    assert "Выдуманная" not in pipe.pubs.rows[-1], (
        "заглушка листа принимает любую колонку - значит проверки выше ничего "
        "не доказывают: %s" % pipe.pubs.rows[-1])
    assert pipe.pubs.rows[-1]["Соответствие"] == "по сцене", pipe.pubs.rows[-1]

    # --- 26. 🔴 креатор отступил - оси помечены и владелец предупрежден ---
    # Отступление не отменяет публикацию: ролик может быть хорошим. Оно отменяет
    # **доверие к разметке**, поэтому строка помечается, а владелец получает то,
    # что написал креатор, - чтобы поправить оси руками до замера на Д7.
    plan = FakeSheet([{"ID": "P1-01", "Механика": "папа", "Ценность": "занят сам", "Описание к посту": "текст поста"}])
    pipe, sheet = build([dict(row(status=T.APPROVED, date="2026-09-01", plan="P1-01",
                                  comment="снял со столом, планшет сел"),
                              **{T.COL_MATCH: ОТВЕТ_НЕТ})], plan=plan)
    pipe.run()
    assert pipe.pubs.rows, "отступление не должно отменять публикацию"
    assert all(x.get("Соответствие") == T.MATCH_OFF for x in pipe.pubs.rows), pipe.pubs.rows
    assert any("отступ" in n.lower() for n in pipe.bot.notes), (
        "креатор отступил, а владельцу не сказали: %s" % pipe.bot.notes)
    assert any("столом" in n for n in pipe.bot.notes), (
        "владельцу не показали, что именно креатор написал: %s" % pipe.bot.notes)

    # --- 27. 🔴 отметки нет вовсе - это «не подтверждено», а не «по сцене» ---
    # Тишина не подтверждение (урок 23.08). Старые сдачи и сдачи мимо формы
    # обязаны отличаться от подтвержденных, иначе дыра невидима.
    plan = FakeSheet([{"ID": "P1-01", "Механика": "папа", "Описание к посту": "текст поста"}])
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-01", plan="P1-01")],
                        plan=plan)
    pipe.run()
    assert pipe.pubs.rows, "ничего не опубликовалось"
    assert all(x.get("Соответствие") == T.MATCH_UNKNOWN for x in pipe.pubs.rows), (
        "пустая отметка обязана читаться как «не подтверждено»: %s" % pipe.pubs.rows)

    # --- 27б. разбор ответа не рассыпается от того, как креатор ответил ---
    # Текст варианта в форме еще будет правиться руками, поэтому опора - первое
    # слово, а не строка целиком. Незнакомый ответ - «не подтверждено»: выдумывать
    # за креатора «наверное, по сцене» нельзя.
    assert T.match_of("Да") == T.MATCH_OK
    assert T.match_of("  да, все по сцене  ") == T.MATCH_OK
    assert T.match_of("ДА, снял как написано") == T.MATCH_OK
    assert T.match_of("Нет") == T.MATCH_OFF
    assert T.match_of("нет, поменял товар") == T.MATCH_OFF
    assert T.match_of("") == T.MATCH_UNKNOWN
    assert T.match_of(None) == T.MATCH_UNKNOWN
    assert T.match_of("частично") == T.MATCH_UNKNOWN, "незнакомое не выдаем за «да»"


    # --- 🔴 подпись к посту берется из ПЛАНА, а не из служебной строки (31.08) ---
    # Найдено ревизией процесса: caption = row.get(COL_PLAN), то есть в пост
    # уходил идентификатор строки плана. Первая живая публикация 04.09 вышла бы
    # с «P26-09» в подписи. Колонки «Описание к посту» не существовало вовсе.
    план = FakeSheet([{u'ID': u'P26-09', u'Механика': u'папа',
                       u'Описание к посту': u'Он два часа не вспоминал про планшет'}])
    pipe, sheet = build([row(status=T.APPROVED, plan=u'P26-09')], plan=план)
    pipe.run()
    assert pipe.vk.posted, u'публикация не состоялась'
    assert u'два часа' in pipe.vk.posted[0], (
        u'в подпись ушло не описание: %r' % pipe.vk.posted[0])
    assert u'P26-09' not in pipe.vk.posted[0], (
        u'служебный ID уехал в подпись поста: %r' % pipe.vk.posted[0])

    # ...и без описания строка НЕ публикуется молча: пустой пост хуже отказа
    пустой = FakeSheet([{u'ID': u'P26-09', u'Механика': u'папа'}])
    pipe2, sheet2 = build([row(status=T.APPROVED, plan=u'P26-09')], plan=пустой)
    лог2 = u" ".join(pipe2.run())
    assert not pipe2.vk.posted, u'строка без описания ушла в эфир пустой'
    assert u'описан' in лог2.lower(), (
        u'про пропажу описания никто не сказал: %s' % лог2)

    # --- 28. 🔴 публикация идет через Postmypost, когда он подключен ---------
    # Решение владельца 29.08: публикуем сервисом, потому что в ВК не работает
    # ни один бесплатный путь. Свои токены Instagram и ВК при этом отключаются -
    # иначе один ролик уйдет в эфир дважды.
    pmp = FakePmp()
    pipe, sheet = build([row(status=T.APPROVED)], pmp=pmp, accounts=PMP_ACCOUNTS)
    pipe.run()
    assert len(pmp.posted) == 1, "созревшая строка обязана уйти в сервис"
    filename, caption, account_ids, post_at, черновик, _детали = pmp.posted[0]
    assert черновик is False, u"обычный такт публикует по-настоящему" 
    assert caption == "текст поста", "в подпись идет описание из ПЛАНА: %r" % caption
    assert "P26-09" not in caption, "🔴 служебный ID в подписи поста"
    assert sorted(account_ids) == [2248535, 2248551], account_ids
    assert post_at[:4] == "2026" and ("+" in post_at), \
        "время публикации уходит по ISO 8601 с зоной, иначе сервис берет свою: %r" % post_at
    assert not pipe.ig.posted and not pipe.vk.posted, \
        "🔴 при работающем сервисе свои токены молчат, иначе ролик выйдет дважды"
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED

    # в учет ложится строка на каждую площадку, названную по chanel_id
    # 🔴 Имена ровно те, что уже ходят по петле: metrics.py отбирает по строке
    # "instagram", import_csv пишет ее же. Свое название развалило бы замер молча.
    площадки = sorted(r["Площадка"] for r in pipe.pubs.rows)
    assert площадки == ["instagram", "vk"], площадки
    # 🔴 Аудит 02.09: id публикации сервиса - с префиксом pmp: и строкой.
    # Голое число уезжало в Graph API как «медиа Instagram», получало 400
    # и спамило владельцу «не снялись метрики» каждый день навсегда.
    assert all(r["Медиа ID"] == "pmp:31879606" for r in pipe.pubs.rows), \
        [r["Медиа ID"] for r in pipe.pubs.rows]
    assert all(r["Механика"] == "папа" for r in pipe.pubs.rows), \
        "механика обязана доехать до учета и через сервис тоже"

    # --- 28б. 🔴 публикация ставится на 18:00 МСК, а не «когда такт проснулся» --
    # Замер 263 роликов (29.07): 18:00 дает ×1,26 на 26 роликах, а 17:00, где выходила
    # половина ленты, - ×0,84. Сервис умеет отложенную публикацию, значит час выбираем мы.
    момент = datetime.datetime(2026, 9, 4, 11, 30, tzinfo=T.MSK)   # такт проснулся днем
    когда = T.post_at_for(datetime.date(2026, 9, 4), now=момент)
    assert когда.startswith("2026-09-04T18:0"), когда
    assert когда.endswith("+03:00"), u"время уходит с зоной, иначе сервис возьмет свою"

    # окно сегодня уже прошло - публикуем сразу, а не завтра: ролик ждать не должен
    поздно = T.post_at_for(datetime.date(2026, 9, 4),
                           now=datetime.datetime(2026, 9, 4, 20, 5, tzinfo=T.MSK))
    assert поздно.startswith("2026-09-04T20:0"), поздно

    # дата в плане прошла (такт стоял) - тоже сразу, а не в прошлое
    вчерашняя = T.post_at_for(datetime.date(2026, 9, 3),
                              now=datetime.datetime(2026, 9, 4, 9, 0, tzinfo=T.MSK))
    assert вчерашняя.startswith("2026-09-04T09:0"), вчерашняя

    # --- 28б. 🔴 два ролика одного дня не выходят в одну минуту (02.09) ----
    # С недели W36 в день выходит по два ролика в ОДИН аккаунт: оба в 18:00 -
    # это вид спама и порча замера (конкурируют в раздаче в один момент).
    # Окна дня: 18:00 (наш замер, ×1,26 надежно) и 21:00 - сведение 02.09
    # (аналитика/ОКНА_ПУБЛИКАЦИИ_2026-09-02.md): пик Mediascope, LiveDune ВК,
    # «мамы после укладывания», рядом с нашим 20:00 ×1,96. Утро 11:00 снято:
    # для ВК это худшая зона (LiveDune, 30 млн постов). Порядок чередуется
    # по дате, иначе окно приклеится к одному креатору навсегда (ID внутри
    # дня всегда в одном порядке) и смешает эффект окна с человеком.
    слоты = T.day_slots([
        {"ID": "W36-02", "Дата в эфир": "2026-09-04"},
        {"ID": "W36-01", "Дата в эфир": "2026-09-04"},
        {"ID": "W36-03", "Дата в эфир": "2026-09-05"},
        {"ID": "", "Дата в эфир": "2026-09-05"},
    ])
    assert слоты["W36-01"] == (0, 2) and слоты["W36-02"] == (1, 2), слоты
    assert слоты["W36-03"] == (0, 1), слоты

    рано = datetime.datetime(2026, 9, 4, 8, 0, tzinfo=T.MSK)
    a = T.post_at_for(datetime.date(2026, 9, 4), now=рано, slot=0, of=2)
    b = T.post_at_for(datetime.date(2026, 9, 4), now=рано, slot=1, of=2)
    assert {a[11:13], b[11:13]} == {"18", "21"}, (a, b)
    a5 = T.post_at_for(datetime.date(2026, 9, 5), now=рано, slot=0, of=2)
    assert a5[11:13] != a[11:13], \
        u"порядок окон обязан чередоваться по дате: %s и %s" % (a, a5)
    один = T.post_at_for(datetime.date(2026, 9, 4), now=рано)
    assert один[11:13] == "18", \
        u"единственный ролик дня идет в лучшее надежное окно: %s" % один

    # --- 28б-пути. 🔴 справочники data/ ищутся в двух раскладках (02.09):
    # локально data/ лежит НАД pipeline/, в публичном репо foxlik-pipeline -
    # РЯДОМ с tick.py (код публикуется снимком папки в корень). Жесткий
    # "../data" в облаке упал бы на ПЕРВОЙ живой публикации - и молча для
    # всех тактов до нее, где до упаковки дело не доходит.
    import tempfile
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "data"))
    with open(os.path.join(tmp, "data", "проба.tsv"), "w") as f:
        f.write("x")
    assert T._data_file("проба.tsv", roots=[os.path.join(tmp, "нет"), tmp]) \
        == os.path.join(tmp, "data", "проба.tsv")
    try:
        T._data_file("не-существует.tsv", roots=[tmp])
        assert False, u"пропажа справочника обязана быть слышной"
    except SystemExit as e:
        assert "не-существует.tsv" in str(e), e
    for имя in ("площадки.tsv", "артикулы.tsv", "mobzio.tsv"):
        assert os.path.exists(T._data_file(имя)), имя

    # --- 28в-минуты. 🔴 некруглые минуты (владелец 02.09: «не точно в 18:00,
    # а 18:07 или 02») - живой вид вместо роботных :00. Сдвиг ТОЛЬКО вперед,
    # 1..9 минут: минус утянул бы 18:00 в 17:5x, а 17:00 - наше замеренно
    # худшее окно (×0,84). Сдвиг детерминирован датой и слотом: повторный
    # пересчет того же ролика дает то же время, а разные дни - разные минуты.
    for t in (a, b, один):
        assert 1 <= int(t[14:16]) <= 9, u"минуты вне 1..9: %s" % t
    assert a == T.post_at_for(datetime.date(2026, 9, 4), now=рано, slot=0, of=2), \
        u"пересчет обязан давать то же время"
    минуты = {T.post_at_for(datetime.date(2026, 9, d), now=datetime.datetime(
        2026, 9, d, 8, 0, tzinfo=T.MSK), slot=0, of=2)[14:16] for d in range(7, 13)}
    assert len(минуты) > 1, u"минуты не меняются по дням: %s" % минуты

    # --- 28в. 🔴 холостой прогон: весь путь без выхода в эфир ---------------
    # Цепочка «сдача → приемка → эфир» ни разу не проходила целиком: проверены
    # куски. Проверить ее на живом ролике значит опубликовать его по-настоящему
    # в аккаунт на 415 тыс. подписчиков. Поэтому у такта есть холостой режим:
    # все шаги настоящие, а публикация создается ЧЕРНОВИКОМ и в ленту не идет.
    pmp = FakePmp()
    pipe, sheet = build([row(status=T.APPROVED)], pmp=pmp, accounts=PMP_ACCOUNTS,
                        вхолостую=True)
    pipe.run()
    assert len(pmp.posted) == 1, u"холостой прогон обязан пройти весь путь"
    assert pmp.posted[0][4] is True, u"публикация не помечена черновиком"
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED
    # 🔴 и это должно быть видно человеку: строка в учете, помеченная как проба,
    # иначе холостой ролик уедет в словарь механик и испортит замер
    assert all(u"холост" in (r.get("Соответствие") or "").lower()
               for r in pipe.pubs.rows), pipe.pubs.rows

    # --- 28г. 🔴 дата из таблицы приходит числом Google ---------------------
    # Тот же дефект, что найден в замере холостым прогоном 31.08, живет и здесь:
    # такт пишет «2026-09-04», Google хранит дату СВОИМ числом и возвращает
    # «46265». Строка с неразобранной датой считается несозревшей - и не
    # публикуется никогда, молча.
    assert T._as_date("46265") == datetime.date(2026, 8, 31), \
        u"серийный номер Google не разобран - строка не созреет никогда"
    assert T._as_date("2026-09-04") == datetime.date(2026, 9, 4)
    assert T._as_date("7") is None, u"однозначное число - мусор, а не дата"

    серийная = row(status=T.APPROVED, date="46265")   # 31.08.2026, уже наступила
    pipe, sheet = build([серийная], pmp=FakePmp(), accounts=PMP_ACCOUNTS,
                        today=datetime.date(2026, 9, 4))
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED, \
        u"строка с датой-числом зависла бы навсегда"

    # --- 28д. 🔴 у каждой сети своя упаковка поста (31.08) ------------------
    # В ВК ссылка кликается, в Instagram нет. До этого в обе сети уходил один
    # текст: в ВК человек видел номер, который нельзя нажать, а в Instagram
    # мы заняли бы строку подписи ссылкой, которая не работает.
    pmp = FakePmp()
    план_с_товаром = FakeSheet([{"ID": "P26-09", "Механика": "папа",
                                 "Товар": "световой стол",
                                 "Описание к посту": "текст поста"}])
    pipe, sheet = build([row(status=T.APPROVED)], pmp=pmp, accounts=PMP_ACCOUNTS,
                        plan=план_с_товаром)
    pipe.run()
    детали = pmp.posted[0][5]
    assert детали, u"такт публикует без деталей - упаковка по площадкам не доехала"
    по_аккаунту = {d["account_id"]: d for d in детали}
    вк = по_аккаунту[2248535]["content"]
    иг = по_аккаунту[2248551]["content"]
    # 🔴 Ссылка ведет на mobz.link, а не на wildberries.ru напрямую (решение
    # владельца 02.09): короткая ссылка открывает товар в приложении, где
    # человек уже авторизован, и метит источник перехода.
    assert "foxlik.mobz.link/43287163vk" in вк, u"в ВК нет ссылки Mobzio: %s" % вк
    assert "wildberries.ru/catalog" not in вк, \
        u"прямая ссылка теряет и вход в приложение, и метку источника: %s" % вк
    assert "43287163" in иг and "mobz.link" not in иг, \
        u"в Instagram должен быть номер, а не нерабочая ссылка: %s" % иг
    # 🔴 Номер БЕЗ решетки: замер живого поста 02.09 - «#43287163» Instagram
    # делает ссылкой на страницу хештега, и человек уходит в поиск, а не к товару.
    assert "#" not in иг, u"номер стал хештегом и уводит из магазина: %s" % иг
    assert u"в шапке профиля" in иг, u"куда идти за ссылкой - не сказано: %s" % иг
    # артикул Ozon ставится в обеих сетях
    assert "646406042" in вк and "646406042" in иг, \
        u"артикул Ozon не доехал до поста: %s | %s" % (вк, иг)

    # --- 29. 🔴 закрытый модуль API объясняется словами, а не трассировкой ----
    from lib import postmypost as PMP
    стена = PMP.TariffError("модуль «API» не включен")
    pipe, sheet = build([row(status=T.APPROVED)], pmp=FakePmp(fail=стена),
                        accounts=PMP_ACCOUNTS)
    try:
        pipe.run()
    except PMP.TariffError:
        raise AssertionError("стена тарифа обязана стать сообщением владельцу, "
                             "а не падением такта")
    assert any("модул" in n.lower() for n in pipe.bot.notes), pipe.bot.notes
    assert sheet.rows[0][T.COL_STATUS] != T.PUBLISHED, \
        "неопубликованное нельзя помечать опубликованным"

    # --- 29б. 🔴 ссылки вышедших постов дотягиваются из сервиса ------------
    # При создании отложенной публикации ссылки не существует - «Ссылка» в
    # ПУБЛИКАЦИЯХ оставалась пустой навсегда, и выгрузке CSV не с чем было
    # связаться (аудит 02.09: петля получала ноль). Такт дотягивает ссылки
    # вышедших публикаций следующим прогоном.
    import setup as S30
    pubs_rows = [{"ID": "W36-01", "Дата": "2026-09-03", "Площадка": "instagram",
                  "Ссылка": "", "Медиа ID": "pmp:90001"},
                 {"ID": "W36-01", "Дата": "2026-09-03", "Площадка": "vk",
                  "Ссылка": "", "Медиа ID": "pmp:90001"}]
    pipe, sheet = build([], pmp=FakePmp(), accounts=PMP_ACCOUNTS)
    pipe.pubs = FakeSheet(pubs_rows, header=list(S30.LAYOUT["ПУБЛИКАЦИИ"]))
    pipe.run()
    ссылки = {r["Площадка"]: r["Ссылка"] for r in pipe.pubs.rows}
    assert ссылки["instagram"].startswith("https://www.instagram.com/"), ссылки
    assert ссылки["vk"].startswith("https://vk.com/"), ссылки

    # === 30. Аудит 02.09: красные дыры публикации ==========================

    # --- 30а. 🔴 отказ ПОСЛЕ создания публикации не рождает двойной эфир ---
    # Публикация уже в очереди сервиса, а упавший notify/append давал строке
    # ОШИБКА и владельцу «Не опубликовалось» - он одобрял заново, ролик
    # выходил дважды. Статус обязан встать сразу после точки невозврата.
    class ЛомкийБот(FakeBot):
        def notify(self, text, chat_id=None):
            raise RuntimeError("telegram недоступен")

    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-04")])
    pipe.bot = ЛомкийБот()
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.PUBLISHED, \
        u"отказ уведомления после эфира не должен делать ролик «неопубликованным»: %s" \
        % sheet.rows[0][T.COL_STATUS]
    assert len(pipe.ig.posted) == 1
    pipe.bot = FakeBot()
    pipe.run()
    assert len(pipe.ig.posted) == 1, u"второй такт не должен публиковать повторно"

    # --- 30б. 🔴 без единого канала строка НЕ становится ОПУБЛИКОВАН -------
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-04")])
    pipe.ig = pipe.vk = pipe.cloud = pipe.pmp = None
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.APPROVED, \
        u"публиковать нечем - строка обязана ждать, а не сгорать: %s" \
        % sheet.rows[0][T.COL_STATUS]
    assert any(u"нечем" in n for n in pipe.bot.notes), pipe.bot.notes

    # --- 30в. 🔴 мусорная дата не публикует немедленно и говорит ОДИН раз --
    pipe, sheet = build([row(status=T.APPROVED, date="05.09")])
    pipe.run()
    pipe.run()
    assert pipe.ig.posted == [], u"нечитаемая дата - не «пора», а «стоп»"
    assert sheet.rows[0][T.COL_STATUS] == T.APPROVED
    assert sum(1 for n in pipe.bot.notes if u"не публикуется" in n) == 1, \
        u"об отложенной строке говорится один раз, не каждый такт: %s" % pipe.bot.notes

    # --- 30г. 🔴 строка без описания не блокирует очередь ------------------
    r1 = row(status=T.APPROVED, plan=u"P26-01 · без описания", date="2026-09-04",
             file_="link-a")
    r2 = row(status=T.APPROVED, plan=u"P26-09 · нормальная", date="2026-09-04",
             file_="link-b")
    план = FakeSheet([{"ID": "P26-01", "Механика": u"мама", "Описание к посту": ""},
                      {"ID": "P26-09", "Механика": u"папа",
                       "Описание к посту": u"текст поста"}])
    pipe, sheet = build([r1, r2], plan=план)
    pipe.run()
    assert len(pipe.ig.posted) == 1, u"вторая строка обязана выйти в этом же такте"
    assert sheet.rows[1][T.COL_STATUS] == T.PUBLISHED, sheet.rows[1][T.COL_STATUS]
    assert any(u"не публикуется" in n for n in pipe.bot.notes), \
        u"про строку без описания владельцу не сказано: %s" % pipe.bot.notes
    pipe.run()
    assert sum(1 for n in pipe.bot.notes if u"не публикуется" in n) == 1, \
        u"напоминание не должно повторяться каждый такт"

    # --- 30д. 🔴 холостой прогон не публикует запасными ветками ------------
    # DRY_RUN держался только на Postmypost: при его отвале ig/vk выпускали
    # «прогон» в настоящий эфир на 415 тыс. подписчиков.
    pipe, sheet = build([row(status=T.APPROVED, date="2026-09-04")], вхолостую=True)
    pipe.run()
    assert pipe.ig.posted == [], u"холостой прогон ушел в живой Instagram"
    assert sheet.rows[0][T.COL_STATUS] == T.APPROVED, sheet.rows[0][T.COL_STATUS]

    # --- 30е. 🔴 статус с пробелом или в нижнем регистре жив ----------------
    # Ручная правка ячейки - штатный путь починки, который бот сам советует.
    pipe, sheet = build([row(status=u" ОДОБРЕН ", date="2026-09-04")])
    pipe.run()
    assert len(pipe.ig.posted) == 1, u"«ОДОБРЕН » с пробелом невидим такту"
    pipe, sheet = build([row(status=u"публикуется")])
    pipe.run()
    assert sheet.rows[0][T.COL_STATUS] == T.FAILED, \
        u"зависшая строка в нижнем регистре не подобрана"

    # --- 30ж. 🔴 «Переснять» доходит до креаторов --------------------------
    # Раньше вердикт не оповещал никого, кроме владельца: он думал, что
    # креатор знает, креатор ждал эфира.
    r = row(status=T.ON_REVIEW)
    pipe, sheet = build([r])
    pipe.group_chat_id = "-100500"
    pipe.bot.presses = [press(T.Pipeline.row_key(dict(r, _row=2)), action="no")]
    pipe.run()
    assert "-100500" in pipe.bot.chats, \
        u"группа креаторов не оповещена о пересъемке: %s" % pipe.bot.chats

    # --- 31. 🔴 отвалившаяся площадка попадает в учет и слышна -------------
    # Случай 05.09: Instagram отключился от сервиса, такт опубликовал в один ВК
    # и промолчал. Владелец решил, что не вышло ничего, а на деле потерялся
    # главный канал на 415 тыс. подписчиков - и узнать, что досылать, было не по чему.
    r = row(status=T.APPROVED, date="2026-09-04")
    pipe, sheet = build([r], pmp=FakePmp(), accounts=PMP_ACCOUNTS[:1],
                        offline=PMP_ACCOUNTS[1:])
    pipe.run()
    сети = dict((p.get("Площадка"), p) for p in pipe.pubs.rows)
    assert "vk" in сети, u"подключенная сеть обязана публиковаться как обычно"
    assert "instagram" in сети, \
        u"пропущенная сеть обязана попасть в учет, иначе досылать нечего: %s" % list(сети)
    assert сети["instagram"]["Медиа ID"] == T.PENDING, \
        u"строка досыла помечается особо, чтобы замер Д7 ее не взял: %r" \
        % сети["instagram"]["Медиа ID"]
    assert any(u"instagram" in n.lower() for n in pipe.bot.notes), \
        u"владелец не узнал, что канал отвалился: %s" % pipe.bot.notes

    # --- 32. 🔴 вернувшаяся площадка получает накопленное, по одному ролику --
    # Пара к проверке 31: отметка «ЖДЕТ ДОСЫЛА» бесполезна, если ее никто не
    # разбирает. Досылается САМЫЙ СТАРЫЙ и ровно один: вывалить в ленту пачку
    # за неделю значит сжечь их все - раздача делит показы между своими же.
    сдачи = [row(status=T.PUBLISHED, plan="W36-03 · Ксения · стол", file_="f3"),
             row(status=T.PUBLISHED, plan="W36-05 · Ксения · песочница", file_="f5")]
    pipe, sheet = build(сдачи, pmp=FakePmp(), accounts=PMP_ACCOUNTS,
                        pubs_rows=[{"ID": "W36-05", "Дата": "2026-09-03",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": T.PENDING},
                                   {"ID": "W36-03", "Дата": "2026-09-02",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": T.PENDING}])
    pipe.run()
    досланные = [p for p in pipe.pubs.rows
                 if str(p.get("Медиа ID", "")).startswith("pmp:")]
    assert len(досланные) == 1, \
        u"за такт досылается ровно один ролик, а не пачка: %d" % len(досланные)
    assert досланные[0]["ID"] == "W36-03", \
        u"досылать надо самый старый, а ушел %s" % досланные[0]["ID"]
    assert досланные[0]["Дата"] == TODAY.isoformat(), \
        u"у досланного эфир сегодня - от этой даты считается замер Д7: %s" \
        % досланные[0]["Дата"]
    assert pipe.pmp.posted, u"файл не ушел в сервис вовсе"
    assert pipe.pmp.posted[-1][2] == [2248551], \
        u"досыл идет только в вернувшуюся площадку: %s" % (pipe.pmp.posted[-1][2],)

    # --- 32б. 🔴 пока площадка отключена, досыл не пытается и не шумит -------
    pipe, sheet = build(сдачи, pmp=FakePmp(), accounts=PMP_ACCOUNTS[:1],
                        offline=PMP_ACCOUNTS[1:],
                        pubs_rows=[{"ID": "W36-03", "Дата": "2026-09-02",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": T.PENDING}])
    pipe.run()
    assert not pipe.pmp.posted, u"в отключенную площадку досылать нечем"

    # --- 34. 🔴 день эфира берется из ПЛАНА, а не из дня одобрения ----------
    # Замер 05.09: креатор сдал три ролика разом, владелец одобрил их одной
    # пачкой - и все три получили сегодняшнюю дату, то есть вышли бы в один
    # вечер друг у друга на голове. Разводить пришлось руками. План на то
    # и план: у каждой строки есть свой день, и одобрение его не отменяет.
    r = row(status=T.ON_REVIEW, plan="W36-05 · Ксения · песочница")
    планы = [{"ID": "W36-05", "Механика": "мама", "Описание к посту": "текст",
              "Дата в эфир": "2026-09-06"}]
    pipe, sheet = build([r], plan=FakeSheet(планы))
    pipe.bot.presses = [press(T.Pipeline.row_key(dict(r, _row=2)))]
    pipe.run()
    assert sheet.rows[0][T.COL_DATE] == "2026-09-06", \
        u"день эфира обязан прийти из плана, а стоит %r" % sheet.rows[0][T.COL_DATE]
    assert not pipe.pmp or not pipe.pmp.posted, u"будущая строка не публикуется сегодня"

    # --- 34б. 🔴 плановый день уже прошел - выпускаем сегодня, не в прошлое --
    # Ролик, сданный с опозданием, обязан выйти, а не застрять навсегда.
    r = row(status=T.ON_REVIEW, plan="W36-01 · Ксения · стол")
    планы = [{"ID": "W36-01", "Механика": "мама", "Описание к посту": "текст",
              "Дата в эфир": "2026-09-01"}]
    pipe, sheet = build([r], plan=FakeSheet(планы))
    pipe.bot.presses = [press(T.Pipeline.row_key(dict(r, _row=2)))]
    pipe.run()
    assert sheet.rows[0][T.COL_DATE] == TODAY.isoformat(), \
        u"просроченная строка выходит сегодня, а стоит %r" % sheet.rows[0][T.COL_DATE]

    # --- 32в. 🔴 дата в учете приходит числом Google, а не текстом -----------
    # Замер 05.09 живого листа ПУБЛИКАЦИИ: в колонке «Дата» стоит 46269, а не
    # «2026-09-04». Сравнение строк тут молча не совпадает ни с чем, и такт
    # досылал бы второй ролик в тот же день поверх свежего эфира.
    pipe, sheet = build(сдачи, pmp=FakePmp(), accounts=PMP_ACCOUNTS,
                        pubs_rows=[{"ID": "W36-03", "Дата": "2026-09-02",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": T.PENDING},
                                   {"ID": "W36-09", "Дата": "46269",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": "pmp:777"}])
    pipe.run()
    assert not pipe.pmp.posted, \
        u"сегодня в instagram уже был эфир (дата числом) - досыл обязан подождать"

    # --- 33. 🔴 пост, упавший при живом аккаунте, тоже встает в очередь досыла
    # 🔴 Ровно случай 04.09: аккаунт числился подключенным, публикация создалась,
    # а пост в Instagram не вышел (post_status 3). ВК рядом вышел. Такт видел
    # эту ошибку в ответе сервиса и молчал: строка учета навсегда осталась
    # с pmp:id и пустой ссылкой, и ролик считался вышедшим везде.
    упавший = FakePmp()
    упавший.posts = [{"account_id": 2248535, "post_status": 1,
                      "url": "https://vk.ru/clip-1_1"},
                     {"account_id": 2248551, "post_status": 3}]
    pipe, sheet = build([], pmp=упавший, accounts=PMP_ACCOUNTS,
                        pubs_rows=[{"ID": "W36-03", "Дата": "2026-09-04",
                                    "Площадка": "instagram", "Ссылка": "",
                                    "Медиа ID": "pmp:31967175"},
                                   {"ID": "W36-03", "Дата": "2026-09-04",
                                    "Площадка": "vk", "Ссылка": "",
                                    "Медиа ID": "pmp:31967175"}])
    pipe.run()
    ig = [p for p in pipe.pubs.rows if p.get("Площадка") == "instagram"][0]
    вк = [p for p in pipe.pubs.rows if p.get("Площадка") == "vk"][0]
    assert ig["Медиа ID"] == T.PENDING, \
        u"упавший пост обязан встать в очередь досыла, а стоит %r" % ig["Медиа ID"]
    assert вк["Ссылка"] == "https://vk.ru/clip-1_1", \
        u"вышедшему посту падение соседа не мешает: %r" % вк["Ссылка"]
    assert any(u"instagram" in n.lower() for n in pipe.bot.notes), \
        u"владелец не узнал, что пост упал: %s" % pipe.bot.notes

    print("tick selftest OK: 36 проверок - полный путь, одна публикация за такт, "
          "идемпотентность, зависшее, сдвиг листа, ошибки, секреты, перевалка, "
          "публичный лог не выдает содержание, имена колонок сняты с живой формы, "
          "механика доезжает до учета и ее пропажа слышна, отступление креатора "
          "помечено и не выдается за замысел, потерянная площадка (отключенная "
          "или упавшая) попадает в учет, слышна и досылается по одному в день, "
          "а день эфира берется из ПЛАНА, а не из дня одобрения")


if __name__ == "__main__":
    selftest()
