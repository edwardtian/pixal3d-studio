const puppeteer=require('puppeteer-core'); const approx=(a,b,eps)=>Math.abs(a-b)<eps;
(async()=>{
  const browser=await puppeteer.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--use-gl=swiftshader','--enable-webgl','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
  const page=await browser.newPage(); await page.setViewport({width:1600,height:900});
  await page.goto('http://127.0.0.1:8765/_synctest.html',{waitUntil:'networkidle2',timeout:60000});
  await page.waitForFunction(()=>{const a=document.getElementById('a'),b=document.getElementById('b');return a&&b&&a.loaded&&b.loaded&&typeof a.getCameraOrbit==='function';},{timeout:60000});

  await page.evaluate(()=>{
    window.setupCompareSyncInContainer = function(container){
      const v=container.querySelectorAll('model-viewer');
      const initialOrbit=new Map(),initialTarget=new Map(),initialFov=new Map(),initialMaxFov=new Map(),initialMinFov=new Map(),initialMinR=new Map(),initialMaxR=new Map();
      const MIN_ZOOM_RADIUS_RATIO=0.2,MIN_ZOOM_FOV_RATIO=0.4;
      function recordInitial(mv){if(initialOrbit.has(mv)||!mv.loaded)return;try{const o=mv.getCameraOrbit(),tg=mv.getCameraTarget(),fov=mv.getFieldOfView();if(o&&o.radius>0){
        initialOrbit.set(mv,{theta:o.theta,phi:o.phi,radius:o.radius});
        initialTarget.set(mv,{x:tg.x,y:tg.y,z:tg.z});
        initialFov.set(mv,fov);initialMaxFov.set(mv,mv.getMaximumFieldOfView());initialMinFov.set(mv,mv.getMinimumFieldOfView());
        // read min/max radius from current attribute strings
        const mo=mv.minCameraOrbit, xo=mv.maxCameraOrbit;
        function parseR(s){if(!s||s==='auto')return null;const m=s.match(/([\-0-9.]+)\s*m/);return m?parseFloat(m[1]):null;}
        initialMinR.set(mv, parseR(mo));
        initialMaxR.set(mv, parseR(xo));
        mv.minCameraOrbit='auto auto '+(o.radius*MIN_ZOOM_RADIUS_RATIO).toFixed(4)+'m';
        mv.minFieldOfView=''+(fov*MIN_ZOOM_FOV_RATIO).toFixed(2)+'deg';
        window.__init=window.__init||{};window.__init[mv.id]={o:{theta:o.theta,phi:o.phi,radius:o.radius},t:{x:tg.x,y:tg.y,z:tg.z},fov,max:mv.getMaximumFieldOfView(),min:mv.getMinimumFieldOfView()};
      }}catch(e){}}
      v.forEach(mv=>{mv.addEventListener('load',()=>{let f=0;const p=()=>{recordInitial(mv);if(!initialOrbit.has(mv)&&++f<60)requestAnimationFrame(p);};requestAnimationFrame(p);});});
      function propagateFrom(mv,orbit,target,fov){
        const srcInit=initialOrbit.get(mv)||orbit,srcInitT=initialTarget.get(mv)||{x:0,y:0,z:0},srcInitFov=initialFov.get(mv)||fov,srcInitMax=initialMaxFov.get(mv)||fov,srcInitMin=initialMinFov.get(mv)||fov;
        const srcInitR=srcInit.radius||orbit.radius;
        const radiusRatio=orbit.radius/srcInitR;
        const panOffsetX=(target.x-srcInitT.x)/srcInitR,panOffsetY=(target.y-srcInitT.y)/srcInitR,panOffsetZ=(target.z-srcInitT.z)/srcInitR;
        v.forEach(other=>{if(other===mv)return;const oI=initialOrbit.get(other),oIT=initialTarget.get(other),oIF=initialFov.get(other),oIMax=initialMaxFov.get(other),oIMin=initialMinFov.get(other);if(!oI||!oIT||!oIF)return;const oIR=oI.radius,oR=oIR*radiusRatio;
          // Compute the same deltaZoom the source experienced, then replicate on other using its own span.
          // deltaZoom source = log(fov/srcInitFov). But the *effective* deltaZoom accounting for clamp:
          // replicate the ratio of the radius movement across its [minR,maxR] span onto the fov [log min, log max] span.
          const srcMinR = initialMinR.get(mv) || (srcInitR*MIN_ZOOM_RADIUS_RATIO);
          const srcMaxR = initialMaxR.get(mv) || srcInitR;
          const otherMinR = (initialMinR.get(other)) || (oIR*MIN_ZOOM_RADIUS_RATIO);
          const otherMaxR = (initialMaxR.get(other)) || oIR;
          // fraction of zoom progress along radius span (from max toward min), clamped
          const srcSpan = srcMaxR - srcMinR;
          const otherSpan = otherMaxR - otherMinR;
          let frac;
          if (srcSpan>1e-9) frac = (srcMaxR - orbit.radius)/srcSpan; else frac=0;
          frac = Math.max(0, Math.min(1, frac));
          const otherLogSpan = Math.log(oIF) - Math.log(oIMin);
          const otherFov = otherLogSpan>1e-9 ? oIF*Math.exp(-frac*otherLogSpan) : oIF;
          other.cameraOrbit=(orbit.theta*180/Math.PI).toFixed(2)+'deg '+(orbit.phi*180/Math.PI).toFixed(2)+'deg '+oR.toFixed(4)+'m';
          other.cameraTarget=(oIT.x+panOffsetX*oIR).toFixed(4)+'m '+(oIT.y+panOffsetY*oIR).toFixed(4)+'m '+(oIT.z+panOffsetZ*oIR).toFixed(4)+'m';
          other.fieldOfView=otherFov.toFixed(2)+'deg';
          other.jumpCameraToGoal();
        });
      }
      v.forEach(mv=>{mv.addEventListener('camera-change',e=>{const source=e&&e.detail&&e.detail.source;if(source!=='user-interaction')return;recordInitial(mv);propagateFrom(mv,mv.getCameraOrbit(),mv.getCameraTarget(),mv.getFieldOfView());});});
      v.forEach(recordInitial);
    };
    window.setupCompareSyncInContainer(document.body);
  });

  const init=await page.evaluate(()=>window.__init);
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  async function snap(){return page.evaluate(()=>{const a=document.getElementById('a'),b=document.getElementById('b');const oa=a.getCameraOrbit(),ob=b.getCameraOrbit(),ta=a.getCameraTarget(),tb=b.getCameraTarget();return{a:{theta:oa.theta,phi:oa.phi,radius:oa.radius,fov:a.getFieldOfView(),tx:ta.x,ty:ta.y,tz:ta.z},b:{theta:ob.theta,phi:ob.phi,radius:ob.radius,fov:b.getFieldOfView(),tx:tb.x,ty:tb.y,tz:tb.z}};});}

  // zoom on B
  await page.evaluate(()=>{const el=document.getElementById('b'),input=el.shadowRoot.querySelector('.userInput'),r=input.getBoundingClientRect();input.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,deltaY:-500,deltaMode:0}));});await wait(1800);
  let s=await snap();
  const iA=init.a,iB=init.b;
  const rA=s.a.radius/iA.o.radius,rB=s.b.radius/iB.o.radius,fA=s.a.fov/iA.fov,fB=s.b.fov/iB.fov;
  console.log("[ZOOM radius-span-fraction -> fov-log-span approach]");
  console.log("  rRatioA="+rA.toFixed(4)+" rRatioB="+rB.toFixed(4)+" match:",approx(rA,rB,0.03));
  console.log("  fRatioA="+fA.toFixed(4)+" fRatioB="+fB.toFixed(4)+" match:",approx(fA,fB,0.03));
  console.log("  fovA="+s.a.fov.toFixed(4)+" fovB="+s.b.fov.toFixed(4));
  await browser.close();
})().catch(e=>{console.error("FATAL",e);process.exit(1);});
