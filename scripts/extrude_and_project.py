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
from pathops.operations import union
import ufoLib2
from path_tools import PathBuilderPen


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
    extruded = pen.path.extrude(angle, offset, reverse=True)
    extruded.draw(glyph.getPen())


def extrudeAndProject(path):
    angle = math.radians(30)
    extrudeAngle = math.radians(-30)

    font = ufoLib2.Font.open(path)

    for glyphName in font.keys():
        glyph = font[glyphName]
        decomposeComponents(glyph, font)
        removeOverlaps(glyph)

    glyphNames = [glyphName for glyphName in font.keys() if glyphName[0] not in "._"]
    for glyphName in glyphNames:
        glyph = font[glyphName]
        t = Transform()
        t = t.translate(glyph.width / 2, 0)
        t = t.scale(math.cos(angle), 1)
        t = t.skew(0, angle)
        t = t.translate(-glyph.width / 2, 0)
        transformGlyph(glyph, t)

    doc = DesignSpaceDocument()
    doc.addAxisDescriptor(name="Depth", tag="DPTH", minimum=0, default=100, maximum=200)

    for depth, depthName in [(100, "Normal"), (200, "Deep"), (0, "Shallow")]:
        extrudedFont = deepcopy(font)

        for glyphName in glyphNames:
            glyph = extrudedFont[glyphName]
            extrudeGlyph(glyph, extrudeAngle, -depth)
            lsb, _ = t.transformPoint((0, 0))
            rsb, _ = t.transformPoint((glyph.width, 0))
            glyph.move((-lsb, 0))
            glyph.width = rsb - lsb

        extrudedPath = path.parent / (path.stem + "-" + depthName + path.suffix)
        extrudedFont.save(extrudedPath, overwrite=True)
        doc.addSourceDescriptor(path=os.fspath(extrudedPath), location={"Depth": depth})

    dsPath = path.parent / (path.stem + ".designspace")
    doc.write(dsPath)


if __name__ == "__main__":
    extrudeAndProject(pathlib.Path(sys.argv[1]).resolve())
