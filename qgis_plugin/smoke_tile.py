from qgis.core import QgsApplication, QgsProject
QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()
assert QgsApplication.authManager().setMasterPassword('smoke-pass', True)
import suomenvaylat_qgis.plugin as plugin_module
from suomenvaylat_qgis.plugin import SuomenvaylatDialog
from qgis.PyQt.QtWidgets import QMessageBox
plugin_module._request_json = lambda url, key: {'tiles': ['https://example.org/{z}/{x}/{y}.pbf']}
QMessageBox.information = lambda *args: None
QMessageBox.critical = lambda *args: (_ for _ in ()).throw(RuntimeError(str(args)))
dialog = SuomenvaylatDialog()
dialog.background_key.setText('dummy')
dialog._add_mml_background('Taustakartta')
assert any(layer.name() == 'MML — Taustakartta' for layer in QgsProject.instance().mapLayers().values())
print('MML tile configuration passed', flush=True)
dialog.karttakuva_user.setText('dummy')
dialog.karttakuva_password.setText('dummy')
dialog._add_karttakuva({'id': 'taustakartta', 'title': 'test',
                        'endpoint': 'https://tiles.kartat.kapsi.fi/taustakartta'})
assert any(layer.name() == 'MML Karttakuva — test' for layer in QgsProject.instance().mapLayers().values())
print('authenticated WMS configuration passed', flush=True)
