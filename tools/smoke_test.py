"""
Smoke test of the 3D dashboard in a headless browser: run it against a local instance (or the live site) before
and after a restart. It checks what a visitor would notice first:

    page and map load without script errors, the version endpoint answers
    city labels in the overview, peak labels in the default (Aletsch) view
    search: a peak by another language's name ("Cervino"), Enter flies onto the summit
    on the summit: the camera lands where planned and stays put while the view turns (arrow keys) and zooms (+),
    looking up keeps the flat map, the compass returns to the first view, the search field shows the peak,
    "Leave summit" is there; leaving restores the normal view
    search: a glacier is selected (address)
    the German page, and the legal pages with their data-source and privacy texts

Needs Playwright (pip install playwright) and a Chromium: either `python -m playwright install chromium` (with its
system libraries: `sudo python -m playwright install-deps`), or an existing headless shell via --browser.

Usage (from the repo root):
    cd code && DASH_PORT=8060 python glacier_dashboard_3d.py &      # a local instance of the current code
    python tools/smoke_test.py [--url http://127.0.0.1:8060] [--browser /path/to/chrome-headless-shell]
Exit code 0 if every check passed.
"""
import argparse
import sys
import urllib.request

from playwright.sync_api import sync_playwright

# where the camera is, from MapLibre's transform (the same formula as cameraNow() in map3d.js)
CAMERA = """() => { const m = window._map3d, t = m.transform, c = t.getCameraLngLat(), lat = m.getCenter().lat;
  const perMetre = t.worldSize / (40075016.686 * Math.cos(lat * Math.PI / 180));
  return {lng: c.lng, lat: c.lat, alt: t.elevation + t.cameraToCenterDistance * Math.cos(m.getPitch() * Math.PI / 180) / perMetre,
          bearing: m.getBearing(), fov: m.getVerticalFieldOfView()}; }"""


