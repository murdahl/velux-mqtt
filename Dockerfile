FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
# Holds the saved login token; mount a volume here so it survives restarts
RUN mkdir /data && chown nobody /data
VOLUME /data

USER nobody
CMD ["velux-mqtt"]
