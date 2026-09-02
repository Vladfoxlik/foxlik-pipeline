# -*- coding: utf-8 -*-
"""Замер на 7-й день, гейт репостов и продление токена.

Раз в сутки. Делает три вещи, и каждая может работать без остальных:

    1. продлить токен Instagram        (иначе через 60 дней конвейер встанет молча)
    2. снять метрики роликов старше 7 дней и посчитать гейт
    3. сообщить владельцу, что выбилось за порог

🔴 **Что API отдает, а что нет** - прочитано в справочнике Meta 27.08.2026:

| Нужно нам | Через API | Как берем |
|---|---|---|
| Репосты - **наш гейт** | ✅ `shares` через `/insights` | автоматически |
| Просмотры, охват, сохранения | ✅ `views`, `reach`, `saved` | автоматически |
| **Подписки с конкретного ролика** | ❌ **нет такой метрики** | только ручной экспорт CSV |

⚠️ **Ловушка, стоившая бы дня.** Поля `shares_count` и `saved_count` у самого медиа
справочник помечает «Available for Instagram API with **Facebook Login** only», а мы
сидим на Instagram Login. Но метрика `shares` на endpoint `/insights` такой пометки
не имеет и нам доступна. **Поэтому все берется через `/insights`, а не через поля медиа.**

🔴 **`reels_skip_rate` через API есть** - «процент просмотров, где ролик пролистали
в первые 3 секунды». У нас записано, что skip rate недоступен, - это было верно про
экспорт Business Suite и неверно про API. Метрика собирается, но **решений на ней
не принимаем**: отказ от «% досмотра» 17.08 стоял на замере (различал 13% случаев),
а не на доступности. Пересматривать - отдельным разговором с владельцем.

Продление токена по справочнику: `GET /refresh_access_token?grant_type=ig_refresh_token`.
Токен должен быть **старше суток и не истекший**; продленный живет 60 дней.
🔴 **Не продлевали 60 дней - токен мертв, продлить его уже нельзя, только выпускать заново.**

⚠️ Живьем не проверено - нужен токен.
"""
import datetime
import os
import sys
import traceback

import dictionary
import generator
from lib import dates
import datetime as _dt
dates_MSK = _dt.timezone(_dt.timedelta(hours=3))
from lib import google_auth, http, instagram, sheets, telegram

SHEET_PUBLICATIONS = "ПУБЛИКАЦИИ"
SHEET_METRICS = "МЕТРИКИ"
SHEET_SETTINGS = "НАСТРОЙКИ"

from lib.gate import GATE  # порог допуска - единственный экземпляр (аудит 02.09)
DAY = 7             # на какой день после публикации снимаем
TOKEN_WARN_DAYS = 7  # за сколько дней до смерти токена бить тревогу

# 🔴 Отметка последнего замера в листе НАСТРОЙКИ. По ней цепочка тактов понимает,
# что расписание не сработало, и запускает замер сама. Без отметки пропущенный
# замер выглядит точно так же, как сделанный.
KEY_LAST_RUN = "последний замер"

# Метрики, без которых замер бессмыслен. Если Meta откажет - это отказ.
CORE = ["views", "reach", "shares", "saved", "comments", "likes"]
# Метрики «на будущее»: собираем, потому что бесплатно, решений на них не принимаем.
# Отдельным запросом - чтобы отказ по любой из них не уронил основной замер.
EXTRA = ["reels_skip_rate", "ig_reels_avg_watch_time", "total_interactions"]

COL_ID = "ID"
COL_MEDIA = "Медиа ID"
COL_DATE = "Дата"
COL_PLATFORM = "Площадка"


def read_insights(ig, media_id, metrics):
    """GET /{media}/insights. Возвращает {имя: число}."""
    r = http.request(ig._url(str(media_id) + "/insights"),
                     params={"metric": ",".join(metrics), "access_token": ig.token})
    out = {}
    for item in (r or {}).get("data", []):
        values = item.get("values") or [{}]
        out[item.get("name")] = values[0].get("value")
    return out


from lib.gate import per_1000, verdict  # noqa: F401 - канон один (аудит 02.09)


