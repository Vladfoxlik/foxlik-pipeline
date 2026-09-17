# -*- coding: utf-8 -*-
"""Отчеты владельцу и напоминания креатору. Решение владельца В48, 17.09.

После отмены приемки (В47) владелец больше не видит конвейер через кнопки,
а у отчета о сдачах есть слепое пятно: креатор не сдал - сообщать нечего,
и пустой эфир проходит молча. Этот модуль закрывает пятно тремя сообщениями:

    сводка на завтра    каждый день с 20:00 МСК, владельцу: что сдано на завтра,
                        чего не хватает, какие опоздавшие ждут окна
    напоминание         с 12:00 накануне эфира, в группу креаторов: какие строки
                        завтрашнего дня еще не пришли
    итог недели         воскресенье с 10:00, владельцу: неделя плана (вс-сб),
                        которая кончилась вчера: вышло, опоздало, не сдано, ссылки

🔴 Такт не помнит ничего между запусками, а бежит каждые 5 минут. Отметка
«сегодня уже отправлено» лежит в листе НАСТРОЙКИ (как отметка замера у metrics.py):
без нее сводка приходила бы двенадцать раз за час.

🔴 Порядок «отметка -> отправка -> при сбое снять отметку». Наоборот нельзя:
упавшая запись отметки после удачной отправки дала бы по сообщению каждые 5 минут.

🔴 План не прочитался - молчим и отметку не ставим. «На завтра ничего нет»,
сказанное из-за сбоя сети, хуже тишины: владелец поверит и не проверит.
"""
import datetime

from lib import dates, http

KEY_EVENING = "ОТЧЕТ_СВОДКА_НА_ЗАВТРА"
KEY_REMINDER = "ОТЧЕТ_НАПОМИНАНИЕ_КРЕАТОРУ"
KEY_WEEK = "ОТЧЕТ_ИТОГ_НЕДЕЛИ"

EVENING_HOUR = 20
REMINDER_HOUR = 12
WEEK_HOUR = 10
SUNDAY = 6

# Статусы листа СДАЧИ (те же слова, что в tick.py; импорт оттуда дал бы цикл)
SUBMITTED = ("ПРИНЯТ", "НА_ПРИЕМКЕ", "ОДОБРЕН", "ПУБЛИКУЕТСЯ", "ОПУБЛИКОВАН", "ОШИБКА")
PUBLISHED = ("ПУБЛИКУЕТСЯ", "ОПУБЛИКОВАН")
FAILED = "ОШИБКА"
APPROVED = "ОДОБРЕН"


def _status(row):
    return " ".join(str(row.get("Статус") or "").split()).upper()


def _dm(day):
    return day.strftime("%d.%m")


