// Local-only sample scene. Replace buildSampleHome when house-model import is added.
const viewport = document.getElementById('mapViewport');
const fallback = document.getElementById('mapFallback');
const dashboard = document.getElementById('dashboard');
const buttons = [...document.querySelectorAll('.map-toolbar button')];
let started = false;

async function startMap() {
  try {
    const THREE = await import('three');
    const {OrbitControls} = await import('/assets/vendor/three/OrbitControls.js');
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#0c1921');
    const camera = new THREE.PerspectiveCamera(42, 1, 0.03, 200);
    const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const canvas = renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute('role', 'region');
    canvas.setAttribute('aria-label', 'Interactive sample house. Pan, zoom, and orbit the floor plan.');
    canvas.setAttribute('aria-describedby', 'mapHelp');
    viewport.prepend(canvas);
    const controls = new OrbitControls(camera, canvas);
    controls.minDistance = 0.6;
    controls.maxDistance = 42;
    controls.maxPolarAngle = Math.PI / 2 - 0.03;
    controls.maxTargetRadius = 16;
    controls.screenSpacePanning = false;
    controls.listenToKeyEvents(canvas);
    scene.add(new THREE.HemisphereLight(0xdff5ff, 0x354c43, 2.8));
    const sun = new THREE.DirectionalLight(0xfff4da, 2.2);
    sun.position.set(-3, 12, 7);
    scene.add(sun);
    const grid = new THREE.GridHelper(40, 40, 0x29434e, 0x182f39);
    grid.position.y = -0.16;
    scene.add(grid);
    buildSampleHome(THREE, scene);

    // No animation loop: draw only on interaction/resize, and never while signed out.
    let frame = 0;
    function render() {
      if (frame || dashboard.hidden || document.hidden) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (dashboard.hidden || document.hidden) return;
        renderer.render(scene, camera);
        // Camera state also makes local UI diagnostics possible without exposing a global API.
        canvas.dataset.view = JSON.stringify({position: camera.position.toArray(), target: controls.target.toArray()});
      });
    }
    const fitDistance = () => Math.max(19, 24 / camera.aspect);
    function resize() {
      const {width, height} = viewport.getBoundingClientRect();
      if (!width || !height) return;
      const previousFit = fitDistance();
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      // Preserve relative zoom while keeping the house framed across responsive layouts.
      zoom(fitDistance() / previousFit);
      render();
    }
    function reset(top = false) {
      controls.target.set(0, 0, 0);
      const distance = fitDistance();
      camera.position.copy(new THREE.Vector3(...(top ? [0, 1, 0.001] : [0.8, 1, 1.15])).normalize().multiplyScalar(distance));
      controls.update();
      render();
    }
    function zoom(factor) {
      const offset = camera.position.clone().sub(controls.target);
      offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
      camera.position.copy(controls.target).add(offset);
      controls.update();
    }
    function mode(pan) {
      controls.mouseButtons.LEFT = pan ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
      controls.touches.ONE = pan ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
      document.getElementById('mapPan').setAttribute('aria-pressed', String(pan));
      document.getElementById('mapOrbit').setAttribute('aria-pressed', String(!pan));
      document.getElementById('mapHelp').textContent = pan
        ? 'Drag to pan. Scroll to zoom. Choose Orbit & tilt to rotate. Touch: drag to pan, pinch to zoom.'
        : 'Drag to orbit and tilt. Right-drag to pan. Scroll to zoom. Touch: one finger to orbit, two to pan or zoom.';
    }
    document.getElementById('mapOrbit').onclick = () => mode(false);
    document.getElementById('mapPan').onclick = () => mode(true);
    document.getElementById('mapZoomIn').onclick = () => zoom(0.8);
    document.getElementById('mapZoomOut').onclick = () => zoom(1.25);
    document.getElementById('mapTop').onclick = () => reset(true);
    document.getElementById('mapReset').onclick = () => {mode(false); reset();};
    canvas.addEventListener('pointerdown', () => canvas.focus({preventScroll: true}));
    canvas.addEventListener('keydown', event => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (!['+', '=', '-', 'r', 't'].includes(key)) return;
      event.preventDefault();
      if (key === 'r') {mode(false); reset();}
      else if (key === 't') reset(true);
      else zoom(key === '-' ? 1.25 : 0.8);
    });
    controls.addEventListener('change', render);
    new ResizeObserver(resize).observe(viewport);
    document.addEventListener('visibilitychange', render);
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      fallback.textContent = 'The 3D view was paused by your browser. Reload this page to restore it.';
      fallback.hidden = false;
      buttons.forEach(button => {button.disabled = true;});
    });
    resize(); reset();
    buttons.forEach(button => {button.disabled = false;});
    fallback.hidden = true;
    viewport.dataset.ready = 'true';
  } catch (error) {
    fallback.textContent = 'The 3D map needs a browser with WebGL 2 enabled. Paging and voice controls below are still available.';
    viewport.dataset.ready = 'unavailable';
    console.warn('Monitor Map unavailable:', error.message);
  }
}

