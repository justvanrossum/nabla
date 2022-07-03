from copy import deepcopy
import math
import os
import pathlib
import sys
from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.misc.arrayTools import rectArea, sectRect
from fontTools.misc.transform import Transform
from fontTools.pens.basePen import DecomposingPen
from fontTools.pens.recordingPen import RecordingPen, RecordingPointPen
from fontTools.pens.transformPen import TransformPointPen
from fontTools.ttLib.tables import otTables as ot
from pathops.operations import union
from ufo2ft.constants import COLOR_LAYERS_KEY, COLOR_PALETTES_KEY
import ufoLib2
from path_tools import PathBuilderPen, Contour, extrudePath


def colorFromHex(hexString):
    assert len(hexString) in [6, 8]
    channels = []
    for i in range(0, len(hexString), 2):
        channels.append(int(hexString[i : i + 2], 16) / 255)
    if len(channels) == 3:
        channels.append(1)
    return channels


def buildPaintGlyph(sourceGlyphName, paint):
    return {
        "Format": ot.PaintFormat.PaintGlyph,
        "Paint": paint,
        "Glyph": sourceGlyphName,
    }


def buildPaintColrGlyph(sourceGlyphName):
    return {
        "Format": ot.PaintFormat.PaintColrGlyph,
        "Glyph": sourceGlyphName,
    }


def buildSolidGlyph(sourceGlyphName, colorIndex):
    paint = {
        "Format": ot.PaintFormat.PaintSolid,
        "PaletteIndex": colorIndex,
        "Alpha": 1.0,
    }
    return buildPaintGlyph(sourceGlyphName, paint)


def buildPaintLayers(layers):
    if len(layers) == 1:
        return layers[0]
    return (ot.PaintFormat.PaintColrLayers, layers)


def buildLinearGradient(pt0, pt1, pt2, colorLine, extend="pad"):
    return {
        "Format": ot.PaintFormat.PaintLinearGradient,
        "ColorLine": {
            "ColorStop": colorLine,
            "Extend": extend,  # pad, repeat, reflect
        },
        "x0": pt0[0],
        "y0": pt0[1],
        "x1": pt1[0],
        "y1": pt1[1],
        "x2": pt2[0],
        "y2": pt2[1],
    }


frontSuffix = ".front"
sideSuffix = ".side"
highlightSuffix = ".highlight"


mainColors = {
    "primer": colorFromHex("ffd214"),
    "shadowBottom": colorFromHex("f5462d"),
    "shadowMiddle": colorFromHex("fd943b"),
    "shadow": colorFromHex("ff8723"),
    "frontBottom": colorFromHex("ffd214"),
    "frontTop": colorFromHex("ffeb6e"),
    "top": colorFromHex("ffed9f"),
    "highlight": colorFromHex("ffffff"),
}


colorIndices = {
    colorName: colorIndex for colorIndex, colorName in enumerate(mainColors)
}


frontGradient = buildLinearGradient(
    (0, -100),
    (0, 500),
    (87, -50),
    [(0.0, colorIndices["frontBottom"]), (1.0, colorIndices["frontTop"])],
)


sideGradientFallback = buildLinearGradient(
    (0, 0),
    (0, 700),
    (87, -50),
    [
        (0.0, colorIndices["shadowBottom"]),
        (0.65, colorIndices["shadow"]),
        (1.0, colorIndices["top"]),
    ],
)


class DecomposingRecordingPointPen(RecordingPointPen):
    def __init__(self, glyphSet):
        super(DecomposingRecordingPointPen, self).__init__()
        self.glyphSet = glyphSet

    def addComponent(self, glyphName, transformation):
        glyph = self.glyphSet[glyphName]
        tPen = TransformPointPen(self, transformation)
        glyph.drawPoints(tPen)


def decomposeComponents(glyph, font):
    recPen = DecomposingRecordingPointPen(font)
    glyph.drawPoints(recPen)
    glyph.clear()
    recPen.replay(glyph.getPointPen())


