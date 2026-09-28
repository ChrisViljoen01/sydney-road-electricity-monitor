# Sydney Road Electricity Monitor

Local NiceGUI application for extracting, analysing and visualising PNPSCADA
readings for the Sydney Road meter accounts. It retains the proven authenticated
Profile Graph Download CSV workflow and adds an owner-facing dashboard for
electricity use, load, demand, unusual-reading review and reporting downloads.

## Dashboard capabilities

- Overview KPIs for electricity used (kWh), average daily use, highest working
  load (kW), highest total demand (kVA), and unusual days.
- Area and date filtering with 7-day, 30-day, MTD, YTD and all-data shortcuts.
- Daily and weekly kWh trends, energy share, typical half-hour load profile and
  peak-demand comparison by area.
- A dedicated Supply & Demand view comparing combined warehouse use with
  solar generation at matching half-hour timestamps. It shows daily energy balance,
  typical load-versus-solar profiles, estimated grid requirement and area demand.
- A Solar Performance view measuring recorded solar generation, average and best
  days, peak output, productive hours, daily/weekly/monthly trends, typical output
  through the day, reading quality and solar readings requiring review.
- A Solar Investment view with Current Performance, Proposal Option 1 and
  Proposal Option 2. It separates PNPSCADA actuals from vendor forecasts and
  shows cost before solar, solar savings, cost after solar, energy flow,
  battery assumptions, investment, payback and cumulative cash flow.
- A Comparisons view with explicit year-versus-year, month-versus-month,
  week-versus-week, and two fully independent custom date ranges.
  It compares warehouse use, kW, kVA, solar generation, unusual readings and each
  warehouse area's change.
- A Cost Centre that applies verified eThekwini CTOU rates to Warehouses 6-8 and
  Business & General Scale 1 to Warehouse 9. It estimates VAT-inclusive energy,
  demand, service and network-surcharge components, applies solar savings to
  Warehouse 8, compares periods and breaks cost down by warehouse area.
- Plain-language statements comparing the selected period with a complete
  preceding equal-length period.
- An unusual-usage register that states what the meter observed, where and
  when it occurred, and the first operational check to make.
- A Connect YMS-style shell with fixed left navigation, compact toolbar,
  a light workspace, navy sidebar, dense KPI cards and Connect Logistics branding.
- A Reports Center for current-month, filtered-overview, usage-comparison and cost-comparison management PDFs.
- A data-restricted AI Hub that answers questions from the local PNPSCADA
  analytical database. Verified calculations continue to work without
  Microsoft access; licensed users can add a Microsoft Copilot explanation
  after organisational consent and sign-in.
- Filtered XLSX workbooks with Summary, Daily Usage and Unusual Usage sheets,
  plus filtered CSV downloads for detailed analysis.
- Direct downloads of the source daily, weekly, spike and half-hour CSV records.
- Background PNPSCADA sync using the login stored in Windows Credential Manager.
- A persistent SQLite history database containing all portal history available from 9 November 2023.
- Incremental updates that start from the latest stored day and replace duplicate
  account/timestamp readings, so refreshes never discard or duplicate history.

## Configured meter accounts

| Area | Account | Code | PNPSCADA entity ID |
|---|---|---:|---:|
| Connect Logistics Solar | 265 Sydney Rd Connect Logistics (Solar) | 35775386 | 9987 |
| Warehouse 6 | 265 Sydney Rd WH 6 | E9623 | 10005 |
| Warehouse 7 | 265 Sydney Rd WH 7 | E9607 | 10006 |
| Warehouse 8 | 265 Sydney Rd WH 8 | E9615 | 10004 |
| Warehouse 9 | 265 Sydney Rd WH 9 | E988 | 10105 |

## Installable Windows desktop app

Send employees the single installer file:

`dist\installer\Sydney_Road_Electricity_Monitor_Setup_1.1.0.exe`

The installer creates Start-menu and desktop shortcuts. Employees launch the
application from either shortcut; it starts locally and opens the dashboard in
their selected default browser. Python, NiceGUI, and the project source do not
need to be installed separately on an employee PC.

