"""Extract document text locally; bounded excerpts for small model contexts."""
import base64
import io
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import memory_engine

ROOT = Path(__file__).resolve().parent


def extract(name, encoded):
    if len(encoded) > 28_000_000:
        raise ValueError('文件超过 20MB，请拆分后上传')
    data = base64.b64decode(encoded, validate=True)
    if len(data) > 20_000_000:
        raise ValueError('文件超过 20MB')
    suffix = Path(name).suffix.lower()
    warnings, images, pages = [], [], None
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 8_000_000:
                raise ValueError('Word 解压后内容过大，请拆分')
            root = ET.fromstring(z.read(info))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in root.findall('.//w:p',ns))
        warnings.append('Word 仅提取正文和表格文字；内嵌图片、批注、公式对象未解析。')
    elif suffix == '.pdf':
        reader = ROOT / 'PDFRead'
        if not reader.is_file():
            raise ValueError('PDF 读取工具尚未编译，请运行“准备文档工具.command”')
        with tempfile.TemporaryDirectory(prefix='aurora-pdf-') as tmp:
            source = Path(tmp) / 'input.pdf'
            source.write_bytes(data)
            p = subprocess.run([str(reader),str(source)],capture_output=True,timeout=90)
            if p.returncode:
                raise ValueError('PDF 读取失败：' + p.stderr.decode(errors='replace')[:500])
            import json
            result = json.loads(p.stdout)
        text, images, pages = result['text'],result['images'],result['pages']
        if result['scanned']:
            warnings.append(f'发现 {result["scanned"]} 页文字较少的页面，仅最多前 2 页转成图片供模型查看；其余扫描页请拆分上传。')
        if result['limited']:
            warnings.append('文件超过本次完整处理范围：最多读取前 200 页文字、2 页扫描图。')
    elif suffix in {'.txt','.md','.csv','.json','.py','.c','.cpp','.h'}:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('gb18030')
    else:
        raise ValueError('支持 PDF、DOCX、TXT、Markdown、CSV 和常见代码文本；旧版 DOC 请先另存为 DOCX。')
    if len(text) > 200000:
        warnings.append('正文超过 20 万字符，当前只保留前 20 万字符。')
        text = text[:200000]
    if not text.strip() and not images:
        raise ValueError('没有提取到可读文字或图片')
    return {'name':Path(name).name,'text':text,'images':images,'pages':pages,'warnings':warnings}


def identity(document):
    """Identify content, so a new file with the same name cannot inherit old context."""
    digest = hashlib.sha256()
    for value in [str(document.get('text', ''))[:200000], *document.get('images', [])[:2]]:
        digest.update(str(value).encode())
        digest.update(b'\0')
    return digest.hexdigest()


def retrieval_question(document, question, previous):
    if len(question) > 45 or not re.search(r'继续|详细|具体|为什么|它|这个|其中|那|再解释|举例|简短', question):
        return question
    row = next((r for r in previous if r['role'] == 'user'), None)
    if row is None:
        return question
    try:
        meta = json.loads(row['attachment'] or '{}')
        if meta.get('id') == identity(document):
            return str(meta.get('query', ''))[:500] + '\n' + question
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        pass
    return question


def conversation_history(document, previous):
    """A different attachment must not supply old file facts to the current one."""
    current = identity(document)
    for row in previous:
        if row['role'] != 'user':
            continue
        try:
            meta = json.loads(row['attachment'] or '{}')
            if meta.get('id'):
                return previous if meta['id'] == current else []
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            continue
    return previous


def excerpts(document, question, budget=2000):
    text = str(document.get('text',''))[:200000]
    # PDFRead preserves page markers. Honour explicit page requests before ranking.
    requested_pages = list(re.finditer(r'第\s*(\d+)\s*页', question))
    requested = requested_pages[-1] if requested_pages else None
    pages = list(re.finditer(r'【第 (\d+) 页】', text))
    if requested and pages:
        target = int(requested.group(1))
        found = next((i for i, match in enumerate(pages) if int(match.group(1)) == target), None)
        if found is None:
            return f'【第 {target} 页未包含在已提取文字中；不能依据其他页面回答该页内容】'
        start = pages[found].end()
        end = pages[found+1].start() if found+1 < len(pages) else len(text)
        label = f'【第 {target} 页，部分文字】\n'
        return (label + memory_engine.excerpt(text[start:end], question, max(1, budget-len(label)-2)))[:budget]
    chunks = [text[i:i+900] for i in range(0,len(text),750)]
    keys = set(memory_engine.terms(question).split())
    ranked = sorted(enumerate(chunks),key=lambda x:len(keys & set(memory_engine.terms(x[1]).split())),reverse=True)
    # A request to summarize needs coverage of the whole document, not only the opening.
    if any(word in question for word in ['总结','概括','摘要']) and len(chunks)>3:
        selected = [0,len(chunks)//2,len(chunks)-1]
    else:
        selected = sorted(i for i,_ in ranked[:3])
    per_chunk = max(1, (budget - 100) // max(1,len(selected)))
    selected_text = '\n\n'.join(f'【文档片段 {i+1}/{len(chunks)}】\n{memory_engine.excerpt(chunks[i],question,per_chunk)}' for i in selected)
    return selected_text[:budget]