def removeOverlaps(glyph):
    recPen = RecordingPen()
    union(glyph.contours, recPen)
    glyph.clear()
    recPen.replay(glyph.getPen())


def transformGlyph(glyph, transformation):
    recPen = RecordingPointPen()
    tPen = TransformPointPen(recPen, transformation)
    glyph.drawPoints(tPen)
    glyph.clear()
    recPen.replay(glyph.getPointPen())


def splitGlyphAtAngle(glyph, angle):
    pen = PathBuilderPen(None)
    glyph.draw(pen)
    left, _ = pen.path.splitAtAngle(angle)
    left = left.splitAtSharpCorners()
    return left


def buildFeatures(glyphNames, featureSpec):
    features = []
    fea = features.append
    fea("")
    for featureTag, glyphSuffix, featureDesc in featureSpec:
        plainGlyphs = [
            gn[: -len(glyphSuffix)] for gn in glyphNames if gn.endswith(glyphSuffix)
        ]
        fea(f"@glyphs_{featureTag}_plain = [{' '.join(plainGlyphs)}];")
        fea(
            f"@glyphs_{featureTag} = [{' '.join(gn + glyphSuffix for gn in plainGlyphs)}];"
        )
    fea("")
    for featureTag, glyphSuffix, featureDesc in featureSpec:
        fea(f"feature {featureTag} {{")
        fea("    featureNames {")
        fea(f'      name "{featureDesc}";')
        fea("    };")
        fea(f"  sub @glyphs_{featureTag}_plain by @glyphs_{featureTag};")
        fea(f"}} {featureTag};")
    fea("")
    return "\n".join(features)


def decomposeAndRemoveOverlaps(font):
    for glyph in font:
        decomposeComponents(glyph, font)
        removeOverlaps(glyph)


def shearGlyph(glyph, shearAngle):
    pivotX = 100  # glyph.width / 2
    t = Transform()
    t = t.translate(pivotX, 0)
    t = t.skew(0, shearAngle)
    t = t.scale(math.cos(shearAngle), 1)
    t = t.translate(-pivotX, 0)
    transformGlyph(glyph, t)
    lsb, _ = t.transformPoint((0, 0))
    rsb, _ = t.transformPoint((glyph.width, 0))
    glyph.move((-lsb, 0))
    glyph.width = rsb - lsb


def extrudeGlyphs(font, glyphNames, extrudeAngle, depth):
    rotateT = Transform().rotate(-extrudeAngle)
    extrudeSlope = math.tan(extrudeAngle)
    highlightLayer = font.layers["highlightColor"]
    gradientLayers = [font.layers["top"], font.layers["side"]]
    colorGlyphs = {}

    for glyphName in glyphNames:
        frontLayerGlyphName = glyphName + frontSuffix
        sideLayerGlyphName = glyphName + sideSuffix
        highlightLayerGlyphName = glyphName + highlightSuffix

        glyph = font[glyphName]
        sideGlyph = font.newGlyph(sideLayerGlyphName)
        sideGlyph.width = glyph.width
        sideGlyphPen = sideGlyph.getPen()
        sideLayers = []
        splitPath = splitGlyphAtAngle(glyph, extrudeAngle)

        def contourSortFunc(contour):
            return ContourSortHelper(contour.transform(rotateT))

        splitPath.contours.sort(key=contourSortFunc, reverse=True)
        sideGradients = makeSideGradients(
            splitPath, gradientLayers, glyphName, extrudeSlope
        )
        extrudedPath = extrudePath(splitPath, extrudeAngle, -depth, reverse=True)

        for contourIndex, (contour, sideGradient) in enumerate(
            zip(extrudedPath.contours, sideGradients)
        ):
            sidePartGlyphName = sideLayerGlyphName + f".{contourIndex}"
            sideGlyphPen.addComponent(sidePartGlyphName, (1, 0, 0, 1, 0, 0))
            sidePartGlyph = font.newGlyph(sidePartGlyphName)
            sidePartGlyph.width = glyph.width
            contour.draw(sidePartGlyph.getPen())
            sideLayers.append(buildPaintGlyph(sidePartGlyphName, sideGradient))

        colorGlyphs[sideLayerGlyphName] = buildPaintLayers(sideLayers)

        colorGlyphs[frontLayerGlyphName] = buildPaintGlyph(
            frontLayerGlyphName, frontGradient
        )

        layerGlyphNames = [sideLayerGlyphName, frontLayerGlyphName]
        if glyphName in highlightLayer:
            layerGlyphNames.append(highlightLayerGlyphName)
        layers = [
            buildSolidGlyph(glyphName, colorIndices["primer"]),
            *(buildPaintColrGlyph(gn) for gn in layerGlyphNames),
        ]
        colorGlyphs[glyphName] = buildPaintLayers(layers)

        font[frontLayerGlyphName] = glyph.copy()
        font[frontLayerGlyphName].unicode = None
        glyph.clear()
        pen = glyph.getPen()
        pen.addComponent(frontLayerGlyphName, (1, 0, 0, 1, 0, 0))
        pen.addComponent(sideLayerGlyphName, (1, 0, 0, 1, 0, 0))

    return colorGlyphs


