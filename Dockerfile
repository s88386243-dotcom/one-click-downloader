# Lightweight Python 3.11 Linux Base
FROM python:3.11-slim

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies: FFmpeg and curl
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure downloads and task states directories exist
RUN mkdir -p downloads/task_states

# Port configuration
ENV PORT=5000
EXPOSE 5000

# Start production server with gunicorn threads
CMD ["gunicorn", "app:app", "--workers", "2", "--threads", "4", "--timeout", "180", "--bind", "0.0.0.0:5000"]
