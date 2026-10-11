# -*- coding: utf-8 -*-
"""ArcGIS Python toolbox entrypoint. Implementation is a regular Python module."""
import importlib.util
import os

_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arcgis_downloader.py")
_spec = importlib.util.spec_from_file_location("suomenvaylat_arcgis", _path)
implementation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(implementation)
# Also expose helper names for existing ArcGIS smoke scripts.
globals().update({name: value for name, value in vars(implementation).items()
                  if not name.startswith("__")})