class Metrics:
    def __init__(self, ig, pubs, metrics_sheet, settings, bot, today=None, book=None):
        self.ig = ig
        self.pubs = pubs
        self.metrics = metrics_sheet
        self.settings = settings
        self.bot = bot
        # книга целиком нужна только для пересчета словаря; в проверках ее нет
        self.book = book
        # 🔴 Аудит 02.09: сегодня - московское, раннер в UTC (см. tick)
        self.today = today or datetime.datetime.now(dates_MSK).date()
        self.log = []

    def say(self, line):
        # как в tick.py: лог публичный, поэтому только номера и статусы
        line = http.mask(line)
        self.log.append(line)
        print(line)

    # ---------- токен ----------

    def refresh_token(self):
        """Продлевает токен и кладет новое значение в лист НАСТРОЙКИ.

        В GitHub Secrets записать нельзя: туда пишут через шифрование libsodium,
        а у нас правило нулевых зависимостей. Таблица приватная, этого достаточно.
        """
        r = http.request("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token",
                                 "access_token": self.ig.token})
        if not isinstance(r, dict) or "access_token" not in r:
            raise RuntimeError("продление токена не удалось: %s" % r)
        days = int(r.get("expires_in", 0)) // 86400
        self.put_setting("IG_TOKEN", r["access_token"])
        self.put_setting("IG_TOKEN_ДО", (self.today +
                                         datetime.timedelta(days=days)).isoformat())
        self.say("токен продлен, осталось дней: %s" % days)
        if days <= TOKEN_WARN_DAYS:
            self.bot.notify("🔴 Токен Instagram живет еще %s дней и почему-то не "
                            "продлевается на полный срок. Не продлить за 60 дней - "
                            "он умрет насовсем, и придется выпускать заново." % days)
        return days

    def get_setting(self, key):
        for row in self.settings.read():
            if row.get("Ключ") == key:
                return str(row.get("Значение") or "").strip()
        return ""

    def overdue(self):
        """Замер просрочен - расписание не сработало, надо запускать самим.

        🔴 Зачем это есть. Замер 28.08: суточный cron «17 6 * * *» пропустил свой
        срок и не запустился вовсе, а замер на Д7 - основа всей петли: без него
        не обновится словарь и не соберется следующая партия. Дока GitHub
        допускает такое прямо: при высокой нагрузке часть поставленных в очередь
        задач отбрасывается.

        Поэтому расписание больше не единственная опора. Цепочка тактов живая -
        она заглядывает сюда каждые пять минут и запускает замер, если тот
        просрочен больше чем на сутки. Ровно на сутки - нет: суточный ритм
        соблюден, а лишний прогон это лишняя сводка владельцу и лишние запросы
        к Meta.

        Нечитаемая отметка считается отсутствием замера. Молчать в неясной
        ситуации нельзя: пропущенный замер выглядит точно так же, как сделанный.
        """
        отметка = self.get_setting(KEY_LAST_RUN)
        было = _as_date(отметка)
        if было is None:
            return True
        return (self.today - было).days > 1

    def put_setting(self, key, value):
        rows = self.settings.read()
        for row in rows:
            if row.get("Ключ") == key:
                self.settings.set(row["_row"], "Значение", value)
                return
        self.settings.append({"Ключ": key, "Значение": value})

    # ---------- замер ----------

    def due(self):
        """Публикации в Instagram старше 7 дней, по которым замера еще не было.

        🔴 Аудит 02.09, два исключения:
        - «Медиа ID» с префиксом pmp: - это id публикации Postmypost, а не
          медиа Instagram. Graph API отвечает на него 400, строка не
          помечалась замеренной и спамила владельцу каждый день навсегда.
          Замер таких роликов приходит из выгрузки CSV (import_csv);
        - «Соответствие: холостой прогон» - проба не выходила в эфир,
          мерить нечего.
        """
        done = {r.get(COL_ID) for r in self.metrics.read()}
        out = []
        for row in self.pubs.read():
            if row.get(COL_PLATFORM) != "instagram" or not row.get(COL_MEDIA):
                continue
            if str(row.get(COL_MEDIA) or "").strip().startswith("pmp:"):
                continue
            if (row.get("Соответствие") or "").strip() == "холостой прогон":
                continue
            if row.get(COL_ID) in done:
                continue
            when = _as_date(row.get(COL_DATE))
            if not when or (self.today - when).days < DAY:
                continue
            out.append(row)
        return out

    def measure(self, row):
        core = read_insights(self.ig, row[COL_MEDIA], CORE)
        try:
            extra = read_insights(self.ig, row[COL_MEDIA], EXTRA)
        except Exception:
            extra = {}          # метрики «на будущее» падать не должны никого
        views = core.get("views") or 0
        shares = core.get("shares") or 0
        rate = per_1000(shares, views)
        self.metrics.append({
            COL_ID: row.get(COL_ID, ""),
            "Дата замера": self.today.isoformat(),
            "Просмотры": views,
            "Охват": core.get("reach") or 0,
            "Репосты": shares,
            "Репосты/1000": "" if rate is None else rate,
            "Сохранения": core.get("saved") or 0,
            "Комментарии": core.get("comments") or 0,
            # 🔴 подписки с конкретного ролика API не отдает - только ручной экспорт CSV
            "Подписки": "",
            "Вердикт": verdict(rate),
            "Пропуск первых 3 сек": extra.get("reels_skip_rate", ""),
            "Среднее время": extra.get("ig_reels_avg_watch_time", ""),
        })
        return rate

    def run(self):
        # 🔴 Отметку ставим В НАЧАЛЕ, а не в конце. Если замер упадет посередине,
        # цепочка тактов иначе запускала бы его снова каждые пять минут: сводка
        # владельцу шла бы потоком, а запросы к Meta - без счета. Один прогон
        # в сутки, даже неудачный, - это ритм; повтор решает человек.
        self.put_setting(KEY_LAST_RUN, self.today.isoformat())
        try:
            self.refresh_token()
        except Exception as e:
            # замер важнее продления: одно упало, второе должно пройти
            self.say("продление токена не прошло")
            self.bot.notify("🔴 Токен Instagram не продлился.\n\n%s" % http.mask(str(e))[:900])

        rows = self.due()
        if not rows:
            self.say("замерять нечего")
            self.refresh_dictionary()
            return self.log

        hits, fails = [], []
        for row in rows:
            try:
                rate = self.measure(row)
            except Exception as e:
                self.say("строка %s: замер не прошел" % row.get(COL_ID, "?"))
                self.bot.notify("⚠️ Не снялись метрики «%s»\n\n%s"
                                % (row.get(COL_ID, ""), http.mask(str(e))[:400]))
                continue
            self.say("замерено %s: %s" % (row.get(COL_ID, "?"), verdict(rate)))
            (hits if (rate or 0) >= GATE else fails).append((row.get(COL_ID, ""), rate))

        self.report(hits, fails)
        self.refresh_dictionary()
        return self.log

    def refresh_dictionary(self):
        """Пересчет словаря механик - памяти петли.

        🔴 Стоит здесь, а не отдельным заданием, по одной причине: словарь обязан
        меняться ровно тогда, когда появились новые замеры. Отдельное расписание
        разъехалось бы с этим моментом, и словарь молча отставал бы на цикл.

        Пересчет идемпотентен: считает заново из ПУБЛИКАЦИЙ и МЕТРИК, дублей
        не плодит, колонки решений владельца не трогает.
        """
        if not self.book:
            return                       # в проверках книги нет, и это нормально
        try:
            data = dictionary.rebuild(self.book)
        except Exception as e:
            # словарь важен, но замер важнее: его результат уже в таблице
            self.say("словарь не пересчитался")
            self.bot.notify("⚠️ Словарь механик не обновился.\n\n%s"
                            % http.mask(str(e))[:400])
            return
        self.say("словарь обновлен, механик: %d" % len(data))
        if data:
            self.bot.notify(dictionary.summary(data))
        self.propose_next(data)

    def propose_next(self, data):
        """Черновик следующей партии, когда предыдущая замерена целиком.

        🔴 Пишется в лист ПЛАН, но ничего не публикует: публикация идет от сдач
        креаторов, а не от строк плана. То есть черновик безопасен, а утверждает
        его владелец - как и требует петля §6.
        """
        if not self.book or not data:
            return
        try:
            rows, why = generator.maybe_propose(self.book, data, self.today.isoformat())
            if not rows:
                self.say("следующая партия не предлагается")
                return
            added = generator.write(self.book.sheet("ПЛАН"), rows)
            self.say("предложена партия: строк %d" % added)
            self.bot.notify(generator.summary(rows))
        except Exception as e:
            self.say("генератор не отработал")
            self.bot.notify("⚠️ Черновик следующей партии не собрался.\n\n%s"
                            % http.mask(str(e))[:400])

    def report(self, hits, fails):
        """Сводка владельцу. Она и есть смысл всего замера - без нее петля не крутится."""
        if not hits and not fails:
            return
        total = len(hits) + len(fails)
        lines = ["📊 Замер на %s-й день: %s роликов, порог %s репоста на 1000"
                 % (DAY, total, GATE), ""]
        if hits:
            lines.append("✅ Прошли гейт:")
            lines += ["   %s - %s" % (i, r) for i, r in hits]
        if fails:
            lines.append("❌ Не прошли:")
            lines += ["   %s - %s" % (i, r) for i, r in fails]
        lines += ["", "Доля выше порога: %d%% (в прошлом квартале было 22%%)"
                  % round(100.0 * len(hits) / total)]
        self.bot.notify("\n".join(lines))


