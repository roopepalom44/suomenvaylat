import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("service_styles_test", ROOT / "qgis_plugin/suomenvaylat_qgis/service_styles.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def fixture(name):
    return (ROOT / "tests/data/styles" / (name + ".sld")).read_bytes()


class StyleDiscoveryTests(unittest.TestCase):
    def test_getstyles_replaces_protocol_params_preserves_token(self):
        url = ss.query_url("https://example.test/ows?service=WFS&request=GetFeature&token=secret", SERVICE="WMS", REQUEST="GetStyles")
        query = ss.urllib.parse.parse_qs(ss.urllib.parse.urlsplit(url).query)
        self.assertEqual({"SERVICE": ["WMS"], "REQUEST": ["GetStyles"], "token": ["secret"]}, query)

    def test_wfs_and_ogc_schemas_are_not_confused(self):
        self.assertEqual(["https://geo.stat.fi/geoserver/wms"], ss.style_endpoints({"kind": "wfs", "endpoint": "https://geo.stat.fi/geoserver/wfs"}))
        self.assertEqual([], ss.style_endpoints({"kind": "ogc", "endpoint": "https://example.test/features"}))
        self.assertEqual([], ss.style_endpoints({"kind": "wfs", "endpoint": "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/gn"}))

    def test_missing_wfs_endpoint_has_no_style_or_network_request(self):
        for entry in ({"kind": "wfs"}, {"kind": "wfs", "endpoint": None},
                      {"kind": "wfs", "endpoint": ""}):
            with self.subTest(entry=entry):
                self.assertEqual([], ss.style_endpoints(entry))
                requests = []
                self.assertIsNone(ss.StyleClient(lambda url: requests.append(url)).get(entry))
                self.assertFalse(requests)

    def test_oskari_uses_layer_name_and_selected_style_and_tries_public_services(self):
        requests = []
        def fetch(url):
            requests.append(url)
            if "/rajoitettu/" not in url:
                raise OSError("not here")
            return fixture("traficom_rajoitettu_1")
        client = ss.StyleClient(fetch)
        root, endpoint = client.get({"kind": "oskari_wfs", "id": "123", "layer_name": "DepthArea_A", "style": "syvyysalue_a"})
        self.assertTrue(endpoint.endswith("/rajoitettu/wms"))
        self.assertIn("LAYERS=DepthArea_A", requests[0])
        self.assertEqual(3, len(requests))
        self.assertEqual("DepthArea_A", ss.value(ss.child(root, "NamedLayer"), "Name"))

    def test_default_userstyle_is_kept_alternative_generic_is_removed(self):
        root = ss.parse_xml(fixture("digiroad_0"))
        layer = ss.child(root, "NamedLayer")
        alternate = ET.SubElement(layer, "{" + ss.SLD + "}UserStyle")
        ET.SubElement(alternate, "{" + ss.SLD + "}Name").text = "generic"
        ET.SubElement(alternate, "{" + ss.SLD + "}Rule")
        data = ET.tostring(root)
        selected = ss.select_style(data, "dr_ajoneuvokoht_rajoitus")
        self.assertEqual(1, sum(ss.local(item) == "UserStyle" for item in selected.iter()))
        self.assertNotIn(b"generic", ET.tostring(selected))

    def test_wrong_layer_is_rejected(self):
        with self.assertRaises(ss.StyleError):
            ss.select_style(fixture("digiroad_0"), "different_layer")

    def test_empty_sld_and_error_documents_are_rejected(self):
        for data in (b'<StyledLayerDescriptor><NamedLayer><Name>a</Name></NamedLayer></StyledLayerDescriptor>',
                     b'<ServiceExceptionReport/>', b'<!DOCTYPE sld><StyledLayerDescriptor/>'):
            with self.assertRaises(ss.StyleError):
                ss.select_style(data, "a")

    def test_cache_avoids_repeated_requests_and_returns_independent_trees(self):
        requests = []
        client = ss.StyleClient(lambda url: requests.append(url) or fixture("digiroad_0"))
        entry = {"kind": "wfs", "id": "dr_ajoneuvokoht_rajoitus", "endpoint": "https://example.test/ows"}
        first, _ = client.get(entry)
        first.clear()
        second, _ = client.get(entry)
        self.assertEqual(1, len(requests))
        self.assertIsNotNone(ss.child(second, "NamedLayer"))

    def test_network_failure_is_cached_and_does_not_expose_url(self):
        requests = []
        def fetch(url):
            requests.append(url)
            raise OSError(url)
        client = ss.StyleClient(fetch)
        for _ in range(2):
            with self.assertRaises(ss.StyleError) as error:
                client.get({"kind": "wfs", "id": "a", "endpoint": "https://example.test/ows?token=secret"})
            self.assertNotIn("secret", str(error.exception))
        self.assertEqual(2, len(requests))

    def test_scoped_wms_unqualified_name_is_discovered_after_wfs_prefix_is_rejected(self):
        requests = []
        def fetch(url):
            requests.append(url)
            query = ss.urllib.parse.parse_qs(ss.urllib.parse.urlsplit(url).query)
            if query["REQUEST"] == ["GetCapabilities"]:
                return b'<WMS_Capabilities><Capability><Layer><Layer><Name>dr_ajoneuvokoht_rajoitus</Name></Layer></Layer></Capability></WMS_Capabilities>'
            if query["LAYERS"] == ["digiroad:dr_ajoneuvokoht_rajoitus"]:
                return b'<ServiceExceptionReport/>'
            return fixture("digiroad_0")
        root, _ = ss.StyleClient(fetch).get({"kind": "wfs", "id": "digiroad:dr_ajoneuvokoht_rajoitus", "endpoint": "https://example.test/ows"})
        self.assertEqual("dr_ajoneuvokoht_rajoitus", ss.value(ss.child(root, "NamedLayer"), "Name"))
        self.assertEqual(3, len(requests))

    def test_sld_persistence_removes_credentials_from_image_urls(self):
        root = ET.fromstring('<StyledLayerDescriptor xmlns:xlink="http://www.w3.org/1999/xlink"><OnlineResource xlink:href="https://user:pass@example.test/image.png?token=secret&amp;x=1"/></StyledLayerDescriptor>')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "styles.sld"
            ss.save_sld(root, path)
            data = path.read_text()
        self.assertNotIn("secret", data)
        self.assertNotIn("user:pass", data)
        self.assertIn("x=1", data)

    def test_downloadable_png_symbol_is_localized_and_embedded_in_cim(self):
        root = ET.fromstring('<StyledLayerDescriptor xmlns:xlink="http://www.w3.org/1999/xlink"><OnlineResource xlink:href="https://example.test/icon.png?token=secret"/></StyledLayerDescriptor>')
        content = b"test PNG contents"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "style.sld"
            ss.localize_graphics(root, "https://example.test/wms", path, lambda url: content)
            online = next(item for item in root.iter() if ss.local(item) == "OnlineResource")
            self.assertTrue(Path(online.get("{" + ss.XLINK + "}href")).is_file())
            element = ET.fromstring('<PointSymbolizer xmlns:xlink="http://www.w3.org/1999/xlink"><Graphic><ExternalGraphic><OnlineResource/><Format>image/png</Format></ExternalGraphic><Size>12</Size></Graphic></PointSymbolizer>')
            resource = next(item for item in element.iter() if ss.local(item) == "OnlineResource")
            resource.attrib.update(online.attrib)
            marker = ss.symbol_layers(element, "Point")[0]
            self.assertEqual("CIMPictureMarker", marker["type"])
            self.assertEqual(9, marker["size"])
            self.assertIn("data:image/png;base64,", marker["uRL"])
            self.assertNotIn("secret", marker["uRL"])


class ArcGISConversionTests(unittest.TestCase):
    def label_style(self, body):
        root = ET.fromstring('<StyledLayerDescriptor><NamedLayer><UserStyle><FeatureTypeStyle>' + body +
                             '</FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>')
        return ss.arc_label_rules(root, "Polyline")

    def test_real_speed_style_has_ten_filtered_scaled_label_classes(self):
        root = ss.parse_xml(fixture("vayla_nopeusrajoitukset"))
        render_rules, warnings = ss.arc_rules(root, "Polyline")
        self.assertFalse(warnings)
        self.assertEqual(20, len(render_rules))
        rules, warnings = ss.arc_label_rules(root, "Polyline")
        self.assertFalse(warnings)
        fields = {"nopeusrajoitus": ("speed", "Integer"), "tyyppi": ("kind", "String")}
        classes = ss.arc_label_classes(rules, fields)
        self.assertEqual(10, len(classes))
        first = classes[0]
        self.assertIn('$feature["speed"] == 120.0', first["expression"])
        self.assertIn('$feature["kind"] == "Nopeusrajoitus"', first["expression"])
        self.assertNotIn("$view", first["expression"])
        self.assertEqual((362834, 0), (first["minimumScale"], first["maximumScale"]))
        symbol = first["textSymbol"]["symbol"]
        self.assertEqual(("Times New Roman", "Bold", 7.5),
                         (symbol["fontFamilyName"], symbol["fontStyleName"], symbol["height"]))
        self.assertEqual("CIMPointSymbolCallout", symbol["callout"]["type"])
        self.assertEqual("PropUniform", symbol["callout"]["backgroundScale"])
        self.assertEqual("CenteredHorizontalOnLine", first["maplexLabelPlacementProperties"]["linePlacementMethod"])
        self.assertTrue(first["maplexLabelPlacementProperties"]["thinDuplicateLabels"])
        # Rendering-only attributes are not unnecessarily required for labels.
        self.assertEqual({"nopeusrajoitus", "tyyppi"}, ss.label_fields(rules[-1], rules))

    def test_label_literals_concatenate_and_fields_use_local_names(self):
        rules, warnings = self.label_style('<Rule><TextSymbolizer><Label><Literal>Nopeus: </Literal>'
                                          '<PropertyName>vayla:parent/nopeusrajoitus</PropertyName>'
                                          '<Literal> km/h</Literal></Label></TextSymbolizer></Rule>')
        self.assertFalse(warnings)
        expression = ss.arc_label_classes(rules, {"nopeusrajoitus": ('speed"field', "Integer")})[0]["expression"]
        self.assertIn('"Nopeus: " + IIf(', expression)
        self.assertIn(' == null, "", Text(', expression)
        self.assertIn('speed\\"field', expression)
        self.assertIn('+ " km/h"', expression)

    def test_label_halo_line_offset_and_scale_direction(self):
        rules, warnings = self.label_style('<Rule><MinScaleDenominator>1000</MinScaleDenominator>'
            '<MaxScaleDenominator>20000</MaxScaleDenominator><TextSymbolizer><Label>Road</Label>'
            '<Font><CssParameter name="font-style">italic</CssParameter></Font>'
            '<Halo><Radius>2</Radius><Fill><CssParameter name="fill">#ffffff</CssParameter></Fill></Halo>'
            '<LabelPlacement><LinePlacement><PerpendicularOffset>4</PerpendicularOffset></LinePlacement></LabelPlacement>'
            '</TextSymbolizer></Rule>')
        self.assertFalse(warnings)
        label = ss.arc_label_classes(rules, {})[0]
        self.assertEqual((20000, 1000), (label["minimumScale"], label["maximumScale"]))
        self.assertEqual(1.5, label["textSymbol"]["symbol"]["haloSize"])
        self.assertEqual("Italic", label["textSymbol"]["symbol"]["fontStyleName"])
        self.assertEqual(3, label["maplexLabelPlacementProperties"]["primaryOffset"])

    def test_else_labels_exclude_graphical_peers_and_split_their_scale_ranges(self):
        rules, warnings = self.label_style('<Rule><MaxScaleDenominator>1000</MaxScaleDenominator>'
            '<Filter><PropertyIsEqualTo><PropertyName>type</PropertyName><Literal>A</Literal></PropertyIsEqualTo></Filter>'
            '<LineSymbolizer><Stroke/></LineSymbolizer></Rule>'
            '<Rule><ElseFilter/><TextSymbolizer><Label>Other</Label></TextSymbolizer></Rule>')
        self.assertFalse(warnings)
        self.assertEqual({"type"}, ss.label_fields(rules[1], rules))
        classes = ss.arc_label_classes(rules, {"type": ("type", "String")})
        self.assertEqual(2, len(classes))
        self.assertIn('!((', classes[0]["expression"])
        self.assertIn('$feature["type"] == "A"', classes[0]["expression"])
        self.assertEqual((1000, 0), (classes[0]["minimumScale"], classes[0]["maximumScale"]))
        self.assertNotIn('$feature', classes[1]["expression"])
        self.assertEqual((0, 1000), (classes[1]["minimumScale"], classes[1]["maximumScale"]))

    def test_unsupported_label_function_warns_without_removing_line_symbols(self):
        root = ET.fromstring('<StyledLayerDescriptor><FeatureTypeStyle><Rule>'
            '<LineSymbolizer><Stroke/></LineSymbolizer><TextSymbolizer><Label>'
            '<Function name="strConcat"><PropertyName>name</PropertyName></Function>'
            '</Label></TextSymbolizer></Rule></FeatureTypeStyle></StyledLayerDescriptor>')
        rules, warnings = ss.arc_rules(root, "Polyline")
        self.assertEqual(1, len(rules))
        labels, warnings = ss.arc_label_rules(root, "Polyline")
        self.assertEqual([], ss.arc_label_classes(labels, {}))
        self.assertEqual(1, len(warnings))
        self.assertIn("Function", warnings[0])

    def test_label_only_style_is_accepted_and_has_no_default_geometry_symbol(self):
        root = ET.fromstring('<StyledLayerDescriptor><FeatureTypeStyle><Rule>'
            '<TextSymbolizer><Label>Only text</Label></TextSymbolizer></Rule></FeatureTypeStyle></StyledLayerDescriptor>')
        rules, warnings = ss.arc_rules(root, "Point")
        self.assertEqual([], rules)
        renderer = ss.arc_renderer(rules, "Point", {}, [{}])
        self.assertFalse(renderer["useDefaultSymbol"])
        self.assertFalse(renderer["groups"][0]["classes"])
        label_rules, warnings = ss.arc_label_rules(root, "Point")
        self.assertFalse(warnings)
        self.assertEqual(1, len(ss.arc_label_classes(label_rules, {})))

    def test_unknown_label_filter_does_not_label_all_features_or_enable_else_labels(self):
        rules, warnings = self.label_style('<Rule><Filter><PropertyIsLike wildCard="*">'
            '<PropertyName>name</PropertyName><Literal>A*</Literal></PropertyIsLike></Filter>'
            '<TextSymbolizer><Label>A</Label></TextSymbolizer></Rule>'
            '<Rule><ElseFilter/><TextSymbolizer><Label>Other</Label></TextSymbolizer></Rule>')
        self.assertEqual([], ss.arc_label_classes(rules, {}))
        self.assertEqual(2, len(warnings))

    def test_provider_width_and_colors_in_points(self):
        rules, warnings = ss.arc_rules(ss.parse_xml(fixture("vayla_0")), "Polyline")
        self.assertFalse(warnings)
        symbol = rules[0]["layers"][0]
        self.assertEqual(2.25, symbol["width"])
        self.assertEqual([68, 119, 170, 100], symbol["color"]["values"])
        self.assertTrue(ss.matches(rules[0]["filter"], {"rakenteelliset_ominaisuudet_tyyppi": "Riista-aidat"}))

    def test_numeric_classification_and_nulls(self):
        rules, _ = ss.arc_rules(ss.parse_xml(fixture("digiroad_0")), "Polyline")
        self.assertTrue(ss.matches(rules[0]["filter"], {"kiell_ajon": 2}))
        self.assertFalse(ss.matches(rules[0]["filter"], {"kiell_ajon": None}))
        renderer = ss.arc_renderer(rules, "Polyline", {"kiell_ajon": ("kiell_ajon", "Integer")}, [{"kiell_ajon": 2}, {"kiell_ajon": 3}])
        expression = renderer["valueExpressionInfo"]["expression"]
        self.assertIn('$feature["kiell_ajon"] == 2.0', expression)
        self.assertEqual(2, len(renderer["groups"][0]["classes"]))

    def test_polygon_opacity_and_compound_depth_filter(self):
        rules, _ = ss.arc_rules(ss.parse_xml(fixture("traficom_rajoitettu_1")), "Polygon")
        self.assertEqual(50, rules[0]["layers"][0]["color"]["values"][3])
        self.assertTrue(any(ss.matches(rule["filter"], {"DRVAL2": 0.5}) for rule in rules))

    def test_overlapping_rules_else_scope_and_scale_intervals(self):
        layer = {"type": "CIMSolidStroke", "width": 2}
        rules = [
            {"label": "A", "filter": ["true"], "else": False, "group": 0, "min": 0, "max": 1000, "layers": [layer]},
            {"label": "B", "filter": ["true"], "else": False, "group": 1, "min": 0, "max": float("inf"), "layers": [layer]},
            {"label": "Else", "filter": ["true"], "else": True, "group": 0, "min": 0, "max": float("inf"), "layers": [layer]},
        ]
        self.assertEqual((0, 1), ss.matching_rules(rules, {}, 500))
        self.assertEqual((1, 2), ss.matching_rules(rules, {}, 1000))
        renderer = ss.arc_renderer(rules, "Polyline", {}, [{}])
        values = [item["values"][0]["fieldValues"][0] for item in renderer["groups"][0]["classes"]]
        self.assertEqual(["0,1", "1,2"], values)
        self.assertIn("$view.scale < 1000", renderer["valueExpressionInfo"]["expression"])

    def test_fill_absence_and_explicit_empty_fill_are_distinct(self):
        self.assertIsNone(ss.fill(None))
        self.assertEqual([128, 128, 128, 100], ss.fill(ET.fromstring("<Fill/>"))["color"]["values"])

    def test_unknown_filter_fails_instead_of_styling_every_feature(self):
        with self.assertRaises(ss.StyleError):
            ss.predicate(ET.fromstring('<PropertyIsEqualTo><Function name="random"/><Literal>1</Literal></PropertyIsEqualTo>'))

    def test_point_marker_size_shape_and_rotation(self):
        rules, _ = ss.arc_rules(ss.parse_xml(fixture("syke_0")), "Point")
        marker = rules[0]["layers"][0]
        self.assertEqual(4.5, marker["size"])
        self.assertEqual(-45, marker["rotation"])
        self.assertEqual(5, len(marker["markerGraphics"][0]["geometry"]["rings"][0]))

    def test_dash_and_offset_are_preserved(self):
        element = ET.fromstring('<LineSymbolizer><Stroke><CssParameter name="stroke-dasharray">4 2</CssParameter></Stroke><PerpendicularOffset>3</PerpendicularOffset></LineSymbolizer>')
        symbol = ss.symbol_layers(element, "Polyline")[0]
        self.assertEqual(2.25, symbol["effects"][0]["offset"])
        self.assertEqual([3, 1.5], symbol["effects"][1]["dashTemplate"])


if __name__ == "__main__":
    unittest.main()
