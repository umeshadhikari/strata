import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'tables',
  },
  {
    path: 'tables',
    loadComponent: () =>
      import('./features/tables/tables.component').then((m) => m.TablesComponent),
    title: 'strata — Tables',
  },
  {
    path: 'tables/:name',
    loadComponent: () =>
      import('./features/tables/table-detail.component').then(
        (m) => m.TableDetailComponent,
      ),
    title: 'strata — Table detail',
  },
  {
    path: 'dashboards',
    loadComponent: () =>
      import('./features/create-dashboard/create-dashboard.component').then(
        (m) => m.CreateDashboardComponent,
      ),
    title: 'strata — Dashboards',
  },
  // Backwards-compat: the old "create" deep link redirects to the unified
  // Dashboards page. The embedded Superset builder covers create + list +
  // view, so a separate /dashboards/new is no longer needed.
  {
    path: 'dashboards/new',
    redirectTo: 'dashboards',
    pathMatch: 'full',
  },
  {
    path: 'new-payment',
    loadComponent: () =>
      import('./features/new-payment/new-payment.component').then(
        (m) => m.NewPaymentComponent,
      ),
    title: 'strata — New payment',
  },
  {
    path: 'wizard',
    loadComponent: () =>
      import('./features/wizard/payment-wizard.component').then(
        (m) => m.PaymentWizardComponent,
      ),
    title: 'strata — AI payment wizard',
  },
  { path: '**', redirectTo: 'tables' },
];
