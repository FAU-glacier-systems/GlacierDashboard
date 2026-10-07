"""
The 3D Alps glacier dashboard, split by concern. glacier_dashboard_3d.py puts the parts together.

  config.py      paths, glacier list and names, properties, scenarios, years, page template
  store.py       glacier store (memory-mapped arrays), volume/area series, glaciers in a bounding box
  terrain.py     terrain tiles: open DEM with the model bedrock merged in, cached on disk
  colours.py     one colour scale per property, and its lookup table for the browser
  api.py         Flask routes for map3d.js: static files, terrain tiles, bedrock and ice blocks
  layout.py      page metadata, map configuration and the Dash layout
  clientside.py  callbacks that run in the browser (map, colour bar, numbers, timelapse, URL)
  callbacks.py   server callbacks (selection, settings from the URL, volume series)
"""
