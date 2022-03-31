#!/usr/bin/env python

#
# This script listens to changes in the "sources" folder,
# and triggers the build script.
# Keep the compiled font from the "fonts" folder open in
# FontGoggles, and watch the saved changes from Glyphs.app
# take effect.
#
# You will need the "watchfiles" package from PyPI:
#
# $ pip install watchfiles
# 

import subprocess
from watchfiles import watch


for changes in watch("Sources"):
    print("Rebuilding font...")
    result = subprocess.run(
        ["./build.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(result.stdout)
    print("Done.")
