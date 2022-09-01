from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property, reduce
import math
from typing import List, Tuple, NamedTuple
from fontTools.misc.arrayTools import calcBounds
from fontTools.misc.transform import Transform
from fontTools.misc.bezierTools import (
    calcCubicParameters,
    solveQuadratic,
    splitCubicAtT,
)
from fontTools.pens.basePen import BasePen


class BoundingBox(NamedTuple):
    """Represents a bounding box as a tuple of (xMin, yMin, xMax, yMax)."""

    xMin: float
    yMin: float
    xMax: float
    yMax: float


@dataclass
class Segment:
    points: List[Tuple[float, float]]

    def reverse(self):
        return Segment(list(reversed(self.points)))

    def translate(self, dx, dy):
        return Segment([(x + dx, y + dy) for x, y in self.points])

    def transform(self, t):
        return Segment(t.transformPoints(self.points))

    def splitAtT(self, t):
        if len(self.points) == 2:
            (x1, y1), (x2, y2) = self.points
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            return Segment([(x1, y1), (x, y)]), Segment([(x, y), (x2, y2)])
        else:
            assert len(self.points) == 4
            points1, points2 = splitCubicAtT(*self.points, t)
            return Segment(points1), Segment(points2)

    @cached_property
    def controlBounds(self):
        return BoundingBox(*calcBounds(self.points))


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
        else:
            pen.endPath()

    def append(self, segment):
        self.segments.append(segment)

    def closePath(self):
        firstPoint = self.segments[0].points[0]
        lastPoint = self.segments[-1].points[-1]
        if firstPoint != lastPoint:
            self.append(Segment([lastPoint, firstPoint]))
        self.closed = True

    def translate(self, dx, dy):
        return Contour(
            [segment.translate(dx, dy) for segment in self.segments], self.closed
        )

    def transform(self, t):
        return Contour([segment.transform(t) for segment in self.segments], self.closed)

    def reverse(self):
        return Contour([seg.reverse() for seg in reversed(self.segments)], self.closed)

    def splitAtAngle(self, angle):
        assert self.closed
        assert self.segments[0].points[0] == self.segments[-1].points[-1]
        angleX, angleY = math.cos(angle), math.sin(angle)
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

    @cached_property
    def controlBounds(self):
        points = list(pt for seg in self.segments for pt in seg.points)
        if points:
            return BoundingBox(*calcBounds(points))
        return None  # empty path


def _pointsEqual(pt1, pt2):
    x1, y1 = pt1
    x2, y2 = pt2
    return math.isclose(x1, x2, abs_tol=0.00001) and math.isclose(
        y1, y2, abs_tol=0.00001
    )


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
        self.contours[-1].closePath()

    def translate(self, dx, dy):
        return Path([contour.translate(dx, dy) for contour in self.contours])

    def transform(self, t):
        return Path([contour.transform(t) for contour in self.contours])

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

    @cached_property
    def controlBounds(self):
        points = list(
            pt for cont in self.contours for seg in cont.segments for pt in seg.points
        )
        if points:
            return BoundingBox(*calcBounds(points))
        return None  # empty path


def extrudePath(path, angle, depth, reverse=False):
    dx = depth * math.cos(angle)
    dy = depth * math.sin(angle)

    pathOffset = path.translate(dx, dy)
    extruded = Path()
    for cont1, cont2 in zip(path.contours, pathOffset.contours):
        segments1 = cont1.segments
        segments2 = cont2.reverse().segments
        seg12 = Segment([segments1[-1].points[-1], segments2[0].points[0]])
        seg21 = Segment([segments2[-1].points[-1], segments1[0].points[0]])
        contour = Contour(segments1 + [seg12] + segments2 + [seg21], True)
        extruded.append(contour.reverse() if reverse else contour)
    return extruded


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
        if bothDirections or (ax3 * t**2 + bx2 * t + cx) > 0:
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
    d = math.hypot(x, y)
    if abs(d) > 0.00000000001:
        return x / d, y / d
    else:
        return 0, 0


class PathBuilderPen(BasePen):
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