The installer includes a verified snapshot of the current historical database.
On first launch, that snapshot is copied into the employee's writable local data
folder:

`%LOCALAPPDATA%\Connect Logistics\Sydney Road Electricity Monitor`

All subsequent readings, tariff data, generated reports, spreadsheet exports,
CSV files, and logs remain in that folder. Reinstalling or upgrading the program
does not replace an employee's existing local database.

There is one shared PNPSCADA account for all installations. On each Windows
profile, open **Data Update**, select **Set up shared portal login**, and enter
that same Connect Logistics username and password once. Every later **Update
meter data** action uses that shared account and incrementally refreshes from the
latest stored reading through the current date. Recent updates use a fast merge:
only the affected dates and their five-week alert baselines are recalculated,
while the complete SQLite history remains available for every dashboard view.

To create a new installer after app or data changes, run:

```powershell
.\packaging\build_windows.ps1
```

The build creates a consistent SQLite backup, bundles the Connect branding and
application runtime, and compiles the distributable installer under
`dist\installer`.

## One-time environment setup

Double-click `Setup Environment.bat`. This creates an isolated `.venv`, updates
pip inside it, and installs the pinned packages from `requirements.txt`.

The application currently pins NiceGUI 3.14.0 and uses its built-in Apache
ECharts integration, FastAPI server and browser UI. No system-wide pip packages
are changed. The setup also connects the bundled spreadsheet runtime used by
the Reports Center, installs ReportLab for local PDF creation, and installs
PyPDF for reading the municipality's official tariff schedule.

## Start the dashboard

Double-click `Start Electricity Tool.bat`, or run:

```powershell
.venv\Scripts\python.exe -m electricity_tool
```

The dashboard opens at `http://127.0.0.1:8080`. Use **Data Update → Update portal
login** to validate and save the existing PNPSCADA login. The password is never
written into this project or any export. Windows encrypts it for the current
Windows user.

Use **Update meter data** to fill any missing historic dates and refresh the latest
stored day through today. PNPSCADA accepts date windows, so gaps are downloaded in
manageable chunks and duplicate half-hour rows are replaced. The dashboard remains
responsive while the extraction runs.

Dates in the application are inclusive. For example, 2026-07-01 through
2026-07-14 requests readings from midnight at the start of 1 July through
midnight after 14 July.

## AI Hub and Microsoft sign-in

Open **AI Hub** from the sidebar to ask app-specific questions such as when the
highest usage occurred, which warehouse used the most electricity, where
unusual readings happened, or how much CTOU electricity fell in Peak, Standard
and Off-peak periods. Each independent question starts with every meter area and
the complete locally stored history, regardless of the filters used on any
dashboard page. A date, month, year or area named in the question focuses only
that answer. Incomplete meter days are excluded and the answer states when
operational evidence is needed to confirm a physical cause.

The conversation retains the AI-only date and area scope for genuine short
follow-ups, accepts explicit dates in ISO, day/month/year or named-month format,
asks a focused clarification when a materially incomplete current week could be
confused with the last complete week, and automatically keeps the newest turn
visible. An independent question starts a fresh grounded Microsoft conversation
so an older narrow scope cannot leak into it. Microsoft responses are rendered
as formatted Markdown rather than displaying raw formatting characters.

The Hub is useful while Microsoft approval is pending: it provides a verified
local answer calculated directly from the database. Once the Entra permissions
are approved, select **Sign in with Microsoft**. The signed-in employee's
Microsoft 365 Copilot licence is then used to explain the same verified evidence
in plain language. Web search is disabled and Copilot receives only the compact
facts required for the question, not the SQLite database or PNPSCADA login.

The desktop app uses Microsoft public-client authentication, so it has no client
secret to distribute. Its tenant and client IDs can be overridden for another
environment with `CONNECT_AI_TENANT_ID` and `CONNECT_AI_CLIENT_ID`. The Microsoft
session cache is encrypted for the current Windows user and stored with the
application's local data. Signing out removes that cached account.

