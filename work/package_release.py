"""Build a source + compiled UI archive without environments or personal data."""
import hashlib
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

workspace = Path(__file__).resolve().parents[1]
project = workspace / 'outputs' / 'paper-reader'
archive = workspace / 'outputs' / 'PaperLoop-v1.zip'
excluded = {'.venv', 'node_modules', 'data', '__pycache__', '.git', 'test-results', 'playwright-report', '.packages', '.build-home', 'bin', 'obj', 'work', 'staged'}
with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
    for folder, directories, files in os.walk(project):
        directories[:] = sorted(d for d in directories if d not in excluded)
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix in {'.pyc', '.pyo', '.db', '.sqlite', '.sqlite3'} or name.startswith('.env'):
                continue
            bundle.write(path, path.relative_to(project.parent))
with ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert 'paper-reader/dist/index.html' in bundle.namelist()
    assert all(not (set(Path(name).parts) & excluded) for name in bundle.namelist())
print(f'{archive.name}: {archive.stat().st_size:,} bytes')
print('SHA256:', hashlib.sha256(archive.read_bytes()).hexdigest())