class Checks:
    def __init__(self):
        self.failed = 0

    def __call__(self, name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
        self.failed += not ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8060", help="the dashboard's address")
    ap.add_argument("--browser", help="path to a Chromium / chrome-headless-shell (default: Playwright's)")
    args = ap.parse_args()
    url, check = args.url.rstrip("/"), Checks()

    for path, text in (("/api3d/version", None), ("/impressum", "OpenStreetMap"), ("/imprint", "OpenStreetMap"),
                       ("/datenschutz", "nach einem Jahr"), ("/privacy", "after one year"),
                       ("/barrierefreiheit", "Gipfel"), ("/accessibility", "summit")):
        try:
            body = urllib.request.urlopen(url + path, timeout=30).read().decode()
            check(f"GET {path}", text is None or text in body, "" if text is None else f"contains '{text}'")
        except Exception as ex:
            check(f"GET {path}", False, str(ex))

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=args.browser,
                              args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url + "/")
        pg.wait_for_function("window._map3d && window._map3d.loaded()", timeout=60000)
        pg.wait_for_timeout(8000)                                   # the first flight to the Aletsch glacier
        check("map loaded", True)
        intro = pg.locator("#intro_close")
        if intro.is_visible():
            intro.click()
        check("peak labels in the Aletsch view", pg.locator(".peak-label").count() > 3,
              f"{pg.locator('.peak-label').count()} labels")

        # search a peak by its Italian name and stand on it
        field = pg.locator(".gs-input input")       # dcc.Input wraps the <input> in a div
        field.click()
        pg.wait_for_function("window.Map3D.peaks().length > 0", timeout=30000)
        field.fill("Cervino")
        pg.wait_for_timeout(500)
        first = pg.locator(".gs-hit:not(.is-hidden) .gs-name").first.inner_text()
        check("search 'Cervino' finds the Matterhorn", first == "Matterhorn", first)
        planned = pg.evaluate("window.Map3D.peaks().find(e => e[1] === 'Matterhorn')[4]")
        field.press("Enter")
        pg.wait_for_function("new URLSearchParams(location.search).get('peak')", timeout=30000)
        pg.wait_for_timeout(3000)
        cam = pg.evaluate(CAMERA)
        off = ((cam["lng"] - planned[0]) * 76000) ** 2 + ((cam["lat"] - planned[1]) * 111320) ** 2
        check("camera lands on the summit", off < 25 and abs(cam["alt"] - planned[2]) < 5,
              f"{off ** 0.5:.1f} m off, {cam['alt']:.0f} m vs {planned[2]} m planned")
        check("wide view on the summit", abs(cam["fov"] - 60) < 1, f"{cam['fov']:.0f}°")
        check("search field shows the peak", "Matterhorn" in pg.locator(".gs-display").inner_text())
        check("'Leave summit' shown", pg.locator("#peak_exit").is_visible())

        field.evaluate("e => e.blur()")
        pg.keyboard.press("ArrowRight")
        pg.keyboard.press("+")
        pg.wait_for_timeout(800)
        cam2 = pg.evaluate(CAMERA)
        moved = ((cam2["lng"] - cam["lng"]) * 76000) ** 2 + ((cam2["lat"] - cam["lat"]) * 111320) ** 2
        check("arrow key turns, + zooms, the camera stays",
              abs(((cam2["bearing"] - cam["bearing"] + 540) % 360) - 180 - 5) < 1 and cam2["fov"] < cam["fov"]
              and moved < 1 and abs(cam2["alt"] - cam["alt"]) < 1,
              f"bearing {cam['bearing']:.0f}→{cam2['bearing']:.0f}°, fov {cam['fov']:.0f}→{cam2['fov']:.0f}°")

        pg.keyboard.press("Shift+ArrowUp")              # up to the horizon and above: no globe, no jump
        pg.keyboard.press("Shift+ArrowUp")
        pg.wait_for_timeout(800)
        check("looking up keeps the flat map", pg.evaluate("window._map3d.getZoom()") > 10,
              f"zoom {pg.evaluate('window._map3d.getZoom()'):.1f}")
        pg.click(".maplibregl-ctrl-compass", no_wait_after=True)
        pg.wait_for_timeout(2000)
        cam4 = pg.evaluate(CAMERA)
        check("compass returns to the summit's first view",
              abs(((cam4["bearing"] - cam["bearing"] + 540) % 360) - 180) < 1 and abs(cam4["fov"] - cam["fov"]) < 1,
              f"bearing {cam4['bearing']:.0f}°, fov {cam4['fov']:.0f}°")
        pg.locator("#peak_exit").click()
        pg.wait_for_timeout(4000)
        cam3 = pg.evaluate(CAMERA)
        check("leaving the summit", not pg.locator("#peak_exit").is_visible() and abs(cam3["fov"] - 36.87) < 1
              and "peak=" not in pg.evaluate("location.search"), f"fov {cam3['fov']:.0f}°")

        # a glacier from the search
        field.click()
        field.fill("Rhone")
        pg.wait_for_timeout(500)
        field.press("Enter")
        pg.wait_for_timeout(4000)
        check("search selects a glacier", "glacier=RGI" in pg.evaluate("location.search")
              and "Rhone" in pg.locator(".gs-display").inner_text(), pg.locator(".gs-display").inner_text())

        # the whole Alps: city labels
        pg.goto(url + "/?glacier=all")
        pg.wait_for_function("window._map3d && window._map3d.loaded()", timeout=60000)
        pg.wait_for_timeout(6000)
        cities = pg.locator(".city-name").all_inner_texts()
        check("city labels in the overview", len(cities) >= 15 and "Zürich" in cities, f"{len(cities)} cities")

        # German
        pg.goto(url + "/de")
        pg.wait_for_function("window._map3d && window._map3d.loaded()", timeout=60000)
        pg.wait_for_timeout(3000)
        check("German page", pg.locator(".gs-input input").get_attribute("placeholder").startswith("Gletscher")
              and pg.evaluate("document.documentElement.lang") == "de")

        check("no script errors", not errors, "; ".join(errors[:3]))
        b.close()

    print("all checks passed" if not check.failed else f"{check.failed} check(s) failed")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
