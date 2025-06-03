import xarray as xr
import numpy as np

from dash import Dash, dcc, html, Input, Output, State

app = Dash()

# Creating the DataFrame
df = {
    'Name': ['Aletsch_Glacier', 'Rhone_Glacier', 'Mer_de_Glace', 'Schiaparelli',
             'Kongsbreen', 'Engabreen', 'Khumbu', 'Perito_Moreno'],
    'Latitude': [46.416667, 46.601111, 45.923333, -54.397508376742614, 78.90030,
                 66.68376269205591, 27.985306612615847, -50.50424],
    'Longitude': [8.083333, 8.378056, 6.930556, -70.87014236561915, 13.06251,
                  13.770939474973, 86.84193314917492, -73.1328],
    'Color': ['#ABE2FB' for i in range(20)]
    # 'Color':  [ matplotlib.colors.rgb2hex(plt.get_cmap('tab20')(i))for i in range(30)]

}
import plotly.graph_objects as go

map_figure = go.Figure(
    data=go.Scattergeo(
        lat=df['Latitude'], lon=df['Longitude'], text=df['Name'],
        marker_color=df['Color'],
        mode='markers', marker=dict(size=20)
    ),
    layout=dict(
        geo=dict(
            oceancolor="white", landcolor="black", showcountries=True,
            countrycolor="gray",
            showocean=True, showland=True, showlakes=False, lakecolor="blue",
            showrivers=False, rivercolor="blue", projection_type="orthographic"
        ),
        margin=dict(l=10, r=10, b=10, t=10),
        mapbox=dict(style="open-street-map", )
    )
)

# App layout
app.layout = html.Div([
    dcc.Dropdown(['Thickness (m)', 'Velocity (m/a)', 'Surface Mass Balance (m/a)'],
                 'Thickness (m)', id='property', searchable=False, clearable=False,
                 style={
                     # Font size for the dropdown text
                     'font-family': 'monospace',  # Font family for the dropdown text
                     # Optional: margin to separate from other elements
                 }),
    dcc.Graph(figure=go.Figure(), id='mnt_surface',
              style=dict(width='100%', height='50vh'),
              config=dict(displayModeBar=False)),

    dcc.Slider(id='year_slider',
               step=10, value=2000, min=2000, max=2100,
               drag_value=2000,
               included=False,
               marks={int(i): {'label': str(i), 'style':
                   {'font-family': 'monospace'}} for i in
                      np.arange(2000, 2101, 10)}),

    html.Div(
        children=[
            html.Div(
                children=[dcc.Graph(id='map', figure=map_figure,
                                    style=dict(width='40vw',
                                               height='min(40vw, 40vh)'),

                                    config=dict(displayModeBar=False),
                                    clickData={'points': [
                                        {'curveNumber': 0, 'pointNumber': 0,
                                         'pointIndex': 0, 'lon': 8.083333,
                                         'lat': 46.416667, 'location': None,
                                         'text': 'Aletsch_Glacier',
                                         'marker.color': '#1f77b4',
                                         'bbox': {'x0': 288.37085438689746,
                                                  'x1': 308.37085438689746,
                                                  'y0': 196.96423191495717,
                                                  'y1': 216.96423191495717}}]}), ],
                style=dict(width='40vw', height='min(40vw, 40vh)'),
                # Flex-grow property
            ),
            html.Div(
                children=[html.Img(src='/assets/hawkinstick.svg', style=dict(
                    width='40vw',
                    height='min(40vw, 40vh)')),
                          dcc.Slider(0, 4, 1, value=1, id='temp_slider',
                                     vertical=True,
                                     included=True, verticalHeight=None,
                                     marks={0: {'label': '+0.0°C', 'style': {
                                         'font-family': 'monospace'}},
                                            1: {'label': '+1.0°C', 'style': {
                                                'font-family': 'monospace'}},
                                            2: {'label': '+2.0°C', 'style': {
                                                'font-family': 'monospace'}},
                                            3: {'label': '+3.0°C', 'style': {
                                                'font-family': 'monospace'}},
                                            4: {'label': '+4.0°C', 'style': {
                                                'font-family': 'monospace'}}})
                          ],
                style=dict(display='flex', flexDirection='row', width='50vw',
                           height='min(40vw, 40vh)', )
                # Flex-grow property
            ),

        ],
        style=dict(display='flex', flexDirection='row',
                   width='100vw',
                   height='100vh')  # Flex container styles
    )

])


