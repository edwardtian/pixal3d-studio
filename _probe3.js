const puppeteer=require('puppeteer-core');
(async()=>{
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});
  // load a third model with different aspect by giving different viewport size
  const info=await page.evaluate(()=>{
    const a=document.getElementById('a'),b=document.getElementById('b');
    // also check maxCameraOrbit attribute default and farRadius
    return {a:{fov:a.getFieldOfView(),max:a.getMaximumFieldOfView(),min:a.getMinimumFieldOfView(),maxOrbit:a.maxCameraOrbit,minOrbit:a.minCameraOrbit},b:{fov:b.getFieldOfView(),max:b.getMaximumFieldOfView(),min:b.getMinimumFieldOfView(),maxOrbit:b.maxCameraOrbit,minOrbit:b.minCameraOrbit}};
  });
  console.log("A:",JSON.stringify(info.a));
  console.log("B:",JSON.stringify(info.b));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
