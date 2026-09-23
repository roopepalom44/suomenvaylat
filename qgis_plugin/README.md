# Suomenväylät QGIS (esijulkaisu 0.2.0)

QGIS 3.44:lle tehty erillinen, natiivi Python-lisäosa. ArcGIS Pro -laajennus pysyy samassa projektissa.

## Asennus

1. Lataa [Suomenvaylat-QGIS-0.2.0.zip](https://github.com/roopepalom44/suomenvaylat/releases/download/qgis-v0.2.0/Suomenvaylat-QGIS-0.2.0.zip).
2. Avaa QGISissä **Lisäosat → Hallitse ja asenna lisäosia → Asenna ZIP-tiedostosta**.
3. Valitse ladattu ZIP. Suomenväylät näkyy lisäosavalikossa ja työkalurivillä.

## Toimii tässä versiossa

- Väylän, DigiRoadin, Liiterin, SYKEn, Karttapaikan ja Ainon WFS-tasojen haku suoraan palveluiden tasoluetteloista. Useita lähteitä voi valita samaan ajoon.
- MML:n kiinteistöaineistojen ja Karttapaikan maastotietojen OGC API Features -sivutus sekä API-avain HTTP Basic -otsakkeessa.
- Hallinnolliset aluerajaukset mukana tulevasta GeoPackagesta: koko Suomi, elinvoimakeskus, hyvinvointialue, maakunta ja kunta. Myös projektin oma polygon- tai viivataso kelpaa.
- WFS- ja OGC-aineistojen rajattu lataus EPSG:3067-GeoPackageen, onnistuneiden tasojen lisäys projektiin.
- Kapsin WMS-tasojen tasoluettelo ja alueittainen, rinnakkain tiilitetty GeoTIFF-lataus.
- OpenStreetMapin 27 tasoa Overpass-palvelun kautta. OSM-tägit säilytetään `tags`-JSON-kentässä.
- Kapsin kolme WMS-taustakarttaa live-tasoina sekä MML:n kolme vektoritiilitaustakarttaa QGISin tunnistautumisasetuksella. Aino WMS ja MML Karttakuva voidaan lisätä live-tasoina; tunnukset säilytetään QGISin tunnistautumistietokannassa.

## Erot ArcGIS Pro -versioon

- Aino WMS- ja MML Karttakuva -tasojen oikeaa palvelulatausta ei ole voitu testata ilman käyttäjän tunnuksia. Niiden tasoluettelo ja QGIS-tason muodostus on testattu jäljitellyillä vastauksilla.
- Kapsin taustakartta-välilehden live-WMS poikkeaa ArcGIS Pro -version rasterilatauksesta. Rasterin lataus löytyy Aineistot-välilehden Kapsi-lähteestä.
- OSM-tägit ovat yhdessä JSON-kentässä; ArcGIS Pro -versio luo niistä erillisiä attribuuttikenttiä.
- MML:n live-vektoritiilit vaativat QGISin tunnistautumistietokannan; niiden näyttöä ei ole voitu testata ilman käyttäjän API-avainta.

Lisäosa on merkitty esijulkaisuksi, koska se ei vielä täytä tavoitetta täysin identtisestä toiminnasta.

## Kehitys ja testaus

Paketointi: `python qgis_plugin/package.py`. QGIS 3.44:n Python-ympäristössä tehty testit: `qgis_plugin/smoke.py` ja `qgis_plugin/smoke_live.py`.
