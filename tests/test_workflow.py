# -*- coding: utf-8 -*-
"""Проверки самих файлов расписания.

Зачем отдельный набор: 28.08 замерено, что cron GitHub наш такт не держит -
`*/5` дал 1 запуск вместо ~223 за 18,6 часов, суточный cron метрик пропустил
свой срок вовсе. Дока GitHub это допускает дословно: при высокой нагрузке
часть поставленных в очередь задач отбрасывается.

Поэтому такт держит сам себя цепочкой: job делает несколько тактов подряд,
а в конце дергает следующий запуск через `workflow_dispatch`. Дока разрешает
это встроенным токеном - `workflow_dispatch` и `repository_dispatch` названы
единственными исключениями из защиты от рекурсии.

Здесь проверяется, что эстафета из файла не пропала. Ошибка тут молчаливая:
конвейер выглядит рабочим, но встает через час и никого не предупреждает.
"""
import re
import sys
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def read(name):
    return (WF / name).read_text(encoding="utf-8")


def check(cond, what):
    if not cond:
        raise AssertionError(what)


def test_tick_holds_itself():
    t = read("tick.yml")

    # Право дергать Actions. Без него эстафета получит 403 и цепочка встанет.
    check(re.search(r"^permissions:", t, re.M), "в tick.yml нет блока permissions")
    check(re.search(r"^\s+actions:\s*write", t, re.M),
          "нет права actions: write - эстафета не сможет запустить следующее звено")

    # Событие, которым дергаем себя. Его отсутствие ломает и эстафету, и кнопку.
    check("workflow_dispatch:" in t, "нет workflow_dispatch - себя не запустить")

    # Сама эстафета.
    check("gh workflow run" in t, "нет шага эстафеты (gh workflow run)")
    check(re.search(r"if:\s*always\(\)", t),
          "эстафета без if: always() - упавший такт оборвет цепочку навсегда")
    check("GH_TOKEN" in t, "эстафете не передан токен")

    # 🔴 Пулемет запусков (замер 02.09: 16 тыс. запусков с шагом ~10 секунд).
    # Проверки упали ДО тактов - звено не работало ни секунды, а always()
    # передает эстафету мгновенно: вечный цикл падений без пауз. Лекарство -
    # не снимать always() (цепочка не должна гаснуть), а выждать такт перед
    # передачей, когда звено не отработало: цикл выравнивается до частоты
    # cron-страховки, конвейер жив и не молотит.
    check(re.search(r"^\s+id:\s*checks", t, re.M),
          "у шага проверок нет id: checks - эстафете не по чему понять, "
          "что звено упало на старте")
    эстафета = t.split("Передать эстафету", 1)[-1]
    check("steps.checks.outcome" in эстафета,
          "эстафета не смотрит на исход проверок - при их падении звено "
          "передает эстафету мгновенно, и цикл превращается в пулемет")
    check(re.search(r"sleep\s+\d+", эстафета),
          "в шаге эстафеты нет паузы sleep для упавшего звена")

    # Цикл тактов внутри job.
    check("sleep" in t, "нет паузы между тактами - цепочка отработает один такт")
    check(re.search(r"^\s*concurrency:", t, re.M),
          "нет concurrency - звенья цепочки пойдут внахлест")

    # Job обязан укладываться в предел хостового раннера: 6 часов по доке.
    m = re.search(r"timeout-minutes:\s*(\d+)", t)
    check(m, "нет timeout-minutes")
    check(int(m.group(1)) <= 360,
          "timeout-minutes больше предела GitHub в 360 минут")

    # Cron остается, но только страховкой на случай обрыва цепочки.
    check("schedule:" in t, "cron убран совсем - порванную цепочку будет некому поднять")

    # 🔴 Суточный замер подстрахован цепочкой. Его собственное расписание 28.08
    # пропустило срок и не запустилось вовсе, а без замера петля не крутится:
    # не обновится словарь и не соберется следующая партия.
    check("metrics.py --if-due" in t,
          "цепочка не подстраховывает суточный замер - при отказе cron петля встанет")


def test_secrets_reach_the_tick():
    """🔴 Оба конца связи: что такт читает из окружения - то расписание обязано дать.

    Класс дефекта, сработавший за три дня четырежды и всегда молча: правится одна
    сторона, вторая остается прежней, ни одна не падает. Здесь цена такая -
    POSTMYPOST_TOKEN есть на машине, но не в tick.yml, и такт в облаке крутится
    вхолостую: приемка идет, в эфир не выходит ничего, и никто не жалуется.
    """
    t = read("tick.yml")
    код = (Path(__file__).resolve().parent.parent / "tick.py").read_text(encoding="utf-8")
    нужны = set(re.findall(r'os\.environ(?:\.get)?[\[\(]"([A-Z0-9_]+)"', код))
    # GOOGLE_SA_JSON приходит файлом ключа, его читает google_auth, а не tick
    нужны.discard("GOOGLE_APPLICATION_CREDENTIALS")
    # 🔴 DRY_RUN - не секрет, а режим холостого прогона: он включается руками
    # на разовом запуске и в расписании стоять НЕ должен. Иначе облако будет
    # вечно публиковать черновики, и это заметят только по пустой ленте.
    нужны.discard("DRY_RUN")
    for имя in sorted(нужны):
        check(imya_v_yml(t, имя),
              "tick.py читает %s, а расписание его не передает: в облаке узел молча "
              "выключится" % имя)


def imya_v_yml(text, name):
    return re.search(r"^\s+%s:\s*\$\{\{\s*secrets\.%s\s*\}\}" % (name, name),
                     text, re.M) is not None


def test_metrics_not_on_the_hour():
    """Дока советует не ставить cron на начало часа: там пик нагрузки."""
    m = read("metrics.yml")
    for cron in re.findall(r'cron:\s*"([^"]+)"', m):
        minute = cron.split()[0]
        check(minute not in ("0", "00"),
              "cron метрик стоит на нулевой минуте - пик нагрузки GitHub: %s" % cron)


def main():
    for fn in (test_tick_holds_itself, test_secrets_reach_the_tick,
               test_metrics_not_on_the_hour):
        try:
            fn()
        except AssertionError as e:
            print("ПАДЕНИЕ %s: %s" % (fn.__name__, e))
            sys.exit(1)
    print("workflow selftest OK: эстафета на месте, права есть, цикл есть, "
          "накладок нет, предел раннера соблюден, cron не на нулевой минуте")


if __name__ == "__main__":
    main()
