#!/bin/bash
# Azure App Service startup: bind to 0.0.0.0
# Use 8000 directly - Azure sets PORT but variable expansion can fail when invoked without shell
set -e
# U-237: generous worker-liveness ceiling for to_thread-offloaded upload work (was unset -> gunicorn's
# 30s default). A timeout hit here post-fix signals a DIFFERENT, still-unaudited loop-blocking
# regression elsewhere in the app, not the upload path this unit fixed.
exec gunicorn -w 2 -k uvicorn.workers.UvicornWorker app:app --bind "0.0.0.0:8000" --timeout 180
