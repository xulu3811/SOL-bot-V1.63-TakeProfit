"""Package only deployable source; exclude credentials, databases and local tooling."""
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
output = root / 'data' / 'p1-package.tar.gz'
output.parent.mkdir(exist_ok=True)
with tarfile.open(output, 'w:gz') as archive:
    for folder in ('backend', 'tests', 'deploy', 'docs'):
        for path in (root / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.sqlite', '.db'):
                archive.add(path, arcname=path.relative_to(root).as_posix())
print(output)
