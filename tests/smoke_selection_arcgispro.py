"""Offline ArcGIS Pro parameter regression for filtered multi-source selections."""

import importlib.machinery
import importlib.util
from pathlib import Path

# Pysähtyy heti, jos skriptiä ei ajeta ArcGIS Pron Python-ympäristössä.
importlib.import_module('arcpy')


toolbox_path = Path(__file__).resolve().parents[1] / 'Toolboxes' / 'VaylaWFSDownloader.pyt'
loader = importlib.machinery.SourceFileLoader('vayla_selection_smoke', str(toolbox_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

tool = module.VaylaWFSDownloader()
tool._warn = lambda message: None
labels = {'Väylä': 'Tieverkko - Väylä',
          'MML': 'Kiinteistöjaotus - MML',
          'Kapsi': 'Kolmas kartta - Kapsi'}


def fake_fetch(sources, cache_key=None, allow_disk_cache=True):
    tool._layer_mapping = {
        labels[source]: {'source': source, 'kind': 'mml_property_ogcapi' if source == 'MML' else 'wfs'}
        for source in sources
    }
    return [labels[source] for source in sources]


tool._fetch_layer_list = fake_fetch
parameters = tool.getParameterInfo()
parameters[0].values = [['Väylä'], ['MML']]
parameters[3].value = 'Koko Suomi'
parameters[7].value = 'test-key'
tool.updateParameters(parameters)
parameters[2].values = [labels['Väylä'], labels['MML']]
tool.updateParameters(parameters)

parameters[0].values = [['Kapsi']]
parameters[1].value = 'kolmas'
tool.updateParameters(parameters)
assert tool._parse_multivalue_param(parameters[2]) == [labels['Väylä'], labels['MML']]
assert parameters[2].filter.list == list(labels.values())
assert parameters[7].enabled
assert tool._layer_mapping[labels['MML']]['source'] == 'MML'

parameters[2].values = list(labels.values())
tool.updateParameters(parameters)
assert tool._parse_multivalue_param(parameters[2]) == list(labels.values())
assert tool._sources_for_layers(tool._parse_multivalue_param(parameters[2])) == list(labels)
print('ArcGIS Pro selection regression passed')
