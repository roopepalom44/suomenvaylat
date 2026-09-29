from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()
assert QgsApplication.authManager().setMasterPassword('smoke-pass', True)
import suomenvaylat_qgis.plugin as plugin_module
from suomenvaylat_qgis.plugin import SuomenvaylatDialog
from qgis.PyQt.QtWidgets import QMessageBox
requested_urls = []
def fake_tilejson(url, key):
    requested_urls.append(url)
    return {'tiles': [url.replace('tilejson.json', '{z}/{x}/{y}.pbf')]}
plugin_module._request_json = fake_tilejson
QMessageBox.information = lambda *args: None
QMessageBox.critical = lambda *args: (_ for _ in ()).throw(RuntimeError(str(args)))
project = QgsProject.instance()
project.addMapLayer(QgsVectorLayer('Point?crs=EPSG:3857', 'existing basemap', 'memory'))
project.setCrs(QgsCoordinateReferenceSystem())
dialog = SuomenvaylatDialog()
dialog.background_key.setText('dummy')
for title in ('Kapsi — Taustakartta', 'Kapsi — Peruskartta', 'Kapsi — Ortokuva'):
    dialog.background.setCurrentText(title)
    dialog._add_background()
    assert any(layer.name() == title and layer.crs().authid() == 'EPSG:3067'
               for layer in QgsProject.instance().mapLayers().values())
assert project.crs().authid() == 'EPSG:3067'
print('Kapsi live WMS configuration passed', flush=True)
project.setCrs(QgsCoordinateReferenceSystem())
for map_name in ('Taustakartta', 'Maastokartta', 'Kiinteistöjaotus'):
    dialog._add_mml_background(map_name)
    layer = next(layer for layer in QgsProject.instance().mapLayers().values()
                 if layer.name() == f'MML — {map_name}')
    assert layer.crs().authid() == 'EPSG:3857'
    assert 'WGS84_Pseudo-Mercator' in layer.source()
assert project.crs().authid() == 'EPSG:3857'
assert len(requested_urls) == 3
assert all('WGS84_Pseudo-Mercator' in url for url in requested_urls)
print('MML tile configuration passed', flush=True)
dialog.karttakuva_user.setText('dummy')
dialog.karttakuva_password.setText('dummy')
dialog._add_karttakuva({'id': 'taustakartta', 'title': 'test',
                        'endpoint': 'https://tiles.kartat.kapsi.fi/taustakartta'})
assert any(layer.name() == 'MML Karttakuva — test' and layer.crs().authid() == 'EPSG:3067'
           for layer in QgsProject.instance().mapLayers().values())
class FakePlugin:
    def set_aino_token(self, token):
        assert token == 'dummy'
dialog.plugin = FakePlugin()
dialog.aino_token.setText('dummy')
dialog._add_aino_wms({'id': 'taustakartta', 'title': 'test',
                      'endpoint': 'https://tiles.kartat.kapsi.fi/taustakartta'})
assert any(layer.name() == 'Aino — test' and layer.crs().authid() == 'EPSG:3067'
           for layer in QgsProject.instance().mapLayers().values())
print('authenticated WMS configuration passed', flush=True)
