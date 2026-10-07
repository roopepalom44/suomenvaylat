<?xml version='1.0' encoding='utf-8'?>
<sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" version="1.0.0"><sld:NamedLayer><sld:Name>tilastointialueet:avi1000k</sld:Name><sld:UserStyle>
            <sld:Name>luokitukset_polygon</sld:Name>
            <sld:Title>luokitukset_polygon</sld:Title>
            <sld:IsDefault>1</sld:IsDefault>
            <sld:Abstract>A sample style that draws a polygon</sld:Abstract>
            <sld:FeatureTypeStyle>
                <sld:Name>name</sld:Name>
                <sld:Rule>
                    <sld:Name>rule1</sld:Name>
                    <sld:Title>Gray Polygon with Black Outline</sld:Title>
                    <sld:Abstract>A polygon with a gray fill and a 0.5 pixel black outline</sld:Abstract>
                    <sld:PolygonSymbolizer>
                        <sld:Fill>
                            <sld:CssParameter name="fill">#FFFFFF</sld:CssParameter>
                            <sld:CssParameter name="fill-opacity">0.1</sld:CssParameter>
                        </sld:Fill>
                        <sld:Stroke>
                            <sld:CssParameter name="stroke-width">0.5</sld:CssParameter>
                        </sld:Stroke>
                    </sld:PolygonSymbolizer>
                </sld:Rule>
            </sld:FeatureTypeStyle>
        </sld:UserStyle>
    </sld:NamedLayer></sld:StyledLayerDescriptor>