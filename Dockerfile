FROM python:3.12-slim

# Khong ghi file .pyc, in log ngay (khong bo dem)
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chay bang tai khoan thuong (khong phai root): neu ung dung bi khai thac thi ke tan cong
# khong co quyen he thong trong container.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8080
CMD ["python", "main.py"]
