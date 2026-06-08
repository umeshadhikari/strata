import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { NavShellComponent } from './shared/nav-shell/nav-shell.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, NavShellComponent],
  template: `
    <app-nav-shell>
      <router-outlet />
    </app-nav-shell>
  `,
})
export class AppComponent {}