The Microsoft 365 Copilot Chat API uses Microsoft-managed automatic model
routing. The model picker available in the Microsoft Copilot/Cowork interface,
including Claude choices enabled for an employee, is not exposed as a model
parameter by the Chat API.

The registered Entra application requests delegated Microsoft Graph permissions
for the Copilot evidence workflow and Agent Centre, including `User.Read`,
`Mail.Read`, `Mail.ReadWrite`, `Mail.ReadWrite.Shared`,
`Mail.Send`, `Mail.Send.Shared`, `Sites.Read.All`, `People.Read.All`,
`OnlineMeetingTranscript.Read.All`, `Chat.Read`, `ChannelMessage.Read.All` and
`ExternalItem.Read.All`. Administrator consent is required where the Connect
Logistics tenant policy requires it. The app reports a clear approval or licence
message when Microsoft returns an access-denied response.

## Agent Centre

Open **Agent Centre** to prepare an internal email for the active filters or a
specific unusual-usage alert. The app first builds a deterministic draft from
verified PNPSCADA evidence. When the renewed Microsoft token is available,
Copilot can improve the wording while the numeric evidence remains grounded in
the local analytical database.

Recipients can be typed directly or selected from Microsoft People results using
the approved `People.Read.All` permission. An optional PDF report uses the same
active dates and meter area. The employee can save the message to Outlook
Drafts or send it after reviewing the recipients, evidence, subject and body.
Manual alert and report emails always require an explicit send confirmation.
Shared-mailbox actions also require the signed-in employee to already have the
appropriate Exchange mailbox rights.

The Agent Centre also contains a bounded autonomous rule for successful meter-data
updates. After **Update meter data** completes successfully, one confirmation email
is sent to the configured `CONNECT_REFRESH_AGENT_RECIPIENT` (Christopher Viljoen by
default) and recorded in the local communication history. Failed updates, page loads
and dashboard navigation never trigger this rule. If Microsoft sign-in has expired,
the data update remains successful and the app reports the email-delivery failure
separately.

Default recipients and the optional sender mailbox are stored only on the local
PC. The communication history is metadata-only: it records the action, time,
subject, recipients and attachment names, but does not store email bodies or
Microsoft access tokens.

## Reports Center

Open **Reports Center** from the sidebar. The PDF report uses the selected meter
area and the exact app scope for the chosen report type:

- Current month overview: available readings from the first of the current month through the latest closed day, with current-month cost projection and no comparison.
- Filtered overview: the active app date and meter filters, with no comparison.
- Usage comparison: the selected and comparison ranges saved on the Comparison page, including the chosen comparison method.
- Cost comparison: the selected and comparison ranges saved in Cost Centre, including solar savings and current-month projection.

Each PDF includes a management summary, recommended checks, directly labelled
charts, daily/weekly/monthly supporting detail, exact kW and kVA peak times,
data-quality status, unusual readings and a follow-up checklist. Comparison
values appear only in comparison reports. Current-day placeholder readings are excluded.
The Solar account is shown separately and is not added to warehouse consumption;
it records electricity generated and supplied by the solar installation.
The workbook and CSV buttons use the complete active From, To and meter-area
filters shown at the top of the page.

## Supply & Demand

The Supply & Demand tab is a site-level view and always uses all warehouse meters.
It compares their combined load with solar generation at matching
half-hour intervals. The Overview page shows only the solar contribution,
estimated grid energy needed and the time warehouse load was above solar generation.

The grid requirement is calculated as warehouse demand remaining after recorded
solar generation; it is not a reading from a main grid meter. The view therefore does
not indicate whether transformer or contracted supply capacity was exceeded. The
main grid meter is still required before any surplus solar generation can be
confirmed as electricity exported to the grid.

## Historical database

`electricity_history.db` is the application's permanent local source of truth.
It stores the granular half-hour readings using `(account_eid, timestamp)` as the
unique key. Existing successful exports from 9 November 2023 onward are imported
once when the database is first created. Daily, weekly, MTD, YTD, kW, kVA and
unusual-usage analytics are recalculated from this stored interval history.

The timestamped folders under `exports` remain the raw audit trail for each portal
download. The files under `exports/_dashboard_current` are rebuilt from the database
and match the complete dataset shown in the dashboard.

## Cost Centre and tariff checks

The Sydney Road address falls within eThekwini Municipality. The May, June and
July 2026 recovery bills confirm this working mapping:

| PNPSCADA area | Portal meter | Recovery-bill reference | Cost treatment |
|---|---|---|---|
| Warehouse 6 | E9623 | Account 840 / E9623 | Commercial Time of Use (CTOU) |
| Warehouse 7 | E9607 | Account 839 / E9607 | Commercial Time of Use (CTOU) |
| Warehouse 8 | E9615 | Account 840 / E9615 | CTOU; solar credit is applied here |
| Warehouse 9 | E988 | Account 841 / E89880 T | Scale 1 area rate; bill meter ID differs |

The Admin Office/common-area allocation and the supplemental Warehouse 8 meter
E1723 do not have matching PNPSCADA meters. They are disclosed in the app but
excluded from calculated cost. PNPSCADA remains the source of truth for every
kWh used in dashboard calculations; bill kWh is used only to validate the tariff
and meter relationships.

The app applies the rate effective on every half-hour, so a period crossing
1 July is split across municipal financial-year schedules. CTOU energy is
classified into Peak, Standard and Off-peak using the published season,
weekday/weekend and public-holiday rules. CTOU demand, minimum demand, service,
network surcharge and VAT are calculated separately. Demand remains an exposure
estimate because the PNPSCADA profile kVA has not matched the billed monthly
demand register consistently.

For 1 July 2026 through 30 June 2027 the current VAT-inclusive rates include:

- Scale 1: **R4.6420/kWh** and **R607.38/month** service charge.
- CTOU high season: **R8.0493 Peak**, **R4.0275 Standard** and
  **R1.9620 Off-peak per kWh**.
- CTOU low season: **R3.9713 Peak**, **R3.1949 Standard** and
  **R1.8584 Off-peak per kWh**.
- CTOU demand: **R171.78/kVA/month**, service **R852.40/month**, and a
  **25% network surcharge** on energy plus demand when monthly demand reaches
  110 kVA.

Matched solar is valued at the Warehouse 8 CTOU band in the same half-hour and
shown as solar savings. Possible excess generation is not called export unless
a main grid meter confirms it. The Cost Centre shows cost before and after those
savings, supports current-month versus matching previous-month, full past-month
and custom comparisons. Current-month projection uses complete meter days only,
so a partially populated day cannot pull the forecast artificially downward.

The app checks eThekwini's official document page shortly after startup, every
24 hours while running, when meter data is updated, and whenever **Check official
tariff now** is selected. It downloads the final tariff PDF, reads both Scale 1
and CTOU energy, demand, service and surcharge components, and keeps a
last-known-good local cache. A failed web check never overwrites verified rates.

The Cost Centre is an operational estimate, not an invoice reconciliation.
Voltage rebates, penalties, deposits, landlord adjustments, water, sewerage and
other non-electricity recovery lines are not inferred from PNPSCADA.

## CSV outputs

Each run creates a timestamped folder below `exports` containing:

- `interval_readings.csv` - the granular meter samples, raw P/Q registers,
  PNPSCADA vector/scalar kVA, and source status (`Ok`, `Calc`, etc.).
- `daily_summary.csv` - daily consumption and power fluctuations.
- `weekly_summary.csv` - Monday-to-Sunday account summaries.
- `daily_meter_record.csv` - the internal area record with day-on-day changes,
  matching-weekday baselines, kW/kVA peaks, alerts, and investigation fields.
- `weekly_meter_record.csv` - weekly area review, prior-week comparable usage,
  alert counts, reviewer, comments, and actions.
- `spike_register.csv` - only flagged consumption/demand days, with exact peak
  times and fields for cause, corrective action, owner, and closure.
- `solar_positive_register.csv` - higher-than-usual solar-generation days kept
  separate from usage incidents and marked for inverter/weather verification.
