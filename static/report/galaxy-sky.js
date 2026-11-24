import {seededRandom} from './topic1-layout.js?v=0.1.57';

const TAU = Math.PI * 2;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const mix = (a, b, t) => a + (b - a) * t;

function surface(size) {
  const image = document.createElement('canvas');
  image.width = image.height = size;
  return image;
}

function makeNoise(random) {
  const grid = Float32Array.from({length: 64 * 64}, random);
  return (x, y) => {
    const i = Math.floor(x), j = Math.floor(y);
    let u = x - i, v = y - j;
    u *= u * (3 - 2 * u);
    v *= v * (3 - 2 * v);
    const row = (j & 63) * 64, next = ((j + 1) & 63) * 64;
    return mix(mix(grid[row + (i & 63)], grid[row + ((i + 1) & 63)], u),
      mix(grid[next + (i & 63)], grid[next + ((i + 1) & 63)], u), v);
  };
}

function rgb(hue, saturation, lightness) {
  const component = offset => {
    const k = (offset + hue / 30) % 12;
    return 255 * (lightness - saturation * Math.min(lightness, 1 - lightness)
      * Math.max(-1, Math.min(k - 3, 9 - k, 1)));
  };
  return [component(0), component(8), component(4)];
}

// Bake the emission and extinction of the dusty spiral onto one face-on plate.
// Rotating it before inclination keeps the axis of the galaxy fixed in the sky.
function makeGalaxy(id, hue) {
  const random = seededRandom(id);
  const noise = makeNoise(random);
  const origin = random() * TAU, tightness = 2.4 + random() * .65;
  const winding = random() < .5 ? 1 : -1;
  const spiral = radius => origin + winding * (tightness * Math.log1p(6 * radius)
    + .14 * Math.sin(8 * radius + origin));
  const armColor = rgb(hue, .72, .68);
  const cloud = surface(320);
  const cloudContext = cloud.getContext('2d');
  const pixels = cloudContext.createImageData(cloud.width, cloud.height);
  const half = cloud.width / 2;
  for (let j = 0; j < cloud.height; j++) {
    const y = (j + .5 - half) / half;
    for (let i = 0; i < cloud.width; i++) {
      const x = (i + .5 - half) / half;
      const r = Math.hypot(x, y);
      if (r >= 1) continue;
      const large = noise(14 * x, 14 * y);
      const small = noise(43 * x, 43 * y);
      const grain = .47 * large + .34 * small + .19 * noise(106 * x, 106 * y);
      const phase = 2 * (Math.atan2(y, x) - spiral(r)) + .9 * (large - .5);
      const arm = Math.exp(-2.8 * (1 - Math.cos(phase)));
      const dustLane = Math.exp(-9 * (1 - Math.cos(phase + .78 * winding)));
      const taper = (1 - r) ** 1.35;
      const core = Math.exp(-r * 12);
      const light = taper * (.065 + (.14 + 1.48 * grain ** 1.8) * arm)
        * (.36 + 1.2 * grain) * (1 - dustLane * .83) + core * .8;
      const bulge = clamp(core * 1.8 + .14 * grain, 0, 1);
      const k = 4 * (j * cloud.width + i);
      pixels.data[k] = mix(armColor[0], 251, bulge);
      pixels.data[k + 1] = mix(armColor[1], 228, bulge);
      pixels.data[k + 2] = mix(armColor[2], 202, bulge);
      pixels.data[k + 3] = 255 * clamp(light, 0, .96);
    }
  }
  cloudContext.putImageData(pixels, 0, 0);

  const plate = surface(768);
  const ctx = plate.getContext('2d');
  const radius = plate.width / 2;
  ctx.drawImage(cloud, 0, 0, plate.width, plate.height);
  ctx.translate(radius, radius);
  const bright = `hsl(${hue} 88% 87%)`, cool = `hsl(${hue} 78% 75%)`;
  const colors = [bright, cool, bright, cool, '#ffdec4', bright, cool, '#c1d7ff'];
  // Independent knots make the arms granular even when the disk is seen edge on.
  for (let i = 0; i < 19500; i++) {
    const r = .015 + .96 * Math.sqrt(random() * random());
    const spread = (random() + random() + random() - 1.5) * (.3 + r * .8);
    const angle = random() < .74 ? spiral(r) + (random() < .5 ? 0 : Math.PI) + spread : random() * TAU;
    const x = Math.cos(angle) * r, y = Math.sin(angle) * r;
    const size = .26 + 1.2 * random() ** 3;
    ctx.fillStyle = colors[i % colors.length];
    ctx.globalAlpha = Math.min(1, (.15 + .7 * random()) * (1 - r ** 1.4) * (.38 + noise(16 * x, 16 * y)));
    ctx.fillRect(x * radius, y * radius, size, size);
  }
  ctx.globalAlpha = 1;
  const coreLight = ctx.createRadialGradient(0, 0, 0, 0, 0, .2 * radius);
  coreLight.addColorStop(0, 'rgba(255,251,239,.96)');
  coreLight.addColorStop(.08, 'rgba(255,242,219,.87)');
  coreLight.addColorStop(.26, 'rgba(255,223,194,.48)');
  coreLight.addColorStop(.56, 'rgba(255,215,181,.14)');
  coreLight.addColorStop(1, 'rgba(255,215,181,0)');
  ctx.fillStyle = coreLight;
  ctx.fillRect(-.2 * radius, -.2 * radius, .4 * radius, .4 * radius);

  const stars = Array.from({length: 105}, () => {
    const r = .09 + .87 * Math.sqrt(random() * random());
    return {r, angle: spiral(r) + (random() < .5 ? 0 : Math.PI) + (random() - .5) * .48,
      speed: .042 + .032 / (.55 + r), size: .38 + random() ** 3 * .95, phase: random() * TAU};
  });
  return {plate, stars, winding, hue};
}

