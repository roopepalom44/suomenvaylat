## Suomenväylät QGIS 0.2.0 — esijulkaisu

Asenna lataamalla `Suomenvaylat-QGIS-0.2.0.zip` ja valitsemalla QGISissä **Lisäosat → Hallitse ja asenna lisäosia → Asenna ZIP-tiedostosta**. Vaatii QGIS 3.44:n.

Tässä versiossa toimivat WFS- ja OGC API Features -aineistojen haku aluerajauksella, hallinnolliset alueet, Kapsin GeoTIFF-lataus, OSM:n Overpass-haku sekä Kapsin ja MML:n taustakarttatasot. Uutta: useita lähteitä voi valita samaan ajoon, Aino WMS- ja MML Karttakuva -tasot voidaan lisätä live-tasoina, ja Aino-token säilytetään QGISin tunnistautumistietokannassa. QGIS 3.44:ssä on testattu viiden WFS-lähteen tasoluettelot, Väylän rajattu lataus, Kapsin rasterilataus, OSM:n rajattu lataus, OGC-sivutus sekä tunnistautumista vaativien tasojen määritys ilman oikeita palvelutunnuksia.

**Tämä ei vielä ole toiminnallisesti identtinen ArcGIS Pro -version kanssa.** Aino WMS:n, MML Karttakuvan ja MML-vektoritiilien oikeaa palvelukäyttöä ei ole voitu validoida ilman tunnuksia. OSM-attribuutit tallennetaan eri tavalla. Tarkempi tilanne: [QGIS-ohje](https://github.com/roopepalom44/suomenvaylat/blob/main/qgis_plugin/README.md).
