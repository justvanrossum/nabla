from dataclasses import dataclass, field
from typing import List, Tuple, Union
from fontTools.misc.transform import Transform
from fontTools.misc.bezierTools import (
    calcCubicParameters,
    solveQuadratic,
    splitCubicAtT,
)
from fontTools.pens.basePen import BasePen


@dataclass
class Segment:
    points: List[Tuple[float, float]]

    def reversed(self):
        return Segment(list(reversed(self.points)))


@dataclass
class Contour:
    segments: List[Segment] = field(default_factory=list)
    closed: bool = False

    def draw(self, pen):
        pen.moveTo(self.segments[0].points[0])
        for segment in self.segments:
            points = segment.points[1:]
            if len(points) == 1:
                pen.lineTo(points[0])
            else:
                assert len(points) == 3
                pen.curveTo(*points)
        if self.closed:
            pen.closePath()

    def append(self, segment):
        self.segments.append(segment)

    def reversed(self):
        return [seg.reversed() for seg in reversed(self.segments)]


@dataclass
class Path:
    contours: List[Contour] = field(default_factory=list)

    def draw(self, pen):
        for contour in self.contours:
            contour.draw(pen)

    def append(self, contour):
        self.contours.append(contour)

    def appendSegment(self, segment):
        self.contours[-1].append(segment)

    def closePath(self):
        firstPoint = self.contours[-1].segments[0].points[0]
        lastPoint = self.contours[-1].segments[-1].points[-1]
        if firstPoint != lastPoint:
            self.appendSegment(Segment([lastPoint, firstPoint]))
        self.contours[-1].closed = True


class PathBuilder(BasePen):
    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.path = Path()
        self.currentPoint = None

    def _moveTo(self, pt):
        self.currentPoint = pt
        self.path.append(Contour())

    def _lineTo(self, pt):
        self.path.appendSegment(Segment([self.currentPoint, pt]))
        self.currentPoint = pt

    def _curveToOne(self, pt2, pt3, pt4):
        self.path.appendSegment(Segment([self.currentPoint, pt2, pt3, pt4]))
        self.currentPoint = pt4

    def _closePath(self):
        self.path.closePath()


def splitCurveAtAngle(curve, angle, bothDirections=False):
    t = Transform().rotate(-angle)
    pt1, pt2, pt3, pt4 = t.transformPoints(curve)
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = calcCubicParameters(pt1, pt2, pt3, pt4)
    # calc first derivative
    ax3 = ax * 3.0
    bx2 = bx * 2.0
    ay3 = ay * 3.0
    by2 = by * 2.0

    yRoots = [t for t in solveQuadratic(ay3, by2, cy) if 0 <= t < 1]

    if not yRoots:
        return curve, None
    elif len(yRoots) == 1:
        t = yRoots[0]
        if bothDirections or (ax3 * t ** 2 + bx2 * t + cx) > 0:
            return splitCubicAtT(*curve, t)
        else:
            return curve, None
    else:
        assert False, "curve too complex"  # a.k.a. I'm too lazy to implement


def whichSide(v1, v2):
    x1, y1 = v1
    x2, y2 = v2
    return x1 * y2 - y1 * x2


if __name__ == "__main__":
    # DrawBot test
    def drawCurve(pt1, pt2, pt3, pt4):
        bez = BezierPath()
        bez.moveTo(pt1)
        bez.curveTo(pt2, pt3, pt4)
        drawPath(bez)

    curve = (100, 100), (160, 300), (300, 300), (400, 100)

    stroke(0)
    fill(None)
    drawCurve(*curve)

    angle = radians(10)

    dx = 300 * cos(angle)
    dy = 300 * sin(angle)
    c1, c2 = splitCurveAtAngle(curve, angle, True)
    print(c1)
    print(c2)

    if c2 is not None:
        strokeWidth(6)
        drawCurve(*c1)
        lineDash(5)
        strokeWidth(2)
        p1x, p1y = c1[-1]
        p2x = p1x + dx
        p2y = p1y + dy
        line((p1x, p1y), (p2x, p2y))

    print(whichSide((0, -100), (-1, -100)))
    bez = BezierPath()
    bez.text("B", font="Helvetica", fontSize=800, offset=(100, 100))
    pen = PathBuilder(None)
    bez.drawToPen(pen)
    drawPath(bez)
    print(pen.path)
    bez = BezierPath()
    pen.path.draw(bez)
    translate(30, 30)
    drawPath(bez)
