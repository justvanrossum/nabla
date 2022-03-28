#!/bin/sh

set -e  # make sure to abort on error
set -x  # echo commands


fontmake -g sources/ColrV1_source.glyphs -o ttf --output-dir build --master-dir build

# python scripts/add_colrv1.py build/Colrv1Test-Regular-VF.ttf
