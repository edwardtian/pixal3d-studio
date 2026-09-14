const puppeteer=require('puppeteer-core'); const fs=require('fs'); const approx=(a,b,eps)=>Math.abs(a-b)<eps;
(async()=>{
  const SYNC_IMPL = fs.readFileSync('_sync_impl.js','utf8');
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});
  await page.evaluate(SYNC_IMPL+'\nwindow.setupCompareSyncInContainer(document.body);');

  const init=await page.evaluate(()=>{const a=document.getElementById('a'),b=document.getElementById('b');const oa=a.getCameraOrbit(),ob=b.getCameraOrbit(),ta=a.getCameraTarget(),tb=b.getCameraTarget();return{a:{o:oa,t:ta,fov:a.getFieldOfView(),min:a.getMinimumFieldOfView(),max:a.getMaximumFieldOfView()},b:{o:ob,t:tb,fov:b.getFieldOfView(),min:b.getMinimumFieldOfView(),max:b.getMaximumFieldOfView()}};});
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  async function snap(){return page.evaluate(()=>{const a=document.getElementById('a'),b=document.getElementById('b');const oa=a.getCameraOrbit(),ob=b.getCameraOrbit(),ta=a.getCameraTarget(),tb=b.getCameraTarget();return{a:{theta:oa.theta,phi:oa.phi,radius:oa.radius,fov:a.getFieldOfView(),tx:ta.x,ty:ta.y,tz:ta.z},b:{theta:ob.theta,phi:ob.phi,radius:ob.radius,fov:b.getFieldOfView(),tx:tb.x,ty:tb.y,tz:tb.z}};});}
  async function drag(sel,dx,dy){await page.evaluate((sel,dx,dy)=>{const el=document.querySelector(sel),input=el.shadowRoot.querySelector('.userInput'),r=input.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;const o={bubbles:true,cancelable:true,clientX:cx,clientY:cy,pointerId:1,pointerType:'mouse',button:0,buttons:1};input.dispatchEvent(new PointerEvent('pointerdown',o));const s=12;for(let i=1;i<=s;i++)input.dispatchEvent(new PointerEvent('pointermove',Object.assign({},o,{clientX:cx+dx*i/s,clientY:cy+dy*i/s})));input.dispatchEvent(new PointerEvent('pointerup',Object.assign({},o,{clientX:cx+dx,clientY:cy+dy,buttons:0})));},sel,dx,dy);await wait(1500);}
  async function wheel(sel,d){await page.evaluate((sel,d)=>{const el=document.querySelector(sel),input=el.shadowRoot.querySelector('.userInput'),r=input.getBoundingClientRect();input.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,deltaY:d,deltaMode:0}));},sel,d);await wait(1500);}
  async function pan(sel,dx,dy,mod){await page.evaluate((sel,dx,dy,m)=>{const el=document.querySelector(sel),input=el.shadowRoot.querySelector('.userInput'),r=input.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;const o={bubbles:true,cancelable:true,clientX:cx,clientY:cy,pointerId:3,pointerType:'mouse',button:0,buttons:1};if(m==='shift')o.shiftKey=true;if(m==='ctrl')o.ctrlKey=true;input.dispatchEvent(new PointerEvent('pointerdown',o));const s=15;for(let i=1;i<=s;i++)input.dispatchEvent(new PointerEvent('pointermove',Object.assign({},o,{clientX:cx+dx*i/s,clientY:cy+dy*i/s})));input.dispatchEvent(new PointerEvent('pointerup',Object.assign({},o,{clientX:cx+dx,clientY:cy+dy,buttons:0})));},sel,dx,dy,mod);await wait(1800);}

  const results=[];
  await drag('#a',150,40); let s=await snap();
  let ok=approx(s.a.theta,s.b.theta,0.01)&&approx(s.a.phi,s.b.phi,0.01); results.push(['rotate',ok]); console.log('rotate',ok,'theta',s.a.theta.toFixed(4),s.b.theta.toFixed(4),'phi',s.a.phi.toFixed(4),s.b.phi.toFixed(4));

  await wheel('#b',-500); s=await snap();
  const iA=init.a,iB=init.b;
  const rA=s.a.radius/iA.o.radius,rB=s.b.radius/iB.o.radius,fA=s.a.fov/iA.fov,fB=s.b.fov/iB.fov;
  ok=approx(rA,rB,0.03)&&approx(fA,fB,0.03); results.push(['zoom-in',ok]); console.log('zoom-in',ok,'rR',rA.toFixed(4),rB.toFixed(4),'fR',fA.toFixed(4),fB.toFixed(4),'fov',s.a.fov.toFixed(3),s.b.fov.toFixed(3));

  await wheel('#a',300); s=await snap();
  const rA2=s.a.radius/iA.o.radius,rB2=s.b.radius/iB.o.radius,fA2=s.a.fov/iA.fov,fB2=s.b.fov/iB.fov;
  ok=approx(rA2,rB2,0.03)&&approx(fA2,fB2,0.03); results.push(['zoom-mixed',ok]); console.log('zoom-mixed',ok,'rR',rA2.toFixed(4),rB2.toFixed(4),'fR',fA2.toFixed(4),fB2.toFixed(4));

  await pan('#a',80,-50,'shift'); s=await snap();
  const pAx=(s.a.tx-iA.t.x)/iA.o.radius,pBx=(s.b.tx-iB.t.x)/iB.o.radius,pAy=(s.a.ty-iA.t.y)/iA.o.radius,pBy=(s.b.ty-iB.t.y)/iB.o.radius,pAz=(s.a.tz-iA.t.z)/iA.o.radius,pBz=(s.b.tz-iB.t.z)/iB.o.radius;
  ok=approx(pAx,pBx,0.02)&&approx(pAy,pBy,0.02)&&approx(pAz,pBz,0.02); results.push(['pan',ok]); console.log('pan',ok,'pX',pAx.toFixed(4),pBx.toFixed(4),'pY',pAy.toFixed(4),pBy.toFixed(4),'pZ',pAz.toFixed(4),pBz.toFixed(4));

  console.log('\nSUMMARY:', results.map(r=>r[0]+':'+(r[1]?'PASS':'FAIL')).join('  '));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
