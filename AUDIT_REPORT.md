# Suomenväylät: Kokonaisvaltainen tekninen auditointiraportti (ArcGIS Pro & QGIS)

**Kohde:** Suomenväylät-työkalukokonaisuus (`c:\Users\Roope\Desktop\työkalut\suomenvaylat`)  
**Tarkastellut komponentit:**
- **ArcGIS Pro:** `Toolboxes/VaylaWFSDownloader.pyt` (8 546 riviä), C# Pro SDK Add-in (`OpenSuomenvaylatToolButton.cs`, `Config.daml`, `suomenvaylat.csproj`, .NET 8 / Pro 3.5), `service_styles.py`
- **QGIS-lisäosa:** `qgis_plugin/suomenvaylat_qgis/` (`plugin.py`, `services.py`, `service_styles.py`, `osm_geometry.py`, `metadata.txt`)
- **Laadunvarmistus ja testit:** `tests/` (176 yksikkötestiä) ja `qgis_plugin/smoke_*.py`  
**Päivämäärä:** 2026-10-11  
**Auditoinnin laajuus:** Suorituskyky (Performance), Virheensieto ja luotettavuus (Robustness & Error Handling), Arkkitehtuuri, Pariteetti ja Ylläpidettävyys (Architecture, Parity & Consistency) sekä Testikattavuus (Test Suite Audit).

---

## 1. Tiivistelmä johdolle (Executive Summary)

### 1.1 Auditoinnin laajuus ja tavoitteet
Suomenväylät on paikkatietoalan ammattilaistyökalu, jolla ladataan suomalaisia julkisia ja sopimuspohjaisia paikkatietoaineistoja (Väylävirasto, DigiRoad, Tilastokeskus, Maanmittauslaitos, Traficom Oskari, SYKE, Liiteri ja OpenStreetMap) rajatuilta maantieteellisiltä alueilta. Työkalusta on kaksi rinnakkaista toteutusta:
1. **ArcGIS Pro -laajennus:** C#-pohjainen ribbon-käynnistin (.NET 8 / ArcGIS Pro SDK 3.5) sekä Python Geoprocessing Toolbox (`Toolboxes/VaylaWFSDownloader.pyt`), joka sisältää kaksi työkalua: `VaylaWFSDownloader` ja `MMLBasemapDownloader`.
2. **QGIS-lisäosa:** Natiivi Python- ja PyQGIS-lisäosa (`suomenvaylat_qgis`), joka tarjoaa graafisen käyttöliittymän, taustasäieajon (`QgsTask`) sekä suoran GeoPackage- ja GeoTIFF-tallennuksen.

Tämän teknisen auditoinnin tavoitteena oli kartoittaa molempien toteutusten suorituskyvyn pullonkaulat, vakaus- ja virheensietoriskit, koodikantojen arkkitehtuurierot ja duplikaatiot sekä testausinfrastruktuurin puutteet.

### 1.2 Kummankin toteutuksen keskeiset vahvuudet
- **ArcGIS Pro -toteutuksen vahvuudet:**
  - **Palvelinpään CQL-suodatus:** Väylän, DigiRoadin ja Tilastokeskuksen WFS-hauissa käytetään palvelinpään `CQL_FILTER=INTERSECTS(...)` -suodatusta. Pitkät monikulmiot yksinkertaistetaan automaattisesti Douglas-Peucker -menetelmällä, ja yli pituusrajan menevät pyynnöt ohjataan automaattisesti HTTP POST -metodiin.
  - **Vankka HTTP-virheensieto:** Keskitetty `HttpTransport`-luokka sisältää kattavan uudelleenyrityslogiikan (`RETRYABLE_HTTP_STATUS`), eksponentiaalisen peruutuksen (backoff) ja suojauksen avointa uudelleenohjausta vastaan.
  - **Sivutuksen eheystarkistus:** WFS-sivutuksessa validoidaan palvelimen ilmoittama `numberMatched`-määrä ja havaitaan kesken katkenneet aineistot.
  - **Automatisoitu testaus:** Repositoriossa on 176 toimivaa yksikkötestiä, jotka testaavat työkalun apufunktioita nopeasti ilman ArcGIS Pro -lisenssiä.
- **QGIS-lisäosan vahvuudet:**
  - **Modulaarinen arkkitehtuuri:** Koodi on jaettu selkeisiin alimoduuleihin (`plugin.py`, `services.py`, `service_styles.py`, `osm_geometry.py`), mikä tekee koodista huomattavasti luettavampaa kuin ArcGIS Pron yli 8 500-rivinen monoliitti.
  - **Natiivi vektoritiilituki:** MML:n vektoritiilet ladataan suoraan Mapbox GL -tyylitiedostoilla hyödyntäen QGISin natiivia `QgsVectorTileLayer`-toteutusta.
  - **Käyttäjäystävällinen aluerajaus:** Hallinnolliset aluerajaukset (kunnat, maakunnat, ELYt) luetaan suoraan GeoPackage-paketista ja tarjotaan selkeinä valintoina.

### 1.3 Keskeiset riskit ja kriittiset haavoittuvuudet
Auditointi paljasti kuitenkin kummassakin alustassa vakavia teknisiä ongelmia, jotka heikentävät suorituskykyä, vaarantavat datan eheyden ja aiheuttavat sovellusten kaatumisia:
1. **QGISin säieturvallisuusrikkomukset (F-CRIT-01):** QGIS-lisäosa luo `QgsVectorLayer`- ja `QgsRasterLayer`-instansseja taustasäikeessä ja kutsuu laitonta `layer.moveToThread(main_thread)` -metodia. PyQGISissä tämä aiheuttaa C++-tason muistin korruptoitumista ja satunnaisia kaatumisia (`Segmentation Fault` / `Access Violation 0xC0000005`).
2. **QGISin hiljainen datan häviäminen (F-CRIT-02):** QGIS-lisäosa ei tarkista WFS-haun kokonaiskohdemäärää. Jos verkkoyhteys katkeaa kesken iteraation (esim. kohteessa 1 500 / 10 000), työkalu sulkee tiedoston ja ilmoittaa onnistumisesta käyttäjälle ilman virhettä.
3. **QGISin puuttuva palvelinpään CQL-suodatus (F-HIGH-01):** Toisin kuin ArcGIS Pro, QGIS hakee WFS-palvelimelta aina koko suorakaiteen muotoisen BBOX-alueen ja karsii ylimääräiset kohteet vasta asiakaskoneella. Tämä siirtää verkon yli jopa 5–10-kertaisen määrän dataa.
4. **ArcGIS Pron Gzip-pakkauksen esto (F-HIGH-02):** `Accept-Encoding: identity` estää HTTP Gzip -pakkauksen kaikissa WFS- ja OGC-hauissa, mikä moninkertaistaa siirrettävän datan määrän ja hidastaa latauksia merkittävästi.
5. **Käyttöliittymän jäätyminen ArcGIS Prossa (F-HIGH-04):** `canRunInBackground = False` ja `arcpy.SetProgressor`-kutsujen puuttuminen lukitsevat ArcGIS Pron käyttöliittymän kokonaan pitkien latausten ajaksi.
6. **Asennusesto tuotanto-QGISissä (F-HIGH-11):** `metadata.txt`-tiedoston kovakoodattu `qgisMinimumVersion=3.44` estää lisäosan asennuksen kaikissa nykyisissä virallisissa QGIS-versioissa (3.34 LTR ja 3.40 LTR).
7. **Testauksen epäsymmetria (F-HIGH-14):** Koodikannan 176 automatisoidusta testistä tasan 0 testaa QGIS-lisäosan lataus- tai käyttöliittymäydintä.

### 1.4 Yhteenvetotaulukko havainnoista (Metrics Summary Table)

| Vakavuusluokka | Suorituskyky (PERF) | Virheensieto (ROB) | Arkkitehtuuri & Muut (ARCH) | Yhteensä |
|---|:---:|:---:|:---:|:---:|
| **Kriittinen (Critical)** | 0 | 2 | 0 | **2** |
| **Korkea (High)** | 4 | 6 | 4 | **14** |
| **Keskitaso (Medium)** | 5 | 3 | 6 | **14** |
| **Matala (Low)** | 3 | 2 | 1 | **6** |
| **Yhteensä** | **12** | **13** | **11** | **36** |

---

## 2. Metodologia ja koodikannan yleiskatsaus (Methodology & Codebase Overview)

Auditointi suoritettiin staattisella koodianalyysillä, ajonaikaisella suoritusprofiililla ja rajapintojen protokollatason katselmoinnilla. Tarkastelun kohteena olivat seuraavat komponentit:

| Komponentti | Tiedostopolku | Rivimäärä | Rooli ja teknologia |
|---|---|:---:|---|
| **ArcGIS Pro Toolbox** | `Toolboxes/VaylaWFSDownloader.pyt` | 8 546 | WFS 1.1/2.0, OGC API, Oskari, OSM ja MML-työkalujen monoliitti |
| **ArcGIS Pro C# Add-In** | `OpenSuomenvaylatToolButton.cs` | 55 | Ribbon-painike ArcGIS Pro SDK 3.5 / .NET 8 |
| **ArcGIS Pro DAML** | `Config.daml` | 58 | UI-deklarointi (ArcGIS Pro Ribbon) |
| **QGIS Plugin UI** | `qgis_plugin/suomenvaylat_qgis/plugin.py` | 780 | PyQt5/PyQt6 GUI, QgsTask-taustasäikeet |
| **QGIS Services Core** | `qgis_plugin/suomenvaylat_qgis/services.py` | 1 125 | OGC WFS, Oskari, OGC API, Kapsi ja OSM -latauslogiikka |
| **Jaettu tyylijärjestelmä** | `service_styles.py` | 553 | OGC SLD- ja QGIS QML -tyylien nouto ja soveltaminen |
| **OSM Geometrialogiikka** | `osm_geometry.py` | 179 | OSM-viivojen ja -alueiden topologinen yhdistäminen |
| **QGIS Metatiedot** | `qgis_plugin/suomenvaylat_qgis/metadata.txt` | 13 | QGIS Plugin Manager -yhteensopivuusmääritykset |
| **Automatisoidut testit** | `tests/test_*.py` (7 tiedostoa) | ~1 850 | 176 automatisoitua yksikkötestiä (pytest) |

### Testausinfrastruktuurin nykytila
Koodikannassa on korkealaatuinen pytest-pohjainen testipatteristo (`tests/`), joka ajaa 176 testiä noin 0,55 sekunnissa hyödyntäen kevyttä `fake_arcpy`- ja `fake_http`-mock-infrastruktuuria. Testaus on kuitenkin täysin epäsymmetristä:
- **ArcGIS Pro helpers:** 176 testiä (100 % kattavuus apufunktioille, nimeämiselle, SLD-tyyleille ja suoritusputkelle).
- **QGIS-lisäosa (`services.py` ja `plugin.py`):** **0 automatisoitua testiä.** QGIS-koodille on ainoastaan erillisiä manuaalisia smoke-skriptejä (`qgis_plugin/smoke_*.py`), jotka eivät pyöri CI-putkessa ja vaativat asennetun QGIS-työpöytäsovelluksen.

