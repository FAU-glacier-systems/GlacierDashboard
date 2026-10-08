/* Full-window 3D glacier map (MapLibre GL).
   - terrain: static (DEM + model bedrock), served as terrarium tiles; never reloaded
   - ice: a custom WebGL layer draws every glacier in view as a 3D surface (bedrock + thickness) coloured by
     the chosen property; a year step fetches one small binary block for all glaciers in view
   - dots for the zoomed-out view
   Driven from Dash through window.Map3D.render(state, cfg). */
(function () {
  "use strict";

  const S = {
    map: null, loaded: false, cfg: null, state: null, appliedTheme: null, appliedVar: null, rgi: null,
    glaciers: new Map(),        // rgi -> GPU record
    visible: [],                // glaciers currently drawn
    frameReq: null,             // url of the frames request in flight
    frameShown: null,           // url of the frames currently drawn
    bedReq: new Set(),          // "stride|ids" bedrock requests in flight
    prefetch: new Map(),        // url -> Promise<ArrayBuffer>
    gl: null, prog: null, lut: null,
    firstFlyDone: false,
  };
  const origin = () => window.location.origin;
  const MAX_PITCH = 85;      // up to near the horizon, for the view from a summit
  const LIGHT = normalize([-0.55, 0.55, 0.65]);   // from the north-west, as on maps
  // The terrain under the ice is a raster resampled from the model bedrock; the coarser the terrain tiles (the
  // further away), the more it smooths narrow valleys upwards. Lift the ice by about a tenth of the terrain
  // tile resolution so it is not hidden there; invisible at that distance.
  const lift = (zoom) => Math.min(Math.max(0.1 * 40075016 / 256 / 2 ** zoom * Math.cos(46.5 * Math.PI / 180), 0.5), 150);

  // the atmosphere: a halo around the globe when zoomed out, a light haze at the horizon close up
  const ATMOSPHERE = ["interpolate", ["linear"], ["zoom"], 0, 1, 6, 0.8, 9, 0.4];
  const LOOK = {
    dark: {
      background: "#202020",
      hillshade: { "hillshade-exaggeration": 0.6, "hillshade-shadow-color": "#000", "hillshade-highlight-color": "#a8a8a8",
                   "hillshade-accent-color": "#1a1a1a" },
      sky: { "sky-color": "#0b1320", "horizon-color": "#2a3444", "fog-color": "#151515", "sky-horizon-blend": 0.6,
             "horizon-fog-blend": 0.6, "fog-ground-blend": 0.85, "atmosphere-blend": ATMOSPHERE },
    },
    light: {
      background: "#ececec",
      hillshade: { "hillshade-exaggeration": 0.5, "hillshade-shadow-color": "#6b6b6b", "hillshade-highlight-color": "#fff",
                   "hillshade-accent-color": "#8a8a8a" },
      sky: { "sky-color": "#bcd6ec", "horizon-color": "#eef3f7", "fog-color": "#f4f4f4", "sky-horizon-blend": 0.6,
             "horizon-fog-blend": 0.6, "fog-ground-blend": 0.85, "atmosphere-blend": ATMOSPHERE },
    },
  };

  function normalize(v) { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }

  // ---------------------------------------------------------------- level of detail
  // Target cell size on screen by zoom; each glacier skips cells (stride 1, 2, 4, 8) to get close to it,
  // so 25 m and 100 m model grids end up at a similar resolution. Coarse grids (Aletsch, 100 m) keep their full
  // resolution one step longer: they have little to spare.
  function targetCell(zoom) {
    return zoom >= 11.5 ? 25 : zoom >= 10.5 ? 50 : zoom >= 9.5 ? 100 : zoom >= 8.5 ? 200 : zoom >= 7.5 ? 400 : 800;
  }
  function strideFor(rgi, zoom) {
    const dx = S.cfg.meshes[rgi].dx, r = targetCell(zoom) / dx / (dx >= 100 ? 2 : 1);
    return r >= 8 ? 8 : r >= 4 ? 4 : r >= 2 ? 2 : 1;
  }
  const dims = (m, s) => [Math.ceil(m.nx / s), Math.ceil(m.ny / s)];

  // ---------------------------------------------------------------- WebGL ice layer
  // MapLibre's projection prelude comes first (args.shaderData): on the globe it provides projectToSphere, the
  // far-side clipping plane and the globe/flat transition. a_pos is relative to the glacier's origin (Mercator).
  const VS = `
    uniform float u_zscale; uniform float u_lift; uniform float u_zbias;
    attribute vec2 a_pos; attribute vec4 a_zn; attribute vec2 a_ti;
    varying vec3 v_n; varying float v_t; varying float v_ice; varying float v_side;
    void main() {
      v_n = a_zn.yzw; v_t = a_ti.x; v_ice = a_ti.y;
      float elev = a_zn.x + u_lift;                                // metres
    #ifdef GLOBE
      vec3 sphere = projectToSphere(a_pos);                        // origin in u_projection_tile_mercator_coords
      v_side = dot(sphere, u_projection_clipping_plane.xyz) + u_projection_clipping_plane.w;   // < 0: far side
      // MapLibre's projection for the terrain, so ice and ground get the same position and the same kind of depth
      // (on the globe: the distance from the clipping plane), also while the globe blends into the flat map
      gl_Position = interpolateProjection(a_pos, sphere, elev);
      // that depth spans the planet, coarse for thin ice on the ground: a small bias towards the camera
      gl_Position.z -= u_zbias * gl_Position.w;
    #else
      v_side = 1.0;
      gl_Position = u_projection_matrix * vec4(a_pos, elev * u_zscale, 1.0);
    #endif
    }`;
  const FS = `
    precision mediump float;
    uniform sampler2D u_lut; uniform vec3 u_light;
    varying vec3 v_n; varying float v_t; varying float v_ice; varying float v_side;
    void main() {
      if (v_ice < 0.4 || v_side < 0.0) discard;
      vec3 c = v_t < -0.5 ? vec3(0.6) : texture2D(u_lut, vec2(clamp(v_t, 0.0, 1.0) * 0.99609375 + 0.001953125, 0.5)).rgb;
      float l = 0.5 + 0.6 * max(dot(normalize(v_n), u_light), 0.0);
      gl_FragColor = vec4(c * l, 1.0);
    }`;

  function compile(gl, shaderData) {
    const sh = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    const vs = `precision highp float;\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${VS}`;
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return {
      p, a_pos: gl.getAttribLocation(p, "a_pos"), a_zn: gl.getAttribLocation(p, "a_zn"), a_ti: gl.getAttribLocation(p, "a_ti"),
      u_matrix: gl.getUniformLocation(p, "u_projection_matrix"), u_zscale: gl.getUniformLocation(p, "u_zscale"),
      u_tile: gl.getUniformLocation(p, "u_projection_tile_mercator_coords"),
      u_clip: gl.getUniformLocation(p, "u_projection_clipping_plane"),
      u_transition: gl.getUniformLocation(p, "u_projection_transition"),
      u_fallback: gl.getUniformLocation(p, "u_projection_fallback_matrix"),
      u_lift: gl.getUniformLocation(p, "u_lift"), u_zbias: gl.getUniformLocation(p, "u_zbias"),
      u_lut: gl.getUniformLocation(p, "u_lut"), u_light: gl.getUniformLocation(p, "u_light"),
    };
  }

  function setLut(variable) {
    const gl = S.gl, lut = S.cfg.vars[variable].lut, px = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) { px[i * 4] = lut[i * 3]; px[i * 4 + 1] = lut[i * 3 + 1]; px[i * 4 + 2] = lut[i * 3 + 2]; px[i * 4 + 3] = 255; }
    gl.bindTexture(gl.TEXTURE_2D, S.lut);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, px);
  }

  const iceLayer = {
    id: "ice", type: "custom", renderingMode: "3d",
    onAdd(map, gl) {
      S.gl = gl;
      if (!(window.WebGL2RenderingContext && gl instanceof WebGL2RenderingContext)) gl.getExtension("OES_element_index_uint");
      S.progs = {};                     // one program per projection variant ("mercator", "globe")
      S.lut = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, S.lut);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      setLut("thk");
    },
    render(gl, args) {
      if (!S.visible.length) return;
      const sd = args.shaderData, pd = args.defaultProjectionData, M = pd.mainMatrix;
      const P = S.progs[sd.variantName] || (S.progs[sd.variantName] = compile(gl, sd));
      const globe = sd.variantName === "globe", F = pd.fallbackMatrix;
      // Between zoom 9 and 10 (see the style's projection) MapLibre blends the globe into the flat map, but tells
      // custom layers the blend is complete (pd.projectionTransition is always 1). The terrain follows the real
      // blend, so take it from the style: drawn on the pure globe, the ice would float above the ground away from
      // the centre of the view.
      const blend = globe ? S.map.style.projection.transitionState : 0;
      // the whole-Alps overview is nearly top-down and its terrain very coarse: draw the ice on top there
      // (MapLibre restores its own depth state after a custom layer)
      if (S.map.getZoom() < 8) gl.disable(gl.DEPTH_TEST);
      if (gl.bindVertexArray) gl.bindVertexArray(null);
      gl.useProgram(P.p);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, S.lut);
      gl.uniform1i(P.u_lut, 0);
      gl.uniform3fv(P.u_light, LIGHT);
      gl.uniform1f(P.u_lift, lift(S.map.getZoom()));
      if (globe) {
        gl.uniform1f(P.u_zbias, 1e-5 * blend);
        gl.uniformMatrix4fv(P.u_matrix, false, M);                 // positions on the unit sphere
        gl.uniform4fv(P.u_clip, pd.clippingPlane);
        gl.uniform1f(P.u_transition, blend);
      }
      gl.enableVertexAttribArray(P.a_pos); gl.enableVertexAttribArray(P.a_zn); gl.enableVertexAttribArray(P.a_ti);
      const m = new Float32Array(16);
      for (const G of S.visible) {
        if (!G.ready) continue;
        if (globe) {
          gl.uniform4f(P.u_tile, G.ox, G.oy, 1, 1);
          // flat map = F * translate(origin) * scale(1, 1, zscale): F takes Mercator units (also for the height),
          // a_pos is relative to the origin and the elevation is in metres
          for (let i = 0; i < 4; i++) {
            m[i] = F[i]; m[4 + i] = F[4 + i]; m[8 + i] = F[8 + i] * G.zscale;
            m[12 + i] = F[i] * G.ox + F[4 + i] * G.oy + F[12 + i];
          }
          gl.uniformMatrix4fv(P.u_fallback, false, m);
        } else {
          // M * translate(origin), in double precision before the cast
          for (let i = 0; i < 12; i++) m[i] = M[i];
          for (let i = 0; i < 4; i++) m[12 + i] = M[i] * G.ox + M[4 + i] * G.oy + M[12 + i];
          gl.uniformMatrix4fv(P.u_matrix, false, m);
        }
        gl.uniform1f(P.u_zscale, G.zscale);
        gl.bindBuffer(gl.ARRAY_BUFFER, G.posBuf);
        gl.vertexAttribPointer(P.a_pos, 2, gl.FLOAT, false, 8, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, G.dynBuf);
        gl.vertexAttribPointer(P.a_zn, 4, gl.FLOAT, false, 24, 0);
        gl.vertexAttribPointer(P.a_ti, 2, gl.FLOAT, false, 24, 16);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, G.idxBuf);
        gl.drawElements(gl.TRIANGLES, G.count, gl.UNSIGNED_INT, 0);
      }
      gl.disableVertexAttribArray(P.a_pos); gl.disableVertexAttribArray(P.a_zn); gl.disableVertexAttribArray(P.a_ti);
    },
  };

  // ---------------------------------------------------------------- per-glacier GPU records
  function makeRecord(rgi, s, bed) {
    const gl = S.gl, m = S.cfg.meshes[rgi], [nx, ny] = dims(m, s), n = nx * ny;
    const C = m.corners.map((c) => maplibregl.MercatorCoordinate.fromLngLat(c, 0));   // TL, TR, BR, BL
    const ox = C[0].x, oy = C[0].y;
    const pos = new Float32Array(n * 2);
    for (let j = 0; j < ny; j++) {
      const v = Math.min(j * s, m.ny - 1) / (m.ny - 1);
      for (let i = 0; i < nx; i++) {
        const u = Math.min(i * s, m.nx - 1) / (m.nx - 1), k = (j * nx + i) * 2;
        const a = (1 - u) * (1 - v), b = u * (1 - v), c = u * v, d = (1 - u) * v;
        pos[k] = a * C[0].x + b * C[1].x + c * C[2].x + d * C[3].x - ox;
        pos[k + 1] = a * C[0].y + b * C[1].y + c * C[2].y + d * C[3].y - oy;
      }
    }
    const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let q = 0;
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const k = j * nx + i;
      idx[q++] = k; idx[q++] = k + nx; idx[q++] = k + 1;
      idx[q++] = k + 1; idx[q++] = k + nx; idx[q++] = k + nx + 1;
    }
    const G = {
      rgi, s, nx, ny, n, ox, oy, bed, ready: false, corners: C,
      zscale: C[0].meterInMercatorCoordinateUnits(), h: m.dx * s,
      dyn: new Float32Array(n * 6), count: idx.length,
      posBuf: gl.createBuffer(), idxBuf: gl.createBuffer(), dynBuf: gl.createBuffer(),
    };
    gl.bindBuffer(gl.ARRAY_BUFFER, G.posBuf); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, G.idxBuf); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    return G;
  }

  function freeRecord(G) {
    const gl = S.gl;
    gl.deleteBuffer(G.posBuf); gl.deleteBuffer(G.idxBuf); gl.deleteBuffer(G.dynBuf);
  }

  // surface, normal, colour value and ice edge per vertex for one year
  function applyFrame(G, thk, prop, variable) {
    const v = S.cfg.vars[variable], span = v.hi - v.lo, { nx, ny, bed, dyn, h } = G;
    const z = new Float32Array(G.n), ice = new Float32Array(G.n);
    for (let k = 0; k < G.n; k++) { z[k] = bed[k] + thk[k] * 0.1; ice[k] = thk[k] >= 5 && Number.isFinite(z[k]) ? 1 : 0; }
    const vals = new Float32Array(G.n);
    for (let k = 0; k < G.n; k++) {
      vals[k] = variable === "thk" ? (thk[k] * 0.1 - v.lo) / span
        : prop[k] === 255 ? -1 : (v.offset + prop[k] * v.scale - v.lo) / span;
    }
    for (let j = 0; j < ny; j++) {
      const jn = Math.max(j - 1, 0), js = Math.min(j + 1, ny - 1);
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i, iw = Math.max(i - 1, 0), ie = Math.min(i + 1, nx - 1);
        const ok = Number.isFinite(z[k]);
        // where the ice ends: half ice-or-not, half that smoothed over the 3x3 neighbours (1-2-1 weights); the
        // shader cuts at 0.4, so the outline runs between the cells with rounded corners instead of stair steps
        const nb = 4 * ice[k] + 2 * (ice[j * nx + iw] + ice[j * nx + ie] + ice[jn * nx + i] + ice[js * nx + i])
                 + ice[jn * nx + iw] + ice[jn * nx + ie] + ice[js * nx + iw] + ice[js * nx + ie];
        const edge = 0.5 * ice[k] + nb / 32;
        let val = vals[k];
        if (val < -0.5) {                // no value (just off the ice): the neighbours' colour, so the edge is not grey
          let sum = 0, cnt = 0;
          for (const q of [j * nx + iw, j * nx + ie, jn * nx + i, js * nx + i]) if (vals[q] >= -0.5) { sum += vals[q]; cnt++; }
          if (cnt) val = sum / cnt;
        }
        let dzdx = (z[j * nx + ie] - z[j * nx + iw]) / ((ie - iw) * h);
        let dzdn = (z[jn * nx + i] - z[js * nx + i]) / ((js - jn) * h);
        if (!Number.isFinite(dzdx)) dzdx = 0;
        if (!Number.isFinite(dzdn)) dzdn = 0;
        const nz = 1 / Math.hypot(dzdx, dzdn, 1), o = k * 6;
        dyn[o] = ok ? z[k] : 0;
        dyn[o + 1] = -dzdx * nz; dyn[o + 2] = -dzdn * nz; dyn[o + 3] = nz;
        dyn[o + 4] = val;
        dyn[o + 5] = ok ? edge : 0;
      }
    }
    G.thk = thk; G.prop = prop; G.variable = variable;   // kept for the value under the cursor
    const gl = S.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, G.dynBuf);
    gl.bufferData(gl.ARRAY_BUFFER, dyn, gl.DYNAMIC_DRAW);
    G.ready = true;
  }

  // ---------------------------------------------------------------- data loading
  function fetchBuf(url) {
    if (S.prefetch.has(url)) { const p = S.prefetch.get(url); S.prefetch.delete(url); return p; }
    return fetch(url).then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); });
  }

  const idList = (items) => items.map(([r, s]) => `${S.cfg.meshes[r].k}:${s}`).join(",");
  function framesUrl(items, st, year) {
    return `${origin()}/api3d/frames?scenario=${st.scenario}&var=${st.variable}&year=${year}&ids=${idList(items)}`;
  }

  function wantedGlaciers() {
    const map = S.map, zoom = map.getZoom();
    const b = map.getBounds(), c = map.getCenter();
    const out = [];
    for (const [rgi, m] of Object.entries(S.cfg.meshes)) {
      const [w, s, e, n] = m.bbox;
      if (e < b.getWest() || w > b.getEast() || n < b.getSouth() || s > b.getNorth()) continue;
      out.push([(w + e) / 2 - c.lng, (s + n) / 2 - c.lat, rgi]);
    }
    out.sort((p, q) => (p[0] ** 2 + p[1] ** 2) - (q[0] ** 2 + q[1] ** 2));
    return out.slice(0, zoom < 9.5 ? 400 : zoom < 11.5 ? 150 : 70).map((p) => p[2]).sort();
  }

  // bring the drawn glaciers in line with the view and the state (scenario, property, year)
  function sync() {
    if (!S.loaded || !S.state) return;
    const st = S.state, zoom = S.map.getZoom(), ids = wantedGlaciers();
    const want = ids.map((r) => [r, strideFor(r, zoom)]);

    const missing = want.filter(([r, s]) => { const G = S.glaciers.get(r); return !G || G.s !== s; });
    const bedKey = idList(missing);
    if (missing.length && !S.bedReq.has(bedKey)) {
      S.bedReq.add(bedKey);
      fetchBuf(`${origin()}/api3d/beds?ids=${bedKey}`).then((buf) => {
        let off = 0;
        for (const [r, s] of missing) {
          const [nx, ny] = dims(S.cfg.meshes[r], s), n = nx * ny;
          const bed = new Float32Array(buf, off, n); off += n * 4;
          const old = S.glaciers.get(r);
          if (old) freeRecord(old);
          S.glaciers.set(r, makeRecord(r, s, bed));
        }
      }).catch(() => {}).finally(() => { S.bedReq.delete(bedKey); S.frameShown = null; sync(); });
    }

    // draw what is loaded (at its current level of detail) until the rest arrives
    S.visible = ids.map((r) => S.glaciers.get(r)).filter(Boolean);
    S.map.triggerRepaint();
    const ready = want.filter(([r, s]) => { const G = S.glaciers.get(r); return G && G.s === s; });
    if (!ready.length) { S.frameShown = null; return; }

    const url = framesUrl(ready, st, st.year);
    if (url === S.frameShown || url === S.frameReq) return;
    S.frameReq = url;
    fetchBuf(url).then((buf) => {
      if (S.frameReq !== url) return;          // a newer request took over
      let off = 0;
      for (const [r, s] of ready) {
        const G = S.glaciers.get(r);
        const [nx, ny] = dims(S.cfg.meshes[r], s), n = nx * ny;
        const thk = new Uint16Array(buf, off, n); off += n * 2;
        let prop = null;
        if (st.variable !== "thk") { prop = new Uint8Array(buf, off, n); off += n + (n % 2); }
        if (G && G.s === s) applyFrame(G, thk, prop, st.variable);
      }
      S.frameShown = url;
      if (S.hover) showHover();             // the label follows the shown year
      S.map.triggerRepaint();
      if (st.playing) {                       // read ahead: the next year of the same glaciers
        const next = st.year >= S.cfg.years[1] ? S.cfg.years[0] : st.year + 1;
        const nu = framesUrl(ready, st, next);
        if (!S.prefetch.has(nu)) S.prefetch.set(nu, fetch(nu).then((r) => r.arrayBuffer()));
        while (S.prefetch.size > 4) S.prefetch.delete(S.prefetch.keys().next().value);
      }
    }).catch(() => {}).finally(() => {
      if (S.frameReq === url) S.frameReq = null;
    });

    // free GPU memory of glaciers far away
    if (S.glaciers.size > 250) {
      const keep = new Set(ids);
      for (const [r, G] of S.glaciers) {
        if (keep.has(r)) continue;
        freeRecord(G); S.glaciers.delete(r);
        if (S.glaciers.size <= 180) break;
      }
    }
  }

  // ---------------------------------------------------------------- camera in the address bar
  // ?view=lon,lat,zoom,bearing,pitch, so a shared link opens the same view; ?peak=<id> while standing on a
  // summit (the link then stands there, facing as in view)
  function viewFromUrl() {
    const v = (new URLSearchParams(window.location.search).get("view") || "").split(",").map(Number);
    if (v.length !== 5 || v.some((x) => !Number.isFinite(x))) return null;
    const [lng, lat, zoom, bearing, pitch] = v;
    if (Math.abs(lng) > 180 || Math.abs(lat) > 85 || zoom < 0 || zoom > 22) return null;
    return { center: [lng, lat], zoom, bearing, pitch: Math.min(Math.max(pitch, 0), MAX_PITCH) };
  }

  function viewToUrl() {
    const m = S.map, c = m.getCenter();
    const v = [c.lng.toFixed(4), c.lat.toFixed(4), m.getZoom().toFixed(2), m.getBearing().toFixed(0), m.getPitch().toFixed(0)];
    const p = new URLSearchParams(window.location.search);
    p.set("view", v.join(","));
    if (standId()) p.set("peak", standId()); else p.delete("peak");
    window.history.replaceState(window.history.state, "", window.location.pathname + "?" + p.toString());
  }

  // the glacier the link was made for (null = all glaciers); its view comes from the link, so no flight to it
  function urlGlacier() {
    const g = new URLSearchParams(window.location.search).get("glacier");
    return g === "all" ? null : g;
  }

  // ---------------------------------------------------------------- first-visit hint
  const INTRO_KEY = "glacier3d-intro-closed";
  function showIntro() {
    const el = document.getElementById("intro");
    let closed = false;
    try { closed = localStorage.getItem(INTRO_KEY) === "1"; } catch (e) { /* storage blocked: show it */ }
    if (el && !closed) el.classList.remove("is-hidden");
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest("#intro_close")) return;
    const el = document.getElementById("intro");
    if (el) el.classList.add("is-hidden");
    try { localStorage.setItem(INTRO_KEY, "1"); } catch (err) { /* only remembered where storage works */ }
  });

  // ---------------------------------------------------------------- map
  function init(cfg) {
    const el = document.getElementById("map3d");
    if (!el || !window.maplibregl) return false;
    S.cfg = cfg;
    document.documentElement.lang = cfg.lang;
    S.urlView = viewFromUrl();
    S.names = new Map(cfg.glaciers.features.map((f) => [f.properties.rgi, f.properties.name]));
    S.centres = cfg.glaciers.features.map((f) => [f.properties.rgi, f.geometry.coordinates]);
    const map = new maplibregl.Map({
      container: el,
      // no paint transitions: the terrain caches draped layers, a half-finished fade would stay visible
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#202020" } }],
               // globe when zoomed out, flat map from zoom 10 on (MapLibre's "globe" switches at 11..12, too early)
               projection: { type: ["interpolate", ["linear"], ["zoom"], 9, "vertical-perspective", 10, "mercator"] },
               transition: { duration: 0, delay: 0 } },
      ...(S.urlView || { bounds: cfg.alps_bounds, fitBoundsOptions: { padding: 30 } }),
      maxPitch: MAX_PITCH,
      attributionControl: false,
      locale: { "NavigationControl.ResetBearing": cfg.t.compass },   // the compass tooltip, in the page's language
    });
    S.map = map;
    S.fov0 = map.getVerticalFieldOfView();     // MapLibre's default; a summit view is wider (STAND_FOV)
    S.urlPeak = new URLSearchParams(window.location.search).get("peak");   // a link that stands on a summit
    // such a link keeps its view while the page loads (the selection can arrive in steps, which would otherwise
    // count as a new glacier and fly there), until the visitor first does something
    S.holdView = !!S.urlPeak;
    for (const ev of ["pointerdown", "keydown", "wheel"])
      document.addEventListener(ev, () => { S.holdView = false; }, { capture: true, once: true });
    lookAround(map.getCanvasContainer());
    window._map3d = map;   // handy for debugging in the console
    map.addControl(themeControl(), "top-right");   // above the zoom buttons
    map.addControl(new maplibregl.NavigationControl({ showZoom: false, visualizePitch: true }), "top-right");   // compass only
    compassToggle(map);
    trackPanelHeight();

    map.on("load", () => {
      const dem = { type: "raster-dem", tiles: [origin() + cfg.terrain_url], tileSize: 256,
                    encoding: "terrarium", maxzoom: cfg.dem_maxzoom };
      map.addSource("dem", dem);   // its sources are credited in the Impressum ("Daten und Quellen")
      map.addSource("dem-hs", dem);   // hillshade wants its own source
      map.setTerrain({ source: "dem", exaggeration: 1.0 });
      map.addLayer({ id: "hillshade", type: "hillshade", source: "dem-hs" });
      map.addLayer(iceLayer);

      // glacier label on hover, selection on click (no markers: the ice itself shows where glaciers are)
      S.popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "glacier-tip",
                                       maxWidth: "340px" });
      let hoverFrame = 0;
      map.on("mousemove", (e) => {
        if (hoverFrame) return;
        hoverFrame = requestAnimationFrame(() => {
          hoverFrame = 0;
          const onLabel = e.originalEvent.target.closest && e.originalEvent.target.closest(".peak-label");
          const rgi = onLabel ? null : pickGlacier(e);
          map.getCanvas().style.cursor = rgi && !standId() ? "pointer" : "";   // on a summit glaciers are not picked
          S.hover = rgi ? { rgi, lngLat: e.lngLat } : null;
          showHover();
        });
      });
      map.on("mouseout", () => { S.hover = null; showHover(); });
      map.on("click", (e) => {
        if (e.originalEvent.target.closest && e.originalEvent.target.closest(".peak-label")) return;
        if (standId()) return;   // on a summit a click on a glacier does not select it (the hover label still shows it)
        const rgi = pickGlacier(e);
        if (rgi && window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("rgi_select", { data: { rgi, t: Date.now() } });
        }
      });
      map.on("moveend", () => {
        if (S.flight || S.quiet) return;    // summit flight or turning: settled() catches up when it rests
        sync(); viewToUrl();
        if (map.getZoom() >= PEAK_ZOOM) loadPeaks();
        updatePeakLabels();
      });

      S.loaded = true;
      if (S.urlPeak) loadPeaks();
      map.once("idle", updatePeakLabels);      // the city labels, before any move
      showIntro();
      apply();
    });
    return true;
  }

  // ---------------------------------------------------------------- hover label
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // name (the RGI ID for unnamed glaciers) and the shown property (thickness if nothing else) under the cursor
  function showHover() {
    const h = S.hover, popup = S.popup;
    if (!h) { popup.remove(); return; }
    const st = S.state || {}, name = S.names.get(h.rgi) || h.rgi;
    let html = `<div class="tip-name">${esc(name)}</div>`;
    const here = valueAt(h.rgi, h.lngLat);
    if (st.variable === "thk") {
      if (here && here.value > 0) html += `<div class="tip-here">${esc(S.cfg.t.thickness)} <b>${Math.round(here.value)} m</b></div>`;
    } else if (here && here.value != null) {
      const label = S.cfg.vars[here.variable].label, m = /^(.*?)\s*\((.*)\)$/.exec(label);
      const what = m ? m[1] : label, unit = m ? m[2] : "";
      const v = (Math.abs(here.value) >= 10 ? here.value.toFixed(0) : here.value.toFixed(1)).replace(".", S.cfg.t.decimal);
      html += `<div class="tip-here">${esc(what)} <b>${v} ${esc(unit)}</b></div>`;
    }
    popup.setLngLat(h.lngLat).setHTML(html).addTo(S.map);
  }

  // the drawn ice and property value of a glacier at a map position: the grid cell from the grid's corners
  function valueAt(rgi, lngLat) {
    const G = S.glaciers && S.glaciers.get(rgi), m = S.cfg.meshes[rgi];
    if (!G || !G.ready || !G.thk) return null;
    const P = maplibregl.MercatorCoordinate.fromLngLat(lngLat, 0), [C0, C1, , C3] = G.corners;
    const ex = C1.x - C0.x, ey = C1.y - C0.y, fx = C3.x - C0.x, fy = C3.y - C0.y, det = ex * fy - ey * fx;
    const dx = P.x - C0.x, dy = P.y - C0.y, u = (dx * fy - dy * fx) / det, v = (ex * dy - ey * dx) / det;
    if (!(u >= 0 && u <= 1 && v >= 0 && v <= 1)) return null;
    const i = Math.min(Math.round(u * (m.nx - 1) / G.s), G.nx - 1), j = Math.min(Math.round(v * (m.ny - 1) / G.s), G.ny - 1);
    const k = j * G.nx + i, t = G.thk[k] * 0.1;
    if (!(t > 0)) return { variable: G.variable, value: null };
    if (G.variable === "thk" || !G.prop) return { variable: "thk", value: t };
    const q = G.prop[k], c = S.cfg.vars[G.variable];
    return { variable: G.variable, value: q === 255 ? null : c.offset + q * c.scale };
  }

  // the glacier under the pointer: the nearest centre within a few pixels, else (zoomed in) the glacier
  // whose footprint contains the point, nearest centre first
  function pickGlacier(e) {
    const map = S.map;
    let best = null, bestD = 14 * 14;
    for (const [rgi, c] of S.centres) {
      const p = map.project(c), d = (p.x - e.point.x) ** 2 + (p.y - e.point.y) ** 2;
      if (d < bestD) { bestD = d; best = rgi; }
    }
    if (best || map.getZoom() < 10) return best;
    const { lng, lat } = e.lngLat;
    bestD = Infinity;
    for (const [rgi, m] of Object.entries(S.cfg.meshes)) {
      const b = m.bbox;
      if (lng < b[0] || lng > b[2] || lat < b[1] || lat > b[3]) continue;
      const d = ((b[0] + b[2]) / 2 - lng) ** 2 + ((b[1] + b[3]) / 2 - lat) ** 2;
      if (d < bestD) { bestD = d; best = rgi; }
    }
    return best;
  }

  function setTheme(theme) {
    const map = S.map, look = LOOK[theme];
    map.setPaintProperty("bg", "background-color", look.background);
    Object.entries(look.hillshade).forEach(([k, v]) => map.setPaintProperty("hillshade", k, v));
    if (map.setSky) map.setSky(look.sky);
  }

  // With 3D terrain MapLibre caches the draped layers per terrain tile, and paint/filter changes do not
  // invalidate that cache. Re-setting the terrain after the next frame redraws it.
  function refreshDrape() {
    const map = S.map;
    map.once("render", () => map.setTerrain(map.getTerrain()));
    map.triggerRepaint();
  }

  // the top panel's height as a CSS variable: on phones the map buttons sit below the full-width panel
  function trackPanelHeight() {
    const panel = document.querySelector(".title-card");
    if (!panel || !window.ResizeObserver) return;
    const set = () => document.documentElement.style.setProperty("--panel-bottom", panel.getBoundingClientRect().bottom + "px");
    new ResizeObserver(set).observe(panel);
    window.addEventListener("resize", set);
    set();
  }

  // light/dark switch as a map control above the zoom buttons; it presses the (hidden) Dash button, which owns
  // the theme
  function themeControl() {
    let box;
    return {
      onAdd() {
        box = document.createElement("div");
        box.className = "maplibregl-ctrl maplibregl-ctrl-group theme-ctrl";
        const btn = document.createElement("button");
        btn.type = "button"; btn.title = S.cfg.t.theme; btn.setAttribute("aria-label", btn.title);
        const sync = () => { btn.textContent = document.documentElement.dataset.theme === "light" ? "☾" : "☀"; };
        btn.addEventListener("click", () => { const b = document.getElementById("theme_toggle"); if (b) b.click(); });
        new MutationObserver(sync).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
        sync();
        box.appendChild(btn);
        return box;
      },
      onRemove() { box.remove(); },
    };
  }

  // the compass turns the map to north and flat; pressed again before the camera moves elsewhere, it turns back to
  // the bearing and tilt from before. Runs ahead of MapLibre's own click handler (capture phase) so it can stop it.
  function compassToggle(map) {
    let before = null, after = null;
    const same = (a, b) => Math.abs(a.zoom - b.zoom) < 0.01 && Math.abs(a.center.lng - b.center.lng) < 1e-6
      && Math.abs(a.center.lat - b.center.lat) < 1e-6 && Math.abs(a.bearing - b.bearing) < 0.5 && Math.abs(a.pitch - b.pitch) < 0.5;
    const cam = () => ({ center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
    map.getContainer().addEventListener("click", (e) => {
      if (!e.target.closest || !e.target.closest(".maplibregl-ctrl-compass")) return;
      if (S.stand || S.flight) {                           // on a summit: turn the view, the camera stays
        e.stopPropagation();
        if (S.stand) turnStand(0, 90);
        return;
      }
      if (before && (!after || same(cam(), after))) {      // also while the reset is still turning
        e.stopPropagation();
        map.easeTo({ bearing: before.bearing, pitch: before.pitch, duration: 1000 });
        before = after = null;
        return;
      }
      const now = cam();
      if (Math.abs(now.bearing) < 0.5 && now.pitch < 0.5) { before = after = null; return; }   // already north and flat
      before = now; after = null;
      map.once("moveend", () => { after = cam(); });     // where the reset ends: a second press only counts there
    }, true);
  }

  // room around a glacier for what floats over the map: the top panel (beside it on wide screens, above it on
  // narrow ones) and the dock at the bottom
  function framePadding() {
    const W = window.innerWidth, H = window.innerHeight, pad = { top: 40, bottom: 40, left: 40, right: 40 };
    const panel = document.querySelector(".title-card"), dock = document.querySelector(".dock");
    if (panel) {
      const r = panel.getBoundingClientRect();
      if (r.right < W * 0.45) pad.left = r.right + 20;
      else pad.top = r.bottom + 20;
    }
    if (dock) pad.bottom = H - dock.getBoundingClientRect().top + 20;
    const free = H - pad.top - pad.bottom;               // always leave the glacier some room
    if (free < H * 0.3) { const k = (H * 0.7) / (pad.top + pad.bottom); pad.top *= k; pad.bottom *= k; }
    return pad;
  }

  // look uphill at a glacier, i.e. from the side it faces, so the mountain behind it cannot hide it; the turn
  // goes the short way round from the current bearing
  function facingBearing(aspect) {
    const now = S.map.getBearing();
    if (aspect == null) return now;
    let b = (aspect + 180) % 360;
    while (b - now > 180) b -= 360;
    while (b - now < -180) b += 360;
    return b;
  }

  function flyToGlacier(rgi, duration) {
    const m = S.cfg.meshes[rgi];
    if (!m) return;
    leaveStand();
    const b = m.bbox;
    const cam = S.map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding: framePadding() });
    if (!cam) return;
    S.map.flyTo({ center: cam.center, zoom: Math.min(cam.zoom + 0.3, 12.2), pitch: 60,
                  bearing: facingBearing(m.aspect), duration, essential: true });
  }

  // ---------------------------------------------------------------- standing on a summit
  // A peak from the search or a label: the camera stands just above the summit with a wide view, first looking at
  // ice of a glacier below (see tools/build_peaks.py). There it stays: dragging (mouse, one finger) turns the
  // view, the wheel or a pinch narrows or widens it, the compass turns it north and level. The search's ×, the
  // title, a glacier or another peak leave. MapLibre's own flights take the height of the destination from the
  // terrain loaded when they start (still coarse there, often far too low) and keep the camera where they end,
  // so it could end inside the mountain: this flight sets the camera's position itself in every frame, and the
  // map's centre is not clamped to the ground while flying or standing.
  const M_PER_DEG = 111320, STAND_FOV = 60, FOV_RANGE = [20, 100], STAND_PITCH = 100;   // up to 10° above level
  const CONTROLS = ["dragPan", "dragRotate", "scrollZoom", "touchZoomRotate", "touchPitch", "doubleClickZoom",
                    "keyboard", "boxZoom"];
  const clamp = (x, a, b) => Math.min(Math.max(x, a), b);

  // camera options that put the camera at lng, lat, alt (m) looking along bearing and pitch: aimed at the point
  // where that line reaches the height ground (m), so nothing depends on terrain that has not loaded yet and the
  // map's centre and zoom stay sensible (at least 0.5 km away, at most 300 km when the line runs almost level)
  function cameraAt(lng, lat, alt, bearing, pitch, ground) {
    const b = bearing * Math.PI / 180, p = pitch * Math.PI / 180;
    const level = Math.cos(p) < 0.05, along = (alt - ground) / Math.max(Math.cos(p), 1e-3);
    const D = Math.max(level ? Math.min(along, 300000) : along, 500), h = D * Math.sin(p);
    const to = new maplibregl.LngLat(lng + h * Math.sin(b) / (M_PER_DEG * Math.cos(lat * Math.PI / 180)),
                                     lat + h * Math.cos(b) / M_PER_DEG);
    return S.map.calculateCameraOptionsFromTo(new maplibregl.LngLat(lng, lat), alt, to, alt - D * Math.cos(p));
  }

  function jumpCamera(o) {
    S.map.jumpTo({ center: o.center, zoom: o.zoom, bearing: o.bearing, pitch: o.pitch, elevation: o.elevation });
  }

  // where the camera is now (position, height in m, direction)
  function cameraNow() {
    const m = S.map, t = m.transform, lat = m.getCenter().lat, c = t.getCameraLngLat();
    const perMetre = t.worldSize / (40075016.686 * Math.cos(lat * Math.PI / 180));
    return { lng: c.lng, lat: c.lat, alt: t.elevation + t.cameraToCenterDistance * Math.cos(m.getPitch() * Math.PI / 180) / perMetre,
             bearing: m.getBearing(), pitch: m.getPitch() };
  }

  const standId = () => (S.stand && S.stand.id) || (S.flight && S.flight.id) || null;

  // the search field shows the peak like its result entry (clientside.py); null when leaving
  function peakSel(p) {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    window.dash_clientside.set_props("peak_sel", { data: p ? {
      id: p[0], name: p[1], sub: [S.cfg.t.peak, p[2] != null ? `${p[2]} m` : "", p[6]].filter((x) => x).join(" · ") } : null });
  }

  function flyToPeak(id, opts = {}) {
    const p = (S.peaks || []).find((e) => e[0] === id);
    if (!p || !S.map) return;
    const [cam, look] = [p[4], p[5]], map = S.map;
    if (S.flight) cancelAnimationFrame(S.flight.frame);
    const a = { ...cameraNow(), fov: map.getVerticalFieldOfView() }, g0 = map.transform.elevation;
    // the direction from the summit to the glacier point; a link's view (opts) keeps its own direction
    const dir = map.calculateCameraOptionsFromTo(new maplibregl.LngLat(cam[0], cam[1]), cam[2],
                                                 new maplibregl.LngLat(look[0], look[1]), look[2]);
    const b = { lng: cam[0], lat: cam[1], alt: cam[2], bearing: opts.bearing ?? dir.bearing,
                pitch: clamp(opts.pitch ?? dir.pitch, 0, STAND_PITCH), fov: STAND_FOV };
    const turn = ((b.bearing - a.bearing + 540) % 360) - 180;              // the short way round
    const km = Math.hypot((b.lng - a.lng) * Math.cos(b.lat * Math.PI / 180), b.lat - a.lat) * M_PER_DEG / 1000;
    const rise = Math.min(km * 250, 15000);                                  // up and over on longer flights
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const duration = reduced || opts.instant ? 0 : Math.min(2500 + km * 40, 6000), t0 = performance.now();
    const ease = (k) => k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
    map.stop();
    for (const c of CONTROLS) map[c].disable();
    map.getCanvasContainer().style.touchAction = "none";   // fingers turn the view (lookAround), not the page
    map.setCenterClampedToGround(false);
    map.setMaxPitch(STAND_PITCH);
    S.stand = null;
    S.flight = { id };
    peakSel(p);
    const step = () => {
      const k = duration ? Math.min((performance.now() - t0) / duration, 1) : 1, e = ease(k);
      map.setVerticalFieldOfView(a.fov + (b.fov - a.fov) * e);
      if (k < 1) {
        jumpCamera(cameraAt(a.lng + (b.lng - a.lng) * e, a.lat + (b.lat - a.lat) * e,
                            a.alt + (b.alt - a.alt) * e + rise * Math.sin(Math.PI * e),
                            a.bearing + turn * e, a.pitch + (b.pitch - a.pitch) * e, g0 + (look[2] - g0) * e));
        S.flight.frame = requestAnimationFrame(step);
        return;
      }
      S.flight = null;
      S.stand = { id, lng: b.lng, lat: b.lat, alt: b.alt, ground: look[2], bearing: b.bearing, pitch: b.pitch };
      standView(b.bearing, b.pitch);
      settled();
    };
    step();
  }

  // turn the view on the summit
  function standView(bearing, pitch) {
    const s = S.stand;
    s.bearing = ((bearing % 360) + 360) % 360;
    s.pitch = clamp(pitch, 0, STAND_PITCH);
    const t = S.map.transform;
    t.clearNearFarZOverride();                // MapLibre's far plane for this view...
    jumpCamera(cameraAt(s.lng, s.lat, s.alt, s.bearing, s.pitch, s.ground));
    // ...but a near one STAND_NEAR m ahead: MapLibre puts it at a fixed share of the distance to the map's centre
    // (tens of metres here), which cuts away the summit right in front of the camera, so one looks into the mountain
    const perMetre = t.worldSize / (40075016.686 * Math.cos(S.map.getCenter().lat * Math.PI / 180));
    t.overrideNearFarZ(STAND_NEAR * perMetre, t.farZ);
  }
  const STAND_NEAR = 5;

  function standFov(fov) {
    S.map.setVerticalFieldOfView(clamp(fov, FOV_RANGE[0], FOV_RANGE[1]));
    standView(S.stand.bearing, S.stand.pitch);
  }

  // after the camera came to rest: glaciers in view, address, labels (skipped while flying or turning)
  function settled() { S.quiet = false; sync(); viewToUrl(); updatePeakLabels(); }

  // end a flight or a stand: the normal field of view, tilt limit and controls again, the camera where it is
  function leaveStand() {
    if (S.flight) cancelAnimationFrame(S.flight.frame);
    if (!S.flight && !S.stand) return;
    const map = S.map, c = cameraNow(), ground = S.stand ? S.stand.ground : map.transform.elevation;
    S.flight = S.stand = null;
    map.transform.clearNearFarZOverride();
    map.setVerticalFieldOfView(S.fov0);
    jumpCamera(cameraAt(c.lng, c.lat, c.alt, c.bearing, Math.min(c.pitch, MAX_PITCH), ground));
    map.setMaxPitch(MAX_PITCH);
    map.setCenterClampedToGround(true);
    for (const k of CONTROLS) map[k].enable();
    map.getCanvasContainer().style.touchAction = "";
    peakSel(null);
    S.quiet = false;
  }

  // leave the summit (the search's ×, the title): back to the selected glacier, or to the whole Alps
  function leavePeak() {
    if (!S.stand && !S.flight) return;
    leaveStand();
    if (S.rgi) flyToGlacier(S.rgi, 2500); else flyToOverview();
  }

  // turning the view on a summit: drag with the mouse or one finger (the view follows the pointer), or the arrow
  // keys; zooming (wheel, pinch, + and -) changes the field of view, the camera stays. Clicks (on labels, glaciers) still go through: a drag starts after 3 px.
  function lookAround(container) {
    const ptrs = new Map();
    let pinch = null, dragging = false, wheelEnd = 0;
    const active = () => S.stand && !S.flight;
    const perPx = () => S.map.getVerticalFieldOfView() / S.map.getCanvas().clientHeight;   // degrees per pixel
    container.addEventListener("pointerdown", (e) => {
      if (!active() || (e.target.closest && e.target.closest(".peak-label, .maplibregl-ctrl"))) return;
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (ptrs.size === 2) {
        const [p, q] = [...ptrs.values()];
        pinch = { d: Math.hypot(p.x - q.x, p.y - q.y), fov: S.map.getVerticalFieldOfView() };
      }
    });
    window.addEventListener("pointermove", (e) => {
      const was = ptrs.get(e.pointerId);
      if (!was || !active()) return;
      if (ptrs.size === 1) {
        const dx = e.clientX - was.x, dy = e.clientY - was.y;
        if (!dragging && Math.hypot(dx, dy) < 3) return;
        dragging = S.quiet = true;
        ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
        standView(S.stand.bearing - dx * perPx(), S.stand.pitch + dy * perPx());
      } else if (ptrs.size === 2 && pinch) {
        ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const [p, q] = [...ptrs.values()];
        dragging = S.quiet = true;
        standFov(pinch.fov * pinch.d / Math.max(Math.hypot(p.x - q.x, p.y - q.y), 1));
      }
    });
    const up = (e) => {
      if (!ptrs.delete(e.pointerId)) return;
      if (ptrs.size < 2) pinch = null;
      if (!ptrs.size && dragging) { dragging = false; settled(); }
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    container.addEventListener("wheel", (e) => {
      if (!active()) return;
      e.preventDefault();
      S.quiet = true;
      standFov(S.map.getVerticalFieldOfView() * Math.exp(e.deltaY * 0.0015));
      clearTimeout(wheelEnd);
      wheelEnd = setTimeout(settled, 250);
    }, { passive: false });
    // keyboard: the arrow keys turn the view (with Shift in larger steps), + and - zoom (the field of view), as
    // MapLibre's keys pan and zoom elsewhere
    document.addEventListener("keydown", (e) => {
      if (!active() || e.target.closest && e.target.closest("input, textarea, button, [role=button]")) return;
      const step = e.shiftKey ? 15 : 5;
      const turn = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
      const f = { "+": 0.8, "=": 0.8, "-": 1.25, "_": 1.25 }[e.key];
      if (!turn && !f) return;
      e.preventDefault();
      S.quiet = true;
      if (turn) standView(S.stand.bearing + turn[0], S.stand.pitch + turn[1]);
      else standFov(S.map.getVerticalFieldOfView() * f);
      clearTimeout(wheelEnd);
      wheelEnd = setTimeout(settled, 250);
    });
  }

  // the compass on a summit: turn the view north and level (MapLibre's reset would move the camera)
  function turnStand(bearing, pitch) {
    const s = S.stand, b0 = s.bearing, p0 = s.pitch, t0 = performance.now();
    const turn = ((bearing - b0 + 540) % 360) - 180;
    S.quiet = true;
    const step = () => {
      if (!S.stand) return;
      const k = Math.min((performance.now() - t0) / 800, 1), e = k * k * (3 - 2 * k);
      standView(b0 + turn * e, p0 + (pitch - p0) * e);
      if (k < 1) requestAnimationFrame(step); else settled();
    };
    step();
  }

  // ---------------------------------------------------------------- peak labels
  // Name and height above the summits in view, with a dashed line down to the summit; a click stands on it.
  // Only the most prominent and high peaks (about a tenth, score > 0 from tools/build_peaks.py); the rest are in
  // the search. HTML markers: MapLibre puts them on the terrain and hides them behind mountains. Where labels
  // would overlap, the higher score wins. From zoom PEAK_ZOOM on, at most PEAK_MAX (fewer on phones).
  const PEAK_ZOOM = 10.5, PEAK_MAX = 20, STEM = 26;
  function peakMarker(p) {
    const el = document.createElement("div");
    el.className = "peak-label";
    el.title = S.cfg.t.peak_tip;
    el.innerHTML = `<div class="peak-text"><span class="peak-name">${esc(p[1])}</span>` +
                   (p[2] != null ? ` <span class="peak-ele">${p[2]} m</span>` : "") +
                   `</div><div class="peak-stem" style="height:${STEM}px"></div>`;
    el.addEventListener("click", () => pickPeak(p));
    return new maplibregl.Marker({ element: el, anchor: "bottom", opacityWhenCovered: "0" })
      .setLngLat([p[4][0], p[4][1]]);
  }

  function pickPeak(p) { flyToPeak(p[0]); }

  // city labels for orientation (config.CITIES): a dot and the name, at every zoom, not clickable; one behind a
  // mountain stays faint. They are placed before the peak labels, which keep clear of them; both keep clear of the
  // panels over the map.
  function cityMarker(c) {
    const el = document.createElement("div");
    el.className = "city-label";
    el.innerHTML = `<span class="city-name">${esc(c[0])}</span><span class="city-dot"></span>`;
    return new maplibregl.Marker({ element: el, anchor: "bottom", opacityWhenCovered: "0.35" }).setLngLat([c[1], c[2]]);
  }

  function updatePeakLabels() {
    const map = S.map, shown = new Set(), cities = new Set(), s = S.stand;
    const W = map.getCanvas().clientWidth, H = map.getCanvas().clientHeight, b = map.getBounds();
    // labels keep clear of what floats over the map: panel, title, dock, buttons, the first-visit hint
    const o = map.getContainer().getBoundingClientRect(), boxes = [];
    for (const el of document.querySelectorAll(".top-stack > *, .bottom-stack > *, .maplibregl-ctrl-top-right")) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) boxes.push([r.left - o.left, r.top - o.top, r.right - o.left, r.bottom - o.top]);
    }
    // on a summit the map's bounds are of no use (its centre can be far off): places within km, ahead of the camera
    const ahead = (lng, lat, km) => {
      const dx = (lng - s.lng) * Math.cos(s.lat * Math.PI / 180), dy = lat - s.lat;
      const off = ((Math.atan2(dx, dy) * 180 / Math.PI - s.bearing + 540) % 360) - 180;
      return Math.hypot(dx, dy) * M_PER_DEG < km * 1000 && Math.abs(off) < 80;
    };
    const inView = (lng, lat, km) => s ? ahead(lng, lat, km) : b.contains([lng, lat]);
    // a label's box above its point (about 6.5 px per character at 11 px), if on screen and clear of the others
    const place = (lng, lat, chars, above) => {
      const pt = map.project([lng, lat]), w = 6.5 * chars + 14, h = 18;
      const box = [pt.x - w / 2, pt.y - above - h, pt.x + w / 2, pt.y];
      if (box[0] < 0 || box[2] > W || box[1] < 0 || pt.y > H) return false;
      if (boxes.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) return false;
      boxes.push(box);
      return true;
    };
    S.cfg.cities.forEach((c, i) => { if (inView(c[1], c[2], 150) && place(c[1], c[2], c[0].length, 8)) cities.add(i); });
    if (map.getZoom() >= PEAK_ZOOM && S.peaks) {
      const max = W < 640 ? 8 : PEAK_MAX;
      const cand = S.peaks.filter((p) => p[7] > 0 && p[0] !== standId() && inView(p[4][0], p[4][1], 60))
        .sort((p, q) => q[7] - p[7]);
      for (const p of cand) {
        if (shown.size >= max) break;
        if (place(p[4][0], p[4][1], p[1].length + (p[2] != null ? 7 : 0), STEM)) shown.add(p[0]);
      }
    }
    S.peakMarkers = S.peakMarkers || new Map();
    for (const [id, m] of S.peakMarkers) if (!shown.has(id)) { m.remove(); S.peakMarkers.delete(id); }
    for (const id of shown) {
      if (!S.peakMarkers.has(id)) S.peakMarkers.set(id, peakMarker(S.peaks.find((p) => p[0] === id)).addTo(map));
    }
    S.cityMarkers = S.cityMarkers || new Map();
    for (const [i, m] of S.cityMarkers) if (!cities.has(i)) { m.remove(); S.cityMarkers.delete(i); }
    for (const i of cities) if (!S.cityMarkers.has(i)) S.cityMarkers.set(i, cityMarker(S.cfg.cities[i]).addTo(map));
  }

  // the peak list (api.py), fetched the first time the search is used or the map shows labels (zoomed in); a
  // search already showing results is
  // run again when it arrives ("peaks_ready"). Each entry gets what the search matches at its end, in lower case.
  function loadPeaks() {
    if (S.peaksLoading || !S.cfg || !S.cfg.peaks_url) return;
    S.peaksLoading = true;
    fetch(origin() + S.cfg.peaks_url).then((r) => r.ok ? r.json() : Promise.reject(r.status)).then((list) => {
      S.peaks = list.map((e) => [...e, [e[1], e[3]].filter((x) => x).join(" / ").toLowerCase()]);
      if (window.dash_clientside && window.dash_clientside.set_props)
        window.dash_clientside.set_props("peaks_ready", { data: S.peaks.length });
      if (S.urlPeak) {           // opened from a link on a summit: stand there, facing as in the link
        const v = (new URLSearchParams(window.location.search).get("view") || "").split(",").map(Number);
        flyToPeak(S.urlPeak, v.length === 5 && v.every(Number.isFinite) ? { instant: true, bearing: v[3], pitch: v[4] } : { instant: true });
        S.urlPeak = null;
      }
      if (S.loaded) updatePeakLabels();
    }).catch(() => { S.peaksLoading = false; });     // try again on the next visit to the search field
  }
  document.addEventListener("focusin", (e) => { if (e.target.closest && e.target.closest(".glacier-search")) loadPeaks(); });

  function flyToOverview() {
    leaveStand();
    const cam = S.map.cameraForBounds(S.cfg.alps_bounds, { padding: 30 });
    if (cam) S.map.flyTo({ center: cam.center, zoom: cam.zoom, pitch: 0, bearing: 0, duration: 2000 });
  }

  function apply() {
    if (!S.loaded || !S.state) return;
    const st = S.state, map = S.map;
    let restyled = false;
    if (S.appliedTheme !== st.theme) { setTheme(st.theme); S.appliedTheme = st.theme; restyled = true; }
    if (S.appliedVar !== st.variable) { setLut(st.variable); S.appliedVar = st.variable; }
    const newGlacier = st.rgi !== S.rgi;
    S.rgi = st.rgi;
    if (restyled) refreshDrape();
    if (newGlacier && S.holdView) {
      // a link on a summit is still loading: stay there
    } else if (newGlacier && S.urlView) {
      // opened from a link with a camera: stay there. The selection may still be on its way from the
      // server (null first), so the link's view only ends once the link's glacier has arrived.
      if (st.rgi || urlGlacier() === null) S.urlView = null;
    } else if (newGlacier && st.rgi) flyToGlacier(st.rgi, S.firstFlyDone ? 2500 : 3500);
    else if (newGlacier) flyToOverview();      // selection cleared: back to all glaciers
    S.firstFlyDone = true;
    sync();
  }

  // ways back after moving the camera: a click on the search field while it shows the selected glacier (or the
  // peak one stands on) returns to that glacier's (or the summit's first) view; a click on the title leaves a
  // summit, or clears the selection (the × button; apply() then flies to the whole Alps), or with nothing
  // selected just flies there
  document.addEventListener("click", (e) => {
    if (!S.loaded || !e.target.closest) return;
    const box = e.target.closest(".glacier-search");
    const onField = box && box.classList.contains("has-sel") && e.target.closest(".gs-input");
    if (onField && S.stand) flyToPeak(S.stand.id);              // back to the summit's first view
    else if (onField && S.rgi && !S.flight) flyToGlacier(S.rgi, 1500);
    else if (e.target.closest(".map-title") && (S.stand || S.flight)) leavePeak();
    else if (e.target.closest(".map-title")) {
      const clear = document.getElementById("glacier_clear");
      if (S.rgi && clear) clear.click();
      else flyToOverview();
    }
  });

  // the colour bar is a div acting as a button: Enter and Space press it, as for a real button
  document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("div[role=button]")) {
      e.preventDefault(); e.target.click();
    }
  });

  // the switch to the other language keeps the current view (glacier, scenario, year, camera)
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a.lang-switch");
    if (a) a.href = a.getAttribute("href").split("?")[0] + window.location.search;
  });

  // glacier search by keyboard: arrows move between the field and the results, Enter picks the highlighted result
  // (Enter in the field picks the first one), typing goes back to the field, Esc closes the results
  document.addEventListener("keydown", (e) => {
    const box = e.target.closest && e.target.closest(".glacier-search");
    if (!box) return;
    const input = box.querySelector("input"), hits = [...box.querySelectorAll(".gs-hit:not(.is-hidden)")];
    const at = hits.indexOf(e.target);
    if (e.key === "Escape") { e.target.blur(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!hits.length) return;
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(at + 1, hits.length - 1) : at - 1;
      (next < 0 ? input : hits[next]).focus();
    } else if (at >= 0 && e.key === "Enter") {
      e.preventDefault(); e.target.click();
    } else if (at >= 0 && (e.key.length === 1 || e.key === "Backspace")) {
      input.focus();                         // the key then lands in the field
    }
  });

  // ---------------------------------------------------------------- a new version after a restart
  // The page knows the version it was built with (cfg.version); after a restart with new code its callbacks may
  // no longer exist on the server. Checked every 5 minutes, when the tab comes back, and right after a failed
  // callback; if the server's differs, a note offers to reload (the address keeps the view).
  function checkVersion() {
    if (!S.cfg || S.updateShown) return;
    fetch(origin() + "/api3d/version", { cache: "no-store" }).then((r) => r.ok ? r.text() : null).then((v) => {
      if (v && v.trim() !== S.cfg.version) showUpdate();
    }).catch(() => {});
  }
  function showUpdate() {
    const stack = document.querySelector(".bottom-stack");
    if (!stack || S.updateShown) return;
    S.updateShown = true;
    const el = document.createElement("div");
    el.className = "float update-note";
    el.setAttribute("role", "status");
    el.innerHTML = `<span>${esc(S.cfg.t.update)}</span><button class="btn">${esc(S.cfg.t.reload)}</button>`;
    el.querySelector("button").addEventListener("click", () => window.location.reload());
    stack.insertBefore(el, stack.firstChild);
  }
  setInterval(checkVersion, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkVersion(); });
  const fetch0 = window.fetch.bind(window);
  window.fetch = (...a) => fetch0(...a).then((r) => {
    if (!r.ok && String((a[0] && a[0].url) || a[0]).includes("_dash-update-component")) checkVersion();
    return r;
  });

  window.Map3D = {
    render(state, cfg) {
      S.state = state;
      if (!S.map && !init(cfg)) {
        // map container or MapLibre not there yet: retry shortly
        setTimeout(() => window.Map3D.render(S.state, cfg), 200);
        return;
      }
      apply();
    },
    busy() { return S.frameReq !== null || S.bedReq.size > 0; },
    flyToPeak,
    leavePeak,
    peaks() { return S.peaks || []; },
  };
})();
