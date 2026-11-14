"""Publish a sealed SQLite projection for readers with another service identity."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile


def publish(source: Path, destination: Path) -> None:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError('snapshot must differ from source')
    descriptor, temporary = tempfile.mkstemp(prefix=destination.name + '.', dir=destination.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as reader:
            with closing(sqlite3.connect(temporary)) as writer:
                reader.backup(writer)
                writer.execute('PRAGMA journal_mode=DELETE')
        os.chmod(temporary, 0o640)
        with open(temporary, 'r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
