FROM python:3.9.18-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . /app
CMD ["python", "/app/app.py"]