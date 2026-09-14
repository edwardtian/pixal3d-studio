window.setupCompareSyncInContainer = function(container){
  const v = container.querySelectorAll('model-viewer');
  const initialOrbit = new Map(), initialTarget = new Map(), initialFov = new Map();
  const initialMaxFov = new Map(), initialMinFov = new Map();
  const initialMinR = new Map(), initialMaxR = new Map();
  const MIN_ZOOM_RADIUS_RATIO = 0.2, MIN_ZOOM_FOV_RATIO = 0.4;

  function parseRadius(s) {
    if (!s || s === 'auto') return null;
    const m = String(s).match(/([-0-9.]+)\s*m/);
    return m ? parseFloat(m[1]) : null;
  }

  function recordInitial(mv) {
    if (initialOrbit.has(mv) || !mv.loaded) return;
    try {
      const o = mv.getCameraOrbit(), tg = mv.getCameraTarget(), fov = mv.getFieldOfView();
      if (o && o.radius > 0) {
        initialOrbit.set(mv, { theta: o.theta, phi: o.phi, radius: o.radius });
        initialTarget.set(mv, { x: tg.x, y: tg.y, z: tg.z });
        initialFov.set(mv, fov);
        initialMaxFov.set(mv, mv.getMaximumFieldOfView());
        initialMinFov.set(mv, mv.getMinimumFieldOfView());
        initialMinR.set(mv, parseRadius(mv.minCameraOrbit));
        initialMaxR.set(mv, parseRadius(mv.maxCameraOrbit));
        mv.minCameraOrbit = 'auto auto ' + (o.radius * MIN_ZOOM_RADIUS_RATIO).toFixed(4) + 'm';
        mv.minFieldOfView = '' + (fov * MIN_ZOOM_FOV_RATIO).toFixed(2) + 'deg';
      }
    } catch (e) { /* ignore */ }
  }

  v.forEach(mv => {
    mv.addEventListener('load', () => {
      let f = 0;
      const p = () => { recordInitial(mv); if (!initialOrbit.has(mv) && ++f < 60) requestAnimationFrame(p); };
      requestAnimationFrame(p);
    });
  });

  function propagateFrom(mv, orbit, target, fov) {
    const srcInit = initialOrbit.get(mv) || orbit;
    const srcInitT = initialTarget.get(mv) || { x: 0, y: 0, z: 0 };
    const srcInitFov = initialFov.get(mv) || fov;
    const srcInitR = srcInit.radius || orbit.radius;
    const radiusRatio = orbit.radius / srcInitR;
    const panOffsetX = (target.x - srcInitT.x) / srcInitR;
    const panOffsetY = (target.y - srcInitT.y) / srcInitR;
    const panOffsetZ = (target.z - srcInitT.z) / srcInitR;

    const srcMinR = initialMinR.get(mv) != null ? initialMinR.get(mv) : (srcInitR * MIN_ZOOM_RADIUS_RATIO);
    const srcMaxR = initialMaxR.get(mv) != null ? initialMaxR.get(mv) : srcInitR;
    const srcSpan = srcMaxR - srcMinR;
    let frac = srcSpan > 1e-9 ? (srcMaxR - orbit.radius) / srcSpan : 0;
    frac = Math.max(0, Math.min(1, frac));

    v.forEach(other => {
      if (other === mv) return;
      const oI = initialOrbit.get(other), oIT = initialTarget.get(other), oIF = initialFov.get(other), oIMin = initialMinFov.get(other);
      if (!oI || !oIT || !oIF || !oIMin) return;
      const oIR = oI.radius, oR = oIR * radiusRatio;
      const otherLogSpan = Math.log(oIF) - Math.log(oIMin);
      const otherFov = otherLogSpan > 1e-9 ? oIF * Math.exp(-frac * otherLogSpan) : oIF;
      other.cameraOrbit = (orbit.theta * 180 / Math.PI).toFixed(2) + 'deg ' + (orbit.phi * 180 / Math.PI).toFixed(2) + 'deg ' + oR.toFixed(4) + 'm';
      other.cameraTarget = (oIT.x + panOffsetX * oIR).toFixed(4) + 'm ' + (oIT.y + panOffsetY * oIR).toFixed(4) + 'm ' + (oIT.z + panOffsetZ * oIR).toFixed(4) + 'm';
      other.fieldOfView = otherFov.toFixed(2) + 'deg';
      other.jumpCameraToGoal();
    });
  }

  v.forEach(mv => {
    mv.addEventListener('camera-change', (event) => {
      const source = event && event.detail && event.detail.source;
      if (source !== 'user-interaction') return;
      recordInitial(mv);
      propagateFrom(mv, mv.getCameraOrbit(), mv.getCameraTarget(), mv.getFieldOfView());
    });
  });
  v.forEach(recordInitial);
};
