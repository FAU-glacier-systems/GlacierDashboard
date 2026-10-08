"""Callbacks that run in the browser: everything that changes per year (map, colour bar, numbers, timelapse),
plus the panels, the theme and the URL."""
from dash import ALL, Input, Output, State, clientside_callback

clientside_callback(
    "function(n, t) { return t === 'light' ? 'dark' : 'light'; }",
    Output("theme", "data"), Input("theme_toggle", "n_clicks"), State("theme", "data"),
    prevent_initial_call=True,
)

clientside_callback(
    """function(t) {
        t = (t === 'light') ? 'light' : 'dark';
        document.documentElement.dataset.theme = t;
        return t === 'dark' ? '☀' : '☾';
    }""",
    Output("theme_toggle", "children"), Input("theme", "data"),
)

# property (colour bar) and scenario menus: the button opens its menu, picking an option closes it and sets the store
for btn, menu, opt, store in (("cbar_btn", "prop_menu", "prop_opt", "property"), ("sc_btn", "sc_menu", "sc_opt", "scenario")):
    clientside_callback(
        f"""function(n, picks, cls) {{
            const open = window.dash_clientside.callback_context.triggered_id === '{btn}' && cls.includes('is-hidden');
            return open ? cls.replace(' is-hidden', '') : (cls.includes('is-hidden') ? cls : cls + ' is-hidden');
        }}""",
        Output(menu, "className"),
        Input(btn, "n_clicks"), Input({"type": opt, "index": ALL}, "n_clicks"), State(menu, "className"),
        prevent_initial_call=True,
    )
    clientside_callback(
        """function(picks) {
            const t = window.dash_clientside.callback_context.triggered_id;
            if (!t || !picks.some(n => n)) return window.dash_clientside.no_update;
            return t.index;
        }""",
        Output(store, "data", allow_duplicate=True),
        Input({"type": opt, "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )

# search: up to 5 matches of what is typed (glacier name or RGI ID, peak name in any of its languages; a peak's
# second line names the glacier it looks at) and how many more there are; names that start with it first,
# glaciers before peaks. Nothing is shown while the field holds the selected glacier's (or summit's) name.
clientside_callback(
    """function(text, ready, selected, peak, cfg) {
        const n = 5, q = (text || '').trim().toLowerCase();
        const peaks = window.Map3D ? window.Map3D.peaks() : [];   // map3d.js loads them; [] until then
        const name = (r) => { const o = cfg.search.find(e => e[0] === r); return o ? (o[1] || r) : ''; };
        const active = q !== '' && !(peak ? text === peak.name : selected && text === name(selected));
        let hits = [];
        if (active) {
            // [id, name, second line, name starts with q]
            const g = cfg.search.filter(([r, nm]) => nm.toLowerCase().includes(q) || r.toLowerCase().includes(q))
                .map(([r, nm]) => [r, nm || r, nm ? r : '', nm.toLowerCase().startsWith(q)]);
            const p = peaks.filter(e => e[8].includes(q))
                .map(e => [e[0], e[1], [cfg.t.peak, e[2] != null ? `${e[2]} m` : '', e[6]].filter(x => x).join(' · '),
                           e[8].split(' / ').some(nm => nm.startsWith(q))]);
            hits = [...g.filter(h => h[3]), ...p.filter(h => h[3]), ...g.filter(h => !h[3]), ...p.filter(h => !h[3])];
        }
        const shown = hits.slice(0, n), more = hits.length - shown.length;
        const slot = (f) => Array.from({length: n}, (_, i) => i < shown.length ? f(shown[i]) : '');
        const note = more > 0 ? `${more} ${cfg.t.more}` : (active && !shown.length ? cfg.t.none : '');
        return [shown.map(h => h[0]), slot(h => h[1]), slot(h => h[2]),
                Array.from({length: n}, (_, i) => 'gs-hit' + (i < shown.length ? '' : ' is-hidden')),
                note, 'gs-results' + (active ? '' : ' is-empty')];
    }""",
    Output("glacier_hits", "data"),
    Output({"type": "ghit_name", "index": ALL}, "children"), Output({"type": "ghit_id", "index": ALL}, "children"),
    Output({"type": "ghit", "index": ALL}, "className"),
    Output("glacier_more", "children"), Output("glacier_results", "className"),
    Input("glacier_search", "value"), Input("peaks_ready", "data"), State("selected_rgi", "data"),
    State("peak_sel", "data"), State("map_config", "data"),
)

# pick a result (click, or Enter for the first one) or clear (×). A glacier is selected; a peak only puts the
# camera on its summit (map3d.js, which also sets peak_sel). × on a summit leaves it, otherwise clears the
# selection.
clientside_callback(
    """function(clicks, submits, cleared, hits, peakSel) {
        const t = window.dash_clientside.callback_context.triggered_id, nu = window.dash_clientside.no_update;
        let id;
        if (t === 'glacier_clear') {
            if (peakSel) { if (window.Map3D) window.Map3D.leavePeak(); return nu; }
            id = null;
        }
        else if (t === 'glacier_search') id = (hits || [])[0];
        else if (t && t.type === 'ghit' && clicks[t.index]) id = (hits || [])[t.index];
        if (id === undefined) return nu;
        if (document.activeElement) document.activeElement.blur();   // closes the results
        if (id && window.Map3D && window.Map3D.peaks().some(e => e[0] === id)) {
            window.Map3D.flyToPeak(id);
            return nu;
        }
        return {rgi: id, t: Date.now()};
    }""",
    Output("rgi_select", "data"),
    Input({"type": "ghit", "index": ALL}, "n_clicks"), Input("glacier_search", "n_submit"),
    Input("glacier_clear", "n_clicks"), State("glacier_hits", "data"), State("peak_sel", "data"),
    prevent_initial_call=True,
)

# the field shows the peak one stands on, else the selected glacier; × leaves the summit or clears the selection
clientside_callback(
    """function(selected, peak, cfg) {
        const o = selected ? cfg.search.find(e => e[0] === selected) : null;
        const name = peak ? peak.name : (o ? (o[1] || o[0]) : ''), sub = peak ? peak.sub : (o && o[1] ? o[0] : '');
        return [name, 'gs-clear' + (selected || peak ? '' : ' is-hidden'), name, sub];
    }""",
    Output("glacier_search", "value"), Output("glacier_clear", "className"),
    Output("glacier_display_name", "children"), Output("glacier_display_id", "children"),
    Input("selected_rgi", "data"), Input("peak_sel", "data"), State("map_config", "data"),
)

# on a summit: the "Leave summit" button, which flies back to the glacier (or the whole Alps)
clientside_callback(
    "function(peak) { return 'peak-exit' + (peak ? '' : ' is-hidden'); }",
    Output("peak_exit", "className"), Input("peak_sel", "data"),
)
clientside_callback(
    """function(n) {
        if (n && window.Map3D) window.Map3D.leavePeak();
        return window.dash_clientside.no_update;
    }""",
    Output("peak_exit", "title"), Input("peak_exit", "n_clicks"),
    prevent_initial_call=True,
)

# while the field holds that name, it is shown over the field like a search result: name and RGI ID, or name and
# "Peak · height · glacier"
clientside_callback(
    """function(text, selected, peak, cfg) {
        const o = selected ? cfg.search.find(e => e[0] === selected) : null;
        const shown = peak ? peak.name : (o ? (o[1] || o[0]) : null);
        return 'glacier-search' + (shown && text === shown ? ' has-sel' : '');
    }""",
    Output("glacier_box", "className"),
    Input("glacier_search", "value"), Input("selected_rgi", "data"), Input("peak_sel", "data"),
    State("map_config", "data"),
)

clientside_callback(
    """function(glacier, scenario, property, year) {
        const p = new URLSearchParams();
        p.set('glacier', glacier || 'all');
        if (scenario) p.set('scenario', scenario);
        if (property) p.set('property', property);
        if (year) p.set('year', year);
        const now = new URLSearchParams(window.location.search);   // the camera and summit, kept by map3d.js
        for (const k of ['view', 'peak']) if (now.get(k)) p.set(k, now.get(k));
        window.history.replaceState(window.history.state, '', window.location.pathname + '?' + p.toString());
        return window.dash_clientside.no_update;
    }""",
    Output("url_sync", "data"),
    Input("selected_rgi", "data"), Input("scenario", "data"), Input("property", "data"),
    Input("year_slider", "value"),
    prevent_initial_call=True,
)

# map and colour bar
clientside_callback(
    """function(rgi, scenario, variable, year, theme, stopped, cfg) {
        const nu = window.dash_clientside.no_update;
        if (!scenario || !variable || year == null) return nu;
        theme = theme === 'light' ? 'light' : 'dark';
        document.documentElement.dataset.prop = variable;     // style3d.css colours the year slider by property
        if (window.Map3D) window.Map3D.render({rgi, scenario, variable, year, theme, playing: !stopped}, cfg);
        const v = cfg.vars[variable], lut = v.lut, stops = [];
        for (let i = 0; i <= 8; i++) { const k = Math.round(i / 8 * 255) * 3; stops.push(`rgb(${lut[k]},${lut[k+1]},${lut[k+2]})`); }
        const open = (variable === 'thk' || variable === 'velsurf_mag') ? '+' : '';
        const H = (type, props) => ({namespace: 'dash_html_components', type, props});
        const bar = [
            H('Div', {className: 'cbar-ticks', children: [
                H('Span', {children: String(v.lo).replace('.', cfg.t.decimal)}),
                H('Span', {className: 'cbar-label', children: v.label}),
                H('Span', {children: String(v.hi).replace('.', cfg.t.decimal) + open})]}),
            H('Div', {className: 'cbar-ramp', style: {background: `linear-gradient(to right, ${stops.join(', ')})`}}),
        ];
        return bar;
    }""",
    Output("colourbar", "children"),
    Input("selected_rgi", "data"), Input("scenario", "data"), Input("property", "data"),
    Input("year_slider", "value"), Input("theme", "data"), Input("timelapse_interval", "disabled"),
    State("map_config", "data"),
)

# timelapse: the next year as soon as the map has drawn the current one
clientside_callback(
    """function(n, year, cfg) {
        if (window.Map3D && window.Map3D.busy()) return window.dash_clientside.no_update;
        return year >= cfg.years[1] ? cfg.years[0] : year + 1;
    }""",
    Output("year_slider", "value", allow_duplicate=True),
    Input("timelapse_interval", "n_intervals"), State("year_slider", "value"), State("map_config", "data"),
    prevent_initial_call=True,
)

clientside_callback(
    """function(n, stopped, cfg) {
        const H = (type, props) => ({namespace: 'dash_html_components', type, props});
        const [icon, word] = stopped ? ['⏸', cfg.t.pause] : ['▶', cfg.t.play];
        return [!stopped, [H('Span', {className: 'play-icon', children: icon}),
                           H('Span', {className: 'play-word', children: word})]];
    }""",
    Output("timelapse_interval", "disabled"), Output("btn_timelapse", "children"),
    Input("btn_timelapse", "n_clicks"), State("timelapse_interval", "disabled"), State("map_config", "data"),
    prevent_initial_call=True,
)

clientside_callback(
    "function(year) { return year == null ? window.dash_clientside.no_update : String(year); }",
    Output("year_label", "children"), Input("year_slider", "value"),
)

# the scenario switch and its menu: the selection's area in the displayed year (the share of 2000 in the tooltip)
clientside_callback(
    """function(series, scenario, year, cfg) {
        const keys = Object.keys(cfg.scenario_labels), n = keys.length;
        const H = (type, props) => ({namespace: 'dash_html_components', type, props});
        const cls = keys.map(k => 'sc-opt' + (k === scenario ? ' is-sel' : ''));
        const label = cfg.scenario_labels[scenario] || '';
        if (!series || year == null) {
            return [cls, Array(n).fill(''), Array(n).fill(''),
                    [H('Strong', {children: label}), H('Span', {className: 'sc-btn-chevron', children: '▾'})]];
        }
        const data = series.area, unit = 'km²';
        const y0 = cfg.years[0], k = Math.min(Math.max(year - y0, 0), data[0].length - 1);
        const t = cfg.t, dec = (x) => x.replace('.', t.decimal);
        const fmt = (v) => dec(v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v >= 1 ? v.toFixed(2) : v.toPrecision(2));
        const pct = (v, ref) => !(ref > 0) ? '–' : v <= 0 ? t.gone : v / ref < 0.01 ? '<1 %' : Math.round(v / ref * 100) + ' %';
        const val = keys.map((_, i) => fmt(data[i][k]) + '\\u202f' + unit);
        const share = keys.map((_, i) => pct(data[i][k], data[i][0]));
        const si = keys.indexOf(scenario);
        return [cls, val,
                keys.map((key, i) => `${cfg.scenario_labels[key]}: ${val[i]} ${t.in} ${year}, ${share[i]} ${t.of} ${y0}`),
                [H('Strong', {children: label}), H('Span', {className: 'sc-btn-value', children: si >= 0 ? val[si] : ''}),
                 H('Span', {className: 'sc-btn-chevron', children: '▾'})]];
    }""",
    Output({"type": "sc_opt", "index": ALL}, "className"),
    Output({"type": "sc_val", "index": ALL}, "children"),
    Output({"type": "sc_opt", "index": ALL}, "title"),
    Output("sc_btn", "children"),
    Input("series_data", "data"), Input("scenario", "data"),
    Input("year_slider", "value"), State("map_config", "data"),
)
