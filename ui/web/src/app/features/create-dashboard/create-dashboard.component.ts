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
  styleUrls: ['./create-dashboard.component.scss'],
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