def makeSideGradients(splitPath, gradientLayers, glyphName, extrudeSlope):
    gradientGlyphs = [gl[glyphName] for gl in gradientLayers if glyphName in gl]
    gradientContours = [cont for g in gradientGlyphs for cont in g.contours]
    gradientBounds = [cont.getControlBounds() for cont in gradientContours]
    gradients = []
    for contour in splitPath.contours:
        contourBox = contour.controlBounds
        boxOverlaps = []
        for index, gb in enumerate(gradientBounds):
            doesOverlap, obox = sectRect(contourBox, gb)
            area = rectArea(obox) if doesOverlap else 0
            boxOverlaps.append((area, index))
        boxOverlaps.sort(reverse=True)
        if boxOverlaps and boxOverlaps[0][0] > 0:
            gradient = makeSideGradient(
                gradientContours[boxOverlaps[0][1]], extrudeSlope
            )
        else:
            gradient = sideGradientFallback
        gradients.append(gradient)

    return gradients


def makeSideGradient(gradientContour, extrudeSlope):
    colorPoints = []
    for point in gradientContour.points:
        colorName = point.name
        if not colorName:
            continue
        if colorName.endswith("Color"):
            colorName = colorName[:-5]
        if colorName not in colorIndices:
            print(f"*** warning: color '{colorName}' is not defined")
            continue
        x = point.x
        y = point.y
        y -= x * extrudeSlope
        colorPoints.append((y, colorName))
    colorPoints.sort()
    y0 = colorPoints[0][0]
    y1 = colorPoints[-1][0]
    x2 = 100
    y2 = y0 + x2 * extrudeSlope
    extent = y1 - y0
    if not extent:
        extent = 1
    colorLine = [
        ((y - y0) / extent, colorIndices[colorName]) for y, colorName in colorPoints
    ]
    return buildLinearGradient((0, y0), (0, y1), (x2, y2), colorLine)


class ContourSortHelper:
    def __init__(self, contour):
        self.contour = contour

    def __lt__(self, other):
        return self.contour.controlBounds[0] < other.contour.controlBounds[0]


