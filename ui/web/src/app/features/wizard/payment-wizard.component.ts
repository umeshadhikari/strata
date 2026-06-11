import { CommonModule } from '@angular/common';
import {
  Component,
  ElementRef,
  NgZone,
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import {
  DebitAccount,
  RailCandidate,
  RailDef,
  RailField,
  RailsRegistry,
  RecurringSuggestion,
  ReusableTemplate,
  SavedBeneficiary,
  WizardFormState,
  WizardToolCall,
  WizardValidation,
} from '../../core/models';

/**
 * A2UI payment wizard — type-and-watch-the-form-morph demo.
 *
 * The user types natural language on the right; the LLM emits tool calls
 * (`set_field`, `select_rail`, `ask`, `explain`) that this component applies
 * to a signal-backed form state. The form on the left re-renders from the
 * rail registry whenever `selectedRail` changes — so picking SEPA shows IBAN
 * + BIC, picking UK FPS shows sort code + account, picking Brazil PIX shows
 * one field. The form definition is fully data-driven (YAML on the server).
 */
type ChatEntry =
  | { kind: 'user'; text: string }
  | { kind: 'tool'; call: WizardToolCall }
  | { kind: 'error'; text: string }
  | { kind: 'system'; text: string };

interface SubmitConfirmation {
  id: number;
  amount: string;
  beneficiary: string;
  rail: string;
  reference?: string;
  reminderDays?: number;          // detected cadence, used by the learn-toast
  reminderSet?: boolean;          // user accepted the reminder
}

@Component({
  selector: 'app-payment-wizard',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="header">
      <div>
        <h1 class="page-title">AI payment wizard</h1>
        <p class="page-sub">
          Describe the payment in plain English. Qwen 2.5 (local, via Ollama)
          extracts the details, picks a rail, and patches the form. Switch
          country or currency and the form morphs in real time —
          IBAN + BIC for SEPA, sort code for UK FPS, routing + account for US
          ACH, IFSC or UPI for India, a single key for Brazil PIX, full
          beneficiary address for SWIFT.
        </p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" (click)="reset()">↻ Start over</button>
      </div>
    </div>

    @if (loadError()) {
      <div class="card error">
        Could not load rail registry: {{ loadError() }}
      </div>
    } @else if (!registry()) {
      <div class="card muted">Loading rail registry…</div>
    } @else {
      <div class="split">
        <!-- ─────────── Left: dynamic form ─────────── -->
        <section class="pane form-pane">
          <header class="pane-head">
            <h2>Payment</h2>
            <div class="head-rail-cluster">
              <button
                type="button"
                class="rail-pill"
                [class.clickable]="!!selectedRail() && candidates().length > 0"
                (click)="toggleRailComparison()"
              >
                @if (selectedRail(); as r) {
                  {{ r.display_name }}
                  <span class="rail-region">· {{ r.region }}</span>
                  @if (candidates().length > 0) {
                    <span class="rail-compare">▾</span>
                  }
                } @else {
                  <span class="muted">Rail not selected</span>
                }
              </button>
              @if (railLocked()) {
                <button
                  type="button"
                  class="rail-lock"
                  title="You picked this rail directly. Click to unlock and let the AI re-pick."
                  (click)="unlockRail()"
                >
                  <span class="rail-lock-icon">🔒</span>
                  <span class="rail-lock-text">Locked</span>
                  <span class="rail-lock-x">×</span>
                </button>
              }
            </div>
          </header>

          @if (showRailComparison() && candidates().length > 0) {
            <div class="rail-compare-panel">
              <div class="rail-compare-head">Compare eligible rails</div>
              @for (cand of candidates(); track cand.rail_id) {
                @if (registry()?.rails?.[cand.rail_id]; as railDef) {
                  <div class="rail-compare-row"
                       [class.rail-current]="formState()['rail_id'] === cand.rail_id">
                    <div class="rcr-head">
                      <span class="rcr-name">{{ railDef.display_name }}</span>
                      @if (formState()['rail_id'] === cand.rail_id) {
                        <span class="rcr-current">CURRENT</span>
                      } @else {
                        <button
                          type="button"
                          class="rcr-switch"
                          (click)="manuallySelectRail(cand.rail_id); showRailComparison.set(false)"
                        >
                          Switch
                        </button>
                      }
                    </div>
                    <div class="rcr-meta">
                      <span><strong>Speed:</strong> {{ getRailField(cand.rail_id, 'speed_text') }}</span>
                      <span><strong>Cost:</strong>
                        {{ cand.cost?.headline ?? getRailField(cand.rail_id, 'cost_text') }}
                      </span>
                      @if (getRailField(cand.rail_id, 'max_amount'); as maxAmt) {
                        <span><strong>Max:</strong> {{ maxAmt }}</span>
                      }
                    </div>
                    @if (cand.availability) {
                      <div class="rcr-avail">
                        <span class="avail-pill" [class]="'avail-' + cand.availability.urgency">
                          {{ cand.availability.status_text }}
                        </span>
                        <span class="rcr-settles">
                          Settles by {{ formatSettlement(cand.availability.settles_by) }}
                        </span>
                      </div>
                    }
                    <div class="rcr-why">{{ cand.why }}</div>
                  </div>
                }
              }
            </div>
          }

          @if (selectedRail()?.summary; as s) {
            <p class="rail-summary">{{ s }}</p>
          }

          @if (isEmptyStart()) {
            <section class="start-screen">
              <div class="start-head">
                <h3 class="start-title">Start your payment</h3>
                <p class="start-sub">
                  Pick a rail to go straight to its form, or describe the payment
                  on the right and the AI will choose for you.
                </p>
              </div>
              <div class="rail-grid">
                @for (rail of allRailEntries(); track rail.id) {
                  <button
                    type="button"
                    class="rail-tile"
                    [style.--tile-accent]="rail.accent"
                    (click)="pickRailDirectly(rail.id)"
                  >
                    <div class="rt-top">
                      <span class="rt-icon" [style.background]="rail.accent">
                        {{ rail.icon }}
                      </span>
                      <div class="rt-titles">
                        <div class="rt-name">{{ rail.name }}</div>
                        <div class="rt-region">{{ rail.region }}</div>
                      </div>
                    </div>
                    <div class="rt-meta">
                      <span class="rt-pill" [class.rt-pill-247]="rail.avail247">
                        @if (rail.avail247) { ⚡ } @else { ⏱ }
                        {{ rail.speed }}
                      </span>
                      <span class="rt-cost">{{ rail.cost }}</span>
                    </div>
                  </button>
                }
              </div>
              <div class="start-foot">
                <span class="start-or">— or —</span>
                <span class="start-chat-hint">
                  Type in the chat: <em>"send €2500 to Acme in Germany"</em>
                </span>
              </div>
            </section>
          }

          @if (!isEmptyStart() && candidates().length > 1 && !selectedRail()) {
            <div class="picker">
              <div class="picker-label">Suggested rails:</div>
              @for (cand of candidates(); track cand.rail_id) {
                <button
                  type="button"
                  class="cand"
                  [class.cand-over-limit]="cand.exceeds_limit"
                  (click)="manuallySelectRail(cand.rail_id)"
                >
                  <div class="cand-head">
                    <span class="cand-name">{{ railName(cand.rail_id) }}</span>
                    @if (cand.availability) {
                      <span class="avail-pill" [class]="'avail-' + cand.availability.urgency">
                        {{ cand.availability.status_text }}
                      </span>
                    }
                  </div>
                  <div class="cand-why">{{ cand.why }}</div>
                  @if (cand.cost) {
                    <div class="cand-cost">
                      <strong>Est. cost:</strong> {{ cand.cost.headline }}
                    </div>
                  }
                </button>
              }
            </div>
          }

          <div class="fields">
            @for (field of visibleFields(); track field.id) {
              <div class="field" [class.is-derived]="isDerived(field.id)">
                <label [for]="'f-' + field.id">
                  {{ field.label }}
                  @if (field.required) {
                    <span class="req">*</span>
                  }
                  @if (confidence(field.id); as c) {
                    <span class="conf" [class.low]="c < 0.7">
                      {{ (c * 100) | number:'1.0-0' }}% conf.
                    </span>
                  }
                </label>

                @switch (field.type) {
                  @case ('account_picker') {
                    <div class="bene-wrap">
                      <input
                        [id]="'f-' + field.id"
                        type="text"
                        [value]="accountQuery()"
                        (input)="onAccountInput($any($event.target).value)"
                        (focus)="onAccountFocus()"
                        (blur)="onAccountBlur()"
                        placeholder="Start typing — OPS, EUR, treasury, …"
                        autocomplete="off"
                      />
                      @if (accountDropdownOpen() && filteredAccounts().length) {
                        <div class="bene-dropdown">
                          @for (acc of filteredAccounts(); track acc.id) {
                            <button
                              type="button"
                              class="bene-row"
                              [class.bene-row-selected]="isSelectedAccount(acc)"
                              (mousedown)="applyAccount(acc)"
                            >
                              <div class="bene-name">
                                {{ acc.code }} · {{ acc.name }}
                              </div>
                              <div class="bene-meta">
                                {{ acc.currency }} ·
                                bal {{ acc.balance | number:'1.2-2' }} ·
                                {{ acc.identifier }}
                              </div>
                            </button>
                          }
                        </div>
                      }
                    </div>
                    @if (selectedAccount(); as a) {
                      <div class="acc-pill" [class.acc-low]="amountExceedsBalance(a)">
                        Balance:
                        <strong>{{ a.balance | number:'1.2-2' }} {{ a.currency }}</strong>
                        · {{ a.identifier }}
                        @if (amountExceedsBalance(a)) {
                          <span class="acc-warn"> ⚠ amount exceeds balance</span>
                        }
                      </div>
                    }
                  }
                  @case ('beneficiary_typeahead') {
                    <div class="bene-wrap">
                      <input
                        [id]="'f-' + field.id"
                        type="text"
                        [value]="(formState()[field.id] ?? '') + ''"
                        (input)="onBeneficiaryInput($any($event.target).value)"
                        (focus)="onBeneficiaryFocus()"
                        (blur)="onBeneficiaryBlur()"
                        [attr.maxlength]="field.max_length || null"
                        [placeholder]="field.placeholder || ''"
                        autocomplete="off"
                      />
                      @if (beneficiarySuggestions().length && beneficiaryDropdownOpen()) {
                        <div class="bene-dropdown">
                          @for (b of beneficiarySuggestions(); track b.id) {
                            <button
                              type="button"
                              class="bene-row"
                              (mousedown)="applyBeneficiary(b)"
                            >
                              <div class="bene-name">
                                {{ b.name }}
                                <span class="bene-freq" [ngClass]="freqClass(b.payment_count)">
                                  {{ freqLabel(b.payment_count) }}
                                </span>
                              </div>
                              <div class="bene-meta">
                                {{ b.country }} ·
                                <strong>{{ railName(b.preferred_rail) }}</strong> ·
                                last paid {{ b.last_paid }} ·
                                {{ b.payment_count }} prior
                              </div>
                              @if (b.bank_alias) {
                                <div class="bene-bank">via {{ b.bank_alias }}</div>
                              }
                            </button>
                          }
                        </div>
                      }
                    </div>
                  }
                  @case ('textarea') {
                    <textarea
                      [id]="'f-' + field.id"
                      [value]="(formState()[field.id] ?? '') + ''"
                      (input)="patchInput(field, $any($event.target).value)"
                      [attr.maxlength]="field.max_length || null"
                      [placeholder]="field.placeholder || ''"
                      rows="2"
                    ></textarea>
                  }
                  @case ('radio') {
                    <div class="radio-row">
                      @for (opt of field.options || []; track opt.value) {
                        <label class="radio-opt">
                          <input
                            type="radio"
                            [name]="field.id"
                            [value]="opt.value"
                            [checked]="formState()[field.id] === opt.value"
                            (change)="patchInput(field, opt.value)"
                          />
                          {{ opt.label }}
                        </label>
                      }
                    </div>
                  }
                  @case ('number') {
                    <input
                      [id]="'f-' + field.id"
                      type="number"
                      [value]="formState()[field.id] ?? ''"
                      (input)="patchInput(field, $any($event.target).value)"
                      [placeholder]="field.placeholder || ''"
                    />
                  }
                  @default {
                    <input
                      [id]="'f-' + field.id"
                      type="text"
                      [value]="(formState()[field.id] ?? '') + ''"
                      (input)="patchInput(field, $any($event.target).value)"
                      [attr.maxlength]="field.max_length || null"
                      [placeholder]="field.placeholder || ''"
                    />
                  }
                }

                @if (field.help) {
                  <div class="help">{{ field.help }}</div>
                }
                @if (fieldError(field.id); as err) {
                  <div class="field-err">{{ err }}</div>
                }
              </div>
            }
          </div>

          <div class="submit-row">
            <button
              class="primary"
              [class.collapsed]="!!submitResult()"
              [disabled]="!canSubmit() || submitting() || !!submitResult()"
              (click)="submit()"
            >
              @if (submitResult()) {
                <span class="check">✓</span>
              } @else {
                {{ submitting() ? 'Submitting…' : 'Submit payment' }}
              }
            </button>
          </div>
        </section>

        @if (submitResult(); as r) {
          <div class="confirm-overlay" aria-live="polite">
            <div class="confirm-card">
              <div class="confirm-icon">✓</div>
              <h2 class="confirm-title">Payment #{{ r.id }} recorded</h2>
              <p class="confirm-body">
                <strong>{{ r.amount }}</strong> to
                <strong>{{ r.beneficiary }}</strong>
                via <strong>{{ r.rail }}</strong>.
                @if (r.reference) {
                  <br />Reference: <span class="confirm-ref">{{ r.reference }}</span>
                }
              </p>
              @if (r.reminderDays && !r.reminderSet) {
                <div class="confirm-reminder">
                  <span>This payment looks like it recurs every ~{{ r.reminderDays }} days.</span>
                  <button class="reminder-btn" (click)="acceptReminder()">
                    Remind me in {{ r.reminderDays }} days
                  </button>
                </div>
              } @else if (r.reminderSet) {
                <div class="confirm-reminder-set">
                  ✓ Reminder set for {{ r.reminderDays }} days from now
                </div>
              }
              <div class="confirm-actions">
                <button class="primary" (click)="sendAnother()">Send another</button>
              </div>
            </div>
          </div>
        }

        <!-- ─────────── Right: chat strip ─────────── -->
        <section class="pane chat-pane">
          <header class="pane-head">
            <h2>Assistant</h2>
            <div class="model-picker" [class.open]="showModelPicker()">
              <button
                type="button"
                class="model-btn"
                (click)="toggleModelPicker()"
                title="Switch Ollama model"
              >
                <span class="model-name">{{ activeModel() || 'no model' }}</span>
                <span class="model-caret">⚙</span>
              </button>
              @if (showModelPicker()) {
                <div class="model-pop">
                  <div class="model-pop-head">Switch model</div>
                  @if (availableModels().length === 0) {
                    <div class="model-empty">
                      No chat models found in Ollama. Pull one with
                      <code>docker exec ollama ollama pull qwen2.5:7b</code>
                    </div>
                  } @else {
                    @for (m of availableModels(); track m.name) {
                      <button
                        type="button"
                        class="model-row"
                        [class.model-active]="m.name === activeModel()"
                        [disabled]="switchingModel()"
                        (click)="switchModel(m.name)"
                      >
                        <span class="model-row-name">{{ m.name }}</span>
                        <span class="model-row-size">{{ formatBytes(m.size) }}</span>
                        @if (m.name === activeModel()) {
                          <span class="model-check">✓</span>
                        }
                      </button>
                    }
                  }
                  <div class="model-pop-foot">
                    <button type="button" class="model-refresh" (click)="loadModels()">↻ Refresh</button>
                  </div>
                </div>
              }
            </div>
          </header>

          <div #log class="chat-log">
            @if (chat().length === 0) {
              @if (recurringSuggestions().length > 0) {
                <div class="empty">
                  <div class="empty-title">Due for you</div>
                  @for (s of recurringSuggestions(); track s.beneficiary_id) {
                    <button
                      type="button"
                      class="suggestion-card"
                      [class.overdue]="s.is_overdue"
                      (click)="applySuggestion(s)"
                    >
                      <div class="sg-head">
                        <span class="sg-name">{{ s.beneficiary }}</span>
                        <span class="sg-conf">{{ (s.confidence * 100).toFixed(0) }}% conf.</span>
                      </div>
                      <div class="sg-amount">
                        {{ formatAmount(s.suggested_amount, s.currency) }}
                        via {{ railName(s.rail_id) }}
                      </div>
                      <div class="sg-reason">{{ s.reason }}</div>
                    </button>
                  }
                </div>
              }
              @if (reusableTemplates().length > 0) {
                <div class="empty">
                  <div class="empty-title">Templates · quick reuse</div>
                  @for (t of reusableTemplates(); track t.beneficiary_id) {
                    <button
                      type="button"
                      class="template-chip"
                      (click)="applyTemplate(t)"
                    >
                      <div class="tpl-head">
                        <span class="tpl-name">{{ t.beneficiary }}</span>
                        <span class="tpl-rail">{{ railName(t.rail_id) }}</span>
                      </div>
                      <div class="tpl-meta">
                        {{ t.country }} · last paid {{ t.last_paid }} · {{ t.payment_count }} prior
                      </div>
                    </button>
                  }
                </div>
              }
              @if (recurringSuggestions().length === 0 && reusableTemplates().length === 0) {
                <div class="empty">
                  <div class="empty-title">Try one of these</div>
                  @for (preset of demoPresets; track preset) {
                    <button
                      type="button"
                      class="preset-chip"
                      (click)="loadPreset(preset)"
                    >
                      {{ preset }}
                    </button>
                  }
                </div>
              }
            }
            @for (entry of chat(); track $index) {
              @switch (entry.kind) {
                @case ('user') {
                  <div class="line user">
                    <span class="bullet">›</span>
                    <span>{{ entry.text }}</span>
                  </div>
                }
                @case ('tool') {
                  <div class="line tool" [ngClass]="entry.call.name">
                    <span class="bullet">{{ toolBullet(entry.call.name) }}</span>
                    <span [innerHTML]="renderTool(entry.call)"></span>
                  </div>
                }
                @case ('error') {
                  <div class="line error">
                    <span class="bullet">!</span>
                    <span>{{ entry.text }}</span>
                  </div>
                }
                @case ('system') {
                  <div class="line system">
                    <span class="bullet">·</span>
                    <span>{{ entry.text }}</span>
                  </div>
                }
              }
            }
            @if (sending()) {
              <div class="line tool thinking-line">
                <span class="bullet">…</span>
                <span class="thinking-label">thinking</span>
                <span class="thinking-dots" aria-hidden="true">
                  <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                </span>
              </div>
            }
          </div>

          <form class="chat-input" (ngSubmit)="send()">
            <input
              type="text"
              [(ngModel)]="draft"
              name="draft"
              [placeholder]="voiceState() === 'listening'
                ? 'Listening… speak now'
                : 'Describe the payment, or ask \\'what is …\\''"
              [disabled]="sending()"
              autocomplete="off"
            />
            @if (voiceSupported()) {
              <button
                type="button"
                class="mic-btn"
                [class.mic-listening]="voiceState() === 'listening'"
                [disabled]="sending()"
                [title]="voiceState() === 'listening' ? 'Stop dictation' : 'Start dictation'"
                (click)="toggleVoice()"
              >
                @if (voiceState() === 'listening') {
                  <span class="mic-wave" aria-hidden="true">
                    <span></span><span></span><span></span>
                  </span>
                } @else {
                  <span aria-hidden="true">🎤</span>
                }
              </button>
            }
            <button type="submit" [disabled]="sending() || !draft().trim()">
              Send
            </button>
          </form>

          @if (lastError(); as e) {
            <div class="footer-err">{{ e }}</div>
          }
        </section>
      </div>
    }
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
      .page-title { margin: 0 0 4px; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; }
      .page-sub { color: var(--text-muted); margin: 0; max-width: 760px; line-height: 1.5; }
      .header-actions { display: flex; gap: 8px; }
      .btn-secondary {
        background: var(--bg-card);
        border: 1px solid var(--border);
        color: var(--text);
        padding: 7px 13px;
        border-radius: 4px;
        font-size: 13px;
        cursor: pointer;
      }
      .btn-secondary:hover { border-color: var(--primary); color: var(--primary); }

      .card.error, .error {
        background: #FDECEA;
        border: 1px solid #F2B8AF;
        color: #8A2316;
        padding: 12px 14px;
        border-radius: 6px;
      }
      .card.muted { color: var(--text-muted); }

      .split {
        display: grid;
        grid-template-columns: 1.15fr 1fr;
        gap: 18px;
        align-items: stretch;
      }
      @media (max-width: 1100px) {
        .split { grid-template-columns: 1fr; }
      }

      .pane {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 18px 20px;
        display: flex;
        flex-direction: column;
        min-height: 560px;
      }
      .pane-head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 10px;
      }
      .pane-head h2 {
        margin: 0;
        font-size: 16px;
        font-weight: 600;
        letter-spacing: -0.01em;
      }
      .rail-pill {
        font-size: 12.5px;
        color: var(--primary);
        background: var(--primary-soft);
        padding: 3px 10px;
        border-radius: 12px;
        font-weight: 500;
      }
      .rail-region { color: var(--text-muted); }

      .rail-summary {
        margin: 0 0 14px;
        padding: 8px 12px;
        background: var(--bg-card-2);
        border-left: 3px solid var(--accent);
        font-size: 12.5px;
        color: var(--text-body);
        line-height: 1.5;
      }

      .picker {
        display: flex; flex-direction: column; gap: 6px;
        margin-bottom: 14px;
        padding: 10px 12px;
        background: var(--accent-soft);
        border: 1px solid #B8E5CC;
        border-radius: 6px;
      }
      .picker-label { font-size: 12px; color: #1F4030; font-weight: 600; }
      .cand {
        text-align: left;
        background: #fff;
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 8px 10px;
        cursor: pointer;
      }
      .cand:hover { border-color: var(--accent); }
      .cand.cand-over-limit { opacity: 0.65; }
      .cand.cand-over-limit::after {
        content: "Over limit";
        display: inline-block;
        margin-left: 6px;
        padding: 1px 6px;
        font-size: 10px;
        border-radius: 9999px;
        background: var(--warning-soft, #fff4d6);
        color: var(--warning, #b07900);
      }
      .cand-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cand-name { font-weight: 600; font-size: 13px; }
      .cand-why { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
      .cand-cost { font-size: 12px; color: var(--text-body); margin-top: 4px; }
      .cand-cost strong { color: var(--text-muted); font-weight: 600; }

      /* ── Availability pills (used in both picker + comparison) ── */
      .avail-pill {
        display: inline-block;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
        border-radius: 9999px;
        white-space: nowrap;
      }
      .avail-pill.avail-now {
        background: var(--accent-soft, #def5e4);
        color: var(--accent, #0a8a3f);
      }
      .avail-pill.avail-today {
        background: var(--accent-soft, #def5e4);
        color: var(--accent, #0a8a3f);
      }
      .avail-pill.avail-today_soon {
        background: var(--warning-soft, #fff4d6);
        color: var(--warning, #b07900);
        animation: avail-pulse 1.8s ease-in-out infinite;
      }
      .avail-pill.avail-next_window {
        background: var(--bg-card-2, #f1f3f5);
        color: var(--text-muted, #5f6b7a);
      }
      @keyframes avail-pulse {
        0%, 100% { opacity: 1; }
        50%      { opacity: 0.55; }
      }
      .rcr-avail {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 6px;
        font-size: 12px;
      }
      .rcr-settles { color: var(--text-muted); }

      .fields { display: flex; flex-direction: column; gap: 14px; }
      .field {
        display: flex;
        flex-direction: column;
        gap: 4px;
        animation: fade-in 200ms ease;
      }
      .field.is-derived input { background: var(--accent-soft); }
      .field > label {
        font-size: 11.5px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        display: flex; gap: 6px; align-items: center;
      }
      .req { color: var(--error); }
      .conf {
        margin-left: auto;
        font-size: 10.5px;
        font-weight: 500;
        color: var(--accent);
        text-transform: none;
        letter-spacing: 0;
      }
      .conf.low { color: var(--warning); }
      .help { font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }
      .field-err { font-size: 11.5px; color: var(--error); }
      /* Radio options stack vertically as full-width pills.
         Robust on any panel width; no overflow possible. */
      .radio-row {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 2px;
      }
      .radio-opt {
        display: flex;
        align-items: center;
        gap: 10px;
        width: 100%;
        box-sizing: border-box;
        padding: 8px 12px;
        font-size: 13px;
        font-weight: 500;
        text-transform: none;
        letter-spacing: 0;
        color: var(--text-body);
        line-height: 1.35;
        cursor: pointer;
        border: 1px solid var(--border, #d8dde3);
        border-radius: 4px;
        background: #fff;
        transition: border-color 120ms ease, background 120ms ease;
      }
      .radio-opt:hover { border-color: var(--accent, #0a8a3f); }
      .radio-opt:has(input:checked) {
        border-color: var(--accent, #0a8a3f);
        background: var(--accent-soft, #def5e4);
      }
      /* Reset the global "form input" full-width block style for radios.
         Without this, input[type="radio"] inherits width:100%; display:block
         from the generic .field input rule and pushes the label text out of
         the pill. */
      .radio-opt input[type="radio"] {
        margin: 0;
        flex-shrink: 0;
        width: auto !important;
        height: auto !important;
        display: inline-block;
        appearance: auto;
      }
      @keyframes fade-in {
        from { opacity: 0; transform: translateY(-4px); }
        to { opacity: 1; transform: none; }
      }

      /* ─── Animated "thinking" indicator ─── */
      .thinking-line {
        display: flex;
        align-items: center;
        gap: 6px;
        animation: fade-in 200ms ease;
      }
      .thinking-label {
        background: linear-gradient(
          90deg,
          var(--text-muted) 0%,
          var(--accent) 50%,
          var(--text-muted) 100%
        );
        background-size: 200% 100%;
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        color: transparent;
        animation: thinking-shimmer 1.8s ease-in-out infinite;
      }
      .thinking-dots {
        display: inline-flex;
        gap: 3px;
        margin-left: 2px;
      }
      .thinking-dots .dot {
        width: 4px;
        height: 4px;
        border-radius: 50%;
        background: var(--accent);
        opacity: 0.35;
        animation: thinking-bounce 1.2s ease-in-out infinite;
      }
      .thinking-dots .dot:nth-child(2) { animation-delay: 0.15s; }
      .thinking-dots .dot:nth-child(3) { animation-delay: 0.30s; }
      @keyframes thinking-shimmer {
        0%   { background-position: 100% 0; }
        100% { background-position: -100% 0; }
      }
      @keyframes thinking-bounce {
        0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
        40%           { transform: translateY(-3px); opacity: 1; }
      }
      @media (prefers-reduced-motion: reduce) {
        .thinking-label,
        .thinking-dots .dot { animation: none; }
        .thinking-label { color: var(--text-muted); -webkit-text-fill-color: var(--text-muted); }
        .thinking-dots .dot { opacity: 0.6; }
      }

      /* ─── Debit account picker ─── */
      .acc-pill {
        margin-top: 6px;
        padding: 6px 10px;
        background: var(--bg-card-2);
        border-radius: 4px;
        font-size: 12px;
        color: var(--text-body);
      }
      .acc-pill strong { color: var(--text); }
      .acc-low { background: #FDECEA; color: #8A2316; }
      .acc-warn { color: var(--error); font-weight: 600; margin-left: 6px; }

      /* ─── Beneficiary typeahead ─── */
      .bene-wrap { position: relative; }
      .bene-dropdown {
        position: absolute;
        top: 100%;
        left: 0; right: 0;
        background: #fff;
        border: 1px solid var(--border);
        border-radius: 4px;
        box-shadow: 0 6px 16px rgba(5, 28, 44, 0.08);
        z-index: 10;
        max-height: 280px;
        overflow-y: auto;
        margin-top: 2px;
      }
      .bene-row {
        display: block;
        width: 100%;
        text-align: left;
        background: #fff;
        border: 0;
        border-bottom: 1px solid var(--border);
        padding: 8px 12px;
        cursor: pointer;
      }
      .bene-row:last-child { border-bottom: none; }
      .bene-row:hover { background: var(--accent-soft); }
      .bene-row-selected { background: var(--accent-soft); }
      .bene-row-selected .bene-name::before { content: "✓ "; color: var(--accent-hover); }
      .bene-name {
        font-weight: 600; font-size: 13px; color: var(--text);
        display: flex; align-items: center; gap: 8px;
      }
      .bene-freq {
        padding: 1px 7px; border-radius: 10px;
        font-size: 10px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.05em;
      }
      .bene-freq.frequent { background: var(--accent-soft); color: #0E6B3A; }
      .bene-freq.regular  { background: var(--bg-card-2); color: var(--text-muted); }
      .bene-freq.new      { background: var(--primary-soft); color: var(--primary); }
      .bene-meta {
        font-size: 11.5px;
        color: var(--text-muted);
        margin-top: 2px;
        font-family: 'JetBrains Mono', 'Menlo', monospace;
      }

      .submit-row {
        margin-top: auto;
        padding-top: 16px;
        display: flex;
        gap: 12px;
        align-items: center;
      }
      .primary {
        background: var(--accent);
        color: #fff;
        border: none;
        padding: 10px 22px;
        border-radius: 4px;
        font-weight: 600;
        cursor: pointer;
        font-size: 14px;
      }
      .primary:hover { background: var(--accent-hover); }
      .primary:disabled { background: var(--border); color: var(--text-muted); cursor: not-allowed; }
      /* Submit collapses to a circle with a checkmark when payment is recorded */
      .primary.collapsed {
        width: 44px; height: 44px;
        padding: 0; border-radius: 50%;
        background: var(--accent); color: #fff;
        font-size: 22px; line-height: 1;
        animation: collapse-btn 350ms ease forwards;
      }
      .primary .check { font-size: 22px; line-height: 1; }
      @keyframes collapse-btn {
        from { width: 156px; border-radius: 4px; opacity: 0.7; }
        to   { width: 44px;  border-radius: 50%; opacity: 1; }
      }

      /* Slide-up confirmation card */
      .confirm-overlay {
        position: fixed;
        inset: 0;
        background: rgba(11, 31, 51, 0.35);
        display: flex;
        align-items: flex-end;
        justify-content: center;
        padding: 0 24px 36px;
        z-index: 50;
        animation: fade-bg 250ms ease;
      }
      .confirm-card {
        width: 100%;
        max-width: 520px;
        background: var(--bg-card);
        border-radius: 12px;
        padding: 28px 28px 24px;
        text-align: center;
        box-shadow: 0 16px 48px rgba(5, 28, 44, 0.18);
        animation: slide-up 450ms cubic-bezier(0.16, 1, 0.3, 1);
      }
      .confirm-icon {
        width: 56px; height: 56px;
        margin: 0 auto 14px;
        border-radius: 50%;
        background: var(--accent);
        color: #fff;
        font-size: 30px; line-height: 56px;
        font-weight: 700;
        animation: pop 500ms cubic-bezier(0.34, 1.56, 0.64, 1) 100ms backwards;
      }
      .confirm-title {
        margin: 0 0 8px;
        font-size: 20px; font-weight: 700;
        letter-spacing: -0.01em;
      }
      .confirm-body {
        color: var(--text-body);
        font-size: 14px;
        line-height: 1.55;
        margin: 0 0 18px;
      }
      .confirm-ref {
        display: inline-block;
        background: var(--bg-card-2);
        padding: 1px 8px;
        border-radius: 4px;
        font-family: 'JetBrains Mono', 'Menlo', monospace;
        font-size: 12.5px;
      }
      .confirm-actions { display: flex; justify-content: center; gap: 10px; }

      @keyframes slide-up {
        from { transform: translateY(40px); opacity: 0; }
        to   { transform: translateY(0); opacity: 1; }
      }
      @keyframes fade-bg {
        from { background: rgba(11, 31, 51, 0); }
        to   { background: rgba(11, 31, 51, 0.35); }
      }
      @keyframes pop {
        from { transform: scale(0.4); opacity: 0; }
        to   { transform: scale(1); opacity: 1; }
      }

      .chat-pane { background: var(--bg-card); }

      /* ─── Ollama model picker ─── */
      .model-picker { position: relative; }
      .model-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        font-size: 12px;
        font-family: inherit;
        color: var(--text-muted, #5f6b7a);
        background: var(--bg-card-2, #f1f3f5);
        border: 1px solid var(--border, #d8dde3);
        border-radius: 9999px;
        cursor: pointer;
        transition: border-color 120ms ease, background 120ms ease;
      }
      .model-btn:hover {
        border-color: var(--accent, #0a8a3f);
        color: var(--text-body, #1f2933);
      }
      .model-name { font-weight: 600; }
      .model-caret { font-size: 12px; opacity: 0.8; }
      .model-picker.open .model-caret { color: var(--accent, #0a8a3f); }

      .model-pop {
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        z-index: 100;
        width: 280px;
        background: #fff;
        border: 1px solid var(--border, #d8dde3);
        border-radius: 6px;
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
        overflow: hidden;
        animation: pop-fade-in 140ms ease;
      }
      @keyframes pop-fade-in {
        from { opacity: 0; transform: translateY(-4px); }
        to   { opacity: 1; transform: none; }
      }
      .model-pop-head {
        padding: 10px 12px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-muted, #5f6b7a);
        background: var(--bg-card-2, #f7f9fb);
        border-bottom: 1px solid var(--border, #d8dde3);
      }
      .model-empty {
        padding: 12px;
        font-size: 12px;
        color: var(--text-muted, #5f6b7a);
        line-height: 1.4;
      }
      .model-empty code {
        display: block;
        margin-top: 6px;
        padding: 4px 6px;
        background: var(--bg-card-2, #f1f3f5);
        border-radius: 3px;
        font-size: 11px;
        font-family: ui-monospace, Menlo, Monaco, monospace;
      }
      .model-row {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        padding: 9px 12px;
        background: #fff;
        border: none;
        border-bottom: 1px solid var(--bg-card-2, #eef0f3);
        font-family: inherit;
        font-size: 12.5px;
        color: var(--text-body, #1f2933);
        cursor: pointer;
        text-align: left;
        transition: background 100ms ease;
      }
      .model-row:hover:not(:disabled) { background: var(--bg-card-2, #f7f9fb); }
      .model-row:disabled { cursor: progress; opacity: 0.6; }
      .model-row.model-active {
        background: var(--accent-soft, #def5e4);
        color: var(--accent, #0a8a3f);
        font-weight: 600;
      }
      .model-row-name { flex: 1; }
      .model-row-size {
        font-size: 11px;
        color: var(--text-muted, #5f6b7a);
        font-variant-numeric: tabular-nums;
      }
      .model-row.model-active .model-row-size { color: var(--accent, #0a8a3f); }
      .model-check { color: var(--accent, #0a8a3f); font-weight: 700; }
      .model-pop-foot {
        padding: 6px 12px;
        background: var(--bg-card-2, #f7f9fb);
        border-top: 1px solid var(--border, #d8dde3);
        text-align: right;
      }
      .model-refresh {
        background: none;
        border: none;
        font-size: 11px;
        color: var(--text-muted, #5f6b7a);
        cursor: pointer;
        font-family: inherit;
      }
      .model-refresh:hover { color: var(--accent, #0a8a3f); }
      .chat-log {
        flex: 1;
        overflow-y: auto;
        font-size: 13px;
        line-height: 1.5;
        padding: 4px 2px 12px;
        display: flex; flex-direction: column; gap: 6px;
        min-height: 380px;
      }
      .empty {
        display: flex;
        flex-direction: column;
        gap: 6px;
        background: var(--bg-card-2);
        padding: 12px 14px;
        border-radius: 6px;
      }
      .empty-title {
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-muted);
        margin-bottom: 4px;
      }
      .preset-chip {
        text-align: left;
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 7px 11px;
        font-size: 12.5px;
        font-family: inherit;
        color: var(--text);
        cursor: pointer;
        line-height: 1.4;
        transition: border-color 120ms ease, background 120ms ease;
      }
      .preset-chip:hover {
        border-color: var(--accent);
        background: var(--accent-soft);
      }

      /* ─── Recurring-suggestion cards ─── */
      .suggestion-card {
        text-align: left;
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-left: 3px solid var(--accent);
        border-radius: 4px;
        padding: 10px 12px;
        cursor: pointer;
        font-family: inherit;
        transition: background 120ms ease, border-color 120ms ease;
      }
      .suggestion-card:hover { background: var(--accent-soft); }
      .suggestion-card.overdue { border-left-color: var(--error); }
      .sg-head {
        display: flex; justify-content: space-between; align-items: baseline;
        margin-bottom: 4px;
      }
      .sg-name { font-weight: 600; font-size: 13.5px; color: var(--text); }
      .sg-conf {
        font-size: 10.5px; font-weight: 600;
        color: var(--accent-hover);
        background: var(--accent-soft);
        padding: 1px 6px; border-radius: 8px;
      }
      .sg-amount { font-size: 12.5px; color: var(--text); margin-bottom: 4px; }
      .sg-reason {
        font-size: 11.5px; color: var(--text-muted);
        font-family: 'JetBrains Mono', 'Menlo', monospace;
      }

      .template-chip {
        text-align: left;
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 8px 12px;
        cursor: pointer;
        font-family: inherit;
      }
      .template-chip:hover { border-color: var(--primary); }
      .tpl-head {
        display: flex; justify-content: space-between; align-items: baseline;
        margin-bottom: 2px;
      }
      .tpl-name { font-weight: 600; font-size: 13px; color: var(--text); }
      .tpl-rail { font-size: 10.5px; color: var(--primary); font-weight: 600; }
      .tpl-meta {
        font-size: 11px; color: var(--text-muted);
        font-family: 'JetBrains Mono', 'Menlo', monospace;
      }

      /* Bank-alias line on typeahead row */
      .bene-bank {
        font-size: 11px; color: var(--text-muted);
        font-style: italic; margin-top: 2px;
      }

      /* Rail pill click affordance + comparison popover */
      .rail-pill {
        background: var(--primary-soft);
        color: var(--primary);
        border: none;
        font: inherit;
        font-size: 12.5px;
        font-weight: 500;
        padding: 3px 10px;
        border-radius: 12px;
        cursor: default;
      }
      .rail-pill.clickable { cursor: pointer; }
      .rail-pill.clickable:hover { background: var(--primary); color: #fff; }
      .rail-compare { margin-left: 4px; font-size: 10px; }

      /* ── Rail-lock indicator next to the header rail pill ── */
      .head-rail-cluster { display: inline-flex; align-items: center; gap: 6px; }
      .rail-lock {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 8px 3px 6px;
        border: 1px solid var(--warning, #b07900);
        background: var(--warning-soft, #fff4d6);
        color: var(--warning, #b07900);
        border-radius: 9999px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition: background 120ms ease;
      }
      .rail-lock:hover { background: #ffe9a8; }
      .rail-lock-icon { font-size: 11px; }
      .rail-lock-x {
        font-size: 13px;
        line-height: 1;
        opacity: 0.7;
        margin-left: 2px;
      }

      /* ── "Start your payment" entry screen ── */
      .start-screen {
        margin: 0 0 18px;
        padding: 18px 18px 14px;
        background: linear-gradient(180deg, var(--bg-card, #fff) 0%, var(--bg-card-2, #f7f9fb) 100%);
        border: 1px solid var(--border, #d8dde3);
        border-radius: 8px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
      }
      .start-head { margin-bottom: 14px; }
      .start-title {
        margin: 0 0 4px;
        font-size: 16px;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.01em;
      }
      .start-sub {
        margin: 0;
        font-size: 12.5px;
        line-height: 1.45;
        color: var(--text-muted);
      }
      .rail-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 10px;
      }
      .rail-tile {
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding: 12px;
        text-align: left;
        background: #fff;
        border: 1px solid var(--border, #d8dde3);
        border-radius: 6px;
        cursor: pointer;
        transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
      }
      .rail-tile:hover {
        transform: translateY(-2px);
        border-color: var(--tile-accent, var(--accent));
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08);
      }
      .rail-tile:active { transform: translateY(0); }
      .rt-top { display: flex; align-items: center; gap: 10px; }
      .rt-icon {
        flex-shrink: 0;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        color: #fff;
        font-weight: 700;
        font-size: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.15);
      }
      .rt-titles { min-width: 0; }
      .rt-name {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        line-height: 1.2;
      }
      .rt-region {
        font-size: 11px;
        color: var(--text-muted);
        margin-top: 1px;
      }
      .rt-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 11.5px;
      }
      .rt-pill {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        padding: 2px 8px;
        border-radius: 9999px;
        background: var(--bg-card-2, #f1f3f5);
        color: var(--text-muted);
        font-weight: 600;
        white-space: nowrap;
      }
      .rt-pill.rt-pill-247 {
        background: var(--accent-soft, #def5e4);
        color: var(--accent, #0a8a3f);
      }
      .rt-cost {
        color: var(--text-muted);
        font-size: 11.5px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .start-foot {
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px dashed var(--border, #d8dde3);
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 12px;
        color: var(--text-muted);
      }
      .start-or {
        flex-shrink: 0;
        font-weight: 600;
        letter-spacing: 0.04em;
      }
      .start-chat-hint em {
        font-style: italic;
        color: var(--text);
        background: var(--bg-card-2, #f1f3f5);
        padding: 1px 6px;
        border-radius: 3px;
      }

      .rail-compare-panel {
        margin: 0 0 14px;
        padding: 12px;
        background: var(--bg-card-2);
        border: 1px solid var(--border);
        border-radius: 6px;
        animation: fade-in 200ms ease;
      }
      .rail-compare-head {
        font-size: 11px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.06em;
        color: var(--text-muted);
        margin-bottom: 10px;
      }
      .rail-compare-row {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 8px 10px;
        margin-bottom: 6px;
      }
      .rail-compare-row:last-child { margin-bottom: 0; }
      .rail-compare-row.rail-current { border-color: var(--accent); }
      .rcr-head {
        display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 4px;
      }
      .rcr-name { font-weight: 600; font-size: 13px; color: var(--text); }
      .rcr-current {
        font-size: 10px; font-weight: 600;
        color: var(--accent-hover); background: var(--accent-soft);
        padding: 1px 7px; border-radius: 8px;
      }
      .rcr-switch {
        background: var(--primary);
        color: #fff;
        border: none;
        padding: 4px 10px;
        border-radius: 3px;
        font-size: 11.5px;
        font-weight: 500;
        cursor: pointer;
      }
      .rcr-switch:hover { background: var(--primary-hover); }
      .rcr-meta {
        font-size: 11.5px;
        color: var(--text-body);
        display: flex; flex-wrap: wrap; gap: 12px;
        margin-bottom: 4px;
      }
      .rcr-meta strong { color: var(--text-muted); font-weight: 500; margin-right: 2px; }
      .rcr-why {
        font-size: 11px; color: var(--text-muted);
        font-style: italic;
      }

      /* Post-payment "learning toast" */
      .confirm-reminder {
        margin: 0 0 14px;
        padding: 10px 12px;
        background: var(--accent-soft);
        border: 1px solid #B8E5CC;
        border-radius: 6px;
        font-size: 12.5px;
        color: #1F4030;
        display: flex; flex-direction: column; gap: 8px;
        align-items: center;
      }
      .reminder-btn {
        background: var(--accent);
        color: #fff;
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        font-size: 12.5px;
        font-weight: 600;
        cursor: pointer;
      }
      .reminder-btn:hover { background: var(--accent-hover); }
      .confirm-reminder-set {
        margin: 0 0 14px;
        padding: 8px 12px;
        background: var(--accent-soft);
        color: #1F4030;
        font-size: 12.5px;
        border-radius: 6px;
        text-align: center;
      }
      .line {
        display: flex; gap: 8px;
        font-family: 'JetBrains Mono', 'Menlo', monospace;
        font-size: 12.5px;
      }
      .bullet { width: 14px; flex-shrink: 0; color: var(--text-muted); text-align: center; }
      .line.user { color: var(--text); }
      .line.user .bullet { color: var(--accent); font-weight: 700; }
      .line.tool.set_field .bullet,
      .line.tool.select_rail .bullet { color: var(--accent); }
      .line.tool.ask .bullet { color: var(--warning); }
      .line.tool.explain .bullet { color: var(--primary); }
      .line.error { color: var(--error); }
      .line.error .bullet { color: var(--error); }
      .line.system { color: var(--text-muted); }

      :host ::ng-deep .line code {
        background: var(--bg-card-2);
        padding: 1px 4px;
        border-radius: 3px;
        font-family: inherit;
      }
      :host ::ng-deep .line .field-name { color: var(--primary); font-weight: 600; }
      :host ::ng-deep .line .value { color: var(--accent-hover); }

      .chat-input {
        display: flex;
        gap: 8px;
        margin-top: 10px;
        border-top: 1px solid var(--border);
        padding-top: 10px;
      }
      .chat-input input {
        flex: 1;
      }
      .chat-input button {
        background: var(--primary);
        color: #fff;
        border: none;
        padding: 8px 18px;
        border-radius: 4px;
        font-weight: 600;
        cursor: pointer;
      }
      .chat-input button:disabled { background: var(--border); color: var(--text-muted); cursor: not-allowed; }

      /* ── Voice dictation button ─────────────────────────────── */
      .chat-input .mic-btn {
        background: var(--bg-card-2, #f1f3f5);
        color: var(--text-muted, #5f6b7a);
        padding: 0;
        width: 38px;
        height: 38px;
        border-radius: 9999px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
        border: 1px solid var(--border, #d8dde3);
        transition: background 140ms ease, color 140ms ease,
                    border-color 140ms ease, transform 140ms ease;
      }
      .chat-input .mic-btn:hover:not(:disabled) {
        background: var(--accent-soft, #def5e4);
        color: var(--accent, #0a8a3f);
        border-color: var(--accent, #0a8a3f);
      }
      .chat-input .mic-btn.mic-listening,
      .chat-input .mic-btn.mic-listening:hover {
        background: #ffe5e5;
        color: #c81e1e;
        border-color: #c81e1e;
        animation: mic-pulse 1.4s ease-in-out infinite;
      }
      @keyframes mic-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(200, 30, 30, 0.4); }
        50%      { box-shadow: 0 0 0 6px rgba(200, 30, 30, 0); }
      }
      .mic-wave {
        display: inline-flex;
        align-items: flex-end;
        gap: 2px;
        height: 14px;
      }
      .mic-wave span {
        display: inline-block;
        width: 3px;
        background: currentColor;
        border-radius: 2px;
        animation: mic-bar 0.9s ease-in-out infinite;
      }
      .mic-wave span:nth-child(1) { height: 6px;  animation-delay: 0s;   }
      .mic-wave span:nth-child(2) { height: 12px; animation-delay: 0.15s;}
      .mic-wave span:nth-child(3) { height: 8px;  animation-delay: 0.30s;}
      @keyframes mic-bar {
        0%, 100% { transform: scaleY(0.6); }
        50%      { transform: scaleY(1.4); }
      }
      @media (prefers-reduced-motion: reduce) {
        .chat-input .mic-btn.mic-listening { animation: none; }
        .mic-wave span { animation: none; }
      }

      .footer-err {
        margin-top: 8px;
        font-size: 12px;
        color: var(--error);
      }
    `,
  ],
})
export class PaymentWizardComponent implements OnInit {
  private api = inject(ApiService);
  private zone = inject(NgZone);

  /** Silence threshold after which dictation auto-stops + auto-sends. */
  private readonly SILENCE_MS = 1400;

  // ── Registry & rail state ─────────────────────────────────────── //
  registry = signal<RailsRegistry | null>(null);
  loadError = signal<string | null>(null);

  formState = signal<WizardFormState>({ rail_id: null });
  candidates = signal<RailCandidate[]>([]);
  confidences = signal<Record<string, number>>({});
  derivedFields = signal<Set<string>>(new Set());
  fieldErrors = signal<Record<string, string>>({});

  // Debit account picker (typeahead style).
  debitAccounts = signal<DebitAccount[]>([]);
  accountQuery = signal('');
  accountDropdownOpen = signal(false);

  /** Currency-first then substring filter. If no currency-match, fall back
   *  to showing all accounts. If a substring query is set, narrow further. */
  filteredAccounts = computed<DebitAccount[]>(() => {
    const all = this.debitAccounts();
    const cur = (this.formState()['currency'] as string | undefined)?.toUpperCase();
    const byCurrency = cur ? all.filter((a) => a.currency === cur) : all;
    const pool = byCurrency.length ? byCurrency : all;
    const q = this.accountQuery().trim().toLowerCase();
    if (!q) return pool;
    return pool.filter((a) =>
      a.code.toLowerCase().includes(q)
      || a.name.toLowerCase().includes(q)
      || a.currency.toLowerCase().includes(q)
      || a.kind.toLowerCase().includes(q),
    );
  });

  selectedAccount = computed<DebitAccount | null>(() => {
    const id = Number(this.formState()['debit_account_id'] ?? 0);
    if (!id) return null;
    return this.debitAccounts().find((a) => a.id === id) ?? null;
  });

  // Beneficiary typeahead.
  beneficiarySuggestions = signal<SavedBeneficiary[]>([]);
  beneficiaryDropdownOpen = signal(false);
  private beneTypingTimer: ReturnType<typeof setTimeout> | null = null;

  // Recurring suggestions (loaded once on init).
  recurringSuggestions = signal<RecurringSuggestion[]>([]);
  reusableTemplates = signal<ReusableTemplate[]>([]);

  // Cross-rail comparison popover.
  showRailComparison = signal(false);

  selectedRail = computed<RailDef | null>(() => {
    const reg = this.registry();
    const id = this.formState().rail_id;
    if (!reg || !id) return null;
    return reg.rails[id] ?? null;
  });

  /** True when the form is in its initial blank state — no rail, no country, no
   *  currency, no amount. Used to decide whether to render the "Start your
   *  payment" entry screen with all 6 rails laid out as cards. */
  isEmptyStart = computed<boolean>(() => {
    const s = this.formState();
    return !s.rail_id && !s['beneficiary_country'] && !s['currency'] && !s['amount'];
  });

  /** True when the user explicitly picked a rail from the entry screen — Qwen
   *  isn't allowed to re-pick. Surfaced as a small "Locked" pill in the header. */
  railLocked = computed<boolean>(() => Boolean(this.formState().rail_locked));

  /** Ordered list of all rails for the entry screen, with availability + cost
   *  pre-computed from the registry's schedule/cost blocks. Built once when the
   *  registry loads. */
  allRailEntries = computed<{
    id: string;
    name: string;
    region: string;
    summary: string;
    icon: string;
    accent: string;
    speed: string;
    cost: string;
    avail247: boolean;
  }[]>(() => {
    const reg = this.registry();
    if (!reg) return [];
    // Cosmetic only — a coloured glyph per rail keeps the entry screen visually
    // varied without pulling in an icon library.
    const cosmetic: Record<string, { icon: string; accent: string }> = {
      sepa_inst:    { icon: '€',  accent: '#1b6fff' },
      uk_fps:       { icon: '£',  accent: '#7a3df0' },
      us_ach:       { icon: '$',  accent: '#0a8a3f' },
      india_imps:   { icon: '₹',  accent: '#e07a00' },
      brazil_pix:   { icon: 'R$', accent: '#00a36e' },
      swift_mt103:  { icon: '⇄',  accent: '#5b6573' },
    };
    const order = ['sepa_inst', 'uk_fps', 'us_ach', 'india_imps', 'brazil_pix', 'swift_mt103'];
    return order
      .filter((id) => !!reg.rails[id])
      .map((id) => {
        const r = reg.rails[id] as unknown as Record<string, unknown>;
        const sched = r['schedule'] as Record<string, unknown> | undefined;
        return {
          id,
          name: String(r['display_name'] ?? id),
          region: String(r['region'] ?? ''),
          summary: String(r['summary'] ?? ''),
          icon: cosmetic[id]?.icon ?? '●',
          accent: cosmetic[id]?.accent ?? '#5b6573',
          speed: String(r['speed_text'] ?? ''),
          cost: String(r['cost_text'] ?? ''),
          avail247: Boolean(sched?.['operates_24x7']) && Boolean(sched?.['weekend_open']),
        };
      });
  });

  // All fields (common + rail-specific) — re-evaluates whenever the rail or
  // form state changes (depends_on can hide/show fields per-rail).
  visibleFields = computed<RailField[]>(() => {
    const reg = this.registry();
    if (!reg) return [];
    const fields: RailField[] = [...reg.common_fields];
    const rail = this.selectedRail();
    if (rail) fields.push(...rail.fields);
    const state = this.formState();
    return fields.filter((f) => {
      if (!f.depends_on) return true;
      return state[f.depends_on.field] === f.depends_on.value;
    });
  });

  // ── Chat state ─────────────────────────────────────────────────── //
  chat = signal<ChatEntry[]>([]);
  draft = signal('');
  sending = signal(false);
  lastError = signal<string | null>(null);
  submitting = signal(false);
  submitResult = signal<SubmitConfirmation | null>(null);

  // ── Ollama model picker ──────────────────────────────────────────── //
  /** Visibility of the gear-icon popover. */
  showModelPicker = signal(false);
  /** Currently active Ollama model — read from /api/wizard/models on init. */
  activeModel = signal<string>('');
  /** Chat-capable models Ollama has locally. */
  availableModels = signal<{ name: string; size: number; modified_at: string }[]>([]);
  /** True while a model switch request is in flight. */
  switchingModel = signal(false);

  // ── Voice dictation (Web Speech API) ───────────────────────────── //
  /** 'idle' = mic shown, click to start. 'listening' = currently capturing. */
  voiceState = signal<'idle' | 'listening'>('idle');
  /** True only when the browser exposes a SpeechRecognition implementation. */
  voiceSupported = signal<boolean>(false);
  /** The live SpeechRecognition instance — typed loose because the spec isn't
   *  standardised across browsers (Chrome uses webkitSpeechRecognition). */
  private recognition: any = null;
  /** Text that was already in the draft when listening started — used so we
   *  can append the interim transcript without clobbering the user's typing. */
  private voiceDraftBase = '';
  /** Pending auto-send after silence. Cleared on every new transcript event. */
  private silenceTimer: ReturnType<typeof setTimeout> | null = null;
  /** True when we stopped recognition ourselves so we can auto-submit on `onend`. */
  private voiceShouldSubmit = false;

  @ViewChild('log') logEl?: ElementRef<HTMLDivElement>;

  ngOnInit(): void {
    this.api.listRails().subscribe({
      next: (r) => this.registry.set(r),
      error: (e) => this.loadError.set(e?.error?.detail ?? 'unknown'),
    });
    this.api.listDebitAccounts().subscribe({
      next: (r) => this.debitAccounts.set(r.accounts),
    });
    this.api.listSuggestions().subscribe({
      next: (r) => {
        this.recurringSuggestions.set(r.suggestions || []);
        this.reusableTemplates.set(r.templates || []);
      },
    });
    this.setupVoice();
    this.loadModels();
  }

  // ── Model picker ─────────────────────────────────────────────── //

  /** Pull the model list from the api. Called on init + Refresh button click. */
  loadModels(): void {
    this.api.listOllamaModels().subscribe({
      next: (r) => {
        this.activeModel.set(r.active || '');
        this.availableModels.set(r.models || []);
      },
      error: () => {
        // Don't blow up — just keep whatever we had. The picker shows "no
        // chat models found" if availableModels stays empty.
      },
    });
  }

  toggleModelPicker(): void {
    if (this.showModelPicker()) {
      this.showModelPicker.set(false);
    } else {
      this.loadModels();
      this.showModelPicker.set(true);
    }
  }

  /** POST /api/wizard/model — swap immediately, no container restart. */
  switchModel(model: string): void {
    if (model === this.activeModel() || this.switchingModel()) return;
    this.switchingModel.set(true);
    this.api.setOllamaModel(model).subscribe({
      next: (r) => {
        this.activeModel.set(r.active);
        this.switchingModel.set(false);
        this.showModelPicker.set(false);
        this.chat.update((c) => [...c, {
          kind: 'system',
          text: `Switched to ${r.active}. The next message will use this model.`,
        }]);
        this.scrollChat();
      },
      error: (e) => {
        this.switchingModel.set(false);
        this.lastError.set(e?.error?.detail ?? e?.message ?? 'failed to switch model');
      },
    });
  }

  /** "4.9 GB" / "562 MB" formatting for the model row size column. */
  formatBytes(bytes: number): string {
    if (!bytes) return '';
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
    if (bytes >= 1e6) return Math.round(bytes / 1e6) + ' MB';
    return Math.round(bytes / 1e3) + ' KB';
  }

  // ── Voice dictation ───────────────────────────────────────────── //

  /** Detect Web Speech API and wire up a single SpeechRecognition instance.
   *  Runs once at startup. If unsupported, the mic button stays hidden. */
  private setupVoice(): void {
    const w = window as unknown as {
      SpeechRecognition?: any;
      webkitSpeechRecognition?: any;
    };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) {
      this.voiceSupported.set(false);
      return;
    }
    this.voiceSupported.set(true);
    const rec = new Ctor();
    // continuous=true lets the user speak long sentences with mid-sentence
    // pauses — we manage stop+submit ourselves via the silence timer.
    rec.continuous = true;
    rec.interimResults = true;       // stream interim transcripts as the user speaks
    rec.lang = navigator.language || 'en-US';
    rec.maxAlternatives = 1;

    rec.onresult = (ev: any) => {
      // Concatenate everything from the start of THIS recognition session.
      let interim = '';
      let finalText = '';
      for (let i = 0; i < ev.results.length; i++) {
        const r = ev.results[i];
        const text = r[0]?.transcript ?? '';
        if (r.isFinal) finalText += text;
        else interim += text;
      }
      const combined = (this.voiceDraftBase + ' ' + finalText + interim).replace(/\s+/g, ' ').trim();
      // SpeechRecognition fires outside NgZone — run inside so the signal
      // update propagates to the input on the same tick (no perceptible lag).
      this.zone.run(() => {
        this.draft.set(combined);
      });
      // Reset the silence timer every time we hear something. When it elapses,
      // we'll stop recognition and the auto-submit branch of onend fires.
      this.armSilenceTimer();
    };

    rec.onerror = (ev: any) => {
      console.warn('Voice recognition error:', ev.error);
      this.zone.run(() => {
        this.voiceState.set('idle');
        this.clearSilenceTimer();
        if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
          this.lastError.set('Microphone permission denied — enable it in browser settings to dictate.');
        } else if (ev.error === 'no-speech') {
          this.lastError.set('No speech detected — try again.');
        }
      });
    };

    rec.onend = () => {
      // Always run UI updates in zone — the onend callback fires outside it.
      this.zone.run(() => {
        this.voiceState.set('idle');
        this.clearSilenceTimer();
        if (this.voiceShouldSubmit) {
          this.voiceShouldSubmit = false;
          // Auto-submit the dictated message if there's anything in the draft.
          if (this.draft().trim().length > 0 && !this.sending()) {
            this.send();
          }
        }
      });
    };

    this.recognition = rec;
  }

  /** Start a fresh silence countdown — auto-stop + submit when it elapses. */
  private armSilenceTimer(): void {
    this.clearSilenceTimer();
    this.silenceTimer = setTimeout(() => {
      this.silenceTimer = null;
      this.voiceShouldSubmit = true;
      try { this.recognition?.stop(); } catch { /* already stopped */ }
    }, this.SILENCE_MS);
  }

  private clearSilenceTimer(): void {
    if (this.silenceTimer != null) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  /** Start or stop dictation. Called from the mic button. */
  toggleVoice(): void {
    if (!this.recognition) return;
    if (this.voiceState() === 'listening') {
      // Manual stop — DO submit, since the user clicked the mic intentionally.
      this.voiceShouldSubmit = true;
      try { this.recognition.stop(); } catch { /* already stopped */ }
      return;
    }
    this.lastError.set(null);
    this.voiceShouldSubmit = false;
    this.voiceDraftBase = this.draft();
    try {
      this.recognition.start();
      this.voiceState.set('listening');
      // Arm a generous initial timer so we auto-stop even if the user never speaks.
      this.armSilenceTimer();
    } catch (e) {
      console.warn('Could not start recognition:', e);
      this.voiceState.set('idle');
    }
  }

  /**
   * Refresh the candidate-rail list from the deterministic backend selector.
   * Called after any cascade (suggestion click, template click, beneficiary
   * typeahead pick) so the rail-comparison popover has data to display.
   */
  private refreshCandidates(): void {
    const state = this.formState();
    const country = (state['beneficiary_country'] as string) || null;
    const currency = (state['currency'] as string) || null;
    const amount = state['amount'] != null ? Number(state['amount']) : null;
    if (!country || !currency) {
      this.candidates.set([]);
      return;
    }
    this.api.selectRails(country, currency, amount).subscribe({
      next: (r) => this.candidates.set(r.candidates || []),
    });
  }

  // ── Recurring-suggestions cascade ───────────────────────────────── //
  applySuggestion(s: RecurringSuggestion): void {
    const patch: Record<string, unknown> = {
      beneficiary_name: s.beneficiary,
      beneficiary_country: s.country,
      currency: s.currency,
      amount: s.suggested_amount,
      rail_id: s.rail_id,
      ...(s.fields ?? {}),
    };
    this.formState.update((state) => ({ ...state, ...patch }));
    this.confidences.update((m) => {
      const next = { ...m };
      for (const k of Object.keys(patch)) next[k] = 1.0;
      return next;
    });
    this.chat.update((c) => [
      ...c,
      {
        kind: 'system',
        text: `applied recurring: ${s.beneficiary} — ${s.reason} (${(s.confidence * 100).toFixed(0)}% conf.)`,
      },
    ]);
    this.scrollChat();
    this.refreshCandidates();
  }

  applyTemplate(t: ReusableTemplate): void {
    const patch: Record<string, unknown> = {
      beneficiary_name: t.beneficiary,
      beneficiary_country: t.country,
      currency: t.currency,
      rail_id: t.rail_id,
      ...(t.fields ?? {}),
    };
    this.formState.update((state) => ({ ...state, ...patch }));
    this.chat.update((c) => [
      ...c,
      {
        kind: 'system',
        text: `applied template: ${t.beneficiary} (last paid ${t.last_paid})`,
      },
    ]);
    this.scrollChat();
    this.refreshCandidates();
  }

  // Pretty-format the suggested amount for the card display
  formatAmount(amount: number, currency: string): string {
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        maximumFractionDigits: 0,
      }).format(amount);
    } catch {
      return `${amount} ${currency}`;
    }
  }

  // ── Debit-account picker (typeahead) ────────────────────────────── //
  amountExceedsBalance(a: DebitAccount): boolean {
    const amt = Number(this.formState()['amount'] ?? 0);
    return amt > a.balance;
  }

  isSelectedAccount(a: DebitAccount): boolean {
    return a.id === Number(this.formState()['debit_account_id'] ?? 0);
  }

  onAccountInput(value: string): void {
    this.accountQuery.set(value);
    this.accountDropdownOpen.set(true);
  }

  onAccountFocus(): void {
    this.accountDropdownOpen.set(true);
    // On focus, clear the query so the full filtered list is shown again —
    // mirrors how Mac's search fields behave.
    this.accountQuery.set('');
  }

  onAccountBlur(): void {
    // Delay close so a mousedown on a suggestion lands first.
    setTimeout(() => {
      this.accountDropdownOpen.set(false);
      // Reset query text to match the currently selected account so the input
      // doesn't look orphaned.
      const sel = this.selectedAccount();
      this.accountQuery.set(sel ? `${sel.code} · ${sel.name}` : '');
    }, 150);
  }

  applyAccount(a: DebitAccount): void {
    this.formState.update((s) => ({ ...s, debit_account_id: a.id }));
    this.confidences.update((m) => ({ ...m, debit_account_id: 1.0 }));
    this.accountQuery.set(`${a.code} · ${a.name}`);
    this.accountDropdownOpen.set(false);
    this.chat.update((c) => [
      ...c,
      {
        kind: 'system',
        text: `picked debit account: ${a.code} · ${a.name} (${a.currency} ${a.balance.toLocaleString()})`,
      },
    ]);
    this.scrollChat();
  }

  // ── Beneficiary typeahead ───────────────────────────────────────── //
  onBeneficiaryInput(value: string): void {
    this.formState.update((s) => ({ ...s, beneficiary_name: value }));
    this.beneficiaryDropdownOpen.set(true);
    if (this.beneTypingTimer) clearTimeout(this.beneTypingTimer);
    this.beneTypingTimer = setTimeout(() => {
      this.api.listBeneficiaries(value || undefined).subscribe({
        next: (r) => this.beneficiarySuggestions.set(r.beneficiaries),
      });
    }, 120);
  }

  /** Show all beneficiaries the moment the user focuses the field — no
   *  2-character minimum. Lets them browse the directory by clicking. */
  onBeneficiaryFocus(): void {
    this.beneficiaryDropdownOpen.set(true);
    if (this.beneficiarySuggestions().length === 0) {
      this.api.listBeneficiaries().subscribe({
        next: (r) => this.beneficiarySuggestions.set(r.beneficiaries),
      });
    }
  }

  onBeneficiaryBlur(): void {
    // Delay close so a click on a suggestion lands before blur hides it.
    setTimeout(() => this.beneficiaryDropdownOpen.set(false), 150);
  }

  /** Click on a typeahead row — fan all the saved fields into the form
   *  and select the preferred rail. The chat strip logs each patch so the
   *  user can see the cascade. */
  applyBeneficiary(b: SavedBeneficiary): void {
    this.beneficiaryDropdownOpen.set(false);
    this.beneficiarySuggestions.set([]);
    const patch: Record<string, unknown> = {
      beneficiary_name: b.name,
      beneficiary_country: b.country,
      rail_id: b.preferred_rail,
    };
    // Only set currency if not already set by the user
    if (!this.formState()['currency']) {
      patch['currency'] = b.preferred_currency;
    }
    for (const [k, v] of Object.entries(b.fields || {})) patch[k] = v;
    this.formState.update((s) => ({ ...s, ...patch }));
    this.confidences.update((m) => {
      const next = { ...m };
      for (const k of Object.keys(patch)) next[k] = 1.0;
      return next;
    });
    this.chat.update((c) => [
      ...c,
      {
        kind: 'system',
        text: `applied saved beneficiary: ${b.name} — filled ${Object.keys(patch).length} fields, selected ${this.railName(b.preferred_rail)}`,
      },
    ]);
    this.scrollChat();
    this.refreshCandidates();
  }

  // ── User input → form ─────────────────────────────────────────── //
  patchInput(field: RailField, raw: string): void {
    let value: unknown = raw;
    if (field.type === 'number' && raw !== '') value = Number(raw);
    if (field.auto_uppercase && typeof value === 'string') {
      value = value.toUpperCase();
    }
    this.formState.update((s) => ({ ...s, [field.id]: value }));
    // Clear stale errors on edit
    this.fieldErrors.update((e) => {
      if (!e[field.id]) return e;
      const next = { ...e };
      delete next[field.id];
      return next;
    });
  }

  // ── Manual rail switch (when picker shows multiple candidates) ──── //
  manuallySelectRail(rail_id: string): void {
    this.formState.update((s) => ({ ...s, rail_id }));
    this.chat.update((c) => [
      ...c,
      {
        kind: 'tool',
        call: {
          name: 'select_rail',
          args: { rail_id, why: 'picked manually' },
        },
      },
    ]);
  }

  /** Direct pick from the "Start your payment" entry screen — locks the rail
   *  so Qwen can't reroute, drops a friendly note in the chat strip. */
  pickRailDirectly(rail_id: string): void {
    const railName = this.registry()?.rails[rail_id]?.display_name ?? rail_id;
    this.formState.update((s) => ({ ...s, rail_id, rail_locked: true }));
    this.chat.update((c) => [
      ...c,
      {
        kind: 'system',
        text: `Picked ${railName} directly — fill the form, or describe the payment in chat and I'll fill the rest.`,
      },
    ]);
    this.scrollChat();
  }

  /** Releases the lock so the LLM can re-pick (or so the user can switch to
   *  another rail via the comparison panel). */
  unlockRail(): void {
    this.formState.update((s) => ({ ...s, rail_locked: false }));
    this.chat.update((c) => [
      ...c,
      { kind: 'system', text: 'Rail unlocked — Qwen can now switch rails based on what you describe.' },
    ]);
  }

  // ── Chat send → backend → apply tool calls ─────────────────────── //
  send(): void {
    const text = this.draft().trim();
    if (!text || this.sending()) return;
    this.draft.set('');
    this.chat.update((c) => [...c, { kind: 'user', text }]);
    this.sending.set(true);
    this.lastError.set(null);
    this.scrollChat();

    this.api.wizardTurn(text, this.formState()).subscribe({
      next: async (res) => {
        // Stagger the tool-call patches so the form visibly populates one
        // field at a time. 100ms per patch is fast enough to feel quick,
        // slow enough that the audience reads each change land.
        for (const call of res.tool_calls) {
          this.applyToolCall(call);
          this.chat.update((c) => [...c, { kind: 'tool', call }]);
          this.scrollChat();
          await new Promise((r) => setTimeout(r, 100));
        }
        // If the model returned prose instead of tool calls, surface it so
        // the demo never silently stalls. Qwen 7B occasionally does this
        // on terse prompts.
        if (res.tool_calls.length === 0 && res.raw_message) {
          this.chat.update((c) => [
            ...c,
            { kind: 'system', text: `model returned prose: "${res.raw_message}"` },
          ]);
        }
        // Apply auto-derived fields (e.g. BIC from IBAN)
        const derived = res.derived || {};
        const derivedKeys = Object.keys(derived).filter((k) => !k.startsWith('_'));
        if (derivedKeys.length) {
          const patch: Record<string, unknown> = {};
          for (const k of derivedKeys) patch[k] = derived[k];
          this.formState.update((s) => ({ ...s, ...patch }));
          this.derivedFields.update((set) => {
            const next = new Set(set);
            for (const k of derivedKeys) next.add(k);
            return next;
          });
          this.chat.update((c) => [
            ...c,
            {
              kind: 'system',
              text: `auto-derived ${derivedKeys.join(', ')}${
                derived['_bank_name'] ? ` (${derived['_bank_name']})` : ''
              }`,
            },
          ]);
        }
        // Validation errors → show on the relevant field. `rail_id` errors
        // (e.g. the model hallucinated a rail that's not in the registry) have
        // no corresponding form field, so route them to the chat strip too.
        if (res.validation.length) {
          const errs: Record<string, string> = {};
          for (const v of res.validation) {
            if (!v.ok && v.error) {
              if (v.field_id === 'rail_id') {
                this.chat.update((c) => [...c, { kind: 'system', text: v.error! }]);
              } else {
                errs[v.field_id] = v.error;
              }
            }
          }
          if (Object.keys(errs).length) this.fieldErrors.update((e) => ({ ...e, ...errs }));
        }
        this.candidates.set(res.candidates || []);
        this.sending.set(false);
        this.scrollChat();
      },
      error: (e) => {
        this.sending.set(false);
        const msg = e?.error?.detail ?? e?.message ?? 'request failed';
        this.lastError.set(msg);
        this.chat.update((c) => [...c, { kind: 'error', text: msg }]);
        this.scrollChat();
      },
    });
  }

  private applyToolCall(call: WizardToolCall): void {
    if (call.name === 'set_field') {
      const id = call.args['field_id'] as string;
      const value = call.args['value'];
      const conf = (call.args['confidence'] as number) ?? 1.0;
      this.formState.update((s) => ({ ...s, [id]: value }));
      this.confidences.update((m) => ({ ...m, [id]: conf }));
      // Sync the account-picker display when the LLM sets debit_account_id.
      if (id === 'debit_account_id') {
        const acc = this.debitAccounts().find((a) => a.id === Number(value));
        if (acc) this.accountQuery.set(`${acc.code} · ${acc.name}`);
      }
    } else if (call.name === 'select_rail') {
      const id = call.args['rail_id'] as string;
      this.formState.update((s) => ({ ...s, rail_id: id }));
    }
    // 'ask' and 'explain' have no form-state effect — they render in chat.
  }

  // ── Confidence colouring + derived-field highlight ─────────────── //
  confidence(field_id: string): number | undefined {
    return this.confidences()[field_id];
  }
  isDerived(field_id: string): boolean {
    return this.derivedFields().has(field_id);
  }
  fieldError(field_id: string): string | undefined {
    return this.fieldErrors()[field_id];
  }

  railName(rail_id: string): string {
    return this.registry()?.rails[rail_id]?.display_name ?? rail_id;
  }

  // ── Beneficiary frequency badge ────────────────────────────────── //
  freqClass(count: number): string {
    if (count > 10) return 'frequent';
    if (count > 2) return 'regular';
    return 'new';
  }
  freqLabel(count: number): string {
    if (count > 10) return 'Frequent';
    if (count > 2) return 'Regular';
    return 'New';
  }

  // ── Submit / reset ─────────────────────────────────────────────── //
  canSubmit(): boolean {
    const state = this.formState();
    if (!state.rail_id) return false;
    return this.visibleFields()
      .filter((f) => f.required)
      .every((f) => {
        const v = state[f.id];
        return v !== undefined && v !== null && v !== '';
      });
  }

  submit(): void {
    // Demo-only: we don't actually clear payments. The slide-up confirmation
    // card is the moment that signals "this product is done" to the audience.
    this.submitting.set(true);
    setTimeout(() => {
      const id = Math.floor(Math.random() * 90000) + 10000;
      const state = this.formState();
      const amt = state['amount'];
      const cur = state['currency'];
      this.submitting.set(false);
      // If the current beneficiary appears in the recurring-suggestions list,
      // surface the detected cadence so the user can opt into a reminder.
      // Falls back to undefined for ad-hoc beneficiaries (no reminder shown).
      const beneName = String(state['beneficiary_name'] ?? '');
      const match = this.recurringSuggestions().find(
        (s) => s.beneficiary === beneName,
      );
      this.submitResult.set({
        id,
        amount: `${amt ?? '?'} ${cur ?? ''}`.trim(),
        beneficiary: beneName,
        rail: this.selectedRail()?.display_name ?? '',
        reference: (state['reference'] || state['remittance_info']) as string | undefined,
        reminderDays: match?.cadence_days,
        reminderSet: false,
      });
      this.chat.update((c) => [
        ...c,
        { kind: 'system', text: `submitted as payment #${id}` },
      ]);
      this.scrollChat();
    }, 600);
  }

  /** Dismiss the confirmation card and start a fresh payment. */
  sendAnother(): void {
    this.reset();
  }

  /** Toast that learns: user accepts the auto-detected reminder cadence. */
  acceptReminder(): void {
    this.submitResult.update((r) => (r ? { ...r, reminderSet: true } : r));
  }

  toggleRailComparison(): void {
    if (!this.selectedRail() || this.candidates().length === 0) return;
    this.showRailComparison.update((v) => !v);
  }

  /** Read a raw field from the rail registry (speed_text, cost_text, max_amount, etc.) */
  getRailField(railId: string, field: string): string | null {
    const r = this.registry()?.rails?.[railId] as Record<string, unknown> | undefined;
    if (!r) return null;
    const v = r[field];
    if (v == null) return null;
    return typeof v === 'number'
      ? v.toLocaleString()
      : String(v);
  }

  /**
   * "Settles by" formatter — compact relative-day phrasing.
   * "today 16:30", "tomorrow 16:30", "Wed 09:00".
   */
  formatSettlement(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const isTomorrow =
      d.getFullYear() === tomorrow.getFullYear() &&
      d.getMonth() === tomorrow.getMonth() &&
      d.getDate() === tomorrow.getDate();
    const hhmm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    if (sameDay) return `today ${hhmm}`;
    if (isTomorrow) return `tomorrow ${hhmm}`;
    const weekday = d.toLocaleDateString([], { weekday: 'short' });
    return `${weekday} ${hhmm}`;
  }

  // Three demo prompts shown above the chat input on first load. Click a
  // chip to drop the text into the draft signal — keeps the activation
  // friction low during demos.
  demoPresets = [
    'Send 5,000 EUR to Acme GmbH in Germany, IBAN DE89370400440532013000',
    'Pay Smith & Holland 2,500 GBP for invoice INV-2026',
    'Pay 200 BRL to consultor@itau.com.br via PIX',
  ];

  loadPreset(text: string): void {
    this.draft.set(text);
  }

  reset(): void {
    this.formState.set({ rail_id: null, rail_locked: false });
    this.candidates.set([]);
    this.confidences.set({});
    this.derivedFields.set(new Set());
    this.fieldErrors.set({});
    this.chat.set([]);
    this.draft.set('');
    this.submitResult.set(null);
    this.lastError.set(null);
    this.accountQuery.set('');
    this.accountDropdownOpen.set(false);
    this.beneficiarySuggestions.set([]);
    this.beneficiaryDropdownOpen.set(false);
  }

  // ── Chat rendering helpers ─────────────────────────────────────── //
  toolBullet(name: string): string {
    return {
      set_field: '✓',
      select_rail: '▸',
      ask: '?',
      explain: 'ⓘ',
    }[name] ?? '·';
  }

  renderTool(call: WizardToolCall): string {
    const args = call.args || {};
    const esc = (s: unknown) =>
      String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    switch (call.name) {
      case 'set_field':
        return `<span class="field-name">${esc(args['field_id'])}</span> = <span class="value">${esc(args['value'])}</span>`;
      case 'select_rail':
        return `selected <span class="field-name">${esc(args['rail_id'])}</span> — ${esc(args['why'])}`;
      case 'ask':
        return `<em>${esc(args['prompt'])}</em>`;
      case 'explain':
        return `<strong>${esc(args['topic'])}.</strong> ${esc(args['body'])}`;
      default:
        return `<code>${esc(JSON.stringify(args))}</code>`;
    }
  }

  private scrollChat(): void {
    queueMicrotask(() => {
      const el = this.logEl?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}
