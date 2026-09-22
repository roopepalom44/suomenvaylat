"""QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import SuomenvaylatPlugin
    return SuomenvaylatPlugin(iface)
