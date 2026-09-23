// Synthetic overlap regression against the built UI. Routes isolate test data and
// settings; this script never modifies live rules/settings or sends Telegram.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1600,height:1050},timezoneId:'America/New_York'});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.BASE_URL||'http://frontend';
 let settings={snapshot_interval_sec:15,timezone_offset_minutes:420},saved,historyUrl;
 await page.route('**/api/settings',async route=>{
  if(route.request().method()==='PUT'){saved=route.request().postDataJSON();settings={...settings,...saved};}
  await route.fulfill({json:settings});
 });
 const origin=Date.UTC(2026,8,20,7),step=1800000;
 const bars=Array.from({length:123},(_,i)=>({start:origin+i*step,end:origin+(i+1)*step,open:100+i*.04,close:100.02+i*.04,high:100.6+i*.04,low:99.8+i*.04,volume:1000,confirmed:true}));
 const metrics={};for(const [lo,hi]of [['resistanceBottom1','resistanceTop1'],['resistanceBottom2','resistanceTop2'],['supportBottom1','supportTop1'],['supportBottom2','supportTop2'],['lockedZoneBottom','lockedZoneTop'],['recentLifecycleZoneBottom','recentLifecycleZoneTop']]){metrics[lo]=104;metrics[hi]=104.04;}
 const snapshot={exchange:'BYBIT',symbol:'UITESTUSDT',timeframe:'30',event_time:bars.at(-1).start,price:105,action:'WATCH',direction:'LONG',metrics,sl:103,t1:103.01};
 const events=Array.from({length:40},(_,i)=>({bar_start:bars[122-Math.floor(i/4)].start,event_time:bars[122-Math.floor(i/4)].start+i,event:i%2?'EXECUTION QUALITY >= 65':'LONG WATCH ENTRY'}));
 const history=bars.map(b=>({...snapshot,event_time:b.start,avg_setup:b.close-40,formation:70,execution:65,geometry:62,context:75,mae:10,exhaustion:12,level:80,approach:7,btc_shock:0}));
 await page.route('**/api/setups/UITESTUSDT/30**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname.endsWith('/history'))historyUrl=url;
  await route.fulfill({json:url.pathname.endsWith('/bars')?{items:bars}:url.pathname.endsWith('/events')?{items:events}:url.pathname.endsWith('/history')?{items:history}:snapshot});
 });
 const detail=base+'/setups/BYBIT/UITESTUSDT/30';
 await page.goto(detail);const chart=page.locator('svg.candle-chart');await chart.waitFor();
 await page.locator('.chart-event-badge').first().waitFor();
 async function separate(){
  const boxes=await page.locator('.price-annotation text,.chart-event-badge rect').evaluateAll(nodes=>nodes.map(n=>{const b=n.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height};}));
  assert.ok(boxes.length>=9);
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
   const a=boxes[i],b=boxes[j];assert.ok(a.x+a.w<=b.x||b.x+b.w<=a.x||a.y+a.h<=b.y||b.y+b.h<=a.y,`overlap ${i}/${j}`);
  }
 }
 await separate();
 assert.match(await chart.locator(':scope > text').first().textContent(),/UTC\+07:00$/);
 assert.match(await chart.locator('title').first().textContent(),/UTC\+07:00/);
 const metric=page.locator('.metric-chart svg').first();await metric.waitFor();
 assert.match(await metric.getAttribute('aria-label'),/UTC\+07:00/);
 await page.locator('.chart-event-badge rect').first().click();
 assert.equal(await page.locator('.chart-event-list span').count(),40);
 assert.ok(await page.locator('.chart-event-list').isVisible());
 const panel=page.locator('.panel').filter({has:chart});
 await panel.screenshot({path:'/artifacts/chart-labels-desktop.png'});
 // X zoom re-clusters nearby events; price levels are not moved.
 const bounds=await chart.boundingBox(),originalBars=Number(await chart.getAttribute('data-visible-bars'));
 await page.mouse.move(bounds.x+bounds.width*.9,bounds.y+bounds.height*.5);await page.mouse.wheel(0,-300);await page.waitForTimeout(200);
 assert.ok(Number(await chart.getAttribute('data-visible-bars'))<originalBars);await separate();
 await chart.focus();await page.keyboard.press('Home');
 await page.setViewportSize({width:390,height:844});await separate();
 assert.ok(await page.locator('.candle-chart-scroll').evaluate(n=>n.scrollWidth>n.clientWidth));
 await panel.screenshot({path:'/artifacts/chart-labels-mobile.png'});
 await page.setViewportSize({width:1600,height:1050});
 async function saveZone(offset){
  await page.goto(base+'/settings');const select=page.getByLabel('Временная зона графиков');await select.waitFor();
  await select.selectOption(String(offset));await page.getByRole('button',{name:'Сохранить временную зону',exact:true}).click();
  await page.getByText(/Временная зона сохранена/).waitFor();assert.deepEqual(saved,{timezone_offset_minutes:offset});
 }
 await saveZone(0);await page.goto(detail);await chart.waitFor();
 assert.match(await chart.locator(':scope > text').first().textContent(),/ UTC$/);
 assert.match(await page.locator('.metric-chart svg').first().getAttribute('aria-label'),/ UTC/);
 await page.getByRole('button',{name:'custom',exact:true}).click();
 await page.getByLabel('С (UTC)',{exact:true}).fill('2026-09-22T10:00');
 const requested=page.waitForResponse(r=>r.url().includes('/history?')&&r.url().includes('since='+Date.UTC(2026,8,22,10)));
 await page.getByLabel('По (UTC)',{exact:true}).fill('2026-09-22T11:00');await requested;
 assert.equal(Number(historyUrl.searchParams.get('since')),Date.UTC(2026,8,22,10));
 await saveZone(-330);await page.goto(detail);await chart.waitFor();
 assert.match(await chart.locator(':scope > text').first().textContent(),/UTC-05:30$/);
 await page.goto(base+'/settings');
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('label')).find(l=>l.textContent.startsWith('Временная зона графиков'))?.querySelector('select')?.value==='-330');
 assert.equal(await page.getByLabel('Временная зона графиков').inputValue(),'-330');
 await page.screenshot({path:'/artifacts/timezone-settings.png',fullPage:true});
 // Previously added alert presets: actual metadata, no saving or messages.
 await page.goto(base+'/alerts');const field=page.getByLabel('Метрика',{exact:true}).last();await field.waitFor();
 await field.selectOption('direction');const row=page.locator('.condition-row').last();await row.getByLabel('Оператор',{exact:true}).selectOption('IN');
 await row.getByLabel('Значения условия',{exact:true}).selectOption(['LONG','SHORT']);
 await page.getByText('JSON условия',{exact:true}).click();assert.match(await page.locator('pre.json').first().innerText(),/SHORT/);
 await field.selectOption('confirmed');await row.getByLabel('Оператор',{exact:true}).selectOption('==');await row.getByLabel('Значение условия',{exact:true}).selectOption('false');
 assert.match(await page.locator('pre.json').first().innerText(),/"value": false/);
 assert.deepEqual(errors,[]);
 fs.writeFileSync('/artifacts/chart-settings-browser.json',JSON.stringify({status:'PASS',fixture:'synthetic overlapping levels and 40 events',browser_timezone:'America/New_York',checks:['non-overlapping annotations desktop/mobile/zoom','full grouped event list','chart and metric timezone','settings save/reload','custom range UTC conversion','alert single/multiselect types'],errors},null,2));
 await browser.close();console.log('Chart/settings browser regression PASS');
})().catch(e=>{console.error(e);process.exit(1)});
