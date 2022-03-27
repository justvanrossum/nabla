from fontTools.misc.transform import Transform
from fontTools.misc.bezierTools import (
    calcCubicParameters,
    solveQuadratic,
    splitCubicAtT,
)


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
