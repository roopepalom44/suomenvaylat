"""Complete label properties omitted by QGIS's native SLD importer."""
from collections import defaultdict
import xml.etree.ElementTree as ET

from qgis.core import (QgsExpression, QgsOgcUtils, QgsPalLayerSettings,
                       QgsTextBackgroundSettings, QgsUnitTypes)
from qgis.PyQt.QtCore import QSizeF
from qgis.PyQt.QtGui import QColor, QFontDatabase
from qgis.PyQt.QtXml import QDomDocument

from . import service_styles as ss


def _expression_key(text):
    return QgsExpression(text).dump() if text else ""


def _source_key(rule):
    if ss.child(rule, "ElseFilter") is not None:
        return "ELSE"
    test = ss.child(rule, "Filter")
    if test is None:
        return ""
    document = QDomDocument()
    if not document.setContent(ET.tostring(test, encoding="unicode"), True)[0]:
        return None
    expression = QgsOgcUtils.expressionFromOgcFilter(document.documentElement())
    return expression.dump() if expression is not None and not expression.hasParserError() else None


def _rules(rule):
    if rule.settings() is not None:
        yield rule
    for item in rule.children():
        yield from _rules(item)


def _shield(text, text_format):
    graphic = ss.child(text, "Graphic")
    mark = ss.child(graphic, "Mark")
    shape = ss.value(mark, "WellKnownName", "square").lower()
    shapes = {"circle": QgsTextBackgroundSettings.ShapeCircle, "square": QgsTextBackgroundSettings.ShapeSquare}
    if graphic is None or mark is None or shape not in shapes:
        return
    options = {item.get("name"): (item.text or "").strip() for item in ss.children(text, "VendorOption")}
    background = QgsTextBackgroundSettings()
    background.setEnabled(True)
    background.setType(shapes[shape])
    background.setSizeUnit(QgsUnitTypes.RenderPixels)
    background.setStrokeWidthUnit(QgsUnitTypes.RenderPixels)
    size = ss.value(graphic, "Size")
    if size:
        background.setSizeType(QgsTextBackgroundSettings.SizeFixed)
        background.setSize(QSizeF(ss.number(size), ss.number(size)))
    else:
        margin = ss.number(options.get("graphic-margin"), 0)
        background.setSizeType(QgsTextBackgroundSettings.SizeBuffer)
        background.setSize(QSizeF(margin, margin))
    for tag, prefix, default, setter in (("Fill", "fill", "#808080", background.setFillColor),
                                         ("Stroke", "stroke", "#000000", background.setStrokeColor)):
        element = ss.child(mark, tag)
        params = ss.parameters(element)
        color = QColor(params.get(prefix, default))
        color.setAlphaF(ss.number(params.get(prefix + "-opacity"), 1) if element is not None else 0)
        setter(color)
        if tag == "Stroke":
            background.setStrokeWidth(ss.number(params.get("stroke-width"), 1))
    text_format.setBackground(background)


def complete_sld_labels(layer, path):
    """Match native rules by their OGC filter; never guess ambiguous matches."""
    if not layer.labelsEnabled() or layer.labeling().type() != "rule-based":
        return
    sources = defaultdict(list)
    root = ss.parse_xml(path.read_bytes())
    for rule in (item for item in root.iter() if ss.local(item) == "Rule"):
        texts = ss.children(rule, "TextSymbolizer")
        if texts:
            key = _source_key(rule)
            if key is not None:
                sources[key].extend((rule, text) for text in texts)
    labeling = layer.labeling().clone()
    rules = list(_rules(labeling.rootRule()))
    native_keys = ["ELSE" if rule.isElse() else _expression_key(rule.filterExpression()) for rule in rules]
    families = {family.casefold(): family for family in QFontDatabase().families()}
    generic = {"serif": ("Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif"),
               "sans-serif": ("Arial", "Liberation Sans", "DejaVu Sans", "Noto Sans"),
               "monospace": ("Courier New", "Liberation Mono", "DejaVu Sans Mono", "Noto Sans Mono")}
    for rule, key in zip(rules, native_keys):
        settings = QgsPalLayerSettings(rule.settings())
        text_format = settings.format()
        font = text_format.font()
        candidates = generic.get(font.family().casefold(), ())
        for candidate in candidates:
            if candidate.casefold() in families:
                font.setFamily(families[candidate.casefold()])
                text_format.setFont(font)
                break
        # Shared filters may represent different scales/fonts. Keep QGIS's
        # imported settings if the source cannot be identified uniquely.
        if len(sources[key]) == 1 and native_keys.count(key) == 1:
            source, text = sources[key][0]
            rule.setMinimumScale(ss.number(ss.value(source, "MaxScaleDenominator")))
            rule.setMaximumScale(ss.number(ss.value(source, "MinScaleDenominator")))
            try:
                _shield(text, text_format)
            except ss.StyleError:
                # Preserve native labels for computed/provider-specific graphics.
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage("Labelin taustamerkkiä ei voitu täydentää SLD-tyylistä.",
                                         "Suomenväylät", Qgis.Warning)
        settings.setFormat(text_format)
        rule.setSettings(settings)
    layer.setLabeling(labeling)
