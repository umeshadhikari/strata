import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { TableSummary } from '../../core/models';

@Component({
  selector: 'app-tables',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <h1>data_mart tables</h1>
    <p class="muted">
      Every base table in the source schema strata replicates into Iceberg.
      Click a row to browse its data.
    </p>

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (error()) {
      <div class="card error">
        <strong>Failed to reach the API.</strong>
        <div class="muted">{{ error() }}</div>
      </div>
    } @else {
      <div class="table-card">
        <table>
          <thead>
            <tr>
              <th>Table</th>
              <th>Kind</th>
              <th class="num">Rows</th>
            </tr>
          </thead>
          <tbody>
            @for (t of tables(); track t.name) {
              <tr [routerLink]="['/tables', t.name]">
                <td class="mono">{{ t.name }}</td>
                <td>
                  <span class="badge" [class]="t.kind">{{ t.kind }}</span>
                </td>
                <td class="num">{{ t.row_count | number }}</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
  styles: [
    `
      h1 { font-family: Georgia, serif; font-size: 26px; margin: 0 0 6px; }
      .table-card {
        background: var(--bg-card);
        border: 1px solid var(--rule);
        border-radius: 6px;
        overflow: hidden;
        margin-top: 16px;
      }
      table { width: 100%; border-collapse: collapse; }
      th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--rule); }
      th { background: var(--bg-card-2); font-weight: 600; font-size: 12.5px; color: var(--text-muted); }
      tbody tr { cursor: pointer; }
      tbody tr:hover { background: var(--bg-card-2); }
      .mono { font-family: 'Menlo', 'Consolas', monospace; font-size: 13px; }
      .num { text-align: right; font-variant-numeric: tabular-nums; }
      .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 11px;
        font-weight: 600;
        background: var(--bg-card-2);
        color: var(--text-body);
        &.fact { background: #f9e7c5; color: #8a5d0d; }
        &.dimension { background: #d8e6dd; color: #2f5a4d; }
      }
    `,
  ],
})
export class TablesComponent {
  private api = inject(ApiService);

  tables = signal<TableSummary[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);

  constructor() {
    this.api.listTables().subscribe({
      next: (res) => {
        this.tables.set(res.tables);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.message ?? 'unknown error');
        this.loading.set(false);
      },
    });
  }
}