def makeHighlightGlyphs(font, glyphNames, extrudeAngle, highlightWidth):
    dx = highlightWidth * math.cos(extrudeAngle)
    dy = highlightWidth * math.sin(extrudeAngle)
    highlightLayer = font.layers["highlightColor"]
    colorGlyphs = {}
    for glyphName in glyphNames:
        if glyphName not in highlightLayer:
            continue
        highlightLayerGlyphName = glyphName + highlightSuffix
        highlightGlyph = font.newGlyph(highlightLayerGlyphName)
        highlightGlyph.width = font[glyphName].width
        highlightGlyphPen = highlightGlyph.getPen()
        sourceGlyph = highlightLayer[glyphName]
        pbp = PathBuilderPen(highlightLayer)
        sourceGlyph.draw(pbp)
        highlightPath = pbp.path
        for contourIndex, contour in enumerate(highlightPath.contours):
            if len(contour.segments) < 2:
                print(
                    f"Skipping highlightColor contour {contourIndex} of {glyphName}: it only has a single segment"
                )
                continue
            numSegments = len(contour.segments)
            firstPoint = contour.segments[0].points[0]
            lastPoint = contour.segments[-1].points[-1]
            leftSegments = contour.translate(dx, dy).segments
            leftSegments[0].points[0] = firstPoint
            leftSegments[-1].points[-1] = lastPoint
            rightSegments = contour.translate(-dx, -dy).reverse().segments
            rightSegments[0].points[0] = lastPoint
            rightSegments[-1].points[-1] = firstPoint
            highlightPath = Contour(leftSegments + rightSegments, closed=True)
            highlightPath.draw(highlightGlyphPen)

        colorGlyphs[highlightLayerGlyphName] = buildSolidGlyph(
            highlightLayerGlyphName, colorIndices["highlight"]
        )

    return colorGlyphs


def shearAndExtrude(path):
    palettes = [list(mainColors.values())]

    shearAngle = math.radians(30)
    extrudeAngle = math.radians(-30)

    font = ufoLib2.Font.open(path)
    decomposeAndRemoveOverlaps(font)

    glyphNames = [glyphName for glyphName in font.keys() if glyphName[0] not in "._"]
    glyphNames.sort()
    for layer in font.layers:
        for glyphName in glyphNames:
            if glyphName in layer:
                shearGlyph(layer[glyphName], shearAngle)

    doc = DesignSpaceDocument()
    doc.addAxisDescriptor(
        name="Weight", tag="wght", minimum=100, default=400, maximum=700
    )
    doc.addAxisDescriptor(
        name="Highlight", tag="HLGT", minimum=0, default=5, maximum=10
    )

    depthAxisFields = [(100, 400, "Normal"), (200, 700, "Deep"), (0, 100, "Shallow")]
    highlightAxisFields = [(0, 0, "NoHighlight"), (10, 10, "MaxHighlight")]

    for depth, axisValue, depthName in depthAxisFields:
        extrudedFont = deepcopy(font)
        extrudedFont.info.styleName = depthName
        colorGlyphs = extrudeGlyphs(extrudedFont, glyphNames, extrudeAngle, depth)

        if depthName == "Normal":
            colorGlyphs.update(
                makeHighlightGlyphs(extrudedFont, glyphNames, extrudeAngle, 6)
            )
            extrudedFont.lib[COLOR_PALETTES_KEY] = palettes
            extrudedFont.lib[COLOR_LAYERS_KEY] = colorGlyphs
            extrudedFont.features.text += buildFeatures(
                sorted(extrudedFont.keys()),
                [
                    ("ss01", frontSuffix, "Front"),
                    ("ss02", sideSuffix, "Side"),
                    ("ss03", highlightSuffix, "Highlight"),
                ],
            )

        extrudedPath = path.parent / (path.stem + "-" + depthName + path.suffix)
        extrudedFont.save(extrudedPath, overwrite=True)
        doc.addSourceDescriptor(
            path=os.fspath(extrudedPath), location={"Weight": axisValue}
        )

    for highlightWidth, axisValue, highlightName in highlightAxisFields:
        highlightFont = deepcopy(font)
        highlightFont.info.styleName = highlightName
        makeHighlightGlyphs(highlightFont, glyphNames, extrudeAngle, highlightWidth)
        for glyphName in list(highlightFont.keys()):
            if not glyphName.endswith(highlightSuffix):
                for layer in highlightFont.layers:
                    if glyphName in layer:
                        del layer[glyphName]

        highlightPath = path.parent / (path.stem + "-" + highlightName + path.suffix)
        highlightFont.save(highlightPath, overwrite=True)
        doc.addSourceDescriptor(
            path=os.fspath(highlightPath), location={"Highlight": axisValue}
        )

    dsPath = path.parent / (path.stem + ".designspace")
    doc.write(dsPath)


if __name__ == "__main__":
    shearAndExtrude(pathlib.Path(sys.argv[1]).resolve())
