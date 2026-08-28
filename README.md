# Suomenväylät - ArcGIS Pro Add-in

ArcGIS Pro -laajennus Suomenväylät-aineistojen lataamiseen WFS-rajapinnoista.

## Vaatimukset


`Config.daml` ja C#-lähdekoodi käyttävät samaa painiketyyppiä: `OpenSuomenvaylatToolButton`.

## Manuaalinen paketointi ilman MSBuildia

Kun C#-osa on jo käännetty esimerkiksi ArcGIS Pron AssemblyCacheen, paketoi nykyiset lähdetiedostot yhdellä PowerShell-komennolla:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1
```

Skripti etsii ensin `suomenvaylat.dll`-tiedoston bin-kansiosta ja sen jälkeen ArcGIS Pron AssemblyCachesta. Se rakentaa uuden `bin\Debug\net8.0-windows\suomenvaylat.esriAddInX`-paketin tyhjästä, sisältää Python-työkalun ja resurssit sekä validoi paketin sisällön.

Jos DLL on muualla, anna sen polku:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

Skripti ei käännä C#-lähdekoodia. Se on tarkoitettu erityisesti Python-työkalun ja sen resurssien päivittämiseen ilman MSBuildia. C#-lähdekoodin muutokset vaativat erillisen .NET/ArcGIS Pro SDK -käännöksen.

### TypeNotFound / command unavailable

Jos ArcGIS Pro näyttää virheen `TypeNotFound` tai ilmoittaa komennon olevan unavailable, paketin DLL on yleensä vanha, väärä tai se ei sisällä nykyistä painiketyyppiä. `package-addin.ps1` tarkistaa nyt ennen paketointia, että DLL on managed .NET -assembly ja sisältää tyypit `Module1` sekä `OpenSuomenvaylatToolButton`. Lisäksi Add-in-versiona on `1.0.3`, jotta ArcGIS Pro ei käytä vanhaa saman tunnisteen pakettia.

Jos tarkistus ilmoittaa väärästä DLL:stä, käännä C#-projekti ArcGIS Pro SDK:n kanssa ja anna tulos suoraan:

```powershell
powershell -ExecutionPolicy Bypass -File .\package-addin.ps1 -AssemblyPath "C:\polku\suomenvaylat.dll"
```

WFS- ja rasterikäsittely tehdään paikallisessa `scratchGDB`-työtilassa. Verkkotyötilaan kopioidaan vasta valmis taso tai rasteri, joten verkkoaseman hitaus ei hidasta jokaista välivaihetta. Lokissa näkyvät nyt jokaisen tason nimi, HTTP-aika, geoprocessing-vaiheiden ajat ja koko ajon kokonaisaika.
