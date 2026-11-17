const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const nodes = new Map(), listeners = {}, calls = [];
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    value: id === 'mode' ? 'text' : '', files: [], disabled: false, hidden: true,
    textContent: '', style: {}, handlers: {},
    classList: { add() {}, remove() {} }, append() {}, replaceChildren() {},
    focus() {}, click() {}, querySelector() { return null; },
    addEventListener(name, fn) { this.handlers[name] = fn; },
  });
  return nodes.get(id);
}
const context = vm.createContext({
  document: {
    getElementById: node, querySelector: () => node('main'),
    createElement: tag => tag === 'canvas' ? {
      getContext: () => ({ fillRect() {}, drawImage() {} }),
      toDataURL: () => 'data:image/jpeg;base64,aGVsbG8=',
    } : { style: {}, append() {} },
    addEventListener(name, fn) { listeners[name] = fn; },
  },
  window: { addEventListener() {} }, setInterval() {},
  URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
  Image: class {
    width = 800; height = 600;
    set src(value) { queueMicrotask(() => this.onload()); }
  },
  FileReader: class {
    readAsDataURL() { this.result = 'data:application/pdf;base64,cGRm'; queueMicrotask(() => this.onload()); }
  },
  fetch: async (url, options) => {
    calls.push({ url, data: JSON.parse(options.body) });
    return { ok: true, json: async () => url === '/api/document' ?
      { name: 'paper.pdf', text: '文档正文', images: [], pages: 1, warnings: [] } :
      { text: { ready: true }, gemma: { ready: false }, vision: { ready: false } } };
  },
});
let source = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8').split('<script>')[1].split('</script>')[0];
source = source.slice(0, source.lastIndexOf('(async()=>{try{const info='));
vm.runInContext(source, context);
async function drop(files, items = []) {
  let prevented = false;
  await listeners.drop({ dataTransfer: { types: ['Files'], files, items }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true, 'A file drop must not navigate away from the app');
}
(async () => {
  const pdf = { name: 'paper.pdf', type: 'application/pdf', size: 40 };
  await drop([pdf]);
  assert.equal(vm.runInContext('attachedDoc.name', context), 'paper.pdf');
  assert.equal(calls[0].url, '/api/document');
  assert.equal(calls.some(c => c.url === '/api/chat'), false, 'Attaching must not auto-send a question');
  await drop([pdf, pdf]);
  assert.equal(vm.runInContext('attachedDoc.name', context), 'paper.pdf');
  await drop([{ name: 'unsafe.exe', type: '', size: 1 }]);
  assert.equal(vm.runInContext('attachedDoc.name', context), 'paper.pdf');
  await drop([], [{ webkitGetAsEntry: () => ({ isDirectory: true }) }]);
  assert.equal(vm.runInContext('attachedDoc.name', context), 'paper.pdf');
  vm.runInContext('busy=true', context);
  const before = calls.length;
  await drop([pdf]);
  assert.equal(calls.length, before, 'No new upload while a reply is being generated');
  vm.runInContext('busy=false', context);
  const image = { name: 'screen.png', type: '', size: 100 };
  await drop([image]);
  assert.equal(vm.runInContext('attachedDoc', context), null);
  assert.equal(vm.runInContext('attached', context), 'data:image/jpeg;base64,aGVsbG8=');
  assert.equal(node('mode').value, 'gemma');
  await drop([{ ...pdf, size: 20000001 }]);
  assert.equal(vm.runInContext('attached', context), 'data:image/jpeg;base64,aGVsbG8=');
  let prevented = false;
  await node('input').handlers.paste({ clipboardData: { items: [
    { kind: 'file', type: 'image/png', getAsFile: () => image },
  ] }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(vm.runInContext('attachmentBusy', context), false);
  assert.equal(node('send').disabled, false);
  // Text (including mixed HTML/image clipboard payloads) must use native editing.
  for (const items of [[], [{ kind: 'file', type: 'image/png', getAsFile: () => image }]]) {
    let blocked = false;
    const oldImage = vm.runInContext('attached', context);
    await node('input').handlers.paste({ clipboardData: { items, getData: type => type === 'text/plain' ? '第一行\n第二行' : '' }, preventDefault() { blocked = true; } });
    assert.equal(blocked, false, 'Text paste must retain the browser default action');
    assert.equal(vm.runInContext('attached', context), oldImage, 'Mixed clipboard text does not replace attachments');
  }
  vm.runInContext('sessionLoading=true',context);
  const beforeNavigationUpload=calls.length;
  await drop([pdf]);
  assert.equal(calls.length,beforeNavigationUpload,'Navigation must block drag and paste uploads');
  assert.equal(vm.runInContext('attached',context),'data:image/jpeg;base64,aGVsbG8=');
  vm.runInContext('sessionLoading=false',context);
  const originalFetch=context.fetch;
  context.fetch=async()=>{throw Error('文档解析失败')};
  await drop([pdf]);
  assert.match(node('hint').textContent,/失败/);
  assert.doesNotMatch(node('hint').textContent,/已就绪/);
  assert.equal(vm.runInContext('attached',context),'data:image/jpeg;base64,aGVsbG8=','Failed replacements preserve the previous attachment');
  assert.equal(node('send').disabled,false);
  context.fetch=originalFetch;
  console.log('Attachment checks passed: PDF/image drops, paste, no auto-send, directory/multi-file/oversize rejection, busy state, attachment preservation.');
})().catch(e => { console.error(e); process.exitCode = 1; });
