# -*- coding: utf-8 -*-
"""Телеграм-бот приемки: кнопки владельцу, разбор нажатий, уведомления.

Роль бота узкая - **только приемка и сигналы тревоги**. Ролики он не принимает
(предел Bot API на скачивание 20 МБ замерен 27.08 ответом самого Telegram) и
файлы не носит. Прием роликов - Google Форма.

🔴 Где здесь состояние. Постоянно работающего процесса у нас нет: `tick.py`
поднимается по расписанию раз в 5 минут и умирает. Хранить «на каком обновлении
я остановился» негде - GitHub Actions без памяти между запусками.

Выход в том, что состояние хранит сам Telegram. `getUpdates` отдает накопленное,
и повторный вызов с `offset = последний_id + 1` **стирает подтвержденное на сервере**.
Неподтвержденное лежит там 24 часа. Отсюда жесткий порядок в `tick.py`:

    presses = bot.get_presses()      # прочитали
    ...записали статусы в таблицу...  # сделали работу
    bot.confirm()                    # и только теперь подтвердили

Подтвердить раньше - значит потерять нажатие навсегда, если такт упадет посередине.
Поэтому `confirm` вынесен наружу и никогда не вызывается сам.

🔴 Три вещи здесь замерены живьем 27.08, а не выведены. Каждая меняла код.

1. **Чтение очередь не разрушает.** Проверено: 13 обновлений прочитаны, показаны
   снова, снова и снова. Значит порядок «чтение → работа → подтверждение» надежен.

2. **Подряд идущие опросы недоговаривают.** Первый вызов отдал 13 обновлений,
   три следующих подряд - по одному, через паузу опять все 13. Ничего не пропало,
   но **пустой ответ на быстрый повторный опрос не доказывает, что очередь пуста**.
   Такту это не мешает, а вот отладке мешает сильно.

3. **`answerCallbackQuery` вызывать нельзя.** Он и так отвергается при задержке
   («query is too old» - идентификатор нажатия живет секунды, а такт даже в 5 минут дольше),
   и после его неудачи нажатие пропало из очереди, хотя больше его ничто не трогало.
   Метода здесь нет намеренно. Обратная связь владельцу идет правкой сообщения.

⚠️ Кнопки слушаются только от владельца. В группе креаторов бот тоже сидит,
и чужое нажатие не должно двигать статус.

⚠️ Подтверждается **весь прочитанный пакет**, а не только нажатия. Группа креаторов
болтливая, а `getUpdates` отдает не больше 100 обновлений за раз: копи мы чужую
болтовню, она рано или поздно вытеснит нажатие за край окна.
"""
import json

from . import http

API = "https://api.telegram.org/bot%s/"
CALLBACK_LIMIT = 64      # предел Telegram на callback_data, в байтах
SEP = "|"
PROTO = "v1"             # версия формата кнопки: старые нажатия после правки отсекутся


class TelegramError(Exception):
    pass


