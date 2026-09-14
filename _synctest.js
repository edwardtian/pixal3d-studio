
const puppeteer = require('puppeteer-core');
const approx = (a,b,eps)=>Math.abs(a-b)<eps;

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: true,
    args: ['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']
  });
  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 900, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: '+e.message));
  await page.goto('http://127.0.0.1:8765/_synctest.html', {waitUntil: 'networkidle2', timeout: 60000});
  await page.waitForFunction(() => {
    const a = document.getElementById('a'), b = document.getElementById('b');
    return a && b && a.loaded && b.loaded && typeof a.getCameraOrbit === 'function';
  }, {timeout: 60000});

  await page.evaluate(() => {
    window.setupCompareSyncInContainer = function setupCompareSyncInContainer(container) {
        const v = container.querySelectorAll('model-viewer');
        window.__viewers = v;
        const initialOrbit = new Map(), initialTarget = new Map(), initialFov = new Map();
        const MIN_ZOOM_RADIUS_RATIO = 0.2, MIN_ZOOM_FOV_RATIO = 0.4;
        function recordInitial(mv) {
            if (initialOrbit.has(mv) || !mv.loaded) return;
            try {
                const o = mv.getCameraOrbit(), tg = mv.getCameraTarget(), fov = mv.getFieldOfView();
                if (o && o.radius > 0) {
                    initialOrbit.set(mv, {theta:o.theta,phi:o.phi,radius:o.radius});
                    initialTarget.set(mv,{x:tg.x,y:tg.y,z:tg.z});
                    initialFov.set(mv,fov);
                    mv.minCameraOrbit='auto auto '+(o.radius*MIN_ZOOM_RADIUS_RATIO).toFixed(4)+'m';
                    mv.minFieldOfView=''+(fov*MIN_ZOOM_FOV_RATIO).toFixed(2)+'deg';
                    window.__init=window.__init||{};
                    window.__init[mv.id]={o:{theta:o.theta,phi:o.phi,radius:o.radius},t:{x:tg.x,y:tg.y,z:tg.z},fov};
                }
            } catch(e){}
        }
        v.forEach(mv=>{ mv.addEventListener('load',()=>{let f=0;const p=()=>{recordInitial(mv);if(!initialOrbit.has(mv)&&++f<60)requestAnimationFrame(p);};requestAnimationFrame(p);});});
        function propagateFrom(mv, orbit, target, fov) {
            const srcInit=initialOrbit.get(mv)||orbit, srcInitT=initialTarget.get(mv)||{x:0,y:0,z:0}, srcInitFov=initialFov.get(mv)||fov;
            const srcInitR=srcInit.radius||orbit.radius;
            const radiusRatio=orbit.radius/srcInitR, fovRatio=fov/srcInitFov;
            const panOffsetX=(target.x-srcInitT.x)/srcInitR, panOffsetY=(target.y-srcInitT.y)/srcInitR, panOffsetZ=(target.z-srcInitT.z)/srcInitR;
            v.forEach(other=>{
                if(other===mv)return;
                const oI=initialOrbit.get(other),oIT=initialTarget.get(other),oIF=initialFov.get(other);
                if(!oI||!oIT||!oIF)return;
                const oIR=oI.radius, oR=oIR*radiusRatio;
                other.cameraOrbit=(orbit.theta*180/Math.PI).toFixed(2)+'deg '+(orbit.phi*180/Math.PI).toFixed(2)+'deg '+oR.toFixed(4)+'m';
                other.cameraTarget=(oIT.x+panOffsetX*oIR).toFixed(4)+'m '+(oIT.y+panOffsetY*oIR).toFixed(4)+'m '+(oIT.z+panOffsetZ*oIR).toFixed(4)+'m';
                other.fieldOfView=(oIF*fovRatio).toFixed(2)+'deg';
                other.jumpCameraToGoal();
            });
        }
        v.forEach(mv=>{ mv.addEventListener('camera-change',(event)=>{
            const source=event&&event.detail&&event.detail.source;
            if(source!=='user-interaction')return;
            recordInitial(mv);
            propagateFrom(mv,mv.getCameraOrbit(),mv.getCameraTarget(),mv.getFieldOfView());
        });});
        v.forEach(recordInitial);
    };
    window.setupCompareSyncInContainer(document.body);
  });

  async function wait(ms){return new Promise(r=>setTimeout(r,ms));}

  async function dragRotate(selector, dx, dy) {
    await page.evaluate((sel,dx,dy)=>{
      const el=document.querySelector(sel);
      const input=el.shadowRoot.querySelector('.userInput');
      const r=input.getBoundingClientRect();
      const cx=r.left+r.width/2, cy=r.top+r.height/2;
      const o={bubbles:true,cancelable:true,clientX:cx,clientY:cy,pointerId:1,pointerType:'mouse',button:0,buttons:1};
      input.dispatchEvent(new PointerEvent('pointerdown',o));
      const steps=12;
      for(let i=1;i<=steps;i++){
        const oo=Object.assign({},o,{clientX:cx+dx*i/steps,clientY:cy+dy*i/steps});
        input.dispatchEvent(new PointerEvent('pointermove',oo));
      }
      input.dispatchEvent(new PointerEvent('pointerup',Object.assign({},o,{clientX:cx+dx,clientY:cy+dy,buttons:0})));
    },selector,dx,dy);
    await wait(1500);
  }

  async function wheel(selector, delta) {
    await page.evaluate((sel,d)=>{
      const el=document.querySelector(sel);
      const input=el.shadowRoot.querySelector('.userInput');
      const r=input.getBoundingClientRect();
      input.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,deltaY:d,deltaMode:0}));
    },selector,delta);
    await wait(1500);
  }

  async function pan(selector, dx, dy) {
    await page.evaluate((sel,dx,dy)=>{
      const el=document.querySelector(sel);
      const input=el.shadowRoot.querySelector('.userInput');
      const r=input.getBoundingClientRect();
      const cx=r.left+r.width/2, cy=r.top+r.height/2;
      const o={bubbles:true,cancelable:true,clientX:cx,clientY:cy,pointerId:2,pointerType:'mouse',button:0,buttons:2}; // right button = pan
      input.dispatchEvent(new PointerEvent('pointerdown',o));
      const steps=12;
      for(let i=1;i<=steps;i++){
        input.dispatchEvent(new PointerEvent('pointermove',Object.assign({},o,{clientX:cx+dx*i/steps,clientY:cy+dy*i/steps})));
      }
      input.dispatchEvent(new PointerEvent('pointerup',Object.assign({},o,{clientX:cx+dx,clientY:cy+dy,buttons:0})));
    },selector,dx,dy);
    await wait(1500);
  }

  async function snap(){
    return await page.evaluate(()=>{
      const a=document.getElementById('a'),b=document.getElementById('b');
      const oa=a.getCameraOrbit(),ob=b.getCameraOrbit(),ta=a.getCameraTarget(),tb=b.getCameraTarget();
      return {a:{theta:oa.theta,phi:oa.phi,radius:oa.radius,fov:a.getFieldOfView(),tx:ta.x,ty:ta.y,tz:ta.z},
              b:{theta:ob.theta,phi:ob.phi,radius:ob.radius,fov:b.getFieldOfView(),tx:tb.x,ty:tb.y,tz:tb.z}};
    });
  }

  const init = await page.evaluate(()=>window.__init);
  console.log("INIT A:",JSON.stringify(init.a));
  console.log("INIT B:",JSON.stringify(init.b));

  // TEST rotation
  await dragRotate('#a',150,40);
  let s=await snap();
  console.log("\n[ROTATE] thetaA="+s.a.theta.toFixed(4)+" thetaB="+s.b.theta.toFixed(4)+"  phiA="+s.a.phi.toFixed(4)+" phiB="+s.b.phi.toFixed(4));
  console.log("  rotate match:", approx(s.a.theta,s.b.theta,0.01)&&approx(s.a.phi,s.b.phi,0.01));

  // TEST zoom on B
  await wheel('#b',-400);
  s=await snap();
  const iA=init.a,iB=init.b;
  const rA=s.a.radius/iA.o.radius, rB=s.b.radius/iB.o.radius, fA=s.a.fov/iA.fov, fB=s.b.fov/iB.fov;
  console.log("\n[ZOOM] rA="+s.a.radius.toFixed(4)+" rB="+s.b.radius.toFixed(4)+" rRatioA="+rA.toFixed(4)+" rRatioB="+rB.toFixed(4));
  console.log("  fovA="+s.a.fov.toFixed(4)+" fovB="+s.b.fov.toFixed(4)+" fRatioA="+fA.toFixed(4)+" fRatioB="+fB.toFixed(4));
  console.log("  zoom radiusRatio match:", approx(rA,rB,0.03), " fovRatio match:", approx(fA,fB,0.03));

  // TEST pan on A
  await pan('#a',60,-40);
  s=await snap();
  const pAx=(s.a.tx-iA.t.x)/iA.o.radius, pBx=(s.b.tx-iB.t.x)/iB.o.radius;
  const pAy=(s.a.ty-iA.t.y)/iA.o.radius, pBy=(s.b.ty-iB.t.y)/iB.o.radius;
  console.log("\n[PAN] panOffsetX A="+pAx.toFixed(4)+" B="+pBx.toFixed(4)+"  panOffsetY A="+pAy.toFixed(4)+" B="+pBy.toFixed(4));
  console.log("  pan match:", approx(pAx,pBx,0.02)&&approx(pAy,pBy,0.02));

  console.log("\nPAGE ERRORS:",JSON.stringify(errors));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
