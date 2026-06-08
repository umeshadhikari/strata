// Shared API DTOs.

export interface TableSummary {
  name: string;
  row_count: number;
  kind: 'dimension' | 'fact' | 'other';
}

export interface TablePage {
  name: string;
  schema: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}

export interface FormOptions {
  currencies: { id: number; code: string; name: string }[];
  data_owners: { id: number; code: string; name: string }[];
  accounts: { id: number; code: string; name: string }[];
  bank_statuses: { id: number }[];
}

export interface NewPaymentDto {
  ordering_account_id: number;
  payment_method_id: number;
  currency_id: number;
  data_owner_id: number;
  amount: number;
  counterparty_country_code: string;
  counterparty_country_name: string;
  counter_account_number: string;
  creation_user_id?: number;
  bank_status_id?: number;
}

export interface PaymentCreated {
  id: number;
  amount: string;
  currency_id: number;
  last_updated_time: string;
  next_step: string;
}
