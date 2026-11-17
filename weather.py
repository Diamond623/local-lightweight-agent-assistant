"""Recognize a weather topic; locations are resolved by the generic search planner."""
import re
import json
import datetime
import urllib.parse
import urllib.request

CLARIFY = '你想查询哪个城市的天气？请告诉我城市名，例如“成都”或“新加坡”。'


def request_for(question,previous):
    clean=re.sub(r'[\s，。？！?！!,.]','',question)
    # Addressing Aurora is not part of the weather question. Natural statements
    # like “今天好冷” still ask about present conditions in this chat context.
    clean=re.sub(r'^(?:Aurora|奥罗拉)[：:，,]*','',clean,flags=re.I)
    if re.fullmatch(r'(?:我这|我这里|我这边|这里|当地|本地|现在|目前|此刻|这儿)*(?:现在|目前|此刻)?(?:气温|温度)?(?:是|有)?多少(?:度|摄氏度)(?:了|啊|呢|吗)?',clean) or re.fullmatch(r'(?:我这|我这里|我这边|这里|当地|本地)?(?:现在|目前|此刻)(?:气温|温度)(?:是|有)?多少(?:度|摄氏度)?(?:了|啊|呢|吗)?',clean):
        return {'city':None,'day':0}
    followup=bool(previous and previous[0]['role']=='assistant' and previous[0]['content']==CLARIFY)
    prior=next((r['content'] for r in previous if r['role']=='user'),'')
    weather_context='天气' in prior or bool(re.search(r'(?:今天|明天|后天|现在).{0,6}(?:冷|热|下雨|带伞|添衣)',prior))
    stated_city=re.fullmatch(r'我(?:现在)?在([\u3400-\u9fffA-Za-z·-]{2,24})',clean)
    if weather_context and stated_city:
        return {'city':stated_city[1],'day':2 if '后天' in prior else 1 if '明天' in prior else 0}
    if re.fullmatch(r'(?:今天|明天|后天|现在|这里|本地|当地|我这里|我这边|呢|会|要|需要|出门|不|好|真|太|挺|有点|很|吗|冷|热|下雨|带伞|添衣|加衣服|多穿点|穿外套)+',clean) and re.search(r'冷|热|下雨|带伞|添衣|加衣服|穿外套',clean):
        clean=re.sub(r'呢|会|要|需要|出门|不|好|真|太|挺|有点|很|吗|冷|热|下雨|带伞|添衣|加衣服|多穿点|穿外套','',clean)+'天气'
    # Plain short Chinese text is not evidence of a location. Implicit weather
    # followups must look like an ellipsis; a bare city is accepted only after
    # our explicit city clarification.
    bare_place=bool(re.fullmatch(r'(?:那|那么|换成|换个)?[\u3400-\u9fffA-Za-z·-]{2,24}(?:呢|啊|呀)',clean)
                    or re.fullmatch(r'(?:那|那么|换成|换个)[\u3400-\u9fffA-Za-z·-]{2,24}',clean))
    if re.search(r'写|创作|润色|改写|翻译|解释|讲解|推导|证明|计算|解题|编写|调试|纠错|介绍|总结|推广|广告|文案',clean):
        return None
    if weather_context and bare_place and not any(w in clean for w in ['为什么','怎么','什么','哪些','旅游','景点','代码','原理','你好','谢谢','不用','算了','多谢','好的','可以']):
        followup=True
    if not followup and ('天气' not in clean or any(w in clean for w in ['模型','原理','算法','代码','预测方法'])):
        return None
    if followup and any(w in clean for w in ['不用','不查','算了','谢谢']):return None
    day_text=prior if followup and not any(w in clean for w in ['今天','明天','后天','现在']) else clean
    day=2 if '后天' in day_text else 1 if '明天' in day_text else 0
    if any(w in clean for w in ['昨天','上周','去年','下周','未来一周','一周']):
        return None
    city=re.sub(r'请帮我|帮我|帮忙|请问|查询一下|查询|查一下|看看|查查|告诉我|一下|现在|目前|今天|明天|后天|天气预报|天气情况|天气|怎么样|如何|好不好|好吗|温度|降水|预报|情况|的','',clean).strip()
    city=re.sub(r'^(?:那么|那|换成|换个|再查|查)+','',city)
    city=re.sub(r'(?:呢|啊|呀|吧)+$','',city)
    if city in ('这里','当地','本地','我这里','我这边','这边','附近','我的位置'):
        city=''
    if not city:
        return {'city':None,'day':day}
    if not re.fullmatch(r'[\u3400-\u9fffA-Za-z ·.-]{2,40}',city):return None
    return {'city':city,'day':day}


def current_temperature(city, fetch=None, clock=None):
    """Fresh city-grid estimate, never a thermometer reading or cached chat fact."""
    if not isinstance(city,str) or not re.fullmatch(r'[\u3400-\u9fffA-Za-z ·.-]{2,60}',city):
        raise ValueError('本轮定位城市无效')
    fetch = fetch or (lambda url: json.load(urllib.request.urlopen(
        urllib.request.Request(url,headers={'User-Agent':'AuroraLocal/1.0'}),timeout=9)))
    geo='https://geocoding-api.open-meteo.com/v1/search?'+urllib.parse.urlencode({'name':city,'count':8,'language':'zh','format':'json'})
    matches=fetch(geo).get('results',[])
    match=next((m for m in matches if m.get('name','').casefold()==city.casefold()),None)
    if not match:raise RuntimeError('没有可靠匹配本轮定位城市，无法取得当前气温。')
    url='https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode({'latitude':match['latitude'],'longitude':match['longitude'],'current':'temperature_2m','timezone':'auto'})
    data=fetch(url);current=data.get('current') or {};timestamp=current.get('time');value=current.get('temperature_2m')
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isinstance(timestamp,str):
        raise RuntimeError('天气服务未返回有效的当前温度与时间。')
    zone=datetime.timezone(datetime.timedelta(seconds=int(data.get('utc_offset_seconds',0))))
    observed=datetime.datetime.fromisoformat(timestamp).replace(tzinfo=zone)
    age=(clock() if clock else datetime.datetime.now(datetime.timezone.utc))-observed.astimezone(datetime.timezone.utc)
    if not datetime.timedelta(minutes=-10)<=age<=datetime.timedelta(minutes=90):
        raise RuntimeError('天气数据时间过旧，不能作为当前温度。')
    return {'city':city,'temperature':value,'time':timestamp,'url':url}

