/* Standalone browser smoke test; no live LMS or ScormStack account is touched. */
const {chromium, firefox} = require(process.env.SCORMSTACK_PLAYWRIGHT || '@playwright/test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, '../scormstack_xblock/static');
const state = {a: {}, b: {}}, saves = [], failures = [];
let failOnce = true;
const html = `<!doctype html><html><body><script src="/player.js"></script>
${['a', 'b'].map(id => `<div id="${id}" class="ss-player"><button class="ss-save">Save</button><button class="ss-fullscreen">Fullscreen</button><div class="ss-frame"></div><p class="ss-status"></p></div>`).join('')}
<script>['a','b'].forEach(id => { const root=document.getElementById(id); ScormStackPlayer({handlerUrl:(_element, handler)=>'/api/'+id+'/'+handler},root,{revision:id,runtimeUrl:location.origin+'/runtime.js',height:400}); });</script></body></html>`;
const content = id => `<!doctype html><html><body><input id="answer"><button id="save">Answer</button><script>
const api=parent.API; api.LMSInitialize(''); document.getElementById('answer').value=api.LMSGetValue('cmi.suspend_data');
document.getElementById('save').onclick=()=>{api.LMSSetValue('cmi.suspend_data',document.getElementById('answer').value);api.LMSSetValue('cmi.core.lesson_status','completed');api.LMSSetValue('cmi.core.score.raw','80');api.LMSCommit('');};
</script></body></html>`;
const server = http.createServer(async (req, res) => {
  const base = `http://${req.headers.host}`;
  if (req.url === '/') { res.setHeader('Content-Type','text/html'); return res.end(html); }
  if (req.url === '/runtime.js' || req.url === '/player.js') { res.setHeader('Content-Type','application/javascript'); return res.end(fs.readFileSync(path.join(source,req.url.slice(1)))); }
  if (req.url.startsWith('/content/')) { res.setHeader('Content-Type','text/html'); return res.end(content(req.url.split('/')[2])); }
  if (req.url.startsWith('/api/')) {
    let body=''; for await (const chunk of req) body+=chunk;
    const data=JSON.parse(body), [, ,id, action]=req.url.split('/');
    res.setHeader('Content-Type','application/json');
    if (action==='launch') return res.end(JSON.stringify({preview:false, data:state[id], version:'1.2', revision:id, session:'session-'+id, launch:base+'/content/'+id+'/index.html'}));
    if (failOnce) { failOnce=false; res.statusCode=503; return res.end(JSON.stringify({error:'Temporary failure'})); }
    saves.push({id,...data}); state[id]=data.data;
    return res.end(JSON.stringify({sequence:data.sequence}));
  }
  res.statusCode=404; res.end();
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  for (const browserType of [chromium, firefox]) {
    state.a={}; state.b={}; saves.length=0; failOnce=true;
    const browser=await browserType.launch({headless:true});
    try {
      const page=await browser.newPage();
      page.on('pageerror', e=>failures.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      const a=page.frameLocator('#a .ss-frame > iframe').frameLocator('iframe');
      const b=page.frameLocator('#b .ss-frame > iframe').frameLocator('iframe');
      await a.locator('#answer').fill('first-answer'); await a.locator('#save').click();
      await page.locator('#a .ss-status').filter({hasText:'Progress saved'}).waitFor({timeout:15000});
      assert.equal(state.a['cmi.suspend_data'],'first-answer');
      assert.deepEqual(state.b,{});
      await b.locator('#answer').fill('independent-answer'); await b.locator('#save').click();
      await page.locator('#b .ss-status').filter({hasText:'Progress saved'}).waitFor();
      assert.equal(state.b['cmi.suspend_data'],'independent-answer');
      await page.reload();
      assert.equal(await a.locator('#answer').inputValue(),'first-answer');
      assert.equal(await b.locator('#answer').inputValue(),'independent-answer');
      assert.equal(await page.evaluate(()=>typeof window.API),'undefined');
      assert.deepEqual(failures,[]);
      console.log(browserType.name()+': independent players, failed-save retry, grade data and resume passed');
    } finally { await browser.close(); }
  }
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
