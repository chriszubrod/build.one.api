#!/bin/bash
# Azure App Service startup: bind to 0.0.0.0 and use PORT from environment
set -e
export PORT=${PORT:-8000}
exec gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind "0.0.0.0:${PORT}"
