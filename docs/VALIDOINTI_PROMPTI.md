# Validointiprompti paikalliselle agentille (ArcGIS Pro + QGIS)

Kopioi alla oleva prompti paikalliseen Claude Code -agenttiin Windows-koneella,
jossa ArcGIS Pro 3.5+ (ja mielellään QGIS 3.44) on asennettu. Prompti validoi
versiossa 1.0.17 / QGIS 0.2.7 tehdyt katselmointikorjaukset.

---

```text
Olet Suomenväylät-repon (roopepalom44/suomenvaylat) validointiagentti Windows-
koneella, jossa on ArcGIS Pro 3.5 tai uudempi. Tehtäväsi on VALIDOIDA main-haaran
katselmointikorjaukset (AddIn 1.0.17, QGIS 0.2.7) oikealla arcpyllä ja oikeilla
palveluilla. Älä muuta tuotantokoodia äläkä pushaa mitään. Jos löydät vian,
kirjaa se raporttiin: toistoaskeleet, lokiote ja ehdotettu korjaus.

Taustaa korjauksista (ks. myös README.md ja docs/ARCGIS_PRO_TESTIT.md, osio 12):
- Tulosnimet varataan ajon ajaksi: saman ajon samannimiset tasot eivät
  ylikirjoita toisiaan (Nimi, Nimi_1; verkko-GDB: Nimi_<ajo>, Nimi_2_<ajo>).
- Tyhjä tallennuskohde -> projektin oletus-GDB; jos sitä ei ole, selkeä virhe.
  Tuloksia ei koskaan kirjoiteta ajon scratch-kansioon.
- Tasokohtainen virheensieto: Merge/Clip/kopiointivirhe kaataa vain kyseisen
  tason; ajo päättyy "Ajo suoritettu osittain".
- OSM: sekageometrisista tasoista syntyy omat tulokset _pisteet/_viivat/_alueet;
  relaatiot (multipolygonit, hallinnolliset alueet) kootaan alueiksi; suljetut
  tiet/aidat pysyvät viivoina.
- Raskaat tasot (liikennemaar*) haetaan kunnittain yhteisellä sivutuksella;
  vajaa sivutus kaataa tason eikä tallenna vajaata aineistoa.
- Rajauksen union-virhe käyttää Dissolve-varareittiä (ei enää vain 1. aluetta).
- Sivutuksen "vajaa"-tulkinta: vajaa viimeinen sivu tai numberMatched täynnä = OK.
- Authorization-otsake pudotetaan uudelleenohjauksessa toiselle hostille,
  HTTPS->HTTP estetty, 4xx/varmennevirheitä ei uusita.
- Rinnakkaisen esihaun lokiviestit kirjoitetaan pääsäikeestä.
- Taustakarttatyökalu palauttaa arcpy.env.overwriteOutput-asetuksen ja käyttää
  Kapsille ajokohtaista scratchia.
- Legacy MML WMTS -koodi (mml_raster) poistettiin.

VAIHEET

1. Valmistelu
   - git fetch && git checkout main && git pull. Kirjaa HEAD-commit.
   - Käytä ArcGIS Pron Pythonia:
     $py = "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
   - Aja: & $py -m unittest discover -s tests -v
     Kaikkien testien pitää mennä läpi (DPAPI-testi ajetaan Windowsissa).

2. AddIn
   - Käännä: powershell -ExecutionPolicy Bypass -File .\build-addin.ps1 -Configuration Release
     (tai lataa uusin Suomenvaylat.esriAddInX GitHub-releasesta). Asenna, avaa
     ArcGIS Pro ja varmista, että Suomenväylät.fi-painike avaa työkalun ilman
     Python-virheitä. Aja powershell .\diagnose-addin.ps1 -PackagePath <paketti>.

3. Olemassa olevat arcpy-smoke-testit
   - & $py tests\smoke_selection_arcgispro.py
   - & $py tests\smoke_traficom_oskari_arcgispro.py

4. Kirjoita scratch-kansioon (EI repoon) arcpy-skripti validate_1017.py, joka
   lataa työkalun: arcpy.ImportToolbox(r"<repo>\Toolboxes\VaylaWFSDownloader.pyt")
   ja ajaa sen funktiona arcpy.VaylaWFSDownloader_vayla_wfs_lataus(...).
   Parametrit järjestyksessä: wfs_sources (esim. "OpenStreetMap" tai
   "Väylä;DigiRoad"), layer_search "", layers (tasojen näyttönimet
   puolipisteillä), extent_type, extent_value, custom_layer, workspace,
   mml_api_key, karttapaikka_api_key, karttakuva_user, karttakuva_pass,
   refresh_layer_catalog, aino_token.
   Hae tasojen tarkat näyttönimet tasoluettelosta, esim.
     mod = importlib.machinery.SourceFileLoader("t", pyt).load_module()
     tool = mod.VaylaWFSDownloader(); labels = tool._fetch_layer_list(["OpenStreetMap"])
   Käytä pientä rajausta (Kunta/Kaupunki: Kauniainen tai Helsinki) ja uutta
   testi-GDB:tä jokaiselle skenaariolle (arcpy.management.CreateFileGDB).
   Tallenna jokaisen ajon arcpy.GetMessages() tiedostoon.

   Skenaariot ja hyväksymisehdot:
   A. Nimivaraus: lähteet "Väylä;DigiRoad", kaksi samannimistä tasoa (etsi
      luettelosta otsikot, jotka ovat samat ennen " - Lähde"-osaa, tai saman
      lähteen "(2)"-päätteinen pari). -> GDB:ssä molemmat tulokset eri nimillä,
      kohdemäärät > 0 ja ne vastaavat lokin "Taso valmis" -rivejä.
   B. Tallennuskohde: aja standalone-Pythonissa workspace tyhjänä (ei CURRENT-
      projektia) -> ajon pitää päättyä selkeään virheeseen "Tallennuskohdetta
      ei annettu ...", ei tuloksia %TEMP%\suomenvaylat_*-kansioon.
   C. OSM sekageometria: tasot "Osoitteet", "Tiet", "Kaupat" (OpenStreetMap).
      -> ajo ei kaadu; Osoitteet/Kaupat tuottavat _pisteet ja _alueet -tulokset
      (jos alueella molempia); Tiet_viivat on Polyline ja suljetut tiet
      (esim. kiertoliittymät) ovat siinä. Tiet_alueet saa sisältää vain
      area=yes-kohteita (esim. highway=pedestrian-aukiot).
   D. OSM-relaatiot: "Hallinnolliset alueet" ja "Metsat" Helsingin rajauksella.
      -> Hallinnolliset alueet ei ole tyhjä ja sisältää Polygon-tuloksen;
      tarkista osm_type-kentästä, että mukana on "relation"-rivejä.
   E. Virheensieto: sama ajo sisältää toimivan tason ja tahallisesti
      epäonnistuvan tason (esim. lähde "Aino" virheellisellä tokenilla
      "invalid-token" + OSM "Koulut"). -> Koulut tallentuu, Aino näkyy
      epäonnistuneena, loki päättyy "Ajo suoritettu osittain".
   F. Raskas taso: Väylän liikennemaar*-alkuinen taso, extent_type "Maakunta",
      extent_value "Uusimaa" (tai pienempi maakunta). -> lokissa kuntakohtainen
      haku, tulos > 0 kohdetta, ei ExecuteErroria; DeleteIdentical-ajon jälkeen
      duplikaatteja ei ole (vertaa GetCount ennen/jälkeen
      arcpy.management.FindIdentical).
   G. Monikuntarajaus: Väylä-taso "Kunta/Kaupunki" = "Helsinki;Espoo;Vantaa".
      -> lokin CQL-geometrian osa-/pistemäärä kattaa kaikki kolme; tuloksen
      extent ulottuu kaikkiin kuntiin.
   H. Taustakarttatyökalu: aseta arcpy.env.overwriteOutput = False, aja
      arcpy.MMLBasemapDownloader_vayla_wfs_lataus Kapsi-palvelulla pienelle
      alueelle ja varmista, että overwriteOutput on ajon jälkeen edelleen False
      ja JPG + .jgw syntyivät tallennuskohteen rasterikansioon.
   I. Sivutus ja katkaisu: aja monisivuinen taso (esim. DigiRoad "Linkki"
      maakuntarajauksella). -> ei "vaillinainen"-virhettä, jos data on täysi;
      tulosmäärä vastaa palvelun numberMatched-arvoa (tarkista suoralla
      GetFeature resultType=hits -pyynnöllä).
   J. Tunnisteet: aja Karttapaikka- tai MML-OGC-taso API-avaimella (jos
      käytettävissä). Hae koko loki ja %TEMP%-scratch: avainta ei saa löytyä
      selväkielisenä mistään. %APPDATA%\Suomenvaylat\service_credentials.json
      arvot alkavat "dpapi:".

5. ArcGIS Pron käyttöliittymä (tee itse tai pyydä käyttäjää tekemään ja
   raportoimaan): docs\ARCGIS_PRO_TESTIT.md osiot 8, 10 (Aino WMS, jos token
   on) ja 12. Erityisesti: MML-vektoritiili lisätään Taustakartta-ryhmään;
   Aino WMS -taso ei katoa kartalta ryhmään siirrettäessä.

6. QGIS (jos QGIS 3.44 on asennettu)
   - Aja ensin verkotta qgis_plugin\smoke_audit_fixes.py (aseta
     QGIS_PREFIX_PATH, esim. C:\Program Files\QGIS 3.44.x\apps\qgis-ltr);
     lopussa pitää tulostua "ALL QGIS AUDIT SMOKE CHECKS PASSED".
   - Aja qgis_plugin\smoke.py, smoke_selection.py, smoke_tile.py,
     smoke_custom_crs.py ja smoke_spatial_live.py QGISin Pythonilla
     (ks. qgis_plugin\README.md). Päivitä tarvittaessa skriptien
     setPrefixPath paikalliseen QGIS-polkuun, mutta älä commitoi muutosta.
   - Asenna python qgis_plugin\package.py -paketti QGISiin ja tarkista
     käsin: lataus ei jäädytä QGISiä, Keskeytä-painike toimii, OSM "Osoitteet"
     tuottaa erilliset pisteet/alueet-tasot, "Hallinnolliset alueet" ei ole
     tyhjä, MML-avaimen tunnistautumisasetuksia on vain yksi
     (Asetukset -> Tunnistautuminen) vaikka taustakartan lisää useasti.

RAPORTTI
Palauta taulukko: skenaario | tulos (PASS/FAIL/SKIP + syy) | todisteet
(kohdemäärät, tulosnimet, lokiotteet). Listaa lopuksi kaikki FAIL-kohdat
toistoaskelineen ja ehdota korjaus (tiedosto:rivi). Älä pushaa mitään.
```
