"""
Local Spark + Iceberg session for the Docker-based local stack.

Uses the Iceberg **JDBC catalog** backed by PostgreSQL, so both Spark and
Trino share the same catalog metadata. The warehouse files live on a shared
Docker volume mounted at `/data/warehouse` in both containers.

Configuration is environment-driven so the same code works on host or
in-container:

    STRATA_WAREHOUSE             default: file:///data/warehouse
    STRATA_JDBC_CATALOG_URI      default: jdbc:postgresql://postgres:5432/data_mart
    STRATA_JDBC_CATALOG_USER     default: strata
    STRATA_JDBC_CATALOG_PASSWORD default: strata
"""

import logging
import os

from pyspark.sql import SparkSession

log = logging.getLogger(__name__)

_ICEBERG_PKG = "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.3"
_POSTGRES_PKG = "org.postgresql:postgresql:42.7.3"


def build_local_spark(
    catalog_name: str = "iceberg",
    app_name: str = "strata-local",
) -> SparkSession:
    """Build a Spark session wired to the shared JDBC Iceberg catalog."""
    warehouse = os.environ.get("STRATA_WAREHOUSE", "file:///data/warehouse")
    jdbc_uri = os.environ.get(
        "STRATA_JDBC_CATALOG_URI", "jdbc:postgresql://postgres:5432/data_mart"
    )
    jdbc_user = os.environ.get("STRATA_JDBC_CATALOG_USER", "strata")
    jdbc_pw = os.environ.get("STRATA_JDBC_CATALOG_PASSWORD", "strata")

    packages = ",".join([_ICEBERG_PKG, _POSTGRES_PKG])
    cat = catalog_name

    log.info(
        "Building Spark with JDBC catalog: warehouse=%s uri=%s",
        warehouse, jdbc_uri,
    )

    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("STRATA_SPARK_MASTER", "local[*]"))
        .config("spark.jars.packages", packages)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(f"spark.sql.catalog.{cat}", "org.apache.iceberg.spark.SparkCatalog")
        .config(f"spark.sql.catalog.{cat}.catalog-impl", "org.apache.iceberg.jdbc.JdbcCatalog")
        .config(f"spark.sql.catalog.{cat}.uri", jdbc_uri)
        .config(f"spark.sql.catalog.{cat}.warehouse", warehouse)
        .config(f"spark.sql.catalog.{cat}.jdbc.user", jdbc_user)
        .config(f"spark.sql.catalog.{cat}.jdbc.password", jdbc_pw)
        .config("spark.sql.defaultCatalog", cat)
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.ui.showConsoleProgress", "false")
    )

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark
