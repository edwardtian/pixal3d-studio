
const puppeteer = require('puppeteer-core');
const approx=(a,b,eps)=>Math.abs(a-b)<eps;
(async()=>{
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});

  // record baseline & instrument source events
  await page.evaluate(()=>{
    window.__log=[];
    ['a','b'].forEach(id=>{
      const el=document.getElementById(id);
      el.addEventListener('camera-change',e=>window.__log.push({id,source:e.detail&&e.detail.source,r:el.getCameraOrbit().radius.toFixed(4),fov:el.getFieldOfView().toFixed(4)}));
    });
  });

  async function pan(sel,dx,dy,modifier){
    await page.evaluate((sel,dx,dy,mod)=>{
      const el=document.querySelector(sel); const input=el.shadowRoot.querySelector('.userInput');
      const r=input.getBoundingClientRect(); const cx=r.left+r.width/2, cy=r.top+r.height/2;
      const o={bubbles:true,cancelable:true,clientX:cx,clientY:cy,pointerId:3,pointerType:'mouse',button:0,buttons:1};
      if(mod==='shift') o.shiftKey=true; if(mod==='ctrl') o.ctrlKey=true;
      input.dispatchEvent(new PointerEvent('pointerdown',o));
      const steps=15;
      for(let i=1;i<=steps;i++){ input.dispatchEvent(new PointerEvent('pointermove',Object.assign({},o,{clientX:cx+dx*i/steps,clientY:cy+dy*i/steps}))); }
      input.dispatchEvent(new PointerEvent('pointerup',Object.assign({},o,{clientX:cx+dx,clientY:cy+dy,buttons:0})));
    },sel,dx,dy,modifier);
    await new Promise(r=>setTimeout(r,1500));
  }

  await pan('#a',80,-50,'shift');
  const log=await page.evaluate(()=>window.__log);
  console.log("PAN events captured:", log.length);
  console.log(JSON.stringify(log.slice(0,8)));
  const after=await page.evaluate(()=>{
    const a=document.getElementById('a'),b=document.getElementById('b');
    return {ta:a.getCameraTarget(),tb:b.getCameraTarget()};
  });
  console.log("targetA", JSON.stringify(after.ta), "targetB", JSON.stringify(after.tb));
  console.log("errors", JSON.stringify(errors));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
