"""Task routing, explicit memories and bounded local conversation context."""
import json
import re
import websearch
import weather


RESPONSE_STYLE = (
    '生活关怀要有依据和分寸：本轮已查询且核实了用户所在地、日期和天气，并且用户正在谈出门、通勤或穿着时，可以在回答末尾自然补一句添衣、带伞或注意防晒的提醒。没有可靠天气资料就不要猜测气温，也不要为了一句关心强制联网。'
    '查询目的地或替别人查询的城市，不等于用户所在地；预报不等于实况。不根据地名推断用户正在那里。非生活话题通常不插入天气提醒，同一段连续对话不要重复叮嘱，不把普通天气描述成危险。'
    '人情味来自认真听懂这一次对话，而不是堆砌温柔词。先辨认用户是在提问、分享、吐槽还是寻求安慰，再决定回应方式；分享和吐槽不必立刻变成解决方案。'
    '接住用户具体说的事情，可以自然说“这一步确实容易卡住”“难怪你会觉得烦”，但没有依据时不要断言“我知道你很难过”。少用客服式确认和抽象心理分析。'
    '对话要接得上：只在相关时自然承接已提供的背景和上一句话，不复述整段记忆，不拿私人信息刻意拉近距离。用户已经作出的选择无需每轮重新询问。'
    '安慰之前不要急着纠正或替用户下结论；需要建议时先给眼下最有帮助的一步。也允许一句简短、贴切的回应就结束，不强行升华、教育或布置任务。'
    '措辞有轻重与变化，允许自然的短句和适度语气词；少用“首先、其次、最后”“建议你保持积极心态”“你并不孤单”等套话。不滥用亲昵称呼、不奉承，也不假装有真实人生经历或人类情感。'
    '表达方式：像一位耐心、坦诚、有判断力的交流伙伴。先直接回应最重要的问题，再自然展开。'
    '使用清楚的口语化书面中文，默认称“你”；温和但不讨好，不夸大，不机械重复用户问题。'
    '简单问题用一两段说清，复杂问题才分层；只有并列要点、操作步骤或对比确有帮助时才用列表、标题或表格。'
    '避免每次都以“当然可以”“这是一个很好的问题”“根据提供的资料”开头，也不要固定添加“总结”或“还有什么需要帮助”。'
    '不使用表情符号、空泛鼓励或过度加粗，除非用户要求；不必每轮重复用户姓名或自我介绍。'
    '解释概念时先给直观含义，再用贴近问题的小例子说明关键一步；不要一次堆满术语。'
    '用户明确要代码或示例时，简短定义后就给最小完整示例，再解释；避免冗长铺垫挤掉所求内容。类比必须对应准确。'
    '讨论难题时说明理由和取舍；不确定就说清缺少什么，区分事实、推测和建议，不凭语气掩盖证据不足。'
    '用户表达困扰时先具体回应困扰，再给可执行的小步骤；不诊断、不说教，不保证一定成功。'
    '交流节奏：先抓住用户这句话真正想聊的点，不把每个问题都写成报告。日常闲聊可以短一点，有自然的停顿、变化和适度幽默，不刻意卖萌。'
    '根据场景变换表达：好奇时一起探索，认真讨论时给出自己的判断，用户有情绪时先回应具体感受；不要每次套用同一套安慰或三段式结构。'
    '写作任务直接给作品；除非用户要比较，不主动提供“风格一、风格二、风格三”，也不先讲创作思路。广告和短文可以有具体画面、节奏和留白，少用“高效、精准、赋能、伙伴、开启新可能”等空泛词。'
    '幽默和比喻服务于理解，不能编造事实、真实经历或用户记忆；不声称能感知屏幕、用户情绪或未提供的信息。涉及技术与事实时仍清楚、准确，不为活泼而增加无关内容。'
    '示例：用户说“今天脑子转不动”，可以说“那今天先不开高速。挑一件最小的事，我们慢慢把它理顺。”而不是列出一套泛泛的效率指南。示例只借鉴语气，不要照抄。'
    '这些只是默认风格；本轮用户对长度、语言、格式和语气的明确要求优先，详细任务不能因追求简短而漏答。'
    '应用名称是 Aurora，不冒充 Claude；被问到底层模型时如实说明已提供的模型标识。'
    '\n语气示例，仅示范表达方式，不套用其中的情境：'
    '用户说“我总觉得学不会”，可回应：“先别急着用‘学不会’概括自己。我们把问题缩小：挑一道最近卡住的题，看看是哪一步开始不清楚。今天先弄懂这一步就够了。”'
    '不要写成“每个人都这样，熬过去就是质变”，也不要擅自判断是智力、心态或学习路径出了问题。'
    '用户问“递归是什么”，可先说：“递归就是函数调用自身，把问题一步步缩小，直到满足停止条件。”然后按用户要求给具体例子，不先写一大段类比。'
)


