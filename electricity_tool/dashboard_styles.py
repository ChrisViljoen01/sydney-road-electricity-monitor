from __future__ import annotations

from nicegui import ui


def apply_dashboard_styles() -> None:
    """Apply the light Connect Logistics layout and accessible navy navigation."""
    ui.add_head_html(
        """
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400;600&display=swap');
          :root {
              color-scheme: light;
              --app-bg: #f4f7fb;
              --app-surface: #ffffff;
              --app-surface-soft: #edf2f8;
              --app-primary: #1c2545;
              --app-text: #1c2545;
              --app-muted: #64748b;
              --app-border: #d6deea;
              --app-accent: #e04403;
              --app-success: #007d6d;
              --app-danger: #b91c1c;
              --app-warning: #a63a12;
              --sidebar: #1c2545;
          }
          html, body { min-height: 100%; }
          body {
              margin: 0;
              color: var(--app-text);
              background:
                  radial-gradient(circle at 16% 8%, rgba(224,68,3,.08), transparent 24%),
                  radial-gradient(circle at 84% 4%, rgba(0,125,109,.08), transparent 22%),
                  linear-gradient(145deg, #f8fafc 0%, #eef4fb 52%, #f4f7fb 100%);
              font-family: 'Manrope', sans-serif;
              font-size: 14px;
          }
          .nicegui-content { padding: 0 !important; }
          .mono { font-family: 'IBM Plex Mono', monospace; }
          .app-shell {
              min-height: 100vh;
              width: 100%;
              display: flex;
              align-items: stretch;
          }
          .app-shell::before {
              content: '';
              position: fixed;
              inset: 0 0 auto 0;
              height: 76px;
              z-index: 0;
              background: rgba(255,255,255,.84);
              border-bottom: 1px solid rgba(100,116,139,.16);
              backdrop-filter: blur(16px);
          }
          .sidebar {
              width: 286px;
              min-width: 286px;
              height: 100vh;
              position: sticky;
              top: 0;
              align-self: flex-start;
              overflow: hidden;
              z-index: 3;
              color: #ffffff;
              background: var(--sidebar);
              border-right: 1px solid rgba(255,255,255,.10);
              box-shadow: 16px 0 38px rgba(28,37,69,.16);
              transition: width 180ms ease, min-width 180ms ease;
          }
          .sidebar.collapsed { width: 86px; min-width: 86px; }
          .sidebar-stack {
              min-height: 100vh;
              height: 100vh;
              display: flex;
              flex-direction: column;
              box-sizing: border-box;
              padding: 12px;
              gap: 16px;
          }
          .sidebar .muted-text { color: #bac5dc !important; }
          .sidebar .body-text { color: #ffffff !important; }
          .sidebar-brand-text { color: #cbd5e1 !important; letter-spacing: .08em; }
          .logo-plate {
              min-height: 100px;
              display: flex;
              align-items: center;
              justify-content: center;
              padding: 12px;
              overflow: hidden;
              background: #ffffff;
              border: 1px solid rgba(255,255,255,.78);
              border-radius: 14px;
              box-shadow: 0 12px 28px rgba(0,0,0,.16);
          }
          .brand-logo {
              width: 220px;
              max-width: 100%;
              max-height: 74px;
              object-fit: contain;
              object-position: center;
          }
          .sidebar.collapsed .logo-plate {
              min-height: 62px;
              width: 62px;
              align-self: center;
              padding: 6px;
              border-radius: 12px;
          }
          .sidebar.collapsed .brand-logo { width: 48px; height: 48px; }
          .sidebar.collapsed .sidebar-label,
          .sidebar.collapsed .sidebar-meta,
          .sidebar.collapsed .sidebar-brand-text { display: none !important; }
          .nav-btn {
              width: 100%;
              min-height: 44px;
              margin: 2px 0;
              padding: 10px 14px;
              border-radius: 8px;
              color: #ffffff !important;
              font-weight: 800;
              justify-content: flex-start !important;
          }
          .nav-btn .q-btn__content { width: 100%; justify-content: flex-start !important; gap: 12px; }
          .nav-btn .q-icon, .nav-btn .sidebar-label { color: #ffffff !important; }
          .nav-btn .q-icon {
              width: 24px !important;
              min-width: 24px !important;
              height: 24px !important;
              display: inline-flex !important;
              align-items: center;
              justify-content: center;
              margin: 0 !important;
          }
          .nav-btn.active {
              background: rgba(224,68,3,.20) !important;
              box-shadow: inset 3px 0 0 #ff7a3d;
          }
          .nav-btn:hover { background: rgba(255,255,255,.08) !important; }
          .nav-btn .q-icon { font-size: 22px; }
          .sidebar.collapsed .nav-btn {
              width: 48px;
              min-width: 48px;
              height: 48px;
              min-height: 48px;
              align-self: center;
              padding: 0;
              justify-content: center !important;
          }
          .sidebar.collapsed .nav-btn .q-btn__content { justify-content: center !important; gap: 0; }
          .glass-panel {
              color: #ffffff;
              background: rgba(255,255,255,.07);
              border: 1px solid rgba(255,255,255,.14);
              border-radius: 10px;
              box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
          }
          .sidebar-bottom { margin-top: auto; }
          .sidebar .toolbar-action {
              color: #ffffff !important;
              background: rgba(255,255,255,.08) !important;
              border-color: rgba(255,255,255,.16) !important;
          }
          .sidebar .sidebar-collapse,
          .sidebar .sidebar-collapse .q-icon,
          .sidebar .sidebar-collapse .q-btn__content {
              color: #ffffff !important;
          }
          .sidebar .sidebar-collapse:hover { background: rgba(255,255,255,.14) !important; }
          .sidebar-status { color: #66e3c7 !important; }
          .app-main { min-width: 0; flex: 1; position: relative; z-index: 1; width: 100%; }
          .toolbar {
              min-height: 76px;
              padding: 14px 24px;
              position: sticky;
              top: 0;
              z-index: 5;
              background: transparent;
              backdrop-filter: blur(14px);
          }
          .toolbar-title { color: var(--app-primary); font-size: 1.18rem; font-weight: 800; line-height: 1.25; }
          .toolbar-subtitle { color: var(--app-muted); font-size: .78rem; }
          .content-wrap { width: 100%; padding: 18px 24px 34px; gap: 16px; box-sizing: border-box; }
          .app-card, .dashboard-card, .metric-card {
              color: var(--app-text);
              background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(247,250,254,.98));
              border: 1px solid var(--app-border);
              border-radius: 10px;
              box-shadow: 0 16px 38px rgba(15,23,42,.08);
          }
          .dashboard-card { padding: 18px; overflow: hidden; }
          .metric-card { min-height: 118px; padding: 16px; }
          .metric-label { color: var(--app-muted); font-size: 11px; line-height: 1.25; font-weight: 900; letter-spacing: .07em; text-transform: uppercase; }
          .metric-value { color: var(--app-primary); font-size: 30px; line-height: 1.05; font-weight: 900; }
          .metric-detail { color: var(--app-muted); font-size: .78rem; line-height: 1.35; font-weight: 600; }
          .metric-icon { color: var(--app-primary); border-radius: 8px; padding: 8px; background: rgba(28,37,69,.06); }
          .metric-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
          .balance-summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
          .incident-summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
          .balance-summary-item {
              min-width: 0;
              padding: 12px 14px;
              border-left: 3px solid var(--app-accent);
              background: rgba(28,37,69,.035);
              border-radius: 6px;
          }
          .balance-value { color: var(--app-primary); font-size: 1.45rem; line-height: 1.15; font-weight: 800; }
          .metric-explainer {
              padding: 9px 12px;
              color: var(--app-primary);
              background: rgba(37,99,235,.055);
              border: 1px solid rgba(37,99,235,.16);
              border-radius: 8px;
          }
          .priority-investigation {
              border-left: 4px solid var(--app-accent);
              background: linear-gradient(90deg, rgba(224,68,3,.07), rgba(255,255,255,.98) 34%);
          }
          .priority-action { color: var(--app-warning); }
          .chart-grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(360px, 1fr); gap: 16px; align-items: stretch; }
          .content-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(360px, 1fr); gap: 16px; }
          .alert-grid { display: grid; grid-template-columns: minmax(360px, .8fr) minmax(0, 1.2fr); gap: 16px; }
          .filter-grid { display: grid; grid-template-columns: 180px minmax(220px, 1fr) 170px 170px auto; gap: 12px; align-items: end; }
          .comparison-preset-grid { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)) auto; gap: 12px; align-items: end; }
          .comparison-range-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)) auto; gap: 12px; align-items: end; }
          .cost-comparison-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)) auto; }
          .section-title { color: var(--app-primary); font-size: 1rem; line-height: 1.35; font-weight: 800; margin-bottom: 2px; }
          .section-subtitle { color: var(--app-muted); font-size: .78rem; margin-bottom: 10px; line-height: 1.45; }
          .insight-row {
              width: 100%;
              padding: 11px;
              border: 1px solid var(--app-border);
              border-radius: 8px;
              background: rgba(28,37,69,.035);
          }
          .body-text { color: var(--app-text); }
          .muted-text { color: var(--app-muted); }
          .plain-note {
              padding: 12px 14px;
              border: 1px solid rgba(224,68,3,.24);
              border-left: 3px solid var(--app-accent);
              border-radius: 8px;
              background: rgba(224,68,3,.06);
              color: var(--app-text);
          }
          .filter-summary {
              min-height: 40px;
              padding: 9px 12px;
              color: var(--app-primary);
              background: rgba(28,37,69,.055);
              border: 1px solid var(--app-border);
              border-radius: 8px;
          }
          .filter-summary .q-icon { color: var(--app-primary) !important; }
          .report-scope {
              padding: 12px 14px;
              color: var(--app-primary);
              background: var(--app-surface-soft);
              border: 1px solid var(--app-border);
              border-left: 3px solid var(--app-accent);
              border-radius: 8px;
          }
          .report-feature-grid {
              display: grid;
              grid-template-columns: repeat(3, minmax(0, 1fr));
              gap: 10px;
          }
          .ai-hero {
              background:
                  radial-gradient(circle at 88% 10%, rgba(40,210,179,.13), transparent 24%),
                  radial-gradient(circle at 8% 90%, rgba(224,68,3,.09), transparent 26%),
                  linear-gradient(180deg, #ffffff, #f7faff);
          }
          .ai-layout {
              display: grid;
              grid-template-columns: minmax(0, 1.75fr) minmax(320px, .75fr);
              gap: 16px;
              align-items: start;
              overflow-anchor: none;
          }
          .agent-hero {
              background:
                  radial-gradient(circle at 92% 12%, rgba(40,210,179,.12), transparent 25%),
                  radial-gradient(circle at 7% 92%, rgba(224,68,3,.08), transparent 28%),
                  linear-gradient(180deg, #ffffff, #f7faff);
          }
          .agent-layout {
              display: grid;
              grid-template-columns: minmax(320px, .85fr) minmax(520px, 1.35fr);
              gap: 16px;
              align-items: start;
          }
          .agent-draft-card { min-height: 650px; }
          .agent-email-body .q-field__control { min-height: 390px; }
          .agent-directory-search { flex: 1 1 280px; min-width: 280px; }
          .agent-person-result {
              min-height: 48px;
              padding: 7px 10px;
              color: var(--app-text) !important;
              border: 1px solid var(--app-border);
              border-radius: 8px;
              background: #f8fafc;
          }
          .agent-person-result .q-btn__content {
              width: 100%;
              justify-content: flex-start !important;
              gap: 10px;
          }
          .agent-review-check {
              width: 100%;
              padding: 10px 12px;
              border: 1px solid rgba(0,125,109,.23);
              border-radius: 8px;
              background: rgba(0,125,109,.055);
          }
          .device-sign-in {
              margin-top: 10px;
              padding: 13px 15px;
              border: 1px solid rgba(224,68,3,.24);
              border-left: 3px solid var(--app-accent);
              border-radius: 10px;
              background: rgba(224,68,3,.055);
          }
          .device-code {
              color: var(--app-primary);
              font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
              font-size: 1.5rem;
              font-weight: 800;
              letter-spacing: .12em;
          }
          .ai-chat-card { min-height: 680px; }
          .ai-account {
              min-height: 38px;
              padding: 8px 11px;
              border: 1px solid rgba(0,125,109,.22);
              border-radius: 8px;
              background: rgba(0,125,109,.06);
          }
          .ai-status {
              margin-top: 8px;
              padding: 11px 13px;
              border: 1px solid var(--app-border);
              border-left: 3px solid #f59e0b;
              border-radius: 8px;
              background: rgba(245,158,11,.065);
          }
          .ai-status.connected {
              border-left-color: var(--app-success);
              background: rgba(0,125,109,.055);
          }
          .ai-status.pending .q-icon { color: #b45309 !important; }
          .ai-status.connected .q-icon { color: var(--app-success) !important; }
          .ai-connection-note { color: #9a3412; font-weight: 700; }
          .ai-conversation {
              min-height: 390px;
              height: min(62vh, 720px);
              max-height: 720px;
              padding: 18px;
              overflow-y: auto;
              border: 1px solid var(--app-border);
              border-radius: 12px;
              background:
                  linear-gradient(180deg, rgba(255,255,255,.82), rgba(247,249,253,.96)),
                  #f7f9fd;
              scroll-behavior: smooth;
              scrollbar-color: rgba(28,37,69,.24) transparent;
              overflow-anchor: none;
          }
          .ai-conversation::-webkit-scrollbar { width: 8px; }
          .ai-conversation::-webkit-scrollbar-thumb {
              border: 2px solid transparent;
              border-radius: 10px;
              background: rgba(28,37,69,.24);
              background-clip: padding-box;
          }
          .ai-empty-state {
              min-height: 290px;
              padding: 28px;
              text-align: left;
          }
          .ai-message {
              width: min(94%, 940px);
              padding: 15px 16px;
              border: 1px solid var(--app-border);
              border-left: 3px solid rgba(28,37,69,.28);
              border-radius: 12px 12px 12px 4px;
              background: #ffffff;
              box-shadow: 0 7px 20px rgba(15,23,42,.055);
          }
          .ai-message.user {
              width: min(76%, 720px);
              align-self: flex-end;
              border-color: rgba(224,68,3,.24);
              border-left-width: 1px;
              border-right: 3px solid var(--app-accent);
              border-radius: 12px 12px 4px 12px;
              background: linear-gradient(135deg, rgba(224,68,3,.075), rgba(255,255,255,.96));
          }
          .ai-message > .q-row { width: 100%; }
          .ai-message-icon {
              flex: 0 0 auto;
              width: 32px;
              height: 32px;
              padding: 6px;
              color: var(--app-primary);
              border-radius: 8px;
              background: rgba(28,37,69,.07);
          }
          .ai-message.user .ai-message-icon {
              color: var(--app-accent);
              background: rgba(224,68,3,.09);
          }
          .ai-message-name {
              color: var(--app-muted);
              font-size: .68rem;
              font-weight: 900;
              letter-spacing: .06em;
              text-transform: uppercase;
          }
          .ai-message.assistant .ai-message-name {
              width: fit-content;
              padding: 3px 7px;
              color: var(--app-primary);
              border-radius: 5px;
              background: rgba(28,37,69,.055);
          }
          .ai-message-text {
              color: var(--app-text);
              width: 100%;
              font-size: .86rem;
              font-weight: 500;
              line-height: 1.62;
              overflow-wrap: anywhere;
          }
          .ai-user-text { white-space: pre-wrap; }
          .ai-message-markdown { white-space: normal; }
          .ai-message-markdown > :first-child { margin-top: 0 !important; }
          .ai-message-markdown > :last-child { margin-bottom: 0 !important; }
          .ai-message-markdown p { margin: 0 0 10px; }
          .ai-message-markdown h1,
          .ai-message-markdown h2,
          .ai-message-markdown h3,
          .ai-message-markdown h4 {
              margin: 16px 0 7px;
              color: var(--app-primary);
              font-size: .91rem;
              font-weight: 900;
              line-height: 1.35;
          }
          .ai-message-markdown ul,
          .ai-message-markdown ol { margin: 7px 0 11px; padding-left: 22px; }
          .ai-message-markdown li { margin: 4px 0; padding-left: 2px; }
          .ai-message-markdown strong { color: var(--app-primary); font-weight: 850; }
          .ai-message-markdown code {
              padding: 1px 4px;
              border-radius: 4px;
              background: rgba(28,37,69,.07);
          }
          .ai-question-input .q-field__control { min-height: 74px; }
          .ai-thinking {
              width: fit-content;
              padding: 9px 12px;
              border-radius: 8px;
              background: rgba(28,37,69,.045);
          }
          .ai-suggestion {
              min-height: 48px;
              padding: 9px 11px;
              color: var(--app-primary) !important;
              border: 1px solid var(--app-border);
              border-radius: 8px;
              background: rgba(28,37,69,.025) !important;
              font-size: .74rem;
              font-weight: 700;
              line-height: 1.35;
              justify-content: flex-start !important;
          }
          .ai-suggestion .q-btn__content {
              width: 100%;
              justify-content: flex-start;
              gap: 8px;
              flex-wrap: nowrap;
          }
          .ai-suggestion:hover { background: rgba(224,68,3,.065) !important; }
          .ai-capability {
              width: 100%;
              padding: 9px 0;
              border-bottom: 1px solid rgba(214,222,234,.75);
          }
          .toolbar-action, .primary-action {
              color: var(--app-primary) !important;
              border: 1px solid rgba(28,37,69,.16);
              border-radius: 8px;
              background: rgba(255,255,255,.82) !important;
              font-weight: 800;
          }
          .primary-action { background: rgba(224,68,3,.10) !important; border-color: rgba(224,68,3,.34); }
          .q-field--outlined .q-field__control { color: var(--app-text); background: rgba(255,255,255,.78); }
          .q-field--outlined .q-field__control:before { border-color: var(--app-border) !important; }
          .q-field__native, .q-field__input, .q-field__label, .q-select__dropdown-icon { color: var(--app-text) !important; }
          .q-table__container, .q-table, .q-table thead, .q-table tbody, .q-table tr, .q-table th, .q-table td {
              background: transparent !important;
              color: var(--app-text) !important;
              border-color: var(--app-border) !important;
          }
          .q-table__container {
              overflow: hidden;
              background: var(--app-surface) !important;
              border: 1px solid var(--app-border);
              border-radius: 10px;
              box-shadow: 0 8px 22px rgba(15,23,42,.055);
          }
          .q-table thead tr, .q-table thead th {
              color: #ffffff !important;
              background: var(--app-primary) !important;
          }
          .q-table thead th {
              min-height: 46px;
              padding: 11px 12px;
              font-size: .72rem;
              font-weight: 800;
              letter-spacing: .035em;
              line-height: 1.3;
              text-transform: uppercase;
              white-space: normal;
          }
          .q-table thead .q-icon { color: #ffffff !important; }
          .q-table tbody td {
              padding: 11px 12px;
              line-height: 1.45;
              vertical-align: top;
          }
          .q-table tbody tr:nth-child(even), .q-table tbody tr:nth-child(even) td {
              background: #f6f8fc !important;
          }
          .q-table tbody tr:hover, .q-table tbody tr:hover td {
              background: #eef3fa !important;
          }
          .q-table__bottom {
              min-height: 48px;
              color: var(--app-muted);
              background: #f8fafc;
              border-top: 1px solid var(--app-border);
          }
          .investigation-table {
              width: 100%;
              min-width: 0;
              max-width: 100%;
              overflow: hidden;
          }
          .investigation-table .q-table__middle {
              width: 100%;
              max-width: 100%;
              overflow-x: auto;
          }
          .investigation-table .q-table tbody td { min-width: 112px; font-size: .75rem; }
          .investigation-table .q-table tbody td:nth-child(7),
          .investigation-table .q-table tbody td:nth-child(8) { min-width: 260px; }
          .solar-signal-table .q-table tbody td { min-width: 96px; }
          .solar-signal-table .q-table tbody td:last-child { min-width: 220px; }
          .q-menu { background: var(--app-surface); color: var(--app-text); }
          .q-item { color: var(--app-text); }
          .footer-note { color: var(--app-muted); font-size: .72rem; text-align: center; }
          .fade-in { animation: fade-in 180ms ease-out both; }
          @keyframes fade-in { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: translateY(0); } }
          @media (max-width: 1400px) {
              .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
              .balance-summary-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
              .incident-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
              .chart-grid, .content-grid, .alert-grid, .ai-layout, .agent-layout { grid-template-columns: 1fr; }
              .filter-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)); }
              .comparison-preset-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)) auto; }
              .comparison-range-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)); }
              .cost-comparison-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)) auto; }
              .report-feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          }
          @media (max-width: 760px) {
              .sidebar { width: 86px; min-width: 86px; }
              .sidebar .sidebar-label, .sidebar .sidebar-meta, .sidebar .sidebar-brand-text { display: none !important; }
              .sidebar .logo-plate { min-height: 62px; width: 62px; align-self: center; padding: 6px; }
              .sidebar .brand-logo { width: 48px; height: 48px; }
              .sidebar .nav-btn { width: 48px; min-width: 48px; height: 48px; padding: 0; align-self: center; }
              .sidebar .nav-btn .q-btn__content { justify-content: center !important; gap: 0; }
              .toolbar { padding: 12px 16px; }
              .content-wrap { padding: 14px; }
              .metric-grid, .filter-grid, .comparison-preset-grid, .balance-summary-grid, .incident-summary-grid { grid-template-columns: 1fr; }
              .cost-comparison-grid { grid-template-columns: 1fr; }
              .report-feature-grid { grid-template-columns: 1fr; }
              .ai-message { width: 100%; }
              .agent-directory-search { min-width: 100%; }
          }
        </style>
        """
    )
