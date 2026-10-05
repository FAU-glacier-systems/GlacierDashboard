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
  const LIGHT = normalize([-0.55, 0.55, 0.65]);   // from the north-west, as on maps
  // The terrain under the ice is a raster resampled from the model bedrock; the coarser the terrain tiles (the
  // further away), the more it smooths narrow valleys upwards. Lift the ice by about a tenth of the terrain
  // tile resolution so it is not hidden there; invisible at that distance.
  const lift = (zoom) => Math.min(Math.max(0.1 * 40075016 / 256 / 2 ** zoom * Math.cos(46.5 * Math.PI / 180), 0.5), 150);

  const LOOK = {
    dark: {
      background: "#202020",
      hillshade: { "hillshade-exaggeration": 0.6, "hillshade-shadow-color": "#000", "hillshade-highlight-color": "#a8a8a8",
                   "hillshade-accent-color": "#1a1a1a" },
      sky: { "sky-color": "#0b1320", "horizon-color": "#2a3444", "fog-color": "#151515", "sky-horizon-blend": 0.6,
             "horizon-fog-blend": 0.6, "fog-ground-blend": 0.85, "atmosphere-blend": 0.4 },
    },
    light: {
      background: "#ececec",
      hillshade: { "hillshade-exaggeration": 0.5, "hillshade-shadow-color": "#6b6b6b", "hillshade-highlight-color": "#fff",
                   "hillshade-accent-color": "#8a8a8a" },
      sky: { "sky-color": "#bcd6ec", "horizon-color": "#eef3f7", "fog-color": "#f4f4f4", "sky-horizon-blend": 0.6,
             "horizon-fog-blend": 0.6, "fog-ground-blend": 0.85, "atmosphere-blend": 0.5 },
    },
  };

  function normalize(v) { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }

  // ---------------------------------------------------------------- level of detail
  // Target cell size on screen by zoom; each glacier skips cells (stride 1, 2, 4, 8) to get close to it,
  // so 25 m and 100 m model grids end up at a similar resolution.
  function targetCell(zoom) { return zoom >= 12.5 ? 25 : zoom >= 11.5 ? 50 : zoom >= 10.5 ? 100 : zoom >= 9.5 ? 200 : 800; }
  function strideFor(rgi, zoom) {
    const r = targetCell(zoom) / S.cfg.meshes[rgi].dx;
    return r >= 8 ? 8 : r >= 4 ? 4 : r >= 2 ? 2 : 1;
  }
  const dims = (m, s) => [Math.ceil(m.nx / s), Math.ceil(m.ny / s)];

  // ---------------------------------------------------------------- WebGL ice layer
  const VS = `
    precision highp float;
    uniform mat4 u_matrix; uniform float u_zscale; uniform float u_lift;
    attribute vec2 a_pos; attribute vec4 a_zn; attribute vec2 a_ti;
    varying vec3 v_n; varying float v_t; varying float v_ice;
    void main() {
      v_n = a_zn.yzw; v_t = a_ti.x; v_ice = a_ti.y;
      gl_Position = u_matrix * vec4(a_pos, (a_zn.x + u_lift) * u_zscale, 1.0);
    }`;
  const FS = `
    precision mediump float;
    uniform sampler2D u_lut; uniform vec3 u_light;
    varying vec3 v_n; varying float v_t; varying float v_ice;
    void main() {
      if (v_ice < 0.5) discard;
      vec3 c = v_t < -0.5 ? vec3(0.6) : texture2D(u_lut, vec2(clamp(v_t, 0.0, 1.0) * 0.99609375 + 0.001953125, 0.5)).rgb;
      float l = 0.5 + 0.6 * max(dot(normalize(v_n), u_light), 0.0);
      gl_FragColor = vec4(c * l, 1.0);
    }`;

  function compile(gl) {
    const sh = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return {
      p, a_pos: gl.getAttribLocation(p, "a_pos"), a_zn: gl.getAttribLocation(p, "a_zn"), a_ti: gl.getAttribLocation(p, "a_ti"),
      u_matrix: gl.getUniformLocation(p, "u_matrix"), u_zscale: gl.getUniformLocation(p, "u_zscale"),
      u_lift: gl.getUniformLocation(p, "u_lift"),
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
      S.prog = compile(gl);
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
      const P = S.prog, M = args.defaultProjectionData.mainMatrix;
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
      gl.enableVertexAttribArray(P.a_pos); gl.enableVertexAttribArray(P.a_zn); gl.enableVertexAttribArray(P.a_ti);
      const m = new Float32Array(16);
      for (const G of S.visible) {
        if (!G.ready) continue;
        // u_matrix = M * translate(origin), in double precision before the cast
        for (let i = 0; i < 12; i++) m[i] = M[i];
        for (let i = 0; i < 4; i++) m[12 + i] = M[i] * G.ox + M[4 + i] * G.oy + M[12 + i];
        gl.uniformMatrix4fv(P.u_matrix, false, m);
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
      rgi, s, nx, ny, n, ox, oy, bed, ready: false,
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

  // surface, normal, colour value and thickness per vertex for one year
  function applyFrame(G, thk, prop, variable) {
    const v = S.cfg.vars[variable], span = v.hi - v.lo, { nx, ny, bed, dyn, h } = G;
    const z = new Float32Array(G.n);
    for (let k = 0; k < G.n; k++) z[k] = bed[k] + thk[k] * 0.1;
    for (let j = 0; j < ny; j++) {
      const jn = Math.max(j - 1, 0), js = Math.min(j + 1, ny - 1);
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i, iw = Math.max(i - 1, 0), ie = Math.min(i + 1, nx - 1);
        const t = thk[k] * 0.1, ok = Number.isFinite(z[k]);
        const val = variable === "thk" ? (t - v.lo) / span
          : prop[k] === 255 ? -1 : (v.offset + prop[k] * v.scale - v.lo) / span;
        let dzdx = (z[j * nx + ie] - z[j * nx + iw]) / ((ie - iw) * h);
        let dzdn = (z[jn * nx + i] - z[js * nx + i]) / ((js - jn) * h);
        if (!Number.isFinite(dzdx)) dzdx = 0;
        if (!Number.isFinite(dzdn)) dzdn = 0;
        const nz = 1 / Math.hypot(dzdx, dzdn, 1), o = k * 6;
        dyn[o] = ok ? z[k] : 0;
        dyn[o + 1] = -dzdx * nz; dyn[o + 2] = -dzdn * nz; dyn[o + 3] = nz;
        dyn[o + 4] = val;
        dyn[o + 5] = ok ? t : 0;
      }
    }
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
  // ?view=lon,lat,zoom,bearing,pitch, so a shared link opens the same view
  function viewFromUrl() {
    const v = (new URLSearchParams(window.location.search).get("view") || "").split(",").map(Number);
    if (v.length !== 5 || v.some((x) => !Number.isFinite(x))) return null;
    const [lng, lat, zoom, bearing, pitch] = v;
    if (Math.abs(lng) > 180 || Math.abs(lat) > 85 || zoom < 0 || zoom > 22) return null;
    return { center: [lng, lat], zoom, bearing, pitch: Math.min(Math.max(pitch, 0), 80) };
  }

  function viewToUrl() {
    const m = S.map, c = m.getCenter();
    const v = [c.lng.toFixed(4), c.lat.toFixed(4), m.getZoom().toFixed(2), m.getBearing().toFixed(0), m.getPitch().toFixed(0)];
    const p = new URLSearchParams(window.location.search);
    p.set("view", v.join(","));
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
    S.urlView = viewFromUrl();
    S.names = new Map(cfg.glaciers.features.map((f) => [f.properties.rgi, f.properties.name]));
    S.centres = cfg.glaciers.features.map((f) => [f.properties.rgi, f.geometry.coordinates]);
    const map = new maplibregl.Map({
      container: el,
      // no paint transitions: the terrain caches draped layers, a half-finished fade would stay visible
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#202020" } }],
               transition: { duration: 0, delay: 0 } },
      ...(S.urlView || { bounds: cfg.alps_bounds, fitBoundsOptions: { padding: 30 } }),
      maxPitch: 80,
      attributionControl: false,
    });
    S.map = map;
    window._map3d = map;   // handy for debugging in the console
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");

    map.on("load", () => {
      const dem = { type: "raster-dem", tiles: [origin() + cfg.terrain_url], tileSize: 256,
                    encoding: "terrarium", maxzoom: cfg.dem_maxzoom };
      map.addSource("dem", { ...dem, attribution: cfg.dem_attribution });
      map.addSource("dem-hs", dem);   // hillshade wants its own source
      map.setTerrain({ source: "dem", exaggeration: 1.0 });
      map.addLayer({ id: "hillshade", type: "hillshade", source: "dem-hs" });
      map.addLayer(iceLayer);

      // glacier names on hover, selection on click (no markers: the ice itself shows where glaciers are)
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
      let hoverFrame = 0;
      map.on("mousemove", (e) => {
        if (hoverFrame) return;
        hoverFrame = requestAnimationFrame(() => {
          hoverFrame = 0;
          const rgi = pickGlacier(e);
          map.getCanvas().style.cursor = rgi ? "pointer" : "";
          if (!rgi) { popup.remove(); return; }
          const f = S.names.get(rgi);
          popup.setLngLat(e.lngLat).setText(f).addTo(map);
        });
      });
      map.on("mouseout", () => { popup.remove(); });
      map.on("click", (e) => {
        const rgi = pickGlacier(e);
        if (rgi && window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("rgi_select", { value: rgi });
        }
      });
      map.on("moveend", () => { sync(); viewToUrl(); });

      // the compact attribution opens itself whenever sources change; keep it folded until clicked
      map.once("idle", () => el.querySelectorAll(".maplibregl-compact-show")
        .forEach((n) => n.classList.remove("maplibregl-compact-show")));
      S.loaded = true;
      showIntro();
      apply();
    });
    return true;
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

  function flyToGlacier(rgi, duration) {
    const m = S.cfg.meshes[rgi];
    if (!m) return;
    const b = m.bbox;
    const cam = S.map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: 80, bottom: 110, left: 40, right: 40 } });
    if (!cam) return;
    S.map.flyTo({ center: cam.center, zoom: Math.min(cam.zoom + 0.3, 12.2), pitch: 60,
                  bearing: S.map.getBearing(), duration, essential: true });
  }

  function flyToOverview() {
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
    sync();
  }

  // When a glacier is selected, Dash's dropdown focuses that entry instead of its search field, so typing
  // would go nowhere. Put the cursor into the search field whenever the glacier list opens.
  document.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest("#rgi_select")) return;
    // the list opens and moves focus asynchronously; take it back once it has settled
    for (const ms of [30, 120, 300]) setTimeout(() => { const f = document.querySelector(".dash-dropdown-search"); if (f) f.focus(); }, ms);
  }, true);

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
  };
})();