def clean(text):
    return re.sub(r'[\s，。？！?！!,.：:]', '', text)


def remember_command(question):
    if re.fullmatch(r'(?:请你?|麻烦)?(?:帮我)?记住(?:这件事|这一点)?[：:\s，,]*', question.strip()):
        raise ValueError('请在“记住：”后填写希望长期保存的内容。')
    match = re.fullmatch(r'(?:请你?|麻烦)?(?:帮我)?记住(?:这件事|这一点)?[：:\s，,]*(.+)', question.strip(), re.S)
    if not match:
        return None
    text = match.group(1).strip()
    if not text or text.startswith(('了吗', '吗', '我说过什么', '什么', '哪些', '不要')) or text.endswith(('吗', '吗？', '了吗', '？', '?')):
        return None
    if len(text) > 500:
        raise ValueError('长期记忆每条最多 500 字，请精简后再保存。')
    return text


def local_task(question):
    """An explicit writing/learning action can follow a polite or recipient prefix."""
    text=clean(question)
    prefixes=('可不可以','能不能','麻烦你','请你','帮忙','劳驾','麻烦','可以','请','你','您','能','先','再','也')
    # Consume prefixes deterministically; repeated optional regex alternatives
    # can backtrack exponentially on long non-task inputs.
    while text:
        prefix=next((p for p in prefixes if text.startswith(p)),None)
        if prefix is None:break
        text=text[len(prefix):]
    action=r'(?:写|创作|续写|润色|改写|翻译|解释|讲解|教我|推导|证明|计算|解题|编写|调试|纠错)'
    recipient=r'(?:(?:给|为|帮)[\w\u3400-\u9fff·-]{1,20}?)?'
    return bool(re.match('^'+recipient+action,text)
                or re.match(r'^把.{1,80}?(?:改写|润色|翻译|改成|写成|整理成)',text))



