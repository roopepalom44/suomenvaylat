from pathlib import Path
from qgis.core import QgsApplication, QgsProject
QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()
from suomenvaylat_qgis.services import catalog, selection_geometry, download, _open_remote_layer
from qgis.core import QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry
entries, errors = catalog('Väylä')
assert entries and not errors
mask, crs = selection_geometry('Kunta/Kaupunki', ['Helsinki'])
layer = _open_remote_layer(entries[0])
source_mask = QgsGeometry(mask)
source_mask.transform(QgsCoordinateTransform(crs, layer.crs(), QgsProject.instance()))
request = QgsFeatureRequest().setFilterRect(source_mask.boundingBox()).setLimit(5)
print('debug', layer.crs().authid(), source_mask.boundingBox().toString(),
      [(f.id(), f.geometry().intersects(source_mask)) for f in layer.getFeatures(request)], flush=True)
path = Path(__file__).parent / 'smoke_vayla.gpkg'
path.unlink(missing_ok=True)
try:
    count = download(entries[0], mask, crs, path)
    assert count > 0
    print('download passed', count, flush=True)
    kapsi = next(entry for entry in catalog('Kapsi')[0]
                 if entry['id'] == 'taustakartta' and entry['endpoint'].endswith('/taustakartta'))
    from qgis.core import QgsGeometry, QgsRectangle
    small = QgsGeometry.fromRect(QgsRectangle(385000, 6670000, 386000, 6671000))
    raster_path = Path(__file__).parent / 'smoke_kapsi.tif'
    raster_path.unlink(missing_ok=True)
    assert download(kapsi, small, crs, raster_path) == 1
    print('Kapsi raster passed', flush=True)
    osm = next(entry for entry in catalog('OpenStreetMap')[0] if entry['id'] == 'osm_bus_stops')
    osm_path = Path(__file__).parent / 'smoke_osm.gpkg'
    osm_path.unlink(missing_ok=True)
    assert download(osm, small, crs, osm_path) > 0
    print('OSM passed', flush=True)
finally:
    QgsProject.instance().removeAllMapLayers()
