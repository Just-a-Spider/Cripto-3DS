FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt aiosqlite

COPY . /app/

ENV HEADLESS=true

# Expose Web Companion (7344) and 3DS TCP (7343)
EXPOSE 7344
EXPOSE 7343

CMD ["python", "main.py"]
