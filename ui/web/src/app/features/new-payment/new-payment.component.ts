import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { FormOptions, PaymentCreated } from '../../core/models';

interface FormState {
  ordering_account_id: number | null;
  payment_method_id: number;
  currency_id: number | null;
  data_owner_id: number | null;
  bank_status_id: number;
  amount: number | null;
  counterparty_country_code: string;
  counterparty_country_name: string;
  counter_account_number: string;
}

const EMPTY: FormState = {
  ordering_account_id: null,
  payment_method_id: 1,
  currency_id: null,
  data_owner_id: null,
  bank_status_id: 1,
  amount: null,
  counterparty_country_code: '',
  counterparty_country_name: '',
  counter_account_number: '',
};

// ISO alpha-2 → country name (matches dim_account country pool from bootstrap.py).
const COUNTRIES: Record<string, string> = {
  US: 'United States',
  GB: 'United Kingdom',
  DE: 'Germany',
  FR: 'France',
  CH: 'Switzerland',
  JP: 'Japan',
  SG: 'Singapore',
  HK: 'Hong Kong',
  AU: 'Australia',
  CA: 'Canada',
  BR: 'Brazil',
  MX: 'Mexico',
  IN: 'India',
  AE: 'United Arab Emirates',
  ZA: 'South Africa',
};

