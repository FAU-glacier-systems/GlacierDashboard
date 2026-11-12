// Not part of MapLibre: map3d.js is a classic script and expects MapLibre as the global `maplibregl`, as in v5.
// MapLibre 6 is an ES module only, so this loads it and sets that global (map3d.js waits for it in init).
import * as maplibregl from "./maplibre-gl.mjs";
window.maplibregl = maplibregl;
