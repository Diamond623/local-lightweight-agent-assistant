"""User-editable text tasks, Beijing wall clock, persistent state and result links."""
import datetime as dt
import json
import os
import re
import subprocess
import time
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Shanghai')


def next_time(clock, repeat, date='', after=None):
    now=dt.datetime.fromtimestamp(after if after is not None else time.time(),ZONE)
    if not re.fullmatch(r'\d{2}:\d{2}',clock): raise ValueError('请选择有效时间，例如 08:00')
    hour,minute=map(int,clock.split(':'))
    if not 0<=hour<24 or not 0<=minute<60: raise ValueError('时间格式应为 HH:MM')
    try: day=dt.date.fromisoformat(date) if repeat=='once' else now.date()
    except ValueError: raise ValueError('单次任务请选择有效日期') from None
    candidate=dt.datetime.combine(day,dt.time(hour,minute),ZONE)
    if repeat=='once':
        if candidate<=now: raise ValueError('单次任务请选择未来的时间')
    else:
        while candidate<=now or (repeat=='weekdays' and candidate.weekday()>=5):
            candidate+=dt.timedelta(days=1)
    return candidate.timestamp()


def setup(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS scheduled_tasks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,prompt TEXT,kind TEXT,repeat TEXT,clock TEXT,date TEXT,
      enabled INTEGER,next_run REAL,last_attempt REAL DEFAULT 0,status TEXT DEFAULT '等待执行',
      last_error TEXT DEFAULT '',session_id INTEGER,last_run REAL);
      CREATE TABLE IF NOT EXISTS task_meta(key TEXT PRIMARY KEY);''')
    if not db.execute("SELECT 1 FROM task_meta WHERE key='seed_daily_v1'").fetchone():
        db.execute('''INSERT INTO scheduled_tasks(name,prompt,kind,repeat,clock,date,enabled,next_run)
            VALUES(?,?,?,?,?,?,?,?)''',('每日简报','整理今天的 AI、大模型、科技与苹果、编程开源、教育高校、国际新闻和市场动态。说明发生了什么、为什么重要，附来源；日期不明要说明。','briefing','daily','08:00','',1,next_time('08:00','daily')))
        db.execute("INSERT INTO task_meta VALUES('seed_daily_v1')")


def api(path,obj):
    import app
    with app.database() as db:
        setup(db)
        if path=='tasks':
            rows=[dict(r) for r in db.execute('SELECT * FROM scheduled_tasks ORDER BY enabled DESC,next_run')]
        elif path=='save-task':
            name=str(obj.get('name','')).strip();prompt=str(obj.get('prompt','')).strip()
            kind=obj.get('kind');repeat=obj.get('repeat');clock=str(obj.get('clock',''));date=str(obj.get('date',''))
            if not name or len(name)>80 or not prompt or len(prompt)>1000: raise ValueError('名称为 1—80 字，任务内容为 1—1000 字')
            if kind not in ('reminder','local','research','briefing') or repeat not in ('once','daily','weekdays'): raise ValueError('未知任务类型或周期')
            future=next_time(clock,repeat,date)
            if obj.get('id'):
                row=db.execute('SELECT status FROM scheduled_tasks WHERE id=?',(int(obj['id']),)).fetchone()
                if not row: raise ValueError('任务已不存在')
                if row['status']=='正在执行': raise ValueError('请等待本轮执行结束后再修改')
                db.execute("UPDATE scheduled_tasks SET name=?,prompt=?,kind=?,repeat=?,clock=?,date=?,next_run=?,last_attempt=0,last_error='',status='等待执行' WHERE id=?",(name,prompt,kind,repeat,clock,date,future,int(obj['id'])))
            else:
                db.execute('INSERT INTO scheduled_tasks(name,prompt,kind,repeat,clock,date,enabled,next_run) VALUES(?,?,?,?,?,?,1,?)',(name,prompt,kind,repeat,clock,date,future))
            return {'ok':True}
        else:
            row=db.execute('SELECT * FROM scheduled_tasks WHERE id=?',(int(obj['id']),)).fetchone()
            if not row: raise ValueError('任务已不存在')
            if path=='delete-task':
                if row['status']=='正在执行': raise ValueError('正在执行的任务请先等待完成；可先暂停后续执行')
                db.execute('DELETE FROM scheduled_tasks WHERE id=?',(row['id'],))
            elif path=='toggle-task':
                enabled=not bool(row['enabled'])
                future=next_time(row['clock'],row['repeat'],row['date']) if enabled else row['next_run']
                db.execute('UPDATE scheduled_tasks SET enabled=?,next_run=? WHERE id=?',(int(enabled),future,row['id']))
            return {'ok':True}
    try:
        result=subprocess.run(['/bin/launchctl','print','gui/'+str(os.getuid())+'/local.aurora.daily-briefing'],capture_output=True,timeout=3)
        background=result.returncode==0
    except (OSError,subprocess.TimeoutExpired): background=False
    import account_client
    return {'tasks':rows,'background':background and not bool(account_client.service_url()),'central':bool(account_client.service_url())}


def reminder(task, key, day):
    import app
    with app.database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS briefing_runs(day TEXT PRIMARY KEY,session_id INTEGER)')
        existing=db.execute('SELECT session_id FROM briefing_runs WHERE day=?',(key,)).fetchone()
        if existing: return existing[0]
        sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',(day+' '+task['name'],app.now())).lastrowid
        mid=db.execute('INSERT INTO messages(session_id,role,content,image,sources,created) VALUES(?,?,?,?,?,?)',(sid,'assistant','定时提醒：'+task['prompt'],'','[]',app.now())).lastrowid
        app.memory_engine.index(db,'message',mid,sid,task['prompt'])
        db.execute('INSERT INTO briefing_runs VALUES(?,?)',(key,sid))
        return sid


def tick(instant=None):
    import app
    import daily_briefing
    now=instant if instant is not None else time.time()
    with app.database() as db:
        setup(db)
        tasks=[dict(r) for r in db.execute('SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run<=? AND last_attempt<=?',(now,now-1800))]
    for task in tasks:
        if task['repeat']=='weekdays' and dt.datetime.fromtimestamp(now,ZONE).weekday()>=5:
            with app.database() as db:
                db.execute("UPDATE scheduled_tasks SET next_run=?,status='等待工作日' WHERE id=?",(next_time(task['clock'],'weekdays',after=now),task['id']))
            continue
        with app.database() as db:
            claim=db.execute("UPDATE scheduled_tasks SET status='正在执行',last_attempt=? WHERE id=? AND enabled=1 AND next_run<=? AND last_attempt<=?",(now,task['id'],now,now-1800))
            if not claim.rowcount: continue
        day=dt.datetime.fromtimestamp(task['next_run'],ZONE).date().isoformat()
        key=f'task:{task["id"]}:{task["next_run"]}'
        try:
            if task['kind']=='reminder': sid=reminder(task,key,day)
            else:
                sid=daily_briefing.produce(dt.datetime.now(ZONE).date().isoformat(),None if task['kind']=='briefing' else task['prompt'],task['kind']!='local',key,task['name'])
            future=task['next_run'] if task['repeat']=='once' else next_time(task['clock'],task['repeat'],after=max(now,time.time()))
            with app.database() as db:
                db.execute("UPDATE scheduled_tasks SET status='已完成',last_error='',session_id=?,last_run=?,next_run=?,enabled=CASE WHEN repeat='once' THEN 0 ELSE enabled END WHERE id=?",(sid,time.time(),future,task['id']))
        except Exception as error:
            with app.database() as db: db.execute("UPDATE scheduled_tasks SET status='失败，30 分钟后重试',last_error=? WHERE id=?",(str(error)[:500],task['id']))
            continue
        try:
            subprocess.run(['/usr/bin/osascript','-e','display notification "定时任务已完成，打开 Aurora 查看结果。" with title "Aurora 定时任务"'],capture_output=True,timeout=10,check=False)
        except (OSError,subprocess.TimeoutExpired): pass
