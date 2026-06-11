import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  DebitAccount,
  FormOptions,
  NewPaymentDto,
  PaymentCreated,
  RailCandidate,
  RailsRegistry,
  SavedBeneficiary,
  SuggestionsResponse,
  TablePage,
  TableSummary,
  WizardFormState,
  WizardTurnResponse,
} from './models';

const BASE = '/api';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  listTables(): Observable<{ tables: TableSummary[] }> {
    return this.http.get<{ tables: TableSummary[] }>(`${BASE}/tables`);
  }

  readTable(
    name: string,
    limit: number,
    offset: number,
  ): Observable<TablePage> {
    return this.http.get<TablePage>(`${BASE}/tables/${name}`, {
      params: { limit, offset },
    });
  }

  paymentFormOptions(): Observable<FormOptions> {
    return this.http.get<FormOptions>(`${BASE}/payments/form-options`);
  }

  createPayment(dto: NewPaymentDto): Observable<PaymentCreated> {
    return this.http.post<PaymentCreated>(`${BASE}/payments`, dto);
  }

  // ── Payment wizard (A2UI / Qwen via Ollama) ────────────────────── //
  listRails(): Observable<RailsRegistry> {
    return this.http.get<RailsRegistry>(`${BASE}/wizard/rails`);
  }

  listDebitAccounts(currency?: string, q?: string): Observable<{accounts: DebitAccount[]}> {
    let params: Record<string, string> = {};
    if (currency) params['currency'] = currency;
    if (q) params['q'] = q;
    return this.http.get<{accounts: DebitAccount[]}>(`${BASE}/wizard/accounts`, { params });
  }

  listBeneficiaries(q?: string): Observable<{beneficiaries: SavedBeneficiary[]}> {
    const params: Record<string, string> = {};
    if (q) params['q'] = q;
    return this.http.get<{beneficiaries: SavedBeneficiary[]}>(
      `${BASE}/wizard/beneficiaries`, { params },
    );
  }

  getBeneficiary(id: number): Observable<SavedBeneficiary> {
    return this.http.get<SavedBeneficiary>(`${BASE}/wizard/beneficiaries/${id}`);
  }

  listSuggestions(): Observable<SuggestionsResponse> {
    return this.http.get<SuggestionsResponse>(`${BASE}/wizard/suggestions`);
  }

  /** List Ollama chat models + currently-active one (powers the gear-icon picker). */
  listOllamaModels(): Observable<{ active: string; models: { name: string; size: number; modified_at: string }[] }> {
    return this.http.get<{ active: string; models: { name: string; size: number; modified_at: string }[] }>(
      `${BASE}/wizard/models`,
    );
  }

  /** Swap the active Ollama chat model at runtime. */
  setOllamaModel(model: string): Observable<{ active: string }> {
    return this.http.post<{ active: string }>(`${BASE}/wizard/model`, { model });
  }

  selectRails(country: string | null, currency: string | null, amount: number | null):
      Observable<{candidates: RailCandidate[]}> {
    return this.http.post<{candidates: RailCandidate[]}>(
      `${BASE}/wizard/select-rail`,
      { country, currency, amount, urgency: null },
    );
  }

  wizardTurn(
    userText: string,
    formState: WizardFormState,
  ): Observable<WizardTurnResponse> {
    return this.http.post<WizardTurnResponse>(`${BASE}/wizard/turn`, {
      user_text: userText,
      form_state: formState,
    });
  }
}
