
const puppeteer=require('puppeteer-core');
(async()=>{
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});
  const info=await page.evaluate(()=>{
    const a=document.getElementById('a'),b=document.getElementById('b');
    return {
      a:{minFov:a.getMinimumFieldOfView(),maxFov:a.getMaximumFieldOfView(),fov:a.getFieldOfView(),minOrbit:a.minCameraOrbit,maxOrbit:a.maxCameraOrbit},
      b:{minFov:b.getMinimumFieldOfView(),maxFov:b.getMaximumFieldOfView(),fov:b.getFieldOfView(),minOrbit:b.minCameraOrbit,maxOrbit:b.maxCameraOrbit},
    };
  });
  console.log("A limits:",JSON.stringify(info.a));
  console.log("B limits:",JSON.stringify(info.b));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
