FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY statking statking
ENV STATKING_DB=/data/statking.db 
VOLUME /data
CMD ["python", "-m", "statking", "daemon"]
