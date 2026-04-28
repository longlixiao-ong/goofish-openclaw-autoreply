FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir goofish-cli

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py ./watcher.py

CMD ["python", "watcher.py"]