@Component({
  selector: 'app-new-payment',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <h1>Create new payment</h1>
    <p class="muted">
      Inserts one row into <span class="kbd">data_mart.fact_pay_payment</span>
      with <span class="kbd">last_updated_time = NOW()</span> so the next
      strata ingest's watermark window picks it up.
    </p>

    @if (options(); as opts) {
      <form class="grid" (submit)="onSubmit($event)">
        <div class="field span-2">
          <label>Ordering account</label>
          <select [(ngModel)]="form.ordering_account_id" name="account" required>
            <option [ngValue]="null" disabled>— choose an account —</option>
            @for (a of opts.accounts; track a.id) {
              <option [ngValue]="a.id">
                {{ a.code }} — {{ a.name }}
              </option>
            }
          </select>
        </div>

        <div class="field">
          <label>Data owner</label>
          <select [(ngModel)]="form.data_owner_id" name="owner" required>
            <option [ngValue]="null" disabled>—</option>
            @for (o of opts.data_owners; track o.id) {
              <option [ngValue]="o.id">{{ o.code }} — {{ o.name }}</option>
            }
          </select>
        </div>

        <div class="field">
          <label>Currency</label>
          <select [(ngModel)]="form.currency_id" name="ccy" required>
            <option [ngValue]="null" disabled>—</option>
            @for (c of opts.currencies; track c.id) {
              <option [ngValue]="c.id">{{ c.code }} — {{ c.name }}</option>
            }
          </select>
        </div>

        <div class="field">
          <label>Amount</label>
          <input
            type="number"
            step="0.01"
            min="0.01"
            [(ngModel)]="form.amount"
            name="amount"
            placeholder="0.00"
            required
          />
        </div>

        <div class="field">
          <label>Payment method</label>
          <select [(ngModel)]="form.payment_method_id" name="method" required>
            <option [ngValue]="1">1 — wire</option>
            <option [ngValue]="2">2 — ACH</option>
            <option [ngValue]="3">3 — RTP</option>
            <option [ngValue]="4">4 — book</option>
            <option [ngValue]="5">5 — card</option>
          </select>
        </div>

        <div class="field">
          <label>Counterparty country</label>
          <select
            [(ngModel)]="form.counterparty_country_code"
            name="country"
            (change)="onCountryChange()"
            required
          >
            <option value="" disabled>—</option>
            @for (k of countryKeys; track k) {
              <option [value]="k">{{ k }} — {{ countries[k] }}</option>
            }
          </select>
        </div>

        <div class="field span-2">
          <label>Counterparty account number</label>
          <input
            [(ngModel)]="form.counter_account_number"
            name="counter_acc"
            placeholder="AC12345678"
            maxlength="64"
            required
          />
        </div>

        <div class="span-2 actions">
          <button type="submit" [disabled]="submitting()">
            {{ submitting() ? 'Saving…' : 'Insert payment' }}
          </button>
          <button type="button" class="ghost" (click)="reset()">Reset</button>
        </div>

        @if (submitError()) {
          <div class="span-2 card error">{{ submitError() }}</div>
        }
        @if (lastCreated(); as created) {
          <div class="span-2 card success-card">
            <strong>Inserted as id #{{ created.id }}.</strong>
            <div class="muted" style="margin-top:6px">
              {{ created.next_step }}
            </div>
            <div style="margin-top:10px">
              <a [routerLink]="['/tables', 'fact_pay_payment']">
                view fact_pay_payment →
              </a>
            </div>
          </div>
        }
      </form>
    } @else if (optionsError()) {
      <div class="card error">{{ optionsError() }}</div>
    } @else {
      <p class="muted">Loading form options…</p>
    }
  `,
  styles: [
    `
      h1 { font-size: 26px; font-weight: 700; letter-spacing: -0.01em; margin: 0 0 6px; }
      .grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px 18px;
        max-width: 720px;
        margin-top: 18px;
      }
      .span-2 { grid-column: span 2; }
      .field { display: flex; flex-direction: column; gap: 6px; }
      label { font-size: 12px; color: var(--text-muted); }
      .actions { display: flex; gap: 12px; margin-top: 6px; }
      .success-card { border-color: var(--accent); background: var(--accent-soft); }
    `,
  ],
})
export class NewPaymentComponent {
  private api = inject(ApiService);

  countries = COUNTRIES;
  countryKeys = Object.keys(COUNTRIES);

  form: FormState = { ...EMPTY };

  options = signal<FormOptions | null>(null);
  loadingOptions = signal(true);
  optionsError = signal<string | null>(null);

  submitting = signal(false);
  submitError = signal<string | null>(null);
  lastCreated = signal<PaymentCreated | null>(null);

  constructor() {
    this.api.paymentFormOptions().subscribe({
      next: (opts) => {
        this.options.set(opts);
        this.loadingOptions.set(false);
      },
      error: (err) => {
        this.optionsError.set(err?.message ?? 'failed to load options');
        this.loadingOptions.set(false);
      },
    });
  }

  onCountryChange(): void {
    const k = this.form.counterparty_country_code;
    this.form.counterparty_country_name = COUNTRIES[k] ?? '';
  }

  onSubmit(ev: Event): void {
    ev.preventDefault();
    this.submitError.set(null);
    this.lastCreated.set(null);

    if (
      this.form.ordering_account_id == null ||
      this.form.currency_id == null ||
      this.form.data_owner_id == null ||
      this.form.amount == null ||
      !this.form.counterparty_country_code ||
      !this.form.counter_account_number
    ) {
      this.submitError.set('All fields are required.');
      return;
    }

    this.submitting.set(true);
    this.api
      .createPayment({
        ordering_account_id: this.form.ordering_account_id!,
        payment_method_id: this.form.payment_method_id,
        currency_id: this.form.currency_id!,
        data_owner_id: this.form.data_owner_id!,
        bank_status_id: this.form.bank_status_id,
        amount: this.form.amount!,
        counterparty_country_code: this.form.counterparty_country_code,
        counterparty_country_name: this.form.counterparty_country_name,
        counter_account_number: this.form.counter_account_number,
      })
      .subscribe({
        next: (created) => {
          this.lastCreated.set(created);
          this.submitting.set(false);
        },
        error: (err) => {
          this.submitError.set(
            err?.error?.detail ?? err?.message ?? 'insert failed',
          );
          this.submitting.set(false);
        },
      });
  }

  reset(): void {
    this.form = { ...EMPTY };
    this.lastCreated.set(null);
    this.submitError.set(null);
  }
}
