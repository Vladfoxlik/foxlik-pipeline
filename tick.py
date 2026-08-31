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
import re
import sys
import traceback

from lib import (cloudinary, drive, google_auth, http, instagram, postmypost,
                 sheets, telegram, vk)

# Каналы Postmypost, сняты живьем 31.08 запросом /channels.
# 🔴 Имена площадок обязаны совпадать с теми, что уже ходят по петле: metrics.py
# отбирает роликами со строкой ровно "instagram", а import_csv пишет ее же.
# Разошлись бы - замер молча не нашел бы ни одного ролика.
CHANNELS = {1: "instagram", 2: "vk", 6: "telegram", 7: "pinterest",
            9: "tiktok", 16: "youtube"}

# Публикация ставится на «сейчас плюс минута»: сервис забирает очередь не мгновенно,
# а прошедшее время он отвергает. Зона московская - в ней живет вся наша таблица.
MSK = datetime.timezone(datetime.timedelta(hours=3))

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
# 🔴 Подпись к посту (31.08). До этого в caption уходил COL_PLAN, то есть
# идентификатор строки: первая живая публикация 04.09 вышла бы с «P1-01»
# в тексте. Колонки не существовало вовсе - ревизия процесса нашла.
COL_CAPTION = "Описание к посту"
COL_FILE = "Файл"
COL_COMMENT = "Комментарий"
COL_STATUS = "Статус"
COL_DATE = "Дата публикации"
COL_REASON = "Причина отказа"
# 🔴 Отметка креатора о том, снял ли он по сцене (разрыв А9, 29.08). Оси учета
# едут из ПЛАНА в ПУБЛИКАЦИИ автоматически, то есть описывают **замысел**.
# Снял иначе - словарь припишет результат ценности и товару, которых в кадре
# не было, и через месяц предложит повторять то, чего не делали. Ошибка тихая:
# ничего не падает и никто не спрашивает.
# ✅ Имя снято с живой таблицы 30.08, а не выведено: вопрос добавлен в форму,
# заголовки листа СДАЧИ прочитаны сервисным аккаунтом. Google поставил колонку
# шестой - ПЕРЕД нашими «Статус», «Дата публикации», «Причина отказа». Код везде
# читает по имени, а не по позиции, поэтому сдвиг ничего не сломал.
COL_MATCH = "Сняли по сцене из задания?"

# Канонические значения в ПУБЛИКАЦИЯХ. Текст ответа в форме длиннее и может
# меняться, поэтому наружу выходит короткое слово, а разбор - в match_of.
MATCH_OK = "по сцене"
MATCH_OFF = "отступил"
MATCH_UNKNOWN = "не подтверждено"


def match_of(value):
    """Ответ креатора → одно из трех состояний.

    🔴 Пустота читается как «не подтверждено», а НЕ как «по сцене». Тишина -
    не подтверждение (урок 23.08): старая сдача, сдача мимо формы и креатор,
    пропустивший вопрос, обязаны отличаться от того, кто ответил «да».
    """
    text = (value or "").strip().lower()
    if not text:
        return MATCH_UNKNOWN
    if text.startswith("да"):
        return MATCH_OK
    if text.startswith("нет"):
        return MATCH_OFF
    return MATCH_UNKNOWN

NEW = ""
ACCEPTED = "ПРИНЯТ"
ON_REVIEW = "НА_ПРИЕМКЕ"
APPROVED = "ОДОБРЕН"
RESHOOT = "НА_ПЕРЕСЪЕМКЕ"
PUBLISHING = "ПУБЛИКУЕТСЯ"
PUBLISHED = "ОПУБЛИКОВАН"
FAILED = "ОШИБКА"

MAX_MB = 300            # предохранитель: больше в память раннера тянуть незачем

# 🔴 Четыре оси учета помимо «Механики» (Ш2, 29.08). Переносятся из ПЛАНА
# в ПУБЛИКАЦИИ, иначе словарь считает только по приему и не может ответить,
# о чем был ролик, каким товаром, для какой боли и какими словами.
AXES_EXTRA = ("Тема", "Товар", "Ценность", "Тип хука", "Сегмент")


