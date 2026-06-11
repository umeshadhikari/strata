import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

@Component({
  selector: 'app-nav-shell',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">s</div>
          <div>
            <div class="brand-name">strata</div>
            <div class="brand-sub">data-mart console</div>
          </div>
        </div>
        <nav>
          <a routerLink="/tables" routerLinkActive="active">
            <span class="icon">▤</span> Tables
          </a>
          <a routerLink="/dashboards" routerLinkActive="active">
            <span class="icon">▦</span> Dashboards
          </a>
          <a routerLink="/new-payment" routerLinkActive="active">
            <span class="icon">＋</span> New payment
          </a>
          <a routerLink="/wizard" routerLinkActive="active">
            <span class="icon">✦</span> AI payment wizard
          </a>
        </nav>
        <div class="foot">
          <div class="muted">Source: data_mart</div>
          <div class="muted">Iceberg via Trino</div>
        </div>
      </aside>
      <main class="content">
        <ng-content />
      </main>
    </div>
  `,
  styleUrls: ['./nav-shell.component.scss'],
})
export class NavShellComponent {}
