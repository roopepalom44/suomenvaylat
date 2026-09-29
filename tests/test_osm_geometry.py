"""OSM-geometriat: samat tapaukset ArcGIS Pro- ja QGIS-toteutuksille."""

import importlib.machinery
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_toolbox():
    old_arcpy = sys.modules.get("arcpy")
    sys.modules["arcpy"] = types.ModuleType("arcpy")
    try:
        loader = importlib.machinery.SourceFileLoader(
            "vayla_toolbox_osm_test", str(ROOT / "Toolboxes" / "VaylaWFSDownloader.pyt")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        if old_arcpy is None:
            sys.modules.pop("arcpy", None)
        else:
            sys.modules["arcpy"] = old_arcpy


def _load_qgis_module():
    path = ROOT / "qgis_plugin" / "suomenvaylat_qgis" / "osm_geometry.py"
    spec = importlib.util.spec_from_file_location("suomenvaylat_osm_geometry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOLBOX = _load_toolbox()
QGIS = _load_qgis_module()
IMPLEMENTATIONS = {
    "arcgis": TOOLBOX.osm_element_geometry,
    "qgis": QGIS.element_geometry,
}


def _pts(*coords):
    return [{"lon": x, "lat": y} for x, y in coords]


SQUARE = _pts((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))


def _signed_area(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2.0


class OsmGeometryTests(unittest.TestCase):
    def check(self, element, **kwargs):
        results = {name: fn(element, **kwargs) for name, fn in IMPLEMENTATIONS.items()}
        self.assertEqual(results["arcgis"], results["qgis"])
        return results["arcgis"]

    def test_node_is_point(self):
        geometry = self.check({"type": "node", "lon": 25.0, "lat": 60.0})
        self.assertEqual({"type": "Point", "coordinates": [25.0, 60.0]}, geometry)

    def test_poi_uses_area_centre(self):
        geometry = self.check(
            {"type": "way", "center": {"lon": 1.5, "lat": 2.5}}, poi=True
        )
        self.assertEqual({"type": "Point", "coordinates": [1.5, 2.5]}, geometry)

    def test_closed_building_is_counter_clockwise_polygon(self):
        clockwise = list(reversed(SQUARE))
        geometry = self.check({"type": "way", "geometry": clockwise, "tags": {"building": "yes"}})
        self.assertEqual("Polygon", geometry["type"])
        self.assertGreater(_signed_area(geometry["coordinates"][0]), 0)

    def test_closed_linear_features_stay_lines(self):
        for tags in (
            {"highway": "residential"}, {"barrier": "fence"}, {"railway": "rail"},
            {"waterway": "stream"}, {"power": "line"}, {"natural": "coastline"},
            {"building": "yes", "area": "no"}, {},
        ):
            with self.subTest(tags=tags):
                geometry = self.check({"type": "way", "geometry": SQUARE, "tags": tags})
                self.assertEqual("LineString", geometry["type"])

    def test_explicit_area_and_area_waterway_are_polygons(self):
        for tags in (
            {"highway": "pedestrian", "area": "yes"}, {"waterway": "riverbank"},
            {"natural": "water"}, {"landuse": "forest"}, {"power": "substation"},
        ):
            with self.subTest(tags=tags):
                geometry = self.check({"type": "way", "geometry": SQUARE, "tags": tags})
                self.assertEqual("Polygon", geometry["type"])

    def test_multipolygon_relation_joins_split_outer_and_keeps_hole(self):
        element = {
            "type": "relation",
            "tags": {"type": "multipolygon", "landuse": "forest"},
            "members": [
                {"type": "way", "role": "outer", "geometry": _pts((0, 0), (10, 0), (10, 10))},
                # Toinen puolikas on tallennettu vastakkaiseen suuntaan.
                {"type": "way", "role": "outer", "geometry": _pts((0, 0), (0, 10), (10, 10))},
                {"type": "way", "role": "inner",
                 "geometry": _pts((2, 2), (4, 2), (4, 4), (2, 4), (2, 2))},
                {"type": "node", "role": "label", "lon": 5, "lat": 5},
            ],
        }
        geometry = self.check(element)
        self.assertEqual("Polygon", geometry["type"])
        self.assertEqual(2, len(geometry["coordinates"]))
        outer, inner = geometry["coordinates"]
        self.assertEqual(outer[0], outer[-1])
        self.assertAlmostEqual(100.0, _signed_area(outer))
        self.assertAlmostEqual(-4.0, _signed_area(inner))

    def test_relation_with_two_outers_is_multipolygon(self):
        element = {
            "type": "relation",
            "members": [
                {"type": "way", "role": "outer", "geometry": SQUARE},
                {"type": "way", "role": "outer",
                 "geometry": _pts((5, 5), (6, 5), (6, 6), (5, 6), (5, 5))},
            ],
        }
        geometry = self.check(element)
        self.assertEqual("MultiPolygon", geometry["type"])
        self.assertEqual(2, len(geometry["coordinates"]))

    def test_unclosed_relation_is_kept_as_lines(self):
        element = {
            "type": "relation",
            "members": [
                {"type": "way", "role": "outer", "geometry": _pts((0, 0), (1, 0))},
                {"type": "way", "role": "outer", "geometry": _pts((5, 5), (6, 5))},
            ],
        }
        geometry = self.check(element)
        self.assertEqual("MultiLineString", geometry["type"])

    def test_relation_without_member_geometry_is_skipped(self):
        self.assertIsNone(self.check({"type": "relation", "members": []}))

    def test_overpass_adapter_outputs_relations(self):
        geojson = TOOLBOX.OverpassAdapter.to_geojson({"elements": [{
            "type": "relation", "id": 7, "tags": {"boundary": "administrative"},
            "members": [{"type": "way", "role": "outer", "geometry": SQUARE}],
        }]})
        self.assertEqual(1, len(geojson["features"]))
        feature = geojson["features"][0]
        self.assertEqual("Polygon", feature["geometry"]["type"])
        self.assertEqual("relation", feature["properties"]["osm_type"])


if __name__ == "__main__":
    unittest.main()
