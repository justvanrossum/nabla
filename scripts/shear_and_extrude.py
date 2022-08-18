import argparse
from copy import deepcopy
import itertools
import math
import os
import pathlib
from fontTools.designspaceLib import AxisLabelDescriptor, DesignSpaceDocument
from fontTools.misc.transform import Transform
from fontTools.misc.bezierTools import cubicPointAtT
from fontTools.pens.recordingPen import RecordingPen, RecordingPointPen
from fontTools.pens.transformPen import TransformPointPen
from fontTools.ttLib.tables import otTables as ot
from pathops.operations import union
from ufo2ft.constants import COLOR_LAYERS_KEY, COLOR_PALETTES_KEY
import ufoLib2
from path_tools import PathBuilderPen, Contour, extrudePath, sortContours


RANDOM_FALLBACK_GRADIENTS = False
NO_FRONT = False


def parseColorTable(colorTable):
    colorNames = []
    colors = []
    for line in colorTable.splitlines():
        line = line.strip()
        if not line:
            continue
        colorName, *hexColors = line.split()
        colorNames.append(colorName)
        colors.append([colorFromHex(hexColor) for hexColor in hexColors])

    palettes = [list(palette) for palette in zip(*colors)]
    colorIndices = {colorName: i for i, colorName in enumerate(colorNames)}
    return palettes, colorIndices


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


colorTable = """
    primer          ffd214  ff1471  00a0e1  5a5a78  c3c3e1
    shadowBottom    ff552d  780082  2200f5  141432  555573
    shadowMiddle    ff9b00  be14b4  3c6eff  464664  8c8caa
    shadow          ff9123  9b1eaf  325aff  323250  8282a0
    topBottom       ffd214  ff1471  00a0e1  5a5a78  c3c3e1
    midTop          ffeb6e  ff6b8b  1ee1ff  787896  d7d7f5
    frontBottom     ffd214  ff1471  00a0e1  5a5a78  c3c3e1
    frontTop        ffeb6e  ff6b8b  1ee1ff  787896  d7d7f5
    top             fffabe  ff9cc2  87ffff  9696b4  f5f5ff
    highlight       ffffff  ffffff  ffffff  c8c8d2  ffffff
"""

palettes, colorIndices = parseColorTable(colorTable)


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


def buildRandomSideGradientFallback():
    from random import shuffle

    colorNames = ["shadowBottom", "shadowMiddle", "shadow", "top"]
    shuffle(colorNames)

    return buildLinearGradient(
        (0, 0),
        (0, 700),
        (87, -50),
        [
            (0.0, colorIndices[colorNames[0]]),
            (0.65, colorIndices[colorNames[1]]),
            (1.0, colorIndices[colorNames[2]]),
        ],
    )


class DecomposingRecordingPointPen(RecordingPointPen):
    def __init__(self, glyphSet):
        super(DecomposingRecordingPointPen, self).__init__()
        self.glyphSet = glyphSet

    def addComponent(self, glyphName, transformation, identifier=None, **kwargs):
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
    left, right = pen.path.splitAtAngle(angle)
    right.contours = [cont.reverse() for cont in right.contours]
    # left.appendPath(right)  # Add "invisible" sides
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
    transformGlyph(glyph, t.translate(50, -75))
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

        splitPath.contours = sortContours(splitPath.contours, rotateT)
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

        layers = [buildSolidGlyph(glyphName, colorIndices["primer"])] + sideLayers
        if not NO_FRONT:
            layers.append(buildPaintGlyph(frontLayerGlyphName, frontGradient))
        if glyphName in highlightLayer:
            layers.append(
                buildSolidGlyph(highlightLayerGlyphName, colorIndices["highlight"])
            )
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
    gradientContourPoints = [
        [((pt.x, pt.y), pt.name) for pt in cont.points if pt.name]
        for cont in gradientContours
    ]
    gradients = []
    for contour in splitPath.contours:
        avgDistances = []
        for index, points in enumerate(gradientContourPoints):
            if not points:
                continue
            distances = [distancePointToContour(pt, contour) for pt, name in points]
            avgDistances.append((sum(distances) / len(distances), index))
        avgDistances.sort()
        gradientIndex = avgDistances[0][1] if avgDistances else None
        if gradientIndex is not None:
            gradient = makeSideGradient(
                gradientContourPoints[gradientIndex], extrudeSlope
            )
        else:
            gradient = (
                buildRandomSideGradientFallback()
                if RANDOM_FALLBACK_GRADIENTS
                else sideGradientFallback
            )
        gradients.append(gradient)

    return gradients


def distancePointToContour(pt, contour):
    dist = math.inf
    for segment in contour.segments:
        dist = min(dist, distancePointToSegment(pt, segment))
    return dist


def distancePointToSegment(pt, segment):
    if len(segment.points) == 4:
        # Cubic curve, flatten into two line segments. Is good enough.
        mid = cubicPointAtT(*segment.points, 0.5)
        d1 = distancePointToLine(pt, segment.points[0], mid)
        d2 = distancePointToLine(pt, mid, segment.points[-1])
        return min(d1, d2)
    else:
        return distancePointToLine(pt, segment.points[0], segment.points[-1])


def distancePointToLine(pt, pt1, pt2):
    x, y = pt
    x1, y1 = pt1
    x2, y2 = pt2

    # ax + by + c = 0 line equation
    a = y1 - y2
    b = x2 - x1
    c = x1 * y2 - x2 * y1

    det = a**2 + b**2

    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) > abs(dy):
        xp = (b * (b * x - a * y) - a * c) / det
        t = (xp - x1) / dx
    else:
        yp = (a * (-b * x + a * y) - b * c) / det
        t = (yp - y1) / dy

    if t < 0:
        return math.hypot(x - x1, y - y1)
    elif t > 1:
        return math.hypot(x - x2, y - y2)

    return abs(a * x + b * y + c) / math.sqrt(det)


