"""Local accounts, salted scrypt passwords, revocable opaque sessions and data scopes."""
import contextvars
import hashlib
import hmac
from http.cookies import SimpleCookie
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

CURRENT=contextvars.ContextVar('aurora_user',default=None)


def user_id():
    user=CURRENT.get()
    return user['id'] if user else None


def data_directory(base):
    user=CURRENT.get()
    base=base.resolve()
    if user and user.get('service'):return base/'services'/user['service']/str(user['id'])
    return base/'accounts'/str(user['id']) if user and not user['owner'] else base


@contextmanager
def database(base):
    directory=base.resolve()
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'accounts.sqlite3'
    db=sqlite3.connect(path,timeout=15)
    path.chmod(0o600)
    db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,
      display_name TEXT NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,
      owner INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS login_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER,expires REAL);
      CREATE TABLE IF NOT EXISTS login_limits(key TEXT PRIMARY KEY,failures INTEGER,until_time REAL);''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(users)')}
    for name,definition in [('disabled','INTEGER NOT NULL DEFAULT 0'),('created','REAL'),('last_login','REAL')]:
        if name not in columns: db.execute('ALTER TABLE users ADD COLUMN '+name+' '+definition)
    db.executescript('''CREATE TABLE IF NOT EXISTS account_settings(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS account_audit(id INTEGER PRIMARY KEY,actor INTEGER,action TEXT,target INTEGER,created REAL);''')
    try:
        with db: yield db
    finally: db.close()


def accounts(base):
    with database(base) as db:
        return [dict(r) for r in db.execute('SELECT id,username,display_name,owner FROM users WHERE disabled=0 ORDER BY id')]


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32,maxmem=64*1024*1024).hex()


def check_password(value):
    if not isinstance(value,str) or not 10<=len(value)<=128:
        raise ValueError('密码请使用 10—128 个字符')
    return value


def cookie_token(cookie):
    try:
        parsed=SimpleCookie();parsed.load(cookie)
        return parsed['aurora_session'].value if 'aurora_session' in parsed else ''
    except Exception: return ''


def digest(token): return hashlib.sha256(token.encode()).hexdigest()


def resolve(base,cookie):
    token=cookie_token(cookie)
    if not token or len(token)>128: return None
    with database(base) as db:
        db.execute('DELETE FROM login_sessions WHERE expires<=?',(time.time(),))
        row=db.execute('''SELECT u.id,u.username,u.display_name,u.owner FROM users u
          JOIN login_sessions s ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.disabled=0''',(digest(token),time.time())).fetchone()
        return dict(row) if row else None


def api(base,action,obj,user,cookie):
    if not isinstance(obj,dict): raise ValueError('请求格式错误')
    with database(base) as db:
        if action=='state':
            return {'user':user,'setup':not bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone()),'registration_open':registration_open(db)},None
        if action.startswith('admin-'):
            return admin(db,action,obj,user),None
        if action=='logout':
            db.execute('DELETE FROM login_sessions WHERE token_hash=?',(digest(cookie_token(cookie)),))
            return {'ok':True},'aurora_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
        if action=='password':
            if not user: raise ValueError('请先登录')
            row=db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
            verify(db,'password:'+str(user['id']),obj.get('old_password'),row)
            password=check_password(obj.get('password'));salt=secrets.token_hex(16)
            db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,password_hash(password,salt),user['id']))
            db.execute('DELETE FROM login_sessions WHERE user_id=?',(user['id'],))
            return {'ok':True},'aurora_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
        if action not in ('register','login'): raise ValueError('未知账号操作')
        if user: raise ValueError('请先退出当前账号')
        username=str(obj.get('username','')).strip().lower()
        if not re.fullmatch(r'[a-z0-9_\-]{3,32}',username): raise ValueError('账号请使用 3—32 位英文字母、数字、下划线或短横线')
        password=check_password(obj.get('password'))
        if action=='register':
            name=str(obj.get('display_name','')).strip()
            if not 1<=len(name)<=20 or any(ord(c)<32 for c in name): raise ValueError('请填写 1—20 字的称呼')
            # Serialize first-owner claim and duplicate registrations across HTTP threads.
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(): raise ValueError('这个账号已经存在')
            owner=not bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())
            if not owner and not registration_open(db): raise ValueError('管理员暂时关闭了注册')
            salt=secrets.token_hex(16)
            uid=db.execute('INSERT INTO users(username,display_name,salt,password_hash,owner) VALUES(?,?,?,?,?)',(username,name,salt,password_hash(password,salt),int(owner))).lastrowid
            user={'id':uid,'username':username,'display_name':name,'owner':int(owner)}
        else:
            row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            verify(db,'login:'+username,password,row)
            user={key:row[key] for key in ('id','username','display_name','owner')}
        db.execute('UPDATE users SET created=COALESCE(created,?),last_login=? WHERE id=?',(time.time(),time.time(),user['id']))
        token=secrets.token_urlsafe(32)
        remembered=obj.get('remember') is True
        duration=30*86400 if remembered else 12*3600
        db.execute('DELETE FROM login_sessions WHERE expires<=?',(time.time(),))
        db.execute('INSERT INTO login_sessions VALUES(?,?,?)',(digest(token),user['id'],time.time()+duration))
        header='aurora_session='+token+'; Path=/; HttpOnly; SameSite=Strict'
        if remembered: header+='; Max-Age='+str(duration)
        return {'user':user},header