- `monthly_summary.csv` - one row per account and calendar month.
- `month_to_date_summary.csv` - the end-date month's cumulative totals.
- `year_to_date_summary.csv` - the end-date year's cumulative totals.
- `raw/` - the original Profile Graph CSV downloads for audit and troubleshooting.
- `run_manifest.json` - extraction window, accounts, row counts, and warnings.

The internal data fields retain the portal's electrical direction names. In the
dashboard, `import_kwh` is labelled **Electricity used**: energy on the incoming
P1 meter channel for the area. Energy on the outgoing P2 channel is kept
separately as electricity sent back.

The main normalized fields are `kw_import`, `kw_export`, `kw_net`, `kvar_net`,
`kva`, `import_kwh`, and `net_kwh`. PNPSCADA's vector `S (per kVA)` is used
directly; scalar S is retained as `raw_scalar_s` for audit. If a tenant ever
omits S, kVA falls back to `sqrt(kW^2 + kVAr^2)`, recorded in `kva_method`.

The portal excludes a sample exactly equal to the requested start boundary.
The tool safely prefetches the prior day, then filters the normalized export
back to the inclusive dates selected in the application. This preserves the
midnight sample and a complete 48 intervals per normal day.

Comparison charts use absolute kWh as the primary measure of operational
change. Percentages are shown only when both periods contain complete readings
and the meter volume is material. Days containing large numbers of portal
`Calc` placeholders are excluded as matched current/previous day pairs, and
the affected area is shown in grey as `Incomplete data`. This prevents a tiny
standby meter or an incomplete zero-filled day from visually dominating the
warehouse comparison.

The Comparison page provides direct selectors for two calendar years, two
calendar months, or two Monday-to-Sunday weeks. Partial current periods are
matched to the same number of elapsed days in the other period. The custom
option accepts four exact dates. Applying the global `From`, `To`, or time-period
filters switches the page to custom ranges so the displayed scope always agrees
with the calculated results.

## Proactive monitoring and spike rules

Daily usage is compared with the previous three to four matching weekdays so
normal weekday/weekend patterns do not create misleading comparisons. A
consumption spike requires both a 20% increase and at least 50 kWh above the
baseline. A demand spike requires both a 15% increase and at least 5 kVA above
baseline. These are transparent starter thresholds recorded in every run's
manifest and can be adjusted after operational review.

Solar uses a separate interpretation. A materially higher output day is a
positive event, not an unusual-usage incident, and is placed in the solar
positive register for verification. A low-generation warning requires a
complete day at least 30% and 30 kWh below the recent matching-weekday average.
Low solar warnings remain actionable because weather, inverter availability,
faults, outages or curtailment may explain the shortfall.

Meter data identifies the area, date, peak time, size, and type of variance. It
cannot know the operational cause by itself. The cause, corrective action,
responsible person, and closure fields in the meter and spike records are for
the internal team to complete after checking site operations.

## Command-line and scheduled use

After saving the login once from the GUI:

```powershell
.venv\Scripts\python.exe -m electricity_tool.cli extract --start 2026-01-01 --end 2026-07-14
```

The command-line tool reads the saved Windows credential. It also supports the
`PNPSCADA_USERNAME` and `PNPSCADA_PASSWORD` environment variables. Do not put a
password directly in a scheduled-task command.

### Daily peak-usage email through GitHub Actions

`.github/workflows/daily-peak-usage.yml` schedules one daily job at **07:00
Africa/Johannesburg time** (05:00 UTC). It runs only on a self-hosted Windows
runner labelled `windows` and `electricity-monitor`, refreshes the recent
PNPSCADA reading window, requires complete prior-day data for every configured
meter, forces the same official eThekwini tariff check available in Cost Centre,
and sends one consolidated HTML CTOU Peak-period email. For Warehouses 6–8 it
sums the kWh consumed specifically inside municipal Peak time bands, estimates
the VAT-inclusive base Peak energy charge before any conditional network
surcharge, displays the exact R/kWh rate applied, and compares each warehouse with its
previous three to four matching complete weekdays. A strict action flag requires
both a 10% increase and at least 5 kWh above that Peak-band baseline. The email
includes the highest load observed inside a Peak interval, the full CTOU
time-band table, applicable rates, official tariff provenance, and a bounded
load-shifting checklist. Warehouse 9 is disclosed but excluded because it uses
Scale 1; solar is generation and is also excluded. A sent-date marker in the
SQLite database prevents reruns from sending a duplicate email.