class Reports:
    def __init__(self, plan, subs, pubs, settings, bot, group_chat_id, key_of, now,
                 say=print):
        self.plan = plan
        self.subs = subs
        self.pubs = pubs
        self.settings = settings
        self.bot = bot
        self.group_chat_id = group_chat_id
        self.key_of = key_of
        self.now = now
        self.today = now.date()
        self.say = say
        self._data = None

    # ---------- отметки ----------

    def _marks(self):
        return dict((str(r.get("Ключ") or "").strip(), r) for r in self.settings.read())

    def _mark(self, marks, key, value):
        строка = marks.get(key)
        if строка:
            self.settings.set(строка["_row"], "Значение", value)
        else:
            self.settings.append({"Ключ": key, "Значение": value})
            # следующая отметка в том же такте должна найти строку, а не дописать вторую
            marks[key] = None
            marks.update(self._marks())

    def _send_once(self, marks, key, text, chat_id=None):
        """Отметка, отправка, при сбое - снять отметку, чтобы следующий такт повторил."""
        self._mark(marks, key, self.today.isoformat())
        try:
            self.bot.notify(text, chat_id=chat_id)
        except Exception as e:
            self._mark(marks, key, "")
            self.say("отчет %s не отправлен: %s" % (key, http.mask(str(e))[:120]))
            return False
        self.say("отчет %s отправлен" % key)
        return True

    # ---------- данные ----------

    def _load(self):
        """ПЛАН и СДАЧИ одним разом на такт. Утвержденные строки плана: {ID: (день, креатор)}."""
        if self._data is not None:
            return self._data
        план = {}
        for r in self.plan.read():
            pid = str(r.get("ID") or "").strip()
            день = dates.as_date(r.get("Дата в эфир"))
            if not pid or not день or "ЧЕРНОВИК" in _status(r):
                continue
            план[pid] = (день, str(r.get("Креатор") or "").strip())
        сдачи = {}
        for r in self.subs.read():
            ключ = self.key_of(r.get("Строка плана"))
            if ключ:
                сдачи.setdefault(ключ, []).append(r)
        self._data = (план, сдачи)
        return self._data

    @staticmethod
    def _state(rows):
        """Итог по всем сдачам одной строки плана: опубликован / сдан / ошибка / нет."""
        статусы = [_status(r) for r in rows or []]
        if any(s in PUBLISHED for s in статусы):
            return "published"
        if any(s in SUBMITTED and s != FAILED for s in статусы):
            return "submitted"
        if FAILED in статусы:
            return "failed"
        return "missing"

    # ---------- три отчета ----------

    def evening(self):
        план, сдачи = self._load()
        завтра = self.today + datetime.timedelta(days=1)
        строки = sorted(pid for pid, (день, _) in план.items() if день == завтра)
        if not строки:
            текст = ("🗓 Завтра, %s: 🔴 в плане ничего нет. Неделя не загружена "
                     "или день пропущен - эфир будет пустым." % _dm(завтра))
        else:
            пункты = []
            for pid in строки:
                сост = self._state(сдачи.get(pid))
                if сост == "missing":
                    пункты.append("🔴 %s не сдан" % pid)
                elif сост == "failed":
                    пункты.append("⚠️ %s сдан, но публикация упала" % pid)
                else:
                    пункты.append("✅ %s сдан" % pid)
            текст = "🗓 Завтра, %s:\n%s" % (_dm(завтра), "\n".join(пункты))
        ждут = sorted(pid for pid, rows in сдачи.items()
                      if pid in план and план[pid][0] <= self.today
                      and any(_status(r) == APPROVED for r in rows)
                      and self._state(rows) != "published")
        if ждут:
            текст += "\n⏳ Опоздали и ждут свободного окна: %s" % ", ".join(ждут)
        return текст

    def reminders(self):
        """{креатор: текст} о завтрашних строках, которые еще не пришли."""
        план, сдачи = self._load()
        завтра = self.today + datetime.timedelta(days=1)
        по_людям = {}
        for pid, (день, кто) in sorted(план.items()):
            if день == завтра and self._state(сдачи.get(pid)) == "missing":
                по_людям.setdefault(кто, []).append(pid)
        тексты = {}
        for кто, ids in по_людям.items():
            обращение = ("%s, добрый день!" % кто) if кто else "Добрый день!"
            if len(ids) == 1:
                суть = ("Завтра, %s, в эфир идет %s, а ролик пока не пришел."
                        % (_dm(завтра), ids[0]))
            else:
                суть = ("Завтра, %s, в эфир идут %s, а ролики пока не пришли."
                        % (_dm(завтра), ", ".join(ids)))
            тексты[кто] = ("%s %s Пришлите, пожалуйста, сегодня через форму сдачи. "
                           "Если что-то мешает снять - напишите." % (обращение, суть))
        return тексты

    def week(self):
        план, сдачи = self._load()
        начало = self.today - datetime.timedelta(days=7)
        конец = self.today - datetime.timedelta(days=1)
        строки = sorted((день, pid) for pid, (день, _) in план.items()
                        if начало <= день <= конец)
        вышли, опоздали, ждут, не_сданы, ошибки = [], [], [], [], []
        for день, pid in строки:
            rows = сдачи.get(pid)
            сост = self._state(rows)
            if сост == "published":
                вышли.append(pid)
                эфир = max([dates.as_date(r.get("Дата публикации")) for r in rows
                            if _status(r) in PUBLISHED and dates.as_date(r.get("Дата публикации"))]
                           or [день])
                if эфир > день:
                    опоздали.append("%s (план %s, вышел %s)" % (pid, _dm(день), _dm(эфир)))
            elif сост == "submitted":
                ждут.append(pid)
            elif сост == "failed":
                ошибки.append(pid)
            else:
                не_сданы.append(pid)
        ссылки = {}
        for p in self.pubs.read():
            pid = str(p.get("ID") or "").strip()
            url = str(p.get("Ссылка") or "").strip()
            if pid in вышли and url.startswith("http"):
                # Instagram первым: главный канал, по нему идет замер
                if pid not in ссылки or "instagram" in str(p.get("Площадка") or "").lower():
                    ссылки[pid] = url
        части = ["📊 Итог недели %s-%s" % (_dm(начало), _dm(конец)),
                 "вышло %d из %d" % (len(вышли), len(строки))]
        if опоздали:
            части.append("опоздали: " + "; ".join(опоздали))
        if ждут:
            части.append("сданы, но еще не вышли: " + ", ".join(ждут))
        if ошибки:
            части.append("⚠️ публикация упала: " + ", ".join(ошибки))
        if не_сданы:
            части.append("🔴 не сдано: " + ", ".join(не_сданы))
        if ссылки:
            части.append("")
            части.extend("%s %s" % (pid, ссылки[pid]) for pid in вышли if pid in ссылки)
        return "\n".join(части)

    # ---------- такт ----------

    def run(self):
        час = self.now.hour
        нужны = []
        if час >= REMINDER_HOUR and self.group_chat_id:
            нужны.append(KEY_REMINDER)
        if час >= EVENING_HOUR:
            нужны.append(KEY_EVENING)
        if self.today.weekday() == SUNDAY and час >= WEEK_HOUR:
            нужны.append(KEY_WEEK)
        if not нужны:
            return
        try:
            marks = self._marks()
        except Exception as e:
            self.say("отчеты пропущены: НАСТРОЙКИ не прочитались: %s"
                     % http.mask(str(e))[:120])
            return
        сегодня = self.today.isoformat()
        for key in нужны:
            строка = marks.get(key)
            if строка and str(строка.get("Значение") or "").strip() == сегодня:
                continue
            try:
                if key == KEY_EVENING:
                    self._send_once(marks, key, self.evening())
                elif key == KEY_WEEK:
                    self._send_once(marks, key, self.week())
                else:
                    тексты = self.reminders()
                    if not тексты:
                        self._mark(marks, key, сегодня)   # напоминать некому - день закрыт
                        continue
                    self._send_once(marks, key, "\n\n".join(тексты.values()),
                                    chat_id=self.group_chat_id)
            except Exception as e:
                # данные не прочитались: отметку не ставим, молчим до следующего такта
                self.say("отчет %s пропущен: %s" % (key, http.mask(str(e))[:120]))
