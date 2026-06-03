# Minimal Spark runtime image for strata local dev.
#
# We build our own rather than depending on bitnami/spark or apache/spark
# because:
#   - bitnami restructured their free images and 3.5.0 isn't pullable anymore.
#   - apache/spark Python variants have inconsistent tag names across versions.
#   - This image is small (~600 MB) and 100% reproducible from a public base.
#
# Build is cached after the first run, so `docker compose up` is fast on
# subsequent invocations.

FROM eclipse-temurin:17-jdk-jammy

# System packages: Python, pip, and a couple of niceties for debugging.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        procps \
        less \
        ca-certificates \
        sqlite3 \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

# Pin PySpark to match the Iceberg runtime jar we declare in spark.py
# (org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.3 needs Spark 3.5.x).
RUN pip3 install --no-cache-dir \
        pyspark==3.5.0 \
        psycopg2-binary==2.9.9 \
        pyyaml==6.0.1 \
        boto3==1.34.34

# Bake the PostgreSQL JDBC driver directly into PySpark's jars/ directory.
#
# Why not just rely on spark.jars.packages from spark.py?
#   - That uses Ivy to download at SparkSession start-up. It works on the
#     first laptop because Ivy's cache is populated, but on a fresh laptop
#     it depends on network reach to Maven Central — and even when the
#     download succeeds, the JAR lands in a classloader that the Iceberg
#     catalog can see but Spark's spark.read.format("jdbc") source reader
#     sometimes can't. The symptom is a baffling "ClassCastException:
#     org.postgresql.Driver" during the extract step *after* Iceberg has
#     already connected fine through the same catalog.
#   - Putting the JAR in $PYSPARK_HOME/jars/ makes it visible to every
#     classloader Spark spins up, zero Ivy involvement.
#
# Pin matches _POSTGRES_PKG in src/strata/local_runtime/spark.py.
ARG POSTGRES_JDBC_VERSION=42.7.3
RUN PYSPARK_JARS=$(python3 -c "import os, pyspark; print(os.path.join(os.path.dirname(pyspark.__file__), 'jars'))") \
    && curl -fsSL \
       "https://repo1.maven.org/maven2/org/postgresql/postgresql/${POSTGRES_JDBC_VERSION}/postgresql-${POSTGRES_JDBC_VERSION}.jar" \
       -o "${PYSPARK_JARS}/postgresql-${POSTGRES_JDBC_VERSION}.jar" \
    && ls -l "${PYSPARK_JARS}/postgresql-${POSTGRES_JDBC_VERSION}.jar"

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    JAVA_HOME=/opt/java/openjdk

WORKDIR /app

# Keep the container alive so we can `docker compose exec` ingest commands
# into it without paying cold-start time on every run.
CMD ["tail", "-f", "/dev/null"]
