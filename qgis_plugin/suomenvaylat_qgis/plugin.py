"""Native QGIS interface for browsing and downloading Finnish map data."""

from pathlib import Path
from qgis.PyQt.QtGui import QIcon

from qgis.PyQt.QtCore import Qt, QUrlQuery
from qgis.PyQt.QtWidgets import (
    QAction, QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import (QgsApplication, QgsAuthMethodConfig, QgsDataSourceUri,
                       QgsNetworkAccessManager, QgsProject, QgsSettings, QgsTask,
                       QgsVectorLayer, QgsVectorTileLayer)

from .services import (WFS_SOURCES, OGC_SOURCES, _add_project_layer, _geometry_type_value,
                       _request_json, area_choices, catalog, download, selection_geometry)

MML_TILEJSON = {
    # QGIS's XYZ vector tile provider uses the Web Mercator tile matrix.
    "Taustakartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/WGS84_Pseudo-Mercator/tilejson.json",
    "Maastokartta": "https://avoin-karttakuva.maanmittauslaitos.fi/vectortiles/tilejson/taustakartta/1.0.0/taustakartta/default/v21/WGS84_Pseudo-Mercator/tilejson.json",
    "Kiinteistöjaotus": "https://avoin-karttakuva.maanmittauslaitos.fi/kiinteisto-avoin/v3/kiinteistojaotus/WGS84_Pseudo-Mercator/tilejson.json",
}


class DownloadCanceled(Exception):
    """Stop a download without reporting cancellation as a layer failure."""


RASTER_KINDS = {"kapsi_wms", "oskari_wms", "oskari_wmts"}
LIVE_KINDS = {"karttakuva_wms", "aino_wms"}


def store_basic_auth(name, username, password):
    """Luo tai päivitä nimetty Basic-tunnistautumisasetus.

    Samaa nimeä käytetään uudelleen, jotta QGISin tunnistautumistietokantaan
    ei kerry uutta kopiota jokaisella tason lisäyskerralla.
    """
    manager = QgsApplication.authManager()
    for config_id, config in manager.availableAuthMethodConfigs().items():
        if config.name() != name or config.method() != "Basic":
            continue
        result = manager.loadAuthenticationConfig(config_id, QgsAuthMethodConfig(), True)
        if not result[0]:
            continue
        existing = result[1]
        if existing.config("username") != username or existing.config("password") != password:
            existing.setConfig("username", username)
            existing.setConfig("password", password)
            if not manager.updateAuthenticationConfig(existing):
                raise RuntimeError("QGISin tunnistautumisasetusta ei voitu päivittää")
        return config_id
    config = QgsAuthMethodConfig()
    config.setName(name)
    config.setMethod("Basic")
    config.setConfig("username", username)
    config.setConfig("password", password)
    result = QgsApplication.authManager().storeAuthenticationConfig(config)
    if not result[0]:
        raise RuntimeError("QGISin tunnistautumisasetusta ei voitu tallentaa")
    return result[1].id()


class FunctionTask(QgsTask):
    """Aja verkko- ja tiedostotyö taustalla ja palauta tulos pääsäikeeseen."""

    def __init__(self, description, work, on_done):
        super().__init__(description, QgsTask.CanCancel)
        self._work = work
        self._on_done = on_done
        self.result_value = None
        self.error = None
        self.status_text = ""

    def run(self):
        try:
            self.result_value = self._work(self)
            return True
        except Exception as exc:  # noqa: BLE001 - raportoidaan finished-vaiheessa
            self.error = exc
            return False

    def finished(self, result):
        self._on_done(self, result)


class SuomenvaylatDialog(QDialog):
    def __init__(self, parent=None, plugin=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("Suomenväylät — QGIS")
        self.resize(820, 780)
        self.entries = []
        self._selected_entries = {}
        self._active_tasks = []
        # Smoke-testit ajavat taustatehtävät suoraan ilman QGISin tehtävähallintaa.
        self.run_tasks_synchronously = False
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        tabs.addTab(self._data_tab(), "Hae aineistoja")
        tabs.addTab(self._background_tab(), "Lisää taustakartta")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _project_directory():
        project_file = QgsProject.instance().fileName()
        return str(Path(project_file).parent) if project_file else ""

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
                                          ["Kapsi", "OpenStreetMap", "MML Karttakuva", "Traficom Oskari"]))
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
        self.refresh_button = QPushButton("Hae tasot")
        self.refresh_button.clicked.connect(self._load_catalog)
        catalog_row.addWidget(self.refresh_button)
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
        self.output.setText(self._project_directory())
        output_layout.addWidget(self.output)
        browse = QPushButton("Valitse…")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(browse)
        layout.addWidget(self.output_group)

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Lataa valitut aineistot")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run_download)
        self.run_button.setEnabled(False)
        run_row.addWidget(self.run_button, 1)
        self.cancel_button = QPushButton("Keskeytä")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_download)
        run_row.addWidget(self.cancel_button)
        layout.addLayout(run_row)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._download_task = None
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
        path = QFileDialog.getExistingDirectory(self, "Valitse kohdekansio", self.output.text().strip())
        if path:
            self.output.setText(path)

    def _start_task(self, task):
        if self.run_tasks_synchronously:
            task.finished(task.run())
            return
        self._active_tasks.append(task)
        task.taskCompleted.connect(lambda: self._forget_task(task))
        task.taskTerminated.connect(lambda: self._forget_task(task))
        QgsApplication.taskManager().addTask(task)

    def _forget_task(self, task):
        if task in self._active_tasks:
            self._active_tasks.remove(task)

    def _load_catalog(self):
        source_names = [self.sources.item(i).text() for i in range(self.sources.count())
                        if self.sources.item(i).checkState() == Qt.Checked]
        if not source_names:
            self.entries = []
            self._filter_catalog()
            return
        # Tunnukset luetaan käyttöliittymästä pääsäikeessä ennen taustatyötä.
        requests = [(name, self._key_for_source(name),
                     self.karttakuva_password.text().strip() if name == "MML Karttakuva" else "")
                    for name in source_names]

        def work(task):
            loaded, errors = [], []
            for index, (source_name, key, password) in enumerate(requests):
                if task.isCanceled():
                    break
                try:
                    entries, source_errors = catalog(source_name, key, password)
                    loaded.extend(entries)
                    errors.extend(source_errors)
                except Exception as exc:
                    errors.append(f"{source_name}: {exc}")
                task.setProgress(100.0 * (index + 1) / len(requests))
            return loaded, errors

        self.refresh_button.setEnabled(False)
        self.setCursor(Qt.WaitCursor)
        self.status_label.setText("Haetaan tasoluetteloa…")
        self._start_task(FunctionTask("Suomenväylät: tasoluettelo", work, self._catalog_loaded))

    def _catalog_loaded(self, task, ok):
        self.refresh_button.setEnabled(True)
        self.unsetCursor()
        self.status_label.setText("")
        if not ok:
            QMessageBox.critical(self, "Suomenväylät", str(task.error or "Tasoluettelon haku keskeytyi"))
            return
        loaded_entries, errors = task.result_value
        self.entries = loaded_entries
        available = {self._entry_key(entry): entry for entry in self.entries}
        self._selected_entries = {
            key: available.get(key, entry)
            for key, entry in self._selected_entries.items()
        }
        self._filter_catalog()
        if not self.entries:
            QMessageBox.critical(self, "Suomenväylät",
                                 "Yhdestäkään valitusta palvelusta ei löytynyt tasoja. " + "; ".join(errors))
        elif errors:
            QMessageBox.warning(self, "Suomenväylät", "Osa palveluista epäonnistui:\n" + "\n".join(errors))

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
            if (isinstance(layer, QgsVectorLayer) and layer.isValid()
                    and _geometry_type_value(layer.geometryType()) in (1, 2)):
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
        if self._download_task is not None:
            return
        chosen = list(self._selected_entries.values())
        folder = self.output.text().strip()
        live = [entry for entry in chosen if entry["kind"] in LIVE_KINDS]
        files = [entry for entry in chosen if entry["kind"] not in LIVE_KINDS]
        if not chosen or (files and not folder):
            QMessageBox.warning(self, "Suomenväylät", "Valitse tasot ja ladattaville aineistoille kohdekansio.")
            return
        area_type = self.area_type.currentText()
        names = [self.areas.item(i).text() for i in range(self.areas.count())
                 if self.areas.item(i).checkState() == Qt.Checked]
        if files and area_type not in {"Koko Suomi", "Oma aineisto"} and not names:
            QMessageBox.warning(self, "Suomenväylät", "Valitse vähintään yksi alue.")
            return
        layer = QgsProject.instance().mapLayer(self.custom_layer.currentData()) if area_type == "Oma aineisto" else None
        project_crs_missing = not QgsProject.instance().crs().isValid()
        try:
            mask, crs = selection_geometry(area_type, names, layer) if files else (None, None)
            if files:
                Path(folder).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc))
            return

        successes, failures = [], []
        # Live-tasot lisätään heti pääsäikeessä: ne eivät lataa tiedostoja.
        for entry in live:
            try:
                if entry["kind"] == "karttakuva_wms":
                    self._add_karttakuva(entry)
                else:
                    self._add_aino_wms(entry)
                successes.append(f"{entry['title']}: live-karttataso")
            except Exception as exc:
                failures.append(f"{entry['title']}: {exc}")
        if not files:
            self._show_download_summary(successes, failures, False, project_crs_missing)
            return

        keys = {entry["source"]: self._key_for_source(entry["source"]) for entry in files}
        plan = []
        reserved = set()
        for entry in files:
            base = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in entry["id"].split(":")[-1])[:60]
            extension = ".tif" if entry["kind"] in RASTER_KINDS else ".gpkg"
            path = Path(folder) / f"{base}{extension}"
            suffix = 2
            while path.exists() or str(path).casefold() in reserved:
                path = Path(folder) / f"{base}_{suffix}{extension}"
                suffix += 1
            reserved.add(str(path).casefold())
            plan.append((entry, path))

        def work(task):
            result = {"successes": [], "failures": [], "layers": [], "canceled": False}
            main_thread = QgsApplication.instance().thread()

            def collect(layer):
                # Taustasäikeessä luotu taso siirretään pääsäikeeseen ennen
                # kuin finished() lisää sen projektiin.
                if not self.run_tasks_synchronously:
                    layer.moveToThread(main_thread)
                result["layers"].append(layer)

            for index, (entry, path) in enumerate(plan):
                if task.isCanceled():
                    result["canceled"] = True
                    break

                def update_count(count, index=index, entry=entry):
                    if task.isCanceled():
                        raise DownloadCanceled()
                    task.status_text = f"{entry['title']} — {count} kohdetta"
                    task.setProgress(100.0 * (index + min(count, 999) / 1000.0) / len(plan))

                task.status_text = entry["title"]
                task.setProgress(100.0 * index / len(plan))
                try:
                    count = download(entry, mask, crs, path, keys.get(entry["source"], ""),
                                     update_count, add_layer=collect)
                    unit = "rasteri" if entry["kind"] in RASTER_KINDS else "kohdetta"
                    result["successes"].append(f"{entry['title']}: {count} {unit}")
                except DownloadCanceled:
                    path.unlink(missing_ok=True)
                    result["canceled"] = True
                    break
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    result["failures"].append(f"{entry['title']}: {exc}")
            return result

        def done(task, ok):
            self._download_task = None
            self.run_button.setEnabled(bool(self._selected_entries))
            self.cancel_button.setEnabled(False)
            self.status_label.setText("")
            result = task.result_value or {"successes": [], "failures": [], "layers": [], "canceled": True}
            if not ok and task.error is not None:
                result["failures"].append(str(task.error))
            for added in result["layers"]:
                try:
                    _add_project_layer(added)
                except Exception as exc:
                    result["failures"].append(f"{added.name()}: {exc}")
            self._show_download_summary(successes + result["successes"], failures + result["failures"],
                                        result["canceled"] or task.isCanceled(), project_crs_missing)

        task = FunctionTask("Suomenväylät: aineistojen lataus", work, done)
        task.progressChanged.connect(
            lambda value: self.status_label.setText(f"{task.status_text} ({value:.0f} %)")
        )
        self._download_task = task
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Ladataan…")
        self._start_task(task)

    def _cancel_download(self):
        if self._download_task is not None:
            self._download_task.cancel()
            self.status_label.setText("Keskeytetään…")

    def _show_download_summary(self, successes, failures, canceled, project_crs_missing):
        status = "Lataus keskeytettiin" if canceled else "Lataus valmis"
        message = f"{status}: {len(successes)} tasoa onnistui, {len(failures)} epäonnistui."
        if project_crs_missing and QgsProject.instance().crs().isValid():
            message += ("\nProjektin koordinaattijärjestelmä asetettiin: "
                        f"{QgsProject.instance().crs().authid()}.")
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
            _add_project_layer(layer)
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
            authcfg = store_basic_auth("Suomenväylät — MML API", key, "")
            uri = QgsDataSourceUri()
            uri.setParam("type", "xyz")
            uri.setParam("url", tiles[0])
            uri.setAuthConfigId(authcfg)
            layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode("utf-8"), f"MML — {map_name}")
            if not layer.isValid():
                raise RuntimeError("QGIS ei voinut avata MML-vektoritiilitasoa")
            _add_project_layer(layer)
            QMessageBox.information(self, "Suomenväylät", f"Lisättiin: MML — {map_name}")
        except Exception as exc:
            QMessageBox.critical(self, "Suomenväylät", str(exc).replace(key, "[PIILOTETTU]"))

    def _add_karttakuva(self, entry):
        from qgis.core import QgsRasterLayer
        username = self.karttakuva_user.text().strip()
        password = self.karttakuva_password.text().strip()
        if not username or not password:
            raise ValueError("MML Karttakuva vaatii käyttäjätunnuksen ja salasanan")
        authcfg = store_basic_auth("Suomenväylät — MML Karttakuva", username, password)
        uri = QgsDataSourceUri()
        for name, value in {"crs": "EPSG:3067", "dpiMode": "7", "format": "image/png",
                            "layers": entry["id"], "styles": "", "url": entry["endpoint"],
                            "authcfg": authcfg}.items():
            uri.setParam(name, value)
        layer = QgsRasterLayer(bytes(uri.encodedUri()).decode("utf-8"),
                               f"MML Karttakuva — {entry['title']}", "wms")
        if not layer.isValid():
            raise RuntimeError(f"MML Karttakuva -tasoa ei voitu avata: {entry['title']}")
        _add_project_layer(layer)

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
        _add_project_layer(layer)


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
        try:
            authcfg = store_basic_auth("Suomenväylät — Aino token", token, "")
        except RuntimeError:
            return
        QgsSettings().setValue("Suomenvaylat/ainoAuthCfg", authcfg)

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
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self._aino_preprocessor_id:
            QgsNetworkAccessManager.removeRequestPreprocessor(self._aino_preprocessor_id)
            self._aino_preprocessor_id = None
        self.iface.removePluginMenu("Suomenväylät", self.action)
        self.iface.removeToolBarIcon(self.action)

    def open(self):
        # Käytä olemassa olevaa ikkunaa, jotta käynnissä oleva lataus ja
        # valinnat säilyvät, kun lisäosa avataan uudelleen.
        if self.dialog is None:
            self.dialog = SuomenvaylatDialog(self.iface.mainWindow(), plugin=self)
        else:
            self.dialog._refresh_custom_layers()
            if not self.dialog.output.text().strip():
                self.dialog.output.setText(self.dialog._project_directory())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
