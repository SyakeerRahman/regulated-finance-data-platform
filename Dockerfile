FROM apache/airflow:3.3.2-python3.12

# Airflow's own pins stay in charge. Only what the pipeline adds is installed on top.
COPY requirements-airflow.txt /tmp/requirements-airflow.txt
RUN pip install --no-cache-dir -r /tmp/requirements-airflow.txt