function makeStarTile(ctx) {
  const tile = surface(640);
  const ink = tile.getContext('2d');
  const random = seededRandom('data-world-deep-sky');
  for (let i = 0; i < 310; i++) {
    const x = random() * tile.width, y = random() * tile.height;
    const radius = .35 + .72 * random() ** 2;
    ink.fillStyle = i % 7 ? '#bacef2' : '#ffe2cc';
    ink.globalAlpha = .17 + .6 * random() ** 2;
    ink.beginPath();
    ink.arc(x, y, radius, 0, TAU);
    ink.fill();
    if (i % 43 === 0) {
      ink.globalAlpha = .32;
      ink.fillRect(x - 2.4, y - .25, 4.8, .5);
      ink.fillRect(x - .25, y - 2.4, .5, 4.8);
    }
  }
  return ctx.createPattern(tile, 'repeat');
}

function drawGalaxy(ctx, texture, galaxy, time) {
  const {x, y, rx, ry, tilt, opacity} = galaxy;
  const rotation = texture.winding * time;
  ctx.save();
  ctx.globalAlpha = opacity;
  ctx.translate(x, y);
  ctx.rotate(tilt * Math.PI / 180);
  ctx.save();
  ctx.scale(rx, ry);
  ctx.rotate(rotation * .063);
  ctx.drawImage(texture.plate, -1, -1, 2, 2);
  ctx.restore();
  // Nearby luminous stars orbit at a radius-dependent speed; their points of
  // light stay circular in screen space instead of flattening with the plate.
  ctx.fillStyle = `hsl(${texture.hue} 90% 91%)`;
  for (const star of texture.stars) {
    const angle = star.angle + rotation * star.speed;
    const u = rx * star.r * Math.cos(angle), v = ry * star.r * Math.sin(angle);
    ctx.globalAlpha = opacity * (.47 + .32 * Math.sin(time * .88 + star.phase));
    ctx.beginPath();
    ctx.arc(u, v, star.size, 0, TAU);
    ctx.fill();
    if (star.size > 1.22) {
      ctx.globalAlpha *= .6;
      ctx.fillRect(u - 3.4, v - .38, 6.8, .76);
      ctx.fillRect(u - .38, v - 3.4, .76, 6.8);
    }
  }
  ctx.restore();
}

