"""Offline QGIS UI regression: selections survive search and source changes."""

import sys
import tempfile
from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt

QgsApplication.setPrefixPath('C:/Program Files/QGIS 3.44.14/apps/qgis-ltr', True)
app = QgsApplication([], False)
app.initQgis()

sys.path.insert(0, str(Path(__file__).parent))
import suomenvaylat_qgis.plugin as plugin_module


def checked_item(dialog, source):
    return next(dialog.sources.item(i) for i in range(dialog.sources.count())
                if dialog.sources.item(i).text() == source)


def fake_catalog(source, key='', password=''):
    titles = {'Väylä': 'Ensimmäinen', 'DigiRoad': 'Toinen', 'Kapsi': 'Kolmas'}
    return ([{'source': source, 'kind': 'wfs', 'id': source.lower(),
              'title': titles[source], 'endpoint': 'https://example.test/'}], [])


original_catalog = plugin_module.catalog
original_selection_geometry = plugin_module.selection_geometry
original_download = plugin_module.download
original_information = plugin_module.QMessageBox.information
plugin_module.catalog = fake_catalog
plugin_module.selection_geometry = lambda *args: (None, None)
downloaded = []
plugin_module.download = lambda entry, mask, crs, path, key, progress: downloaded.append(entry['source']) or 1
plugin_module.QMessageBox.information = lambda *args: plugin_module.QMessageBox.Ok

try:
    dialog = plugin_module.SuomenvaylatDialog()
    checked_item(dialog, 'DigiRoad').setCheckState(Qt.Checked)
    dialog._load_catalog()
    for query in ('Ensimmäinen', 'Toinen'):
        dialog.search.setText(query)
        assert dialog.layers.count() == 1
        dialog.layers.item(0).setCheckState(Qt.Checked)
    assert len(dialog._selected_entries) == 2

    checked_item(dialog, 'Väylä').setCheckState(Qt.Unchecked)
    checked_item(dialog, 'DigiRoad').setCheckState(Qt.Unchecked)
    dialog._load_catalog()
    assert dialog.layers.count() == 0
    assert len(dialog._selected_entries) == 2
    assert dialog.run_button.isEnabled()

    checked_item(dialog, 'Kapsi').setCheckState(Qt.Checked)
    dialog.search.setText('Kolmas')
    dialog._load_catalog()
    assert dialog.layers.count() == 1
    dialog.layers.item(0).setCheckState(Qt.Checked)
    assert len(dialog._selected_entries) == 3

    with tempfile.TemporaryDirectory() as folder:
        dialog.output.setText(folder)
        dialog._run_download()
    assert downloaded == ['Väylä', 'DigiRoad', 'Kapsi'], downloaded
    dialog.close()
    print('QGIS selection regression passed')
finally:
    plugin_module.catalog = original_catalog
    plugin_module.selection_geometry = original_selection_geometry
    plugin_module.download = original_download
    plugin_module.QMessageBox.information = original_information
