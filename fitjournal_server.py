"""Play2Go entry point with SQLite data stored outside tracked source files."""
import os
from pathlib import Path

from waitress import serve
from fitjournal import create_app


def build_app():
    data_dir = Path(__file__).resolve().parent / 'fitjournal-data'
    data_dir.mkdir(exist_ok=True, mode=0o700)
    return create_app({
        'DATABASE': str(data_dir / 'journal.sqlite3'),
        'SESSION_COOKIE_SECURE': os.environ.get('FITJOURNAL_HTTPS') == '1',
    })


if __name__ == '__main__':
    serve(build_app(), host='0.0.0.0', port=int(os.environ.get('SERVER_PORT', '20261')), threads=2)