---

## 3. Yksityiskohtaiset auditoinnin havainnot (Detailed Findings by Severity)

---

### 3.1 Kriittiset havainnot (Kriittinen / Critical)

#### F-CRIT-01: Taustasäikeen QgsMapLayer-luonti ja virheellinen `moveToThread`-kutsu aiheuttavat muistinkorruptoitumista ja kaatumisia
- **Osa-alue:** Virheensieto / Arkkitehtuuri
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:554–560`  
  `qgis_plugin/suomenvaylat_qgis/services.py:418, 504–525, 581, 633, 678, 813, 978`
- **Ongelman kuvaus ja juurisyy:**  
  `FunctionTask` suorittaa latauksen `work(task)` -metodissa `QgsApplication.taskManager()`:in taustasäikeessä (worker thread). Tämän taustasäikeen sisällä kutsutaan funktioita, jotka instansioivat suoraan `QgsVectorLayer`- ja `QgsRasterLayer`-olioita (`QgsVectorLayer(..., "WFS")`, `QgsVectorLayer(..., "ogr")`, `QgsRasterLayer(..., "gdal")`).  
  Lisäksi funktiossa `collect(layer)` (rivit 554–560) yritetään kiertää Qt-säierajoituksia:
  ```python
  if not self.run_tasks_synchronously:
      layer.moveToThread(main_thread)
  result["layers"].append(layer)
  ```
  Vielä vakavampaa on se, että taustasäikeessä kutsutaan funktiota `_apply_provider_style` (rivit 504–525), joka kutsuu `layer.loadSldStyle()`, `layer.saveStyleToDatabase()` ja `layer.saveNamedStyle()`.
- **Vaikutus käyttäjään ja järjestelmään:**  
  PyQGIS- ja Qt-arkkitehtuuri kieltää ehdottomasti `QgsMapLayer`-aliluokkien luomisen ja käsittelyn taustasäikeissä. `QgsVectorLayer` sisältää sisäisiä C++-viittauksia QGIS-pääsäikeen datasignaaleihin, kumouspinoon ja tyylijärjestelmään. `moveToThread()` ei siirrä näitä C++-alijärjestelmiä ja epäonnistuu välittömästi, jos oliolla on lapsiolioita. Seurauksena on satunnaisia QGIS-prosessin kaatumisia (`Segmentation Fault` tai `Access Violation 0xC0000005`), SQLite/GPKG-tietokantojen lukkiutumisia ja muistialueiden korruptoitumista monisäikeisessä ajossa.
- **Konkreettinen korjausehdotus:**  
  Taustasäikeen tehtäväksi jätetään ainoastaan datan I/O (verkkohaku ja GeoPackage/GeoTIFF-kirjoitus ilman `QgsMapLayer`-instansseja). Tasojen instansiointi, tyylien soveltaminen ja karttaprojektiin lisäys siirretään kokonaan pääsäikeelle `done()` / `finished()` -käsittelijään:

```python
# Korjaus tiedostoon plugin.py:
def work(task):
    result = {"successes": [], "failures": [], "created_files": [], "canceled": False}
    for index, (entry, path) in enumerate(plan):
        if task.isCanceled():
            result["canceled"] = True
            break
        # Taustasäie kirjoittaa vain tiedoston levylle ja palauttaa metatiedot
        count, layer_names = download_to_file(entry, mask, crs, path, progress=update_count)
        result["created_files"].append({
            "path": str(path),
            "entry": entry,
            "layer_names": layer_names,
            "count": count,
            "kind": entry["kind"]
        })
        result["successes"].append(entry["title"])
    return result

def done(task, ok):
    # Suoritetaan turvallisesti pääsäikeessä (Main GUI Thread):
    result = task.result_value or {}
    for item in result.get("created_files", []):
        path = item["path"]
        entry = item["entry"]
        for layer_name in item["layer_names"]:
            if item["kind"] in RASTER_KINDS:
                layer = QgsRasterLayer(path, entry["title"])
            else:
                uri = f"{path}|layername={layer_name}"
                layer = QgsVectorLayer(uri, entry["title"], "ogr")
            if layer.isValid():
                if isinstance(layer, QgsVectorLayer):
                    _apply_provider_style(layer, entry, path)
                _add_project_layer(layer)
```

---

#### F-CRIT-02: Hiljainen WFS-aineiston katkeaminen verkkohäiriössä ilman kokonaismäärän validointia (datan hävikki)
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:557–580`
- **Ongelman kuvaus ja juurisyy:**  
  QGIS-lisäosassa vektoritasojen lataus tapahtuu iteroimalla QGISin WFS-tietolähdettä:
  ```python
  count = 0
  try:
      for feature in layer.getFeatures(request):
          ...
          writer.addFeature(output)
          count += 1
  finally:
      del writer
  if count == 0:
      raise RuntimeError("Rajauksesta ei löytynyt kohteita")
  return count
  ```
  QGISin C++ WFS data providerissa verkkoyhteyden katkeaminen, palvelimen aikakatkaisu tai GeoServerin sisäinen sivutusvirhe kesken iteraation saa `nextFeature()`-kutsun palauttamaan hiljaa `False` ilman Python-poikkeusta. Jos lataus keskeytyy esimerkiksi 1 500 kohteen kohdalla aineistossa, jossa on 12 000 kohdetta, `for`-silmukka päättyy normaalisti. Koska `count > 0`, funktio sulkee kirjoittimen, hyväksyy vajaan GeoPackagen ja raportoi onnistumisesta.  
  ArcGIS Prossa vastaava tilanne tunnistetaan aina vertaamalla palvelimen ilmoittamaan `numberMatched`-arvoon ja tarkistamalla sivukoot, jolloin vaillinainen aineisto hylätään välittömästi virheenä.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Hiljainen datan hävikki ja virheelliset analyysitulokset. Käyttäjä luulee saaneensa koko kunnan tai tiejakson kohteet, vaikka merkittävä osa verkosta puuttuu GeoPackagesta.
- **Konkreettinen korjaus:**  
  Tarkistetaan ennen latausta tai sen aikana palvelimen ilmoittama odotettu kohdemäärä (WFS 2.0.0 `resultType=hits` tai `layer.featureCount()`). Jos iteroitu kohdemäärä poikkeaa tunnetusta kokonaismäärästä tai provider ilmoittaa virheestä, hylätään vaillinainen tiedosto:

```python
# Korjaus tiedostoon services.py (_download_vector_layer):
expected_count = layer.featureCount()
# Jos WFS provider tietää kokonaismäärän (featureCount >= 0):
if expected_count > 0 and count < expected_count:
    Path(destination).unlink(missing_ok=True)
    raise RuntimeError(
        f"WFS-lataus katkesi ennenaikaisesti verkkohäiriöön: saatiin {count}/{expected_count} kohdetta."
    )

# Tarkistetaan myös providerin virhetila iteroinnin jälkeen:
if layer.dataProvider().hasErrors():
    errors = "; ".join(layer.dataProvider().errors())
    Path(destination).unlink(missing_ok=True)
    raise RuntimeError(f"WFS-tietolähde ilmoitti virheistä latauksen aikana: {errors}")
```

---

### 3.2 Korkean vakavuusluokan havainnot (Korkea / High)

#### F-HIGH-01: Palvelinpään CQL-aluesuodatuksen puute: QGIS lataa koko BBOXin ja suodattaa vasta asiakasohjelmassa
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:63–68, 411–423, 540–565`
- **Ongelman kuvaus ja juurisyy:**  
  ArcGIS Prossa luokille "Väylä", "DigiRoad" ja "Tilastokeskus" muodostetaan geometrinen palvelinpään kysely `CQL_FILTER=INTERSECTS(geom, POLYGON(...))`. GeoServer suorittaa geometrisen leikkauksen suoraan tietokantaindeksillä ja palauttaa vain aluerajaukseen osuvat kohteet.  
  QGIS-lisäosassa (`services.py:63–68`) todetaan kommentissa, että näille aineistoille käytetään ArcGIS Pron tapaan INTERSECTS-hakua ilman leikkausta. Kuitenkin toteutuksessa asetetaan ainoastaan suorakaiteen muotoinen BBOX (`restrictToRequestBBOX=1` ja `setFilterRect`). QGIS lataa verkon yli kaikki BBOXin sisään osuvat kohteet ja suodattaa ne vasta omassa Python-silmukassaan: `if not geometry.intersects(source_mask): continue`.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Vinoilla, kapeilla tai pitkänomaisilla aluerajauksilla (esim. tiekäytävä, ratalinja, rannikkokunta tai vesistöalue) suorakaiteen muotoinen BBOX kattaa usein 3–10 kertaa suuremman maantieteellisen alueen kuin varsinainen aluerajaus. QGIS lataa kymmeniä tuhansia turhia kohteita ja kymmeniä megatavuja ylimääräistä dataa verkon yli, mikä hidastaa latausta minuuteilla ja kuormittaa julkisia palvelimia.
- **Konkreettinen korjaus:**  
  Asetetaan QGIS WFS -tietolähteelle palvelinpään `cql_filter` parametrina hyödyntäen yksinkertaistettua konveksia peitettä tai WKT-geometriaa:

```python
# Korjaus tiedostoon services.py (_open_remote_layer):
if mask_geom and entry.get("source") in UNCLIPPED_WFS_SOURCES:
    wkt = mask_geom.asWkt()
    if len(wkt) < 3000:
        uri.setParam("cql_filter", f"INTERSECTS(the_geom, {wkt})")
    else:
        # Yksinkertaistetaan liian pitkä WKT konveksiksi peitteeksi (Convex Hull)
        hull_wkt = mask_geom.convexHull().asWkt()
        uri.setParam("cql_filter", f"INTERSECTS(the_geom, {hull_wkt})")
else:
    uri.setParam("restrictToRequestBBOX", "1")
```

---

#### F-HIGH-02: `Accept-Encoding: identity` kytkee HTTP Gzip -pakkauksen pois päältä kaikista WFS/OGC-pyynnöistä
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:216–218`
- **Ongelman kuvaus ja juurisyy:**  
  `HttpTransport._single_request`-metodissa asetetaan kovakoodattu pyyntöotsake:
  ```python
  send_headers = {"Connection": "keep-alive", "Accept-Encoding": "identity"}
  ```
  `Accept-Encoding: identity` kieltää palvelinta käyttämästä gzip- tai deflate-pakkausta. GeoJSON- ja XML-vastaussisällöt ovat toisteista tekstiä (`"type": "Feature"`, koordinaattilistat, kenttien nimet). Gzip-pakkaus pienentää GeoJSON-tiedostokokoa tyypillisesti 70–85 %.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Kaikki WFS- ja OGC-aineistot siirretään verkon yli 3–6 kertaa hitaammin pakkaamattomana tekstinä, mikä kuluttaa tarpeettomasti käyttäjän ja organisaation verkkokaistaa ja pidentää latausaikoja huomattavasti.
