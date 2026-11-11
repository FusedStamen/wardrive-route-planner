FROM python:3.12-slim
WORKDIR /app
COPY app/ /app/
ENV DATA_DIR=/data PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)" || exit 1
CMD ["python", "-I", "/app/server.py"]
