# APEX API/worker image. Not yet built or tested in CI (see docs/STATUS.md).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres,server]" && useradd --create-home apex
USER apex
EXPOSE 8000
CMD ["sh", "-c", "apex db-upgrade && apex serve --host 0.0.0.0 --port 8000"]
