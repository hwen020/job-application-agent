FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright DEMO_BIND=0.0.0.0 PORT=10000
WORKDIR /opt/demo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY app ./app
RUN useradd --create-home demo && mkdir runtime && chown demo:demo runtime
USER demo
EXPOSE 10000
CMD ["python", "-m", "app.server"]
