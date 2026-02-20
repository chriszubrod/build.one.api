#!/bin/bash
# Azure App Service startup: bind to 0.0.0.0
# Use 8000 directly - Azure sets PORT but variable expansion can fail when invoked without shell
set -e
exec gunicorn -w 2 -k uvicorn.workers.UvicornWorker app:app --bind "0.0.0.0:8000"
