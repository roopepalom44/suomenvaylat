# Suomenväylät QGIS (esijulkaisu 0.2.3)

QGIS 3.44:lle tehty erillinen, natiivi Python-lisäosa. ArcGIS Pro -laajennus pysyy samassa projektissa.

## Asennus

**Windows, suoraviivainen asennus:** Lataa uusimmasta [yhteisestä julkaisusta](https://github.com/roopepalom44/suomenvaylat/releases/latest) `Suomenvaylat-QGIS-0.2.3-Windows.zip`, pura ZIP ja kaksoisnapsauta `install_windows.bat`. Sulje QGIS ennen asennusta. Asennin kopioi lisäosan käyttäjän kaikkiin olemassa oleviin QGIS 3 -profiileihin (tai luo `default`-profiilin) ja ottaa lisäosan käyttöön. Käynnistä QGIS asennuksen jälkeen.

**QGISin oma asennus:** Lataa samasta julkaisusta `Suomenvaylat-QGIS-0.2.3.zip` ja valitse QGISissä **Lisäosat → Hallitse ja asenna lisäosia → Asenna ZIP-tiedostosta**.

## Toimii tässä versiossa

- Väylän, DigiRoadin, Liiterin, SYKEn, Karttapaikan ja Ainon WFS-tasojen haku suoraan palveluiden tasoluetteloista. Useita lähteitä voi valita samaan ajoon.
- MML:n kiinteistöaineistojen ja Karttapaikan maastotietojen OGC API Features -sivutus sekä API-avain HTTP Basic -otsakkeessa.
- Hallinnolliset aluerajaukset mukana tulevasta GeoPackagesta: koko Suomi, elinvoimakeskus, hyvinvointialue, maakunta ja kunta. Myös projektin oma polygon- tai viivataso kelpaa.
- WFS- ja OGC-aineistojen rajattu lataus EPSG:3067-GeoPackageen, onnistuneiden tasojen lisäys projektiin.
- Kapsin WMS-tasojen tasoluettelo ja alueittainen, rinnakkain tiilitetty GeoTIFF-lataus.
- OpenStreetMapin 27 tasoa Overpass-palvelun kautta. OSM-tägit säilytetään `tags`-JSON-kentässä.
- Kapsin kolme WMS-taustakarttaa live-tasoina sekä MML:n kolme vektoritiilitaustakarttaa QGISin tunnistautumisasetuksella. Aino WMS ja MML Karttakuva voidaan lisätä live-tasoina; tunnukset säilytetään QGISin tunnistautumistietokannassa.
- Traficomin Oskarin 76 tason dynaaminen luettelo: 61 vektoritasoa attribuutteineen sekä 10 WMS- ja viisi WMTS-karttatasoa GeoTIFF-kuvina.

## Erot ArcGIS Pro -versioon

- Aino WMS- ja MML Karttakuva -tasojen oikeaa palvelulatausta ei ole voitu testata ilman käyttäjän tunnuksia. Niiden tasoluettelo ja QGIS-tason muodostus on testattu jäljitellyillä vastauksilla.
- Kapsin taustakartta-välilehden live-WMS poikkeaa ArcGIS Pro -version rasterilatauksesta. Rasterin lataus löytyy Aineistot-välilehden Kapsi-lähteestä.
- OSM-tägit ovat yhdessä JSON-kentässä; ArcGIS Pro -versio luo niistä erillisiä attribuuttikenttiä.
- MML:n live-vektoritiilit vaativat QGISin tunnistautumistietokannan; niiden näyttöä ei ole voitu testata ilman käyttäjän API-avainta.

Lisäosa on merkitty esijulkaisuksi, koska käyttöliittymä ja osa palvelu- sekä attribuuttikäsittelystä eroavat ArcGIS Pro -versiosta. Molempien versioiden muutoksia ei voi olettaa automaattisesti samoiksi.

## Kehitys ja testaus

Paketointi: `python qgis_plugin/package.py`. QGIS 3.44:n Python-ympäristössä tehty testit: `qgis_plugin/smoke.py` ja `qgis_plugin/smoke_live.py`.
