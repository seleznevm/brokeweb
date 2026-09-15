const { chromium } = require('playwright');
const fs = require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1600,height:1050}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.BASE_URL||'http://frontend';
 const response=await page.request.get(base+'/api/setups?active_only=false');
 const data=await response.json();const first=data.items[0];
 const routes=['/setups','/alerts','/settings','/health','/parity'];
 if(first)routes.splice(1,0,`/setups/${first.exchange}/${first.symbol}/${first.timeframe}`);
 const results=[];
 for(const route of routes){
  await page.goto(base+route);await page.locator('main h1').waitFor();
  await page.waitForTimeout(1200);
  if(route==='/setups'){
    const check=page.locator('input[type=checkbox]');if(await check.isChecked())await check.uncheck();
    if(first)await page.getByRole('link',{name:first.symbol}).first().waitFor();
  }
  const title=await page.locator('main h1').innerText();
  const fatal=await page.getByText('Не удалось отобразить страницу').count();
  if(fatal)throw new Error('React error boundary on '+route);
  const filename=route.startsWith('/setups/')?'detail':route.slice(1);
  await page.screenshot({path:'/artifacts/'+filename+'.png',fullPage:true});
  results.push({route,title,bodyLength:(await page.locator('main').innerText()).length});
 }
 // Mobile layout and navigation are also exercised against actual API responses.
 await page.setViewportSize({width:390,height:844});await page.goto(base+'/setups');await page.locator('main h1').waitFor();
 await page.screenshot({path:'/artifacts/mobile.png',fullPage:true});
 fs.writeFileSync('/artifacts/browser.json',JSON.stringify({results,errors},null,2));
 await browser.close();if(errors.length)throw new Error(errors.join('\n'));console.log(JSON.stringify({pages:results.length,errors:errors.length}));
})().catch(error=>{console.error(error);process.exit(1)});
