# Use Miniconda3 as the base image
FROM continuumio/miniconda3

# Set working directory
WORKDIR /app

# Copy project files into the container
COPY . /app

# Install system build tools
RUN apt-get update && apt-get install -y \
    wget \
    build-essential \
    libssl-dev \
    libreadline-dev \
    zlib1g-dev \
    libbz2-dev \
    libsqlite3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build and install SQLite 3.40+
RUN wget https://www.sqlite.org/2023/sqlite-autoconf-3420000.tar.gz && \
    tar xvfz sqlite-autoconf-3420000.tar.gz && \
    cd sqlite-autoconf-3420000 && \
    ./configure --prefix=/usr/local && \
    make && \
    make install && \
    cd .. && rm -rf sqlite-autoconf-3420000*

# Update Conda and install Python + pip packages
RUN conda update -n base -c defaults conda && \
    conda install -c conda-forge python=3.11 && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Set environment variable to help Python use the correct SQLite
ENV LD_LIBRARY_PATH="/usr/local/lib:$LD_LIBRARY_PATH"

# Expose the port Azure expects
EXPOSE 8000

# Run the app
CMD ["gunicorn", "--bind=0.0.0.0:8000", "app:app"]
