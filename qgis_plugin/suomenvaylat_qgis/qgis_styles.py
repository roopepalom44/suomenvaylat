"""Keep downloaded features visible when a provider SLD cannot classify them."""
from pathlib import Path

from qgis.core import (QgsExpressionContextUtils, QgsFillSymbol, QgsLineSymbol,
                       QgsMarkerSymbol, QgsRenderContext, QgsRuleBasedRenderer,
                       QgsSingleSymbolRenderer)

from . import service_styles as ss


def fallback_symbol(layer):
    geometry = layer.geometryType()
    geometry = int(getattr(geometry, "value", geometry))
    if geometry == 0:
        return QgsMarkerSymbol.createSimple({"name": "circle", "color": "100,100,100,255",
                                             "outline_color": "40,40,40,255", "size": "2.5"})
    if geometry == 1:
        return QgsLineSymbol.createSimple({"line_color": "100,100,100,255", "line_width": "0.5"})
    return QgsFillSymbol.createSimple({"color": "180,180,180,90", "outline_color": "70,70,70,255",
                                       "outline_width": "0.3"})


def reset_to_fallback(layer):
    layer.setRenderer(QgsSingleSymbolRenderer(fallback_symbol(layer)))
    layer.setLabelsEnabled(False)
    layer.setOpacity(1)
    layer.setScaleBasedVisibility(False)
    layer.setCustomProperty("suomenvaylat/style_fallback", True)


def validate_sld_resources(path):
    root = ss.parse_xml(path.read_bytes())
    for item in root.iter():
        if ss.local(item) != "OnlineResource":
            continue
        href = item.get("{" + ss.XLINK + "}href", "")
        target = Path(href)
        if not target.is_absolute():
            target = path.parent / target
        if not href or not target.is_file():
            raise ss.StyleError("Tyylin kuvasymbolia ei saatu ladattua")


def ensure_visible_renderer(layer):
    """Keep native classes and give otherwise unclassified features a symbol."""
    renderer = layer.renderer()
    context = QgsRenderContext()
    context.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    available = {field.name() for field in layer.fields()}
    required = set(renderer.usedAttributes(context))
    missing = required - available - {"#!allattributes!#"}
    if missing:
        raise ss.StyleError("Tyylin kenttiä puuttuu aineistosta: " + ", ".join(sorted(missing))[:200])
    if not any(symbol.symbolLayerCount() and symbol.opacity() > 0 for symbol in renderer.symbols(context)):
        raise ss.StyleError("Tyyli ei sisällä näkyvää geometriasymbolia")
    if not isinstance(renderer, QgsRuleBasedRenderer):
        return
    root = QgsRuleBasedRenderer.Rule(None)
    source = renderer.rootRule().clone()
    source.setLabel("Rajapinnan symboliikka")
    root.appendChild(source)
    other = QgsRuleBasedRenderer.Rule(fallback_symbol(layer))
    other.setLabel("Luokittelemattomat kohteet")
    other.setIsElse(True)
    root.appendChild(other)
    visible = QgsRuleBasedRenderer(root)
    renderer.copyRendererData(visible)
    layer.setRenderer(visible)
    layer.setScaleBasedVisibility(False)
    layer.setCustomProperty("suomenvaylat/style_fallback", True)