- **Konkreettinen korjaus:**  
  Sallitaan `gzip, deflate` ja puretaan pakattu vastaus `gzip.decompress()`- ja `zlib.decompress()`-funktioilla:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt (HttpTransport._single_request):
import gzip
import zlib

send_headers = {"Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, identity"}
send_headers.update(headers or {})
...
# Vastauksen luvun jälkeen:
content_encoding = (resp.getheader("Content-Encoding") or "").lower()
if "gzip" in content_encoding:
    body = gzip.decompress(body)
elif "deflate" in content_encoding:
    try:
        body = zlib.decompress(body)
    except zlib.error:
        body = zlib.decompress(body, -zlib.MAX_WBITS)
```

---

#### F-HIGH-03: Oletusarvoinen raskas GML-formaatti GeoJSON-muodon sijaan WFS-hauissa
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:413–418`
- **Ongelman kuvaus ja juurisyy:**  
  `_open_remote_layer`-funktiossa muodostetaan WFS-yhteys:
  ```python
  uri = QgsDataSourceUri()
  for name, value in {"url": entry["endpoint"], "typename": entry["id"], "version": "auto",
                      "pagingEnabled": "true", "restrictToRequestBBOX": "1",
                      "srsname": "EPSG:3067"}.items():
      uri.setParam(name, value)
  layer = QgsVectorLayer(uri.uri(), entry["title"], "WFS")
  ```
  Parametreissa ei aseteta `outputformat`-arvoa. Tällöin QGIS WFS provider neuvottelee palvelimen kanssa ja käyttää oletuksena raskasta GML 2- tai GML 3 -XML-formaattia.
- **Vaikutus käyttäjään ja järjestelmään:**  
  GML-vastausten XML-rakenne on 4–8 kertaa suurempi tavumäärältään kuin GeoJSON (`application/json`). Esimerkiksi Väyläviraston, DigiRoadin ja Tilastokeskuksen GeoServer-palvelimet tukevat suoraan GeoJSON-tulostetta. GML:n siirtäminen ja XML-jäsentäminen kuormittaa prosessoria huomattavasti enemmän ja moninkertaistaa siirtoajat.
- **Konkreettinen korjaus:**  
  Määritetään `outputformat=application/json` palveluille, jotka tukevat GeoJSONia:

```python
# Korjaus tiedostoon services.py:
JSON_CAPABLE_SOURCES = frozenset(["Väylä", "DigiRoad", "Tilastokeskus", "Liiteri", "Syke"])

def _configure_wfs_uri(uri, entry):
    uri.setParam("url", entry["endpoint"])
    uri.setParam("typename", entry["id"])
    uri.setParam("version", "auto")
    uri.setParam("pagingEnabled", "true")
    uri.setParam("restrictToRequestBBOX", "1")
    uri.setParam("srsname", "EPSG:3067")
    if entry.get("source") in JSON_CAPABLE_SOURCES:
        uri.setParam("outputformat", "application/json")
```

---

#### F-HIGH-04: `canRunInBackground = False` ja `arcpy.SetProgressor`-kutsujen puuttuminen lukitsee käyttöliittymän
- **Osa-alue:** Suorituskyky / Käyttökokemus
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:1216, 8276`
- **Ongelman kuvaus ja juurisyy:**  
  Molemmat ArcGIS Pro -työkalut (`VaylaWFSDownloader` ja `MMLBasemapDownloader`) asettavat alustuksessaan:
  ```python
  self.canRunInBackground = False
  ```
  Tämä pakottaa työkalun ajon sovelluksen pääsäikeelle. Lisäksi koko koodissa ei käytetä kertaakaan `arcpy.SetProgressor`-kutsuja (`SetProgressor("step", ...)`, `SetProgressorLabel`, `SetProgressorPosition`).
- **Vaikutus käyttäjään ja järjestelmään:**  
  ArcGIS Pron käyttöliittymä menee säännöllisesti "Not Responding" -tilaan suurien aineistojen latauksen aikana. Geoprocessing-ikkunan edistymispalkki pysyy staattisena, eikä käyttäjä näe latauksen edistymistä, sivunumeroita tai valmistumisastetta ilman erillistä viestilokin avaamista.
- **Konkreettinen korjaus:**  
  Sallitaan tausta-ajo ArcGIS Prossa (`self.canRunInBackground = True`) ja päivitetään Geoprocessing Progressoria tasojen ja sivujen latauksen aikana:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt:
class VaylaWFSDownloader(object):
    def __init__(self):
        ...
        self.canRunInBackground = True

# execute-metodissa:
arcpy.SetProgressor("step", "Alustetaan latausta...", 0, total_layers, 1)
try:
    for layer_index, layer in enumerate(layers, 1):
        arcpy.SetProgressorLabel(f"Ladataan tasoa [{layer_index}/{total_layers}]: {layer['title']}")
        arcpy.SetProgressorPosition(layer_index - 1)
        ...
finally:
    arcpy.ResetProgressor()
```

---

#### F-HIGH-05: OGC API Features -latauksen suojaamattomat `_request_json`-kutsut ilman retry-silmukkaa ja backoffia
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:154–161, 1056–1057`
- **Ongelman kuvaus ja juurisyy:**  
  Toisin kuin ArcGIS Prossa, jossa ohimenevät HTTP-virheet (408, 425, 429, 500, 502, 503, 504) käsitellään eksponentiaalisella odotuksella, QGIS-lisäosassa `_request_json` tekee suoran kutsun ilman uudelleenyritystä:
  ```python
  def _request_json(url, key=""):
      ...
      with _urlopen(request, timeout=45) as response:
          return json.load(response)
  ```
- **Vaikutus käyttäjään ja järjestelmään:**  
  OGC API Features -latauksissa sivutetaan usein kymmeniä tai satoja sivuja peräkkäin. Yksikin hetkellinen pakettihäviö, palvelimen 503-ylikuormitus tai verkon aikakatkaisu kaataa välittömästi koko useita minuutteja kestäneen latauksen ilman toipumismahdollisuutta.
- **Konkreettinen korjaus:**  
  Lisätään keskitetty uudelleenyrityssilmukka eksponentiaalisella viiveellä:

```python
# Korjaus tiedostoon services.py:
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}

