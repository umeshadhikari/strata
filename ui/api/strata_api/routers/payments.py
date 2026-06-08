"""POST /api/payments — insert a new fact_pay_payment row.

After the row lands, strata's next incremental ingest picks it up via the
watermark window and propagates it into Iceberg. So this endpoint is also
the simplest end-to-end demo of the framework.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import connect, dict_cursor

router = APIRouter()


class NewPayment(BaseModel):
    """The minimum a user can fill in. All FK ids are validated against
    their dim tables before insert, so the UI can show a friendly error
    instead of a Postgres FK violation.
    """

    ordering_account_id: int = Field(..., description="dim_account.id")
    payment_method_id: int = Field(
        ..., ge=1, le=5, description="Synthetic payment method 1-5"
    )
    currency_id: int = Field(..., description="dim_currency.id")
    data_owner_id: int = Field(..., description="dim_data_owner.id")
    amount: Decimal = Field(..., gt=0, description="Payment amount (positive)")
    counterparty_country_code: str = Field(
        ..., min_length=2, max_length=2, description="ISO alpha-2"
    )
    counterparty_country_name: str = Field(..., min_length=1, max_length=200)
    counter_account_number: str = Field(..., min_length=1, max_length=64)
    creation_user_id: int = Field(default=1, description="dim_user.id")
    classification_id: int = Field(default=1)
    bank_status_id: int = Field(default=1)
    characteristics_id: int = Field(default=1)
    routing_id: int = Field(default=1)


@router.get("/form-options")
def form_options() -> dict[str, list[dict[str, Any]]]:
    """Populate the dropdowns on the New Payment screen.

    Returns currencies, data owners, accounts, and bank statuses so the
    Angular form can render meaningful labels instead of raw ids.
    """
    with connect() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, code, name FROM dim_currency ORDER BY code")
        currencies = cur.fetchall()

        cur.execute("SELECT id, code, name FROM dim_data_owner ORDER BY id")
        data_owners = cur.fetchall()

        cur.execute(
            "SELECT id, code, name FROM dim_account ORDER BY id LIMIT 200"
        )
        accounts = cur.fetchall()

        cur.execute("SELECT id FROM dim_pay_bank_status ORDER BY id")
        bank_statuses = cur.fetchall()
        cur.close()

        return {
            "currencies": list(currencies),
            "data_owners": list(data_owners),
            "accounts": list(accounts),
            "bank_statuses": list(bank_statuses),
        }


@router.post("", status_code=201)
def create_payment(p: NewPayment) -> dict[str, Any]:
    """Insert one row into ``fact_pay_payment``.

    ``last_updated_time`` is set to NOW() so the next ``strata`` ingest's
    watermark window picks it up. Identity column ``id`` is auto-assigned.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sql_text = """
        INSERT INTO fact_pay_payment (
            ordering_account_id, requesting_execution_date,
            payment_method_id, currency_id, data_owner_id,
            classification_id, characteristics_id,
            first_approver_id, second_approver_id, bank_status_id,
            routing_id, amount, amount_in_default_currency,
            fx_rate_of_default_currency,
            counter_account_number, counter_bank_name,
            counter_bank_country_code, counter_bank_country_name,
            counterparty_country_code, counterparty_country_name,
            counterparty_country_group_code, counterparty_country_group_name,
            own_reference_id, own_reference_batch_id, own_reference_first_id,
            amount_format_date, amount_approval_date, amount_release_date,
            first_approval_date, second_approval_date,
            first_signed_date, second_signed_date,
            creation_date, creation_user_id, last_updated_time
        ) VALUES (
            %(ordering_account_id)s, 0,
            %(payment_method_id)s, %(currency_id)s, %(data_owner_id)s,
            %(classification_id)s, %(characteristics_id)s,
            %(user_id)s, %(user_id)s, %(bank_status_id)s,
            %(routing_id)s, %(amount)s, %(amount)s,
            1.0000000000,
            %(counter_account_number)s, 'UI-entered',
            %(country_code)s, %(country_name)s,
            %(country_code)s, %(country_name)s,
            'UI', 'UI',
            1, 1, 'UI-REF',
            %(now)s, %(now)s, %(now)s,
            %(now)s, %(now)s,
            %(now)s, %(now)s,
            %(now)s, %(user_id)s, %(now)s
        )
        RETURNING id
    """

    params = {
        "ordering_account_id": p.ordering_account_id,
        "payment_method_id": p.payment_method_id,
        "currency_id": p.currency_id,
        "data_owner_id": p.data_owner_id,
        "classification_id": p.classification_id,
        "characteristics_id": p.characteristics_id,
        "user_id": p.creation_user_id,
        "bank_status_id": p.bank_status_id,
        "routing_id": p.routing_id,
        "amount": p.amount,
        "counter_account_number": p.counter_account_number,
        "country_code": p.counterparty_country_code.upper(),
        "country_name": p.counterparty_country_name,
        "now": now,
    }

    try:
        with connect() as conn:
            cur = dict_cursor(conn)
            cur.execute(sql_text, params)
            new_id = cur.fetchone()["id"]
            cur.close()
    except Exception as exc:  # psycopg2.errors.ForeignKeyViolation, etc.
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "id": int(new_id),
        "amount": str(p.amount),
        "currency_id": p.currency_id,
        "last_updated_time": now.isoformat(),
        "next_step": (
            "Run `./local/scripts/ingest.sh FACT_PAY_PAYMENT` to propagate "
            "this row into Iceberg."
        ),
    }
