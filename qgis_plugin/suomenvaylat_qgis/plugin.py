"""Native QGIS interface for browsing and downloading Finnish map data."""

from pathlib import Path
import hashlib
from qgis.PyQt.QtGui import QIcon

from qgis.PyQt.QtCore import Qt, QUrlQuery
from qgis.PyQt.QtWidgets import (
    QAction, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressDialog, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import (QgsApplication, QgsAuthMethodConfig, QgsDataSourceUri,
                       QgsNetworkAccessManager, QgsProject, QgsSettings,
                       QgsVectorLayer, QgsVectorTileLayer)

from .services import WFS_SOURCES, OGC_SOURCES, KAPSI_SERVICES, _request_json, area_choices, catalog, download, selection_geometry

MML_TILEJSON = {
    "Taustakartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/ETRS-TM35FIN/tilejson.json",
    "Maastokartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/ETRS-TM35FIN/tilejson.json",
    "Kiinteistöjaotus": "https://avoin-karttakuva.maanmittauslaitos.fi/kiinteisto-avoin/v3/kiinteistojaotus/ETRS-TM35FIN/tilejson.json",
}


class SuomenvaylatDialog(QDialog):
    def __init__(self, parent=None, plugin=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("Suomenväylät — QGIS")
        self.resize(720, 750)
        self.entries = []
        self._authcfg_by_credential = {}
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
        self.sources = QListWidget()
        self.sources.setMaximumHeight(115)
        source_names = list(dict.fromkeys(list(WFS_SOURCES) + list(OGC_SOURCES) +
                                          ["Kapsi", "OpenStreetMap", "MML Karttakuva"]))
        for source_name in source_names:
            item = QListWidgetItem(source_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if source_name == "Väylä" else Qt.Unchecked)
            self.sources.addItem(item)
        form.addRow("Rajapinnat", self.sources)
        self.mml_key = QLineEdit()
        self.mml_key.setEchoMode(QLineEdit.Password)
        form.addRow("MML API-avain", self.mml_key)
        self.karttapaikka_key = QLineEdit()
        self.karttapaikka_key.setEchoMode(QLineEdit.Password)
        form.addRow("Karttapaikka API-avain", self.karttapaikka_key)
        self.aino_token = QLineEdit()
        self.aino_token.setEchoMode(QLineEdit.Password)
        if self.plugin and self.plugin.aino_token:
            self.aino_token.setText(self.plugin.aino_token)
        form.addRow("Aino-token", self.aino_token)
        self.karttakuva_user = QLineEdit()
        form.addRow("MML Karttakuva -tunnus", self.karttakuva_user)
        self.karttakuva_password = QLineEdit()
        self.karttakuva_password.setEchoMode(QLineEdit.Password)
        form.addRow("MML Karttakuva -salasana", self.karttakuva_password)
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
            self.entries = []
            errors = []
            source_names = [self.sources.item(i).text() for i in range(self.sources.count())
                            if self.sources.item(i).checkState() == Qt.Checked]
            if not source_names:
                raise ValueError("Valitse vähintään yksi rajapinta")
            for source_name in source_names:
                try:
                    entries, source_errors = catalog(source_name, self._key_for_source(source_name),
                                                     self.karttakuva_password.text().strip()
                                                     if source_name == "MML Karttakuva" else "")
                    self.entries.extend(entries)
                    errors.extend(source_errors)
                except Exception as exc:
                    errors.append(f"{source_name}: {exc}")
            self._filter_catalog()
            if not self.entries:
                raise RuntimeError("Yhdestäkään valitusta palvelusta ei löytynyt tasoja. " + "; ".join(errors))
            if errors:
                QMessageBox.warning(self, "Suomenväylät", "Osa palveluista epäonnistui:\n" + "\n".join(errors))
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc))
        finally:
            self.unsetCursor()

    def _filter_catalog(self):
        query = self.search.text().casefold()
        selected = {(self.layers.item(i).data(Qt.UserRole)["source"],
                     self.layers.item(i).data(Qt.UserRole)["id"],
                     self.layers.item(i).data(Qt.UserRole).get("endpoint")) for i in range(self.layers.count())
                    if self.layers.item(i).checkState() == Qt.Checked}
        self.layers.clear()
        for entry in self.entries:
            text = f"{entry['source']} — {entry['title']} ({entry['id']})"
            if query not in text.casefold():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if (entry["source"], entry["id"], entry.get("endpoint")) in selected
                               else Qt.Unchecked)
            self.layers.addItem(item)

    def _key_for_source(self, source_name):
        widget = {"MML": self.mml_key, "Karttapaikka": self.karttapaikka_key,
                  "Aino": self.aino_token, "MML Karttakuva": self.karttakuva_user}.get(source_name)
        return widget.text().strip() if widget else ""

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
        live_kinds = {"karttakuva_wms", "aino_wms"}
        needs_folder = any(entry["kind"] not in live_kinds for entry in chosen)
        if not chosen or (needs_folder and not folder):
            QMessageBox.warning(self, "Suomenväylät", "Valitse tasot ja ladattaville aineistoille kohdekansio.")
            return
        area_type = self.area_type.currentText()
        names = [self.areas.item(i).text() for i in range(self.areas.count())
                 if self.areas.item(i).checkState() == Qt.Checked]
        if needs_folder and area_type not in {"Koko Suomi", "Oma aineisto"} and not names:
            QMessageBox.warning(self, "Suomenväylät", "Valitse vähintään yksi alue.")
            return
        layer = QgsProject.instance().mapLayer(self.custom_layer.currentData()) if area_type == "Oma aineisto" else None
        try:
            mask, crs = selection_geometry(area_type, names, layer) if needs_folder else (None, None)
            if needs_folder:
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
                path = None
                if entry["kind"] not in live_kinds:
                    base = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in entry["id"].split(":")[-1])[:60]
                    extension = ".tif" if entry["kind"] == "kapsi_wms" else ".gpkg"
                    path = Path(folder) / f"{base}{extension}"
                    suffix = 2
                    while path.exists():
                        path = Path(folder) / f"{base}_{suffix}{extension}"
                        suffix += 1
                try:
                    if entry["kind"] == "karttakuva_wms":
                        self._add_karttakuva(entry)
                        successes.append(f"{entry['title']}: live-karttataso")
                    elif entry["kind"] == "aino_wms":
                        self._add_aino_wms(entry)
                        successes.append(f"{entry['title']}: live-karttataso")
                    else:
                        def update_count(count):
                            progress.setLabelText(f"{entry['title']} — {count} kohdetta")
                            QApplication.processEvents()
                            if progress.wasCanceled():
                                raise RuntimeError("Käyttäjä keskeytti")
                        count = download(entry, mask, crs, path,
                                         self._key_for_source(entry["source"]), update_count)
                        successes.append(f"{entry['title']}: {count} {'rasteri' if entry['kind'] == 'kapsi_wms' else 'kohdetta'}")
                except Exception as exc:
                    if path is not None:
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
            authcfg = self._basic_auth("Suomenväylät — MML API", key, "")
            uri = QgsDataSourceUri()
            uri.setParam("type", "xyz")
            uri.setParam("url", tiles[0])
            uri.setAuthConfigId(authcfg)
            layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode("utf-8"), f"MML — {map_name}")
            if not layer.isValid():
                raise RuntimeError("QGIS ei voinut avata MML-vektoritiilitasoa")
            QgsProject.instance().addMapLayer(layer)
            QMessageBox.information(self, "Suomenväylät", f"Lisättiin: MML — {map_name}")
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc).replace(key, "[PIILOTETTU]"))

    def _basic_auth(self, name, username, password):
        digest = hashlib.sha256((name + "\0" + username + "\0" + password).encode("utf-8")).hexdigest()
        if digest in self._authcfg_by_credential:
            return self._authcfg_by_credential[digest]
        config = QgsAuthMethodConfig()
        config.setName(name)
        config.setMethod("Basic")
        config.setConfig("username", username)
        config.setConfig("password", password)
        result = QgsApplication.authManager().storeAuthenticationConfig(config)
        if not result[0]:
            raise RuntimeError("QGISin tunnistautumisasetusta ei voitu tallentaa")
        authcfg = result[1].id()
        self._authcfg_by_credential[digest] = authcfg
        return authcfg

    def _add_karttakuva(self, entry):
        from qgis.core import QgsRasterLayer
        username = self.karttakuva_user.text().strip()
        password = self.karttakuva_password.text().strip()
        if not username or not password:
            raise ValueError("MML Karttakuva vaatii käyttäjätunnuksen ja salasanan")
        authcfg = self._basic_auth("Suomenväylät — MML Karttakuva", username, password)
        uri = QgsDataSourceUri()
        for name, value in {"crs": "EPSG:3067", "dpiMode": "7", "format": "image/png",
                            "layers": entry["id"], "styles": "", "url": entry["endpoint"],
                            "authcfg": authcfg}.items():
            uri.setParam(name, value)
        layer = QgsRasterLayer(bytes(uri.encodedUri()).decode("utf-8"),
                               f"MML Karttakuva — {entry['title']}", "wms")
        if not layer.isValid():
            raise RuntimeError(f"MML Karttakuva -tasoa ei voitu avata: {entry['title']}")
        QgsProject.instance().addMapLayer(layer)

    def _add_aino_wms(self, entry):
        from qgis.core import QgsRasterLayer
        token = self.aino_token.text().strip()
        if not token:
            raise ValueError("Aino WMS vaatii tokenin")
        if not self.plugin:
            raise RuntimeError("Aino WMS vaatii aktivoidun Suomenväylät-lisäosan")
        self.plugin.set_aino_token(token)
        uri = QgsDataSourceUri()
        for name, value in {"crs": "EPSG:3067", "dpiMode": "7", "format": "image/png",
                            "layers": entry["id"], "styles": "", "url": entry["endpoint"]}.items():
            uri.setParam(name, value)
        layer = QgsRasterLayer(bytes(uri.encodedUri()).decode("utf-8"),
                               f"Aino — {entry['title']}", "wms")
        if not layer.isValid():
            raise RuntimeError(f"Aino WMS -tasoa ei voitu avata: {entry['title']}")
        QgsProject.instance().addMapLayer(layer)


class SuomenvaylatPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.aino_token = ""
        self._aino_preprocessor_id = None

    def _preprocess_aino(self, request):
        url = request.url()
        if not self.aino_token or url.host().casefold() != "aino.sitowise.com" or not url.path().startswith("/ows"):
            return
        query = QUrlQuery(url)
        if not query.hasQueryItem("token"):
            query.addQueryItem("token", self.aino_token)
            url.setQuery(query)
            request.setUrl(url)

    def set_aino_token(self, token):
        self.aino_token = token
        config = QgsAuthMethodConfig()
        config.setName("Suomenväylät — Aino token")
        config.setMethod("Basic")
        config.setConfig("username", token)
        config.setConfig("password", "")
        result = QgsApplication.authManager().storeAuthenticationConfig(config)
        if result[0]:
            QgsSettings().setValue("Suomenvaylat/ainoAuthCfg", result[1].id())

    def initGui(self):
        authcfg = QgsSettings().value("Suomenvaylat/ainoAuthCfg", "")
        if authcfg:
            result = QgsApplication.authManager().loadAuthenticationConfig(authcfg, QgsAuthMethodConfig(), True)
            if result[0]:
                self.aino_token = result[1].config("username")
        self._aino_preprocessor_id = QgsNetworkAccessManager.setRequestPreprocessor(self._preprocess_aino)
        self.action = QAction(QIcon(str(Path(__file__).parent / "icon.png")), "Suomenväylät", self.iface.mainWindow())
        self.action.triggered.connect(self.open)
        self.iface.addPluginToMenu("Suomenväylät", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self._aino_preprocessor_id:
            QgsNetworkAccessManager.removeRequestPreprocessor(self._aino_preprocessor_id)
            self._aino_preprocessor_id = None
        self.iface.removePluginMenu("Suomenväylät", self.action)
        self.iface.removeToolBarIcon(self.action)

    def open(self):
        self.dialog = SuomenvaylatDialog(self.iface.mainWindow(), plugin=self)
        self.dialog.show()