def route(question, online, attachment=False, previous=()):
    text = clean(question)
    # The checkbox grants permission, not an instruction to search.
    if re.search(r'不要(?:再)?(?:联网|上网|搜索)|不用(?:联网|上网|搜索)|别(?:去|再)?(?:联网|上网|搜索)|不(?:联网|上网|搜索)|不需要(?:联网|上网|搜索)|仅用本地|只用本地', text):
        return 'attachment' if attachment else 'local'
    if re.fullmatch(r'(?:以后|接下来|之后|今后|请)?你?(?:回答|说话|回复|语气|表达|风格)(?:能不能|尽量|不要那么|别那么|要|得|更|用|像)?(?:自然|简短|简洁|详细|温柔|Claude|claude|口语化)(?:一点|一些|点|些)?',text):
        return 'local'
    if re.fullmatch(r'(?:hi|hello|hey|thanks|thankyou)',text,re.I):
        return 'local'
    if re.fullmatch(r'(?:你好|您好|嗨|哈喽|早上好|中午好|下午好|晚上好|早安|晚安|谢谢|多谢|好的)(?:呀|啊|呢|哦|啦|哇)?',text):
        return 'local'
    if re.fullmatch(r'你(?:还)?(?:认识|记得|了解)我(?:吗|么)?',text):
        return 'local'
    if attachment and not re.search(r'联网|上网|搜索|查证|核实', text):
        return 'attachment'
    if re.fullmatch(r'(?:请)?(?:继续|接着说|展开讲讲|详细一点|具体一点|详细说说|简短一点|再简短些|换个说法|整理成表格|用表格列出|翻译成英文|翻译成中文|再解释一下|为什么|为什么呢|怎么理解|这是什么意思|举个例子|再举个例子|能举个例子吗|这一步怎么来的)', text):
        return 'transform'
    if not online:
        return 'local'
    if re.search(r'搜索|搜一下|搜一搜|搜搜|查一下|查查|查询|查找网页|查证|核实|(?:联网|上网)(?:查|搜|核实|找)|https?://', question):
        return 'search'
    if re.search(r'(?:以后|今后|之后|接下来|我让你|请|你要|你可以|你就|希望你|麻烦你).{0,20}(?:叫我|称呼我)|^(?:叫我|称呼我|我的称呼)',text):
        return 'local'
    if re.search(r'你.*(?:记得|知道).*(?:我是谁|我叫什么|我的名字)|你对我了解|你了解我|我(?:是谁|叫什么|叫|喜欢|的名字|的称呼|的专业|的目标|的偏好|的习惯|的兴趣|的学校|的研究方向|的学习进度|之前|以前|上次|学到)|还记得我|关于我的记忆', text):
        return 'local'
    if re.match(r'(?:请|帮我|请帮我)?(?:翻译|润色|改写|总结|概括)(?:一下)?(?:下面|以下|这段|这篇|这份|这张|这句)', text):
        return 'local'
    if re.search(r'最新|实时|热搜|股价|汇率|航班|赛程',text):
        return 'search'
    if local_task(question):
        return 'local'
    if re.search(r'当地|本地|附近|我这里|我这边|当前位置|所在城市|周边|这附近',text) and re.search(r'天气|气温|降水|新闻|路况|交通|餐厅|吃什么|好吃|景点|有什么|有哪些|怎么去|营业|活动|推荐',text):
        return 'search'
    if re.search(r'什么是|是什么|为什么|如何形成|原理|含义|怎么理解|怎么写',text) and not re.search(r'今天|明天|后天|当前|现在|最近|近期',text):
        return 'local'
    if weather.request_for(question,planning_history(previous)):
        return 'search'
    if re.search(r'天气|气温|降水|新闻|简报|热榜|营业时间|门票|路况|余票', text):
        return 'search'
    if re.search(r'.{2,}(?:有哪些景点|有什么景点|有什么好吃的|美食推荐|旅游攻略|住宿推荐|怎么去)',text):
        return 'search'
    if previous:
        sources,meta=source_context(previous)
        if sources and (followup_question(question,previous)!=question or
                        (meta.get('weather') and weather.request_for(question,planning_history(previous)))):
            return 'search'
    if re.search(r'代码|报错|函数|数组|指针|方程|求导|积分|逻辑题|这道题|什么是|原理是什么', text):
        return 'local'
    if re.fullmatch(r'[\d\s+*/%=().×÷−-]+', question):
        return 'local'
    if re.fullmatch(r'你.*(?:是谁|什么模型|哪个模型|什么名字|叫什么|谁开发|哪个公司|什么功能|做什么)|你好|您好|谢谢|好的|你还记得我吗', text):
        return 'local'
    return 'local'


