from copy import deepcopy
import math
import os
import pathlib
import sys
from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.misc.transform import Transform
from fontTools.pens.basePen import DecomposingPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
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


frontGradient = {
    "Format": ot.PaintFormat.PaintLinearGradient,
    "ColorLine": {
        "ColorStop": [
            (0.0, colorIndices["frontBottom"]),
            (1.0, colorIndices["frontTop"]),
        ],
        "Extend": "pad",  # pad, repeat, reflect
    },
    "x0": 0,
    "y0": -100,
    "x1": 0,
    "y1": 500,
    "x2": 87,
    "y2": -50,
}


sideGradient = {
    "Format": ot.PaintFormat.PaintLinearGradient,
    "ColorLine": {
        "ColorStop": [
            (0.0, colorIndices["shadowBottom"]),
            (0.65, colorIndices["shadow"]),
            (1.0, colorIndices["top"]),
        ],
        "Extend": "pad",  # pad, repeat, reflect
    },
    "x0": 0,
    "y0": 0,
    "x1": 0,
    "y1": 700,
    "x2": -87,
    "y2": 50,
}


class DecomposingRecordingPen(DecomposingPen, RecordingPen):
    pass


def decomposeComponents(glyph, font):
    recPen = DecomposingRecordingPen(font)
    glyph.draw(recPen)
    glyph.clear()
    recPen.replay(glyph.getPen())


def removeOverlaps(glyph):
    recPen = RecordingPen()
    union(glyph.contours, recPen)
    glyph.clear()
    recPen.replay(glyph.getPen())


def transformGlyph(glyph, transformation):
    recPen = RecordingPen()
    tPen = TransformPen(recPen, transformation)
    glyph.draw(tPen)
    glyph.clear()
    recPen.replay(glyph.getPen())


def extrudeGlyph(glyph, angle, offset):
    pen = PathBuilderPen(None)
    glyph.draw(pen)
    left, _ = pen.path.splitAtAngle(angle)
    left = left.splitAtSharpCorners()
    return extrudePath(left, angle, offset, reverse=True)


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
    highlightLayer = font.layers["highlightColor"]
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
        extrudedPath = extrudeGlyph(glyph, extrudeAngle, -depth)
        for contourIndex, contour in enumerate(extrudedPath.contours):
            sidePartGlyphName = sideLayerGlyphName + f".{contourIndex}"
            sideGlyphPen.addComponent(sidePartGlyphName, (1, 0, 0, 1, 0, 0))
            sidePartGlyph = font.newGlyph(sidePartGlyphName)
            sidePartGlyph.width = glyph.width
            contour.draw(sidePartGlyph.getPen())
            sideLayers.append(buildPaintGlyph(sidePartGlyphName, sideGradient))

        def contourSortFunc(contourIndex):
            contour = extrudedPath.contours[contourIndex]
            return -contour.transform(rotateT).controlBounds[0]

        sideIndices = sorted(range(len(sideLayers)), key=contourSortFunc)
        sideLayers = [sideLayers[i] for i in sideIndices]
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
