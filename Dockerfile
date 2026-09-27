FROM apache/airflow:3.3.2-python3.12

# Only what the pipeline needs is installed on top. requirements-airflow.txt says why some of
# the image's own versions are replaced.
COPY requirements-airflow.txt /tmp/requirements-airflow.txt
# xgboost pulls NCCL on Linux for training across GPUs: 240 MB of an 8 GB project disk budget,
# for a CPU-only server. It goes after `pip check`, which would report it as missing.
RUN pip install --no-cache-dir -r /tmp/requirements-airflow.txt \
    && pip check \
    && pip uninstall -y nvidia-nccl-cu13

# The code is part of the image, so the server runs exactly what CI tested. The local compose
# file mounts ./finplat and ./dags over these paths, so a local edit needs no rebuild.
ENV PYTHONPATH=/opt/project
COPY --chown=airflow:root finplat /opt/project/finplat
COPY --chown=airflow:root dags /opt/airflow/dags

# The lake volume is shared with the API. Both images run as uid 50000, so either one can
# append to a table the other created. A named volume copies this folder's owner.
# /opt/airflow/state holds the metadata database and the task logs on the server, in a volume
# of its own. Same ownership rule as /lake.
USER root
RUN mkdir /lake /opt/airflow/state && chown airflow:root /lake /opt/airflow/state
USER airflow
