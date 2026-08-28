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


def test_metrics_not_on_the_hour():
    """Дока советует не ставить cron на начало часа: там пик нагрузки."""
    m = read("metrics.yml")
    for cron in re.findall(r'cron:\s*"([^"]+)"', m):
        minute = cron.split()[0]
        check(minute not in ("0", "00"),
              "cron метрик стоит на нулевой минуте - пик нагрузки GitHub: %s" % cron)


def main():
    for fn in (test_tick_holds_itself, test_metrics_not_on_the_hour):
        try:
            fn()
        except AssertionError as e:
            print("ПАДЕНИЕ %s: %s" % (fn.__name__, e))
            sys.exit(1)
    print("workflow selftest OK: эстафета на месте, права есть, цикл есть, "
          "накладок нет, предел раннера соблюден, cron не на нулевой минуте")


if __name__ == "__main__":
    main()
