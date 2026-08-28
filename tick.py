# -*- coding: utf-8 -*-
"""Один такт конвейера. Запускается по расписанию раз в 5 минут и умирает.

За такт делается ровно четыре вещи:

    1. разобрать нажатия владельца      -> ОДОБРЕН / НА_ПЕРЕСЪЕМКЕ
    2. показать владельцу новые сдачи   -> НА_ПРИЕМКЕ
    3. опубликовать ОДНУ созревшую      -> ОПУБЛИКОВАН
    4. подобрать зависшее с прошлого раза -> ОШИБКА и сигнал владельцу

🔴 **Одна публикация за такт.** Даже если созрело пять. Так сбой расписания
не выплеснет партию в эфир пачкой, а разложит по тактам.

🔴 **Идемпотентность держится статусом.** Строка берется только в статусе ОДОБРЕН
и первым же действием переводится в ПУБЛИКУЕТСЯ. Второй запуск ее уже не увидит,
даже если первый упал в середине.

🔴 **Зависшее не молчит.** Строка, найденная в ПУБЛИКУЕТСЯ на входе в такт, -
это след упавшего прошлого такта: накладки запрещены настройкой `concurrency`
в расписании, значит соседнего работающего такта быть не может.
Такая строка переводится в ОШИБКА, и владелец получает сообщение.

🔴 **Кнопка привязывается к строке номером и отпечатком.** Номер строки короткий
и влезает в 64 байта callback_data, но он поедет, если в лист вставить строку
руками. Поэтому рядом идет отпечаток от неизменных полей: не совпал - нажатие
отклоняется вслух, а не двигает статус чужого ролика.
"""
import datetime
import hashlib
import os
import sys
import traceback

from lib import cloudinary, drive, google_auth, http, instagram, sheets, telegram, vk

SHEET_SUBMISSIONS = "СДАЧИ"
SHEET_PUBLICATIONS = "ПУБЛИКАЦИИ"
SHEET_PLAN = "ПЛАН"

# Колонки листа СДАЧИ. Имена обязаны совпадать с заголовками в таблице:
# промах ловится вслух в sheets.column_letter, а не молча.
COL_TIME = "Отметка времени"
# 🔴 Поле «креатор» обязательно с первой партии (ПЕТЛЯ_ПРОЦЕДУРА §5): сейчас
# ответить на вопрос «у кого лучше заходит» нечем. Форма собирает почту сдающего -
# она и есть надежный ответ на «кто снял», в отличие от строки плана, где записан
# тот, кому слот назначили.
# 🔴 Имя снято с живой формы 27.08, не выведено: Google называет эту колонку
# «Адрес электронной почты», а не «Электронная почта». Промах был бы молчаливым.
COL_EMAIL = "Адрес электронной почты"
COL_PLAN = "Строка плана"
COL_FILE = "Файл"
COL_COMMENT = "Комментарий"
COL_STATUS = "Статус"
COL_DATE = "Дата публикации"
COL_REASON = "Причина отказа"

NEW = ""
ACCEPTED = "ПРИНЯТ"
ON_REVIEW = "НА_ПРИЕМКЕ"
APPROVED = "ОДОБРЕН"
RESHOOT = "НА_ПЕРЕСЪЕМКЕ"
PUBLISHING = "ПУБЛИКУЕТСЯ"
PUBLISHED = "ОПУБЛИКОВАН"
FAILED = "ОШИБКА"

MAX_MB = 300            # предохранитель: больше в память раннера тянуть незачем


