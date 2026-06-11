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
  styles: [
    `
      .shell { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }
      .sidebar {
        background: var(--bg-dark);
        color: var(--text-on-dark);
        padding: 24px 18px;
        display: flex;
        flex-direction: column;
        gap: 20px;
      }
      .brand { display: flex; gap: 12px; align-items: center; }
      .brand-mark {
        width: 36px; height: 36px; border-radius: 6px;
        background: var(--accent); color: #fff;
        font-weight: 700; font-size: 20px;
        letter-spacing: -0.02em;
        display: flex; align-items: center; justify-content: center;
      }
      .brand-name {
        font-weight: 700; font-size: 16px;
        letter-spacing: -0.01em;
        color: #fff;
      }
      .brand-sub  {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #8FA0B0;
        margin-top: 2px;
      }
      nav { display: flex; flex-direction: column; gap: 2px; margin-top: 12px; }
      nav a {
        color: var(--text-on-dark);
        padding: 9px 12px;
        border-radius: 4px;
        display: flex; align-items: center; gap: 10px;
        font-size: 13.5px;
        font-weight: 500;
        transition: background 120ms ease, color 120ms ease;
      }
      nav a:hover { background: rgba(255,255,255,0.06); color: #fff; text-decoration: none; }
      nav a.active {
        background: var(--accent);
        color: #fff;
      }
      .icon { width: 16px; text-align: center; opacity: 0.85; }
      .foot { margin-top: auto; font-size: 11px; }
      .foot .muted {
        color: #6B7C8C;
        line-height: 1.6;
      }
      .content { padding: 28px 36px; max-width: 1280px; }
    `,
  ],
})
export class NavShellComponent {}
