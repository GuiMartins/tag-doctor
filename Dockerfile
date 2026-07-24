FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tag_doctor ./tag_doctor

EXPOSE 8991
CMD ["uvicorn", "tag_doctor.webapp:app", "--host", "0.0.0.0", "--port", "8991"]
