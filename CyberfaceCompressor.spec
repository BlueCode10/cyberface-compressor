# PyInstaller spec for the Cyberface Compressor: build with build.bat
# lib/ holds copies of the Cyberface Converter's 2K14 readers (iff2k, layout, meshio); bundled as modules.
import os

here = os.path.abspath(SPECPATH)
datas = [(os.path.join(here, 'index.html'), '.'), (os.path.join(here, 'icon.ico'), '.')]

a = Analysis([os.path.join(here, 'app.py')], pathex=[here, os.path.join(here, 'lib')], datas=datas,
             hiddenimports=['numpy', 'compressor', 'lockfmt', 'iff2k', 'layout', 'meshio', 'zopfli', 'zopfli.zlib'],
             excludes=['tkinter', 'matplotlib', 'pytest', 'IPython', 'pandas', 'scipy', 'PIL'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CyberfaceCompressor',
          icon=os.path.join(here, 'icon.ico'), console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='Cyberface Compressor', upx=False)
