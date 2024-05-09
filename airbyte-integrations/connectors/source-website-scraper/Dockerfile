 FROM python:3.9.17-slim as base

# build and load all requirements
FROM base as builder
WORKDIR /airbyte/integration_code

# upgrade pip to the latest version
RUN apt upgrade \
    && pip install --upgrade pip \
    && apt install tzdata

# Install Playwright dependencies
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
    build-essential \
    libpq-dev \
    gcc \ 
    python3-dev \ 
    curl \ 
    poppler-utils \ 
    tesseract-ocr


COPY setup.py ./
# install necessary packages to a temporary folder
RUN pip install --prefix=/install .

ENV PLAYWRIGHT_BROWSERS_PATH /usr/local/bin/ms-playwright/
# Install Playwright
RUN pip install playwright
RUN playwright install --with-deps chromium
# build a clean environment
FROM base
WORKDIR /airbyte/integration_code

# copy all loaded and built libraries to a pure basic image
COPY --from=builder /install /usr/local
# add default timezone settings
COPY --from=builder /usr/share/zoneinfo/Etc/UTC /etc/localtime
RUN echo "Etc/UTC" > /etc/timezone

# bash is installed for more convenient debugging.
RUN apt install bash

# copy payload code only
COPY main.py ./
COPY source_website_scraper ./source_website_scraper

ENV AIRBYTE_ENTRYPOINT "python /airbyte/integration_code/main.py"
ENTRYPOINT ["python", "/airbyte/integration_code/main.py"]

LABEL io.airbyte.version=0.1.0
LABEL io.airbyte.name=airbyte/source-website-scraper