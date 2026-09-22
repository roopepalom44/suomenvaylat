from qgis.core import QgsApplication, Qgis
QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()
from suomenvaylat_qgis.services import area_choices, selection_geometry, catalog
from suomenvaylat_qgis.plugin import SuomenvaylatDialog
from suomenvaylat_qgis import classFactory
import suomenvaylat_qgis.services as services
from pathlib import Path
from qgis.core import QgsProject
names = area_choices('Maakunta')
assert len(names) >= 18
geometry, crs = selection_geometry('Maakunta', names[:1])
assert not geometry.isEmpty() and crs.authid() == 'EPSG:3067'
print('areas passed', len(names), flush=True)
dialog = SuomenvaylatDialog()
dialog.close()
print('dialog constructed', flush=True)
class Interface:
    def mainWindow(self): return None
    def addPluginToMenu(self, *args): pass
    def addToolBarIcon(self, *args): pass
    def removePluginMenu(self, *args): pass
    def removeToolBarIcon(self, *args): pass
plugin = classFactory(Interface())
plugin.initGui()
plugin.unload()
print('plugin lifecycle passed', flush=True)
mask, crs = selection_geometry('Kunta/Kaupunki', ['Helsinki'])
calls = []
def fake_page(url, key=''):
    calls.append(url)
    number = len(calls)
    return {'features': [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [24.93, 60.17]},
                          'properties': {'number': number}}],
            'links': [{'rel': 'next', 'href': '?page=2'}] if number == 1 else []}
old_request = services._request_json
services._request_json = fake_page
out = Path(__file__).parent / 'smoke_ogc.gpkg'
out.unlink(missing_ok=True)
try:
    entry = {'kind': 'ogc', 'id': 'demo', 'title': 'demo', 'endpoint': 'https://example.test/'}
    assert services.download(entry, mask, crs, out) == 2
    assert len(calls) == 2
    print('OGC pagination passed', flush=True)
finally:
    services._request_json = old_request
    QgsProject.instance().removeAllMapLayers()
