# Use Miniconda3 as the base image
FROM continuumio/miniconda3

# Set a working directory
WORKDIR /app

# Copy all files into the container
COPY . /app

# Update Conda and install needed packages
RUN conda update -n base -c defaults conda && \
    conda install -c conda-forge python=3.11 sqlite>=3.35 && \
    pip install --no-cache-dir -r requirements.txt

# Expose port 8000 to match Azure's expected port
EXPOSE 8000

# Start the app using gunicorn
CMD ["gunicorn", "--bind=0.0.0.0:8000", "app:app"]