The same email includes current-month CTOU demand exposure for Warehouses 6–8:
the maximum PNPSCADA kVA recorded during Peak or Standard periods, its exact
time, variance against the 110 kVA surcharge threshold, month-to-date Peak kWh,
and an estimated surcharge when the threshold is reached. The surcharge estimate
applies 25% to month-to-date energy plus the monthly demand charge, then VAT. It
is labelled **Estimated MTD 25% surcharge**, not total electricity cost or
Peak-hour energy cost. A below-threshold row shows the conditional amount only
if UMFA's billed demand register still triggers the surcharge. UMFA's register
is authoritative, and solar-credit reassignment between warehouse bills cannot
be inferred from PNPSCADA.

For the previous day, the email includes a table explicitly titled **Electricity
used during CTOU Peak time bands**. Consecutive half-hour readings are grouped
into the municipality's applicable Peak windows, such as 07:00–09:00 and
18:00–21:00 on low-season weekdays. Standard and Off-peak consumption is
excluded. The table shows each window's kWh, average kW, VAT-inclusive Peak
R/kWh rate and resulting Peak energy cost before any applicable network surcharge.
The stretch target is zero flexible-load kWh in each Peak time band; essential
refrigeration, safety, security and operational base load must be identified and
documented rather than switched off indiscriminately.

The closing **Current-month cost outlook** reuses the app's Cost Centre model.
It shows estimated bill-to-date, projected month-end bill, projected month-end
kWh, total month-to-date CTOU Peak-hours kWh and its VAT-inclusive base energy
cost. The bill estimate includes modeled energy, demand, service, VAT, network
surcharge and matched solar savings, while disclosing that landlord/UMFA
adjustments, bill-only meters and unconfirmed solar-credit reassignment are
outside PNPSCADA.

When the previous day has no applicable CTOU Peak time band, the email suppresses
all zero-value Peak KPI and comparison rows. It states that no Peak band applied
and shows the latest prior complete day with Peak hours instead. Current-month
demand, surcharge and bill outlook values continue through the actual previous
complete day.

On its first run against a new data directory, the job backfills the prior
35-day comparison window from PNPSCADA. Later runs request only missing dates
plus the recent repair window. If any CTOU warehouse has fewer than three
complete matching weekdays after refresh, the job fails without sending rather
than publishing an unassessed comparison.

Configure the runner and GitHub repository as follows:

1. Install the runner under the **same Windows user** that has saved the
   PNPSCADA login and completed the app's Microsoft sign-in. The Microsoft token
   cache is encrypted with Windows DPAPI and cannot be used by another account.
2. Add repository secret `CONNECT_DAILY_PEAK_RECIPIENTS` containing the
   semicolon-separated production email recipients.
3. Prefer the existing Windows Credential Manager PNPSCADA login. If the runner
   account cannot hold it, add `PNPSCADA_USERNAME` and `PNPSCADA_PASSWORD` as
   repository secrets instead.
4. Set repository variable `SYDNEY_ROAD_DATA_DIR` to a durable writable folder
   outside the checkout, for example
   `C:\ProgramData\Connect Logistics\Sydney Road Electricity Monitor`. The
   runner account must first start the desktop app with this variable set and
   sign in to Microsoft so its encrypted token cache is created in that folder.
5. Keep the real meter configuration outside a public checkout. Copy
   `accounts.example.json` to the durable data folder as `accounts.json`, enter
   the real meter values, and set repository variable
   `SYDNEY_ROAD_ACCOUNTS_FILE` to that full file path.

The workflow exits without sending when previous-day readings are incomplete or
the Microsoft token is unavailable. Use **Run workflow** for a controlled
recovery run after correcting the reported condition.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
