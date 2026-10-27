<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
    <sld:NamedLayer>
        <sld:Name>tiestotiedot:nopeusrajoitukset</sld:Name>
        <sld:UserStyle>
            <sld:Name>Nopeusrajoitukset_Velho</sld:Name>
            <sld:IsDefault>1</sld:IsDefault>
            <sld:FeatureTypeStyle>
                <sld:Name>name</sld:Name>
                <sld:Rule>
                    <sld:Name>120 km/h</sld:Name>
                    <sld:Title>120 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>120</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5.5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>100 km/h</sld:Name>
                    <sld:Title>100 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>100</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>90 km/h</sld:Name>
                    <sld:Title>90 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>90</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>80 km/h</sld:Name>
                    <sld:Title>80 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>80</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>70 km/h</sld:Name>
                    <sld:Title>70 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>70</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>60 km/h</sld:Name>
                    <sld:Title>60 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>60</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>50 km/h</sld:Name>
                    <sld:Title>50 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>50</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>40 km/h</sld:Name>
                    <sld:Title>40 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>40</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>30 km/h</sld:Name>
                    <sld:Title>30 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>30</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>20 km/h</sld:Name>
                    <sld:Title>20 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>20</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsLessThanOrEqualTo>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsLessThanOrEqualTo>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>120 km/h</sld:Name>
                    <sld:Title>120 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>120</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5.5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>100 km/h</sld:Name>
                    <sld:Title>100 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>100</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>90 km/h</sld:Name>
                    <sld:Title>90 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>90</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>80 km/h</sld:Name>
                    <sld:Title>80 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>80</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>70 km/h</sld:Name>
                    <sld:Title>70 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>70</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ffffff</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">square</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">2</sld:CssParameter>
                            <sld:CssParameter name="stroke-dasharray">5.0 10.0</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>60 km/h</sld:Name>
                    <sld:Title>60 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>60</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>50 km/h</sld:Name>
                    <sld:Title>50 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>50</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>40 km/h</sld:Name>
                    <sld:Title>40 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>40</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>30 km/h</sld:Name>
                    <sld:Title>30 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>30</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <sld:Name>20 km/h</sld:Name>
                    <sld:Title>20 km/h</sld:Title>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:And>
                                <ogc:PropertyIsEqualTo>
                                    <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                    <ogc:Literal>20</ogc:Literal>
                                </ogc:PropertyIsEqualTo>
                                <ogc:PropertyIsGreaterThan>
                                    <ogc:PropertyName>alkusijainti_tie</ogc:PropertyName>
                                    <ogc:Literal>99</ogc:Literal>
                                </ogc:PropertyIsGreaterThan>
                            </ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:LineSymbolizer>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                            <sld:CssParameter name="stroke-linecap">round</sld:CssParameter>
                            <sld:CssParameter name="stroke-linejoin">bevel</sld:CssParameter>
                            <sld:CssParameter name="stroke-opacity">0.85</sld:CssParameter>
                            <sld:CssParameter name="stroke-width">3</sld:CssParameter>
                        </sld:Stroke>
                    </sld:LineSymbolizer>
                    <sld:VendorOption name="inclusion">mapOnly</sld:VendorOption>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>120</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#ff66cc</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>100</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#cc3311</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>90</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#ff9900</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>80</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#009988</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>70</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#33bbee</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>60</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#ff66cc</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#ff66cc</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>50</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#cc3311</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#cc3311</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>40</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#ff9900</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#ff9900</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>30</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#009988</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#009988</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
                <sld:Rule>
                    <ogc:Filter>
                        <ogc:And>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                                <ogc:Literal>20</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                            <ogc:PropertyIsEqualTo>
                                <ogc:PropertyName>tyyppi</ogc:PropertyName>
                                <ogc:Literal>Nopeusrajoitus</ogc:Literal>
                            </ogc:PropertyIsEqualTo>
                        </ogc:And>
                    </ogc:Filter>
                    <sld:MaxScaleDenominator>362834.0</sld:MaxScaleDenominator>
                    <sld:TextSymbolizer>
                        <sld:Label>
                            <ogc:PropertyName>nopeusrajoitus</ogc:PropertyName>
                        </sld:Label>
                        <sld:Font>
                            <sld:CssParameter name="font-family">Serif</sld:CssParameter>
                            <sld:CssParameter name="font-size">10</sld:CssParameter>
                            <sld:CssParameter name="font-style">normal</sld:CssParameter>
                            <sld:CssParameter name="font-weight">bold</sld:CssParameter>
                        </sld:Font>
                        <sld:LabelPlacement>
                            <sld:PointPlacement>
                                <sld:AnchorPoint>
                                    <sld:AnchorPointX>0.0</sld:AnchorPointX>
                                    <sld:AnchorPointY>0.5</sld:AnchorPointY>
                                </sld:AnchorPoint>
                            </sld:PointPlacement>
                        </sld:LabelPlacement>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#33bbee</sld:CssParameter>
                        </sld:Fill>
                        <sld:Graphic>
                            <sld:Mark>
                                <sld:WellKnownName>circle</sld:WellKnownName>
                                <sld:Fill>
                                    <sld:CssParameter name="fill">#ffffff</sld:CssParameter>
                                </sld:Fill>
                                <sld:Stroke>
                                    <sld:CssParameter name="stroke">#33bbee</sld:CssParameter>
                                </sld:Stroke>
                            </sld:Mark>
                        </sld:Graphic>
                        <sld:VendorOption name="group">yes</sld:VendorOption>
                        <sld:VendorOption name="graphic-resize">proportional</sld:VendorOption>
                        <sld:VendorOption name="graphic-margin">4</sld:VendorOption>
                    </sld:TextSymbolizer>
                </sld:Rule>
            </sld:FeatureTypeStyle>
        </sld:UserStyle>
    </sld:NamedLayer>
</sld:StyledLayerDescriptor>

