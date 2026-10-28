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

# search: below the empty field the largest glaciers; below a typed one every match (glacier name or RGI ID, peak
# name in any of its languages; a peak's second line names the glacier it looks at), in a list that scrolls, names
# that start with it first, glaciers before peaks; an icon tells glaciers from peaks. Nothing is shown while the
# field holds the selected glacier's name.
clientside_callback(
    """function(text, ready, selected, cfg) {
        const q = (text || '').trim().toLowerCase();
        const peaks = window.Map3D ? window.Map3D.peaks() : [];   // map3d.js loads them; [] until then
        const name = (r) => { const o = cfg.search.find(e => e[0] === r); return o ? (o[1] || r) : ''; };
        const typed = q !== '', active = !(selected && text === name(selected));
        // [id, name, second line, name starts with q, is a peak]
        let hits = [];
        if (active && typed) {
            const g = cfg.search.filter(([r, nm]) => nm.toLowerCase().includes(q) || r.toLowerCase().includes(q))
                .map(([r, nm]) => [r, nm || r, nm ? r : '', nm.toLowerCase().startsWith(q), false]);
            const p = peaks.filter(e => e[8].includes(q))
                .map(e => [e[0], e[1], [e[2] != null ? `${e[2]} m` : '', e[6]].filter(x => x).join(' · '),
                           e[8].split(' / ').some(nm => nm.startsWith(q)), true]);
            hits = [...g.filter(h => h[3]), ...p.filter(h => h[3]), ...g.filter(h => !h[3]), ...p.filter(h => !h[3])];
        } else if (active) {
            hits = (cfg.largest || []).map(r => [r, name(r), name(r) !== r ? r : '', true, false]);
        }
        const el = (type, props) => ({type, namespace: 'dash_html_components', props});
        const rows = hits.map((h, i) => el('Div', {
            id: {type: 'ghit', index: i}, n_clicks: 0, role: 'option', tabIndex: '-1',
            className: 'gs-hit ' + (h[4] ? 'is-peak' : 'is-glacier'),
            children: [el('Span', {className: 'gs-icon', title: h[4] ? cfg.t.peak : cfg.t.glacier}),
                       el('Span', {className: 'gs-name', children: h[1]}),
                       el('Span', {className: 'gs-id', children: h[2]})]}));
        if (active && !typed && rows.length) rows.unshift(el('Div', {className: 'gs-note', children: cfg.t.largest}));
        if (active && typed && !hits.length) rows.push(el('Div', {className: 'gs-note', children: cfg.t.none}));
        return [hits.map(h => h[0]), rows, 'gs-results' + (rows.length ? '' : ' is-empty')];
    }""",
    Output("glacier_hits", "data"),
    Output("glacier_results", "children"), Output("glacier_results", "className"),
    Input("glacier_search", "value"), Input("peaks_ready", "data"), State("selected_rgi", "data"),
    State("map_config", "data"),
)

# pick a result (click, or Enter for the first one) or clear (×). A glacier is selected; a peak only flies the
# camera there (map3d.js). × clears the selection.
clientside_callback(
    """function(clicks, submits, cleared, hits, text) {
        const t = window.dash_clientside.callback_context.triggered_id, nu = window.dash_clientside.no_update;
        let id;
        if (t === 'glacier_clear') id = null;
        else if (t === 'glacier_search') id = (text || '').trim() ? (hits || [])[0] : undefined;   // not the largest
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
    Input("glacier_clear", "n_clicks"), State("glacier_hits", "data"), State("glacier_search", "value"),
    prevent_initial_call=True,
)

# the field shows the selected glacier; × clears the selection
clientside_callback(
    """function(selected, cfg) {
        const o = selected ? cfg.search.find(e => e[0] === selected) : null;
        const name = o ? (o[1] || o[0]) : '', sub = o && o[1] ? o[0] : '';
        return [name, 'gs-clear' + (selected ? '' : ' is-hidden'), name, sub];
    }""",
    Output("glacier_search", "value"), Output("glacier_clear", "className"),
    Output("glacier_display_name", "children"), Output("glacier_display_id", "children"),
    Input("selected_rgi", "data"), State("map_config", "data"),
)

# while the field holds that name, it is shown over the field like a search result: name and RGI ID
clientside_callback(
    """function(text, selected, cfg) {
        const o = selected ? cfg.search.find(e => e[0] === selected) : null;
        const shown = o ? (o[1] || o[0]) : null;
        return 'glacier-search' + (shown && text === shown ? ' has-sel' : '');
    }""",
    Output("glacier_box", "className"),
    Input("glacier_search", "value"), Input("selected_rgi", "data"),
    State("map_config", "data"),
)

clientside_callback(
    """function(glacier, scenario, property, year) {
        const p = new URLSearchParams();
        p.set('glacier', glacier || 'all');
        if (scenario) p.set('scenario', scenario);
        if (property) p.set('property', property);
        if (year) p.set('year', year);
        const view = new URLSearchParams(window.location.search).get('view');   // the camera, kept by map3d.js
        if (view) p.set('view', view);
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

# the year in the title; the slider's dot takes the year's colour on the climate-stripes ramp (the stops of the
# slider in style3d.css), its filled part the share of the ramp up to the year
clientside_callback(
    """function(year, cfg) {
        if (year == null) return window.dash_clientside.no_update;
        const stops = ['#08306b', '#2171b5', '#6baed6', '#deebf7', '#fcbba1', '#fb6a4a', '#cb181d', '#67000d']
            .map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)));
        const frac = Math.min(Math.max((year - cfg.years[0]) / (cfg.years[1] - cfg.years[0]), 0), 1);
        const f = frac * (stops.length - 1);
        const i = Math.min(Math.floor(f), stops.length - 2), t = f - i;
        const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * t));
        document.documentElement.style.setProperty('--year-colour', `rgb(${c.join(',')})`);
        document.documentElement.style.setProperty('--year-frac', Math.max(frac, 0.001));
        return String(year);
    }""",
    Output("year_label", "children"), Input("year_slider", "value"), State("map_config", "data"),
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
                [H('Strong', {children: label}), H('Span', {className: 'sc-btn-value', children: si >= 0 ? val[si] : '',
                                               title: `${t.area_tip} ${year}`}),
                 H('Span', {className: 'sc-btn-chevron', children: '▾'})]];
    }""",
    Output({"type": "sc_opt", "index": ALL}, "className"),
    Output({"type": "sc_val", "index": ALL}, "children"),
    Output({"type": "sc_opt", "index": ALL}, "title"),
    Output("sc_btn", "children"),
    Input("series_data", "data"), Input("scenario", "data"),
    Input("year_slider", "value"), State("map_config", "data"),
)
