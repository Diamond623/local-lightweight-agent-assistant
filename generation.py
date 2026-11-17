"""Stream local OpenAI-compatible completions without saving truncated replies."""
import json
import queue
import socket
import threading
import urllib.error
import urllib.request


class Cancelled(Exception):
    pass


def _receive(request, timeout, cancelled, lines=False):
    """Keep cancellation responsive even while opening or reading a silent socket."""
    events = queue.Queue(maxsize=8)
    stopped = threading.Event()
    upstream = {}

    def send(kind, value):
        while not stopped.is_set():
            try:
                events.put((kind, value), timeout=.05)
                return
            except queue.Full:
                pass

    def read():
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                upstream['response'] = response
                if lines:
                    while not stopped.is_set():
                        line = response.readline(1_000_000)
                        if not line:
                            break
                        send('data', line)
                elif not stopped.is_set():
                    send('data', response.read())
            send('end', None)
        except Exception as error:
            send('error', error)

    if cancelled():
        raise Cancelled('已停止，本轮未完成回答没有保存。')
    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    try:
        while True:
            if cancelled():
                raise Cancelled('已停止，本轮未完成回答没有保存。')
            try:
                kind, value = events.get(timeout=.05)
            except queue.Empty:
                continue
            if kind == 'end':
                return
            if kind == 'error':
                if isinstance(value, urllib.error.HTTPError):
                    value.close()
                    raise RuntimeError(f'本地模型返回 HTTP {value.code}，请查看状态详情。') from value
                raise value
            yield value
    finally:
        stopped.set()
        # shutdown wakes a worker blocked in readline; close remains owned by it.
        response = upstream.get('response')
        connection = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        worker.join(timeout=.2)


def prepare_payload(url, payload, cancelled):
    """Count the actual rendered Qwen prompt; never truncate the current question."""
    if payload.get('model') != 'aurora-qwen27b':
        return payload
    payload = dict(payload)
    kwargs = dict(payload.get('chat_template_kwargs', {}))
    thinking = kwargs.get('enable_thinking') is True
    kwargs['enable_thinking'] = thinking
    payload['chat_template_kwargs'] = kwargs
    payload['reasoning_budget_tokens'] = 1024 if thinking else 0
    payload['reasoning_format'] = 'deepseek'
    messages = list(payload['messages'])
    base = url.rsplit('/v1/', 1)[0]
    request = urllib.request.Request(base + '/props')
    settings = json.loads(b''.join(_receive(request, 10, cancelled)))
    context = min(8192, settings.get('default_generation_settings', {}).get('n_ctx', 2048))
    # Honor the running server, including a previous 2048-context instance.
    if thinking and context < 8192:
        raise ValueError('深度思考需要 8192 上下文，请重启 27B 模型加载新配置。原对话已保留。')
    output_limit = 3072 if thinking else 2048
    payload['max_tokens'] = min(max(1, int(payload.get('max_tokens', output_limit))), output_limit,
                                context * 3 // 8 if thinking else max(1, context // 4))
    available = context - payload['max_tokens'] - 64
    while True:
        rendered = request_json(base + '/apply-template',
                                {'messages': messages, 'add_generation_prompt': True,
                                 'chat_template_kwargs': payload['chat_template_kwargs']}, cancelled, timeout=10)
        tokens = request_json(base + '/tokenize', {'content': rendered['prompt'], 'add_special': False}, cancelled, timeout=10)
        if len(tokens['tokens']) <= available:
            break
        # Retain system and current user message; discard the oldest history pair.
        if len(messages) > 2:
            del messages[1:min(3, len(messages)-1)]
        else:
            raise ValueError(f'27B 深度模式的 {context} token 上下文已满（已为输出预留 {payload["max_tokens"]} token），请缩短问题、附件或搜索资料，或切换到其他模型。若刚更新配置，请重启模型。原对话已保留。')
    payload['messages'] = messages
    return payload


def request_json(url, payload, cancelled, timeout=90):
    payload = prepare_payload(url, payload, cancelled)
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    chunks = _receive(request, timeout, cancelled)
    try:
        body = b''.join(chunks)
    finally:
        chunks.close()
    if cancelled():
        raise Cancelled('已停止，本轮未完成回答没有保存。')
    return json.loads(body)


def completion(url, payload, emit, cancelled, timeout=240):
    payload = dict(payload, stream=True)
    payload = prepare_payload(url, payload, cancelled)
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    parts, reasoning, finish, complete = [], [], None, False
    thinking_notified = False
    lines = _receive(request, timeout, cancelled, lines=True)
    try:
        for line in lines:
            if cancelled():
                raise Cancelled('已停止生成，本轮未完成回答没有保存。')
            if not line.startswith(b'data:'):
                continue
            raw = line[5:].strip()
            if raw == b'[DONE]':
                # Transport closure alone cannot certify that generation finished.
                # A valid choice must carry its finish_reason before we save it.
                break
            data = json.loads(raw)
            if data.get('error'):
                raise RuntimeError('本地模型生成失败，请查看状态详情。')
            choices = data.get('choices', [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get('delta', {})
            thought = delta.get('reasoning_content') or ''
            if thought:
                reasoning.append(thought)
                if not thinking_notified:
                    emit({'type': 'status', 'message': '模型正在思考，等待正文…'})
                    thinking_notified = True
            text = delta.get('content') or ''
            if text:
                parts.append(text)
                emit({'type': 'delta', 'text': text})
            if choice.get('finish_reason'):
                finish, complete = choice['finish_reason'], True
                break
    finally:
        lines.close()
    if cancelled():
        raise Cancelled('已停止生成，本轮未完成回答没有保存。')
    if not complete:
        raise RuntimeError('模型连接中断，本轮未完成回答没有保存，请重试。')
    return {'choices': [{'message': {'content': ''.join(parts), 'reasoning_content': ''.join(reasoning)}, 'finish_reason': finish}]}