def makeSideGradient(gradientPoints, extrudeSlope):
    colorPoints = []
    for (x, y), colorName in gradientPoints:
        if colorName.endswith("Color"):
            colorName = colorName[:-5]
        if colorName not in colorIndices:
            print(f"*** warning: color '{colorName}' is not defined")
            continue
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


def makeHighlightGlyphs(font, glyphNames, extrudeAngle, highlightWidth):
    dx = highlightWidth * math.cos(extrudeAngle) / 2
    dy = highlightWidth * math.sin(extrudeAngle) / 2
    highlightLayer = font.layers["highlightColor"]
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
            assert contour.segments
            if len(contour.segments) == 1:
                # Split in two
                contour.segments = list(contour.segments[0].splitAtT(0.5))
            convertLineToCurve(contour, 0, 0.5, 0.75)
            convertLineToCurve(contour, -1, 0.25, 0.5)
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


def convertLineToCurve(contour, segmentIndex, t1, t2):
    if len(contour.segments[segmentIndex].points) != 2:
        return
    pt1, pt4 = contour.segments[segmentIndex].points
    pt2 = interpolatePoints(t1, pt1, pt4)
    pt3 = interpolatePoints(t2, pt1, pt4)
    contour.segments[segmentIndex].points = [pt1, pt2, pt3, pt4]


def interpolatePoints(t, pt1, pt2):
    x1, y1 = pt1
    x2, y2 = pt2
    dx = x2 - x1
    dy = y2 - y1
    return (x1 + t * dx, y1 + t * dy)


manualFeatures = """
feature ccmp {
  sub IJ acutecomb by IJacute;
  sub ij acutecomb by ijacute;
  sub J acutecomb by Jacute;
  sub j acutecomb by jacute;
} ccmp;
"""


depthAxisName = "Extrusion Depth"
highlightAxisName = "Edge Highlight"


def setupDesignSpaceDocument():
    doc = DesignSpaceDocument()
    doc.addAxisDescriptor(
        name=depthAxisName,
        tag="EDPT",
        minimum=0,
        default=100,
        maximum=200,
        axisLabels=[
            AxisLabelDescriptor(name="Shallow", userValue=0, elidable=False),
            AxisLabelDescriptor(name="Regular", userValue=100, elidable=False),
            AxisLabelDescriptor(name="Deep", userValue=200, elidable=False),
        ],
    )
    doc.addAxisDescriptor(
        name=highlightAxisName,
        tag="EHLT",
        minimum=0,
        default=12,
        maximum=24,
        axisLabels=[
            AxisLabelDescriptor(name="No Highlight", userValue=0, elidable=False),
            AxisLabelDescriptor(name="Highlight", userValue=12, elidable=True),
            AxisLabelDescriptor(name="Max Highlight", userValue=24, elidable=False),
        ],
    )

    # Add "Regular" named instance at the default location, so that
    # fontbakery can't say we don't have named instances.
    doc.addInstanceDescriptor(styleName=f"Regular", location={})
    return doc


def getAxisFields(axis):
    return [(label.userValue, "".join(label.name.split())) for label in axis.axisLabels]


def shearAndExtrude(path):
    shearAngle = math.radians(30)
    extrudeAngle = math.radians(-30)

    font = ufoLib2.Font.open(path)
    decomposeAndRemoveOverlaps(font)

    for glyphName in list(font.keys()):
        if glyphName.startswith("_"):
            del font[glyphName]

    glyphNames = sorted(font.keys())
    for layer in font.layers:
        for glyphName in glyphNames:
            if glyphName in layer:
                shearGlyph(layer[glyphName], shearAngle)

    doc = setupDesignSpaceDocument()
    axesByTag = {axis.tag: axis for axis in doc.axes}
    depthAxisFields = getAxisFields(axesByTag["EDPT"])
    highlightAxisFields = getAxisFields(axesByTag["EHLT"])

    for depth, depthName in depthAxisFields:
        extrudedFont = deepcopy(font)
        extrudedFont.info.styleName = depthName
        colorGlyphs = extrudeGlyphs(extrudedFont, glyphNames, extrudeAngle, depth)

        if depthName == "Regular":
            makeHighlightGlyphs(
                extrudedFont, glyphNames, extrudeAngle, axesByTag["EHLT"].default
            )
            extrudedFont.lib[COLOR_PALETTES_KEY] = palettes
            extrudedFont.lib[COLOR_LAYERS_KEY] = colorGlyphs
            extrudedFont.features.text += manualFeatures

        extrudedPath = path.parent / (path.stem + "-" + depthName + path.suffix)
        extrudedFont.save(extrudedPath, overwrite=True)
        doc.addSourceDescriptor(
            familyName="Nabla",
            path=os.fspath(extrudedPath),
            location={depthAxisName: depth},
        )

    for highlightWidth, highlightName in highlightAxisFields:
        if highlightName == "Highlight":
            continue
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
            path=os.fspath(highlightPath), location={highlightAxisName: highlightWidth}
        )

    dsPath = path.parent / (path.stem + ".designspace")
    doc.write(dsPath)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_ufo")
    parser.add_argument(
        "--random-fallback-gradients", action="store_true", default=False
    )
    parser.add_argument("--no-front", action="store_true", default=False)
    args = parser.parse_args()
    RANDOM_FALLBACK_GRADIENTS = args.random_fallback_gradients
    NO_FRONT = args.no_front
    shearAndExtrude(pathlib.Path(args.source_ufo).resolve())