def _request_json(url, key="", max_retries=3):
    headers = {"Accept": "application/geo+json, application/json", "User-Agent": SERVICE_USER_AGENT}
    if key:
        headers["Authorization"] = "Basic " + base64.b64encode((key + ":").encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            with _urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in RETRYABLE_STATUSES and attempt < max_retries:
                time.sleep(1.0 * (2 ** (attempt - 1)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError) as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(1.0 * attempt)
                continue
            raise
    raise last_exc
```

---

#### F-HIGH-06: OGC XML `<ows:ExceptionReport>` -virheiden jäsennys puuttuu (merkkijonoleikkaus tai virheiden ohitus)
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** Molemmat (ArcGIS Pro & QGIS)
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:4114–4120`  
  `qgis_plugin/suomenvaylat_qgis/services.py:174–178, 230–243, 285–298`  
  `qgis_plugin/suomenvaylat_qgis/service_styles.py:81–87`
- **Ongelman kuvaus ja juurisyy:**  
  Kun OGC WFS- tai WMS-palvelin palauttaa virheen XML-muodossa (`<ows:ExceptionReport>` tai `<ServiceExceptionReport>`), kumpikaan työkalu ei pura todellista virheviestiä asianmukaisesti:
  - ArcGIS Pro ottaa raa'asta XML-merkkijonosta ensimmäiset 200 merkkiä (`[:max_len]`), jolloin käyttäjälle näytetään teknistä XML-alkutunnistetta (`<?xml version="1.0"... <ows:ExceptionReport`) ja todellinen `ExceptionText` leikkautuu pois.
  - QGIS heittää geneerisen poikkeuksen `RuntimeError(f"Palvelutasoa ei voitu avata: {entry['title']}")`, jolloin palvelimen ilmoittama virhe häviää kokonaan.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Palvelimen antama todellinen virhesyy (esim. *"Invalid SRS EPSG:3067"*, *"Query limit exceeded"*, *"Layer does not exist"*) häviää. Käyttäjälle ei selviä, miksi haku epäonnistui, mikä tekee vianmäärityksestä mahdotonta.
- **Konkreettinen korjaus:**  
  Toteutetaan keskitetty OGC XML -poikkeusten jäsennys:

```python
# Yhteinen korjausfunktio (services.py ja VaylaWFSDownloader.pyt):
import xml.etree.ElementTree as ET

def parse_ogc_exception(raw_bytes_or_str, max_len=300):
    if not raw_bytes_or_str:
        return ""
    if isinstance(raw_bytes_or_str, str):
        raw_bytes = raw_bytes_or_str.encode("utf-8", errors="replace")
    else:
        raw_bytes = raw_bytes_or_str
    if not raw_bytes.lstrip().startswith(b"<"):
        return raw_bytes_or_str[:max_len]
    try:
        root = ET.fromstring(raw_bytes)
        messages = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag in ("ExceptionText", "ServiceException"):
                text = (elem.text or "").strip()
                code = elem.get("exceptionCode") or elem.get("code") or ""
                if text:
                    messages.append(f"[{code}] {text}" if code else text)
        if messages:
            return "OGC-virhe: " + "; ".join(messages)[:max_len]
    except Exception:
        pass
    return raw_bytes_or_str[:max_len]
```

---

#### F-HIGH-07: Nelinkertainen GeoJSON/WKT-serialisointi ja väliaikaistiedostojen lukusilmukka OGC API -käsittelyssä
- **Osa-alue:** Suorituskyky / UI
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:1058–1075, 1103–1115`
- **Ongelman kuvaus ja juurisyy:**  
  `_download_ogc`-funktiossa sivutetut kohteet ladataan ja käsitellään seuraavasti:
  1. `raw.get("geometry")` (Python dict) serialisoidaan JSON-merkkijonoksi: `json.dumps(geom_data)`
  2. Merkkijono jäsennetään C++-geometriaksi: `QgsJsonUtils.geometryFromGeoJson(...)`
  3. Geometria muunnetaan WKT-tekstiksi: `geometry.asWkt()`
  4. WKT ja attribuutit serialisoidaan JSON-riviksi levylle: `spool.write(json.dumps(...))`
  5. Kirjoitusvaiheessa levykettä luetaan rivi riviltä ja jäsennetään uudelleen: `json.loads(line)`
  6. WKT jäsennetään uudelleen geometriaksi: `QgsGeometry.fromWkt(record["wkt"])`  
  Lisäksi rivien 1104–1115 kirjoitussilmukassa **ei kutsuta `progress()`-funktiota lainkaan**, eikä tehtävän peruutusta (`task.isCanceled()`) voida havaita.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Jokainen geometria serialisoidaan ja deserialisoidaan 4 kertaa. WKT-tekstimuunnos heikentää liukulukutarkkuutta verrattuna WKB-binaariin. 50 000 kohteen latauksessa tämä aiheuttaa satoja tuhansia turhia merkkijonomuunnoksia ja CPU-kuormaa. Levylle kirjoituksen aikana (joka voi kestää minuutteja) käyttöliittymä ei reagoi "Keskeytä"-painikkeeseen, koska peruutuslippua ei tarkisteta kirjoitussilmukassa.
- **Konkreettinen korjaus:**  
  Käytetään suoraa WKB-binaaria WKT:n sijaan ja lisätään peruutustarkistus kirjoitussilmukkaan:

```python
# Korjaus tiedostoon services.py (_download_ogc):
# 1. Tallennetaan WKB-heksana WKT-tekstin sijaan (säästää muistia ja nopeuttaa jäsennystä):
wkb_hex = geometry.asWkb().hex()
spool.write(json.dumps({"wkb": wkb_hex, "props": properties}, ensure_ascii=False) + "\n")

# 2. Lisätään edistymis- ja peruutustuki kirjoitusvaiheeseen:
with spool_path.open("r", encoding="utf-8") as spool:
    for line_num, line in enumerate(spool, start=1):
        if progress and line_num % 200 == 0:
            progress(count)  # Tarkistaa myös DownloadCanceled
        record = json.loads(line)
        output = QgsFeature(fields)
        output.setAttributes([
            _ogc_value(record["props"].get(f.name()), variants[f.name()])
            for f in fields
        ])
        output.setGeometry(QgsGeometry.fromWkb(bytes.fromhex(record["wkb"])))
        if not writer.addFeature(output):
            raise RuntimeError(writer.errorMessage())
```

---

#### F-HIGH-08: `_clip_with_repair` korjaa vain kohdegeometrian mutta jättää leikkaavan aluerajauksen korjaamatta
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:6903–6938`
- **Ongelman kuvaus ja juurisyy:**  
  Metodissa `_clip_with_repair`:
  ```python
  def _clip_with_repair(self, in_fc, boundary_fc, out_fc, metrics, layer_clean):
      try:
          arcpy.analysis.Clip(in_fc, boundary_fc, out_fc)
          return
      except Exception as ex:
          clip_error = ex
      ...
      try:
          arcpy.management.RepairGeometry(in_fc, "DELETE_NULL")
          arcpy.analysis.Clip(in_fc, boundary_fc, out_fc)
          return
      except Exception:
          pass
  ```
  `Clip` kaatuu usein topologiavirheeseen (ERROR 160196 / ERROR 000148) sen vuoksi, että **rajausaineistossa** (`boundary_fc`, esim. käyttäjän oma shapefile tai monimutkainen kuntaraja) on itsensä leikkaavia renkaita tai duplikaattipisteitä. Koodi ajaa `RepairGeometry`-työkalun vain syöteaineistolle `in_fc`, mutta ei koskaan rajausaineistolle `boundary_fc`.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Rajaus epäonnistuu toistuvasti viallisen aluerajauksen takia, ja koko tason lataus hylätään virheellä, vaikka aluerajaus olisi ollut korjattavissa yhdellä `RepairGeometry`-kutsulla.
- **Konkreettinen korjaus:**  
  Ajetaan geometrian korjaus tarvittaessa myös rajausaineistolle `boundary_fc`:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt (_clip_with_repair):
try:
    repair_start = time.perf_counter()
    arcpy.management.RepairGeometry(in_fc, "DELETE_NULL")
    arcpy.management.RepairGeometry(boundary_fc, "DELETE_NULL")
    metrics.add("geometrian korjaus", time.perf_counter() - repair_start)
    arcpy.analysis.Clip(in_fc, boundary_fc, out_fc)
    self._msg("  [INFO] Clip onnistui geometrioiden korjauksen jälkeen.")
    return
except Exception:
    self._safe_delete(out_fc)
```

---

#### F-HIGH-09: Yksittäinen kirjoitusvirhe `writer.addFeature()` hylkää ja poistaa koko ladatun GeoPackagen
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:571–573, 1113–1115`  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:588–590`
- **Ongelman kuvaus ja juurisyy:**  
  Kun kohteita kirjoitetaan GeoPackageen:
  ```python
  if not writer.addFeature(output):
      raise RuntimeError(writer.errorMessage())
  ```
  Jos yksittäinen kohde (esim. kohde numero 42 150) hylätään skeemarikkoutuman, erikoismerkin tai epävalidin geometrian vuoksi, heitetään välittömästi `RuntimeError`. Funktiossa `work(task)` (plugin.py:588) tämä siirtyy `except Exception`-haaraan, joka poistaa koko GeoPackage-tiedoston levyltä (`path.unlink(missing_ok=True)`) ja raportoi koko tason epäonnistuneeksi.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Koko 50 000 kohteen aineisto menetetään ja poistetaan levyltä yhden ainoan virheellisen kohteen vuoksi.
- **Konkreettinen korjaus:**  
  Kirjataan varoitus QGISin viestilokiin ja hypätään virheellisen kohteen yli:

```python
# Korjaus tiedostoon services.py:
from qgis.core import QgsMessageLog, Qgis

skipped_count = 0
for feature in layer.getFeatures(request):
    ...
    if not writer.addFeature(output):
        skipped_count += 1
        if skipped_count <= 5:
            QgsMessageLog.logMessage(
                f"Ohitettiin kohde kirjoitusvirheen vuoksi: {writer.errorMessage()}",
                "Suomenväylät", Qgis.Warning
            )
        continue

if count == 0 and skipped_count > 0:
    raise RuntimeError(f"Kaikki {skipped_count} kohdetta hylättiin kirjoitusvaiheessa: {writer.errorMessage()}")
```

---

#### F-HIGH-10: MML-vektoritiilien oletustyylin tarkistus (`loadDefaultStyle`) kaataa tason latauksen jos `.qml` puuttuu
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:674–676`
- **Ongelman kuvaus ja juurisyy:**  
  Funktiossa `_add_mml_background` lisätään MML:n vektoritiilitaso:
  ```python
  uri.setParam("styleUrl", mml_tile_style(map_name))
  layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode("utf-8"), f"MML — {map_name}")
  if not layer.isValid():
      raise RuntimeError("QGIS ei voinut avata MML-vektoritiilitasoa")
  message, ok = layer.loadDefaultStyle()
  if not ok:
      raise RuntimeError("MML:n vektoritiilien symboliikka ei avaudu")
  ```
  `QgsVectorTileLayer` renderöi Mapbox GL -tyylin automaattisesti URI:ssa määritellystä `styleUrl`-osoitteesta. Metodi `layer.loadDefaultStyle()` etsii käyttäjän profiilista paikallista oletustyyliä (`.qml`). Jos tällaista paikallista tiedostoa ei ole (kuten käytännössä aina uusilla asennuksilla), `loadDefaultStyle()` palauttaa `ok = False`.
- **Vaikutus käyttäjään ja järjestelmään:**  
  MML:n vektoritiilitaustakarttojen (Taustakartta, Maastokartta, Kiinteistöjaotus) lisääminen epäonnistuu välittömästi virheellä *"MML:n vektoritiilien symboliikka ei avaudu"*, vaikka itse taso ja sen Mapbox GL -tyyli olisivat täysin kunnossa ja toimivia.
- **Konkreettinen korjaus:**  
  Poistetaan tarpeeton `loadDefaultStyle()` -kutsu:

```python
# Korjaus tiedostoon plugin.py (_add_mml_background):
layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode("utf-8"), f"MML — {map_name}")
if not layer.isValid():
    raise RuntimeError("QGIS ei voinut avata MML-vektoritiilitasoa")

# Poistetaan loadDefaultStyle-kutsu; QgsVectorTileLayer renderöi suoraan styleUrl-osoitteesta:
_add_project_layer(layer)
QMessageBox.information(self, "Suomenväylät", f"Lisättiin: MML — {map_name}")
```

---

#### F-HIGH-11: `qgisMinimumVersion=3.44` estää lisäosan asennuksen kaikissa tuotanto-QGIS-versioissa (3.34 LTR, 3.40 LTR)
- **Osa-alue:** Yhteensopivuus / Käytettävyys
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/metadata.txt:6`
- **Ongelman kuvaus ja juurisyy:**  
  Tiedostossa `metadata.txt` on asetettu:
  ```ini
  qgisMinimumVersion=3.44
  ```
  QGIS 3.44 on vasta tulevaisuudessa julkaistava versio (nykyiset viralliset tuotantoversiot ovat 3.34 LTR ja 3.40 LTR). Vaatimus asetettiin, koska `QgsJsonUtils.geometryFromGeoJson` tuli mukaan QGIS 3.36:ssa.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Yksikään käyttäjä, jolla on virallinen QGIS 3.34 LTR, 3.36, 3.38 tai 3.40, ei voi asentaa lisäosaa QGISin lisäosien hallinnasta. QGIS ilmoittaa, että lisäosa ei ole yhteensopiva.
- **Konkreettinen korjaus:**  
  Asetetaan `qgisMinimumVersion=3.34` ja lisätään taaksepäin yhteensopiva OGR-toteutus `geometryFromGeoJson`-kutsulle:

```ini
# Korjaus tiedostoon metadata.txt:
qgisMinimumVersion=3.34
```

```python
# Korjaus tiedostoon services.py:
def _geometry_from_geojson(geom_data):
    """Yhteensopiva geometrian luonti QGIS 3.34+ -versioille."""
    if hasattr(QgsJsonUtils, "geometryFromGeoJson"):
        return QgsJsonUtils.geometryFromGeoJson(json.dumps(geom_data))
    from osgeo import ogr
    ogr_geom = ogr.CreateGeometryFromJson(json.dumps(geom_data))
    return QgsGeometry.fromWkt(ogr_geom.ExportToIsoWkt()) if ogr_geom else QgsGeometry()
```

---

#### F-HIGH-12: Väliaikaiset `suomenvaylat_*`-hakemistot ja FileGDB-kannat jäävät pysyvästi levylle osittaisissa virheissä
- **Osa-alue:** Virheensieto / Resurssienhallinta
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:1480–1499, 6805–6807`
- **Ongelman kuvaus ja juurisyy:**  
  Metodissa `execute`:
  ```python
  cleanup_elapsed, cleanup_error = self._cleanup_run_scratch(
      preserve=(not success or self._run_had_layer_failures)
  )
  ```
  Jos monitasoisessa ajossa yksikin valinnainen taso epäonnistuu (`self._run_had_layer_failures = True`), koko ajokohtainen scratch-hakemisto (`%TEMP%\suomenvaylat_<guid>`) **jätetään pysyvästi levylle**. Koodissa ei ole mitään vanhojen scratch-kansioiden TTL-vanhennusta tai siivousrutiinia. Lisäksi Windowsissa `shutil.rmtree(folder)` kaatuu `PermissionError`-virheeseen, jos jokin ArcPy-lukko on auki.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Käyttäjän kiintolevytila (%TEMP%) täyttyy gigatavuilla hylättyjä FileGDB-kantoja ja JSON-väliaikaistiedostoja.
- **Konkreettinen korjaus:**  
  Siivotaan scratch aina onnistuneiden aineistojen osalta ja toteutetaan vanhojen `suomenvaylat_*`-hakemistojen automaattinen siivous työkalun käynnistyksessä:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt:
def _cleanup_old_scratch_folders(self, max_age_hours=24):
    temp_dir = tempfile.gettempdir()
    cutoff = time.time() - (max_age_hours * 3600)
    for entry in os.scandir(temp_dir):
        if entry.is_dir() and entry.name.startswith("suomenvaylat_"):
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry.path, ignore_errors=True)
            except Exception:
                pass
```

---

#### F-HIGH-13: ArcGIS Pron 8 546-rivinen monoliitti: heikko ylläpidettävyys ja testattavuus ilman modulirakennetta
- **Osa-alue:** Arkkitehtuuri
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:1–8546`
- **Ongelman kuvaus ja juurisyy:**  
  ArcGIS Pron koko toiminnallisuus (HTTP-transport, WFS-, WMS-, WMTS-, OGC API- ja OSM-logiikka, geometriankäsittely, Geoprocessing UI, parametrien validointi) on ahdettu yhteen ainoaan 8 546-riviseen tiedostoon.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Erittäin korkea ylläpitoriski ja suuri todennäköisyys regressioille koodia muokattaessa. Koodin testaus ilman täyttä ArcGIS Pro -ympäristöä vaatii keinotekoisia vale-arcpy-lataajia.
- **Konkreettinen korjaus:**  
  Pilkotaan `.pyt`-tiedosto siten, että se toimii ohuena käyttöliittymäkuorena ja liiketoimintalogiikka eristetään alikansioon `Toolboxes/vayla_wfs/`:
  - `vayla_wfs/registry.py` (rajapintamäärittelyt)
  - `vayla_wfs/http.py` (HTTP-asiakas, retryt, gzip)
  - `vayla_wfs/geometry.py` (spatiaalilaskenta, WKT, BBOX)
  - `vayla_wfs/downloader.py` (latausputki ja GP-logiikka)

---

#### F-HIGH-14: Testauksen epäsymmetria: 176 automatisoitua testiä testaavat vain ArcGIS Prota, 0 QGIS-lisäosaa
- **Osa-alue:** Testaus / Laadunvarmistus
- **Vaikutusalue:** Molemmat (Testausinfrastruktuuri)
- **Tiedostopolut ja rivinumerot:**  
  `tests/` vs `qgis_plugin/suomenvaylat_qgis/`
- **Ongelman kuvaus ja juurisyy:**  
  Repositorion 176 automatisoidusta pytest-testistä yksikään ei testaa QGIS-lisäosan ydintä (`services.py` tai `plugin.py`). QGIS-koodille on ainoastaan erillisiä smoke-skriptejä, jotka vaativat manuaalisen ajon QGIS-työpöytäympäristössä ja elävän verkkoyhteyden.
- **Vaikutus käyttäjään ja järjestelmään:**  
  QGIS-lisäosan logiikkavirheet, rikkoutuneet URL-rakenteet tai datamuunnosbugit pääsevät tuotantoon ilman automaattista kiinnijäämistä CI-putkessa.
- **Konkreettinen korjaus:**  
  Luodaan `tests/test_qgis_services.py`, joka testaa QGIS-katalogien jäsennyksen, URL-generoinnin ja virheenkäsittelyn hyödyntäen jo olemassa olevaa `tests/fake_http.py` -valekirjastoa ilman tarvetta elävälle QGIS-käyttöliittymälle.

---

### 3.3 Keskitason havainnot (Keskitaso / Medium)

#### F-MED-01: `DeleteIdentical` skannaa kaikki attribuuttikentät ja Shape-kentän ilman optimoitua avainta
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:2377–2386`
- **Ongelman kuvaus ja juurisyy:**  
  Metodi `_delete_identical_downloads` vertailee kaikkien kohteiden geometrioita kärkipiste kerrallaan sekä kaikkia attribuuttikenttiä. Suurilla aineistoilla tämä on $O(N \log N)$ -operaatio ja kestää erittäin kauan. Jos aineistossa on uniikki palvelinkohtainen ID (`id`, `gml_id`, `fid`, `tunnus`), duplikaatit voidaan tunnistaa pelkän ID-kentän perusteella murto-osassa ajasta.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Tarpeeton 10–60 sekunnin viive jokaisen suuren tason tallennusvaiheessa.
- **Konkreettinen korjaus:**  
  Tarkistetaan ensin tunnetut ID-kentät; jos yksilöivä kenttä löytyy, vertaillaan vain sitä:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt:
def _delete_identical_downloads(self, feature_class):
    available = {f.name.lower(): f.name for f in arcpy.ListFields(feature_class)}
    for id_candidate in ["gml_id", "id", "fid", "kohde_id", "oid_wfs"]:
        if id_candidate in available:
            arcpy.management.DeleteIdentical(feature_class, [available[id_candidate]])
            return
    fields = ["Shape"]
    for field in arcpy.ListFields(feature_class):
        if field.type not in ("OID", "Geometry", "Blob", "Raster"):
            fields.append(field.name)
    arcpy.management.DeleteIdentical(feature_class, fields)
```

---

#### F-MED-02: Turhat levypohjaiset JSON- ja projektio-väliaikaistiedostot muistissa tehtävien muunnosten sijaan
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:3036–3041, 4440–4516`
- **Ongelman kuvaus ja juurisyy:**  
  `_extent_bbox_4326` luo väliaikaisen feature classin `scratch.gdb`:hen, ajaa raskaan `arcpy.management.Project`-työkalun, lukee `Describe.extent`-arvon ja poistaa feature classin pelkän WGS84-bounding boxin laskemiseksi. Lisäksi `_pages_to_temp_fc` kirjoittaa satoja megatavuja JSON-tekstiä levylle ennen `JSONToFeatures`-kutsua.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Tarpeetonta levy-I/O-kuormitusta ja FileGDB-lukitusongelmia.
- **Konkreettinen korjaus:**  
  Käytetään in-memory `.projectAs()` -metodia ilman levylle kirjoittamista:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt:
def _extent_bbox_4326(self, boundary_fc):
    merged, _ = self._merged_boundary_geometry(boundary_fc)
    if merged is None:
        ext = arcpy.Describe(boundary_fc).extent
        poly = arcpy.Polygon(arcpy.Array([
            arcpy.Point(ext.XMin, ext.YMin), arcpy.Point(ext.XMax, ext.YMin),
            arcpy.Point(ext.XMax, ext.YMax), arcpy.Point(ext.XMin, ext.YMax),
        ]), arcpy.SpatialReference(EPSG_TM35FIN))
        wgs84_ext = poly.projectAs(arcpy.SpatialReference(4326)).extent
    else:
        wgs84_ext = merged.projectAs(arcpy.SpatialReference(4326)).extent
    return "{:.8f},{:.8f},{:.8f},{:.8f}".format(
        wgs84_ext.YMin, wgs84_ext.XMin, wgs84_ext.YMax, wgs84_ext.XMax
    )
```

---

#### F-MED-03: Geometrian leikkaustarkistuksista puuttuu `QgsGeometryEngine` / Prepared Geometry
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:559–563, 859–864, 1062–1066`
- **Ongelman kuvaus ja juurisyy:**  
  Kaikissa vektorilatauksissa suoritetaan silmukassa leikkaustarkistus `if not geometry.intersects(source_mask): continue`. Kun rajausmaskissa on kymmeniä tuhansia pisteitä, GEOS joutuu rakentamaan monikulmion segmenttipuun uudelleen jokaista testattavaa kohdetta kohden.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Monimutkaisilla aluerajauksilla leikkaustarkistus hidastaa käsittelyä huomattavasti (jopa 10–30-kertainen suoritusaika verrattuna esivalmisteltuun geometriaan).
- **Konkreettinen korjaus:**  
  Valmistellaan geometria `QgsGeometryEngine`:llä ennen silmukkaa:

```python
# Korjaus tiedostoon services.py:
engine = QgsGeometry.createGeometryEngine(source_mask.constGet())
engine.prepareGeometry()

for feature in layer.getFeatures(request):
    geom = feature.geometry()
    if not engine.intersects(geom.constGet()):
        continue
```

---

#### F-MED-04: Koko OSM-alueen kaikkien tasojen kerääminen keskusmuistiin ennen tiedostokirjoitusta
- **Osa-alue:** Suorituskyky / Muistinhallinta
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:840–880`
- **Ongelman kuvaus ja juurisyy:**  
  `_download_osm`-funktiossa kaikki noudetut kohteet lisätään keskusmuistissa olevaan sanakirjaan `groups = {0: [], 1: [], 2: []}`. Vasta kaikkien ruutujen suorituksen jälkeen kohteet kirjoitetaan levylle.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Laajalta alueelta (esim. koko maakunta) ladattaessa muistiin kertyy satoja tuhansia kohteita, mikä voi viedä gigatavuja RAM-muistia ja aiheuttaa `MemoryError`-poikkeuksen.
- **Konkreettinen korjaus:**  
  Kirjoitetaan kohteet GeoPackageen suoraan jokaisen ruudun jälkeen tai puskuroituna 1 000 kohteen erissä avoimeen writeriin.

---

#### F-MED-05: Pääsäiettä lukittava 36 MB aluerajaustiedoston skannaus ilman välimuistia (`area_choices`)
- **Osa-alue:** Suorituskyky / UI
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:484–490`  
  `qgis_plugin/suomenvaylat_qgis/services.py:338–346`
- **Ongelman kuvaus ja juurisyy:**  
  Aina kun käyttäjä vaihtaa aluetyyppiä (Kunta, Maakunta, ELY), pääsäikeessä avataan 36 Mt:n `hallinnolliset_aluejaot.gpkg` ja iteroidaan sen kaikki kohteet ilman välimuistia.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Käyttöliittymä jäätyy havaittavasti (0,5–1,5 sekunniksi) jokaisella pudotusvalikon valinnalla.
- **Konkreettinen korjaus:**  
  Lisätään `@lru_cache`-välimuisti `area_choices`-funktioon:

```python
# Korjaus tiedostoon services.py:
from functools import lru_cache

@lru_cache(maxsize=16)
def area_choices(area_type):
    name = ADMIN_LAYERS.get(area_type)
    if name is None:
        return []
    layer = QgsVectorLayer(f"{admin_path()}|layername={name}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError("Hallinnollisten alueiden GeoPackage puuttuu tai on virheellinen")
    return sorted({str(f["namefin"]) for f in layer.getFeatures() if f["namefin"]})
```

---

#### F-MED-06: ShapefileFieldConverter tekee erillisen `UpdateCursor`-kierroksen jokaiselle kentälle ja altistaa 10 merkin nimikollisioille
- **Osa-alue:** Suorituskyky / Virheensieto
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:1078–1108`
- **Ongelman kuvaus ja juurisyy:**  
  `ShapefileFieldConverter` käy aineiston läpi erillisellä `UpdateCursor`-kierroksella jokaiselle muunnettavalle kentälle. Lisäksi jos kaksi kenttää alkaa samoilla 10 merkillä (esim. `rakennustunnus` ja `rakennustyyppi`), `AlterField` kaatuu kollisiovirheeseen.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Moninkertainen taulukoskannaus hidastaa tallennusta, ja nimikolliisiot kaatavat shapefile-viennin.
- **Konkreettinen korjaus:**  
  Muunnetaan kaikki kentät yhdellä `UpdateCursor`-kierroksella ja varmistetaan uniikit DBF-kenttänimet leikkauksessa.

---

#### F-MED-07: API-tunnukset tallennetaan add-inin asennuskansioon, josta ne pyyhkiytyvät päivityksessä
- **Osa-alue:** Arkkitehtuuri / Tietoturva
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:3600–3625`
- **Ongelman kuvaus ja juurisyy:**  
  `service_credentials.json` tallennetaan `.pyt`-tiedoston viereen add-inin asennuskansioon (`AssemblyCache/{GUID}/`). Kun add-in päivitetään, vanha asennuskansio tuhoutuu ja käyttäjän API-avaimet katoavat. Lisäksi jaetuissa asennuksissa käyttäjällä ei ole kirjoitusoikeutta asennuskansioon.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Käyttäjätunnukset ja API-avaimet katoavat jokaisessa versiopäivityksessä.
- **Konkreettinen korjaus:**  
  Tallennetaan tunnistetiedosto käyttäjän `%APPDATA%\Suomenvaylat`-hakemistoon:

```python
# Korjaus tiedostoon Toolboxes/VaylaWFSDownloader.pyt:
def _credentials_file_path(self):
    appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or ""
    if appdata:
        target_dir = os.path.join(appdata, "Suomenvaylat")
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, "service_credentials.json")
    return os.path.join(tempfile.gettempdir(), "suomenvaylat_service_credentials.json")
```

---

#### F-MED-08: Merkittävä koodiduplikaatio: `osm_geometry.py`, uudelleenohjauskäsittelijät ja POI-määrittelyt
- **Osa-alue:** Arkkitehtuuri
- **Vaikutusalue:** Molemmat (ArcGIS Pro & QGIS)
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:252–298, 540–710, 800–965`  
  `qgis_plugin/suomenvaylat_qgis/osm_geometry.py:1–179`  
  `qgis_plugin/suomenvaylat_qgis/services.py:75–105`  
  `qgis_plugin/suomenvaylat_qgis/osm_poi_classes.json:1–550`
- **Ongelman kuvaus ja juurisyy:**  
  179 riviä OSM-geometriakoodia, HTTP-uudelleenohjaussuojaukset (`_SafeRedirectHandler`) ja laajat POI-määrittelyt on kopioitu identtisinä koodilohkoina tiedostosta toiseen sen sijaan, että ne ladattaisiin yhteisestä moduulista.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Tuplattu ylläpitotaakka ja riski toimintalogiikan erkaantumisesta bugikorjauksissa.
- **Konkreettinen korjaus:**  
  Pakataan `osm_geometry.py` ja POI-määrittelyt add-in-pakettiin `package-addin.ps1`-skriptissä samalla tavalla kuin `service_styles.py` ja ladataan ne yhteisestä moduulista.

---

#### F-MED-09: `Config.daml` ja C#-käynnistin eivät tarjoa ribbon-painiketta `MMLBasemapDownloader`-työkalulle
- **Osa-alue:** Arkkitehtuuri / GUI
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Config.daml:29–53`  
  `OpenSuomenvaylatToolButton.cs:37`
- **Ongelman kuvaus ja juurisyy:**  
  `Config.daml` määrittää ainoastaan yhden painikkeen `VaylaWFSDownloader`-työkalulle. Työkalupakissa olevalle toiselle täysimittaiselle työkalulle `MMLBasemapDownloader` ei ole lainkaan painiketta ArcGIS Pron ribbonissa.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Loppukäyttäjä ei löydä taustakarttatyökalua ribbonista, ja se jää piiloon ilman manuaalista Catalog-hakua.
- **Konkreettinen korjaus:**  
  Lisätään `Config.daml`-tiedostoon painike `suomenvaylat_BasemapButton` ja luokka `OpenBasemapToolButton.cs`.

---

#### F-MED-10: BBOX-puskurointiero: ArcGIS Pro käyttää 1 000 m puskuria, QGIS 0 m puskuria
- **Osa-alue:** Arkkitehtuuri / Pariteetti
- **Vaikutusalue:** Molemmat (ArcGIS Pro & QGIS)
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:7132–7133`  
  `qgis_plugin/suomenvaylat_qgis/services.py:453–455, 540`
- **Ongelman kuvaus ja juurisyy:**  
  ArcGIS Pro lisää 1 000 metrin puskurin BBOX-kyselyyn ennen palvelinpyyntöä varmistaakseen reunakohteiden saapumisen, kun taas QGIS käyttää tiukkaa 0 metrin rajauslaatikkoa.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Kohteet, joiden geometria sivuaa tai leikkaa aluerajaa osittain, voivat joissakin WFS-palvelimissa jäädä pois QGISissä, kun taas ArcGIS Pro lataa ne aina.
- **Konkreettinen korjaus:**  
  Yhdenmukaistetaan laajuuden puskurointilogiikka käyttämään pientä varmistuspuskuria (esim. 500 m) myös QGISin BBOX-kyselyssä.

---

#### F-MED-11: MML Karttakuva -protokollaero: WMTS (ArcGIS Pro) vs WMS (QGIS)
- **Osa-alue:** Arkkitehtuuri / Suorituskyky
- **Vaikutusalue:** Molemmat (ArcGIS Pro & QGIS)
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:436, 7470–7483`  
  `qgis_plugin/suomenvaylat_qgis/services.py:134, 168–189`  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:682–698`
- **Ongelman kuvaus ja juurisyy:**  
  MML:n maastokartalle ArcGIS Pro käyttää WMTS-tiilirajapintaa, kun taas QGIS käyttää dynaamista WMS-palvelua.
- **Vaikutus käyttäjään ja järjestelmään:**  
  WMS laskee ja piirtää karttakuvat dynaamisesti uudelleen joka kerta, mikä tekee kartan vierityksestä QGISissä raskaampaa kuin WMTS-tiiliä käytettäessä.
- **Konkreettinen korjaus:**  
  Päivitetään QGIS-lisäosa käyttämään ensisijaisesti Karttakuvan WMTS-rajapintaa QGISin natiivilla WMTS-tietolähteellä.

---

#### F-MED-12: Windowsin tiedostolukitukset ja `TemporaryDirectory`-siivouksen poikkeukset
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:577–580, 942, 1049, 1117–1120`
- **Ongelman kuvaus ja juurisyy:**  
  Windowsissa avoinna olevia tiedostoja ei voi poistaa. Pythonin `del writer` ei pakota GDAL/OGR-tiedostokahvaa sulkeutumaan välittömästi ennen roskienkeruuta. `Path(destination).unlink(missing_ok=True)` vaimentaa vain `FileNotFoundError`-virheen, mutta ei `PermissionError [WinError 32]` -lukitusvirhettä.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Epäonnistuneen latauksen siivous kaatuu `PermissionError`-poikkeukseen, mikä estää asianmukaisen virheraportoinnin ja jättää väliaikaistiedostoja levylle.
- **Konkreettinen korjaus:**  
  Pakotetaan roskienkeruu `gc.collect()` ennen poistoa ja käytetään `TemporaryDirectory(ignore_cleanup_errors=True)` -parametria.

---

#### F-MED-13: Suojaamaton `xml.etree.ElementTree.fromstring` altistaa XML entity / DoS -riskeille
- **Osa-alue:** Tietoturva / Virheensieto
- **Vaikutusalue:** Molemmat (ArcGIS Pro & QGIS)
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:176, 196, 231, 250, 288`  
  `Toolboxes/VaylaWFSDownloader.pyt:4157, 5563`
- **Ongelman kuvaus ja juurisyy:**  
  GetCapabilities- ja DescribeFeatureType -vastaukset luetaan suoraan `ET.fromstring(response.read())` -funktiolla ilman kokorajoitusta tai DTD/ENTITY-tarkistusta.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Altis XML Entity Expansion (Billion Laughs / XXE) -kuormitukselle, jos ulkoinen WFS-palvelin tai välimiespalvelin palauttaa haitallista XML-dataa.
- **Konkreettinen korjaus:**  
  Hyödynnetään `service_styles.py`:ssä jo olevaa turvallista XML-tarkistusta: hylätään yli 10 Mt:n vastaukset ja vastaukset, joissa esiintyy `<!DOCTYPE` tai `<!ENTITY`.

---

#### F-MED-14: Kovakoodattu edistymiskaava `min(count, 999)` aiheuttaa edistymispalkin jäätymisen
- **Osa-alue:** UI / Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:569–571`
- **Ongelman kuvaus ja juurisyy:**  
  Edistymisprosentti lasketaan kaavalla `task.setProgress(100.0 * (index + min(count, 999) / 1000.0) / len(plan))`, joka olettaa mielivaltaisesti tason maksimikohdemääräksi 1 000 kohdetta.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Kun aineistossa on yli 1 000 kohdetta, `min(count, 999)` saavuttaa arvon 999 heti latauksen alussa. Tämän jälkeen edistymispalkki jäätyy täysin paikalleen loppuajaksi, jolloin käyttäjä luulee sovelluksen jumiutuneen.
- **Konkreettinen korjaus:**  
  Päivitetään tilatekstiin todellinen kohdemäärä ja edetään loogisissa vaiheissa:

```python
# Korjaus tiedostoon plugin.py:
def update_count(count, index=index, entry=entry):
    if task.isCanceled():
        raise DownloadCanceled()
    task.status_text = f"{entry['title']} — {count:,} kohdetta ladattu".replace(",", " ")
    task.setProgress(100.0 * (index + 0.5) / len(plan))
```

---

### 3.4 Matalan vakavuusluokan havainnot (Matala / Low)

#### F-LOW-01: Kapsi Ortokuva WMS pyytää raskasta PNG-kuvaa JPEG-formaatin sijaan
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:646–648`
- **Ongelman kuvaus ja juurisyy:**  
  Kaikille Kapsin WMS-tasoille käytetään kovakoodattua formaattia `format=image/png`. Ortokuva on valokuvamaista ilmakuvadataa, jolle PNG-pakkaus tuottaa 5–10 kertaa suuremman tiedostokoon kuin häviöllinen JPEG.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Ortokuvatiilien lataus on hidasta ja kuluttaa turhaan kaistaa.
- **Konkreettinen korjaus:**  
  Käytetään ortokuvalle formaattia `image/jpeg`:
  ```python
  fmt = "image/jpeg" if "ortokuva" in layer_name.lower() else "image/png"
  uri = f"crs=EPSG:3067&dpiMode=7&format={fmt}&layers={layer_name}&styles=&url={url}"
  ```

---

#### F-LOW-02: GDAL WMTS XML -kahvojen sulkematta jättäminen (`gdal.Open` ilman `ds = None`)
- **Osa-alue:** Arkkitehtuuri / Resurssit
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:651–662`
- **Ongelman kuvaus ja juurisyy:**  
  `_download_oskari_wmts`-funktiossa avataan GDAL-datasetit (`container = gdal.Open(...)`, `source = gdal.Open(...)`), mutta niitä ei koskaan suljeta asettamalla `container = None; source = None`.
- **Vaikutus käyttäjään ja järjestelmään:**  
  GDAL-datasetit jäävät auki prosessiin ja vuotavat verkkoyhteyksiä ja tiedostokahvoja.
- **Konkreettinen korjaus:**  
  Suljetaan GDAL-datasetit `try...finally`-lohkossa.

---

#### F-LOW-03: Oletuksena venäläinen `maps.mail.ru` Overpass API -palvelinlistan kärjessä
- **Osa-alue:** Luotettavuus / Tietoturva
- **Vaikutusalue:** ArcGIS Pro
- **Tiedostopolut ja rivinumerot:**  
  `Toolboxes/VaylaWFSDownloader.pyt:428–431`
- **Ongelman kuvaus ja juurisyy:**  
  `WFSSourceRegistry`-luokassa ensisijaiseksi Overpass API -päätepisteeksi on asetettu `https://maps.mail.ru/osm/tools/overpass/api/interpreter`. Useat suomalaiset valtionhallinnon ja kuntasektorin verkot estävät `.ru`-päätteisen liikenteen palomuuritasolla.
- **Vaikutus käyttäjään ja järjestelmään:**  
  OSM-latauksissa ensimmäinen pyyntö aiheuttaa palomuurin eston ja tarpeettoman 30–60 sekunnin aikakatkaisun ennen toipumista varapalvelimelle.
- **Konkreettinen korjaus:**  
  Poistetaan `maps.mail.ru` ja käytetään luotettavia eurooppalaisia julkisia Overpass-instansseja (`overpass-api.de`, `lz4.overpass-api.de`).

---

#### F-LOW-04: Hakukentän debouncen puute ja widget-muisti-intensiivisyys suodatuksessa
- **Osa-alue:** UI / Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:184, 397–415`
- **Ongelman kuvaus ja juurisyy:**  
  `self.search.textChanged` laukeaa jokaisella näppäinpainalluksella, tyhjentää koko `QListWidget`:in ja luo sadat tai tuhannet `QListWidgetItem`-oliot uudelleen.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Kirjoittaminen hakukenttään tökkii suurilla tasomäärillä.
- **Konkreettinen korjaus:**  
  Käytetään `QTimer`-pohjaista 200 ms debounce-viivettä ja suodatetaan kohteet piilottamalla (`item.setHidden()`) uudelleenluonnin sijaan.

---

#### F-LOW-05: `QgsGeometry.transform`-kutsun palautuskoodia ei tarkisteta
- **Osa-alue:** Virheensieto
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/services.py:561, 861, 1064`
- **Ongelman kuvaus ja juurisyy:**  
  PyQGISissä `geometry.transform()` palauttaa virhekoodin (0 = Onnistui). Jos muunnos epäonnistuu, paluuarvo jätetään tarkistamatta ja jatketaan vioittuneella geometrialla.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Vialliset koordinaatit voivat kaataa GEOS-leikkauksen tai pudota hiljaa pois aineistosta.
- **Konkreettinen korjaus:**  
  Tarkistetaan palautuskoodi: `if geometry.transform(to_target) != 0 or geometry.isEmpty(): continue`.

---

#### F-LOW-06: Peräkkäinen GetCapabilities-tarkistus useille tasoille rinnakkaisten sijaan
- **Osa-alue:** Suorituskyky
- **Vaikutusalue:** QGIS-lisäosa
- **Tiedostopolut ja rivinumerot:**  
  `qgis_plugin/suomenvaylat_qgis/plugin.py:357–370`  
  `qgis_plugin/suomenvaylat_qgis/services.py:170–190`
- **Ongelman kuvaus ja juurisyy:**  
  Tasoluettelon haku ajaa GetCapabilities-pyynnöt peräkkäisessä silmukassa. Karttapaikalla on yksinään 7 eri WFS-osoitetta.
- **Vaikutus käyttäjään ja järjestelmään:**  
  Tasoluettelon avaus kestää 30–60 sekuntia, ja yhden palvelimen hitaus hidastaa kaikkia muita.
- **Konkreettinen korjaus:**  
  Rinnakkaistetaan tasoluettelopyynnöt `ThreadPoolExecutorilla`.

---

## 4. Arkkitehtuuripariteetti ja ristiinalustavertailu (Architectural Parity & Cross-Platform Comparison)

### 4.1 Kattava vertailumatriisi: ArcGIS Pro vs QGIS-lisäosa

| Ominaisuus / Arkkitehtuurialue | ArcGIS Pro (`Toolboxes/VaylaWFSDownloader.pyt` + C#) | QGIS-lisäosa (`qgis_plugin/suomenvaylat_qgis/`) | Arvio & Pariteettitila |
|---|---|---|---|
| **Pääarkkitehtuuri ja käynnistys** | C# Add-In Ribbon -painike avaa Geoprocessing-työkalun | QGIS Action avaa mukautetun PyQt-dialogin | Natiivit alustan mukaiset käynnistystavat. |
| **Koodirakenne & modulaarisuus** | 8 546 rivin yksittäinen `.pyt`-monoliitti | Jaettu moduuleihin (`plugin.py`, `services.py`, `service_styles.py`, `osm_geometry.py`) | **QGIS parempi:** Selkeä jako vastuualueisiin. |
| **WFS-versiointi** | WFS 2.0.0 oletus; WFS 1.1.0 Sitowise Ainolle | `version: "auto"` QGIS WFS-tarjoajalle | Yhdenmukainen. Aino saa molemmissa 1.1.0:n. |
| **WFS-suodatus (Väylä, DigiRoad jne.)** | Palvelinpään `CQL_FILTER=INTERSECTS(...)` GET/POST; monikulmion yksinkertaistus | **Ei palvelinpään CQL-suodatusta.** Lataa koko BBOXin ja leikkaa asiakkaalla | **Kriittinen suorituskykyero:** QGIS siirtää 3–10x enemmän dataa verkon yli. |
| **Aluerajauksen BBOX-puskurointi** | BBOX lasketaan 1 000 metrin puskurilla (`ext.XMin - 1000...`) | BBOX lasketaan 0 metrin puskurilla (`boundingBox()`) | ArcGIS Pro varmistaa reunakohteiden saapumisen; QGIS rajaa tiukasti. |
| **HTTP-pakkaus (Gzip/Deflate)** | `Accept-Encoding: identity` (pakkaus estetty) | QGIS/Qt ja Python `_urlopen` hyväksyvät pakkauksen oletuksena | **QGIS parempi:** ArcGIS Pro siirtää pakkaamatonta tekstiä. |
| **HTTP-virheensieto & Retry** | Kattava `RETRYABLE_HTTP_STATUS` (500, 502, 503, 504, 429, 408) ja eksponentiaalinen backoff | Yksittäiset `_request_json`-kutsut ilman retry-silmukkaa | **ArcGIS Pro huomattavasti luotettavampi** verkkohäiriöissä. |
| **WFS-sivutuksen eheyden tarkistus** | Tarkistaa `numberMatched`, sivukoon ja hashit; hylkää katkenneet lataukset | Ei tarkistusta `getFeatures()`-silmukan katketessa | **Kriittinen luotettavuusero:** QGIS altis hiljaiselle datan hävikille. |
| **Säiemalli ja UI-responsivisuus** | Geoprocessing worker thread, mutta `canRunInBackground=False` ja progressori puuttuu | `QgsTask` taustasäie, mutta laiton `QgsMapLayer` luonti säikeessä | **Molemmat vaativat korjausta:** ArcGIS Pro lukitsee UI:n, QGIS riskeeraa kaatumisen. |
| **Tallennusformaatti** | File Geodatabase `.gdb` (tai Shapefile) | GeoPackage `.gpkg` (tai GeoTIFF) | Natiivit optimoidut formaatit alustoilleen. |
| **MML Karttakuva -protokolla** | **WMTS** (`WMTSCapabilities.xml` ETRS-TM35FIN tiilet) | **WMS** (`karttakuva.maanmittauslaitos.fi/maasto/wms`) | WMTS esilasketuilla tiilillä on huomattavasti nopeampi kuin WMS. |
| **MML Vektoritiilet** | ETRS-TM35FIN TileJSON | WGS84 Pseudo-Mercator TileJSON Mapbox GL -tyylillä | **QGIS parempi:** Natiivi `QgsVectorTileLayer`-tuki tyyleillä. |
| **SLD-symboliikka** | `service_styles.py` ladataan importlibillä | `service_styles.py` tallentaa `.sld`, `.qml` ja GPKG-tyylin | Korkea pariteetti; jaettu tyylikirjasto toimii hyvin. |
| **Automatisoidut testit** | 176 yksikkötestiä (`tests/`), ajettavissa pytestillä ilman lisenssiä | 0 automatisoitua testiä `tests/`-kansiossa | **Kriittinen testausvaje QGIS-lisäosassa.** |

### 4.2 Koodiduplikaation poisto ja yhteinen ydinmoduuli (`vayla_wfs_core`)
Tällä hetkellä `service_styles.py` on ainoa onnistuneesti jaettu moduuli. Muut riippuvuudettomat toiminnot on kopioitu käsin koodikantojen välillä:
1. `osm_geometry.py`: 179 riviä topologista rengaslogiikkaa on kopioitu identtisenä ArcGIS Pron `.pyt`-tiedostoon.
2. `_SafeRedirectHandler` ja HTTP-alkuperätarkistukset ovat identtisiä molemmissa.
3. OSM POI -luokittelutaulukot on määritelty kahdessa eri paikassa.

**Suositeltava modulaarisointistrategia:**
Eristetään yhteinen alustariippumaton Python-kirjasto `vayla_wfs_core`:
```
suomenvaylat/
├── vayla_wfs_core/               # Puhdas Python, ei arcpy- tai qgis-riippuvuuksia
│   ├── __init__.py
│   ├── http.py                   # HttpTransport, Gzip, retryt, safe redirect
│   ├── registry.py               # WFS-, OGC API-, Oskari- ja Kapsi-lähteet
│   ├── geometry.py               # WKT, BBOX, Douglas-Peucker, osm_geometry
│   ├── styles.py                 # service_styles.py (SLD & QML)
│   └── ogc_xml.py                # OGC ExceptionReport ja GetCapabilities parserit
├── Toolboxes/
│   ├── VaylaWFSDownloader.pyt    # Ohut ArcGIS Pro GP UI -kuori
│   └── vayla_wfs/                # ArcPy-kohtaiset GeoProcessing-työkalut
└── qgis_plugin/
    └── suomenvaylat_qgis/        # PyQGIS-kohtaiset GUI- ja Task-komponentit
```
Paketointiskriptit (`package-addin.ps1` ja QGIS ZIP) pakkaavat `vayla_wfs_core`-kansion automaattisesti kummankin laajennuksen asennuspakettiin.

---

## 5. Testikattavuusauditointi ja laadunvarmistus (Test Coverage Audit & Quality Assurance)

### 5.1 `tests/`-hakemiston nykytila (176 testiä)
Repositorion automatisoitu testipatteristo suoriutuu erinomaisesti perusregressioiden tunnistamisesta:
- `test_toolbox_helpers.py` (81 testiä): Mittaa `.pyt`-tiedoston apufunktioita, URL-rakenteita ja parametrimuunnoksia.
- `test_audit_fixes.py` (39 testiä): Testaa tulosnimien varaukset, WFS-sivutuksen eheyden, geometrian erottelun ja uudelleenohjaussuojaukset.
- `test_service_styles.py` (20 testiä): Testaa SLD- ja XML-tyylien noudon, koon rajoituksen ja välimuistin.
- `test_osm_geometry.py` (10 testiä): Ajaa samat geometriatestit sekä `.pyt`:n että QGISin `osm_geometry.py`:n yli.
- `test_output_names.py` (10 testiä): Testaa tulosnimien siistimisen ja pituusrajat.
- `test_execute_flow.py` (9 testiä): Ajaa työkalun läpimenon vale-arcpyllä.
- `test_cql_post.py` (7 testiä): Testaa CQL-suodattimen siirtymisen POST-metodiin pituusrajan ylittyessä.

### 5.2 Tunnistetut testausvajeet
1. **QGIS-lisäosan puuttuminen CI:stä:** Kuten havainnossa F-HIGH-14 todettiin, `services.py`- ja `plugin.py`-tiedostoille ei ole yhtäkään testiä `tests/`-kansiossa.
2. **Kesken katkenneet JSON/WFS-virrat:** Testipatteristossa ei ole testiä sille, miten järjestelmä toipuu, jos HTTP-virta katkeaa kesken JSON-jäsennyksen (`json.JSONDecodeError`).
3. **HTTP 429 Rate Limiting:** Ei testiä, joka validoisi eksponentiaalisen peruutuksen ja odotuksen Overpass- tai Cloudflare-nopeusrajoituksissa.
4. **3D- ja monimutkaiset geometriat:** Ei testitapausta Z- ja M-koordinaattien pudottamiselle ilman aineiston hylkäämistä.
5. **Erittäin suuret aineistot:** Kaikki testit simuloivat vain 1–5 sivua; monen gigatavun ja kymmenien sivujen muistinkulutusta ei testata.

### 5.3 Suunnitelma automaattiselle QGIS-testaukselle (Headless CI)
Otetaan käyttöön kaksiportainen testaus:
1. **Yksikkötestit ilman QGIS-asennusta (`tests/test_qgis_services.py`):** Testataan `services.py`:n WFS-, OGC API-, Oskari- ja Kapsi-katalogien jäsennys, URL-generointi ja virheenkäsittely hyödyntäen `fake_http.py`-kirjastoa osana tavallista `pytest`-ajoa.
2. **Headless PyQGIS -integraatiotestit (`pytest-qgis`):** Ajetaan GitHub Actions CI:ssä Docker-kontissa (`qgis/qgis:latest`) headless-tilassa (`QgsApplication([], False)`), jolloin testataan oikeat vektorikerrosten luonnit ja GeoPackage-kirjoitukset ilman näyttöä.

---

## 6. Priorisoitu toteutuspolku (Prioritized Implementation Roadmap)

Suositeltu toimenpidejärjestys ja etenemissuunnitelma on jaettu kolmeen vaiheeseen:

```
┌────────────────────────────────────────────────────────────────────────┐
│ VAIHE 1: Välittömät kriittiset korjaukset (P0 - Blocker / Quick Wins) │
│ - QGIS Säieturvallisuus (F-CRIT-01) & WFS-datakatkoksen esto (F-CRIT-02)│
│ - QGIS qgisMinimumVersion=3.34 (F-HIGH-11) & loadDefaultStyle (F-HIGH-10)│
│ - ArcGIS Pro Accept-Encoding: gzip (F-HIGH-02)                         │
│ - OGC XML ExceptionReport -jäsennys (F-HIGH-06)                        │
│ - Overpass maps.mail.ru korvaaminen (F-LOW-03)                        │
│ - ArcGIS Pro API-tunnusten siirto APPDATA-kansioon (F-MED-07)         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ VAIHE 2: Suorituskyky- ja luotettavuusoptimoinnit (P1)                │
│ - QGIS Palvelinpään CQL-suodatus (F-HIGH-01) & GeoJSON-oletus (F-HIGH-03)│
│ - QGIS HTTP Retry/Backoff (F-HIGH-05) & Kirjoitusvirheiden ohitus (09)│
│ - ArcGIS Pro canRunInBackground & arcpy.SetProgressor (F-HIGH-04)     │
│ - QGIS OGC API WKB-serialisointi (F-HIGH-07)                           │
│ - ArcGIS Pro _clip_with_repair rajauskorjaus (F-HIGH-08)              │
│ - ArcGIS Pro scratch-automaattisiivous (F-HIGH-12)                    │
│ - QGIS OSM suoratoisto (F-MED-04) & QgsGeometryEngine (F-MED-03)      │
│ - ArcGIS Pro DeleteIdentical & In-memory BBOX (F-MED-01, F-MED-02)     │
│ - Config.daml MMLBasemapDownloader-nappi (F-MED-09)                   │
│ - BBOX-puskuroinnin ja MML-protokollien yhdenmukaistus (F-MED-10, 11) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ VAIHE 3: Arkkitehtuurin refaktorointi ja koodin yhdistäminen (P2)      │
│ - ArcGIS Pro 8 546-rivisen monoliitin modulaarinen pilkkominen (13)   │
│ - Koodiduplikaation poisto: yhteinen vayla_wfs_core -kirjasto (F-MED-08)│
│ - QGIS-yksikkötestit tests/-hakemistoon ja CI-putkeen (F-HIGH-14)     │
│ - Ääritapaustestien lisäys testipatteristoon                          │
└────────────────────────────────────────────────────────────────────────┘
```

### Vaihe 1: Välittömät kriittiset korjaukset (P0 - Blocker & Quick Wins)
- **Tavoite:** Estää kaatumiset, hiljainen datan häviäminen ja asennusestot välittömillä pistekorjauksilla.
- **Toimenpiteet:**
  1. **F-CRIT-01:** Siirretään `QgsVectorLayer`-instansiointi ja tyylien soveltaminen taustasäikeestä pääsäikeelle `done()`-funktioon. Poistetaan laiton `moveToThread()`.
  2. **F-CRIT-02:** Lisätään WFS-kokonaismäärän validointi `_download_vector_layer`-funktioon katkenneiden latausten hylkäämiseksi.
  3. **F-HIGH-11:** Korjataan `metadata.txt`: `qgisMinimumVersion=3.34` ja lisätään taaksepäin yhteensopiva GeoJSON-geometrian luonti.
  4. **F-HIGH-10:** Poistetaan `loadDefaultStyle()`-kutsu MML-vektoritiilten latauksesta.
  5. **F-HIGH-02:** Sallitaan Gzip- ja Deflate-purku ArcGIS Pron `HttpTransport`-luokassa.
  6. **F-HIGH-06:** Lisätään OGC XML ExceptionReport -virheiden jäsennys molempiin alustoihin.
  7. **F-LOW-03:** Poistetaan `maps.mail.ru` Overpass-palvelinlistalta ja korvataan eurooppalaisilla instansseilla.
  8. **F-MED-07:** Siirretään `service_credentials.json` käyttäjän `%APPDATA%\Suomenvaylat`-kansioon.

### Vaihe 2: Suorituskyky- ja luotettavuusoptimoinnit (P1)
- **Tavoite:** Moninkertaistaa tiedonsiirtonopeus, poistaa käyttöliittymän jäätymiset ja parantaa virheensietokykyä.
- **Toimenpiteet:**
  1. **F-HIGH-01 & F-HIGH-03:** Implementoidaan palvelinpään `cql_filter` ja `outputformat=application/json` QGIS WFS -tasojen avaukseen.
  2. **F-HIGH-05:** Lisätään eksponentiaalinen uudelleenyrityssilmukka QGISin `_request_json`-kutsuihin.
  3. **F-HIGH-09 & F-MED-12:** Lisätään ohitus yksittäisille kirjoitusvirheille GeoPackage-tallennuksessa ja korjataan Windowsin tiedostolukitusten siivous.
  4. **F-HIGH-04:** Sallitaan `canRunInBackground = True` ja otetaan käyttöön `arcpy.SetProgressor` ArcGIS Prossa.
  5. **F-HIGH-07:** Korvataan WKT-serialisointi WKB-binaarilla ja lisätään peruutustuki OGC API -kirjoitukseen.
  6. **F-HIGH-08:** Lisätään `boundary_fc`-rajausgeometrian automaattinen korjaus `_clip_with_repair`-metodiin.
  7. **F-HIGH-12:** Lisätään vanhojen scratch-hakemistojen automaattinen siivous.
  8. **F-MED-04 & F-MED-03:** Muutetaan OSM-lataus suoratoistavaksi levylle ja otetaan käyttöön `QgsGeometryEngine` (Prepared Geometry) leikkauksissa.
  9. **F-MED-01 & F-MED-02:** Optimoidaan `DeleteIdentical` ID-kentillä ja korvataan levyprojektointi in-memory `.projectAs()` -metodilla.
  10. **F-MED-05 & F-LOW-04:** Lisätään `@lru_cache` aluerajausvalintoihin ja debounce hakukenttään.
  11. **F-MED-09:** Lisätään `MMLBasemapDownloader`-painike `Config.daml`-tiedostoon ja C#-käynnistimeen.
  12. **F-MED-10 & F-MED-11:** Harmonisoidaan BBOX-puskurointi ja tuodaan WMTS-tiilituki QGIS-lisäosaan.

### Vaihe 3: Arkkitehtuurin refaktorointi ja koodin yhdistäminen (P2)
- **Tavoite:** Poistaa monoliittisuus, eliminoida koodiduplikaatio ja saavuttaa 100 % symmetrinen CI-testikattavuus.
- **Toimenpiteet:**
  1. **F-HIGH-13:** Pilkotaan ArcGIS Pron 8 546-rivinen `.pyt`-monoliitti modulaarisiin Python-tiedostoihin.
  2. **F-MED-08:** Eristetään yhteinen alustariippumaton ydinmoduuli `vayla_wfs_core` ja päivitetään paketointityökalut.
  3. **F-HIGH-14:** Luodaan `tests/test_qgis_services.py` ja integroidaan QGIS-latauslogiikan testit osana normaalia `pytest`-ajoa.
  4. **Testipatteriston laajennus:** Lisätään ääritapaustestit (katkennut JSON, HTTP 429 rate limit, 3D/Z-koordinaatit ja datum-muunnokset).