GOOGLE_EPOCH = datetime.date(1899, 12, 30)   # день 0 в счете Google Sheets


def _as_date(value):
    """Дата из листа. Единый разбор - lib/dates (аудит 02.09)."""
    if isinstance(value, datetime.date):
        return value
    return dates.as_date(value)


def from_env():
    need = ("SHEET_ID", "IG_TOKEN", "IG_USER_ID",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")
    missing = [n for n in need if not os.environ.get(n)]
    if missing:
        raise SystemExit("нет обязательных секретов: " + ", ".join(missing))
    sa = google_auth.ServiceAccount.load()
    sid = os.environ["SHEET_ID"]
    settings = sheets.Sheet(sa, sid, SHEET_SETTINGS)
    # 🔴 продленный токен лежит в таблице и главнее секрета GitHub: секрет хранит
    # только первоначальное значение и после первого продления устаревает
    token = os.environ["IG_TOKEN"]
    try:
        for row in settings.read():
            if row.get("Ключ") == "IG_TOKEN" and row.get("Значение"):
                token = row["Значение"]
    except Exception:
        pass                    # листа еще нет - работаем с секретом
    return Metrics(ig=instagram.Instagram(token, os.environ["IG_USER_ID"]),
                   pubs=sheets.Sheet(sa, sid, SHEET_PUBLICATIONS),
                   metrics_sheet=sheets.Sheet(sa, sid, SHEET_METRICS),
                   settings=settings,
                   bot=telegram.Bot(os.environ["TELEGRAM_BOT_TOKEN"],
                                    os.environ["TELEGRAM_OWNER_ID"]),
                   book=sheets.Book(sa, sid))


def main():
    # 🔴 Режим страховки: цепочка тактов зовет metrics каждые пять минут, но
    # работать он должен раз в сутки. Проверяем отметку и молча выходим, если
    # замер уже сделан. Так суточный ритм держится живой цепочкой, а не cron -
    # тот 28.08 пропустил свой срок и не запустился вовсе.
    if "--if-due" in sys.argv:
        if not (os.environ.get("IG_TOKEN") and os.environ.get("IG_USER_ID")):
            return 0
        try:
            m = from_env()
            if not m.overdue():
                return 0
            print("замер просрочен - расписание не сработало, запускаю сам")
            m.run()
        except Exception:
            # страховка не имеет права ронять такт публикации
            print(http.mask(traceback.format_exc()), file=sys.stderr)
        return 0

    # 🔴 Нет токена Instagram - это не поломка, а «замерять нечем»: без него
    # у замера нет источника вообще. Выходим спокойно, кодом 0.
    #
    # Замер 27.08: раньше здесь была ошибка, и ежедневное задание краснело бы
    # каждый день до самого получения токена. Вечно красная тревога перестает
    # быть тревогой - настоящий отказ в ней уже не разглядеть. Отметка против
    # выключения расписания стоит с `if: always()` и уцелеет в любом случае.
    if not (os.environ.get("IG_TOKEN") and os.environ.get("IG_USER_ID")):
        print("токена Instagram нет - замерять нечем, выхожу спокойно")
        return 0
    try:
        from_env().run()
    except SystemExit:
        raise
    except Exception:
        print(http.mask(traceback.format_exc()), file=sys.stderr)
        raise


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from tests.test_metrics import selftest
        selftest()
    else:
        main()
