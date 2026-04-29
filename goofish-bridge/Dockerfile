FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir goofish-cli

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py
COPY items.py ./items.py

CMD ["sh", "-c", "uvicorn app:APP --host ${GOOFISH_BRIDGE_HOST:-0.0.0.0} --port ${GOOFISH_BRIDGE_PORT:-8787}"]