class Pipeline:
    def __init__(self, bot, sheet, pubs, disk, ig=None, vkontakte=None, cloud=None,
                 today=None, plan=None, pmp=None, pmp_accounts=(), вхолостую=False):
        # 🔴 Холостой прогон (31.08). Цепочка «сдача → приемка → эфир» ни разу
        # не проходила целиком, а проверить ее на живом ролике значит опубликовать
        # его по-настоящему в аккаунт на 415 тыс. подписчиков. В этом режиме все
        # шаги настоящие - файл скачивается, грузится в сервис, публикация
        # создается, - но со статусом «черновик»: в ленту не выходит ничего.
        self.вхолостую = вхолостую
        self.bot = bot
        self.sheet = sheet
        self.pubs = pubs
        self.disk = disk
        self.ig = ig
        self.vk = vkontakte
        self.cloud = cloud
        # 🔴 Postmypost старше своих токенов: когда он подключен, ig и vk молчат.
        # Иначе один ролик уйдет в эфир дважды - и второй раз уже не отозвать.
        self.pmp = pmp
        self.pmp_accounts = list(pmp_accounts or ())
        self.plan = plan
        self.today = today or datetime.date.today()
        self.log = []
        self._mechanics = None
        self._captions = {}      # лист ПЛАН читается один раз на такт
        self._axes = None           # остальные четыре оси оттуда же

    # ---------- механика ролика ----------

    def axes_of(self, plan_id):
        """Все пять осей учета строки плана: {ось: значение}.

        🔴 С 29.08 осей пять, а не одна (Ш2 в МОДЕЛЬ_КОНТЕНТА). Раньше ехала
        только «Механика», и словарь умел ответить «прием папа дал 1,79», но
        не «что именно сработало»: о чем ролик, каким товаром, для какой боли,
        какими словами. Колонки в плане появились, а до ПУБЛИКАЦИЙ не доезжали -
        то есть были текстом, которого петля не видит.
        """
        plan_id = self.plan_key(plan_id)
        self._load_plan()
        return self._axes.get(plan_id, {})

    PLAN_ID = re.compile(r"^\s*([A-ZА-Я]?P?\d+-\d+)")

    @classmethod
    def plan_key(cls, value):
        """ID строки плана из того, что выбрал креатор в форме.

        🔴 Найдено ревизией процесса 31.08. В листе ПЛАН ключ - «P1-01», а в форме
        человек выбирает пункт списка, и там рядом с номером обычно стоит подсказка:
        «P1-01 · Спартак · световой планшет». Точного совпадения нет, и механика
        строки не находилась - молча, одним предупреждением. Словарь после партии
        доложил бы «считать нечего» и указал бы неверную причину.
        """
        m = cls.PLAN_ID.match(str(value or ""))
        return m.group(1) if m else (value or "").strip()

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
        self._load_plan()
        return self._mechanics.get(plan_id, "")

    def _load_plan(self):
        """Лист ПЛАН читается один раз на такт: и механика, и остальные оси."""
        if self._mechanics is not None:
            return
        self._mechanics, self._axes, self._captions = {}, {}, {}
        if self.plan is not None:
            try:
                for row in self.plan.read():
                    key = (row.get("ID") or "").strip()
                    if not key:
                        continue
                    self._mechanics[key] = (row.get("Механика") or "").strip()
                    self._captions[key] = (row.get(COL_CAPTION) or "").strip()
                    # Оси кроме механики: пустые не пишем, чтобы в ПУБЛИКАЦИЯХ
                    # не появлялись колонки-пустышки на старых планах.
                    self._axes[key] = dict(
                        (ось, (row.get(ось) or "").strip())
                        for ось in AXES_EXTRA if (row.get(ось) or "").strip())
            except Exception as e:
                self.warn_mechanic("лист ПЛАН не прочитался: %s"
                                   % http.mask(str(e))[:200])
        else:
            self.warn_mechanic("такту не передан лист ПЛАН")

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
        plan_key = self.plan_key(row.get(COL_PLAN))
        self._load_plan()
        caption = self._captions.get(plan_key, "")
        if not caption:
            # 🔴 Пустой пост хуже отказа: ролик уйдет в эфир без текста и ссылки,
            # переснять его нельзя, а место в ленте уже занято. Останавливаемся
            # и говорим вслух - строку допишет человек.
            self.sheet.set(row["_row"], COL_STATUS, APPROVED)
            self.say("строка %s: в ПЛАНЕ нет описания к посту для «%s» - "
                     "публикация отложена" % (row["_row"], plan_key or "?"))
            return
        name, content = self.disk.fetch(row[COL_FILE], max_mb=MAX_MB)
        links = {}

        media_ids = {}
        if self.pmp:
            try:
                # день берем из строки сдачи (его проставил владелец при одобрении),
                # час - из замера окон: 18:00 МСК
                когда = post_at_for(_as_date(row.get(COL_DATE)) or self.today)
                pub_id = self.pmp.post_video_bytes(
                    content, name, caption,
                    [a["id"] for a in self.pmp_accounts], когда,
                    черновик=self.вхолостую)
            except postmypost.TariffError as e:
                # 🔴 Стена тарифа - не сбой сети: повторять запрос бессмысленно,
                # нужен человек в биллинге. Строка возвращается в очередь целой.
                self.sheet.set(row["_row"], COL_STATUS, APPROVED)
                self.bot.notify("⛔ Postmypost не публикует: %s\nСтрока %s ждет."
                                % (e, row["_row"]))
                self.say("строка %s: %s" % (row["_row"], e))
                return
            for account in self.pmp_accounts:
                platform = CHANNELS.get(account.get("chanel_id"), "площадка %s"
                                        % account.get("chanel_id"))
                # 🔴 Ссылки на пост в этот момент еще нет: сервис ставит публикацию
                # в очередь и выдает свой id. По нему пост и находится потом.
                links[platform] = ""
                media_ids[platform] = pub_id
        elif self.ig and self.cloud:
            public_id, url = self.cloud.upload(name, content)
            try:
                result = self.ig.post_reel(url, caption)
                if not result:
                    raise RuntimeError("Instagram не дозрел за 5 минут")
                media_ids["instagram"], links["instagram"] = result
            finally:
                self.cloud.destroy(public_id)   # перевалка не должна копить файлы
        if self.vk and not self.pmp:
            links["vk"] = self.vk.publish(name, content, name=caption, message=caption)

        # 🔴 Ключ, а не сырая строка формы. 31.08 починку сделали только для подписи
        # к посту, а механика и пять осей остались на сыром значении «P1-01 · Спартак ·
        # световой планшет» - и не находились. Прежние проверки брали чистый ID,
        # поэтому дыра была невидима. Сюда же идет колонка ID листа ПУБЛИКАЦИИ:
        # по ней замер связывает ролик с планом.
        plan_id = plan_key
        mechanic = self.mechanic_of(plan_id)
        if not mechanic:
            self.warn_mechanic("строка %s не найдена в листе ПЛАН или механика "
                               "в ней пуста" % (plan_id or "без номера"))
        # 🔴 Отступление не отменяет публикацию - ролик может быть хорошим.
        # Оно отменяет доверие к разметке: оси в ПУБЛИКАЦИЯХ описывают замысел,
        # а в кадре другое. Поэтому строка помечается, а владелец получает
        # текст креатора и правит оси руками до замера на Д7.
        match = match_of(row.get(COL_MATCH, ""))
        if match == MATCH_OFF:
            self.bot.notify("⚠️ Креатор отступил от сцены: %s\nЧто написал: %s\n"
                            "Поправьте оси в ПУБЛИКАЦИЯХ до замера на Д7."
                            % (plan_id or "без номера",
                               (row.get(COL_COMMENT) or "").strip() or "без пояснения"))
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
                              "Механика": mechanic,
                              # 🔴 чему верить в пяти колонках справа: «по сцене» -
                              # оси описывают кадр, «отступил» и «не подтверждено» -
                              # только замысел. Словарь считает первые и вслух
                              # называет, сколько отложил (А9, 29.08)
                              # 🔴 Холостая строка обязана быть видна в учете:
                              # иначе проба уедет в словарь механик наравне
                              # с настоящим роликом и испортит замер приема.
                              "Соответствие": ("холостой прогон" if self.вхолостую
                                               else match),
                              # четыре остальные оси учета (Ш2, 29.08): без них
                              # словарь считает только по приему
                              **self.axes_of(plan_id)})
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
    # 🔴 Google хранит дату своим числом и возвращает «46265» вместо «2026-08-31»
    # (найдено холостым прогоном 31.08). Неразобранная дата означает «строка
    # не созрела» - и она не опубликуется никогда, без единого сообщения.
    # Пятизначность - защита от мусора: «7» это не 1900 год, а чья-то опечатка.
    if text.isdigit() and len(text) >= 5:
        try:
            return GOOGLE_EPOCH + datetime.timedelta(days=int(text))
        except (ValueError, OverflowError):
            return None
    return None


