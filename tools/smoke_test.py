"""
Smoke test of the 3D dashboard in a headless browser: run it against a local instance (or the live site) before
and after a restart. It checks what a visitor would notice first:

    page and map load without script errors, the version endpoint answers
    city labels in the overview, peak labels in the default (Aletsch) view
    search: a peak by another language's name ("Cervino"), Enter flies there (direction and tilt kept, the field
    cleared); a click on a peak label flies there too
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
                       ("/barrierefreiheit", "Gletschern und Gipfeln"), ("/accessibility", "glaciers and peaks")):
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

        # search a peak by its Italian name and fly there
        field = pg.locator(".gs-input input")       # dcc.Input wraps the <input> in a div
        field.click()
        pg.wait_for_function("window.Map3D.peaks().length > 0", timeout=30000)
        field.fill("Cervino")
        pg.wait_for_timeout(500)
        first = pg.locator(".gs-hit:not(.is-hidden) .gs-name").first.inner_text()
        check("search 'Cervino' finds the Matterhorn", first == "Matterhorn", first)
        top = pg.evaluate("window.Map3D.peaks().find(e => e[1] === 'Matterhorn')[4]")
        angle = pg.evaluate("[window._map3d.getBearing(), window._map3d.getPitch()]")
        field.press("Enter")
        pg.wait_for_timeout(4500)
        c = pg.evaluate("(() => { const m = window._map3d, c = m.getCenter(); return {lng: c.lng, lat: c.lat, b: m.getBearing(), p: m.getPitch()}; })()")
        off = (((c["lng"] - top[0]) * 76000) ** 2 + ((c["lat"] - top[1]) * 111320) ** 2) ** 0.5
        check("Enter flies to the peak, same angle", off < 200 and abs(c["b"] - angle[0]) < 0.5 and abs(c["p"] - angle[1]) < 0.5,
              f"{off:.0f} m off, bearing {angle[0]:.0f}→{c['b']:.0f}°, pitch {angle[1]:.0f}→{c['p']:.0f}°")
        check("no summit view left", "peak=" not in pg.evaluate("location.search")
              and not pg.locator("#peak_exit").count() and field.input_value() != "Cervino", field.input_value())

        # a click on a peak label flies there
        pg.wait_for_function("document.querySelectorAll('.peak-label').length > 0", timeout=30000)
        label = pg.locator(".peak-label").first
        name = label.locator(".peak-name").inner_text()
        top = pg.evaluate("(n) => window.Map3D.peaks().find(e => e[1] === n)[4]", name)
        angle = pg.evaluate("[window._map3d.getBearing(), window._map3d.getPitch()]")
        label.click()
        pg.wait_for_timeout(4500)
        c = pg.evaluate("(() => { const m = window._map3d, c = m.getCenter(); return {lng: c.lng, lat: c.lat, b: m.getBearing(), p: m.getPitch()}; })()")
        off = (((c["lng"] - top[0]) * 76000) ** 2 + ((c["lat"] - top[1]) * 111320) ** 2) ** 0.5
        check("a peak label flies there, same angle", off < 200 and abs(c["b"] - angle[0]) < 0.5 and abs(c["p"] - angle[1]) < 0.5,
              f"{name}, {off:.0f} m off")

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
