#!/bin/sh

set -e  # make sure to abort on error
set -x  # echo commands


python -m glyphsLib glyphs2ufo --output-dir build --designspace-path /dev/null sources/Nabla.glyphs

python scripts/shear_and_extrude.py build/Nabla-Regular.ufo $1 $2

mkdir -p fonts

output=fonts/Nabla[EDPT,EHLT].ttf

fontmake -m build/Nabla-Regular.designspace -o variable --output-path $output --flatten-components --no-optimize-gvar

gftools fix-nonhinting $output $output

# Remove leftovers from gftools fix-nonhinting
rm fonts/*backup-fonttools*.ttf