@app.callback(
    Output("mnt_surface", "figure"),
    Input("year_slider", 'drag_value'),
    Input('map', 'clickData'),
    Input('temp_slider', 'value'),
    Input('property', 'value'),
    State("mnt_surface", "relayoutData"),
)
def get_fig(year, map_click, temp, property, layout):
    if map_click != None:
        glacier = map_click['points'][0]['text']
    else:
        return

    try:
        camera = layout['scene.camera']
        print(camera)
    except:
        camera = dict(
            eye=dict(x=0, y=0, z=1.5),  # Set the initial camera eye position
            up=dict(x=0, y=0, z=1),  # Set the up direction
            center=dict(x=0, y=0, z=0)  # Set the center position
        )

    with xr.open_dataset(f"data/{glacier}/outputs/output_{temp}.nc") as ds:
        # Extract variables
        bedrock = ds["topg"].isel(
            time=0).values  # .isel(time=0) for selecting the first time slice
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

    i = int((year - time[0]) / 10)

    year_index = int((year - time[0]) / 10)
    property_map = property_maps[year_index]
    glacier_surface = glacier_surfaces[year_index]
    glacier_surface[thickness[year_index] < 1] = None

    min_bedrock = np.min(bedrock)
    bedrock[[0, -1], :] = min_bedrock
    bedrock[:, [0, -1]] = min_bedrock

    # create surface plot
    bedrock_plot = go.Surface(z=bedrock, name='bedrock', showlegend=False,
                              colorscale='gray',
                              x=lat_range, y=lon_range, showscale=False,
                              # colorbar=dict(title='Elevation (m)',
                              #               titleside="right",
                              #               orientation="h",
                              #               ),
                              )

    glacier_plot = go.Surface(z=glacier_surface, name='glacier', showlegend=False,
                              colorscale=color_scale,
                              x=lat_range, y=lon_range, showscale=True,
                              cmin=min_property_map, cmax=max_property_map,
                              surfacecolor=property_map,
                              colorbar=dict(
                                  # title='Thickness (m)',
                                  # titleside="top",
                                  orientation="h",
                                  thickness=5,
                                  ypad=0
                              )
                              )

    resolution = int(lat_range[1] - lat_range[0])
    ratio_y = bedrock.shape[0] / bedrock.shape[1]
    ratio_z = (np.max(bedrock) - np.min(bedrock)) / (bedrock.shape[0] * resolution)
    ratio_z *= 1.5

    fig = go.Figure(data=[bedrock_plot, glacier_plot],
                    layout=dict(legend=dict(orientation="h", yanchor="bottom",
                                            xanchor="left"),
                                scene=dict(
                                    aspectratio=dict(x=1, y=ratio_y, z=ratio_z),
                                    zaxis=dict(visible=False,
                                               showbackground=False,
                                               range=[np.min(bedrock),
                                                      np.max(bedrock)]),
                                    xaxis=dict(visible=False,
                                               showbackground=False,
                                               range=[lat_range[0], lat_range[-1]],
                                               title="[m]"),
                                    yaxis=dict(visible=False,
                                               showbackground=False,
                                               range=[lon_range[0], lon_range[-1]],
                                               title="[m]"),
                                    camera=camera
                                ),
                                margin=dict(l=0, r=0, b=0, t=0)
                                )
                    )

    return fig


if __name__ == '__main__':
    app.run_server(debug=False, host='0.0.0.0', port=8050)
