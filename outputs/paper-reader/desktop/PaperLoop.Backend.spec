# Build from the application directory with its Python virtual environment.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'dist'), 'dist')]
datas += collect_data_files('rapidocr')
datas += copy_metadata('rapidocr')
datas += copy_metadata('onnxruntime')
hidden = ['backend.app', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
          'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
hidden += collect_submodules('rapidocr', filter=lambda name: not any(part in name for part in
    ('.pytorch', '.paddle', '.openvino', '.tensorrt', '.mnn', '.cann')))
a = Analysis([str(root / 'desktop' / 'launch_backend.py')], pathex=[str(root)],
    binaries=collect_dynamic_libs('onnxruntime'), datas=datas, hiddenimports=hidden,
    hookspath=[], runtime_hooks=[], excludes=['torch', 'tensorflow', 'paddle', 'openvino',
    'tensorrt', 'jax', 'matplotlib', 'pandas', 'scipy', 'IPython', 'notebook', 'pytest', 'tkinter'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PaperLoop.Backend',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    icon=str(root / 'desktop' / 'assets' / 'paperloop.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PaperLoop.Backend')
