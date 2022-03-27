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

    def translated(self, dx, dy):
        return Segment([(x + dx, y + dy) for x, y in self.points])


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

    def translated(self, dx, dy):
        return Contour([segment.translated(dx, dy) for segment in self.segments])

    def reversed(self):
        return Contour([seg.reversed() for seg in reversed(self.segments)])

    def splitAtAngle(self, angle):
        assert self.closed
        assert self.segments[0].points[0] == self.segments[-1].points[-1]
        angleX, angleY = cos(angle), sin(angle)
        sides = [[], []]
        previousSide = None
        for segment in self.segments:
            dx1 = segment.points[1][0] - segment.points[0][0]
            dy1 = segment.points[1][1] - segment.points[0][1]
            side1 = whichSide((angleX, angleY), (dx1, dy1)) >= 0
            if len(segment.points) == 4:
                dx2 = segment.points[3][0] - segment.points[2][0]
                dy2 = segment.points[3][1] - segment.points[2][1]
                side2 = whichSide((angleX, angleY), (dx2, dy2)) >= 0
                if side1 == side2:
                    if previousSide != side1:
                        sides[side1].append([])
                    sides[side1][-1].append(segment)
                else:
                    curve1, curve2 = splitCurveAtAngle(segment.points, angle, True)
                    if previousSide != side1:
                        sides[side1].append([])
                    sides[side1][-1].append(Segment(curve1))
                    if curve2 is not None:
                        sides[side2].append([])
                        sides[side2][-1].append(Segment(curve2))
                    else:
                        side2 = side1  # why
                previousSide = side2
            else:
                if previousSide != side1:
                    sides[side1].append([])
                sides[side1][-1].append(segment)
                previousSide = side1
        leftSides, rightSides = sides
        for sides in [leftSides, rightSides]:
            if len(sides) > 1 and _pointsEqual(
                sides[-1][-1].points[-1], sides[0][0].points[0]
            ):
                sides[0] = sides[-1] + sides[0]
                del sides[-1]
        return Path(map(Contour, leftSides)), Path(map(Contour, rightSides))

    def splitAtSharpCorners(self):
        assert not self.closed
        lastDelta = None
        contours = [[]]
        for segment in self.segments:
            d = (
                segment.points[1][0] - segment.points[0][0],
                segment.points[1][1] - segment.points[0][1],
            )
            if (
                lastDelta is not None
                and abs(whichSide(normalize(*lastDelta), normalize(*d))) > 0.1
            ):
                contours.append([])
            contours[-1].append(segment)
            if len(segment.points) == 4:
                d = (
                    segment.points[3][0] - segment.points[2][0],
                    segment.points[3][1] - segment.points[2][1],
                )
            lastDelta = d

        return Path([Contour(segments) for segments in contours])


def _pointsEqual(pt1, pt2):
    x1, y1 = pt1
    x2, y2 = pt2
    return isclose(x1, x2, abs_tol=0.00001) and isclose(y1, y2, abs_tol=0.00001)


@dataclass
class Path:
    contours: List[Contour] = field(default_factory=list)

    def draw(self, pen):
        for contour in self.contours:
            contour.draw(pen)

    def append(self, contour):
        self.contours.append(contour)

    def appendPath(self, path):
        self.contours.extend(path.contours)

    def appendSegment(self, segment):
        self.contours[-1].append(segment)

    def closePath(self):
        firstPoint = self.contours[-1].segments[0].points[0]
        lastPoint = self.contours[-1].segments[-1].points[-1]
        if firstPoint != lastPoint:
            self.appendSegment(Segment([lastPoint, firstPoint]))
        self.contours[-1].closed = True

    def translated(self, dx, dy):
        return Path([contour.translated(dx, dy) for contour in self.contours])

    def splitAtAngle(self, angle):
        leftPath = Path()
        rightPath = Path()
        for contour in self.contours:
            left, right = contour.splitAtAngle(angle)
            leftPath.appendPath(left)
            rightPath.appendPath(right)
        return leftPath, rightPath

    def splitAtSharpCorners(self):
        path = Path()
        for contour in self.contours:
            path.appendPath(contour.splitAtSharpCorners())
        return path


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


def normalize(x, y):
    d = hypot(x, y)
    if abs(d) > 0.00000000001:
        return x / d, y / d
    else:
        return 0, 0


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


if __name__ == "__main__":
    # DrawBot test
    def drawCurve(pt1, pt2, pt3, pt4):
        bez = BezierPath()
        bez.moveTo(pt1)
        bez.curveTo(pt2, pt3, pt4)
        drawPath(bez)

    offset = 156
    angle = radians(-212)

    lineJoin("round")
    lineCap("round")
    stroke(0)
    fill(None)

    if False:
        curve = (100, 100), (160, 300), (300, 300), (400, 100)
        drawCurve(*curve)
        dx = 300 * cos(angle)
        dy = 300 * sin(angle)
        c1, c2 = splitCurveAtAngle(curve, angle, True)

        if c2 is not None:
            strokeWidth(6)
            drawCurve(*c1)
            lineDash(5)
            strokeWidth(2)
            p1x, p1y = c1[-1]
            p2x = p1x + dx
            p2y = p1y + dy
            line((p1x, p1y), (p2x, p2y))

    letterBez = BezierPath()
    letterBez.text("d", font="Helvetica", fontSize=800, offset=(190, 210))
    pen = PathBuilder(None)
    letterBez.drawToPen(pen)
    # drawPath(bez)
    path = pen.path
    with savedState():
        strokeWidth(2)
        left, right = path.splitAtAngle(angle)
        right = right.splitAtSharpCorners()
        dx = offset * cos(angle)
        dy = offset * sin(angle)
        bez = BezierPath()
        left.draw(bez)
        lineDash(5)
        drawPath(bez)
        bez = BezierPath()
        rightOffset = right.translated(-dx, -dy)
        combined = Path()
        for cont1, cont2 in zip(right.contours, rightOffset.contours):
            segments1 = cont1.segments
            segments2 = cont2.reversed().segments
            seg12 = Segment([segments1[-1].points[-1], segments2[0].points[0]])
            seg21 = Segment([segments2[-1].points[-1], segments1[0].points[0]])
            contour = Contour(segments1 + [seg12] + segments2 + [seg21], True)
            combined.append(contour)
        # right =
        combined.draw(bez)
        strokeWidth(6)
        lineDash(None)
        fill(0.5)
        drawPath(bez)

    fill(1, 0.7, 0.4, 0.8)
    stroke(None)
    bez = BezierPath()
    path.draw(bez)
    drawPath(bez)
