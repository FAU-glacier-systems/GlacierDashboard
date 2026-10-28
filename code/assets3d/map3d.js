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
    pending: new Map(),         // rgi -> GPU record at a new level of detail, waiting for its ice (sync)
    visible: [],                // glaciers currently drawn
    frameReq: null,             // url of the frames request in flight
    frameShown: null,           // url of the frames currently drawn
    bedReq: new Set(),          // "stride|ids" bedrock requests in flight
    prefetch: new Map(),        // url -> Promise<ArrayBuffer>
    gl: null, prog: null, lut: null,
    firstFlyDone: false,
  };
  const origin = () => window.location.origin;
  const MAX_PITCH = 80;
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
    // a glacier at a new level of detail is loaded beside the drawn one (S.pending) and replaces it only once its ice
    // has arrived too, so it never vanishes in between; back at the drawn level, the one loading is dropped
    const have = (r, s) => { const G = S.glaciers.get(r), P = S.pending.get(r); return (G && G.s === s) || (P && P.s === s); };
    for (const [r, s] of want) {
      const G = S.glaciers.get(r), P = S.pending.get(r);
      if (P && G && G.s === s) { freeRecord(P); S.pending.delete(r); }
    }

    const missing = want.filter(([r, s]) => !have(r, s));
    const bedKey = idList(missing);
    if (missing.length && !S.bedReq.has(bedKey)) {
      S.bedReq.add(bedKey);
      const gl = S.gl;
      let ok = false;
      fetchBuf(`${origin()}/api3d/beds?ids=${bedKey}`).then((buf) => {
        if (S.gl !== gl) return;               // the WebGL context was replaced meanwhile (contextRestored)
        let off = 0;
        for (const [r, s] of missing) {
          const [nx, ny] = dims(S.cfg.meshes[r], s), n = nx * ny;
          const bed = new Float32Array(buf, off, n); off += n * 4;
          const old = S.glaciers.get(r), loading = S.pending.get(r);
          if (loading) { freeRecord(loading); S.pending.delete(r); }
          if (old && old.ready) S.pending.set(r, makeRecord(r, s, bed));
          else { if (old) freeRecord(old); S.glaciers.set(r, makeRecord(r, s, bed)); }
        }
        ok = true;
      }).catch(() => {}).finally(() => {
        S.bedReq.delete(bedKey); S.frameShown = null;
        retryLater(ok);                         // a failure (busy server, network) is tried again after a pause
      });
    }

    // draw what is loaded (at its current level of detail) until the rest arrives
    S.visible = ids.map((r) => S.glaciers.get(r)).filter(Boolean);
    S.map.triggerRepaint();
    const ready = want.filter(([r, s]) => have(r, s));
    if (!ready.length) { S.frameShown = null; return; }

    const url = framesUrl(ready, st, st.year);
    if (url === S.frameShown || url === S.frameReq) return;
    S.frameReq = url;
    fetchBuf(url).then((buf) => {
      if (S.frameReq !== url) return;          // a newer request took over
      if (buf.byteLength < ready.reduce((sum, [r, s]) => { const [nx, ny] = dims(S.cfg.meshes[r], s); return sum + nx * ny * 2; }, 0))
        throw new Error("short");               // not a frame (an error page from a read-ahead)
      let off = 0;
      for (const [r, s] of ready) {
        const G = S.glaciers.get(r), P = S.pending.get(r);
        const [nx, ny] = dims(S.cfg.meshes[r], s), n = nx * ny;
        const thk = new Uint16Array(buf, off, n); off += n * 2;
        let prop = null;
        if (st.variable !== "thk") { prop = new Uint8Array(buf, off, n); off += n + (n % 2); }
        if (P && P.s === s) {                  // the new level of detail is complete: it takes over
          applyFrame(P, thk, prop, st.variable);
          if (G) freeRecord(G);
          S.glaciers.set(r, P); S.pending.delete(r);
        } else if (G && G.s === s) applyFrame(G, thk, prop, st.variable);
      }
      S.visible = ids.map((r) => S.glaciers.get(r)).filter(Boolean);
      S.frameShown = url; S.failures = 0;
      if (S.hover) showHover();            // the label follows the shown year
      S.map.triggerRepaint();
      if (st.playing) {                       // read ahead: the next year of the same glaciers
        const next = st.year >= S.cfg.years[1] ? S.cfg.years[0] : st.year + 1;
        const nu = framesUrl(ready, st, next);
        if (!S.prefetch.has(nu)) S.prefetch.set(nu, fetch(nu).then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); }));
        while (S.prefetch.size > 4) S.prefetch.delete(S.prefetch.keys().next().value);
      }
    }).catch(() => {
      if (S.frameReq === url) { S.frameReq = null; retryLater(false); }   // else the ice would wait for the next move
    }).finally(() => {
      if (S.frameReq === url) S.frameReq = null;
    });

    // free GPU memory of glaciers far away
    const keep = new Set(ids);
    for (const [r, P] of S.pending) if (!keep.has(r)) { freeRecord(P); S.pending.delete(r); }
    if (S.glaciers.size > 250) {
      for (const [r, G] of S.glaciers) {
        if (keep.has(r)) continue;
        freeRecord(G); S.glaciers.delete(r);
        if (S.glaciers.size <= 180) break;
      }
    }
  }

  // sync again: at once after a success, after a pause growing to 8 s after failures in a row (a busy server answers
  // 429 when a whole room opens the page at once), one pending retry at a time
  function retryLater(ok) {
    if (ok) { S.failures = 0; sync(); return; }
    if (S.retryTimer) return;
    S.failures = (S.failures || 0) + 1;
    S.retryTimer = setTimeout(() => { S.retryTimer = null; sync(); }, Math.min(1000 * 2 ** (S.failures - 1), 8000));
  }

  // ---------------------------------------------------------------- camera in the address bar
  // ?view=lon,lat,zoom,bearing,pitch, so a shared link opens the same view
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
    p.delete("peak");                  // links from the former summit view
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
    let map;
    try {
      map = new maplibregl.Map({
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
    } catch (e) {
      // no WebGL (switched off, or a very old device): say so instead of an empty page
      const note = document.createElement("div");
      note.className = "webgl-note";
      note.textContent = cfg.t.no_webgl || "This map needs WebGL, which this browser does not provide.";
      el.appendChild(note);
      S.map = true;                                 // do not try again
      return true;
    }
    S.map = map;
    // phones drop the WebGL context under memory pressure or in the background; MapLibre restores its own layers
    // but not the ice: everything on the GPU is gone, so it is added again and reloaded
    map.on("webglcontextrestored", () => {
      const readd = () => {
        S.glaciers.clear(); S.pending.clear(); S.visible = []; S.progs = {}; S.frameShown = null; S.frameReq = null;
        S.appliedVar = null; S.appliedTheme = null;
        if (!map.getLayer("ice")) map.addLayer(iceLayer);
        apply();
      };
      if (map.isStyleLoaded()) readd(); else map.once("style.load", readd);
    });
    window._map3d = map;   // handy for debugging in the console
    map.addControl(themeControl(), "top-right");   // above the zoom buttons
    map.addControl(new maplibregl.NavigationControl({ showZoom: false, visualizePitch: true }), "top-right");   // compass only
    map.addControl(homeControl(), "top-right");     // under the compass
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
          map.getCanvas().style.cursor = rgi ? "pointer" : "";
          S.hover = rgi ? { rgi, lngLat: e.lngLat } : null;
          showHover();
        });
      });
      map.on("mouseout", () => { S.hover = null; showHover(); });
      map.on("click", (e) => {
        if (e.originalEvent.target.closest && e.originalEvent.target.closest(".peak-label")) return;
        const rgi = pickGlacier(e);
        if (rgi && window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("rgi_select", { data: { rgi, t: Date.now() } });
        }
      });
      map.on("moveend", () => { if (!S.flight) rested(); });   // a peak flight calls it when it ends

      S.loaded = true;
      const ov = map.cameraForBounds(cfg.alps_bounds, { padding: 30 });
      S.overviewZoom = ov ? ov.zoom : null;
      if (map.getZoom() >= PEAK_ZOOM) loadPeaks();   // a link opens close in without moving: labels anyway
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
  // the home button under the compass, as a map control like it: back to all glaciers (goHome); shown only while
  // there is a way back (html[data-away], updateHome)
  function homeControl() {
    let box;
    return {
      onAdd() {
        box = document.createElement("div");
        box.className = "maplibregl-ctrl maplibregl-ctrl-group home-ctrl";
        const btn = document.createElement("button");
        btn.type = "button";      // the text from the page; a page built before it existed: by language
        btn.title = S.cfg.t.title_tip || (S.cfg.lang === "de" ? "Zurück zu allen Gletschern" : "Back to all glaciers");
        btn.setAttribute("aria-label", btn.title);
        btn.innerHTML = '<span class="maplibregl-ctrl-icon" aria-hidden="true"></span>';
        btn.addEventListener("click", () => { if (S.loaded) goHome(); });
        box.appendChild(btn);
        return box;
      },
      onRemove() { box.remove(); },
    };
  }

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

  // after the camera came to rest: glaciers in view, address, labels. MapLibre places a label on the terrain
  // loaded at that moment and moves it only with the map, so once the finer terrain is in, they are placed again.
  function rested() {
    updateHome();
    sync(); viewToUrl();
    if (S.map.getZoom() >= PEAK_ZOOM) loadPeaks();
    updatePeakLabels();
    S.map.once("idle", () => {
      for (const m of [...(S.peakMarkers || new Map()).values(), ...(S.cityMarkers || new Map()).values()])
        m.setLngLat(m.getLngLat());
    });
  }

  // the home icon at the title, while there is a way back: a glacier selected, or closer in than the whole Alps
  function updateHome() {
    const away = !!S.rgi || (S.overviewZoom != null && S.map.getZoom() > S.overviewZoom + 0.5);
    if (away) document.documentElement.dataset.away = ""; else delete document.documentElement.dataset.away;
  }

  function flyToGlacier(rgi, duration) {
    const m = S.cfg.meshes[rgi];
    if (!m) return;
    stopPeakFlight();
    const b = m.bbox;
    const cam = S.map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding: framePadding() });
    if (!cam) return;
    S.map.flyTo({ center: cam.center, zoom: Math.min(cam.zoom + 0.3, 12.2), pitch: 60,
                  bearing: facingBearing(m.aspect), duration, essential: true });
  }

  // a peak from the search or a label: fly there, keeping the direction and tilt of the view, closer in only from
  // further out than PEAK_FLY_ZOOM; the search field goes back to the selected glacier. The flight moves the camera
  // itself, frame by frame, with the summit's height from the peak list (tools/build_peaks.py): MapLibre's flyTo
  // takes the height of the destination from the terrain loaded there, and if those tiles arrive only after the
  // flight it stays at 0 m and the camera ends far above and behind the mountain. Its path and timing are those of
  // flyTo (van Wijk & Nuij: out and back in so the motion on screen stays even, MapLibre's easing, 2.5 s as for a
  // glacier), so both kinds of flight look alike. A pointer, wheel or key stops it.
  const PEAK_FLY_ZOOM = 12.5, FLY_CURVE = 1.42, FLY_MS = 2500;
  // MapLibre's default easing, cubic-bezier(0.25, 0.1, 0.25, 1)
  const flyEase = (x) => {
    const bez = (t, p1, p2) => 3 * (1 - t) * (1 - t) * t * p1 + 3 * (1 - t) * t * t * p2 + t * t * t;
    let t = x;
    for (let i = 0; i < 8; i++) {                               // Newton steps for the t with bez_x(t) = x
      const dx = 0.75 * (1 - t) * (1 - t) + 2.25 * t * t;        // d bez_x / dt
      t = Math.min(Math.max(t - (bez(t, 0.25, 0.25) - x) / Math.max(dx, 1e-6), 0), 1);
    }
    return bez(t, 0.1, 1);
  };
  // put the map's centre where the line of sight from the camera meets the terrain, the camera staying exactly where
  // it is (MapLibre's flyTo ends with the centre at 0 m when the destination's terrain loaded late)
  function centreOnGround() {
    const m = S.map, t = m.transform, c = t.getCameraLngLat(), lat = m.getCenter().lat;
    const perM = t.worldSize / (40075016.686 * Math.cos(lat * Math.PI / 180));
    const P = m.getPitch() * Math.PI / 180, B = m.getBearing() * Math.PI / 180;
    const alt = t.elevation + t.cameraToCenterDistance * Math.cos(P) / perM, far = t.cameraToCenterDistance / perM;
    const at = (D) => [c.lng + D * Math.sin(P) * Math.sin(B) / (111320 * Math.cos(c.lat * Math.PI / 180)),
                       c.lat + D * Math.sin(P) * Math.cos(B) / 111320, alt - D * Math.cos(P)];
    const below = (D) => { const q = at(D), g = m.queryTerrainElevation([q[0], q[1]]); return g != null && q[2] <= g; };
    let lo = 0, hi = null;
    for (let i = 1; i <= 200 && hi === null; i++) { const D = far * i / 200; if (below(D)) hi = D; else lo = D; }
    if (hi === null) return;                                   // no terrain there yet: leave it
    for (let i = 0; i < 12; i++) { const mid = (lo + hi) / 2; if (below(mid)) hi = mid; else lo = mid; }
    const q = at(hi);
    if (Math.abs(q[2] - t.elevation) < 1) return;
    const o = m.calculateCameraOptionsFromTo(c, alt, new maplibregl.LngLat(q[0], q[1]), q[2]);
    m.jumpTo({ center: o.center, zoom: o.zoom, bearing: o.bearing, pitch: o.pitch, elevation: o.elevation });
  }

  function flyToPeak(id) {
    const p = (S.peaks || []).find((e) => e[0] === id);
    if (!p || !S.map) return;
    const map = S.map, tr = map.transform;
    stopPeakFlight();
    map.stop();
    centreOnGround();           // MapLibre's own flights can leave the centre at 0 m: from there it would rise km
    const z0 = map.getZoom(), z1 = Math.max(z0, PEAK_FLY_ZOOM), h0 = tr.elevation, h1 = p[4][2];
    const from = maplibregl.MercatorCoordinate.fromLngLat(map.getCenter());
    const to = maplibregl.MercatorCoordinate.fromLngLat([p[4][0], p[4][1]]);
    // the flight path: w = visible width relative to the start, u = share of the way, both over the path length S
    const rho = FLY_CURVE, rho2 = rho * rho, w0 = Math.max(map.getCanvas().clientWidth, map.getCanvas().clientHeight);
    const w1 = w0 / 2 ** (z1 - z0), u1 = Math.hypot(to.x - from.x, to.y - from.y) * 512 * 2 ** z0;
    const r = (i) => { const q = (w1 * w1 - w0 * w0 + (i ? -1 : 1) * rho2 * rho2 * u1 * u1) / (2 * (i ? w1 : w0) * rho2 * u1);
                       return Math.log(Math.sqrt(q * q + 1) - q); };
    const r0 = r(0);
    let S_ = (r(1) - r0) / rho, w = (s) => Math.cosh(r0) / Math.cosh(r0 + rho * s);
    let u = (s) => w0 * ((Math.cosh(r0) * Math.tanh(r0 + rho * s) - Math.sinh(r0)) / rho2) / u1;
    if (Math.abs(u1) < 2e-6 || !isFinite(S_)) {                   // (almost) there already: only the zoom changes
      const dir = w1 < w0 ? -1 : 1;
      S_ = Math.abs(Math.log(w1 / w0)) / rho; u = () => 1; w = (s) => Math.exp(dir * rho * s);
    }
    // always flown, also with "reduce motion" set, like the glacier flights (essential: true): the flight shows
    // where the peak lies
    const duration = FLY_MS, t0 = performance.now();
    map.setCenterClampedToGround(false);    // the centre at the summit's height, not where the terrain has loaded
    S.flight = {};
    const step = () => {
      const k = duration ? Math.min((performance.now() - t0) / duration, 1) : 1, e = flyEase(k), s = e * S_;
      const f = k < 1 ? u(s) : 1, zoom = k < 1 ? z0 + Math.log2(1 / w(s)) : z1;
      const c = new maplibregl.MercatorCoordinate(from.x + (to.x - from.x) * f, from.y + (to.y - from.y) * f).toLngLat();
      map.jumpTo({ center: c, zoom, elevation: h0 + (h1 - h0) * e });
      if (k < 1) { S.flight.frame = requestAnimationFrame(step); return; }
      stopPeakFlight();
      rested();
    };
    step();
    if (window.dash_clientside && window.dash_clientside.set_props) {
      const o = S.rgi ? S.cfg.search.find((e) => e[0] === S.rgi) : null;
      window.dash_clientside.set_props("glacier_search", { value: o ? (o[1] || o[0]) : "" });
    }
  }

  function stopPeakFlight() {
    if (!S.flight) return;
    cancelAnimationFrame(S.flight.frame);
    S.flight = null;
    S.map.setCenterClampedToGround(true);
  }
  for (const ev of ["pointerdown", "wheel", "keydown"]) {
    document.addEventListener(ev, (e) => {
      if (!S.flight || (e.target.closest && e.target.closest(".glacier-search, .peak-label"))) return;
      stopPeakFlight();
      rested();
    }, { capture: true });
  }

  // ---------------------------------------------------------------- peak labels
  // Name and height above the summits in view, with a dashed line down to the summit; a click flies there.
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
    const map = S.map, shown = new Set(), cities = new Set();
    const W = map.getCanvas().clientWidth, H = map.getCanvas().clientHeight, b = map.getBounds();
    // labels keep clear of what floats over the map: panel, title, dock, buttons, the first-visit hint
    const o = map.getContainer().getBoundingClientRect(), boxes = [];
    for (const el of document.querySelectorAll(".top-stack > *, .bottom-stack > *, .maplibregl-ctrl-top-right")) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) boxes.push([r.left - o.left, r.top - o.top, r.right - o.left, r.bottom - o.top]);
    }
    const inView = (lng, lat) => b.contains([lng, lat]);
    // a label's box above its point (about 6.5 px per character at 11 px), if on screen and clear of the others
    const place = (lng, lat, chars, above) => {
      const pt = map.project([lng, lat]), w = 6.5 * chars + 14, h = 18;
      const box = [pt.x - w / 2, pt.y - above - h, pt.x + w / 2, pt.y];
      if (box[0] < 0 || box[2] > W || box[1] < 0 || pt.y > H) return false;
      if (boxes.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) return false;
      boxes.push(box);
      return true;
    };
    S.cfg.cities.forEach((c, i) => { if (inView(c[1], c[2]) && place(c[1], c[2], c[0].length, 8)) cities.add(i); });
    if (map.getZoom() >= PEAK_ZOOM && S.peaks) {
      const max = W < 640 ? 8 : PEAK_MAX;
      const cand = S.peaks.filter((p) => p[7] > 0 && inView(p[4][0], p[4][1]))
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
      if (S.loaded) updatePeakLabels();
    }).catch(() => { S.peaksLoading = false; });     // try again on the next visit to the search field
  }
  document.addEventListener("focusin", (e) => { if (e.target.closest && e.target.closest(".glacier-search")) loadPeaks(); });

  function flyToOverview() {
    stopPeakFlight();
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
    if (newGlacier && S.urlView) {
      // opened from a link with a camera: stay there. The selection may still be on its way from the
      // server (null first), so the link's view only ends once the link's glacier has arrived.
      if (st.rgi || urlGlacier() === null) S.urlView = null;
    } else if (newGlacier && st.rgi) flyToGlacier(st.rgi, S.firstFlyDone ? 2500 : 3500);
    else if (newGlacier) flyToOverview();      // selection cleared: back to all glaciers
    S.firstFlyDone = true;
    updateHome();
    sync();
  }

  // ways back after moving the camera: a click on the search field while it shows the selected glacier returns to
  // that glacier's view; a click on the title or the home button (goHome) clears the selection (the × button;
  // apply() then flies to the whole Alps), or with nothing selected just flies there
  function goHome() {
    const clear = document.getElementById("glacier_clear");
    if (S.rgi && clear) clear.click();
    else flyToOverview();
  }
  document.addEventListener("click", (e) => {
    if (!S.loaded || !e.target.closest) return;
    const box = e.target.closest(".glacier-search");
    const onField = box && box.classList.contains("has-sel") && e.target.closest(".gs-input");
    if (onField && S.rgi) flyToGlacier(S.rgi, 1500);
    else if (e.target.closest(".map-title")) goHome();
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
    peaks() { return S.peaks || []; },
  };
})();
