package com.strata.wizard.tables;

import com.strata.wizard.config.AppSettings;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;
import java.util.regex.Pattern;

/**
 * /api/tables endpoints — port of routers/tables.py.
 *
 * Two endpoints:
 *   GET /api/tables                                — table summary with row counts + kind classification
 *   GET /api/tables/{name}?limit=&offset=          — paginated rows
 *
 * Table-name parameters are guarded by a strict regex to keep identifiers
 * out of dynamic SQL (the FastAPI version uses psycopg2.sql.Identifier — we
 * achieve the same with a whitelist).
 */
@RestController
@RequestMapping("/api/tables")
public class TablesController {

    private static final Pattern SAFE_IDENT = Pattern.compile("^[a-zA-Z][a-zA-Z0-9_]*$");

    private final NamedParameterJdbcTemplate jdbc;
    private final String schema;

    public TablesController(NamedParameterJdbcTemplate jdbc, AppSettings settings) {
        this.jdbc = jdbc;
        this.schema = settings.getPg().getSchema();
    }

    @GetMapping
    public Map<String, Object> list() {
        String sql = """
                SELECT table_name
                FROM   information_schema.tables
                WHERE  table_schema = :schema
                  AND  table_type   = 'BASE TABLE'
                ORDER  BY table_name
                """;
        var names = jdbc.queryForList(sql, new MapSqlParameterSource("schema", schema), String.class);
        List<Map<String, Object>> tables = new ArrayList<>();
        for (String name : names) {
            String kind = name.startsWith("dim_") ? "dimension"
                    : name.startsWith("fact_") ? "fact" : "other";
            long count = countRows(name);
            tables.add(Map.of("name", name, "row_count", count, "kind", kind));
        }
        return Map.of("tables", tables);
    }

    @GetMapping("/{name}")
    public Map<String, Object> read(@PathVariable String name,
                                    @RequestParam(defaultValue = "50") int limit,
                                    @RequestParam(defaultValue = "0") int offset) {
        if (!SAFE_IDENT.matcher(name).matches()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid table name");
        }
        // Column list — pull schema metadata so we can use it as headers.
        String colSql = """
                SELECT column_name
                FROM   information_schema.columns
                WHERE  table_schema = :schema AND table_name = :name
                ORDER  BY ordinal_position
                """;
        var columns = jdbc.queryForList(colSql,
                new MapSqlParameterSource("schema", schema).addValue("name", name),
                String.class);
        if (columns.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Table not found");
        }

        // Order by `id` when present for stable pagination.
        String orderBy = columns.contains("id") ? "ORDER BY id" : "";
        long total = countRows(name);

        @SuppressWarnings("squid:S2077")  // identifier safety enforced by SAFE_IDENT above
        String dataSql = "SELECT * FROM " + schema + "." + name + " " + orderBy
                + " LIMIT " + limit + " OFFSET " + offset;
        var rows = jdbc.getJdbcTemplate().queryForList(dataSql);

        return Map.of(
                "name", name,
                "schema", schema,
                "columns", columns,
                "rows", rows,
                "total", total,
                "limit", limit,
                "offset", offset
        );
    }

    private long countRows(String name) {
        @SuppressWarnings("squid:S2077")
        String sql = "SELECT COUNT(*) FROM " + schema + "." + name;
        Long n = jdbc.getJdbcTemplate().queryForObject(sql, Long.class);
        return n == null ? 0 : n;
    }
}
