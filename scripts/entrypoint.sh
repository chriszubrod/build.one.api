#!/bin/bash
set -e

# Validate config and app load before gunicorn (surfaces startup errors in Azure Log Stream)
python -c "
import sys
try:
    import config
    config.Settings()
    from app import app
    print('Config and app load OK', flush=True)
except Exception as e:
    import traceback
    print('STARTUP ERROR:', str(e), file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
"

exec gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind "0.0.0.0:${PORT:-8000}"
