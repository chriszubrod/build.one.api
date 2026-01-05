#!/bin/bash
# Azure App Service startup script
# Uses gunicorn with uvicorn workers to run the FastAPI app

gunicorn app:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600