class Pipeline:
    def __init__(self, bot, sheet, pubs, disk, ig=None, vkontakte=None, cloud=None,
                 today=None, plan=None):
        self.bot = bot
        self.sheet = sheet
        self.pubs = pubs
        self.disk = disk
        self.ig = ig
        self.vk = vkontakte
        self.cloud = cloud
        self.plan = plan
        self.today = today or datetime.date.today()
        self.log = []
        self._mechanics = None      # лист ПЛАН читается один раз на такт

    # ---------- механика ролика ----------

    def mechanic_of(self, plan_id):
        """Механика строки плана. Без нее ролик выпадает из памяти петли.

        🔴 Это место 28.08 оказалось разрывом всей петли. Словарь механик берет
        механику ТОЛЬКО из листа ПУБЛИКАЦИИ и строки с пустой механикой пропускает,
        а такт ее не писал вовсе - при снятых замерах система докладывала бы
        «считать нечего» и указывала бы неверную причину.

        Источник истины - лист ПЛАН: там механику задает человек при сборке партии.
        Такт ее только переносит и никогда не придумывает.

        Пропажу озвучиваем вслух: молча оставленная пустота выглядит как успех,
        а стоит одного ролика в замере.
        """
        plan_id = (plan_id or "").strip()
        if self._mechanics is None:
            self._mechanics = {}
            if self.plan is not None:
                try:
                    for row in self.plan.read():
                        key = (row.get("ID") or "").strip()
                        if key:
                            self._mechanics[key] = (row.get("Механика") or "").strip()
                except Exception as e:
                    self.warn_mechanic("лист ПЛАН не прочитался: %s"
                                       % http.mask(str(e))[:200])
            else:
                self.warn_mechanic("такту не передан лист ПЛАН")
        return self._mechanics.get(plan_id, "")

    def warn_mechanic(self, why):
        """Пропажа механики обязана быть слышной, но лог публичный.

        Поэтому в лог идет только факт («механика не определена»), а причина
        с номером строки - владельцу в Telegram. Промах поймала проверка 19:
        первая версия писала номер строки плана в лог, а он содержит название ролика.
        """
        self.say("механика не определена")
        self.bot.notify("⚠️ Ролик уйдет в учет без механики: %s\n\n"
                        "Словарь такие строки пропускает - этот ролик не попадет "
                        "в замер механик. Проверьте лист ПЛАН." % why)

    def say(self, line):
        """Единственная точка вывода - и единственное место, где чистятся секреты.

        Чистить по месту было ошибкой: таблицу и сообщение владельцу почистили,
        а журнал такта забыли, и токен ушел бы в лог GitHub Actions. Поймано
        глазами в выводе проверки 27.08, сама проверка это пропустила.

        🔴 **Репозиторий публичный** (решение владельца 27.08: только так пятиминутный
        такт влезает в бесплатный тариф), а логи запусков в публичном репозитории
        видны любому. Поэтому здесь пишется **что произошло, а не что внутри**:
        номера строк и статусы, без названий роликов, текстов заданий и тел ошибок.
        Подробности уходят владельцу в Telegram и в таблицу - туда чужой не смотрит.
        """
        line = http.mask(line)
        self.log.append(line)
        print(line)

    # ---------- привязка кнопки к строке ----------

    @staticmethod
    def row_key(row):
        """Номер строки плюс отпечаток неизменных полей. Влезает в callback_data."""
        seed = "%s|%s" % (row.get(COL_TIME, ""), row.get(COL_FILE, ""))
        stamp = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]
        return "%s:%s" % (row["_row"], stamp)

    def find_by_key(self, rows, key):
        """Ищет строку по ключу кнопки. Отпечаток не сошелся - строку сдвинули."""
        number = key.split(":")[0]
        for row in rows:
            if str(row["_row"]) == number:
                if self.row_key(row) != key:
                    raise LookupError(
                        "строка %s больше не та, на которую нажимали: лист сдвинули. "
                        "Проверьте вручную и нажмите заново" % number)
                return row
        raise LookupError("строки %s в листе больше нет" % number)

    # ---------- шаг 1: нажатия ----------

    def handle_presses(self, rows):
        presses = self.bot.get_presses()
        if not presses:
            return
        for press in presses:
            try:
                row = self.find_by_key(rows, press["row_id"])
            except LookupError as e:
                self.bot.notify("⚠️ Нажатие не применено: %s" % e)
                self.say("нажатие мимо: %s" % e)
                continue
            if row[COL_STATUS] not in (ON_REVIEW, ACCEPTED):
                # повторное нажатие по старой карточке не должно откатывать статус
                self.say("нажатие по строке %s пропущено: статус уже %s"
                         % (row["_row"], row[COL_STATUS]))
                continue
            if press["action"] == "ok":
                self.sheet.set_many(row["_row"], {
                    COL_STATUS: APPROVED,
                    COL_DATE: row.get(COL_DATE) or self.today.isoformat()})
                row[COL_STATUS] = APPROVED
                verdict = "✅ Годен"
            else:
                self.sheet.set(row["_row"], COL_STATUS, RESHOOT)
                row[COL_STATUS] = RESHOOT
                verdict = "🔁 Переснять"
            self.say("строка %s -> %s" % (row["_row"], row[COL_STATUS]))
            self.bot.lock(press["chat_id"], press["message_id"], verdict)
        # подтверждаем только теперь: упади такт раньше - нажатия дождутся следующего
        self.bot.confirm()

    # ---------- шаг 2: новые сдачи ----------

    def offer_new(self, rows):
        for row in rows:
            if row.get(COL_STATUS, NEW).strip() not in (NEW, ACCEPTED):
                continue
            if not row.get(COL_FILE):
                continue                      # форма еще дописывает строку
            self.bot.ask_review(row_id=self.row_key(row),
                                title=row.get(COL_PLAN) or "без строки плана",
                                file_url=row[COL_FILE],
                                comment=row.get(COL_COMMENT, ""))
            self.sheet.set(row["_row"], COL_STATUS, ON_REVIEW)
            row[COL_STATUS] = ON_REVIEW
            self.say("строка %s отправлена на приемку" % row["_row"])

    # ---------- шаг 3: публикация ----------

    def pick_due(self, rows):
        """Первая созревшая строка: одобрена и дата подошла. Больше одной не берем."""
        for row in rows:
            if row.get(COL_STATUS) != APPROVED:
                continue
            when = _as_date(row.get(COL_DATE))
            if when and when > self.today:
                continue
            return row
        return None

    def publish(self, row):
        # 🔴 первым действием - метка. Она и есть защита от второго запуска.
        self.sheet.set(row["_row"], COL_STATUS, PUBLISHING)
        self.say("строка %s взята в публикацию" % row["_row"])
        name, content = self.disk.fetch(row[COL_FILE], max_mb=MAX_MB)
        caption = row.get(COL_PLAN, "")
        links = {}

        media_ids = {}
        if self.ig and self.cloud:
            public_id, url = self.cloud.upload(name, content)
            try:
                result = self.ig.post_reel(url, caption)
                if not result:
                    raise RuntimeError("Instagram не дозрел за 5 минут")
                media_ids["instagram"], links["instagram"] = result
            finally:
                self.cloud.destroy(public_id)   # перевалка не должна копить файлы
        if self.vk:
            links["vk"] = self.vk.publish(name, content, name=caption, message=caption)

        plan_id = row.get(COL_PLAN, "")
        mechanic = self.mechanic_of(plan_id)
        if not mechanic:
            self.warn_mechanic("строка %s не найдена в листе ПЛАН или механика "
                               "в ней пуста" % (plan_id or "без номера"))
        for platform, link in links.items():
            self.pubs.append({"ID": plan_id,
                              "Дата": self.today.isoformat(),
                              "Площадка": platform,
                              "Ссылка": link,
                              # 🔴 без него metrics.py не сможет снять замер на Д7:
                              # insights запрашиваются по идентификатору медиа, не по ссылке
                              "Медиа ID": media_ids.get(platform, ""),
                              "Креатор": row.get(COL_EMAIL, ""),
                              # 🔴 без нее словарь механик пропустит этот ролик,
                              # и партия не попадет в память петли
                              "Механика": mechanic})
        self.sheet.set(row["_row"], COL_STATUS, PUBLISHED)
        self.say("строка %s опубликована: %s" % (row["_row"], ", ".join(links) or "никуда"))
        self.bot.notify("📤 Опубликовано: %s\n%s" % (
            row.get(COL_PLAN, ""), "\n".join("%s - %s" % kv for kv in links.items())))
        return links

    # ---------- шаг 4: зависшее ----------

    def rescue_stuck(self, rows):
        for row in rows:
            if row.get(COL_STATUS) != PUBLISHING:
                continue
            self.sheet.set_many(row["_row"], {
                COL_STATUS: FAILED,
                COL_REASON: "такт упал в середине публикации, нужна проверка"})
            row[COL_STATUS] = FAILED
            self.say("строка %s подобрана из зависших" % row["_row"])
            self.bot.notify("🔴 Ролик «%s» завис в публикации: прошлый такт не дошел "
                            "до конца. Проверьте, не ушел ли он в эфир, и поставьте "
                            "статус ОДОБРЕН заново." % row.get(COL_PLAN, row["_row"]))

    # ---------- такт целиком ----------

    def run(self):
        rows = self.sheet.read()
        self.rescue_stuck(rows)       # раньше всего: иначе зависшее увидят как новое
        self.handle_presses(rows)
        self.offer_new(rows)
        due = self.pick_due(rows)
        if not due:
            self.say("публиковать нечего")
            return self.log
        try:
            self.publish(due)
        except Exception as e:
            self.sheet.set_many(due["_row"], {
                COL_STATUS: FAILED, COL_REASON: http.mask(str(e))[:900]})
            # в публичный лог - только факт; текст ошибки в таблицу и владельцу
            self.say("строка %s -> ОШИБКА, подробности отправлены владельцу" % due["_row"])
            self.bot.notify("🔴 Не опубликовалось: «%s»\n\n%s"
                            % (due.get(COL_PLAN, ""), http.mask(str(e))[:900]))
        return self.log


