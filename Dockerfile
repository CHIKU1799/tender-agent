FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium (already bundled in base image)
RUN playwright install chromium --with-deps 2>/dev/null || true

# Copy source
COPY . .

# Runtime dirs
RUN mkdir -p output logs screenshots

EXPOSE 5002

CMD ["python", "dashboard.py"]
