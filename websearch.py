"""Best-effort public search with retries, fallback and relevance checks."""
import html
from html.parser import HTMLParser
import re
import time
import socket
import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import memory_engine
import generation
import search_plan

LAST_SEARCH = {'query':'','steps':[]}
DNS_CACHE = {}
FAKE_IP_NETWORK = ipaddress.ip_network('198.18.0.0/15')
TEMPERATURE = re.compile(r'-?\d+(?:\.\d+)?\s*(?:℃|°\s*[Cc]?|摄氏度)')


def failure_reason(error):
    reason=str(getattr(error,'reason',error))
    if 'CERTIFICATE_VERIFY_FAILED' in reason:return '网站证书校验失败'
    if isinstance(error,(TimeoutError,socket.timeout)) or 'timed out' in reason:return '连接超时'
    if isinstance(error,socket.gaierror):return '域名解析失败'
    if isinstance(error,urllib.error.HTTPError):return f'网页返回 HTTP {error.code}'
    if isinstance(error,ValueError):return reason[:160]
    if isinstance(error,urllib.error.URLError):return '网络连接失败'
    return type(error).__name__


class HeadingSearchParser(HTMLParser):
    """Read result headings and published destination attributes, not guessed URLs."""
    def __init__(self,base):
        super().__init__();self.base=base;self.stack=[];self.heading=False;self.current=None;self.rows=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        destination=attrs.get('data-mdurl') or attrs.get('mu') or attrs.get('data-landurl')
        if tag not in ('meta','link','input','img','br','hr','source','wbr'):
            self.stack.append((tag,destination))
        if tag in ('script','style'):self.skip+=1
        if tag=='h3':self.heading=True;self.current={'title':'','url':'','snippet':''}
        if tag=='a' and self.heading and not self.current['url']:
            parent=next((url for _,url in reversed(self.stack) if url),None)
            self.current['url']=urllib.parse.urljoin(self.base,attrs.get('data-mdurl') or parent or attrs.get('href',''))
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
        if tag=='h3':
            self.heading=False
            if self.current and self.current['url'] and self.current['title'].strip():self.rows.append(self.current)
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,data):
        if self.skip or not self.current:return
        key='title' if self.heading else 'snippet'
        self.current[key]=(self.current[key]+data.strip()+' ')[:700]


def heading_search(base,parameter,query):
    # Verbose measurement words can push current-weather searches toward old news.
    if '天气' in query:query=re.sub(r'\s+(?:气温|降水)(?=\s|$)','',query)
    body=read_url(base+'?'+urllib.parse.urlencode({parameter:query}))
    parser=HeadingSearchParser(base);parser.feed(body.decode('utf-8',errors='replace'))
    rows=[]
    for row in parser.rows:
        if row['url'].startswith('http://'):row['url']='https://'+row['url'][7:]
        rows.append(row)
    if not rows:raise ValueError('搜索没有返回可读结果，可能需要验证')
    return rows[:15]


def baidu(query):return heading_search('https://www.baidu.com/s','wd',query)


def so360(query):return heading_search('https://www.so.com/s','q',query)


def search_providers():return (baidu,so360,duckduckgo,bing)

WEATHER_CLARIFICATION = '你想查询哪个城市的天气？请告诉我城市名，例如“成都”或“新加坡”。'


def prepare_query(question, previous):
    clean=re.sub(r'[\s，。？！?！!,.]','',question)
    if re.fullmatch(r'(?:今天|明天|后天|现在|目前)?的?天气(?:怎么样|如何|好吗|情况|预报)?',clean):
        return question, WEATHER_CLARIFICATION
    if previous and previous[0]['role']=='assistant' and previous[0]['content']==WEATHER_CLARIFICATION:
        if re.fullmatch(r'[\u3400-\u9fffA-Za-z ·.-]{2,40}',clean) and not any(x in clean for x in ['不用','不查','算了','谢谢']):
            return clean+' 今天天气 温度 降水',None
    return question,None


def needs_search(question):
    text=re.sub(r'[\s，。？！?！!,.]','',question).lower()
    if re.fullmatch(r'你(?:现在|目前|到底)?(?:是谁|是什么模型|是哪个模型|是哪个公司(?:开发的)?|是哪家公司(?:开发的)?|叫什么(?:名字)?|由谁开发(?:的)?|的底层模型是什么)',text):
        return False
    return not bool(re.fullmatch(r'(你好|您好|谢谢|好的|你是谁|你叫什么名字|你是什么模型|你是谁开发的|谁开发的你|谁开发了你|你是哪个公司的|你能做什么|你有什么功能|你还记得我吗)',text))


