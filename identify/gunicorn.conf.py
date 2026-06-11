"""Gunicorn auto-loads this file from the working directory — it applies even
when the Render service was created manually with a bare `gunicorn app:app`
start command (where render.yaml's startCommand is ignored).

The /provenance endpoint runs up to 6 server-side web searches in one
request; the default 30s worker timeout kills it mid-search.
"""

timeout = 300
