#!/bin/sh

set -e  # make sure to abort on error
set -x  # echo commands


python -m glyphsLib glyphs2ufo --output-dir build --designspace-path /dev/null sources/Nabla.glyphs

python scripts/shear_and_extrude.py build/Colrv1Test-Regular.ufo

fontmake -m build/Colrv1Test-Regular.designspace -o variable --output-dir fonts
