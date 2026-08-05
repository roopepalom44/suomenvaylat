# Suomenväylät - ArcGIS Pro Add-in

Tämä on ArcGIS Pro -laajennus (Add-in), joka tarjoaa työkalut Suomenväylät-aineiston hyödyntämiseen suoraan ArcGIS Pro -ympäristössä. Se sisältää muun muassa työkalun WFS-rajapintojen lukemiseen.

## Ominaisuudet
- **Suomenväylät-työkalu:** Avaa Python-pohjaisen geoprosessointityökalun (`VaylaWFSDownloader.pyt`) suoraan ArcGIS Pron käyttöliittymästä.
- **Integraatio ArcGIS Prohon:** Lisää työkalupainikkeen ArcGIS Pron käyttöliittymään (katso `Config.daml`).

## Vaatimukset
- ArcGIS Pro 3.x
- .NET 8.0 Desktop Runtime

## Projektin rakenne
- `suomenvaylat.csproj`: Laajennuksen C#-projektitiedosto.
- `Config.daml`: ArcGIS Pro -käyttöliittymän konfiguraatio (määrittää painikkeet ja välilehdet).
- `OpenSuomenvaylatToolButton.cs`: C#-koodi, joka vastaa työkalun avaamisesta, kun painiketta klikataan.
- `Toolboxes/VaylaWFSDownloader.pyt`: Python-työkalupakki (Python Toolbox), joka suorittaa varsinaisen työn (esim. WFS-lataukset).
- `Suomenvaylat_kayttoohje.pdf`: Projektin käyttöohje.

## Asennus ja kääntäminen
1. Avaa `suomenvaylat.slnx` tai `suomenvaylat.csproj` Visual Studiossa.
2. Varmista, että ArcGIS Pro on asennettuna koneelle (SDK-riippuvuudet haetaan asennuskansiosta).
3. Käännä (Build) projekti.
4. Käännetty `.esriAddinX`-tiedosto muodostuu `bin`-kansioon ja/tai asentuu automaattisesti ArcGIS Prohon, jos asetukset ovat kunnossa.
