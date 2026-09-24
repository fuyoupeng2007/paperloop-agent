"""Copy dependency license files into the generated distribution."""
import importlib.metadata as metadata
import shutil
import sys
from pathlib import Path

stage = Path(sys.argv[1]).resolve()
target = stage / 'third-party-licenses'
target.mkdir(parents=True, exist_ok=True)
packages = ['fastapi', 'starlette', 'pydantic', 'pydantic_core', 'uvicorn', 'h11', 'click',
    'httpx', 'httpcore', 'anyio', 'certifi', 'idna', 'sniffio', 'python-multipart', 'filelock',
    'pdfplumber', 'pdfminer.six', 'pypdf', 'pypdfium2', 'pillow', 'cryptography', 'cffi',
    'charset-normalizer', 'numpy', 'rapidocr', 'onnxruntime', 'opencv-python', 'shapely',
    'pyclipper', 'omegaconf', 'antlr4-python3-runtime', 'PyYAML', 'tqdm', 'colorlog',
    'requests', 'urllib3', 'flatbuffers', 'packaging', 'typing_extensions', 'sympy', 'mpmath',
    'PyInstaller', 'pyinstaller-hooks-contrib']
lines = ['PaperLoop 0.3.0 third-party notices', '',
    'Bundled libraries and OCR model files retain their respective licenses.',
    'Python, .NET and WebView2 components retain their respective notices.', '',
    'Python ' + sys.version, '']
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    shutil.copy2(python_license, target / 'Python-LICENSE.txt')
for name in packages:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        continue
    lines.append(f'{dist.metadata["Name"]} {dist.version}')
    for file in dist.files or []:
        if any(word in Path(str(file)).name.lower() for word in ('license', 'notice', 'copying')):
            source = Path(dist.locate_file(file))
            if source.is_file():
                destination = target / name / str(file).replace('../', '').replace('..\\', '')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
for name in ['react', 'react-dom', 'pdfjs-dist', 'lucide-react']:
    package = Path(__file__).resolve().parents[1] / 'node_modules' / name
    for source in package.glob('*'):
        if source.is_file() and source.name.lower().startswith(('license', 'notice')):
            destination = target / name / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    lines.append(name + ' (frontend)')
(stage / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
