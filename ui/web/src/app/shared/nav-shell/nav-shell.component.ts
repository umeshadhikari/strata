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
  styles: [
    `
      .shell { display: grid; grid-template-columns: 230px 1fr; min-height: 100vh; }
      .sidebar {
        background: var(--bg-dark);
        color: #d4c9bd;
        padding: 22px 18px;
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .brand { display: flex; gap: 12px; align-items: center; }
      .brand-mark {
        width: 36px; height: 36px; border-radius: 6px;
        background: var(--primary); color: #fff;
        font-weight: 700; font-size: 22px; font-family: Georgia, serif;
        display: flex; align-items: center; justify-content: center;
      }
      .brand-name { font-weight: 700; font-size: 16px; color: #f5efe6; font-family: Georgia, serif; }
      .brand-sub  { font-size: 11px; color: #a89678; }
      nav { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }
      nav a {
        color: #c9bda9;
        padding: 8px 10px;
        border-radius: 4px;
        display: flex; align-items: center; gap: 10px;
        font-size: 13.5px;
      }
      nav a:hover { background: rgba(255,255,255,0.04); text-decoration: none; }
      nav a.active {
        background: var(--primary);
        color: #fff;
      }
      .icon { width: 16px; text-align: center; opacity: 0.85; }
      .foot { margin-top: auto; font-size: 11px; }
      .foot .muted { color: #80715f; line-height: 1.6; }
      .content { padding: 28px 36px; max-width: 1280px; }
    `,
  ],
})
export class NavShellComponent {}
