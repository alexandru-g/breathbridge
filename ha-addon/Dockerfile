FROM python:3.13-alpine

WORKDIR /app

RUN pip install --no-cache-dir \
    "websockets>=13.0" \
    "aiomqtt>=2.0" \
    "aiohttp>=3.9" \
    "pydantic>=2.0" \
    "pydantic-settings>=2.0"

COPY breathbridge/ /app/breathbridge/
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT ["/run.sh"]
