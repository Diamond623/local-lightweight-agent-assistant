"""Local lexical retrieval. Chinese bigrams + FTS5; no embedding model needed."""
import re


def terms(text):
    words = re.findall(r'[a-z0-9+#]+', text.lower())
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        if len(run) == 1:
            words.append(run)
        words.extend(run[i:i + 2] for i in range(len(run) - 1))
    return ' '.join(words)


def setup(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS summaries(
        id INTEGER PRIMARY KEY, session_id INTEGER, first_id INTEGER, last_id INTEGER,
        content TEXT, created TEXT, UNIQUE(session_id,first_id,last_id));
      CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        kind UNINDEXED, source_id UNINDEXED, session_id UNINDEXED,
        content UNINDEXED, terms, tokenize='unicode61');
      CREATE TABLE IF NOT EXISTS memory_meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE TRIGGER IF NOT EXISTS aurora_delete_note AFTER DELETE ON memories BEGIN
        DELETE FROM memory_fts WHERE kind='note' AND source_id=old.id;
      END;
      CREATE TRIGGER IF NOT EXISTS aurora_delete_message AFTER DELETE ON messages BEGIN
        DELETE FROM memory_fts WHERE kind='message' AND source_id=old.id;
      END;
      CREATE TRIGGER IF NOT EXISTS aurora_delete_summary AFTER DELETE ON summaries BEGIN
        DELETE FROM memory_fts WHERE kind='summary' AND source_id=old.id;
      END;
      CREATE TRIGGER IF NOT EXISTS aurora_delete_session AFTER DELETE ON sessions BEGIN
        DELETE FROM memory_fts WHERE session_id=old.id;
        DELETE FROM summaries WHERE session_id=old.id;
      END;
    ''')
    import auto_memory
    auto_memory.setup(db)
    # Only backfill once, retaining the original tables and IDs unchanged.
    if not db.execute("SELECT 1 FROM memory_meta WHERE key='index_v1'").fetchone():
        for kind, table in [('note', 'memories'), ('message', 'messages'), ('summary', 'summaries')]:
            for row in db.execute(f'SELECT * FROM {table}').fetchall():
                index(db, kind, row['id'], row['session_id'] if kind != 'note' else None, row['content'])
        db.execute("INSERT INTO memory_meta VALUES('index_v1','1')")


def index(db, kind, source_id, session_id, content):
    db.execute('DELETE FROM memory_fts WHERE kind=? AND source_id=?', (kind, source_id))
    db.execute('INSERT INTO memory_fts(kind,source_id,session_id,content,terms) VALUES(?,?,?,?,?)',
               (kind, source_id, session_id, content, terms(content)))


def excerpt(content, query, limit=400):
    if len(content) <= limit:
        return content
    tokens = terms(query).split()
    positions = [content.lower().find(t) for t in tokens]
    pos = min((p for p in positions if p >= 0), default=0)
    start = max(0, pos - 100)
    return ('…' if start else '') + content[start:start + limit] + '…'


_STOP={'一下','什么','怎么','怎样','如何','这个','那个','一些','帮我','给我','自己','你自','我想','想要','可以','能不','不能','有没有','有没','还有','一下','一段','一篇','一个','我的','你的','现在','今天'}
_GROUPS=[('姓名','名字','称呼','我叫','叫我'),('考研','研究生','升学'),('编程','代码','程序'),('记忆','记住','保存'),('归结','合一','逻辑'),('简短','简洁','精简')]


def query_terms(query):
    result=[x for x in terms(query).split() if x not in _STOP and len(x)>1]
    for group in _GROUPS:
        if any(x in query for x in group):
            for word in group:result.extend(terms(word).split())
    return list(dict.fromkeys(result))


def retrieval_query(question,previous):
    if re.fullmatch(r'(?:继续|然后呢|还有呢|还有吗|这个呢|再说说|展开讲讲|详细一点|简短一点|为什么呢?)',question.strip(' ？?。！!')):
        prior=next((r['content'] for r in previous if r['role']=='user'),'')
        return prior[:500] or question
    return question


def recall(db, query, session_id, budget=1800):
    """Bounded topic retrieval via FTS, plus explicit communication preferences."""
    import auto_memory
    auto_memory.prune(db)
    selected, seen, used = [], set(), 0
    personal = bool(re.search(r'我是谁|我叫什么|我的名字|我的称呼|还记得我|知道我是谁|你对我了解|你了解我|你(?:还)?认识我', query))
    if personal:
        query += ' 姓名 名字 称呼 我叫 叫我 专业 身份'

    useful=set(query_terms(query))
    elliptical=bool(re.fullmatch(r'(?:继续|然后呢|还有呢|还有吗|这个呢|再说说|展开讲讲|详细一点|简短一点|为什么呢?)',query.strip(' ？?。！!')))

    def add(row,global_preference=False):
        nonlocal used
        key = (row['kind'], int(row['source_id']))
        if key in seen or used >= budget:
            return
        if row['kind']=='note':
            note=db.execute('SELECT active,expires,origin,memory_key FROM memories WHERE id=?',(row['source_id'],)).fetchone()
            if not note or not note['active']:return
        overlap=useful.intersection(query_terms(row['content']))
        if not global_preference and not elliptical and not overlap:return
        if personal and row['kind']=='summary':
            return
        speaker = None
        if row['kind'] == 'message':
            source = db.execute('SELECT role FROM messages WHERE id=?', (row['source_id'],)).fetchone()
            speaker = source['role'] if source else 'unknown'
            if personal and speaker != 'user':
                return
            if db.execute('SELECT 1 FROM memories WHERE active=0 AND source_message_id=?',(row['source_id'],)).fetchone():return
            if db.execute("SELECT 1 FROM memory_events WHERE action='update' AND source_message_id=?",(row['source_id'],)).fetchone():return
            if personal and db.execute("SELECT 1 FROM memory_blocks WHERE memory_key LIKE 'profile.%' AND through_id>=?",(row['source_id'],)).fetchone():return
        content = excerpt(row['content'], query, min(400, budget - used))
        if not content.strip():
            return
        selected.append({'kind': row['kind'], 'id': int(row['source_id']),
                         'session_id': row['session_id'], 'content': content})
        if row['kind']=='note':selected[-1].update(origin=note['origin'],memory_key=note['memory_key'])
        if row['kind'] == 'message':
            selected[-1]['speaker'] = speaker
        seen.add(key)
        used += len(content)
        if row['kind']=='note':db.execute('UPDATE memories SET last_used=?,uses=uses+1 WHERE id=?',(__import__('time').time(),row['source_id']))

    tokens = list(dict.fromkeys(query_terms(query)))[:64]
    if tokens:
        expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in tokens)
        hits = db.execute('''SELECT kind,source_id,session_id,content,bm25(memory_fts) AS rank
          FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT 30''', (expression,)).fetchall()
        # Rank notes and summaries slightly above raw exchanges at comparable relevance.
        hits = sorted(hits, key=lambda r: r['rank'] * (1.3 if r['kind'] == 'note' else 1.1 if r['kind'] == 'summary' else 1))
        # Reserve space for a relevant summary instead of filling every slot with
        # similar verbatim turns from the same long conversation.
        for kind in ('note', 'summary'):
            best = next((r for r in hits if r['kind'] == kind), None)
            if best is not None:
                add(best)
        for row in hits:
            if len(selected) >= 6 or used >= max(400, budget - 500):
                break
            add(row)
    style_expression=' OR '.join('"'+word+'"' for word in ['中文','英文','简短','详细','叫我','称呼','步骤'])
    for row in db.execute('''SELECT kind,source_id,session_id,content FROM memory_fts
                            WHERE kind='note' AND memory_fts MATCH ?
                            ORDER BY CAST(source_id AS INTEGER) DESC LIMIT 2''',(style_expression,)):
        if re.search(r'用(?:中文|英文|英语)|称呼|叫我|(?:回答|回复|讲解|表达).{0,8}(?:简短|简洁|详细|自然|步骤)|(?:简短|详细).{0,4}(?:回答|回复)',row['content']):add(row,global_preference=True)
    # A same-session summary is relevant only for an elliptical continuation.
    if elliptical:
        row = db.execute("SELECT 'summary' AS kind,id AS source_id,session_id,content FROM summaries WHERE session_id=? ORDER BY last_id DESC LIMIT 1", (session_id,)).fetchone()
        if row:add(row)
    return selected


def pending(db, session_id, force=False):
    last = db.execute('SELECT COALESCE(MAX(last_id),0) FROM summaries WHERE session_id=?', (session_id,)).fetchone()[0]
    rows = db.execute('SELECT id,role,content FROM messages WHERE session_id=? AND id>? ORDER BY id', (session_id,last)).fetchall()
    # Leave two recent turns verbatim; summarize older material in bounded batches.
    older = rows[:-4]
    if len(older) < (2 if force else 12):
        return []
    block, size = [], 0
    for row in older[:12]:
        if size + len(row['content']) > 5000 and block and block[-1]['role'] == 'assistant':
            break
        block.append(row)
        size += len(row['content'])
    while block and block[-1]['role'] != 'assistant':
        block.pop()
    return block
