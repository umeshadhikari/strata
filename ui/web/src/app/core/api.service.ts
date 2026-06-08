import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  FormOptions,
  NewPaymentDto,
  PaymentCreated,
  TablePage,
  TableSummary,
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
}
