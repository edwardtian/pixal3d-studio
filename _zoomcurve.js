const puppeteer=require('puppeteer-core');
(async()=>{
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});
  const wait=ms=>new Promise(r=>setTimeout(r,ms));

  // For B: do a series of wheel zooms and record radius & fov
  const samples=[];
  for(let i=0;i<6;i++){
    await page.evaluate(()=>{const el=document.getElementById('b'),input=el.shadowRoot.querySelector('.userInput'),r=input.getBoundingClientRect();input.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,deltaY:-200,deltaMode:0}));});
    await wait(1200);
    const s=await page.evaluate(()=>{const b=document.getElementById('b');const o=b.getCameraOrbit();return{r:o.radius,fov:b.getFieldOfView(),min:b.getMinimumFieldOfView(),max:b.getMaximumFieldOfView()};});
    samples.push(s);
  }
  console.log("B samples (r, fov, min, max):");
  samples.forEach((s,i)=>console.log("  #"+i+": r="+s.r.toFixed(4)+" fov="+s.fov.toFixed(4)+" min="+s.min.toFixed(4)+" max="+s.max.toFixed(4)));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