GOOGLE_EPOCH = datetime.date(1899, 12, 30)   # день 0 в счете Google Sheets


def post_at_now(now=None):
    """Время публикации для сервиса: ISO 8601 с зоной, минутой позже текущего."""
    now = now or datetime.datetime.now(MSK)
    return (now + datetime.timedelta(minutes=1)).replace(microsecond=0).isoformat()


def post_at_for(день, now=None):
    """Когда выпустить ролик: в 18:00 МСК назначенного дня.

    🔴 Час выбран замером, а не привычкой. 263 ролика, посчитано 29.07: **18:00
    дает ×1,26** к просмотрам на 26 роликах, а 17:00, где выходила половина нашей
    ленты, - ×0,84. Худшее окно 14:00 (×0,73). Смещение постинга на час стоит
    ~1,5 тыс. просмотров на ролик и не требует ни одной правки в контенте.
    Подписки от часа не зависят - их строит содержание.

    Раньше такт публиковал в момент, когда проснулся, то есть в случайный час:
    сервис умеет отложенную публикацию, и не пользоваться этим было потерей.

    Если окно дня уже прошло (или день в прошлом - такт стоял), ролик уходит
    сразу: ждать сутки ради множителя дороже, чем выйти в неидеальный час.
    """
    now = now or datetime.datetime.now(MSK)
    окно = datetime.datetime.combine(день, datetime.time(18, 0), tzinfo=MSK)
    if окно <= now:
        return post_at_now(now)
    return окно.isoformat()


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
    # 🔴 Postmypost старше своих токенов (решение владельца 29.08). Список аккаунтов
    # спрашивается у сервиса, а не хранится у нас: подключить площадку можно в его
    # кабинете в любой момент, и захардкоженный список молча отстал бы от жизни.
    pmp, pmp_accounts = None, []
    if os.environ.get("POSTMYPOST_TOKEN"):
        pmp = postmypost.Postmypost(os.environ["POSTMYPOST_TOKEN"],
                                    os.environ.get("POSTMYPOST_PROJECT_ID", 358244))
        try:
            pmp_accounts = [a for a in pmp.accounts()
                            if a.get("connection_status") == 1]
        except postmypost.TariffError as e:
            # публиковать нечем - но такт обязан доработать: приемка и сдачи живут
            print("Postmypost выключен: %s" % e, file=sys.stderr)
            pmp = None
    return Pipeline(
        bot=telegram.Bot(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_OWNER_ID"]),
        sheet=sheets.Sheet(sa, sid, SHEET_SUBMISSIONS),
        pubs=sheets.Sheet(sa, sid, SHEET_PUBLICATIONS),
        # 🔴 Лист ПЛАН нужен ради одной колонки - «Механика». Без него ролик
        # уходит в учет обезличенным и выпадает из памяти петли.
        plan=sheets.Sheet(sa, sid, SHEET_PLAN),
        disk=drive.Drive(sa), ig=ig, vkontakte=vkontakte, cloud=cloud,
        pmp=pmp, pmp_accounts=pmp_accounts,
        # 🔴 Холостой прогон включается переменной среды, а не флагом командной
        # строки: такт запускается из расписания, где аргументы не передашь.
        вхолостую=bool(os.environ.get("DRY_RUN")))


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