class Bot:
    def __init__(self, token, owner_id):
        self.token = token
        self.owner = int(owner_id)
        self.seen_up_to = None      # максимальный id прочитанного пакета целиком

    def call(self, method, **params):
        """Вложенные структуры Telegram ждет строкой JSON, а не формой."""
        data = {k: (json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (dict, list)) else v)
                for k, v in params.items() if v is not None}
        r = http.request(API % self.token + method, method="POST", data=data)
        if not isinstance(r, dict) or not r.get("ok"):
            raise TelegramError("%s: %s" % (method, r))
        return r.get("result")

    # ---------- отправка ----------

    def ask_review(self, row_id, title, file_url, comment="", chat_id=None):
        """Карточка ролика владельцу с двумя кнопками. Возвращает message_id."""
        text = ("🎬 <b>%s</b>\n%s\n\n<a href=\"%s\">открыть ролик</a>"
                % (_esc(row_id), _esc(title), file_url))
        if comment:
            text += "\n\n<i>от креатора:</i> %s" % _esc(comment)
        r = self.call("sendMessage",
                      chat_id=chat_id or self.owner,
                      text=text,
                      parse_mode="HTML",
                      link_preview_options={"is_disabled": False},
                      reply_markup={"inline_keyboard": [[
                          {"text": "✅ Годен", "callback_data": pack("ok", row_id)},
                          {"text": "🔁 Переснять", "callback_data": pack("no", row_id)},
                      ]]})
        return r["message_id"]

    def notify(self, text, chat_id=None):
        """Уведомление без кнопок. Через это идут все ошибки конвейера."""
        return self.call("sendMessage", chat_id=chat_id or self.owner,
                         text=text, parse_mode="HTML")["message_id"]

    # ---------- чтение ----------

    def get_presses(self):
        """Накопленные нажатия владельца. Чужие и устаревшие отбрасываются.

        Возвращает список словарей и НЕ подтверждает прочитанное - подтверждает
        `confirm` после того, как статусы записаны. Заодно запоминает границу
        всего пакета, включая чужую болтовню: подтверждать надо и ее.
        """
        updates = self.call("getUpdates", timeout=0, limit=100) or []
        if updates:
            self.seen_up_to = max(u["update_id"] for u in updates)
        out = []
        for u in updates:
            q = u.get("callback_query")
            if not q:
                continue
            who = (q.get("from") or {}).get("id")
            if who != self.owner:
                continue                      # кнопку жмет только владелец
            action, row_id = unpack(q.get("data") or "")
            if not action:
                continue                      # кнопка от прошлой версии формата
            msg = q.get("message") or {}
            out.append({
                "update_id": u["update_id"],
                "callback_id": q["id"],
                "action": action,
                "row_id": row_id,
                "chat_id": (msg.get("chat") or {}).get("id"),
                "message_id": msg.get("message_id"),
            })
        out.sort(key=lambda p: p["update_id"])
        return out

    def confirm(self):
        """Стирает весь прочитанный пакет на сервере. Вызывать ПОСЛЕ записи статусов.

        Подтверждается граница пакета, а не последнее нажатие: иначе болтовня
        креаторов копится в очереди и вытесняет нажатия за предел в 100 обновлений.
        """
        if self.seen_up_to is None:
            return None
        self.call("getUpdates", offset=self.seen_up_to + 1, limit=1, timeout=0)
        return self.seen_up_to + 1

    # ---------- ответ на нажатие ----------

    def lock(self, chat_id, message_id, verdict):
        """Снимает кнопки и дописывает вердикт - повторно нажать уже нельзя.

        В отличие от `ack`, отказ здесь настоящий: не сняли кнопки - владелец
        нажмет второй раз и не поймет, почему ничего не происходит.
        """
        return self.call("editMessageReplyMarkup", chat_id=chat_id,
                         message_id=message_id,
                         reply_markup={"inline_keyboard": [[
                             {"text": verdict, "callback_data": pack("done", "-")}]]})


def pack(action, row_id):
    """Собирает callback_data и проверяет предел - иначе Telegram молча съест кнопку."""
    data = SEP.join((PROTO, action, str(row_id)))
    if len(data.encode("utf-8")) > CALLBACK_LIMIT:
        raise TelegramError("callback_data длиннее %s байт: %s" % (CALLBACK_LIMIT, data))
    return data


