<?xml version='1.0' encoding='utf-8'?>
<sld:StyledLayerDescriptor xmlns:ogc="http://www.opengis.net/ogc" xmlns:sld="http://www.opengis.net/sld" version="1.0.0"><sld:NamedLayer><sld:Name>tiestotiedot:aidat</sld:Name><sld:UserStyle>
            <sld:Name>Aidat_velho</sld:Name>
            <sld:IsDefault>1</sld:IsDefault>
            <sld:FeatureTypeStyle>
                <sld:Name>name</sld:Name>
                <sld:Rule>
                    <sld:Name>Riista-aita</sld:Name>
                    <sld:Title>Riista-aita</sld:Title>
                    <ogc:Filter>
                        <ogc:PropertyIsEqualTo>
                            <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            <ogc:Literal>Riista-aidat</ogc:Literal>
                        </ogc:PropertyIsEqualTo>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#4477aa</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>Lumiaita</sld:Name>
                    <sld:Title>Lumiaita</sld:Title>
                    <ogc:Filter>
                        <ogc:PropertyIsEqualTo>
                            <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            <ogc:Literal>Lumiaidat</ogc:Literal>
                        </ogc:PropertyIsEqualTo>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ccbb44</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>Putoamista estävä suoja-aita liikenneväylillä</sld:Name>
                    <sld:Title>Putoamista estävä suoja-aita liikenneväylillä</sld:Title>
                    <ogc:Filter>
                        <ogc:PropertyIsEqualTo>
                            <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            <ogc:Literal>Putoamista estävät suoja-aidat liikenneväylillä</ogc:Literal>
                        </ogc:PropertyIsEqualTo>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ee6677</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>Liikenneväylän ylitystä estävä aita</sld:Name>
                    <sld:Title>Liikenneväylän ylitystä estävä aita</sld:Title>
                    <ogc:Filter>
                        <ogc:PropertyIsEqualTo>
                            <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            <ogc:Literal>Liikenneväylän ylitystä estävä aita</ogc:Literal>
                        </ogc:PropertyIsEqualTo>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#117733</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>Muu aita</sld:Name>
                    <sld:Title>Muu aita</sld:Title>
                    <ogc:Filter>
                        <ogc:PropertyIsEqualTo>
                            <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            <ogc:Literal>Muu</ogc:Literal>
                        </ogc:PropertyIsEqualTo>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#818181</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>Muu</sld:Name>
                    <ogc:Filter>
                        <ogc:Or>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                                <ogc:Literal />
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsNull>
                                <ogc:PropertyName>rakenteelliset_ominaisuudet_tyyppi</ogc:PropertyName>
                            </ogc:PropertyIsNull>
                        </ogc:Or>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#818181</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
            </sld:FeatureTypeStyle>
        </sld:UserStyle>
        </sld:NamedLayer></sld:StyledLayerDescriptor>