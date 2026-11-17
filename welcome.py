"""Explicit welcome name preference; never infer identity from assistant replies."""
import json
from pathlib import Path
import re
import threading

SETTINGS=Path(__file__).resolve().parent/'welcome-settings.json'
LOCK=threading.Lock()


def read():
    import auth
    import app
    if auth.CURRENT.get():
        with app.database() as db:
            row=db.execute("SELECT value FROM preferences WHERE key='welcome_nickname'").fetchone()
            if row: return {'nickname':json.loads(row[0])}
        if not auth.CURRENT.get()['owner']: return {'nickname':auth.CURRENT.get()['display_name']}
    with LOCK:
        if SETTINGS.exists():
            try:
                value=json.loads(SETTINGS.read_text()).get('nickname','')
                if isinstance(value,str): return {'nickname':value[:20]}
            except (OSError,ValueError,AttributeError): pass
    import app
    with app.database() as db:
        for row in db.execute('SELECT content FROM memories WHERE active=1 ORDER BY id DESC'):
            match=re.match(r'^(?:请)?(?:叫我|称呼我为|我叫|我的名字是)[：:\s]*([\w·\-]{1,20})(?=$|[，。；,;\s])',row['content'])
            if match: return {'nickname':match.group(1)}
    return {'nickname':''}


def save(value):
    if not isinstance(value,str): raise ValueError('请填写文字称呼')
    value=value.strip()
    if len(value)>20 or any(ord(c)<32 for c in value): raise ValueError('称呼最多 20 字，不能换行')
    import auth
    import app
    if auth.CURRENT.get():
        with app.database() as db:
            db.execute("INSERT OR REPLACE INTO preferences(key,value) VALUES('welcome_nickname',?)",(json.dumps(value),))
        return {'nickname':value}
    with LOCK:
        staged=SETTINGS.with_suffix('.tmp')
        staged.write_text(json.dumps({'nickname':value},ensure_ascii=False))
        staged.replace(SETTINGS)
    return {'nickname':value}
