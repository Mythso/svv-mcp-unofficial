FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Railway setter PORT automatisk og injiserer den til containeren.
# server.py leser os.environ["PORT"] og starter Streamable HTTP-transport
# når den er satt.
EXPOSE 8000

CMD ["python", "server.py"]