def _as_date(value):
    """Дата из таблицы. Google отдает по-разному, поэтому берем что распознаем."""
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def from_env():
    """Собирает конвейер из секретов. Чего нет - тот узел просто выключен."""
    need = ("SHEET_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")
    missing = [n for n in need if not os.environ.get(n)]
    if missing:
        raise SystemExit("нет обязательных секретов: " + ", ".join(missing))
    sa = google_auth.ServiceAccount.load()
    sid = os.environ["SHEET_ID"]
    ig = (instagram.Instagram(os.environ["IG_TOKEN"], os.environ["IG_USER_ID"])
          if os.environ.get("IG_TOKEN") else None)
    cloud = (cloudinary.Cloudinary(url=os.environ["CLOUDINARY_URL"])
             if os.environ.get("CLOUDINARY_URL") else None)
    vkontakte = (vk.Vk(os.environ["VK_TOKEN"], os.environ["VK_GROUP_ID"])
                 if os.environ.get("VK_TOKEN") else None)
    return Pipeline(
        bot=telegram.Bot(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_OWNER_ID"]),
        sheet=sheets.Sheet(sa, sid, SHEET_SUBMISSIONS),
        pubs=sheets.Sheet(sa, sid, SHEET_PUBLICATIONS),
        # 🔴 Лист ПЛАН нужен ради одной колонки - «Механика». Без него ролик
        # уходит в учет обезличенным и выпадает из памяти петли.
        plan=sheets.Sheet(sa, sid, SHEET_PLAN),
        disk=drive.Drive(sa), ig=ig, vkontakte=vkontakte, cloud=cloud)


def main():
    try:
        from_env().run()
    except SystemExit:
        raise
    except Exception:
        # последний рубеж: молча падать нельзя, но и секреты в лог не пускаем
        print(http.mask(traceback.format_exc()), file=sys.stderr)
        raise


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from tests.test_tick import selftest
        selftest()
    else:
        main()
