FROM python:3.14-slim
WORKDIR /srv/inventory
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Explicit source copies exclude the existing database, backups and certificates.
COPY app/ app/
COPY create_owner.py .
RUN useradd --create-home --uid 10001 inventory && mkdir /inventory-data && chown inventory:inventory /inventory-data
USER inventory
ENV INVENTORY_DB=/inventory-data/pilot.sqlite3 INVENTORY_OPERATOR_SETUP=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