def source_context(previous):
    row = next((r for r in previous if r['role'] == 'assistant'), None)
    if row is None:
        return [], {}


    try:
        sources = json.loads(row['sources'] or '[]')
        if not isinstance(sources, list):
            return [], {}
        meta = sources[0].get('_aurora', {}) if sources else {}
        if not meta and sources:
            user=next((r for r in previous if r['role']=='user'),None)
            raw=user['content'] if user is not None else ''
            # Old versions did not store query metadata. Restore only a literal
            # user place confirmed by the actual saved weather-page title.
            if len(raw)<=100 and not re.search(r'模型|原理|算法|研究',raw) and '天气' in sources[0].get('title',''):
                candidate=weather.request_for(raw,[{'role':'assistant','content':weather.CLARIFY}])
                place=candidate.get('city') if candidate else None
                if place and place in raw and websearch.place_matches(sources[0]['title'],place):
                    day=candidate['day']
                    meta={'query':place+' '+['今天','明天','后天'][day]+'天气','places':[place],'weather':True,'day':day,'legacy':True}
                    sources=[dict(source,_aurora=meta) for source in sources]
        return (sources, meta) if isinstance(meta, dict) else ([], {})
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return [], {}


def planning_history(previous):
    _,meta=source_context(previous)
    if not meta.get('weather') or not isinstance(meta.get('query'),str):
        return previous
    result=[dict(row) for row in previous]
    user=next((row for row in result if row['role']=='user'),None)
    if user is not None:
        user['content']=meta['query'][:600]
    return result


def followup_question(question, previous):
    sources, meta = source_context(previous)
    places = meta.get('places', [])
    if not sources or not isinstance(places, list) or not places:
        return question
    if any(not isinstance(p, str) or not 2 <= len(p) <= 60 or p not in meta.get('query', '') for p in places):
        return question
    text = clean(question)
    if re.fullmatch(r'(?:那边|那里|当地|这里|附近)?(?:有什么景点|有哪些景点|有什么好吃的|美食推荐|美食呢|旅游景点呢|交通怎么样|怎么去|需要注意什么|住宿推荐|玩几天合适)',text):
        return ' '.join(places)+' '+text
    if not meta.get('weather'):
        return question
    if re.fullmatch(r'(?:那)?(?:今天|明天|后天)(?:呢|怎么样|天气呢|天气怎么样)?', text):
        day = next(day for day in ('今天', '明天', '后天') if day in text)
        return ' '.join(places) + ' ' + day + '天气'
    if re.fullmatch(r'(?:那)?(?:会下雨吗|下不下雨|需要带伞吗|要带伞吗|降水概率呢)', text):
        day = meta.get('day', 0)
        day = day if day in (0, 1, 2) else 0
        return ' '.join(places) + ' ' + ['今天', '明天', '后天'][day] + '天气 降水概率 要带伞吗'
    return question


def web_preferences(recalled):
    result = []
    for row in recalled:
        if row['kind'] != 'note':
            continue
        clauses = re.split(r'[，。；;\n]', row['content'])
        kept = [c for c in clauses if re.search(r'称呼|叫我|用中文|用英文|简短|详细|分步骤|一步一步|回答风格|不要表格', c)
                and not re.search(r'天气|气温|降水|湿度|℃|°|\d{4}[-/年]', c)]
        if kept:
            result.append(dict(row, content='；'.join(kept)))
    return result


def history(previous, budget, question):
    selected = []
    remaining = max(0, budget)
    for row in previous:
        if remaining < 80:
            break
        limit = min(3500 if not selected else 1400, max(0, remaining - (350 if not selected else 0)))
        if limit < 40:
            break
        text = row['content']
        if len(text) > limit:
            tail = not selected and clean(question) in ('继续', '接着说')
            text = ('【较早部分省略】' + text[-max(1, limit - 12):]) if tail else text[:max(0, limit - 12)] + '【较长内容已截取】'
        selected.append({'role': row['role'], 'content': text})
        remaining -= len(text)
    selected.reverse()
    while selected and selected[0]['role'] != 'user':
        selected.pop(0)
    return selected