def read_url(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/605.1.15 Safari/605.1.15','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.7'})
    with urllib.request.urlopen(request,timeout=10) as response:
        return response.read(1_500_000)


def bing(query):
    url='https://www.bing.com/search?'+urllib.parse.urlencode({'format':'rss','q':query,'mkt':'zh-CN','setlang':'zh-hans'})
    feed=ET.fromstring(read_url(url))
    if feed.tag.lower()!='rss':
        raise ValueError('搜索返回了非 RSS 页面')
    return [{'title':html.unescape(item.findtext('title','')),
             'url':item.findtext('link',''),
             'snippet':re.sub('<[^>]+>','',html.unescape(item.findtext('description','')))[:700]}
            for item in feed.findall('.//item')[:10]]


class DDGParser(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.current=None;self.field=None;self.depth=0

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);classes=attrs.get('class','').split()
        field='title' if tag=='a' and 'result__a' in classes else 'snippet' if 'result__snippet' in classes else None
        if field=='title':
            self.current={'title':'','url':attrs.get('href',''),'snippet':''}
            self.rows.append(self.current)
        if field and self.current:
            self.field=field;self.depth=1
        elif self.field:
            self.depth+=1

    def handle_endtag(self,tag):
        if self.field:
            self.depth-=1
            if self.depth<=0:self.field=None

    def handle_data(self,data):
        if self.field and self.current:self.current[self.field]+=data


def duckduckgo(query):
    body=read_url('https://html.duckduckgo.com/html/?'+urllib.parse.urlencode({'q':query}))
    parser=DDGParser();parser.feed(body.decode('utf-8',errors='replace'))
    for row in parser.rows:
        url=row['url']
        if url.startswith('//'):url='https:'+url
        parsed=urllib.parse.urlparse(url)
        if parsed.hostname and parsed.hostname.endswith('duckduckgo.com'):
            url=urllib.parse.parse_qs(parsed.query).get('uddg',[url])[0]
        row['url']=url;row['snippet']=row['snippet'][:700]
    if not parser.rows:
        raise ValueError('备用搜索未返回可用结果，可能需要浏览器验证')
    return parser.rows


def place_matches(text,place):
    normalize=lambda s:re.sub(r'[\s·,，。:：()（）-]','',s).casefold()
    key=re.sub(r'(?:市|县|区)$','',place)
    return bool(key and normalize(key) in normalize(text))


def places_list(value):
    return [value] if isinstance(value,str) else list(value or [])


def matches_places(text,value):
    places=places_list(value)
    return not places or any(place_matches(text,p) for p in places)


def relevant(rows,query,required_place=None):
    query=search_plan.clean_question(query)
    # Remove request boilerplate, retaining technical and topic terms.
    topic=re.sub(r'请帮我|帮我|搜索一下|查一下|联网搜索|上网搜索|最新的|现在的|是什么|有哪些|为什么|怎么样|怎么|如何|介绍一下|最新|最近|近期|今天|明天|后天|现在|目前|请问',' ',query)
    keys=set(memory_engine.terms(topic).split())
    keys -= {'关于','消息','情况','数据','信息','介绍','哪个','公司','是谁','开发','查询','搜索'}
    anchors={k for k in keys if re.fullmatch(r'[a-z][a-z0-9+#]{1,}',k) and k not in {'the','what','how','is','are','please','today','latest','news','weather'}}
    if not keys:
        return []
    ranked=[];seen=set()
    for row in rows:
        url=row.get('url','')
        if urllib.parse.urlparse(url).scheme not in ('https','http') or url in seen:continue
        seen.add(url)
        text=(row.get('title','')+' '+row.get('snippet','')).lower()
        if not matches_places(text,required_place):continue
        if anchors and not any(anchor in text for anchor in anchors):continue
        # Generic date words cannot qualify a car listing or calendar as weather.
        if '天气' in query and not any(term in text for term in ['天气','气温','降水','weather','forecast','temperature']):
            continue
        matches=[key for key in keys if key in text]
        # Require one substantive keyword, not just a grammatical single character.
        if not any(len(key)>=2 for key in matches):continue
        score=sum(2 if key in row.get('title','').lower() else 1 for key in matches)
        ranked.append((score,row))
    return [row for _,row in sorted(ranked,key=lambda pair:pair[0],reverse=True)[:5]]


def public_dns(hostname):
    cached=DNS_CACHE.get(hostname)
    if cached and time.monotonic()<cached[0]:return cached[1]
    errors=[]
    for service in ('https://dns.alidns.com/resolve','https://cloudflare-dns.com/dns-query'):
        def record(kind):
            req=urllib.request.Request(service+'?'+urllib.parse.urlencode({'name':hostname,'type':kind}),headers={'Accept':'application/dns-json','User-Agent':'AuroraLocal/1.0'})
            with urllib.request.urlopen(req,timeout=10) as response:return json.loads(response.read(20000))
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:answers=list(executor.map(record,('A','AAAA')))
            if any(r.get('Status')!=0 for r in answers):raise ValueError('公开 DNS 未找到该网页域名')
            records=[r for a in answers for r in a.get('Answer',[]) if r.get('type') in (1,28)]
            addresses=[r['data'] for r in records]
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise ValueError('网页域名未解析到公网地址')
            ttl=min(60,max(1,min(r.get('TTL',30) for r in records)))
            DNS_CACHE[hostname]=(time.monotonic()+ttl,addresses)
            return addresses
        except Exception as e:errors.append(failure_reason(e))
    raise ValueError('公开 DNS 查询失败：'+'；'.join(dict.fromkeys(errors)))


def validate_public_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只读取公开的 HTTP/HTTPS 网页')
    if parsed.port not in (None,80,443):raise ValueError('不读取非标准端口')
    hostname=parsed.hostname.lower().rstrip('.')
    try:
        literal=ipaddress.ip_address(hostname)
    except ValueError:
        literal=None
    if literal is not None:
        if not literal.is_global:raise ValueError('不访问本地或内网地址')
        return
    if '.' not in hostname or hostname.endswith(('.localhost','.local','.internal')):
        raise ValueError('不访问本地或内网地址')
    try:
        addresses=[row[4][0].split('%')[0] for row in socket.getaddrinfo(hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)]
    except socket.gaierror:
        addresses=public_dns(hostname)
    # TUN clients use benchmark addresses as virtual public-site destinations.
    # Confirm the hostname with public DNS; never permit literal/private URLs.
    private=[ipaddress.ip_address(a) for a in addresses if not ipaddress.ip_address(a).is_global]
    if private and all(a.version==4 and a in FAKE_IP_NETWORK for a in private):
        addresses=public_dns(hostname)
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('不访问本地或内网地址')


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__();self.skip=0;self.stack=[];self.in_title=False;self.title=[];self.parts=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        label=attrs.get('id','')+' '+attrs.get('class','')
        boilerplate=bool(re.search(r'nav|menu|footer|header|sidebar|city[-_]box|city[-_]guonei|city[-_]guowai|recommend|hot[-_]cit|search',label,re.I))
        hidden='hidden' in attrs or attrs.get('aria-hidden')=='true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',attrs.get('style',''),re.I))
        blocked=tag in ('head','script','style','noscript','svg','nav','footer','aside','header') or boilerplate or hidden
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append((tag,blocked));self.skip+=int(blocked)
        if tag=='title':self.in_title=True
        if not self.skip and tag in ('p','div','br','li','h1','h2','h3','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                self.skip-=sum(int(blocked) for _,blocked in self.stack[i:])
                del self.stack[i:]
                break
        if tag=='title':self.in_title=False
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        elif not self.skip:self.parts.append(data+' ')


def read_page(url,required_place=None):
    validate_public_url(url)
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 AuroraLocal/1.0','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.7'})
    opener=urllib.request.build_opener(PublicRedirect())
    with opener.open(req,timeout=10) as response:
        if response.url!=url:validate_public_url(response.url)
        kind=response.headers.get_content_type()
        if kind not in ('text/html','text/plain','application/xhtml+xml'):raise ValueError('网页格式不支持')
        body=response.read(2_000_000)
        encoding=response.headers.get_content_charset()
        if not encoding:
            match=re.search(br'charset\s*=\s*[\"\x27]?([a-zA-Z0-9_-]+)',body[:4096])
            encoding=match.group(1).decode('ascii') if match else 'utf-8'
        if encoding.lower() in ('gb2312','gbk'):encoding='gb18030'
    parser=PageParser();parser.feed(body.decode(encoding,errors='replace'))
    title=''.join(parser.title).strip()
    # Check the actual destination page, not just the search engine's label.
    if not matches_places(title,required_place):
        raise ValueError(f'网页标题不属于所查询地点“{"、".join(places_list(required_place))}”，已丢弃')
    text='\n'.join(re.sub(r'\s+',' ',line).strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    if len(text.strip())<80:raise ValueError('网页正文过少或需要浏览器验证')
    if re.search(r'just a moment|access denied|安全验证|人机验证',title,re.I):raise ValueError('网页需要浏览器验证')
    return title,text[:24000]


def page_excerpt(text,query,limit=1300,required_place=None):
    chunks=[text[i:i+700] for i in range(0,len(text),600)]
    keys=set(memory_engine.terms(query).split())
    def score(pair):
        content=pair[1].lower()
        value=sum(k in content for k in keys)+min(5,len(re.findall(r'\d+(?:\.\d+)?\s*[℃°%]',content)))+12*sum(place_matches(content,p) for p in places_list(required_place))
        if '天气' in query:
            value+=100*bool(TEMPERATURE.search(content))
            value+=35*bool(re.search(r'今天|今日|实时|当前|发布时间|更新时间',content))
            value-=45*any(word in content for word in ('重大天气事件','周边城市','周边景点','热门景点','景点推荐'))
        return value
    best=sorted(enumerate(chunks),key=score,reverse=True)[:1 if required_place else 2]
    if required_place and best and best[0][0]+1<len(chunks):
        i=best[0][0]+1;best.append((i,chunks[i]))
    best.sort(key=lambda pair:pair[0])
    allowance=max(1,(limit-50)//max(1,len(best)))
    return '\n'.join(memory_engine.excerpt(t,query,allowance) for _,t in best)[:limit]


def search_pages(query,required_place=None,check_cancel=None):
    """Query afresh; accept only pages for the current explicit location."""
    query=search_plan.clean_question(query)
    LAST_SEARCH.update(query=query,steps=[],ok=False)
    def check():
        if check_cancel and check_cancel():raise generation.Cancelled('已停止联网，本轮未完成回答没有保存。')
    check()
    steps=LAST_SEARCH['steps']
    successes=[]
    seen=set()
    places=places_list(required_place)
    def complete():
        return successes and all(any(place_matches(r['title'],p) for r in successes) for p in places)
    supplied=re.findall(r'https?://[^\s<>"，。]+',query)
    if supplied:
        for url in supplied[:3]:
            check()
            title,body=read_page(url,required_place)
            successes.append({'title':title,'url':url,'snippet':'','content':page_excerpt(body,query,required_place=required_place),'read':True})
        if complete():LAST_SEARCH['ok']=True;return successes
        raise RuntimeError('所提供网页未覆盖本轮查询的地点，没有使用其他地点数据。')
    names={'baidu':'百度','so360':'360搜索','duckduckgo':'DuckDuckGo','bing':'Bing'}
    for provider in search_providers():
        check()
        identifier=getattr(provider,'__name__','搜索入口')
        name=names.get(identifier,identifier)
        try:
            raw=provider(query)
            rows=relevant(raw,query,required_place)
            steps.append(f'{name}：返回 {len(raw)} 条，筛选后 {len(rows)} 条匹配结果')
        except Exception as e:
            steps.append(f'{name}：{failure_reason(e)}')
            continue
        # Prefer public institutions when available, without a list of cities.
        def authority(row):
            host=(urllib.parse.urlparse(row['url']).hostname or '').lower()
            current='今天' in query or '实时' in query
            daily=any(w in row['title'] for w in ('今天','实时','24小时'))
            official=host.endswith(('.gov.cn','.gov','.edu','.edu.cn')) or host in ('www.weather.com.cn','www.nmc.cn')
            return (current and daily,official)
        rows=sorted(rows,key=authority,reverse=True)
        for row in rows[:4]:
            check()
            if row['url'] in seen:continue
            seen.add(row['url'])
            try:
                title,text=read_page(row['url'],required_place)
                check()
                if '天气' in query and not any(w in query for w in ['历史','往年','去年','昨天']) and any(w in title for w in ['历史天气','往年天气']):
                    steps.append('丢弃历史天气页面：'+title[:70])
                    continue
                content=page_excerpt(text,query,required_place=required_place)
                if '天气' in query and '气温' in query and not TEMPERATURE.search(content):
                    steps.append('正文未读到气温，尝试下一页：'+title[:70])
                    continue
                successes.append({'title':title or row['title'],'url':row['url'],
                                  'snippet':row['snippet'],'content':content,'read':True})
                steps.append('已读取：'+title[:90])
                if complete():LAST_SEARCH['ok']=True;return successes
            except generation.Cancelled:
                raise
            except Exception as e:
                steps.append((urllib.parse.urlparse(row['url']).hostname or '网页')+'：'+failure_reason(e))
                continue
        if complete():LAST_SEARCH['ok']=True;return successes
    subject=f'“{"、".join(places)}”的' if places else ''
    check()
    message = f'本次未能读取{subject}相关网页正文，没有沿用其他地点的数据。' if places else '本次未能读取与查询主题匹配的网页正文，尚未生成联网答案。'
    raise RuntimeError(message+'\n'+'\n'.join(steps[-8:])+'\n详细联网记录可在“状态详情”查看。')


def search(query):
    errors=[]
    for provider in (bing,duckduckgo):
        for attempt in range(2):
            try:
                rows=relevant(provider(query),query)
                if rows:return rows
                errors.append('结果与问题相关性不足')
                break
            except Exception as e:
                errors.append(type(e).__name__)
                if attempt==0:time.sleep(0.4)
    raise RuntimeError('本次联网搜索未取得可靠结果：连接失败、服务验证或结果不相关。已尝试重连和备用搜索；请稍后重试，或关闭联网使用本地回答。')
