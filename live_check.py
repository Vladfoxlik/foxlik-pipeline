# -*- coding: utf-8 -*-
"""Живая проверка узлов конвейера. Selftest доказывает логику, этот файл - работу.

Правило проекта: «готово» ставится только после прогона на живом сервисе.
Здесь по одной команде на узел, каждая печатает, что именно доказала.

    python live_check.py doctor           что готово, чего не хватает
    python live_check.py telegram-send    отправить владельцу карточку приемки
    python live_check.py telegram-peek    посмотреть очередь, ничего не трогая
    python live_check.py telegram-read    забрать нажатие, ответить, снять кнопки

Токены берутся из .env в корне проекта и никогда не печатаются целиком.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import telegram  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
TG_CONFIG = ROOT / "tools" / "tg_config.json"


def read_env():
    if not ENV.exists():
        sys.exit("Нет файла .env в корне проекта")
    out = {}
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def make_bot():
    token = read_env().get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("В .env нет TELEGRAM_BOT_TOKEN")
    cfg = json.loads(TG_CONFIG.read_text(encoding="utf-8"))
    owner = cfg["владелец"]["id"]
    print("Токен из .env, хвост ...%s · владелец %s" % (token[-4:], owner))
    return telegram.Bot(token, owner)


def telegram_send():
    bot = make_bot()
    mid = bot.ask_review(
        row_id="2:проба",
        title="ПРОБА. Так будет выглядеть карточка ролика от креатора",
        file_url="https://drive.google.com/drive/my-drive",
        comment="Нажмите любую из двух кнопок ниже - это проверка приемки. "
                "В настоящей карточке за ссылкой будет ролик, а кнопки будут "
                "ставить статус ОДОБРЕН или НА_ПЕРЕСЪЕМКЕ.")
    print("ДОКАЗАНО: карточка ушла владельцу, message_id=%s" % mid)
    print("Теперь нажмите кнопку в Telegram и запустите: python live_check.py telegram-read")


def doctor():
    """Что готово, чего не хватает. Проверяет доступом, а не наличием строки в .env."""
    env = read_env()
    ok, todo = [], []

    def check(name, probe, need):
        try:
            ok.append("%-14s %s" % (name, probe()))
        except Exception as e:
            todo.append("%-14s %s\n%s   нужно: %s"
                        % (name, str(e).splitlines()[0][:110], " " * 14, need))

    def google():
        from lib import google_auth, sheets
        sa = google_auth.ServiceAccount.load()
        sid = env.get("SHEET_ID") or os.environ.get("SHEET_ID")
        if not sid:
            raise RuntimeError("нет SHEET_ID в .env")
        titles = sheets.Book(sa, sid).titles()
        return "%s · листы: %s" % (sa.email, ", ".join(titles))

    def tg():
        token = env.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("нет TELEGRAM_BOT_TOKEN в .env")
        me = telegram.Bot(token, env.get("TELEGRAM_OWNER_ID") or 0).call("getMe")
        return "@%s" % me.get("username")

    def group():
        gid = env.get("TELEGRAM_GROUP_ID")
        if not gid:
            raise RuntimeError("нет TELEGRAM_GROUP_ID в .env")
        chat = telegram.Bot(env["TELEGRAM_BOT_TOKEN"], 0).call("getChat", chat_id=gid)
        return chat.get("title")

    def ig():
        from lib import instagram
        token, uid = env.get("IG_TOKEN"), env.get("IG_USER_ID")
        if not (token and uid):
            raise RuntimeError("нет IG_TOKEN или IG_USER_ID в .env")
        left = instagram.Instagram(token, uid).quota_left()
        return "публикаций в сутки осталось: %s из 100" % left

    def cloud():
        from lib import cloudinary
        url = env.get("CLOUDINARY_URL")
        if not url:
            raise RuntimeError("нет CLOUDINARY_URL в .env")
        return "облако %s" % cloudinary.parse_url(url)[0]

    def vkontakte():
        from lib import vk
        token, gid = env.get("VK_TOKEN"), env.get("VK_GROUP_ID")
        if not (token and gid):
            raise RuntimeError("нет VK_TOKEN или VK_GROUP_ID в .env")
        r = vk.Vk(token, gid).call("groups.getById", group_id=abs(int(gid)))
        return str(r)[:80]

    def uploads():
        """🔴 Проверка появилась 27.08 после того, как на этом споткнулись живьем.

        Таблица робота видна, а **папка, куда форма кладет ролики, - отдельный
        объект с отдельными правами**. Первый настоящий ролик уперся бы в 404
        уже в день партии. Идентификатор папки лежит в листе НАСТРОЙКИ, ключ
        ПАПКА_ЗАГРУЗОК: это не секрет, а конфигурация.
        """
        from lib import google_auth, sheets, drive, http
        sa = google_auth.ServiceAccount.load()
        sid = env.get("SHEET_ID") or os.environ.get("SHEET_ID")
        if not sid:
            raise RuntimeError("нет SHEET_ID в .env")
        fid = ""
        for r in sheets.Sheet(sa, sid, "НАСТРОЙКИ").read():
            if r.get("Ключ") == "ПАПКА_ЗАГРУЗОК":
                fid = (r.get("Значение") or "").strip()
        if not fid:
            raise RuntimeError("в листе НАСТРОЙКИ нет строки ПАПКА_ЗАГРУЗОК")
        r = http.request("https://www.googleapis.com/drive/v3/files/" + fid,
                         params={"fields": "name", "supportsAllDrives": "true"},
                         headers=sa.headers(drive.SCOPES))
        return "папка «%s» открыта роботу" % (r or {}).get("name")

    def pmp():
        """🔴 Чем публикуем с 31.08. Проверяется доступом, а не наличием токена:

        замер того дня показал, что оплаченного тарифа мало - API открывается
        отдельным модулем в биллинге, и до его включения все запросы к проекту
        отвечают 400 «Ваш тариф не поддерживает API».
        """
        from lib import postmypost
        token = env.get("POSTMYPOST_TOKEN") or os.environ.get("POSTMYPOST_TOKEN")
        if not token:
            raise RuntimeError("нет POSTMYPOST_TOKEN")
        pid = env.get("POSTMYPOST_PROJECT_ID") or os.environ.get(
            "POSTMYPOST_PROJECT_ID", 358244)
        accounts = postmypost.Postmypost(token, pid).accounts()
        живые = [a for a in accounts if a.get("connection_status") == 1]
        return "аккаунтов подключено: %s (%s)" % (
            len(живые), ", ".join(a.get("name", "?") for a in живые) or "нет")

    check("Google", google, "шаг 1: ключ в корень проекта, таблицу расшарить на робота")
    check("Папка сдач", uploads,
          "расшарить папку «... (File responses)» на почту робота, права Читатель")
    check("Бот", tg, "TELEGRAM_BOT_TOKEN в .env")
    check("Группа", group, "TELEGRAM_GROUP_ID в .env")
    check("Postmypost", pmp,
          "POSTMYPOST_TOKEN в .env, модуль «API» включен в app.postmypost.io/billing")
    check("Cloudinary", cloud, "шаг 4: cloudinary.com, CLOUDINARY_URL в .env")

    # 🔴 Instagram и ВК своими токенами отменены решением владельца 29.08: публикует
    # Postmypost. Доктор требовал их до 31.08 и печатал два ложных «не хватает» -
    # человек читал их как незакрытые шаги настройки (А39). Проверки остаются
    # выключенными, а не удаленными: запасной путь на случай отказа сервиса.
    if env.get("IG_TOKEN"):
        check("Instagram", ig, "запасной путь, основной - Postmypost")
    if env.get("VK_TOKEN"):
        check("ВКонтакте", vkontakte, "запасной путь, основной - Postmypost")

    print("✅ ГОТОВО (%s):" % len(ok))
    for line in ok:
        print("  " + line)
    print()
    if todo:
        print("⬜ НЕ ХВАТАЕТ (%s):" % len(todo))
        for line in todo:
            print("  " + line)
    else:
        print("🎉 все узлы отвечают")


def telegram_peek():
    """Смотрит очередь, ничего не трогая: ни ack, ни подтверждения.

    Нужен для замера: сколько живет нажатие, если такт до него не дошел.
    Дока обещает 24 часа, живой прогон 27.08 показал пропажу за минуты -
    расхождение решается только повторным наблюдением.
    """
    bot = make_bot()
    presses = bot.get_presses()
    print("нажатий в очереди: %s" % len(presses))
    for p in presses:
        print("  update_id=%s строка=%s вердикт=%s" % (p["update_id"], p["row_id"], p["action"]))
    return presses


def telegram_read():
    bot = make_bot()
    presses = bot.get_presses()
    if not presses:
        print("Нажатий нет. Нажмите кнопку в Telegram и повторите.")
        return
    for p in presses:
        verdict = "✅ Годен" if p["action"] == "ok" else "🔁 Переснять"
        # порядок как в tick.py: сначала работа, потом подтверждение
        print("нажатие: строка %s, вердикт %s" % (p["row_id"], p["action"]))
        bot.lock(p["chat_id"], p["message_id"], verdict)
    offset = bot.confirm()
    print("ДОКАЗАНО: нажатие прочитано, кнопки сняты, подтверждено offset=%s" % offset)


COMMANDS = {"doctor": doctor,
            "telegram-send": telegram_send,
            "telegram-peek": telegram_peek,
            "telegram-read": telegram_read}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        sys.exit("Команды: " + " · ".join(COMMANDS))
    COMMANDS[cmd]()
