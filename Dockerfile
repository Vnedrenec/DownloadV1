FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# If you don't have poetry installed globally, you may want to run pip install -r requirements.txt instead of the above command. 

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]