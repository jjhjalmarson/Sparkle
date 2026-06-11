"""Gunicorn auto-loads ./gunicorn.conf.py from the directory it starts in.
The Render service for identify/ was created manually and starts gunicorn
from the repo root, so this copy (duplicating identify/gunicorn.conf.py)
is the one that actually applies there.

The /provenance endpoint runs up to 6 server-side web searches in one
request; the default 30s worker timeout kills it mid-search.
"""

timeout = 300
