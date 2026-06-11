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

// ─── Payment Wizard / A2UI ─────────────────────────────────────────── //

export interface RailFieldOption {
  value: string;
  label: string;
}

export interface RailFieldDependsOn {
  field: string;
  value: string | number | boolean;
}

export interface RailField {
  id: string;
  label: string;
  help?: string;
  type:
    | 'text'
    | 'number'
    | 'textarea'
    | 'radio'
    | 'select'
    | 'account_picker'
    | 'beneficiary_typeahead';
  required?: boolean;
  validate?: string;
  placeholder?: string;
  max_length?: number;
  auto_uppercase?: boolean;
  auto_derive_from?: string;
  depends_on?: RailFieldDependsOn;
  options?: RailFieldOption[];
}

export interface DebitAccount {
  id: number;
  code: string;
  name: string;
  currency: string;
  balance: number;
  identifier: string;
  kind: string;
}

export interface SavedBeneficiary {
  id: number;
  name: string;
  country: string;
  preferred_currency: string;
  preferred_rail: string;
  fields: Record<string, unknown>;
  last_paid: string;
  payment_count: number;
  bank_alias?: string;
  payment_history?: Array<{ date: string; amount: number }>;
}

// ─── Recurring suggestions ─────────────────────────────────────────── //

export interface RecurringSuggestion {
  beneficiary_id: number;
  beneficiary: string;
  bank_alias?: string;
  country: string;
  rail_id: string;
  currency: string;
  suggested_amount: number;
  reason: string;
  confidence: number;
  cadence_days: number;
  days_since_last: number;
  days_until_due: number;
  is_overdue: boolean;
  fields: Record<string, unknown>;
}

export interface ReusableTemplate {
  beneficiary_id: number;
  beneficiary: string;
  bank_alias?: string;
  country: string;
  rail_id: string;
  currency: string;
  last_paid: string;
  payment_count: number;
  fields: Record<string, unknown>;
}

export interface SuggestionsResponse {
  suggestions: RecurringSuggestion[];
  templates: ReusableTemplate[];
}

export interface RailDef {
  display_name: string;
  region: string;
  countries: string[];
  currencies: string[];
  settlement: string;
  max_amount?: number;
  summary?: string;
  fields: RailField[];
}

export interface RailsRegistry {
  rails: Record<string, RailDef>;
  common_fields: RailField[];
}

export interface RailAvailability {
  available_now: boolean;
  status_text: string;
  urgency: 'now' | 'today_soon' | 'today' | 'next_window' | string;
  cutoff_at: string | null;
  next_window_at: string;
  settles_by: string;
}

export interface RailCost {
  fixed_fee: number;
  percentage_fee: number;
  fx_spread_estimate: number;
  correspondent_fee_low: number;
  correspondent_fee_high: number;
  total_low: number;
  total_high: number;
  currency: string;
  headline: string;
}

export interface RailCandidate {
  rail_id: string;
  why: string;
  availability?: RailAvailability | null;
  cost?: RailCost | null;
  score?: number;
  exceeds_limit?: boolean;
}

export interface WizardToolCall {
  name: 'set_field' | 'select_rail' | 'ask' | 'explain';
  args: Record<string, unknown>;
}

export interface WizardValidation {
  field_id: string;
  ok: boolean;
  error: string | null;
}

export interface WizardTurnResponse {
  tool_calls: WizardToolCall[];
  candidates: RailCandidate[];
  available_fields: string[];
  validation: WizardValidation[];
  derived: Record<string, unknown>;
  raw_message?: string | null;
}

export type WizardFormState = Record<string, unknown> & {
  rail_id?: string | null;
  /** When true, the user picked the rail directly. The backend will not let the
   *  LLM override `rail_id` until the lock is released (see WizardService). */
  rail_locked?: boolean;
};
