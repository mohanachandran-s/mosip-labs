FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY roottrace ./roottrace
RUN pip install --no-cache-dir . && useradd -u 10001 roottrace
USER 10001
EXPOSE 8080
ENTRYPOINT ["roottrace"]
CMD ["serve", "-c", "/etc/roottrace/config.yaml", "--port", "8080", "--out", "/tmp/out"]
