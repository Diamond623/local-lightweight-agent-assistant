"""Bounded per-message local extraction; account context follows the request thread."""
import contextvars
import threading
import auto_memory

_LOCK=threading.Lock()
_QUEUED=set()
_CANCEL={}


def preempt():
    """A new chat takes priority over any queued or running extraction."""
    with _LOCK:
        pending=bool(_CANCEL)
        for stop in _CANCEL.values():stop.set()
        return pending


def schedule(message_id,mode):
    import app
    with app.database() as db:
        row=db.execute('SELECT content FROM messages WHERE id=? AND role=\'user\'',(message_id,)).fetchone()
        if not row or not auto_memory.enabled(db) or not auto_memory.eligible(row['content']):return
        if db.execute('SELECT 1 FROM memory_processed WHERE message_id=?',(message_id,)).fetchone():return
    key=(str(app.auth.data_directory(app.DATA)),message_id)
    with _LOCK:
        if key in _QUEUED or len(_QUEUED)>=8:return
        _QUEUED.add(key)
        stop=threading.Event();_CANCEL[key]=stop
    context=contextvars.copy_context()

    def worker():
        acquired=False
        try:
            import time
            deadline=time.monotonic()+30
            while not stop.is_set() and time.monotonic()<deadline:
                if app.CHAT_LOCK.acquire(timeout=.1):acquired=True;break
            if stop.is_set():return
            if not acquired:raise RuntimeError('模型繁忙，本轮只保留直接识别的记忆')
            with app.database() as db:
                row=db.execute('SELECT content FROM messages WHERE id=?',(message_id,)).fetchone()
                if not row or not auto_memory.enabled(db):return
                text=row['content']
            model=app.model_info(mode)
            if not model:return
            def infer(messages):
                payload={'model':model,'messages':messages,'max_tokens':750,'temperature':0,'stream':False,'chat_template_kwargs':{'enable_thinking':False}}
                result=app.generation.request_json(f'http://127.0.0.1:{app.PORTS[mode]}/v1/chat/completions',payload,stop.is_set,timeout=30)
                choice=result['choices'][0]
                return '' if choice.get('finish_reason')=='length' else choice['message'].get('content','')
            candidates=auto_memory.extract(text,infer)
            if stop.is_set():return
            with app.database() as db:
                changes=auto_memory.apply(db,message_id,text,candidates)
                db.execute('INSERT OR IGNORE INTO memory_processed VALUES(?)',(message_id,))
                db.execute("INSERT OR REPLACE INTO memory_meta VALUES('auto_memory_status',?)",('最近一轮筛选完成：更新 '+str(len(changes))+' 条；未通过证据检查的内容未保存。',))
        except Exception:
            try:
                with app.database() as db:db.execute("INSERT OR REPLACE INTO memory_meta VALUES('auto_memory_status','本轮后台筛选未完成；直接识别的记忆与原聊天已保留。')")
            except Exception:pass
        finally:
            if acquired:app.CHAT_LOCK.release()
            with _LOCK:
                _QUEUED.discard(key);_CANCEL.pop(key,None)
    threading.Thread(target=lambda:context.run(worker),daemon=True).start()
