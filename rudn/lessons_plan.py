"""План уроков по дням — чистая функция, без Telegram.

Пересчитывается при каждом показе по текущему прогрессу, поэтому:
* пропущенные уроки сами переезжают вперёд, сделанные заранее освобождают дни;
* новые уроки и новые сроки, добавленные в любой момент, сразу распределяются.

Правила:
* сроки идут группами (дата срока); урок группы ставится на дни с «старт» по
  день за два до срока, день перед сроком — «🔁 Повторение»; если дней мало —
  повторение сливается с учёбой;
* лимит в день по нагрузке из расписания: ≥5 пар → 1 урок, 1–4 пары → 2,
  нет пар → 3; не больше 2 уроков одного предмета в день;
* уроки размазываются ровно («водой»): сначала по одному на каждый день, лишнее —
  на более лёгкие и ранние дни; если не помещается — лимит поднимается по одному
  уроку на самый лёгкий день (никогда всё на один день) и показывается предупреждение;
* предметы перемежаются, внутри предмета порядок уроков сохраняется;
* уроки без срока (или с прошедшим) — по одному в день в оставшееся место на неделю;
* после CUTOFF_HOUR новые уроки на сегодня не ставятся (план с завтра).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Protocol

CUTOFF_HOUR = 21  # после 21:00 по Москве новые уроки на сегодня не назначаются
SUBJECT_CAP = 2  # больше двух уроков одного предмета в день не ставим
HARD_CAP = 4  # даже при аврале — не больше 4 уроков в день
UNDATED_HORIZON = 7  # уроки без срока раскладываем на неделю вперёд, по одному в день
SHOW_DAYS = 14


class LessonLike(Protocol):
    id: str
    subject: str
    order: int

    @property
    def minutes(self) -> int: ...


def base_cap(pairs: int) -> int:
    if pairs <= 0:
        return 3
    if pairs >= 5:
        return 1
    return 2


@dataclass
class Group:
    label: str
    due: date
    subjects: list[str]
    lessons: list  # все уроки группы (для повторения)


@dataclass
class Day:
    day: date
    pairs: int
    cap: int
    items: list = field(default_factory=list)  # уроки к прохождению
    done: list = field(default_factory=list)  # уроки, усвоенные в этот день (только сегодня)
    review: list[Group] = field(default_factory=list)
    due: list[Group] = field(default_factory=list)
    urgent: bool = False  # срок сегодня/завтра — пришлось поставить сверх обычного

    @property
    def minutes(self) -> int:
        return sum(l.minutes for l in self.items) + sum(review_minutes(g) for g in self.review)

    @property
    def empty(self) -> bool:
        return not (self.items or self.done or self.review or self.due)


@dataclass
class Plan:
    today: date
    start: date
    days: dict[date, Day]
    warnings: list[str]
    remaining: list  # все неусвоенные уроки в плане (по порядку)

    def get(self, day: date) -> Day | None:
        return self.days.get(day)

    @property
    def today_day(self) -> Day | None:
        return self.days.get(self.today)

    def next_lesson(self):
        """Ближайший неусвоенный урок по плану (для «Взять следующий урок»)."""
        for day in sorted(self.days):
            if day < self.today:
                continue
            for lesson in self.days[day].items:
                return lesson
        return self.remaining[0] if self.remaining else None

    def visible_days(self) -> list[Day]:
        return [d for k, d in sorted(self.days.items()) if not d.empty or k == self.today][:SHOW_DAYS]


def review_minutes(group: Group) -> int:
    return max(10, min(20, 2 * len(group.lessons)))


def interleave(lessons: Iterable) -> list:
    """Перемешивает предметы равномерно (как линии Брезенхема), сохраняя порядок
    внутри предмета: 8 латыни + 1 химия → химия в середине."""
    by_subject: dict[str, list] = {}
    for lesson in sorted(lessons, key=lambda l: (l.subject, l.order, l.id)):
        by_subject.setdefault(lesson.subject, []).append(lesson)
    keyed = []
    for subject, items in by_subject.items():
        for i, lesson in enumerate(items):
            keyed.append(((i + 0.5) / len(items), -len(items), subject, lesson))
    keyed.sort(key=lambda k: k[:3])
    return [k[3] for k in keyed]


def _spread(count: int, room: dict[date, int], pairs: dict[date, int], allow_raise: bool = True) -> tuple[dict[date, int], int]:
    """Сколько уроков на какой день: ровно, лишнее — на лёгкие/ранние дни.
    Возвращает (раскладка, на сколько пришлось поднять лимит)."""
    days = sorted(room)
    room = dict(room)
    raised = 0
    while allow_raise and sum(room.values()) < count:
        free = [d for d in days if room[d] < HARD_CAP]
        if not free:
            break
        # поднимаем лимит самого лёгкого (меньше пар, меньше уже места), раннего дня
        pick = min(free, key=lambda d: (room[d], pairs[d], d))
        room[pick] += 1
        raised += 1
    count = min(count, sum(room.values()))
    taken = {d: 0 for d in days}
    left = count
    level = 0
    while left > 0:
        level += 1
        # на этом «уровне» каждый день, где есть место, получает ещё один урок;
        # если на всех не хватает — сначала лёгкие дни ранней недели
        candidates = [d for d in days if room[d] >= level]
        candidates.sort(key=lambda d: ((d - days[0]).days // 7, pairs[d], d))
        for d in candidates[:left]:
            taken[d] += 1
        left -= min(left, len(candidates))
        if not candidates:
            break
    return taken, raised


def _fill(todo, study, counts, day, subject_load, subject_cap=SUBJECT_CAP) -> list:
    """Раскладывает уроки по дням согласно `counts`: предметы перемежаются, порядок
    внутри предмета сохраняется, ≤subject_cap одного предмета в день — с проверкой,
    что остальное ещё помещается (иначе химия «посередине» отняла бы место у латыни).
    Возвращает то, что не поместилось."""
    queues: dict[str, list] = {}
    for lesson in todo:
        queues.setdefault(lesson.subject, []).append(lesson)
    rank = {id(l): i for i, l in enumerate(todo)}

    def load(d, subject):
        return subject_load.get((d, subject), 0)

    def fits_after(d, index, left_today, chosen):
        """Поместится ли всё оставшееся, если сейчас поставить `chosen` на день d."""
        slots = left_today + sum(counts[x] for x in study[index + 1 :])
        if sum(len(q) for q in queues.values()) - 1 > slots:
            return True  # общая нехватка — не из-за выбора предмета
        for subject, q in queues.items():
            need = len(q) - (1 if subject == chosen else 0)
            if need <= 0:
                continue
            used = load(d, subject) + (1 if subject == chosen else 0)
            room = min(left_today, max(0, subject_cap - used))
            room += sum(min(counts[x], max(0, subject_cap - load(x, subject))) for x in study[index + 1 :])
            if need > room:
                return False
        return True

    for index, d in enumerate(study):
        want = counts.get(d, 0)
        while want > 0:
            heads = sorted((q[0] for q in queues.values() if q), key=lambda l: rank[id(l)])
            allowed = [l for l in heads if load(d, l.subject) < subject_cap]
            if not allowed:
                break
            pick = next((l for l in allowed if fits_after(d, index, want - 1, l.subject)), allowed[0])
            queues[pick.subject].pop(0)
            day(d).items.append(pick)
            subject_load[(d, pick.subject)] = load(d, pick.subject) + 1
            want -= 1
    return [lesson for q in queues.values() for lesson in q]


def build(
    lessons: Iterable,
    today: date | datetime,
    pairs_for: Callable[[date], int],
    due_for: Callable[[object], tuple[str, date] | None],
    learned_on: Callable[[object], date | None],
) -> Plan:
    """План от сегодняшнего дня. `due_for(lesson)` → (метка, дата) ближайшего срока
    или None; `learned_on(lesson)` → дата, когда урок усвоен, или None."""
    moment = today if isinstance(today, datetime) else None
    today = today.date() if isinstance(today, datetime) else today
    start = today + timedelta(days=1) if moment is not None and moment.hour >= CUTOFF_HOUR else today
    lessons = list(lessons)
    days: dict[date, Day] = {}
    warnings: list[str] = []
    pairs_cache: dict[date, int] = {}

    def pairs(d: date) -> int:
        if d not in pairs_cache:
            try:
                pairs_cache[d] = max(0, int(pairs_for(d)))
            except Exception:  # нет расписания на день — считаем свободным
                pairs_cache[d] = 0
        return pairs_cache[d]

    def day(d: date) -> Day:
        if d not in days:
            days[d] = Day(d, pairs(d), base_cap(pairs(d)))
        return days[d]

    subject_load: dict[tuple[date, str], int] = {}

    def room_of(d: date) -> int:
        item = day(d)
        if d < start:
            return 0
        return max(0, item.cap - len(item.items) - len(item.done) - len(item.review))

    # усвоенные сегодня — занимают сегодняшний лимит
    for lesson in lessons:
        if learned_on(lesson) == today:
            day(today).done.append(lesson)
            subject_load[(today, lesson.subject)] = subject_load.get((today, lesson.subject), 0) + 1

    # группы сроков
    groups: dict[date, dict] = {}
    undated = []
    for lesson in lessons:
        found = due_for(lesson)
        learned = learned_on(lesson) is not None
        if not found or found[1] < today:
            if not learned:
                undated.append(lesson)
            continue
        label, due = found
        group = groups.setdefault(due, {"labels": {}, "all": [], "todo": []})
        group["labels"].setdefault(lesson.subject, label)
        group["all"].append(lesson)
        if not learned:
            group["todo"].append(lesson)

    remaining: list = []
    for due in sorted(groups):
        info = groups[due]
        labels = info["labels"]
        group = Group(
            label="; ".join(sorted(set(labels.values()))),
            due=due,
            subjects=sorted(labels),
            lessons=interleave(info["all"]),
        )
        day(due).due.append(group)
        todo = interleave(info["todo"])
        remaining += todo
        review_day = due - timedelta(days=1)
        study = [start + timedelta(days=i) for i in range((review_day - start).days)]
        if review_day >= start:
            day(review_day).review.append(group)
        if not study:
            review_day = None  # учёба и повторение в один день
            # срок завтра/сегодня: учим в оставшиеся дни, даже если уже вечер
            first = min(start, today) if due > today else today
            study = [first + timedelta(days=i) for i in range(max(1, (due - first).days))]
            if todo:
                warnings.append(
                    f"⚠️ До срока {due.strftime('%d.%m')} почти не осталось дней — "
                    f"уроки поставлены на {', '.join(d.strftime('%d.%m') for d in study)}."
                )
            for d in study:
                day(d).urgent = True
        if not todo:
            continue
        # от мягкого к жёсткому: обычный темп → занять день повторения →
        # поднять дневной лимит → разрешить 3–4 урока одного предмета
        attempts = [(study, False, SUBJECT_CAP)]
        if review_day is not None:
            attempts.append((study + [review_day], False, SUBJECT_CAP))
        wide = study + ([review_day] if review_day is not None else [])
        attempts += [(wide, True, cap) for cap in range(SUBJECT_CAP, HARD_CAP + 1)]
        subjects_todo = {l.subject for l in todo}
        for number, (span, allow_raise, subject_cap) in enumerate(attempts):
            last = number == len(attempts) - 1
            room = {}
            for d in span:
                item = day(d)
                used = len(item.items) + len(item.done) + (len(item.review) if d != review_day or span is study else 0)
                limit = item.cap if d >= start else max(1, item.cap)
                room[d] = max(0, limit - used)
                room[d] = min(room[d], sum(max(0, subject_cap - subject_load.get((d, x), 0)) for x in subjects_todo))
            if not allow_raise and sum(room.values()) < len(todo):
                continue
            counts, raised = _spread(len(todo), room, {d: pairs(d) for d in span}, allow_raise)
            snapshot = ({d: list(day(d).items) for d in span}, dict(subject_load))
            left = _fill(todo, span, counts, day, subject_load, subject_cap)
            if left and not last:
                for d, items in snapshot[0].items():
                    day(d).items = items
                subject_load.clear()
                subject_load.update(snapshot[1])
                continue
            for lesson in left:  # последний шанс: самый свободный день после предыдущего урока предмета
                prior = [d for d in span if any(x.subject == lesson.subject for x in day(d).items)]
                pool = [d for d in span if not prior or d >= max(prior)]
                pick = min(pool, key=lambda d: (len(day(d).items), d))
                day(pick).items.append(lesson)
                subject_load[(pick, lesson.subject)] = subject_load.get((pick, lesson.subject), 0) + 1
            if review_day is not None and span is not study and any(day(review_day).items):
                warnings.append(
                    f"⚠️ К {due.strftime('%d.%m')}: уроки заняли и день повторения {review_day.strftime('%d.%m')}."
                )
            if raised or subject_cap > SUBJECT_CAP:
                peak = max(len(day(d).items) + len(day(d).done) for d in span)
                warnings.append(
                    f"⚠️ К {due.strftime('%d.%m')} в обычный темп не помещается: до {peak} уроков в день"
                    + (f", до {subject_cap} по одному предмету" if subject_cap > SUBJECT_CAP else "")
                    + ". Можно взять урок заранее — «📚 Все уроки»."
                )
            break

    # уроки без срока: по одному в день в оставшееся место, неделя вперёд
    queue = interleave(undated)
    remaining += queue
    for offset in range(UNDATED_HORIZON):
        if not queue:
            break
        d = start + timedelta(days=offset)
        if room_of(d) <= 0:
            continue
        lesson = next((l for l in queue if subject_load.get((d, l.subject), 0) < SUBJECT_CAP), None)
        if lesson is None:
            continue
        queue.remove(lesson)
        day(d).items.append(lesson)
        subject_load[(d, lesson.subject)] = subject_load.get((d, lesson.subject), 0) + 1

    for d in list(days):
        if d < today or (days[d].empty and d != today):
            days.pop(d)
    day(today)
    for d in days.values():  # порядок внутри дня — по плану
        d.items.sort(key=lambda l: remaining.index(l) if l in remaining else 0)
    return Plan(today=today, start=start, days=days, warnings=warnings, remaining=remaining)
