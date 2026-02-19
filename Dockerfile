# Python 3.11 on Debian Bookworm
# ACR Tasks builds on amd64. For local Mac ARM build, use: docker build --platform linux/amd64 ...
FROM python:3.11-slim-bookworm

# Install Microsoft ODBC Driver 18 for SQL Server (required for pyodbc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Azure App Service sets PORT; default for local runs
ENV PORT=8000
EXPOSE 8000

CMD gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind "0.0.0.0:${PORT}"
