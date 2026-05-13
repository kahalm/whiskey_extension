FROM python:3.12-slim-bookworm

# Install Chrome and Xvfb
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       wget gnupg xvfb \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | \
       gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY scraper/ scraper/
COPY tests/ tests/
COPY run_scraper.py .

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

ENTRYPOINT ["bash", "-c", "Xvfb :99 -screen 0 1920x1080x24 &>/dev/null & exec python run_scraper.py \"$@\"", "--"]
