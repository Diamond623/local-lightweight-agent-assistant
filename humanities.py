"""Local public-domain reading reference for Aurora's response style."""
import functools
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent / 'resources' / 'humanities' / 'fushengliuji'

# The book is a reference for attention and tone, not evidence about the user.
RESPONSE_GUIDANCE = (
    '\n人文表达参考《浮生六记》的观察方式：留意对方说出的具体处境和细小感受，'
    '承认生活中的欢喜与艰难可以并存。先回应当下真正的问题；分享或失落不必立即变成建议。'
    '不要模仿古文、堆砌诗句、强行升华，或把书中人物的经历当作用户经历。'
    '技术、事实和紧急问题仍以准确、直接、有用的回答为先。'
)


@functools.lru_cache(maxsize=1)
def chapters():
    metadata = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
    return [(item['title'], (ROOT / item['file']).read_text(encoding='utf-8'))
            for item in metadata['chapters']]


def excerpt(question, limit=450):
    """Return a bounded original passage only for an explicit book question."""
    if not re.search(r'浮生六[記记]|沈[復复]', question):
        return ''
    clean = re.sub(r'浮生六[記记]|沈[復复]|[^\u3400-\u9fff]', '', question)
    terms = {clean[index:index + 2] for index in range(max(0, len(clean) - 1))}
    candidates = []
    for title, text in chapters():
        for paragraph in text.split('\n\n'):
            paragraph = paragraph.strip()
            if len(paragraph) < 30:
                continue
            score = sum(2 for term in terms if term in paragraph)
            score += sum(1 for term in ('芸', '日常', '悲', '情') if term in question and term in paragraph)
            if title[:2] in question:
                score += 20
            candidates.append((score, title, paragraph))
    if not candidates:
        return ''
    _, title, paragraph = max(candidates, key=lambda row: row[0])
    return (f'\n本地作品参考：《浮生六记》{title}节选（原文，不是用户资料）：'
            f'{paragraph[:limit]}\n只将节选用于回答本轮关于作品的问题；若需要全章，请说明目前只引用了节选。')
