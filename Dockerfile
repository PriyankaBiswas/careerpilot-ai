FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY app/ ./app/
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c 'import urllib.request; urllib.request.urlopen("http://localhost:10000/_stcore/health", timeout=3)' || exit 1
CMD ["sh", "-c", "streamlit run app/main.py --server.address=0.0.0.0 --server.port=${PORT:-10000} --server.headless=true --server.enableCORS=true --server.enableXsrfProtection=true"]