export function mountGalaxySky(canvas, signal) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return {setScene() {}, setPaused() {}, moveGalaxy() {}};
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const tile = makeStarTile(ctx);
  const textures = new Map();
  let width = 0, height = 0, top = 0, slice = 0, dpr = 1;
  let from = new Map(), to = new Map(), progress = 1;
  let frame = 0, previous = null, time = 0;
  let paused = false, visible = true, measure = true;

  function layers() {
    if (progress === 1) return to;
    const t = 1 - (1 - progress) ** 3;
    const result = new Map();
    for (const key of new Set([...from.keys(), ...to.keys()])) {
      const old = from.get(key), next = to.get(key);
      const a = old || {...next, opacity: 0}, b = next || {...old, opacity: 0};
      const item = {...b};
      for (const prop of ['x', 'y', 'rx', 'ry', 'tilt', 'opacity']) item[prop] = mix(a[prop], b[prop], t);
      result.set(key, item);
    }
    return result;
  }

  // Keep the backing bitmap close to viewport size even when a catalog has
  // many rows. Coordinates on the atlas remain stable as the window scrolls.
  function sizeBitmap() {
    const viewport = Math.max(1, document.documentElement.clientHeight);
    const scroll = clamp(-canvas.parentElement.getBoundingClientRect().top, 0, height);
    slice = Math.min(height, viewport + 400);
    top = clamp(scroll - 200, 0, height - slice);
    dpr = Math.min(window.devicePixelRatio || 1, 1.75, Math.sqrt(4400000 / (width * slice)));
    const pixelsX = Math.max(1, Math.round(width * dpr));
    const pixelsY = Math.max(1, Math.round(slice * dpr));
    if (canvas.width !== pixelsX) canvas.width = pixelsX;
    if (canvas.height !== pixelsY) canvas.height = pixelsY;
    Object.assign(canvas.style, {top: top + 'px', width: width + 'px', height: slice + 'px'});
    measure = false;
  }

  function schedule() {
    if (!frame && !signal.aborted && !document.hidden && visible && width && height) {
      frame = requestAnimationFrame(paint);
    }
  }

  function paint(now) {
    frame = 0;
    if (signal.aborted || document.hidden || !visible) return;
    const elapsed = previous === null ? 0 : now - previous;
    if (elapsed > 0 && elapsed < 30 && !measure) { schedule(); return; }
    previous = now;
    const dt = Math.min(.07, elapsed / 1000);
    if (!paused && !reduced.matches) time += dt;
    progress = reduced.matches || paused ? 1 : Math.min(1, progress + dt / .58);
    if (progress === 1) from.clear();
    if (measure) sizeBitmap();
    ctx.setTransform(dpr, 0, 0, dpr, 0, -top * dpr);
    ctx.clearRect(0, top, width, slice);
    ctx.fillStyle = tile;
    ctx.fillRect(0, top, width, slice);
    const seen = new Set();
    for (const galaxy of layers().values()) {
      if (galaxy.opacity <= 0 || galaxy.rx <= 0 || galaxy.ry <= 0) continue;
      const tilt = galaxy.tilt * Math.PI / 180;
      const extent = Math.hypot(galaxy.rx * Math.sin(tilt), galaxy.ry * Math.cos(tilt));
      if (galaxy.y + extent < top || galaxy.y - extent > top + slice) continue;
      const key = galaxy.id + ':' + galaxy.hue;
      if (!textures.has(key)) textures.set(key, makeGalaxy(galaxy.id, galaxy.hue));
      seen.add(key);
      drawGalaxy(ctx, textures.get(key), galaxy, time);
    }
    for (const key of textures.keys()) {
      if (textures.size > 5 && !seen.has(key)) textures.delete(key);
    }
    if (!paused && !reduced.matches && (seen.size || progress < 1)) schedule();
    else previous = null;
  }

  function refresh() {
    measure = true;
    previous = null;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    schedule();
  }
  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      refresh();
    }) : null;
  observer?.observe(canvas.parentElement);
  window.addEventListener('scroll', () => {
    if (slice < height) { measure = true; schedule(); }
  }, {capture: true, passive: true, signal});
  window.addEventListener('resize', refresh, {signal});
  document.addEventListener('visibilitychange', refresh, {signal});
  reduced.addEventListener('change', refresh, {signal});
  signal.addEventListener('abort', () => {
    observer?.disconnect();
    cancelAnimationFrame(frame);
    textures.clear();
  }, {once: true});
  return {
    setScene(scene) {
      if (width === scene.width && height === scene.height && to.size === scene.galaxies.length
        && scene.galaxies.every(item => {
          const last = to.get(item.id);
          return last && last.x === item.x && last.y === item.y && last.rx === item.rx && last.ry === item.ry
            && last.hue === item.hue && last.tilt === item.tilt;
        })) return;
      from = new Map(layers());
      width = scene.width;
      height = scene.height;
      to = new Map(scene.galaxies.map(item => [item.id, {...item, opacity: 1}]));
      progress = from.size ? 0 : 1;
      refresh();
    },
    moveGalaxy(id, x, y) {
      for (const scene of [from, to]) {
        const disk = scene.get(id);
        if (disk) Object.assign(disk, {x, y});
      }
      schedule();
    },
    setPaused(value) { paused = value; refresh(); }
  };
}
