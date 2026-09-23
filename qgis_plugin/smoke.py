from qgis.core import QgsApplication, Qgis
QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()
from suomenvaylat_qgis.services import area_choices, selection_geometry, catalog
from suomenvaylat_qgis.plugin import SuomenvaylatDialog
import suomenvaylat_qgis.plugin as plugin_module
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
from qgis.PyQt.QtCore import Qt
for index in range(dialog.sources.count()):
    if dialog.sources.item(index).text() == 'DigiRoad':
        dialog.sources.item(index).setCheckState(Qt.Checked)
old_catalog = plugin_module.catalog
plugin_module.catalog = lambda source, key='', password='': ([{'source': source, 'kind': 'wfs',
    'id': source.lower(), 'title': source, 'endpoint': 'https://example.test/'}], [])
try:
    dialog._load_catalog()
    assert {entry['source'] for entry in dialog.entries} == {'Väylä', 'DigiRoad'}
finally:
    plugin_module.catalog = old_catalog
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
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
plugin.aino_token = 'dummy-token'
request = QNetworkRequest(QUrl('https://aino.sitowise.com/ows?SERVICE=WMS'))
plugin._preprocess_aino(request)
assert 'token=dummy-token' in request.url().toString()
unrelated = QNetworkRequest(QUrl('https://example.org/ows'))
plugin._preprocess_aino(unrelated)
assert 'token=' not in unrelated.url().toString()
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
class FakeResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.data
old_urlopen = services.urllib.request.urlopen
def fake_urlopen(request, timeout=45):
    url = request if isinstance(request, str) else request.full_url
    if 'SERVICE=WMS' in url:
        return FakeResponse(b'<WMS_Capabilities><Layer><Name>aino:map</Name><Title>Aino map</Title></Layer></WMS_Capabilities>')
    return FakeResponse(b'<WFS_Capabilities><FeatureType><Name>aino:data</Name><Title>Aino data</Title></FeatureType></WFS_Capabilities>')
services.urllib.request.urlopen = fake_urlopen
try:
    entries, errors = services.catalog('Aino', 'dummy')
    assert len(entries) == 2 and not errors
    assert {entry['kind'] for entry in entries} == {'wfs', 'aino_wms'}
    karttakuva, errors = services.catalog('MML Karttakuva', 'user', 'password')
    assert len(karttakuva) == 1 and karttakuva[0]['kind'] == 'karttakuva_wms' and not errors
    print('Aino WFS/WMS catalog passed', flush=True)
finally:
    services.urllib.request.urlopen = old_urlopen