def sortContours(contours, transform):
    if not contours:
        return contours
    contoursTransformed = [cont.transform(transform) for cont in contours]
    indices = set(range(len(contours)))
    comparisons = []
    for i, cont1 in enumerate(contoursTransformed):
        for j, cont2 in enumerate(contoursTransformed[i + 1 :], i + 1):
            comparisons.append((i, j, horizontalOrderContour(cont1, cont2)))
    comparisons = [(i, j) if ho == -1 else (j, i) for i, j, ho in comparisons if ho]

    deps = defaultdict(set)
    for i, j in comparisons:
        deps[i].add(j)
    for i in indices - set(deps):
        deps[i] = set()
    assert deps, indices

    sortedIndices = sum(topologicalSort(deps), [])
    assert len(sortedIndices) == len(contours), sorted(indices - set(sortedIndices))
    return [contours[i] for i in (sortedIndices)]


def topologicalSort(data):
    # Adapted from https://code.activestate.com/recipes/577413-topological-sort/
    extra_items_in_deps = reduce(set.union, data.values()) - set(data.keys())
    data.update({item: set() for item in extra_items_in_deps})
    while True:
        ordered = set(item for item, dep in data.items() if not dep)
        if not ordered:
            break
        yield sorted(ordered)
        data = {
            item: (dep - ordered) for item, dep in data.items() if item not in ordered
        }


def horizontalOrderContour(contour1, contour2):
    bounds1 = contour1.controlBounds
    bounds2 = contour2.controlBounds
    ho = horizontalOrderRect(bounds1, bounds2)
    if ho:
        return ho

    if rectsOverlap(bounds1, bounds2):
        for segment1 in contour1.segments:
            for segment2 in contour2.segments:
            	ho = horizontalOrderSegment(segment1, segment2)
                if ho:
                    return ho
    return 0


def horizontalOrderSegment(segment1, segment2, maxRecursionLevel=4):
    if maxRecursionLevel < 0:
        return 0
    bounds1 = segment1.controlBounds
    bounds2 = segment2.controlBounds
    ho = horizontalOrderRect(bounds1, bounds2)
    if ho:
        return ho

    if rectsOverlap(bounds1, bounds2):
        overlaps = []
        for seg1 in segment1.splitAtT(0.5):
            for seg2 in segment2.splitAtT(0.5):
                bounds1 = seg1.controlBounds
                bounds2 = seg2.controlBounds
                ho = horizontalOrderRect(bounds1, bounds2)
                if ho:
                    return ho
                if rectsOverlap(bounds1, bounds2):
                    overlaps.append((seg1, seg2))
        for seg1, seg2 in overlaps:
            ho = horizontalOrderSegment(seg1, seg2, maxRecursionLevel - 1)
            if ho:
                return ho
    return 0


def horizontalOrderRect(rect1, rect2):
    if rectsOverlapVertically(rect1, rect2):
        if rect1.xMax <= rect2.xMin:
            return -1
        elif rect1.xMin >= rect2.xMax:
            return 1
    return 0


def rectsOverlap(rect1, rect2):
    return rectsOverlapVertically(rect1, rect2) and rectsOverlapHorizontally(
        rect1, rect2
    )


def rectsOverlapHorizontally(rect1, rect2):
    return max(rect1.xMin, rect2.xMin) < min(rect1.xMax, rect2.xMax)


def rectsOverlapVertically(rect1, rect2):
    return max(rect1.yMin, rect2.yMin) < min(rect1.yMax, rect2.yMax)


if __name__ == "__main__":
    # DrawBot test
    def drawCurve(pt1, pt2, pt3, pt4):
        bez = BezierPath()
        bez.moveTo(pt1)
        bez.curveTo(pt2, pt3, pt4)
        drawPath(bez)

    offset = 100
    angle = radians(150)

    lineJoin("round")
    lineCap("round")
    stroke(0)
    fill(None)

    if False:
        curve = (100, 100), (160, 300), (300, 300), (400, 100)
        drawCurve(*curve)
        dx = 300 * math.cos(angle)
        dy = 300 * math.sin(angle)
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
    letterBez.text("S", font="Helvetica", fontSize=800, offset=(190, 210))
    pen = PathBuilderPen(None)
    letterBez.drawToPen(pen)
    # drawPath(bez)
    path = pen.path
    with savedState():
        strokeWidth(2)

        left, right = path.splitAtAngle(angle)
        extruded = extrudePath(left, angle, offset)

        bez = BezierPath()
        extruded.draw(bez)
        strokeWidth(6)
        lineDash(None)
        fill(0.5)
        drawPath(bez)

    fill(1, 0.7, 0.4, 0.8)
    stroke(None)
    bez = BezierPath()
    path.draw(bez)
    drawPath(bez)
