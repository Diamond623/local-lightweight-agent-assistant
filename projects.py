"""Account-scoped projects, durable task records and reviewed UTF-8 file edits."""
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading
import time
import tomllib

LOCK = threading.RLock()
INITIALIZED=set()
MAX_BYTES = 100_000
SUFFIXES = {'.txt', '.md', '.json', '.csv', '.py', '.c', '.cpp', '.h', '.js', '.ts', '.html', '.css', '.yaml', '.yml', '.toml'}
SEARCH_FILE_LIMIT = 200
SEARCH_DIR_LIMIT = 40
SEARCH_RESULT_LIMIT = 15


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,goal TEXT NOT NULL DEFAULT '',progress TEXT NOT NULL DEFAULT '',created REAL);
    CREATE TABLE IF NOT EXISTS project_sessions(session_id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS project_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,session_id INTEGER NOT NULL,question TEXT NOT NULL,status TEXT NOT NULL,events TEXT NOT NULL DEFAULT '[]',result TEXT NOT NULL DEFAULT '',created REAL,updated REAL);
    CREATE TABLE IF NOT EXISTS project_changes(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,task_id INTEGER,path TEXT NOT NULL,before_text TEXT,after_text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created REAL);
    ''')
    identity=db.execute('PRAGMA database_list').fetchone()[2]
    if identity and identity not in INITIALIZED:
        db.execute("UPDATE project_tasks SET status='interrupted',result='上次运行中断，请核对待审修改后载入原问题重试。' WHERE status='running'")
        INITIALIZED.add(identity)


def project(db, pid):
    row = db.execute('SELECT * FROM projects WHERE id=?', (int(pid),)).fetchone()
    if not row: raise ValueError('项目不存在或不属于当前账号')
    return dict(row)


def root_for(base, pid):
    base = Path(base).resolve()
    directory = base / 'workspaces' / str(int(pid))
    # Reject links at every managed component; never follow an external workspace.
    for part in (base / 'workspaces', directory):
        if part.is_symlink(): raise ValueError('项目目录不能是符号链接')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def target_for(root, name):
    if not isinstance(name, str) or len(name) > 180 or '\\' in name or '\x00' in name:
        raise ValueError('文件路径无效')
    rel = PurePosixPath(name)
    if not name or rel.is_absolute() or any(not p or p in ('..', '.') or p.startswith('.') for p in name.split('/')):
        raise ValueError('只允许项目内非隐藏文件的相对路径')
    if rel.suffix.lower() not in SUFFIXES: raise ValueError('仅支持常见文本与代码文件')
    path = root.joinpath(*rel.parts)
    for part in [path, *path.parents]:
        if part == root: break
        if part.is_symlink(): raise ValueError('不能访问符号链接')
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('路径超出项目目录')
    if path.exists() and (not path.is_file() or path.stat().st_nlink > 1): raise ValueError('仅支持普通文件，不支持硬链接')
    return path


def read_text(path):
    if not path.exists(): return None
    with path.open('rb') as handle: data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise ValueError('文本文件超过 100 KB，请拆分')
    text = data.decode('utf-8')
    if '\x00' in text: raise ValueError('不支持二进制文件')
    return text


def read_page(text, start_line=1, limit=40):
    if type(start_line) is not int or type(limit) is not int or not 1 <= start_line <= 100_000 or not 1 <= limit <= 80:
        raise ValueError('起始行与行数必须是有效整数（每次最多 80 行）')
    lines = text.splitlines(keepends=True)
    if start_line > max(1, len(lines)):
        raise ValueError('起始行超出文件范围')
    page = []; size = 0
    for line in lines[start_line-1:start_line-1+limit]:
        if page and size + len(line) > 2000: break
        page.append(line[:2000-size]); size += len(page[-1])
        if len(page[-1]) < len(line): break
    last = start_line + len(page) - 1
    long_line = bool(page and len(page[-1]) < len(lines[last-1]))
    return {'text': ''.join(page), 'start_line': start_line, 'end_line': last,
            'total_lines': len(lines), 'next_line': last + 1 if last < len(lines) else None,
            'truncated': last < len(lines) or long_line,
            'long_line_truncated': long_line}


def search_files(root, query):
    """Literal bounded search inside this account's managed workspace only."""
    if not isinstance(query,str) or not 2 <= len(query.strip()) <= 80 or '\x00' in query:
        raise ValueError('请输入 2—80 个字的搜索词')
    query=query.strip().casefold()
    result=[]; scanned=0; directories=0; pending=[root]; limited=False
    while pending and scanned<SEARCH_FILE_LIMIT and directories<SEARCH_DIR_LIMIT and len(result)<SEARCH_RESULT_LIMIT:
        current=pending.pop();directories+=1
        if current.is_symlink():continue
        try:
            with os.scandir(current) as entries:
                candidates=sorted((entry for _,entry in zip(range(201),entries)),key=lambda e:e.name)
            if len(candidates)>200:limited=True
        except OSError:continue
        for entry in candidates:
            if entry.name.startswith('.') or entry.is_symlink():continue
            path=Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                if len(path.relative_to(root).parts)<=6 and entry.name not in ('node_modules','venv','__pycache__','dist','build'):
                    pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in SUFFIXES:continue
            if len(path.relative_to(root).as_posix())>180:continue
            try:
                if entry.stat(follow_symlinks=False).st_size>MAX_BYTES or entry.stat(follow_symlinks=False).st_nlink!=1:continue
                text=read_text(target_for(root,path.relative_to(root).as_posix()))
            except (OSError,UnicodeError,ValueError):continue
            if text is None:continue
            scanned+=1
            for line_number,line in enumerate(text.splitlines(),1):
                if query in line.casefold():
                    result.append({'path':path.relative_to(root).as_posix(),'line':line_number,'excerpt':line.strip()[:180]})
                    if len(result)>=SEARCH_RESULT_LIMIT:break
            if scanned>=SEARCH_FILE_LIMIT or len(result)>=SEARCH_RESULT_LIMIT:break
    return {'matches':result,'scanned_files':scanned,
            'truncated':limited or bool(pending) or scanned>=SEARCH_FILE_LIMIT or len(result)>=SEARCH_RESULT_LIMIT}


def check_code(path,content):
    """Parse selected formats without running imports, tests, or user code."""
    if not isinstance(content,str) or len(content.encode('utf-8'))>MAX_BYTES or '\x00' in content:
        raise ValueError('检查内容须为不超过 100 KB 的文本')
    suffix=PurePosixPath(path).suffix.lower()
    try:
        if suffix=='.py':ast.parse(content,filename=path)
        elif suffix=='.json':json.loads(content)
        elif suffix=='.toml':tomllib.loads(content)
        else:return {'status':'unsupported','message':'此格式尚无安全的静态语法检查；未运行代码。'}
    except (SyntaxError,json.JSONDecodeError,tomllib.TOMLDecodeError) as exc:
        return {'status':'failed','message':f'{suffix} 语法错误：第 {getattr(exc,"lineno",1) or 1} 行：{str(exc).splitlines()[0][:160]}'}
    return {'status':'passed','message':f'{suffix} 静态语法检查通过；未执行测试或代码。'}


def listing(root):
    result=[]
    # Bounded, non-recursive per request: directories are traversed by path.
    for path in sorted(root.iterdir(),key=lambda p:p.name)[:300]:
        if path.name.startswith('.') or path.is_symlink(): continue
        if path.is_dir(): result.append({'path':path.name+'/', 'directory':True})
        elif path.suffix.lower() in SUFFIXES: result.append({'path':path.name,'bytes':path.stat().st_size})
    return result


def revision(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest() if text is not None else None


def preview(row):
    before=row['before_text'];after=row['after_text']
    lines=difflib.unified_diff((before or '').splitlines(True),after.splitlines(True),fromfile='原文件/'+row['path'],tofile='新文件/'+row['path'])
    diff=''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n' for line in lines)
    return {'id':row['id'],'path':row['path'],'status':row['status'],'task_id':row['task_id'],'diff':diff,'new_file':before is None}


def propose(db, base, pid, path, content, task_id=None, base_revision=None):
    project(db,pid)
    if not isinstance(content,str) or len(content.encode('utf-8'))>MAX_BYTES or '\x00' in content: raise ValueError('修改内容须为不超过 100 KB 的文本')
    target=target_for(root_for(base,pid),path);before=read_text(target)
    if revision(before)!=base_revision: raise ValueError('文件版本已变化或尚未读取。请保留草稿，重新读取当前文件并合并后再生成修改。')
    if before==content: raise ValueError('文件内容没有变化')
    cur=db.execute('INSERT INTO project_changes(project_id,task_id,path,before_text,after_text,created) VALUES(?,?,?,?,?,?)',(pid,task_id,path,before,content,time.time()))
    return {'proposal':cur.lastrowid,'path':path,'status':'pending','message':'修改建议已保存，等待用户查看差异并应用；文件尚未写入。'}


def atomic_write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.aurora-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as handle: handle.write(text)
        if path.exists(): os.chmod(name,path.stat().st_mode & 0o777)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def sync_task_review(db,task_id):
    if task_id is not None:
        pending=db.execute("SELECT 1 FROM project_changes WHERE task_id=? AND status IN ('pending','applying')",(task_id,)).fetchone()
        if not pending:
            db.execute("UPDATE project_tasks SET status='reviewed',updated=? WHERE id=? AND status='waiting_review'",(time.time(),task_id))


def change(db,base,cid,action):
    row=db.execute('SELECT * FROM project_changes WHERE id=?',(int(cid),)).fetchone()
    if not row: raise ValueError('修改记录不存在')
    row=dict(row);project(db,row['project_id'])
    if action=='reject':
        if row['status']!='pending': raise ValueError('只能忽略待审修改')
        db.execute("UPDATE project_changes SET status='rejected' WHERE id=?",(cid,));sync_task_review(db,row['task_id']);return {'ok':True}
    expected='pending' if action=='apply' else 'applied'
    if row['status']!=expected: raise ValueError('修改状态已变化，请刷新')
    path=target_for(root_for(base,row['project_id']),row['path'])
    before=row['before_text'] if action=='apply' else row['after_text']
    after=row['after_text'] if action=='apply' else row['before_text']
    if read_text(path)!=before: raise ValueError('文件已被其他操作修改，已停止以避免覆盖。请重新生成修改建议。')
    # Reserve state before touching disk. Any uncertain outcome remains explicit.
    db.execute("UPDATE project_changes SET status='applying' WHERE id=?",(cid,));db.commit()
    try:
        if after is None: path.unlink()
        else: atomic_write(path,after)
    except Exception:
        # Persist rollback of the reserved state even when the outer transaction aborts.
        # If disk content changed unexpectedly, keep 'applying' for manual inspection.
        try:
            if read_text(path)==before:
                db.execute('UPDATE project_changes SET status=? WHERE id=?',(expected,cid));db.commit()
        except (OSError,ValueError): pass
        raise
    db.execute('UPDATE project_changes SET status=? WHERE id=?',('applied' if action=='apply' else 'undone',cid))
    sync_task_review(db,row['task_id'])
    return {'ok':True,'path':row['path']}


def session_project(db,sid):
    row=db.execute('SELECT p.* FROM projects p JOIN project_sessions s ON s.project_id=p.id WHERE s.session_id=?',(sid,)).fetchone()
    return dict(row) if row else None


def begin(db,sid,question):
    setup(db)
    if not db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone(): raise ValueError('对话不存在')
    p=session_project(db,sid)
    if not p:return None
    t=time.time();cur=db.execute('INSERT INTO project_tasks(project_id,session_id,question,status,created,updated) VALUES(?,?,?,?,?,?)',(p['id'],sid,question[:4000],'running',t,t))
    return {'id':cur.lastrowid,'project':p}


def event(db,tid,item):
    row=db.execute('SELECT events FROM project_tasks WHERE id=?',(tid,)).fetchone()
    events=json.loads(row[0]);events.append(item)
    db.execute('UPDATE project_tasks SET events=?,updated=? WHERE id=?',(json.dumps(events[-30:],ensure_ascii=False),time.time(),tid))


def finish(db,tid,status,result):
    db.execute('UPDATE project_tasks SET status=?,result=?,updated=? WHERE id=?',(status,result[:6000],time.time(),tid))


def execute(db,base,task,action,obj):
    pid=task['project']['id'];project(db,pid);root=root_for(base,pid)
    if action=='project_info':return json.dumps(project(db,pid),ensure_ascii=False)
    path=obj.get('path','')
    if action=='search_files':
        return json.dumps(search_files(root,obj.get('query')),ensure_ascii=False)
    if action=='list_files':
        directory=root
        if path:
            if not isinstance(path,str) or len(path)>180 or any(x.startswith('.') or not x for x in path.split('/')) or '\\' in path or path.startswith('/'):raise ValueError('目录路径无效')
            for part in path.split('/'):
                directory=directory/part
                if directory.is_symlink():raise ValueError('不能访问符号链接')
            if not directory.resolve().is_relative_to(root.resolve()):raise ValueError('路径越界')
        return json.dumps(listing(directory),ensure_ascii=False)[:8000]
    if action=='read_file':
        text=read_text(target_for(root,path))
        if text is None:raise ValueError('文件不存在')
        task.setdefault('_read',{})[path]=revision(text)
        page=read_page(text,obj.get('start_line',1),obj.get('limit',40))
        return json.dumps({'path':path,'revision':revision(text),**page},ensure_ascii=False)
    if action=='check_code':
        target_for(root,path)
        return json.dumps(check_code(path,obj.get('content')),ensure_ascii=False)
    if action=='propose_file':
        target_for(root,path)
        validation=check_code(path,obj.get('content'))
        if validation['status']=='failed':raise ValueError(validation['message']+'；修改建议尚未创建。')
        before=read_text(target_for(root,path))
        if before is not None and (len(before)>2000 or task.get('_read',{}).get(path)!=hashlib.sha256(before.encode()).hexdigest()):raise ValueError('须先完整读取当前文件才能提出修改；超过 2000 字请在界面手动编辑')
        proposal=propose(db,base,pid,path,obj.get('content'),task['id'],revision(before))
        proposal['check']=validation
        return json.dumps(proposal,ensure_ascii=False)
    if action=='propose_patch':
        target=target_for(root,path)
        old,new=obj.get('old'),obj.get('new')
        if not isinstance(old,str) or not old or len(old)>2000 or not isinstance(new,str) or len(new)>2000:
            raise ValueError('精确替换须提供不超过 2000 字的非空原文和替换文字')
        before=read_text(target)
        if before is None:raise ValueError('文件不存在；新文件请使用 propose_file')
        base_revision=task.get('_read',{}).get(path)
        if not base_revision or base_revision!=revision(before):
            raise ValueError('文件版本已变化或尚未读取，请重新读取文件后再提出修改')
        if before.count(old)!=1:raise ValueError('原文必须在当前文件中恰好出现一次；请补充上下文后重试')
        after=before.replace(old,new,1)
        validation=check_code(path,after)
        if validation['status']=='failed':raise ValueError(validation['message']+'；修改建议尚未创建。')
        proposal=propose(db,base,pid,path,after,task['id'],base_revision)
        proposal['check']=validation
        return json.dumps(proposal,ensure_ascii=False)
    raise ValueError('未知项目工具')


def api(db,base,action,obj):
    setup(db)
    if action=='list':
        return {'projects':[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY id DESC')], 'current':session_project(db,int(obj.get('session') or 0))}
    if action=='create':
        name=str(obj.get('name','')).strip();goal=str(obj.get('goal','')).strip()
        if not name or len(name)>80 or len(goal)>1000:raise ValueError('项目名 1—80 字，目标最多 1000 字')
        cur=db.execute('INSERT INTO projects(name,goal,created) VALUES(?,?,?)',(name,goal,time.time()));pid=cur.lastrowid;root_for(base,pid)
        return {'id':pid}
    if action=='change':
        if obj.get('operation') not in ('apply','undo','reject'):raise ValueError('未知修改操作')
        return change(db,base,obj['id'],obj['operation'])
    if action=='unbind':
        db.execute('DELETE FROM project_sessions WHERE session_id=?',(int(obj['session']),));return {'ok':True}
    pid=int(obj['project']);p=project(db,pid)
    if action=='new-chat':
        cur=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',(p['name'][:80],time.strftime('%Y-%m-%dT%H:%M:%S')))
        sid=cur.lastrowid
        db.execute('INSERT INTO project_sessions VALUES(?,?)',(sid,pid))
        return {'id':sid}
    if action=='bind':
        sid=int(obj['session'])
        if not db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone():raise ValueError('对话不存在')
        db.execute('INSERT OR REPLACE INTO project_sessions VALUES(?,?)',(sid,pid));return {'ok':True}
    if action=='update':
        goal=str(obj.get('goal',''));progress=str(obj.get('progress',''))
        if len(goal)>1000 or len(progress)>2000:raise ValueError('目标最多 1000 字，进度最多 2000 字')
        db.execute('UPDATE projects SET goal=?,progress=? WHERE id=?',(goal,progress,pid));return {'ok':True}
    if action=='propose':return propose(db,base,pid,obj.get('path'),obj.get('content'),base_revision=obj.get('base_revision'))
    if action=='read':
        text=read_text(target_for(root_for(base,pid),obj.get('path')))
        return {'text':text,'revision':revision(text)}
    if action=='detail':
        return {'project':p,'directory':str(root_for(base,pid)),'files':listing(root_for(base,pid)),
          'tasks':[dict(r) for r in db.execute('SELECT * FROM project_tasks WHERE project_id=? ORDER BY id DESC LIMIT 30',(pid,))],
          'changes':[preview(dict(r)) for r in db.execute('SELECT * FROM project_changes WHERE project_id=? ORDER BY id DESC LIMIT 20',(pid,))]}
    raise ValueError('未知项目操作')