def verify(db,key,password,row):
    limit=db.execute('SELECT * FROM login_limits WHERE key=?',(key,)).fetchone()
    if limit and limit['failures']>=5 and limit['until_time']>time.time(): raise ValueError('尝试次数较多，请一分钟后重试')
    supplied=password if isinstance(password,str) and len(password)<=128 else ''
    expected=row['password_hash'] if row else '0'*64
    actual=password_hash(supplied,row['salt'] if row else '0'*32)
    if not row or not hmac.compare_digest(expected,actual) or row['disabled']:
        count=(limit['failures'] if limit and limit['until_time']>time.time()-600 else 0)+1
        db.execute('INSERT OR REPLACE INTO login_limits VALUES(?,?,?)',(key,count,time.time()+60))
        db.commit()  # Failed attempts must survive the exception rollback.
        raise ValueError('账号或密码不正确')
    db.execute('DELETE FROM login_limits WHERE key=?',(key,))


def registration_open(db):
    row=db.execute("SELECT value FROM account_settings WHERE key='registration_open'").fetchone()
    return not row or row[0]=='1'


def admin(db,action,obj,user):
    # Recheck the role in storage; never trust a supplied owner flag.
    actor=db.execute('SELECT owner,disabled FROM users WHERE id=?',(user['id'],)).fetchone() if user else None
    if not actor or not actor['owner'] or actor['disabled']: raise ValueError('仅管理员可管理账号')
    if action=='admin-list':
        return {'users':[dict(r) for r in db.execute('SELECT id,username,display_name,owner,disabled,created,last_login FROM users ORDER BY id')],
                'registration_open':registration_open(db),
                'audit':[dict(r) for r in db.execute('SELECT * FROM account_audit ORDER BY id DESC LIMIT 50')]}
    if action=='admin-registration':
        if not isinstance(obj.get('enabled'),bool): raise ValueError('注册设置应为布尔值')
        db.execute("INSERT OR REPLACE INTO account_settings VALUES('registration_open',?)",('1' if obj['enabled'] else '0',))
        target=None
    elif action in ('admin-disable','admin-revoke'):
        target=int(obj['id'])
        row=db.execute('SELECT owner FROM users WHERE id=?',(target,)).fetchone()
        if not row: raise ValueError('账号不存在')
        if action=='admin-disable':
            if row['owner']: raise ValueError('不能停用管理员账号')
            if not isinstance(obj.get('disabled'),bool): raise ValueError('账号状态应为布尔值')
            db.execute('UPDATE users SET disabled=? WHERE id=?',(int(obj['disabled']),target))
        db.execute('DELETE FROM login_sessions WHERE user_id=?',(target,))
    else: raise ValueError('未知管理操作')
    db.execute('INSERT INTO account_audit(actor,action,target,created) VALUES(?,?,?,?)',(user['id'],action+(':'+str(obj.get('enabled',obj.get('disabled','')))),target,time.time()))
    return {'ok':True}
