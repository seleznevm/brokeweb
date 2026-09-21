const {chromium}=require('playwright');
const fs=require('fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1600,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.BASE_URL||'http://frontend';
 await page.goto(base+'/alerts');await page.getByRole('heading',{name:'Alerts',exact:true}).waitFor();
 const field=page.getByLabel('Метрика',{exact:true}).last();
 for(const name of ['avg_setup','formation','execution','geometry','context','level','mae','btc_shock','price','timeframe']){await field.selectOption(name);assert.equal(await field.inputValue(),name);}
 await field.selectOption('formation');
 await page.getByText('JSON условия',{exact:true}).click();assert.match(await page.locator('pre.json').first().innerText(),/formation/);
 await page.screenshot({path:'/artifacts/updated-alerts.png',fullPage:true});
 const response=await page.request.get(base+'/api/setups?active_only=false&limit=10000'),data=await response.json();let selected;
 for(const row of data.items.slice(0,30)){
  const bars=await (await page.request.get(`${base}/api/setups/${row.symbol}/${row.timeframe}/bars?exchange=${row.exchange}&limit=1000`)).json();
  if(bars.items?.length>30){selected=row;break;}
 }
 assert.ok(selected,'An actual market chart is required');
 await page.goto(`${base}/setups/${selected.exchange}/${selected.symbol}/${selected.timeframe}`);
 const chart=page.locator('svg.candle-chart');await chart.waitFor();assert.equal(await page.locator('input[type=range]').count(),0);
 const b=await chart.boundingBox();const center={x:b.x+b.width*.45,y:b.y+b.height*.5};
 const count=Number(await chart.getAttribute('data-visible-bars'));
 await page.mouse.move(center.x,center.y);await page.mouse.wheel(0,-400);await page.waitForTimeout(150);
 assert.ok(Number(await chart.getAttribute('data-visible-bars'))<count,'X wheel must zoom');
 const low=Number(await chart.getAttribute('data-price-low')),high=Number(await chart.getAttribute('data-price-high'));
 await page.mouse.move(b.x+b.width*.96,center.y);await page.mouse.wheel(0,-400);await page.waitForTimeout(150);
 assert.ok(Number(await chart.getAttribute('data-price-high'))-Number(await chart.getAttribute('data-price-low'))<high-low,'Y axis wheel must zoom');
 const times=await chart.locator(':scope > text').allTextContents();
 await page.mouse.move(center.x,center.y);await page.mouse.down();await page.mouse.move(center.x+120,center.y+20,{steps:6});await page.mouse.up();
 assert.notDeepEqual(await chart.locator(':scope > text').allTextContents(),times,'Drag must pan history');
 await page.keyboard.down('Shift');await page.mouse.move(center.x,center.y);await page.mouse.down();await page.mouse.move(center.x+90,center.y-45,{steps:6});await page.mouse.up();await page.keyboard.up('Shift');
 await chart.locator('.chart-ruler').waitFor();assert.match(await chart.locator('.chart-ruler text').textContent(),/%/);
 await page.screenshot({path:'/artifacts/updated-chart-ruler.png',fullPage:true});
 await page.keyboard.press('Escape');assert.equal(await chart.locator('.chart-ruler').count(),0);
 await chart.dblclick({position:{x:b.width*.45,y:b.height*.5}});assert.equal(Number(await chart.getAttribute('data-visible-bars')),count);
 await page.getByRole('button',{name:'7d',exact:true}).click();
 const metric=page.locator('.metric-chart svg').first();await metric.waitFor({timeout:30000});
 const points=await metric.locator('polyline').getAttribute('points'),mb=await metric.boundingBox();
 await page.mouse.move(mb.x+mb.width*.4,mb.y+mb.height*.5);await page.mouse.wheel(0,-350);await page.waitForTimeout(150);
 assert.notEqual(await metric.locator('polyline').getAttribute('points'),points,'Metric chart must zoom');
 const ids=await page.locator('clipPath').evaluateAll(nodes=>nodes.map(n=>n.id));assert.equal(new Set(ids).size,ids.length);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/artifacts/updated-chart-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 fs.writeFileSync('/artifacts/interactions.json',JSON.stringify({status:'PASS',symbol:selected.symbol,timeframe:selected.timeframe,checks:['attribute selection','X wheel','Y wheel','drag pan','Shift percentage ruler','Escape','double click reset','metric chart zoom','unique plot clips','mobile render'],errors},null,2));
 await browser.close();console.log('Browser interactions PASS');
})().catch(e=>{console.error(e);process.exit(1)});
