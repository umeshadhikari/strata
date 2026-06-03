"""
Streamlit dashboard — the QuickSight substitute for local development.

Run::

    streamlit run local/dashboard.py

Or specify a different warehouse path::

    STRATA_WAREHOUSE=./other/path streamlit run local/dashboard.py
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

WAREHOUSE = Path(os.environ.get("STRATA_WAREHOUSE", "local/data/warehouse")).resolve()


@st.cache_resource
def get_connection():
    """Open a DuckDB session with the Iceberg extension and register
    every Iceberg table in the local warehouse as a DuckDB view.

    Cached so Streamlit reuses the same in-memory DuckDB across reruns
    of the page — otherwise every widget interaction would re-scan all
    table metadata.
    """
    con = duckdb.connect(":memory:")
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    for db_dir in WAREHOUSE.iterdir() if WAREHOUSE.exists() else []:
        if not db_dir.is_dir():
            continue
        for table_dir in db_dir.iterdir():
            if not table_dir.is_dir() or not (table_dir / "metadata").exists():
                continue
            try:
                con.execute(
                    f"CREATE OR REPLACE VIEW {table_dir.name} AS "
                    f"SELECT * FROM iceberg_scan('{table_dir}', allow_moved_paths = true)"
                )
            except Exception:
                pass
    return con


@st.cache_data(ttl=60)
def query(sql: str) -> pd.DataFrame:
    """Run SQL through DuckDB and return the result as a DataFrame.
    Cached for 60 seconds so rapid widget changes don't hammer DuckDB."""
    return get_connection().execute(sql).fetchdf()


st.set_page_config(page_title="strata local", layout="wide")
st.title("strata — local lake dashboard")
st.caption(f"Warehouse: `{WAREHOUSE}`")

# --------------------------------------------------------------------- #
# Sidebar — table picker + watermark info
# --------------------------------------------------------------------- #
con = get_connection()
tables_df = con.execute(
    "SELECT table_name FROM duckdb_views() WHERE schema_name = 'main' ORDER BY 1"
).fetchdf()

if tables_df.empty:
    st.warning(
        "No Iceberg tables found in the warehouse. "
        "Run `python -m strata.local_ingest --table FACT_PAYMENT` first."
    )
    st.stop()

table = st.sidebar.selectbox("Table", tables_df["table_name"].tolist())

# --------------------------------------------------------------------- #
# Tabs: Overview, Schema, Data
# --------------------------------------------------------------------- #
tab_overview, tab_schema, tab_data, tab_query = st.tabs(
    ["Overview", "Schema", "Recent rows", "SQL"]
)

with tab_overview:
    col1, col2, col3 = st.columns(3)
    row_count = query(f"SELECT COUNT(*) AS n FROM {table}").iloc[0]["n"]
    col1.metric("Row count", f"{row_count:,}")
    try:
        wm = query(
            f"SELECT MAX(last_updated_time) AS w FROM {table}"
        ).iloc[0]["w"]
        col2.metric("Max last_updated_time", str(wm))
    except Exception:
        col2.metric("Max last_updated_time", "—")
    try:
        runs = query(
            f"SELECT COUNT(DISTINCT _ingest_run_id) AS r FROM {table}"
        ).iloc[0]["r"]
        col3.metric("Distinct ingest runs", f"{runs}")
    except Exception:
        col3.metric("Distinct ingest runs", "—")

    if table == "fact_payment":
        st.subheader("Daily payment volume")
        daily = query(
            "SELECT value_date AS day, COUNT(*) AS payments, "
            "SUM(amount) AS total_amount "
            "FROM fact_payment GROUP BY value_date ORDER BY value_date"
        )
        if not daily.empty:
            st.bar_chart(daily.set_index("day")["payments"])
            st.line_chart(daily.set_index("day")["total_amount"])

        st.subheader("By data owner")
        by_owner = query(
            "SELECT data_owner_id, COUNT(*) AS payments, "
            "SUM(amount) AS total_amount FROM fact_payment "
            "GROUP BY data_owner_id ORDER BY 2 DESC"
        )
        st.dataframe(by_owner)

with tab_schema:
    schema = query(f"DESCRIBE {table}")
    st.dataframe(schema)

with tab_data:
    n = st.slider("Rows to show", 10, 500, 50)
    st.dataframe(query(f"SELECT * FROM {table} LIMIT {n}"))

with tab_query:
    default_sql = f"SELECT *\nFROM {table}\nLIMIT 100"
    sql = st.text_area("SQL", value=default_sql, height=200)
    if st.button("Run"):
        try:
            st.dataframe(query(sql))
        except Exception as exc:
            st.error(str(exc))
