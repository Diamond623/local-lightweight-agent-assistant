const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.style={};this.className='';this._text='';this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.handlers={};this.classList={add:c=>{if(!this.className.split(' ').includes(c))this.className+=' '+c;},remove:c=>{this.className=this.className.split(' ').filter(x=>x!==c).join(' ');}};}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 set innerHTML(v){throw Error('Untrusted HTML parsing must not be used by the chat renderer');}
 append(...els){for(const e of els){e.parent=this;this.children.push(e);}}replaceChildren(...els){this._text='';this.children=[];this.append(...els);}
 cloneNode(deep){const n=new Element(this.tagName);n.className=this.className;n._text=this._text;if(deep)n.append(...this.children.map(c=>c.cloneNode(true)));return n;}removeAttribute(){}
 addEventListener(k,f){this.handlers[k]=f;}showModal(){this.open=true;}close(){this.open=false;}focus(){}select(){}remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
 querySelector(selector){const cls=selector.startsWith('.')?selector.slice(1):null;return this.children.find(c=>cls&&c.className.split(' ').includes(cls))||null;}
}
const nodes=new Map(),calls=[],copied=[];function node(id){if(!nodes.has(id))nodes.set(id,new Element(id==='input'?'textarea':'div'));return nodes.get(id);}
node('mode').value='text';let behavior='success',pendingRead=null,lastReader=null;
function ndjson(events){return new TextEncoder().encode(events.map(e=>JSON.stringify(e)+'\n').join(''));}
const context=vm.createContext({document:{getElementById:node,querySelector:()=>node('main'),createElement:t=>new Element(t),createElementNS:(_,t)=>{const n=new Element(t);n.setAttribute=()=>{};return n},createTextNode:t=>{const n=new Element('#text');n.textContent=t;return n;},addEventListener(){},body:new Element('body')},window:{addEventListener(){}},navigator:{clipboard:{writeText:async t=>copied.push(t)}},setInterval(){},TextDecoder,TextEncoder,console,URL:{},fetch:async(url,options)=>{
 const data=JSON.parse(options.body);calls.push({url,data});
 if(url==='/api/sessions')return{ok:true,json:async()=>[]};
 if(url==='/api/cancel'){pendingRead?.({value:ndjson([{type:'error',message:'已停止',cancelled:true}]),done:false});return{ok:true,json:async()=>({cancelled:true})};}
 if(url!=='/api/chat')return{ok:true,json:async()=>({})};
 let chunks;
 if(behavior==='failure'){node('input').value='新写的草稿';chunks=[ndjson([{type:'delta',text:'半句'},{type:'error',message:'连接失败'}])];}
 else if(behavior==='pending')chunks=[ndjson([{type:'status',message:'正在生成'},{type:'delta',text:'半句'}])];
 else{
  const answer='**完成**\n\n```js\nconst a = "<script>bad()</script>";\n```\n<img src=x onerror=bad()>\n你好世界';
  const bytes=ndjson([{type:'status',message:'生成中'},{type:'delta',text:'你好世界'},{type:'result',answer,sources:[{title:'测试来源',url:'https://example.org'}],recalled:[],search_note:'本地测试'}]);
  chunks=[];for(let i=0;i<bytes.length;i+=7)chunks.push(bytes.slice(i,i+7));
 }
 let index=0;const reader={async read(){if(index<chunks.length)return{value:chunks[index++],done:false};if(behavior==='pending'&&index++===chunks.length)return new Promise(resolve=>pendingRead=resolve);return{done:true};},async cancel(){this.cancelled=true;},releaseLock(){this.released=true;}};lastReader=reader;
 return{ok:true,body:{getReader:()=>reader}};
}});
let source=fs.readFileSync(path.join(__dirname,'index.html'),'utf8').split('<script>')[1].split('</script>')[0];source=source.slice(0,source.lastIndexOf('(async()=>{try{const info='));vm.runInContext(source,context);vm.runInContext('session=7',context);
function all(root){return [root,...root.children.flatMap(all)];}
if(require.main===module)(async()=>{
 node('sidebarToggle').onclick();assert.equal(node('sidebarToggle').ariaExpanded,'false');assert.match(context.document.body.className,/sidebar-collapsed/);
 node('sidebarToggle').onclick();assert.equal(node('sidebarToggle').ariaExpanded,'true');assert.doesNotMatch(context.document.body.className,/sidebar-collapsed/);
 node('sidebarshade').onclick();assert.equal(node('sidebarToggle').ariaExpanded,'false');
 node('sidebarToggle').onclick();
 const greet=iso=>JSON.parse(vm.runInContext('JSON.stringify(welcomeCopy(new Date('+JSON.stringify(iso)+'),"艺焜"))',context));
 assert.match(greet('2026-02-04T01:00:00Z').heading,/立春安好，艺焜/);
 assert.match(greet('2026-02-04T01:00:00Z').dateText,/04:02交节/);
 assert.match(greet('2026-04-05T01:00:00Z').heading,/清明安好/);
 assert.doesNotMatch(greet('2026-04-05T01:00:00Z').heading,/快乐/);
 assert.match(greet('2026-10-25T01:00:00Z').subtitle,/霜降/);
 assert.match(greet('2026-01-01T01:00:00Z').heading,/元旦快乐/);
 const november=greet('2026-11-11T08:00:00Z');
 assert.equal(november.heading,'下午好，艺焜');
 assert.match(november.subtitle,/一个人的时光|把今天留给自己/);
 assert.match(november.dateText,/11·11 小彩蛋/);
 assert.doesNotMatch(november.heading,/小彩蛋|安好/);
 assert.equal(vm.runInContext('solarContext(new Date("2026-02-03T20:01:00Z")).current.name',context),'大寒');
 assert.equal(vm.runInContext('solarContext(new Date("2026-02-03T20:02:00Z")).current.name',context),'立春');
 assert.equal(vm.runInContext('solarContext(new Date("2040-02-04T01:00:00Z")).today',context),null);
 assert.match(greet('2040-02-04T01:00:00Z').heading,/早安/);
 assert.match(greet('2026-10-25T01:00:00Z').heading,/早安，艺焜/);
 assert.match(greet('2026-10-25T04:00:00Z').heading,/中午好/);
 assert.match(greet('2026-10-25T07:00:00Z').heading,/下午好/);
 assert.match(greet('2026-10-25T12:00:00Z').heading,/晚上好/);
 assert.match(greet('2026-10-25T16:30:00Z').heading,/夜深了/);
 assert.match(greet('2026-10-25T16:30:00Z').dateText,/10月26日/);
 assert.match(greet('2025-01-29T01:00:00Z').heading,/春节快乐/);
 assert.match(greet('2025-01-28T01:00:00Z').heading,/除夕快乐/);
 assert.match(greet('2026-10-01T01:00:00Z').heading,/国庆节/);
 assert.equal(vm.runInContext('welcomeCopy(new Date("2026-10-25T01:00:00Z"), "").heading',context),'早安');
 vm.runInContext("sessionList=[{id:7,title:'归结法学习'},{id:8,title:'C语言代码'}];renderSessions()",context);
 assert.equal(node('sessions').children.length,2);assert.equal(node('conversationTitle').textContent,'归结法学习');
 node('sessionfilter').value='归结';node('sessionfilter').oninput();
 assert.equal(node('sessions').children.length,1);assert.equal(node('sessions').children[0].children[0].textContent,'归结法学习');
 node('sessionfilter').value='';node('sessionfilter').oninput();
 vm.runInContext("attached='data:image/jpeg;base64,aGVsbG8='",context);node('attachment').textContent='test.png · 图片已就绪';node('input').value='这张图是什么';
 await node('send').onclick();
 assert.equal(calls.find(c=>c.url==='/api/chat').data.stream,true);assert.equal(node('mode').value,'gemma');
 assert.equal(vm.runInContext('attached',context),'','Successful image sends clear the pending attachment');
 assert.equal(node('attachment').textContent,'');assert.equal(node('remove').hidden,true);assert.equal(node('file').value,'');
 node('input').value='下一个普通问题';await node('send').onclick();
 assert.equal(calls.filter(c=>c.url==='/api/chat').at(-1).data.image,'','Next turn must not resend the old image');
 const assistant=node('chat').children.find(n=>n.className==='message assistant');
 const mathTarget=new Element('div');context.mathTarget=mathTarget;
 vm.runInContext('renderText(mathTarget,"\\\\[\\nA = \\\\frac{QK^T}{\\\\sqrt{d_k}}\\n\\\\]")',context);
 assert.ok(all(mathTarget).some(n=>n.tagName==='MFRAC'),'Fractions render as MathML');
 assert.ok(all(mathTarget).some(n=>n.tagName==='MSQRT'),'Square roots render as MathML');
 assert.ok(all(assistant).some(n=>n.tagName==='STRONG'));assert.ok(all(assistant).some(n=>n.tagName==='CODE'));
 assert.equal(all(assistant).some(n=>n.tagName==='SCRIPT'),false);assert.equal(all(assistant).some(n=>n.tagName==='IMG'),false);
 assert.ok(assistant.textContent.includes('<img src=x onerror=bad()>'));assert.ok(assistant.textContent.includes('你好世界'));
 const copy=all(assistant).find(n=>n.tagName==='BUTTON');await copy.onclick();assert.ok(copied[0].includes('const a'));
 assert.equal(lastReader.released,true);
 const chat=node('chat');chat.scrollHeight=1500;chat.clientHeight=500;chat.scrollTop=200;
 context.scrollTestBubble=assistant;
 vm.runInContext("updateMessage(scrollTestBubble,'新增的一段内容',false)",context);
 assert.equal(chat.scrollTop,200,'Streaming must not pull readers away from older content');
 chat.scrollTop=980;
 vm.runInContext("updateMessage(scrollTestBubble,'末尾继续显示',false)",context);
 assert.equal(chat.scrollTop,1500,'Readers near the bottom should keep following the reply');
 vm.runInContext("attached='data:image/jpeg;base64,aGVsbG8='",context);node('input').value='';const beforeImageOnly=calls.filter(c=>c.url==='/api/chat').length;
 await node('send').onclick();
 assert.equal(calls.filter(c=>c.url==='/api/chat').length,beforeImageOnly+1,'An image-only submission is allowed without inventing a question');
 assert.equal(calls.filter(c=>c.url==='/api/chat').at(-1).data.text,'');
 vm.runInContext("attached='data:image/jpeg;base64,aGVsbG8='",context);behavior='failure';node('input').value='上一个问题';await node('send').onclick();
 assert.equal(node('input').value,'新写的草稿','Failed request never overwrites a new draft');
 assert.equal(vm.runInContext('attached',context),'data:image/jpeg;base64,aGVsbG8=','Failed image sends retain the attachment for retry');
 assert.equal(vm.runInContext('busy',context),false);assert.equal(node('send').disabled,false);
 behavior='pending';node('input').value='长回答';const request=node('send').onclick();
 for(let i=0;i<30&&!pendingRead;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.ok(pendingRead);assert.equal(node('stop').hidden,false);assert.equal(node('mode').disabled,true);
 node('input').value='停止后继续的草稿';await node('stop').onclick();await request;
 assert.equal(node('input').value,'停止后继续的草稿');assert.equal(vm.runInContext('busy',context),false);assert.equal(node('stop').hidden,true);
 assert.equal(calls.filter(c=>c.url==='/api/cancel').length,1);
 behavior='success';node('agent').checked=true;node('input').value='请计算 2+3';await node('send').onclick();
 assert.equal(calls.filter(c=>c.url==='/api/chat').at(-1).data.agent,true,'Agent mode reaches the chat API');
 assert.equal(node('agent').disabled,false);
 node('mode').value='vision';await node('mode').onchange();
 assert.equal(calls.filter(c=>c.url==='/api/start').at(-1).data.mode,'vision','Changing models automatically requests startup');
 assert.equal(node('mode').disabled,false,'Picker recovers after startup request');
 const originalClipboard=context.navigator.clipboard.writeText;let fallbackCopies=0,restored=false;
 context.navigator.clipboard.writeText=async()=>{throw Error('WebKit denied clipboard API')};
 context.document.execCommand=command=>{assert.equal(command,'copy');fallbackCopies++;return true};
 context.document.activeElement={selectionStart:2,selectionEnd:5,focus(){restored=true},setSelectionRange(start,end){assert.deepEqual([start,end],[2,5])}};
 await vm.runInContext('copyText("clipboard fallback")',context);
 assert.equal(fallbackCopies,1);assert.equal(restored,true);
 context.navigator.clipboard.writeText=originalClipboard;
 const fixedDates=JSON.parse(vm.runInContext('JSON.stringify(FESTIVAL_FIXED)',context));
 for(const [date,entry] of Object.entries(fixedDates)){const [m,d]=date.split('-');const value=greet(`2026-${m.padStart(2,'0')}-${d.padStart(2,'0')}T01:00:00Z`);assert.ok((entry[2]==='彩蛋'?value.dateText:value.heading).includes(entry[0]),entry[0]);assert.ok(entry[1].some(line=>value.subtitle.includes(line)),entry[0]+' copy');}
 assert.match(greet('2026-05-10T01:00:00Z').heading,/母亲节/);assert.doesNotMatch(greet('2026-05-03T01:00:00Z').heading,/母亲节/);
 assert.match(greet('2026-06-21T01:00:00Z').heading,/父亲节/);assert.match(greet('2026-11-26T01:00:00Z').heading,/感恩节/);
 assert.match(greet('2026-02-17T01:00:00Z').heading,/春节/);assert.match(greet('2026-06-19T01:00:00Z').heading,/端午安康/);
 assert.doesNotMatch(greet('2026-12-13T01:00:00Z').heading,/快乐/);assert.doesNotMatch(greet('2026-09-18T01:00:00Z').heading,/快乐/);
 assert.doesNotMatch(greet('2023-03-23T01:00:00Z').heading,/龙抬头/,'Leap lunar months must not duplicate festivals');
 assert.match(greet('2026-12-31T16:00:00Z').heading,/元旦/,'Calendar uses Beijing midnight');
 assert.equal(greet('2026-10-23T01:00:00Z').subtitle,greet('2026-10-23T02:00:00Z').subtitle,'Copy stays stable within a date');
 assert.equal(vm.runInContext('Object.keys(SOLAR_EXTRA).length',context),24);
 node('agent').checked=true;node('desktop').checked=true;node('online').checked=true;vm.runInContext('updateToolSummary()',context);
 assert.equal(node('toolsummary').textContent,'Agent · 桌面 · 联网','Compact menu must expose enabled permissions');
 node('agent').checked=false;node('desktop').checked=false;node('online').checked=false;vm.runInContext('updateToolSummary()',context);assert.equal(node('toolsummary').textContent,'工具');
 console.log('Chat UI checks passed: chunked UTF-8, Markdown/code safety, copy, image cleanup after success and retention after failure, draft preservation, stop and control recovery.');
})().catch(e=>{console.error(e);process.exitCode=1;});

module.exports={context,node,nodes,Element,all};
