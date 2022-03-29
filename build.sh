#!/bin/sh

set -e  # make sure to abort on error
set -x  # echo commands


python -m glyphsLib glyphs2ufo --output-dir build sources/ColrV1_source.glyphs --designspace-path /dev/null

python scripts/extrude_and_project.py build/Colrv1Test-Regular.ufo

fontmake -m build/Colrv1Test-Regular.designspace -o variable --output-dir fonts

# python scripts/add_colrv1.py build/Colrv1Test-Regular-VF.ttf