def unpack(data):
    parts = data.split(SEP)
    if len(parts) != 3 or parts[0] != PROTO:
        return None, None
    return parts[1], parts[2]


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def selftest():
    # --- формат кнопки ---
    assert unpack(pack("ok", "P26-03")) == ("ok", "P26-03")
    assert unpack("мусор") == (None, None)
    assert unpack("v0|ok|X") == (None, None), "кнопка чужой версии не должна исполняться"
    try:
        pack("ok", "Ы" * 40)          # кириллица - два байта на букву
        raise AssertionError("перебор длины должен ловиться до отправки")
    except TelegramError:
        pass
    assert _esc("<b> & </b>") == "&lt;b&gt; &amp; &lt;/b&gt;"

    calls = []
    inbox = {"updates": []}

    def fake(url, method="GET", data=None, **kw):
        name = url.rsplit("/", 1)[-1]
        calls.append((name, dict(data or {})))
        if name == "getUpdates":
            if "offset" in (data or {}):
                return {"ok": True, "result": []}          # подтверждение
            return {"ok": True, "result": inbox["updates"]}
        if name == "sendMessage":
            return {"ok": True, "result": {"message_id": 500}}
        return {"ok": True, "result": True}

    real, http.request = http.request, fake
    try:
        bot = Bot("T", 369675757)

        # --- карточка на приемку ---
        mid = bot.ask_review("P26-03", "папа собирает столик",
                             "https://drive.google.com/file/d/X", "снял два дубля")
        assert mid == 500
        sent = calls[0][1]
        assert sent["chat_id"] == 369675757
        kb = json.loads(sent["reply_markup"])["inline_keyboard"][0]
        assert [b["callback_data"] for b in kb] == ["v1|ok|P26-03", "v1|no|P26-03"]
        assert "папа собирает столик" in sent["text"], "русский текст не должен портиться"

        # --- разбор нажатий: свое берем, чужое и мусор отбрасываем ---
        inbox["updates"] = [
            {"update_id": 11, "callback_query": {
                "id": "c1", "data": "v1|ok|P26-03", "from": {"id": 369675757},
                "message": {"message_id": 500, "chat": {"id": 369675757}}}},
            {"update_id": 12, "callback_query": {
                "id": "c2", "data": "v1|no|P26-04", "from": {"id": 999},
                "message": {"message_id": 501, "chat": {"id": -100}}}},
            {"update_id": 13, "message": {"text": "просто болтовня"}},
        ]
        presses = bot.get_presses()
        assert len(presses) == 1, "чужое нажатие и обычное сообщение должны отсеяться"
        assert presses[0]["action"] == "ok" and presses[0]["row_id"] == "P26-03"

        # чтение НЕ подтверждает - иначе падение такта съест нажатие
        reads = [c for c in calls if c[0] == "getUpdates"]
        assert all("offset" not in c[1] for c in reads), "get_presses не смеет подтверждать"

        # 🔴 подтверждается граница ВСЕГО пакета (13), а не последнего нажатия (11):
        # иначе болтовня креаторов копится и вытесняет нажатия за предел в 100
        assert bot.seen_up_to == 13, bot.seen_up_to
        assert bot.confirm() == 14
        confirm_call = [c for c in calls if c[0] == "getUpdates" and "offset" in c[1]][-1]
        assert confirm_call[1]["offset"] == 14, "offset = граница пакета + 1"
        assert Bot("T", 1).confirm() is None, "без чтения подтверждать нечего"

        # --- 🔴 answerCallbackQuery в модуле быть не должно (замер 27.08: он съел нажатие) ---
        assert not hasattr(bot, "ack"), "ack удален намеренно, возвращать его нельзя"
        assert "answerCallbackQuery" not in "".join(c[0] for c in calls)

        # --- снятие кнопок: единственная обратная связь владельцу ---
        bot.lock(369675757, 500, "✅ Годен")
        assert calls[-1][0] == "editMessageReplyMarkup"

        # --- ошибка Telegram обязана всплыть, а не притвориться успехом ---
        http.request = lambda *a, **k: {"ok": False, "description": "chat not found"}
        try:
            Bot("T", 1).notify("привет")
            raise AssertionError("ошибка должна была подняться")
        except TelegramError as e:
            assert "chat not found" in str(e), e
    finally:
        http.request = real
    print("telegram selftest OK: формат кнопки, отсев чужих, порядок чтение→работа→подтверждение, "
          "граница пакета целиком, ack удален, ошибка не глотается")


if __name__ == "__main__":
    selftest()
