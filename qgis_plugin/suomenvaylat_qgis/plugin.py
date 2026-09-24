"""Native QGIS interface for browsing and downloading Finnish map data."""

from pathlib import Path
import hashlib
from qgis.PyQt.QtGui import QIcon

from qgis.PyQt.QtCore import Qt, QUrlQuery
from qgis.PyQt.QtWidgets import (
    QAction, QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressDialog, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
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


class DownloadCanceled(Exception):
    """Stop a download without reporting cancellation as a layer failure."""


class SuomenvaylatDialog(QDialog):
    def __init__(self, parent=None, plugin=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("Suomenväylät — QGIS")
        self.resize(820, 780)
        self.entries = []
        self._selected_entries = {}
        self._authcfg_by_credential = {}
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        tabs.addTab(self._data_tab(), "Hae aineistoja")
        tabs.addTab(self._background_tab(), "Lisää taustakartta")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _scroll_page(content, layout):
        content.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _row_widget(layout):
        row = QWidget()
        row.setLayout(layout)
        return row

    def _data_tab(self):
        page = QWidget()
        layout = QVBoxLayout()
        intro = QLabel("Hae palveluiden tasot, valitse aineistot ja rajaa ne tarvittaessa alueella.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        sources_group = QGroupBox("1. Valitse tietolähteet")
        sources_layout = QVBoxLayout(sources_group)
        self.sources = QListWidget()
        self.sources.setMaximumHeight(130)
        source_names = list(dict.fromkeys(list(WFS_SOURCES) + list(OGC_SOURCES) +
                                          ["Kapsi", "OpenStreetMap", "MML Karttakuva"]))
        for source_name in source_names:
            item = QListWidgetItem(source_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if source_name == "Väylä" else Qt.Unchecked)
            self.sources.addItem(item)
        self.sources.itemChanged.connect(self._update_credential_visibility)
        sources_layout.addWidget(self.sources)
        layout.addWidget(sources_group)

        self.credentials_group = QGroupBox("Tunnukset valituille lähteille")
        credentials_form = QFormLayout(self.credentials_group)
        credentials_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._credential_rows = {}
        self.mml_key = QLineEdit()
        self.mml_key.setEchoMode(QLineEdit.Password)
        self._add_credential_row(credentials_form, "MML", "MML API-avain", self.mml_key)
        self.karttapaikka_key = QLineEdit()
        self.karttapaikka_key.setEchoMode(QLineEdit.Password)
        self._add_credential_row(credentials_form, "Karttapaikka", "Karttapaikka API-avain", self.karttapaikka_key)
        self.aino_token = QLineEdit()
        self.aino_token.setEchoMode(QLineEdit.Password)
        if self.plugin and self.plugin.aino_token:
            self.aino_token.setText(self.plugin.aino_token)
        self._add_credential_row(credentials_form, "Aino", "Aino-token", self.aino_token)
        self.karttakuva_user = QLineEdit()
        self._add_credential_row(credentials_form, "MML Karttakuva", "MML Karttakuva -tunnus", self.karttakuva_user)
        self.karttakuva_password = QLineEdit()
        self.karttakuva_password.setEchoMode(QLineEdit.Password)
        self._add_credential_row(credentials_form, "MML Karttakuva", "MML Karttakuva -salasana", self.karttakuva_password)
        credentials_help = QLabel("Tunnuksia kysytään vain valituilta palveluilta. Live-tasojen tunnistus tallentuu QGISin tunnistautumistietokantaan.")
        credentials_help.setWordWrap(True)
        credentials_form.addRow("", credentials_help)
        self.credentials_group.setVisible(False)
        layout.addWidget(self.credentials_group)

        catalog_group = QGroupBox("2. Hae ja valitse aineistot")
        catalog_layout = QVBoxLayout(catalog_group)
        catalog_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hae nimellä tai tunnuksella…")
        self.search.textChanged.connect(self._filter_catalog)
        catalog_row.addWidget(self.search)
        refresh = QPushButton("Hae tasot")
        refresh.clicked.connect(self._load_catalog)
        catalog_row.addWidget(refresh)
        catalog_layout.addLayout(catalog_row)

        selection_buttons = QHBoxLayout()
        select_visible = QPushButton("Valitse näkyvät")
        select_visible.clicked.connect(self._select_visible_layers)
        selection_buttons.addWidget(select_visible)
        clear_selection = QPushButton("Tyhjennä valinnat")
        clear_selection.clicked.connect(self._clear_layer_selection)
        selection_buttons.addWidget(clear_selection)
        self.selection_summary = QLabel("0 aineistoa valittuna")
        selection_buttons.addWidget(self.selection_summary)
        selection_buttons.addStretch(1)
        catalog_layout.addLayout(selection_buttons)

        self.layers = QListWidget()
        self.layers.setSelectionMode(QAbstractItemView.NoSelection)
        self.layers.setMinimumHeight(180)
        self.layers.itemChanged.connect(self._remember_layer_selection)
        catalog_layout.addWidget(self.layers)
        layout.addWidget(catalog_group)

        self.area_group = QGroupBox("3. Rajaa tiedostoon ladattavat aineistot")
        area_form = QFormLayout(self.area_group)
        area_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.area_type = QComboBox()
        self.area_type.addItems(["Koko Suomi", "Elinvoimakeskus", "Hyvinvointialue",
                                 "Maakunta", "Kunta/Kaupunki", "Oma aineisto"])
        self.area_type.currentTextChanged.connect(self._update_areas)
        self.area_form_label = QLabel("Aluerajaus")
        area_form.addRow(self.area_form_label, self.area_type)
        self.area_help = QLabel("Live-karttatasot lisätään suoraan projektiin eivätkä vaadi rajausta tai tallennuskansiota.")
        self.area_help.setWordWrap(True)
        area_form.addRow("", self.area_help)
        self.areas = QListWidget()
        self.areas.setMaximumHeight(145)
        self.areas_label = QLabel("Valitse alueet")
        area_form.addRow(self.areas_label, self.areas)
        self.custom_layer = QComboBox()
        self.custom_layer_label = QLabel("Oma rajausaineisto")
        area_form.addRow(self.custom_layer_label, self.custom_layer)
        self._refresh_custom_layers()
        layout.addWidget(self.area_group)

        self.output_group = QGroupBox("Tallennuskansio")
        output_layout = QHBoxLayout(self.output_group)
        self.output = QLineEdit()
        self.output.setPlaceholderText("Kohdekansio ladattaville tiedostoille")
        output_layout.addWidget(self.output)
        browse = QPushButton("Valitse…")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(browse)
        layout.addWidget(self.output_group)

        self.run_button = QPushButton("Lataa valitut aineistot")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run_download)
        self.run_button.setEnabled(False)
        layout.addWidget(self.run_button)
        layout.addStretch(1)
        self._update_areas()
        self._update_credential_visibility()
        self._update_layer_selection_state()
        return self._scroll_page(page, layout)

    def _add_credential_row(self, form, source, label_text, field):
        label = QLabel(label_text)
        form.addRow(label, field)
        self._credential_rows.setdefault(source, []).append((label, field))

    def _background_tab(self):
        page = QWidget()
        layout = QVBoxLayout()
        intro = QLabel("Valitse palvelu ja lisää live-karttataso nykyiseen QGIS-projektiin.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        background_group = QGroupBox("Taustakartta")
        form = QFormLayout(background_group)
        self.background = QComboBox()
        self.background.addItems(["MML — Taustakartta", "MML — Maastokartta", "MML — Kiinteistöjaotus",
                                  "Kapsi — Taustakartta", "Kapsi — Peruskartta", "Kapsi — Ortokuva"])
        self.background.currentTextChanged.connect(self._update_background_credentials)
        form.addRow("Karttapalvelu", self.background)
        self.background_key = QLineEdit()
        self.background_key.setEchoMode(QLineEdit.Password)
        self.background_key.setPlaceholderText("MML API-avain")
        self.background_key.textChanged.connect(self._sync_mml_key_from_background)
        self.mml_key.textChanged.connect(self._sync_background_key_from_mml)
        form.addRow("MML API-avain", self.background_key)
        self.background_key_label = form.labelForField(self.background_key)
        layout.addWidget(background_group)

        note = QLabel("MML:n vektoritiilet vaativat API-avaimen. Kapsin taustakartat eivät vaadi tunnuksia.")
        note.setWordWrap(True)
        layout.addWidget(note)
        add = QPushButton("Lisää kartalle")
        add.clicked.connect(self._add_background)
        layout.addWidget(add)
        layout.addStretch(1)
        self._update_background_credentials()
        return self._scroll_page(page, layout)

    def _update_credential_visibility(self, *_):
        selected = {self.sources.item(i).text() for i in range(self.sources.count())
                    if self.sources.item(i).checkState() == Qt.Checked}
        has_visible_credentials = False
        for source, rows in self._credential_rows.items():
            visible = source in selected
            has_visible_credentials = has_visible_credentials or visible
            for label, field in rows:
                label.setVisible(visible)
                field.setVisible(visible)
        self.credentials_group.setVisible(has_visible_credentials)

    def _sync_mml_key_from_background(self, value):
        if self.mml_key.text() != value:
            self.mml_key.setText(value)

    def _sync_background_key_from_mml(self, value):
        if self.background_key.text() != value:
            self.background_key.setText(value)

    def _update_background_credentials(self, *_):
        visible = self.background.currentText().startswith("MML — ")
        self.background_key.setVisible(visible)
        self.background_key_label.setVisible(visible)

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Valitse kohdekansio")
        if path:
            self.output.setText(path)

    def _load_catalog(self):
        try:
            self.setCursor(Qt.WaitCursor)
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.processEvents()
            self.entries = []
            errors = []
            source_names = [self.sources.item(i).text() for i in range(self.sources.count())
                            if self.sources.item(i).checkState() == Qt.Checked]
            if not source_names:
                raise ValueError("Valitse vähintään yksi rajapinta")
            loaded_entries = []
            for source_name in source_names:
                try:
                    entries, source_errors = catalog(source_name, self._key_for_source(source_name),
                                                     self.karttakuva_password.text().strip()
                                                     if source_name == "MML Karttakuva" else "")
                    loaded_entries.extend(entries)
                    errors.extend(source_errors)
                except Exception as exc:
                    errors.append(f"{source_name}: {exc}")
            self.entries = loaded_entries
            available = {self._entry_key(entry): entry for entry in self.entries}
            self._selected_entries = {
                key: available[key] for key in self._selected_entries if key in available
            }
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
        self.layers.blockSignals(True)
        try:
            self.layers.clear()
            for entry in self.entries:
                text = f"{entry['source']} — {entry['title']} ({entry['id']})"
                if query not in text.casefold():
                    continue
                key = self._entry_key(entry)
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, entry)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if key in self._selected_entries else Qt.Unchecked)
                self.layers.addItem(item)
        finally:
            self.layers.blockSignals(False)
        self._update_layer_selection_state()

    @staticmethod
    def _entry_key(entry):
        return (entry.get("source"), entry.get("id"), entry.get("endpoint"), entry.get("kind"))

    def _remember_layer_selection(self, item):
        entry = item.data(Qt.UserRole)
        key = self._entry_key(entry)
        if item.checkState() == Qt.Checked:
            self._selected_entries[key] = entry
        else:
            self._selected_entries.pop(key, None)
        self._update_layer_selection_state()

    def _select_visible_layers(self):
        for index in range(self.layers.count()):
            self.layers.item(index).setCheckState(Qt.Checked)

    def _clear_layer_selection(self):
        self._selected_entries.clear()
        self.layers.blockSignals(True)
        try:
            for index in range(self.layers.count()):
                self.layers.item(index).setCheckState(Qt.Unchecked)
        finally:
            self.layers.blockSignals(False)
        self._update_layer_selection_state()

    def _update_layer_selection_state(self):
        selected = list(self._selected_entries.values())
        count = len(selected)
        noun = "aineisto" if count == 1 else "aineistoa"
        self.selection_summary.setText(f"{count} {noun} valittuna")
        self.run_button.setEnabled(count > 0)
        live_kinds = {"karttakuva_wms", "aino_wms"}
        needs_files = any(entry.get("kind") not in live_kinds for entry in selected)
        self.area_group.setEnabled(needs_files)
        self.output_group.setEnabled(needs_files)
        if not selected:
            self.area_help.setText("Valitse aineistot listasta. Live-karttatasot eivät vaadi rajausta tai tallennuskansiota.")
        elif not needs_files:
            self.area_help.setText("Valitut live-karttatasot lisätään suoraan projektiin.")
        else:
            self.area_help.setText("Valitse alue ja tallennuskansio tiedostoina ladattaville aineistoille.")

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
        needs_area_list = area_type not in {"Koko Suomi", "Oma aineisto"}
        needs_custom_layer = area_type == "Oma aineisto"
        self.areas.setVisible(needs_area_list)
        self.areas_label.setVisible(needs_area_list)
        self.custom_layer.setVisible(needs_custom_layer)
        self.custom_layer_label.setVisible(needs_custom_layer)
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
                self.area_help.setText(f"Aluelistaa ei saatu: {exc}")
        elif area_type == "Koko Suomi":
            self.area_help.setText("Ladattavat aineistot haetaan koko Suomesta.")

    def _run_download(self):
        chosen = list(self._selected_entries.values())
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
        progress = None
        canceled = False
        successes, failures = [], []
        try:
            mask, crs = selection_geometry(area_type, names, layer) if needs_folder else (None, None)
            if needs_folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
            progress = QProgressDialog("Ladataan…", "Keskeytä", 0, len(chosen), self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            for index, entry in enumerate(chosen):
                if progress.wasCanceled():
                    canceled = True
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
                                raise DownloadCanceled()
                        count = download(entry, mask, crs, path,
                                         self._key_for_source(entry["source"]), update_count)
                        successes.append(f"{entry['title']}: {count} {'rasteri' if entry['kind'] == 'kapsi_wms' else 'kohdetta'}")
                except DownloadCanceled:
                    if path is not None:
                        path.unlink(missing_ok=True)
                    canceled = True
                    break
                except Exception as exc:
                    if path is not None:
                        path.unlink(missing_ok=True)
                    failures.append(f"{entry['title']}: {exc}")
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc))
            return
        finally:
            if progress is not None:
                progress.close()
        status = "Lataus keskeytettiin" if canceled else "Lataus valmis"
        message = f"{status}: {len(successes)} tasoa onnistui, {len(failures)} epäonnistui."
        details = "\n".join(successes + failures)
        if details:
            message += "\n\n" + details
        QMessageBox.information(self, "Suomenväylät", message)

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
