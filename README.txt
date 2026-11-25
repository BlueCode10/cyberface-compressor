Cyberface Compressor
====================

Make an NBA 2K14 cyberface (png####.iff) smaller. The original file is never changed: you pick where the
smaller copy goes (default: a "Compressed" folder next to the original, same file name, ready for the game).

Use
  - Run CyberfaceCompressor.exe, then click Open..., or drag a .iff onto the window (or onto the .exe).
  - Textures: leave every texture on "Keep" for a lossless file, or pick a smaller size for any of them.
    "Standard (SD) sizes" sets the stock NBA 2K14 sizes in one click (face 512, chest 512, normal 512,
    occlusion 256, hair 256; textures already that size or smaller stay as they are). "Keep all" resets.
  - Compress...: choose where to save. Takes 10-40 seconds.

What it does
  Lossless (always)   Both zlib chunks are recompressed with zopfli, a slower encoder that writes ordinary
                      zlib data. The decompressed file is byte-identical (checked after writing).
                      About 3-4% smaller.
  Texture downscale   A texture is halved or quartered by dropping its top mip levels: the smaller level
  (optional)          already in the file becomes the top, so nothing is re-encoded. Biggest savings on HD
                      files, e.g. a 3.7 MB legacy file with chest 2048->1024, face 1024->512 and
                      hair 512->256 becomes 2.1 MB. The file is written in the free texture layout (no VRAM
                      block table), as the converter's HD mode and RED MC write it.
  Locked files        Locked (NBA2KHUB IFF Locker) files work with both: the header is put back in the
                      normal order in memory, edited, then locked again the same way, so the smaller
                      copy is still locked. No need to unlock or re-lock by hand.
  RED MC files        Files saved by RED MC ("EDITED WITH RED MC" in the header, e.g. the Sample
                      Cyberfaces) use the same header order and are handled the same way; the mark is
                      kept. Their HD textures (often 2048) shrink a lot at SD sizes: 75-93% smaller.
                      A texture that isn't square with a full mip chain (e.g. Shake Milton's 512x128
                      hair) is kept as it is.

Command line
  python compressor.py k27_08058_legacy.iff --chest_color 1024 --hair_color 256 [-o out.iff]

Build
  build.bat (needs Python 3 with numpy, zopfli and pyinstaller)

Files
  app.py         local server (127.0.0.1 only) + Microsoft Edge app window
  index.html     the window
  compressor.py  the compression itself (also a command-line tool)
  lockfmt.py     reads / restores the marked header layout (NBA2KHUB IFF Locker, RED MC)
  make_icon.py   draws icon.ico (python make_icon.py, needs pillow)
  lib/           copies of the Cyberface Converter's 2K14 readers (iff2k, layout, meshio)
