# Python 3.11 on Debian Bookworm
# For local Mac ARM build: docker build --platform linux/amd64 ...
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

# Production deps (excludes torch/sentence-transformers ~3GB - Azure embeddings used)
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

# Copy application code
COPY . .

EXPOSE 8000

# Startup script binds to 0.0.0.0:8000 (Azure default)
RUN chmod +x /app/startup.sh

CMD ["/app/startup.sh"]
