"""Answer narrow clock requests with the host clock instead of an LLM guess."""

import datetime
import re


_ZONE = re.compile(r'东八区|北京时间|中国时间|上海时间|(?:utc|gmt)\+?0?8(?::?00)?|(?:utc|gmt)(?![A-Za-z0-9_+])', re.I)
_CLOCK = re.compile(
    r'(?:(?:请问|你知道|告诉我|帮我查查|帮我看看|帮我看一下))?'
    r'(?:现在|当前|此刻|此时)?(?:具体)?(?:是)?'
    r'(?:几点(?:几分|钟|了)?|时间(?:是)?(?:多少|几点|什么|是多少)?|什么时间)'
    r'(?:了|啊|呀|呢|吗)?'
)
_WEEKDAYS = '一二三四五六日'


def _clean(text):
    text = re.sub(r'[\s，,。！？!?？：:]+', '', text or '').lower()
    return re.sub(r'^aurora', '', text)


def is_candidate(text):
    """Cheap check so ordinary chats keep their existing database path."""
    value = _clean(text)
    matches = list(_ZONE.finditer(value))
    if len(matches) > 1:
        return False
    remainder = value[:matches[0].start()] + value[matches[0].end():] if matches else value
    return bool(_CLOCK.fullmatch(remainder) or (matches and not remainder))


def _request(text, previous):
    value = _clean(text)
    matches = list(_ZONE.finditer(value))
    if len(matches) > 1:
        return None
    zone = matches[0].group().lower() if matches else None
    remainder = value[:matches[0].start()] + value[matches[0].end():] if matches else value
    if _CLOCK.fullmatch(remainder):
        return zone or 'system'
    if not zone or remainder:
        return None
    # A bare "东八区" is a timezone correction only when the preceding user
    # turn was a clock request. Other discussions of timezones stay with the model.
    latest_user = next((row['content'] for row in previous if row['role'] == 'user'), None)
    if latest_user and _request(latest_user, []) is not None:
        return zone
    return None


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def system_time(moment):
    return moment.astimezone()


def answer(question, previous=()):
    zone = _request(question, previous)
    if zone is None:
        return None
    moment = utc_now()
    if zone == 'system':
        local = system_time(moment)
        offset = local.utcoffset() or datetime.timedelta()
        label = '本机系统时区'
    elif zone == 'utc':
        offset = datetime.timedelta()
        local = moment.astimezone(datetime.timezone.utc)
        label = 'UTC'
    else:
        offset = datetime.timedelta(hours=8)
        local = moment.astimezone(datetime.timezone(offset))
        label = '东八区'
    minutes = int(offset.total_seconds() // 60)
    sign = '+' if minutes >= 0 else '-'
    offset_name = f'UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}'
    date = f'{local.year}年{local.month}月{local.day}日'
    return f'现在是 {date} 星期{_WEEKDAYS[local.weekday()]} {local:%H:%M:%S}（{label}，{offset_name}）。'
