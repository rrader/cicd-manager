FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install docker CLI and git
RUN apt-get update && \
    apt-get install -y docker.io git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the application
CMD ["python", "app.py"]

