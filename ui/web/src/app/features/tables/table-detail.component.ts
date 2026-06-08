import { CommonModule } from '@angular/common';
import { Component, Input, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { TablePage } from '../../core/models';

@Component({
  selector: 'app-table-detail',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="crumbs">
      <a routerLink="/tables">← tables</a>
      <span class="muted">/ {{ name }}</span>
    </div>
    <h1 class="mono">{{ name }}</h1>

    @if (page(); as p) {
      <div class="meta muted">
        Showing {{ p.offset + 1 | number }}–{{
          (p.offset + p.rows.length) | number
        }}
        of {{ p.total | number }} rows
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              @for (c of p.columns; track c) {
                <th>{{ c }}</th>
              }
            </tr>
          </thead>
          <tbody>
            @for (row of p.rows; track $index) {
              <tr>
                @for (c of p.columns; track c) {
                  <td>{{ format(row[c]) }}</td>
                }
              </tr>
            }
          </tbody>
        </table>
      </div>
      <div class="pager">
        <button
          class="ghost"
          [disabled]="p.offset === 0"
          (click)="page$(p.offset - p.limit)"
        >
          ← prev
        </button>
        <span class="muted">page {{ pageNum(p) }} / {{ totalPages(p) }}</span>
        <button
          class="ghost"
          [disabled]="p.offset + p.limit >= p.total"
          (click)="page$(p.offset + p.limit)"
        >
          next →
        </button>
      </div>
    } @else if (error()) {
      <div class="card error">{{ error() }}</div>
    } @else {
      <p class="muted">Loading…</p>
    }
  `,
  styles: [
    `
      .crumbs { font-size: 13px; margin-bottom: 6px; }
      h1.mono {
        font-family: 'Menlo', 'Consolas', monospace;
        font-size: 22px;
        margin: 0 0 12px;
      }
      .meta { margin-bottom: 10px; font-size: 12.5px; }
      .table-wrap {
        background: var(--bg-card);
        border: 1px solid var(--rule);
        border-radius: 6px;
        overflow: auto;
        max-height: 65vh;
      }
      table { border-collapse: collapse; width: max-content; min-width: 100%; }
      th, td {
        padding: 8px 14px;
        border-bottom: 1px solid var(--rule);
        font-size: 12.5px;
        white-space: nowrap;
      }
      th {
        background: var(--bg-card-2);
        color: var(--text-muted);
        font-weight: 600;
        position: sticky;
        top: 0;
      }
      td { font-family: 'Menlo', 'Consolas', monospace; }
      .pager {
        display: flex;
        align-items: center;
        gap: 16px;
        margin-top: 14px;
      }
    `,
  ],
})
export class TableDetailComponent {
  @Input({ required: true }) name!: string;

  private api = inject(ApiService);
  private limit = 50;

  page = signal<TablePage | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);

  ngOnInit() {
    this.page$(0);
  }

  page$(offset: number) {
    this.loading.set(true);
    this.api.readTable(this.name, this.limit, Math.max(0, offset)).subscribe({
      next: (p) => {
        this.page.set(p);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.message ?? 'unknown error');
        this.loading.set(false);
      },
    });
  }

  pageNum(p: TablePage): number {
    return Math.floor(p.offset / p.limit) + 1;
  }
  totalPages(p: TablePage): number {
    return Math.max(1, Math.ceil(p.total / p.limit));
  }
  format(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
}
