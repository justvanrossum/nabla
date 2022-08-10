#!/bin/sh

set -e  # make sure to abort on error
set -x  # echo commands


python -m glyphsLib glyphs2ufo --output-dir build --designspace-path /dev/null sources/Nabla.glyphs

python scripts/shear_and_extrude.py build/Nabla-Regular.ufo $1 $2

mkdir -p fonts

fontmake -m build/Nabla-Regular.designspace -o variable --output-path fonts/Nabla.ttf --flatten-components
