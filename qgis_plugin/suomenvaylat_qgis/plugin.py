"""Native QGIS interface for browsing and downloading Finnish map data."""

from pathlib import Path
from qgis.PyQt.QtGui import QIcon

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAction, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressDialog, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsDataSourceUri, QgsProject, QgsVectorLayer, QgsVectorTileLayer

from .services import WFS_SOURCES, OGC_SOURCES, KAPSI_SERVICES, _request_json, area_choices, catalog, download, selection_geometry

MML_TILEJSON = {
    "Taustakartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/ETRS-TM35FIN/tilejson.json",
    "Maastokartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/ETRS-TM35FIN/tilejson.json",
    "Kiinteistöjaotus": "https://avoin-karttakuva.maanmittauslaitos.fi/kiinteisto-avoin/v3/kiinteistojaotus/ETRS-TM35FIN/tilejson.json",
}


class SuomenvaylatDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Suomenväylät — QGIS")
        self.resize(680, 620)
        self.entries = []
        self._mml_authcfg = None
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        tabs.addTab(self._data_tab(), "Aineistot")
        tabs.addTab(self._background_tab(), "Taustakartat")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _data_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItems(list(dict.fromkeys(list(WFS_SOURCES) + list(OGC_SOURCES) +
                                                ["Kapsi", "OpenStreetMap"])))
        form.addRow("Rajapinta", self.source)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("MML tai Karttapaikka, jos tarvitaan")
        form.addRow("API-avain", self.key)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suodata tasoluetteloa…")
        self.search.textChanged.connect(self._filter_catalog)
        row.addWidget(self.search)
        refresh = QPushButton("Hae tasoluettelo")
        refresh.clicked.connect(self._load_catalog)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.layers = QListWidget()
        layout.addWidget(self.layers)
        area_form = QFormLayout()
        self.area_type = QComboBox()
        self.area_type.addItems(["Koko Suomi", "Elinvoimakeskus", "Hyvinvointialue",
                                 "Maakunta", "Kunta/Kaupunki", "Oma aineisto"])
        self.area_type.currentTextChanged.connect(self._update_areas)
        area_form.addRow("Aluerajaus", self.area_type)
        self.areas = QListWidget()
        self.areas.setMaximumHeight(100)
        area_form.addRow("Valitse alueet", self.areas)
        self.custom_layer = QComboBox()
        area_form.addRow("Oma rajausaineisto", self.custom_layer)
        self._refresh_custom_layers()
        layout.addLayout(area_form)
        outrow = QHBoxLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("Kohdekansio")
        outrow.addWidget(self.output)
        browse = QPushButton("Selaa…")
        browse.clicked.connect(self._choose_output)
        outrow.addWidget(browse)
        layout.addLayout(outrow)
        run = QPushButton("Lataa valitut aineistot")
        run.clicked.connect(self._run_download)
        layout.addWidget(run)
        self._update_areas()
        return page

    def _background_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Taustakarttapalvelut lisätään QGIS-projektiin live-tasoina."))
        self.background = QComboBox()
        self.background.addItems(["MML — Taustakartta", "MML — Maastokartta", "MML — Kiinteistöjaotus",
                                  "Kapsi — Taustakartta", "Kapsi — Peruskartta", "Kapsi — Ortokuva"])
        layout.addWidget(self.background)
        self.background_key = QLineEdit()
        self.background_key.setEchoMode(QLineEdit.Password)
        self.background_key.setPlaceholderText("MML API-avain")
        layout.addWidget(self.background_key)
        add = QPushButton("Lisää kartalle")
        add.clicked.connect(self._add_background)
        layout.addWidget(add)
        layout.addStretch()
        return page

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Valitse kohdekansio")
        if path:
            self.output.setText(path)

    def _load_catalog(self):
        try:
            self.setCursor(Qt.WaitCursor)
            self.entries, errors = catalog(self.source.currentText(), self.key.text().strip())
            self._filter_catalog()
            if errors:
                QMessageBox.warning(self, "Suomenväylät", "Osa palveluista epäonnistui:\n" + "\n".join(errors))
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc))
        finally:
            self.unsetCursor()

    def _filter_catalog(self):
        query = self.search.text().casefold()
        selected = {self.layers.item(i).data(Qt.UserRole)["id"] for i in range(self.layers.count())
                    if self.layers.item(i).checkState() == Qt.Checked}
        self.layers.clear()
        for entry in self.entries:
            text = f"{entry['title']} ({entry['id']})"
            if query not in text.casefold():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if entry["id"] in selected else Qt.Unchecked)
            self.layers.addItem(item)

    def _refresh_custom_layers(self):
        self.custom_layer.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.geometryType() in (1, 2):
                self.custom_layer.addItem(layer.name(), layer.id())

    def _update_areas(self):
        self.areas.clear()
        area_type = self.area_type.currentText()
        self.areas.setEnabled(area_type not in {"Koko Suomi", "Oma aineisto"})
        self.custom_layer.setEnabled(area_type == "Oma aineisto")
        if area_type == "Oma aineisto":
            self._refresh_custom_layers()
        elif area_type != "Koko Suomi":
            try:
                for name in area_choices(area_type):
                    item = QListWidgetItem(name)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Unchecked)
                    self.areas.addItem(item)
            except Exception as exc:
                QMessageBox.critical(self, "Suomenväylät", str(exc))

    def _run_download(self):
        chosen = [self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count())
                  if self.layers.item(i).checkState() == Qt.Checked]
        folder = self.output.text().strip()
        if not chosen or not folder:
            QMessageBox.warning(self, "Suomenväylät", "Valitse tasot ja kohdekansio.")
            return
        area_type = self.area_type.currentText()
        names = [self.areas.item(i).text() for i in range(self.areas.count())
                 if self.areas.item(i).checkState() == Qt.Checked]
        if area_type not in {"Koko Suomi", "Oma aineisto"} and not names:
            QMessageBox.warning(self, "Suomenväylät", "Valitse vähintään yksi alue.")
            return
        layer = QgsProject.instance().mapLayer(self.custom_layer.currentData()) if area_type == "Oma aineisto" else None
        try:
            mask, crs = selection_geometry(area_type, names, layer)
            Path(folder).mkdir(parents=True, exist_ok=True)
            progress = QProgressDialog("Ladataan…", "Keskeytä", 0, len(chosen), self)
            successes, failures = [], []
            for index, entry in enumerate(chosen):
                if progress.wasCanceled():
                    break
                progress.setValue(index)
                progress.setLabelText(entry["title"])
                from qgis.PyQt.QtWidgets import QApplication
                QApplication.processEvents()
                base = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in entry["id"].split(":")[-1])[:60]
                extension = ".tif" if entry["kind"] == "kapsi_wms" else ".gpkg"
                path = Path(folder) / f"{base}{extension}"
                suffix = 2
                while path.exists():
                    path = Path(folder) / f"{base}_{suffix}{extension}"
                    suffix += 1
                try:
                    def update_count(count):
                        progress.setLabelText(f"{entry['title']} — {count} kohdetta")
                        QApplication.processEvents()
                        if progress.wasCanceled():
                            raise RuntimeError("Käyttäjä keskeytti")
                    count = download(entry, mask, crs, path, self.key.text().strip(), update_count)
                    successes.append(f"{entry['title']}: {count} {'rasteri' if entry['kind'] == 'kapsi_wms' else 'kohdetta'}")
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    failures.append(f"{entry['title']}: {exc}")
            progress.close()
            QMessageBox.information(self, "Suomenväylät",
                                    f"{len(successes)} tasoa onnistui, {len(failures)} epäonnistui.\n\n" +
                                    "\n".join(successes + failures))
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc))

    def _add_background(self):
        from qgis.core import QgsRasterLayer
        title = self.background.currentText()
        if title.startswith("MML — "):
            self._add_mml_background(title.removeprefix("MML — "))
            return
        services = {
            "Kapsi — Taustakartta": ("https://tiles.kartat.kapsi.fi/taustakartta", "taustakartta"),
            "Kapsi — Peruskartta": ("https://tiles.kartat.kapsi.fi/peruskartta", "peruskartta"),
            "Kapsi — Ortokuva": ("https://tiles.kartat.kapsi.fi/ortokuva", "ortokuva"),
        }
        url, layer_name = services[title]
        uri = f"crs=EPSG:3067&dpiMode=7&format=image/png&layers={layer_name}&styles=&url={url}"
        layer = QgsRasterLayer(uri, title, "wms")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            QMessageBox.information(self, "Suomenväylät", f"Lisättiin: {title}")
        else:
            QMessageBox.critical(self, "Suomenväylät", "WMS-tasoa ei voitu avata")

    def _add_mml_background(self, map_name):
        key = self.background_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Suomenväylät", "MML:n vektoritiilit vaativat API-avaimen.")
            return
        try:
            tilejson = _request_json(MML_TILEJSON[map_name], key)
            tiles = tilejson.get("tiles") or []
            if not tiles:
                raise RuntimeError("MML:n TileJSON ei sisältänyt tiiliosoitetta")
            if not self._mml_authcfg:
                config = QgsAuthMethodConfig()
                config.setName("Suomenväylät — MML API")
                config.setMethod("Basic")
                config.setConfig("username", key)
                config.setConfig("password", "")
                result = QgsApplication.authManager().storeAuthenticationConfig(config)
                if not result[0]:
                    raise RuntimeError("QGISin tunnistautumisasetusta ei voitu tallentaa")
                self._mml_authcfg = result[1].id()
            uri = QgsDataSourceUri()
            uri.setParam("type", "xyz")
            uri.setParam("url", tiles[0])
            uri.setAuthConfigId(self._mml_authcfg)
            layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode("utf-8"), f"MML — {map_name}")
            if not layer.isValid():
                raise RuntimeError("QGIS ei voinut avata MML-vektoritiilitasoa")
            QgsProject.instance().addMapLayer(layer)
            QMessageBox.information(self, "Suomenväylät", f"Lisättiin: MML — {map_name}")
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc).replace(key, "[PIILOTETTU]"))


class SuomenvaylatPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        self.action = QAction(QIcon(str(Path(__file__).parent / "icon.png")), "Suomenväylät", self.iface.mainWindow())
        self.action.triggered.connect(self.open)
        self.iface.addPluginToMenu("Suomenväylät", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginMenu("Suomenväylät", self.action)
        self.iface.removeToolBarIcon(self.action)

    def open(self):
        self.dialog = SuomenvaylatDialog(self.iface.mainWindow())
        self.dialog.show()
