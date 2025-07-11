import xarray as xr
import numpy as np
import plotly.graph_objects as go
import socket
import qrcode

from dash import Dash, dcc, html, Input, Output, State

app = Dash()

# Glacier metadata
df = {
    'Name': ['Aletsch_Glacier', 'Rhone_Glacier', 'Mer_de_Glace', 'Schiaparelli',
             'Kongsbreen', 'Engabreen', 'Khumbu', 'Perito_Moreno'],
    'Latitude': [46.416667, 46.601111, 45.923333, -54.397508376742614, 78.90030,
                 66.68376269205591, 27.985306612615847, -50.50424],
    'Longitude': [8.083333, 8.378056, 6.930556, -70.87014236561915, 13.06251,
                  13.770939474973, 86.84193314917492, -73.1328],
    'Color': ['#ABE2FB' for _ in range(20)]
}

map_figure = go.Figure(
    data=go.Scattergeo(
        lat=df['Latitude'], lon=df['Longitude'], text=df['Name'],
        marker_color=df['Color'],
        mode='markers', marker=dict(size=20)
    ),
    layout=dict(
        geo=dict(
            oceancolor="white", landcolor="black", showcountries=True,
            countrycolor="gray", showocean=True, showland=True,
            showlakes=False, lakecolor="blue",
            showrivers=False, rivercolor="blue", projection_type="orthographic"
        ),
        margin=dict(l=10, r=10, b=10, t=10),
        mapbox=dict(style="open-street-map")
    )
)

# App layout
app.layout = html.Div([
    dcc.Dropdown(['Thickness (m)', 'Velocity (m/a)', 'Surface Mass Balance (m/a)'],
                 'Thickness (m)', id='property', searchable=False, clearable=False,
                 style={'font-family': 'monospace', 'width': '100%', 'marginBottom': '10px'}),

    dcc.Slider(
        id='year_slider',
        step=10,
        value=2000,
        min=2000,
        max=2100,
        drag_value=2000,
        included=False,
        marks={int(i): {'label': str(i), 'style': {'font-family': 'monospace', 'fontSize': '10px'}} for i in np.arange(2000, 2101, 10)},
        tooltip={'always_visible': False},
        updatemode='drag'
    ),

    dcc.Graph(figure=go.Figure(), id='mnt_surface',
              style=dict(width='100%', height='60vh', marginBottom='20px'),
              config=dict(displayModeBar=False)),

    html.Div([
        dcc.Graph(id='map', figure=map_figure,
                  style=dict(width='100%', height='40vh'),
                  config=dict(displayModeBar=False),
                  clickData={'points': [{'text': 'Aletsch_Glacier', 'lat': 46.416667, 'lon': 8.083333}]}),

        html.Div([
            html.Img(src='/assets/hawkinstick.svg', style=dict(width='70%', height='auto')),
            dcc.Slider(0, 4, 1, value=1, id='temp_slider', vertical=True,
                       included=True,
                       marks={i: {'label': f'+{i}.0°C', 'style': {'font-family': 'monospace'}} for i in range(5)},
                       tooltip={'always_visible': False})
        ], style=dict(display='flex', flexDirection='column', alignItems='center', width='100%')),
    ], style=dict(display='flex', flexDirection='column', width='100%')),

    dcc.RadioItems(
        id='volume_mode',
        options=[
            {'label': ' Absolute Volume', 'value': 'absolute'},
            {'label': ' Relative Change (%)', 'value': 'relative'},
        ],
        value='absolute',
        labelStyle={'display': 'inline-block', 'margin-right': '20px'},
        inputStyle={'margin-right': '5px'},
        style={'font-family': 'monospace', 'margin': '10px'}
    ),

    dcc.Graph(id='volume_graph',
              style=dict(width='100%', height='25vh', backgroundColor='lightgray', marginTop='10px'),
              config=dict(displayModeBar=False))
], style={'padding': '10px', 'maxWidth': '100%', 'overflowX': 'hidden'})

