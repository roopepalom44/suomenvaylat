# OpenStreetMap / POI-pisteet

## Tutkittu vertailuaineisto

Toteutuksen vertailuaineistona käytettiin tiedostoa
`finland__gis_osm_pois_free.gpkg`. GeoPackage sisältää yhden
`finland__gis_osm_pois_free`-feature-taulun:

- geometria: `POINT`, OGC CRS84 (WGS 84, longitude/latitude);
- kohteita: 114 130;
- attribuutit: `osm_id`, `code`, `fclass`, `name`;
- eri `code`/`fclass`-luokkia: 141;
- geometrialtaan tyhjiä kohteita: 0;
- 591 OSM-tunnusta esiintyy useassa luokassa.

Vertailuaineiston luokkaryhmät:

| Koodit | Ryhmä | Kohteita | Luokkia |
|---|---|---:|---:|
| 2000–2079 | julkiset palvelut ja kierrätys | 8 705 | 21 |
| 2080–2099 | koulutus | 1 202 | 5 |
| 2100–2199 | terveys | 1 980 | 6 |
| 2200–2299 | vapaa-aika ja urheilu | 3 466 | 15 |
| 2300–2399 | ravitsemus | 11 509 | 7 |
| 2400–2499 | majoitus ja ulkoilupalvelut | 6 432 | 10 |
| 2500–2599 | kaupat ja kaupalliset palvelut | 15 228 | 41 |
| 2600–2699 | pankit ja automaatit | 1 572 | 2 |
| 2700–2799 | matkailu ja historia | 23 662 | 17 |
| 2900–2999 | muut POI-kohteet | 40 374 | 17 |

Luokkakartoitus perustuu Geofabrikin ajantasaiseen
[Free Shapefiles and GeoPackages -määrittelyyn](https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf)
ja toimitetun Suomen aineiston toteutuneisiin `code`/`fclass`-pareihin.

## Suomenväylien toteutus

Käyttöliittymässä taso on **OpenStreetMap → POI-pisteet**. Se ei ole uusi
erillinen aineistolähde, vaan kuuluu nykyiseen OpenStreetMap-lähteeseen.

Työkalu muodostaa yhden Overpass QL -kyselyn OSM:n `amenity`, `historic`,
`landuse`, `leisure`, `man_made`, `office`, `shop`, `sport` ja `tourism`
-tageista. Kysely hakee `node`, `way` ja `relation` -kohteet. Node säilyy
pisteenä; wayn ja relationin tulokseksi otetaan Overpassin laskema keskipiste.
Tuntemattomat tagiarvot suodatetaan pois paikallisessa luokitusvaiheessa.

Yksi OSM-kohde voi vastata useaa Geofabrik-luokkaa. Tällöin tulokseen tehdään
yksi piste jokaista osuvaa luokkaa kohti. Tuloksen kentät ovat:

| Kenttä | Sisältö |
|---|---|
| `osm_id` | OSM-kohteen tunnus tekstinä |
| `osm_type` | `node`, `way` tai `relation` |
| `code` | Geofabrik-luokan numerokoodi |
| `fclass` | Geofabrik-luokan nimi |
| `name` | OSM:n `name`-tagi tai tyhjä arvo |

Rajapintana käytetään [Overpass APIa](https://wiki.openstreetmap.org/wiki/Overpass_API).
Ensisijaisen palvelun virheessä työkalu kokeilee automaattisesti toista
julkista instanssia. Jos rajaus on yhdelle pyynnölle liian tiheä, se yritetään
uudelleen 2×2-, 4×4- ja 8×8-ruudukkoina. Ruutujen identtiset rivit poistetaan
ennen tarkkaa paikallista Clip-rajausta.

## Yhteys- ja vertailutesti 15.9.2026

Kampin testiruudun Overpass POST -pyyntö onnistui HTTP 200 -vastauksella.
Vertailutiedoston pistekerros palautti ruudusta 25 kohdetta ja yhdistetty live-
haku 26 kohdetta. Lisäkohde oli wayna mallinnettu **Kampin keskus**
(`fclass=mall`), joka muunnettiin odotetusti pisteeksi. Live-vastauksessa oli
samalla sekä `node`- että `way`-lähteisiä kohteita.
