# Suomenväylät QGIS (esijulkaisu 0.2.11)

QGIS 3.44:lle tehty erillinen, natiivi Python-lisäosa. ArcGIS Pro -laajennus pysyy samassa projektissa.

## Asennus

**Windows, suoraviivainen asennus:** Lataa [uusimmasta julkaisusta](https://github.com/roopepalom44/suomenvaylat/releases/latest) `Suomenvaylat-QGIS-<versio>-Windows.zip`, pura ZIP ja kaksoisnapsauta `install_windows.bat`. Sulje QGIS ennen asennusta. Asennin korvaa aiemman version kokonaan, kopioi lisäosan käyttäjän kaikkiin olemassa oleviin QGIS 3 -profiileihin (tai luo `default`-profiilin) ja ottaa lisäosan käyttöön. Käynnistä QGIS asennuksen jälkeen.

**QGISin oma asennus:** Lataa samasta julkaisusta `Suomenvaylat-QGIS-<versio>.zip` ja valitse QGISissä **Lisäosat → Hallitse ja asenna lisäosia → Asenna ZIP-tiedostosta**. Jos vanhan projektin asetuksissa lukee **Ei koordinaattijärjestelmää**, valitse projektin CRS:ksi **EPSG:3067**. Tämä sijoittaa jo ladatut EPSG:3067-tasot oikein ilman uutta latausta.

## Toimii tässä versiossa

- Rajapinnan SLD-symboliikan automaattinen lataus WFS- ja Oskari-vektoritasoille. Valittu oletustyyli tuodaan QGISiin ja tallennetaan GeoPackagen oletustyyliksi sekä `.sld`- ja `.qml`-tiedostoina. Luokittelut, viivanleveydet, värit ja mittakaavarajat tulevat palvelun tyylistä. Tyylinhaun virhe ei estä aineiston tallennusta. [Rajapintakohtainen selvitys ja rajoitukset](../docs/SYMBOLIIKKA.md).
- MML:n vektoritiilien esitystyyli ladataan erikseen: Taustakartta ja Maastokartta käyttävät eri Mapbox-tyylejä, Kiinteistöjaotus QGISiin sopivaa pelkistettyä tyyliä.

- Väylän, DigiRoadin, Liiterin, SYKEn, Tilastokeskuksen, Karttapaikan ja Ainon WFS-tasojen haku suoraan palveluiden tasoluetteloista. Useita lähteitä voi valita samaan ajoon.
- Aino-pyynnöt lähettävät Suomenväylät-User-Agentin. Palvelun Cloudflare estää Pythonin oletustunnisteen virheellä HTTP 403 / 1010 ennen tokenin tarkistusta.
- MML:n kiinteistöaineistojen ja Karttapaikan maastotietojen OGC API Features -sivutus sekä API-avain HTTP Basic -otsakkeessa.
- Hallinnolliset aluerajaukset mukana tulevasta GeoPackagesta: koko Suomi, elinvoimakeskus, hyvinvointialue, maakunta ja kunta. Myös projektin oma polygon- tai viivataso kelpaa.
- WFS- ja OGC-aineistojen rajattu lataus EPSG:3067-GeoPackageen, onnistuneiden tasojen lisäys projektiin. Väylän ja DigiRoadin kohteet tallennetaan kokonaisina kuten ArcGIS Pron CQL-haussa; muut vektoriaineistot leikataan rajaukseen. Oma viivarajaus muutetaan kohteiden konveksiksi peitteeksi kuten ArcGIS Prossa, ja tason valinta rajaa käytettävät kohteet.
- Tasoluettelon haku ja lataukset ajetaan QGISin taustatehtävinä, joten QGIS ei jäädy latauksen ajaksi. Latauksen voi keskeyttää **Keskeytä**-painikkeella.
- Kapsin WMS-tasojen tasoluettelo ja alueittainen, rinnakkain tiilitetty GeoTIFF-lataus.
- OpenStreetMapin 27 tasoa Overpass-palvelun kautta. OSM-tägit säilytetään `tags`-JSON-kentässä. Relaatiot (esim. hallinnolliset alueet ja metsä- tai vesialueiden multipolygonit) kootaan alueiksi. Jos tasossa on useita geometriatyyppejä, pisteet, viivat ja alueet tallentuvat omiksi tasoikseen samaan GeoPackageen.
- Kapsin kolme WMS-taustakarttaa live-tasoina sekä MML:n kolme vektoritiilitaustakarttaa QGISin tunnistautumisasetuksella. Aino WMS ja MML Karttakuva voidaan lisätä live-tasoina; tunnukset säilytetään QGISin tunnistautumistietokannassa.
- Traficomin Oskarin 76 tason dynaaminen luettelo: 61 vektoritasoa attribuutteineen sekä 10 WMS- ja viisi WMTS-karttatasoa GeoTIFF-kuvina.
- Tallennetun QGIS-projektin kansio ehdotetaan aineistojen tallennuskansioksi. Tallentamaton projekti ei vielä anna oletuskansiota.

## Erot ArcGIS Pro -versioon

- Aino WMS- ja MML Karttakuva -tasojen oikeaa palvelulatausta ei ole voitu testata ilman käyttäjän tunnuksia. Niiden tasoluettelo ja QGIS-tason muodostus on testattu jäljitellyillä vastauksilla.
- Kapsin taustakartta-välilehden live-WMS poikkeaa ArcGIS Pro -version rasterilatauksesta. Rasterin lataus löytyy Aineistot-välilehden Kapsi-lähteestä.
- OSM-tägit ovat yhdessä JSON-kentässä; ArcGIS Pro -versio luo niistä erillisiä attribuuttikenttiä.
- MML:n live-vektoritiilit vaativat QGISin tunnistautumistietokannan; niiden näyttöä ei ole voitu testata ilman käyttäjän API-avainta.

Lisäosa on merkitty esijulkaisuksi, koska käyttöliittymä ja osa palvelu- sekä attribuuttikäsittelystä eroavat ArcGIS Pro -versiosta. Molempien versioiden muutoksia ei voi olettaa automaattisesti samoiksi.

## Kehitys ja testaus

Paketointi: `python qgis_plugin/package.py` (hallinnolliset aluejaot otetaan pakettiin tiedostosta `Toolboxes/Resources/hallinnolliset_aluejaot.gpkg`; lähdekoodista ajettaessa lisäosa käyttää samaa tiedostoa). Ilman QGISiä ajettavat yksikkötestit: `python -m unittest discover -s tests`. QGIS 3.44:n Python-ympäristön testit (dialogi ajaa taustatehtävät synkronisesti, kun `run_tasks_synchronously = True`): `qgis_plugin/smoke.py`, `qgis_plugin/smoke_live.py`, `qgis_plugin/smoke_spatial_live.py` (13 julkisen palvelun sijaintitarkistusta) ja `qgis_plugin/smoke_custom_crs.py` (EPSG:3857-projekti ja rajaus) sekä verkotta ajettava `qgis_plugin/smoke_audit_fixes.py` (0.2.7:n korjaukset: viivarajaus, OGC-kentät, OSM-relaatiot ja geometriajako, WFS-leikkaus, tunnistautumisasetukset ja taustatehtävä; QGIS-polku ympäristömuuttujalla `QGIS_PREFIX_PATH`). Tunnuksia vaativien palvelujen vastaukset on testattu simuloituina; oikea palvelulataus tarvitsee kyseisen palvelun tunnukset.
