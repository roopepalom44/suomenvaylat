"""Native QGIS file and failure regressions; no external service is contacted."""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsRectangle, QgsVectorLayer, QgsWkbTypes)

profile = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = profile.name
os.environ["QGIS_AUTH_DB_DIR_PATH"] = profile.name
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.temp.name) / "result.gpkg"
        self.crs = QgsCoordinateReferenceSystem("EPSG:3067")
        self.mask = QgsGeometry.fromRect(QgsRectangle(0, 0, 100, 100))
        self.entry = {"id": "demo", "title": "Demo", "kind": "ogc", "endpoint": "https://example.test/"}
        self.outputs = []

    def tearDown(self):
        self.temp.cleanup()

    def write(self, pages, progress=None):
        return services._write_pages(self.entry, iter(pages), self.crs, self.mask, self.crs,
                                     self.path, progress, self.outputs.append)

    def test_filtering_counts_written_features_not_server_bbox_total(self):
        self.assertEqual(1, self.write([[{"geometry": {"type": "Point", "coordinates": [10, 20]}},
                                       {"geometry": {"type": "Point", "coordinates": [200, 300]}}]]))
        self.assertEqual(1, len(self.outputs))

    def test_wkb_preserves_coordinates_and_z(self):
        x = 10.123456789012345
        self.write([[{"geometry": {"type": "Point", "coordinates": [x, 20, 45]}}]])
        item = self.outputs[0]
        layer = QgsVectorLayer(f"{self.path}|layername={item['layer_name']}", "test", "ogr")
        geometry = next(layer.getFeatures()).geometry()
        self.assertEqual(x, geometry.asPoint().x())
        self.assertTrue(QgsWkbTypes.hasZ(geometry.wkbType()))

    def test_style_persistence_keeps_the_open_layer_readable(self):
        self.write([[{"geometry": {"type": "Point", "coordinates": [10, 20]}, "properties": {"id": 42}}]])
        self.path.with_suffix(".sld").write_text('''<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld">
          <NamedLayer><Name>demo</Name><UserStyle><FeatureTypeStyle><Rule>
          <PointSymbolizer><Graphic><Mark><WellKnownName>circle</WellKnownName></Mark><Size>5</Size></Graphic></PointSymbolizer>
          </Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>''', encoding="utf-8")
        item = dict(self.outputs[0], style_prepared=True)
        layer = services.open_downloaded_layer(item, dict(self.entry, kind="wfs"))
        self.assertEqual(1, layer.featureCount())
        features = list(layer.getFeatures())
        self.assertEqual(1, len(features))
        self.assertEqual(42, features[0]["id"])

    def test_cancellation_during_writing_removes_partial_output(self):
        class Canceled(Exception):
            pass
        def progress(count, total=None, stage="Ladataan"):
            if stage == "Tallennetaan":
                raise Canceled()
        with self.assertRaises(Canceled):
            self.write([[{"geometry": {"type": "Point", "coordinates": [10, 20]}}]], progress)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.outputs)

    def test_writer_failure_does_not_report_partial_success(self):
        original = services.QgsVectorFileWriter
        class Writer:
            def __init__(self, *args):
                self.inner = original.create(*args)
            def hasError(self):
                return self.inner.hasError()
            def errorMessage(self):
                return "test write failure"
            def addFeature(self, feature):
                return False
        substitute = types.SimpleNamespace(SaveVectorOptions=original.SaveVectorOptions,
                                            CreateOrOverwriteLayer=original.CreateOrOverwriteLayer,
                                            NoError=original.NoError, create=Writer)
        with patch.object(services, "QgsVectorFileWriter", substitute):
            with self.assertRaisesRegex(RuntimeError, "test write failure"):
                self.write([[{"geometry": {"type": "Point", "coordinates": [10, 20]}}]])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.outputs)

    def test_later_page_error_leaves_no_accepted_file(self):
        def pages():
            yield [{"geometry": {"type": "Point", "coordinates": [10, 20]}}]
            raise ConnectionResetError()
        with self.assertRaises(ConnectionResetError):
            self.write(pages())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.outputs)

    def test_geojson_fallback_works_without_new_qgis_api(self):
        with patch.object(services, "QgsJsonUtils", object()):
            geometry = services._geometry_from_geojson({"type": "Point", "coordinates": [10, 20, 45]})
        self.assertFalse(geometry.isEmpty())
        self.assertTrue(QgsWkbTypes.hasZ(geometry.wkbType()))

    def test_transform_error_is_not_ignored(self):
        with self.assertRaisesRegex(RuntimeError, "koordinaatistomuunnos"):
            services._transform(types.SimpleNamespace(transform=lambda context: 1), None)

    def test_worker_returns_only_plain_descriptors(self):
        wfs_entry = dict(self.entry, kind="wfs", source="Liiteri")
        pages = [([{"geometry": {"type": "Point", "coordinates": [10, 20]}}], 1, 1)]
        with patch.object(services.wfs_pages, "pages", return_value=iter(pages)), \
             patch.object(services, "QgsVectorLayer", side_effect=AssertionError("worker created a layer")), \
             patch.object(services, "QgsProject", side_effect=AssertionError("worker touched project")), \
             patch.object(services, "_prepare_provider_style"):
            count, outputs = services.download_to_files(wfs_entry, self.mask, self.crs, self.path,
                                                        QgsProject.instance().transformContext())
        self.assertEqual(1, count)
        self.assertEqual(1, len(outputs))
        self.assertIsInstance(outputs[0], dict)


if __name__ == "__main__":
    unittest.main()