# Glacier rendering callback
@app.callback(
    Output("mnt_surface", "figure"),
    Input("year_slider", 'drag_value'),
    Input('map', 'clickData'),
    Input('temp_slider', 'value'),
    Input('property', 'value'),
    State("mnt_surface", "relayoutData")
)
def get_fig(year, map_click, temp, property, layout):
    if not map_click:
        return go.Figure()

    glacier = map_click['points'][0]['text']

    try:
        camera = layout['scene.camera']
    except:
        camera = dict(eye=dict(x=0, y=0, z=1.5), up=dict(x=0, y=0, z=1), center=dict(x=0, y=0, z=0))

    with xr.open_dataset(f"data/{glacier}/outputs/output_{temp}.nc") as ds:
        bedrock = ds["topg"].isel(time=0).values
        time = ds["time"].values
        lat_range = np.arange(len(ds["x"])) * 100
        lon_range = np.arange(len(ds["y"])) * 100
        glacier_surfaces = ds["usurf"].values
        thickness = ds["thk"].values
        velocities = ds["velsurf_mag"].values
        smbs = ds["smb"].values

    if property == "Surface Mass Balance (m/a)":
        property_maps = smbs
        color_scale = "rdbu"
        max_property_map = 10
        min_property_map = -10.1
    elif property == "Velocity (m/a)":
        property_maps = velocities
        color_scale = "magma"
        max_property_map = np.max(property_maps)
        min_property_map = np.min(property_maps)
    else:
        property_maps = thickness
        color_scale = "Blues"
        max_property_map = 500
        min_property_map = 0

    year_index = int((year - time[0]) / 10)
    property_map = property_maps[year_index]
    glacier_surface = glacier_surfaces[year_index]
    glacier_surface[thickness[year_index] < 1] = None

    min_bedrock = np.min(bedrock)
    bedrock[[0, -1], :] = min_bedrock
    bedrock[:, [0, -1]] = min_bedrock

    bedrock_plot = go.Surface(z=bedrock, name='bedrock', showlegend=False,
                              colorscale='gray', x=lat_range, y=lon_range, showscale=False)

    glacier_plot = go.Surface(z=glacier_surface, name='glacier', showlegend=False,
                              colorscale=color_scale, x=lat_range, y=lon_range,
                              showscale=True, cmin=min_property_map, cmax=max_property_map,
                              surfacecolor=property_map,
                              colorbar=dict(orientation="h", thickness=5, ypad=0))

    resolution = int(lat_range[1] - lat_range[0])
    ratio_y = bedrock.shape[0] / bedrock.shape[1]
    ratio_z = (np.max(bedrock) - np.min(bedrock)) / (bedrock.shape[0] * resolution)
    ratio_z *= 1.5

    fig = go.Figure(data=[bedrock_plot, glacier_plot],
                    layout=dict(scene=dict(aspectratio=dict(x=1, y=ratio_y, z=ratio_z),
                                            zaxis=dict(visible=False, showbackground=False),
                                            xaxis=dict(visible=False, showbackground=False),
                                            yaxis=dict(visible=False, showbackground=False),
                                            camera=camera),
                                margin=dict(l=0, r=0, b=0, t=0)))
    return fig

# Volume graph callback
@app.callback(
    Output("volume_graph", "figure"),
    Input('map', 'clickData'),
    Input('temp_slider', 'value'),
    Input('volume_mode', 'value')
)
def update_volume_graph(map_click, temp, mode):
    if not map_click:
        return go.Figure()

    glacier = map_click['points'][0]['text']

    try:
        with xr.open_dataset(f"data/{glacier}/outputs/output_{temp}.nc") as ds:
            thickness = ds["thk"].values
            times = ds["time"].values
    except FileNotFoundError:
        return go.Figure()

    area_per_cell = 250
    volume = np.sum(thickness, axis=(1, 2)) * area_per_cell

    if mode == 'relative':
        base_volume = volume[0]
        volume_display = ((volume - base_volume) / base_volume) * 100
        y_title = "Relative Change (%)"
        line_color = 'black'
    else:
        volume_display = volume / 1e6  # Convert to million m^3
        y_title = "Volume (Million m³)"
        line_color = 'black'

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=volume_display, mode='lines+markers',
                             name='Volume',
                             line=dict(color=line_color, shape='spline'),
                             marker=dict(color=line_color)))
    fig.update_layout(
        margin=dict(l=10, r=10, t=60, b=10),
        title="Glacier Ice Volume Over Time",
        xaxis=dict(title="Year", tickmode='array', tickvals=[int(t) for t in times if int(t) % 10 == 0],
                   showline=True, linewidth=1, linecolor='black'),
        yaxis=dict(title=y_title, showline=True, linewidth=1, linecolor='black'),
        font=dict(family="monospace"),
        plot_bgcolor='lightgray',
        paper_bgcolor='lightgray'
    )
    return fig

# Replace the run command to be accessible on mobile via IP
if __name__ == '__main__':
    local_ip = socket.gethostbyname(socket.gethostname())
    url = f"http://{local_ip}:8050"
    img = qrcode.make(url)
    img.show()
    print(f"App is available at {url}")
    app.run(debug=False, host='0.0.0.0', port=8050)