import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

/**
 * Embeds Superset's native builder inside the strata shell.
 *
 * Approach: load Superset in an iframe so the user gets the full
 * Superset UI — dashboard list, charts list, "+ DASHBOARD", "+ CHART",
 * drag-and-drop editor — without leaving the Angular app. The Superset
 * logo in the top-left corner is covered with a strata-branded mask
 * so the shell feels integrated.
 *
 * IMPORTANT: this component does NOT wrap with <app-nav-shell> —
 * AppComponent already wraps the router outlet in the shell.
 *
 * Cross-origin notes:
 *   - Superset is configured with `X-Frame-Options: ALLOWALL`,
 *     `TALISMAN_ENABLED = False`, and `SESSION_COOKIE_SAMESITE = None`
 *     so the iframe can both load and carry the session cookie.
 *   - We cannot inject CSS into the iframe (cross-origin), so the logo
 *     is covered with an absolute-positioned div on our side.
 *   - To create/edit dashboards or charts the user needs to be logged
 *     in as Admin (default admin / admin). The iframe initially loads
 *     `/login/`; once they sign in, Superset redirects them to the
 *     welcome page inside the same iframe.
 */
@Component({
  selector: 'app-create-dashboard',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="header">
      <div>
        <h1 class="page-title">Dashboards</h1>
        <p class="page-sub">
          Superset's native builder + dashboard list, embedded. Use the
          buttons below to browse, create, or edit. After
          <strong>delete / publish / rename</strong> inside the iframe,
          hit <strong>↻ Reload</strong> — Superset's home page doesn't
          refresh its cards on its own.
        </p>
      </div>
      <div class="header-actions">
        <button class="btn-reload" (click)="reloadIframe()" title="Refresh the embedded view (use after delete / publish / rename)">
          ↻ Reload
        </button>
        <a class="open-out" [href]="welcomeUrl()" target="_blank" rel="noopener">
          Open in new tab ↗
        </a>
      </div>
    </div>

    <!-- Quick actions: bypass Superset's in-iframe + DASHBOARD click which
         (on 3.1.0) sometimes constructs port-less URLs. Direct iframe.src
         navigation to /dashboard/new/ is reliable. -->
    <div class="quick-actions">
      <button class="qa-btn primary" (click)="openHome()">
        🏠 Home
      </button>
      <button class="qa-btn primary" (click)="openDashboardsList()">
        ▦ All dashboards
      </button>
      <button class="qa-btn primary" (click)="openChartsList()">
        ▤ All charts
      </button>
      <span class="qa-spacer"></span>
      <button class="qa-btn create" (click)="newDashboard()">
        + New dashboard
      </button>
      <button class="qa-btn create" (click)="newChart()">
        + New chart
      </button>
    </div>

    <div class="frame">
      <!-- Plain white mask covers Superset's logo (top-left). -->
      <div class="logo-mask"></div>
      <iframe
        [src]="iframeUrl()"
        title="Superset builder"
      ></iframe>
    </div>

    <aside class="note">
      <strong>First time?</strong> The iframe shows Superset's login —
      sign in (default <code>admin</code> / <code>admin</code>) and you'll
      land on the home page with the create buttons. Anything you publish
      lands in strata's Dashboards tab.
    </aside>
  `,
  styles: [
    `
      :host { display: block; }
      .header {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 18px;
        margin-bottom: 14px;
      }
      .page-title {
        margin: 0 0 4px;
        font-size: 26px;
        font-weight: 700;
        letter-spacing: -0.01em;
      }
      .page-sub {
        color: var(--text-muted);
        margin: 0;
        max-width: 720px;
        line-height: 1.5;
      }
      .page-sub a { color: var(--accent); text-decoration: underline; }
      .link-btn {
        background: none; border: none; padding: 0;
        color: var(--accent); text-decoration: underline; cursor: pointer;
        font: inherit;
      }
      .page-sub strong { color: var(--text); font-weight: 600; }

      .header-actions { display: flex; gap: 8px; align-items: center; }

      .quick-actions {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 12px;
        padding: 10px 12px;
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 6px;
      }
      .qa-btn {
        padding: 6px 12px;
        border-radius: 4px;
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        border: 1px solid var(--border);
        background: var(--bg-card);
        color: var(--text);
        transition: border-color 120ms ease, color 120ms ease;
      }
      .qa-btn:hover { border-color: var(--primary); color: var(--primary); }
      .qa-btn.create {
        background: var(--accent);
        color: #fff;
        border-color: var(--accent);
        font-weight: 600;
      }
      .qa-btn.create:hover { background: var(--accent-hover); border-color: var(--accent-hover); color: #fff; }
      .qa-spacer { flex: 1; }
      .btn-reload {
        background: var(--primary);
        border: 1px solid var(--primary);
        color: #fff;
        padding: 7px 14px;
        border-radius: 4px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: background 120ms ease;
      }
      .btn-reload:hover { background: var(--primary-hover); border-color: var(--primary-hover); }
      .open-out {
        font-size: 13px;
        color: var(--primary);
        white-space: nowrap;
        padding: 6px 10px;
        border: 1px solid var(--border-strong);
        border-radius: 4px;
        font-weight: 500;
      }
      .open-out:hover { background: var(--primary); border-color: var(--primary); color: #fff; text-decoration: none; }

      .frame {
        position: relative;
        height: calc(100vh - 200px);
        min-height: 600px;
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow: hidden;
        background: #fff;
      }
      iframe {
        width: 100%;
        height: 100%;
        border: 0;
        display: block;
      }

      .logo-mask {
        position: absolute;
        top: 0;
        left: 0;
        width: 140px;
        height: 56px;
        background: #fff;
        z-index: 5;
        pointer-events: none;
      }

      .note {
        margin-top: 14px;
        padding: 10px 14px;
        background: var(--accent-soft);
        border: 1px solid #B8E5CC;
        border-radius: 6px;
        font-size: 12.5px;
        color: #1F4030;
        line-height: 1.55;
      }
      .note code {
        background: #FFFFFF;
        border: 1px solid #C5E5D2;
        padding: 1px 5px;
        border-radius: 3px;
        font-size: 12px;
        font-family: 'JetBrains Mono', 'Menlo', monospace;
      }
    `,
  ],
})
export class CreateDashboardComponent {
  private sanitizer = inject(DomSanitizer);

  /**
   * Build the iframe URL using the SAME origin as the Angular app
   * (e.g. http://localhost:4200/superset/welcome/). nginx proxies
   * /superset/, /dashboard/, /chart/, /static/, /login/, /api/v1/, …
   * to the Superset container, so this URL is same-origin and the
   * Superset session cookie is sent with iframe requests.
   */
  private buildUrl(suffix: string = '/superset/welcome/'): SafeResourceUrl {
    const sep = suffix.includes('?') ? '&' : '?';
    return this.sanitizer.bypassSecurityTrustResourceUrl(
      `${suffix}${sep}t=${Date.now()}`,
    );
  }

  iframeUrl = signal<SafeResourceUrl>(this.buildUrl());

  welcomeUrl(): string {
    // For the "Open in new tab" button — full URL including origin.
    return `${window.location.origin}/superset/welcome/`;
  }

  reloadIframe(): void {
    this.iframeUrl.set(this.buildUrl());
  }

  /** Open Superset Home in the iframe. */
  openHome(): void {
    this.iframeUrl.set(this.buildUrl('/superset/welcome/'));
  }

  /** Open Superset's dashboards list in the iframe. */
  openDashboardsList(): void {
    this.iframeUrl.set(this.buildUrl('/dashboard/list/'));
  }

  /** Open Superset's charts list in the iframe. */
  openChartsList(): void {
    this.iframeUrl.set(this.buildUrl('/chart/list/'));
  }

  /** Create a new draft dashboard and open the editor in the iframe.
   *  This bypasses Superset 3.1's broken in-iframe + DASHBOARD click,
   *  which constructs port-less URLs. Direct iframe.src navigation is
   *  reliable because the server-side redirect (Host: localhost:4200)
   *  works correctly. */
  newDashboard(): void {
    this.iframeUrl.set(this.buildUrl('/dashboard/new/'));
  }

  /** Open the new-chart wizard in the iframe. */
  newChart(): void {
    this.iframeUrl.set(this.buildUrl('/chart/add'));
  }
}