function buildSampleHome(T, scene) {
  const home = new T.Group();
  scene.add(home);
  function box(x, y, z, w, h, d, color, edges = false) {
    const geometry = new T.BoxGeometry(w, h, d);
    const mesh = new T.Mesh(geometry, new T.MeshStandardMaterial({color, roughness: 0.85}));
    mesh.position.set(x, y, z);
    home.add(mesh);
    if (edges) {
      const lines = new T.LineSegments(new T.EdgesGeometry(geometry), new T.LineBasicMaterial({color: 0x6c9997}));
      mesh.add(lines);
    }
    return mesh;
  }
  // Roofless cutaway: low walls keep the rooms visible when looking around inside.
  box(0, -0.10, 0, 12.3, 0.2, 8.3, 0x29454d, true);
  box(-2.55, 0.015, 0, 6.7, 0.03, 7.8, 0x244c4e);
  box(3.5, 0.015, -2, 4.7, 0.03, 3.8, 0x354953);
  box(3.5, 0.015, 2, 4.7, 0.03, 3.8, 0x414b4e);
  const wall = (x, z, w, d) => box(x, 0.5, z, w, 1, d, 0x9ebbb7, true);
  wall(0, -4, 12.2, 0.15); wall(-6, 0, 0.15, 8); wall(6, 0, 0.15, 8);
  wall(-3.7, 4, 4.6, 0.15); wall(3.1, 4, 5.8, 0.15);
  wall(1, -2.65, 0.15, 2.7); wall(1, 0.1, 0.15, 0.8); wall(1, 3, 0.15, 2);
  wall(3.5, 0, 5, 0.15);
  // Living room: sofa, rug, low table.
  box(-3.5, 0.055, 1.25, 3.9, 0.05, 2.65, 0x37686a);
  box(-4.3, 0.3, 0.3, 2.8, 0.5, 0.9, 0x81a69b, true);
  box(-4.3, 0.63, -0.03, 2.8, 0.34, 0.22, 0x81a69b);
  box(-3.2, 0.25, 1.7, 1.4, 0.45, 0.75, 0xb39a73, true);
  // Kitchen: perimeter counter and island.
  box(-3.8, 0.4, -3.35, 3.8, 0.8, 0.9, 0x708e92, true);
  box(-5.3, 0.4, -2.45, 0.8, 0.8, 1.7, 0x708e92, true);
  box(-2.7, 0.43, -1.9, 2.3, 0.86, 0.8, 0xb39a73, true);
  // Bedroom and study.
  box(3.9, 0.27, -2.5, 2.1, 0.5, 2.5, 0x7397a5, true);
  box(3.9, 0.56, -3.27, 1.8, 0.14, 0.55, 0xc2d2cb);
  box(4.8, 0.4, 1.3, 1.9, 0.8, 0.8, 0xb39a73, true);
  box(4.8, 0.22, 2.2, 0.65, 0.4, 0.65, 0x81a69b, true);
  function label(text, x, z) {
    const surface = document.createElement('canvas');
    surface.width = 512; surface.height = 96;
    const ctx = surface.getContext('2d');
    ctx.font = '500 32px Segoe UI, sans-serif'; ctx.textAlign = 'center';
    ctx.fillStyle = '#d3e8df'; ctx.fillText(text, 256, 60);
    const texture = new T.CanvasTexture(surface); texture.colorSpace = T.SRGBColorSpace;
    const plane = new T.Mesh(new T.PlaneGeometry(3.3, 0.62), new T.MeshBasicMaterial({map: texture, transparent: true, depthWrite: false, side: T.DoubleSide}));
    plane.rotation.x = -Math.PI / 2; plane.position.set(x, 0.06, z); home.add(plane);
  }
  label('LIVING', -2.4, 3.1); label('KITCHEN', -2.5, -0.8);
  label('BEDROOM', 3.6, -0.7); label('STUDY', 3.5, 3.1);
}

const observer = new MutationObserver(() => {
  if (!dashboard.hidden && !started) {started = true; startMap();}
});
observer.observe(dashboard, {attributes: true, attributeFilter: ['hidden']});
if (!dashboard.hidden) {started = true; startMap();}
