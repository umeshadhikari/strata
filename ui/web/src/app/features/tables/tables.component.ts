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
  styleUrls: ['./tables.component.scss'],
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
