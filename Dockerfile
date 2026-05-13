FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY scraper/ scraper/
COPY tests/ tests/
COPY run_scraper.py .

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

ENTRYPOINT ["bash", "-c", "Xvfb :99 -screen 0 1920x1080x24 &>/dev/null & exec python run_scraper.py \"$@\"", "--"]
