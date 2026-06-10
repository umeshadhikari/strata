package com.strata.wizard.payments;

import com.strata.wizard.config.AppSettings;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.*;

/**
 * /api/payments endpoints — port of routers/payments.py.
 *
 * The wizard's "Submit payment" doesn't currently call this in the demo,
 * but the endpoint shape is kept identical so a future wizard turn that
 * actually inserts the row works without frontend changes.
 */
@RestController
@RequestMapping("/api/payments")
public class PaymentsController {

    private final NamedParameterJdbcTemplate jdbc;

    public PaymentsController(NamedParameterJdbcTemplate jdbc, AppSettings settings) {
        this.jdbc = jdbc;
    }

    @GetMapping("/form-options")
    public Map<String, Object> formOptions() {
        var currencies = jdbc.getJdbcTemplate().queryForList(
                "SELECT id, code, name FROM dim_currency ORDER BY code");
        var dataOwners = jdbc.getJdbcTemplate().queryForList(
                "SELECT id, code, name FROM dim_data_owner ORDER BY code");
        var accounts = jdbc.getJdbcTemplate().queryForList(
                "SELECT id, code, name FROM dim_account ORDER BY code");
        var bankStatuses = jdbc.getJdbcTemplate().queryForList(
                "SELECT id FROM dim_pay_bank_status ORDER BY id");
        return Map.of(
                "currencies", currencies,
                "data_owners", dataOwners,
                "accounts", accounts,
                "bank_statuses", bankStatuses
        );
    }

    public record NewPaymentDto(
            int ordering_account_id,
            int payment_method_id,
            int currency_id,
            int data_owner_id,
            double amount,
            String counterparty_country_code,
            String counterparty_country_name,
            String counter_account_number,
            Integer creation_user_id,
            Integer bank_status_id
    ) {}

    @PostMapping
    public Map<String, Object> create(@RequestBody NewPaymentDto dto) {
        String sql = """
                INSERT INTO fact_pay_payment (
                    ordering_account_id, payment_method_id, currency_id, data_owner_id,
                    amount, counterparty_country_code, counterparty_country_name,
                    counter_account_number, creation_user_id, bank_status_id,
                    last_updated_time
                ) VALUES (
                    :ordering_account_id, :payment_method_id, :currency_id, :data_owner_id,
                    :amount, :counterparty_country_code, :counterparty_country_name,
                    :counter_account_number, :creation_user_id, :bank_status_id,
                    NOW()
                ) RETURNING id, amount, currency_id, last_updated_time
                """;

        var params = new MapSqlParameterSource()
                .addValue("ordering_account_id", dto.ordering_account_id())
                .addValue("payment_method_id", dto.payment_method_id())
                .addValue("currency_id", dto.currency_id())
                .addValue("data_owner_id", dto.data_owner_id())
                .addValue("amount", dto.amount())
                .addValue("counterparty_country_code", dto.counterparty_country_code())
                .addValue("counterparty_country_name", dto.counterparty_country_name())
                .addValue("counter_account_number", dto.counter_account_number())
                .addValue("creation_user_id", dto.creation_user_id())
                .addValue("bank_status_id", dto.bank_status_id());

        Map<String, Object> row = jdbc.queryForMap(sql, params);
        return Map.of(
                "id", row.get("id"),
                "amount", String.valueOf(row.get("amount")),
                "currency_id", row.get("currency_id"),
                "last_updated_time", row.get("last_updated_time") == null
                        ? Instant.now().toString() : row.get("last_updated_time").toString(),
                "next_step", "Run an incremental ingest to propagate the new row into Iceberg."
        );
    }
}
