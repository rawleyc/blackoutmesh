# BlackoutMesh Dashboard Redesign: WBS and Change Specification

## 0. End goal

An authorized operator can **safely compose, verify and send a signed alert**, and can see **what was sent and whether the transmitter is working**. A separate, clearly labeled **planner** helps decide how much redundancy is needed. **Nothing on screen may imply that real coverage is known**, because phones never report back.

### Design rules (every change below follows these)

1. **Truth in labeling.** Live data and simulated data are never mixed, and every simulated number says so.
2. **One-way protocol.** There is no delivery confirmation. The only live signal is "the transmitter is working and N phones fetched directly from this laptop (first hop only)".
3. **Sending is high stakes.** What the operator previews must be exactly what gets signed, and every send is logged.
4. **Live never waits on the simulation.** A long simulation must never delay or block a send.
5. **Works offline.** In a blackout the dashboard cannot depend on the internet.

---

## 1. What I know and what I assumed

I have **not seen your dashboard's source code**. This document is written from the screen text of your two dashboard snapshots:

- Header: title, status badge (`ERROR` earlier, `STANDBY` now), key fingerprint.
- Map: "Kraków Mesh Coverage", legend (Seeds, Reached, Unreached), zoom control, Esri attribution.
- Stat cards: Coverage, Broadcasts, Duplicates, Suppressed. Charts: Coverage Over Time, Broadcasts & Redundancy.
- Dispatcher: codebook templates (TID 101 to 104, 999), target sites, Seq, Duration, TTL, PL/EN/UA preview, Sign & Broadcast (Ed25519).
- Laptop Official BLE Transmitter: standby state, "phone(s) reached" count, Start BLE Beacon.
- Simulation Controls: Civilians, Duration, Radio Range, Run Simulation.

Because I can't see code, this spec describes **behavior, exact on-screen text, validation rules, and an API contract**. Step 0 has you fill in a short inventory so that every item below can be mapped to your real file names. **If you paste the source, I can turn each work package into exact code diffs.**

Anything marked **VERIFY** is something I could not confirm from the screenshots.

---

## 2. Target layout

```
+--------------------------------------------------------------------------+
| BlackoutMesh  DTN Tactical Console   [ STANDBY ]   Key: 9d2b0f03...a1c4  |
|                                      [TEST KEY]    System time: 14:03 UTC |
+-------------------------------------+------------------------------------+
|  LIVE OPERATIONS         [blue strip]|  COVERAGE PLANNER   [amber strip]  |
|                                      |  SIMULATION ONLY. Not live data... |
|  1. Alert Dispatcher                 |  Map: Krakow Mesh Coverage (sim)   |
|  2. Live Transmitter                 |  Simulation controls + results     |
|  3. Sent Alerts Log                  |  Stats (sim) and charts (sim)      |
+-------------------------------------+------------------------------------+
```

- Under 900 px width, stack the columns with **LIVE OPERATIONS first**.
- LIVE panels get a solid blue header strip labeled `LIVE`.
- PLANNER panels get an amber, diagonal-hatched header strip labeled `SIMULATION`.
- The simulation banner is **always visible and cannot be dismissed**.

---

## 3. Exact on-screen text changes

Copy these strings exactly.

| Element | Current text | New text |
| --- | --- | --- |
| Map panel title | Kraków Mesh Coverage | **Coverage Planner (Simulation)** |
| Banner under title (new) | none | **SIMULATION ONLY. Not live data. Phones do not report back, so real coverage cannot be measured.** |
| Map caption (new) | none | **Kraków Mesh Coverage (simulated example run)** |
| Legend | Seeds / Reached / Unreached | **Seeds / Reached (simulated) / Unreached (simulated)** |
| Stat card 1 | Coverage | **Reached (sim)** |
| Stat card 2 | Broadcasts | **Broadcasts (sim)** |
| Stat card 3 | Duplicates | **Duplicates (sim)** |
| Stat card 4 | Suppressed | **Suppressed (sim)** |
| Chart 1 title | Coverage Over Time | **Coverage Over Time (sim)** |
| Chart 2 title | Broadcasts & Redundancy | **Broadcasts & Redundancy (sim)** |
| Stat placeholder | a single dash symbol | **Run a simulation to see results** |
| Dispatcher title | Authority HSM Dispatcher | **Alert Dispatcher** (see WP 7.5 about the word HSM) |
| Dispatcher sub-line (new) | none | **Signing key: {key label}, {fingerprint short}** |
| Wire size label | Compact Wire (\~94 B) | **Packet size: {N} bytes (limit {MAX})**, computed live, never hard-coded |
| Target field | Target Evacuation / Shelter Site | **Target site** |
| Seq field | Seq (Update #) | **Update number** |
| TTL field | TTL | **Max hops (TTL)** |
| Language tabs | PL / EN / UA tabs | **Preview in all languages** (three stacked blocks) |
| Send button | Sign & Broadcast (Ed25519) | **Review and send** (VERIFY what the button does today, see WP 0.3) |
| Transmitter count | 0 phone(s) reached (0 transfers) | **0 first-hop transfers (approximate)** |
| Transmitter footnote (new) | none | **Counts phones that fetched the alert directly from this laptop. Phones that receive it from other phones cannot be counted. Approximate, because phone addresses change.** |
| Sim button | Run Simulation | **Run simulation** |
| Sim results line (new) | none | **In {N} runs, at least 90% were reached within {T} s in {X}% of runs. Average coverage {M}%. Worst 10% of runs: {Q}% or less.** |

Keep the Esri attribution text. It is a licensing requirement for the basemap.

---

## 4. Status badge: exact rules

The header badge has three states. Evaluate top to bottom, first match wins.

| State | Condition | Badge color | Extra |
| --- | --- | --- | --- |
| **ERROR** | Any row in the error table below is true | Red | Click the badge to list reasons |
| **TRANSMITTING** | Beacon is on and the loaded alert has not expired | Blue, pulsing | Shows time remaining |
| **STANDBY** | Key loaded, transmitter ready, no beacon running | Grey | Text "Ready" |

### Error reasons (each has a fixed message)

| Condition | Message shown |
| --- | --- |
| Signing key missing or unreadable | **Signing key not available.** |
| Bluetooth adapter missing or failed | **Bluetooth transmitter unavailable.** |
| Dashboard cannot reach its own server for 3 checks in a row (about 6 s) | **Lost contact with the dashboard server. Do not rely on the status shown.** |
| Audit log integrity check failed | **Audit log integrity check failed.** |
| Codebook missing a language for any template | **Codebook is incomplete.** |

A running simulation **never** changes this badge.

---

## 5. Validation rules (enforced in the page AND on the server)

The server is the authority. Page checks are only for speed and friendly messages.

| Field | Rule | Message |
| --- | --- | --- |
| Template | Must exist in the codebook | **Choose a template.** |
| Target site | Required when the template is marked `needs_target` in the codebook | **Choose a target site for this template.** |
| Update number | Whole number, 1 or more, **greater than the last sent update number** for this alert chain. Auto-filled with last + 1. VERIFY the client accepts only a higher number | **Update number must be higher than {last}.** |
| Duration (min) | Whole number from 5 to 1440. Default 120 | **Duration must be 5 to 1440 minutes.** |
| Max hops (TTL) | Whole number from 1 to 15. Default 15 | **Max hops must be 1 to 15.** |
| TID 999 free text | Required, not empty, **at most 60 BYTES in UTF-8**, not 60 characters. A counter shows bytes left | **Free text is {n} bytes. Maximum is 60.** |
| Packet size | At most `MAX_PACKET_BYTES` = 182 (fits one Bluetooth read at the app's requested size) | **Packet is {n} bytes. Maximum is 182.** |
| Languages | PL, EN and UA text must exist for the chosen template | **Missing translation: {language}.** |

Why bytes: Polish and Ukrainian letters take up to 2 bytes each in UTF-8, so 60 characters can be 120 bytes. **VERIFY** which one your current code counts, and enforce bytes.

Put all limits in **one shared constants file** (see WP 9.1) so the page, server and Android app cannot disagree.

---

## 6. Proposed API contract

**Illustrative names.** Map these to your real routes in Step 0.

| Purpose | Request | Response (key fields) |
| --- | --- | --- |
| Status poll (every 2 s) | `GET /api/status` | `state`, `reasons[]`, `key {fingerprint, label}`, `transmitter {state, alert_id, expires_at, first_hop_transfers}`, `server_time_utc` |
| Preview | `POST /api/alerts/preview` with `{template, target, update, duration_min, ttl, free_text?}` | `errors[]`, `rendered {pl, en, ua}`, `packet_bytes`, `preview_id` |
| Send | `POST /api/alerts/send` with the same fields plus `preview_id` | `alert_id`, `packet_sha256`, `issued_at`, `expires_at` |
| Beacon | `POST /api/transmitter/start` and `/stop` | new `transmitter` state |
| Sent log | `GET /api/alerts` | list of log entries (see WP 5.1) |
| Start simulation | `POST /api/sim/jobs` with `{civilians, seeds, duration_s, range_m, runs, scenario}` | `job_id` |
| Simulation progress | `GET /api/sim/jobs/{job_id}` | `state`, `progress`, `results` when done |
| Cancel simulation | `POST /api/sim/jobs/{job_id}/cancel` | `state` |

**Rule:** `send` is rejected unless `preview_id` matches a preview made in the last 5 minutes for exactly the same fields. This guarantees that what the operator saw is what gets signed.

---

## 7. Work Breakdown Structure

Size key: **S** up to 2 hours, **M** about half a day, **L** 1 to 2 days. Each package says exactly what to change and what "done" looks like.

### 0.0 Prepare (do not skip)

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 0.1 | Back up: copy the whole dashboard folder, or `git init`, `git add .`, `git commit -m "before redesign"`, then `git tag before-redesign` | You can restore the old dashboard in under a minute | S | none |
| 0.2 | Fill the **Inventory table** (Section 9) with real file names, routes, element IDs and function names | Every cell is filled or marked "unknown" | S | 0.1 |
| 0.3 | Answer two questions in writing. (a) What exactly does **Sign & Broadcast** do today: sign only, sign and load the transmitter, or sign and start the beacon? (b) Where do the Coverage, Broadcasts, Duplicates and Suppressed numbers come from: the simulation, or real transmitter data? | Both answers are written down | S | 0.2 |
| 0.4 | Take "before" screenshots at desktop width and at 700 px width | Four images saved | S | 0.1 |

### 1.0 Truthful labeling (text only, no logic changes: do this first)

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 1.1 | Apply every row of Section 3 that is a plain text change (titles, legend, stat labels, chart titles, buttons, field names) | Test T1 passes | S | 0.2 |
| 1.2 | Add the simulation banner under the Planner title, not dismissible | Banner visible on every load and after every resize | S | 1.1 |
| 1.3 | Replace the transmitter count text and add the footnote | Panel shows "first-hop transfers (approximate)" plus the footnote | S | 1.1 |
| 1.4 | Replace the hard-coded "\~94 B" with a computed packet size | Changing the template or free text changes the number | S | 0.3 |

### 2.0 Layout

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 2.1 | Two-column grid: LIVE OPERATIONS on the left, COVERAGE PLANNER on the right. Stack under 900 px with LIVE first | Test T2 passes | M | 1.x |
| 2.2 | LIVE header strip: solid blue with the word `LIVE`. PLANNER header strip: amber, diagonal hatch, the word `SIMULATION` | Strips visible, text readable | S | 2.1 |
| 2.3 | Move Simulation Controls directly under the map inside the Planner column | No simulation control remains in the Live column | S | 2.1 |
| 2.4 | Header: status badge, key fingerprint shortened to first 8 plus last 4 characters with a copy button, key label (`TEST KEY` or `PRODUCTION KEY`), system time in UTC | Fingerprint copy works. Label changes when a different key is loaded | S | 2.1 |

### 3.0 Live status and transmitter

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 3.1 | Server: implement the status endpoint (Section 6). Page: poll every 2 s | Stopping the server turns the badge red within 8 s with the "Lost contact" message | M | 2.4 |
| 3.2 | Implement the state rules from Section 4 exactly | Tests T3 and T4 pass | M | 3.1 |
| 3.3 | Transmitter panel shows: state, current alert id, **time remaining** (counts down), first-hop transfers | Countdown reaches zero and the panel returns to Standby | M | 3.1 |
| 3.4 | Beacon stops **automatically** at expiry | After expiry, nothing is advertised (check with nRF Connect) | S | 3.3 |
| 3.5 | Button toggles between **Start BLE Beacon** and **Stop BLE Beacon**. Disabled when no signed alert is loaded | Button cannot be clicked with nothing loaded | S | 3.3 |
| 3.6 | Show the first-hop counter with the footnote. Do not show anything labeled "reached" in the Live column | Search the Live column for the word "reached": none found | S | 3.3 |

### 4.0 Dispatcher hardening

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 4.1 | Implement all validation rules from Section 5 on the server, then mirror them in the page | Each rule rejects a bad value with the exact message | M | 0.3 |
| 4.2 | Auto-fill **Update number** with last sent + 1 | Field is pre-filled after every send | S | 5.1 |
| 4.3 | Live **packet size** meter and **bytes left** counter for TID 999 | Typing a Polish letter reduces bytes left by 2 | S | 1.4 |
| 4.4 | **Preview in all languages**: PL, EN and UA stacked and visible at once. Block Review and send if a language is missing | Removing one translation from the codebook disables sending | M | 2.1 |
| 4.5 | Implement preview and send as two steps with `preview_id` (Section 6) | A send with a changed field, or a `preview_id` older than 5 minutes, is rejected | M | 4.1 |
| 4.6 | Confirmation dialog (text below) | Dialog shows correct values for every field | M | 4.5 |
| 4.7 | TID 999 extra step: show the exact text, warn that it bypasses the reviewed codebook, require a tick box "I have checked this text" | Send is disabled until the box is ticked | S | 4.6 |
| 4.8 | Disable the confirm button after one click until the server replies, and make `send` safe against double submission | Double-clicking creates exactly one log entry | S | 4.6 |

**Confirmation dialog text**

```
Title:  Confirm broadcast

Template:      {TID} {name}
Target:        {site}
Valid for:     {duration} min (expires {UTC time} UTC)
Update number: {n}
Max hops:      {ttl}
Packet size:   {bytes} bytes
Signing key:   {label} ({fingerprint short})
System time:   {UTC time} UTC

Alerts cannot be recalled. To correct a mistake, send an update
with a higher update number.

[ Cancel ]   [ Sign and send ]
```

For TID 999, add above the buttons: `FREE TEXT bypasses the reviewed codebook: "{text}"` and the tick box.

### 5.0 Sent alerts log (audit)

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 5.1 | Append every send to an **append-only file**, one JSON object per line, with fields: `time_utc`, `alert_id`, `update`, `template`, `target`, `duration_min`, `ttl`, `packet_sha256`, `key_fingerprint`, and `prev_hash` (SHA-256 of the previous line). No personal data | Restarting the server keeps the log. Editing a line breaks the chain | M | 4.5 |
| 5.2 | **Sent Alerts Log** panel in the Live column: newest first, status column (Active, Expired, Replaced by a higher update) | Status updates without reloading the page | M | 5.1 |
| 5.3 | **Export CSV** button | Opens correctly in a spreadsheet | S | 5.2 |
| 5.4 | At server start, verify the hash chain. If broken, set the ERROR reason from Section 4 | Corrupting one line triggers the error | S | 5.1 |

### 6.0 Planner (simulation)

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 6.1 | Run simulations in a **separate process** from the server that handles status and send | While a long simulation runs, `/api/status` still answers in under 500 ms (Test T9) | M | 0.2 |
| 6.2 | Job API (Section 6): start, progress, cancel. Add a progress bar and Cancel button | Cancel stops the process within 5 s | M | 6.1 |
| 6.3 | New controls: **Seeds** (1 to 10), **Runs** (1 to 50), **Scenario** (Baseline, Pessimistic). Keep Civilians, Duration (s), Radio Range (m) with bounds | Out-of-range values show a message and do not start a job | M | 2.3 |
| 6.4 | Show the results line from Section 3 | Numbers match the Monte Carlo output for the same inputs | M | 6.2 |
| 6.5 | Map and charts show **one example run**, captioned "simulated example run", with the seed value used printed beside it | The same seed reproduces the same map | S | 6.2 |
| 6.6 | "Assumptions" expandable box listing the model settings (range, path-loss exponent, body loss, scan duty) and the sentence "Simplified model, not a measurement" | Box opens and closes | S | 6.3 |

### 7.0 Security hardening

The sign endpoint uses your private key. On a `localhost` page this still matters, because **any website open in the operator's browser can try to send requests to `localhost`**.

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 7.1 | Bind the server to `127.0.0.1` only | Another computer on the network cannot open the dashboard | S | none |
| 7.2 | Reject any request whose `Host` header is not `localhost:PORT` or `127.0.0.1:PORT`, and any state-changing request whose `Origin` is not the dashboard's own | Test T11 passes | S | none |
| 7.3 | Generate a random token at server start, embed it in the page, require it in a custom header on every `POST` | A `POST` without the header returns an error | M | 7.2 |
| 7.4 | Never write the private key, passphrase or full packet to logs or API responses | Search logs and responses for the key bytes: none found | S | none |
| 7.5 | **Naming:** the word "HSM" means a hardware security module. If the key is a file on the laptop, the panel must not say HSM (already handled in Section 3). If you later move the key into hardware, rename it again | Dispatcher title reads "Alert Dispatcher" | S | none |
| 7.6 | Key storage, in order of effort. Level 1: key file readable only by the operator account. Level 2: key encrypted with a passphrase entered at server start. Level 3: key in a hardware token | Chosen level is written in the docs | M | none |
| 7.7 | Optional, later: two-approver send. Two PINs, both required within 60 s | Single PIN cannot send | M | 4.6 |

### 8.0 Works offline

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 8.1 | Bundle every script, stylesheet and font **locally**. No file loaded from the internet | Page loads and works with the network cable unplugged (Test T10) | M | 0.2 |
| 8.2 | Map fallback: if basemap tiles fail, show a plain grid with the simulated dots and the notice **"Basemap unavailable offline."** Keep the Esri attribution whenever Esri tiles are shown | Offline test shows dots on a grid, no broken images | M | 8.1 |
| 8.3 | Any API key for tiles lives on the server, not in the page | View-source shows no key | S | 8.1 |

### 9.0 Protocol conformance (blocker for real phones)

Your dashboard says **Compact Wire (\~94 B)**. The Android MVP guide used a **79-byte** packet. If they differ, phones will reject every alert.

| ID | Change | Done when | Size | Needs |
| --- | --- | --- | --- | --- |
| 9.1 | One shared constants file (JSON) holding: packet field order and sizes, `MAX_PACKET_BYTES`, duration, TTL and free-text limits. The server reads it, and the Android app gets a copy | Changing a limit in one file changes it everywhere | M | 0.2 |
| 9.2 | Create **golden test vectors**: a fixed test key, fixed field values, and the exact expected packet hex | File committed | S | 9.1 |
| 9.3 | Server test: building the golden fields produces the golden hex byte for byte | Test passes | S | 9.2 |
| 9.4 | Android test: the app parses a packet **generated by the real dashboard** and shows the expected message | Real phone shows the right text in all three languages (VERIFY on a phone) | M | 9.3 |
| 9.5 | Write the packet layout in a one-page document with a table of bytes | Document matches the golden vector | S | 9.2 |

### 10.0 Acceptance tests

Run all of these before calling the redesign done.

| ID | Test | Pass looks like |
| --- | --- | --- |
| T1 | Read the entire page | Every simulated number carries "(sim)" or "simulated". No number in the Live column uses the word "reached" |
| T2 | Resize to 700 px | Columns stack, LIVE panels first |
| T3 | Stop the server process | Badge turns red within 8 s: "Lost contact with the dashboard server..." |
| T4 | Start a long simulation, then check the badge | Badge unchanged. Simulation never affects it |
| T5 | Send with update number equal to the last one | Rejected: "Update number must be higher than {last}" |
| T6 | Free text of 31 Cyrillic letters | Rejected as over 60 bytes, counter shows negative |
| T7 | Remove the UA translation from one template | Send disabled, "Missing translation: UA" |
| T8 | Preview, change a field, then send using the old `preview_id` | Rejected by the server |
| T9 | Run a 50-run simulation and call `/api/status` repeatedly | Every answer arrives in under 500 ms |
| T10 | Unplug network, reload the page | Everything works, map falls back to the grid with the notice |
| T11 | `curl` a state-changing request with a wrong `Origin`, then with a wrong `Host`, then with no token | All three rejected |
| T12 | Double-click the confirm button | Exactly one log line |
| T13 | Send an alert, restart the server | Log entry still there. Chain check passes |
| T14 | Edit one character in the log file, restart | ERROR badge: "Audit log integrity check failed." |
| T15 | Send an alert, start the beacon, wait for expiry | Beacon stops by itself, state returns to Standby |
| T16 | Send from the dashboard to the real Android app | Phone shows the correct message and rejects a tampered packet |
| T17 | Search logs and API responses for the private key | Not present |

---

## 8. Order of work

```
Phase 1  (about 1 day, low risk):   0.x  >  1.x  >  2.x
Phase 2  (about 1 to 2 days):       3.x  >  4.x  >  5.x
Phase 3  (about 1 day):             7.1 to 7.5  >  9.x
Phase 4  (about 1 day):             6.x  >  8.x
Phase 5  (optional):                7.6, 7.7
Final:                              Run every test in WBS group 10.0 (T1 to T17)
```

**Do Phase 1 first.** It removes the misleading labels using only text and layout changes, so the biggest risk (someone reading simulated coverage as real) is gone before any logic is touched.

**Checkpoint after each phase:** run the tests that apply, commit with a message naming the phase, then continue. If a phase fails, you can go back one commit.

---

## 9. Step 0 inventory table (fill this in)

| Item | Where it lives today (file and line or name) |
| --- | --- |
| Page file (HTML) |  |
| Page script (JS) |  |
| Server file(s) |  |
| Route that signs and sends |  |
| Route that starts the beacon |  |
| Route that runs the simulation |  |
| Function that builds the packet |  |
| File that holds the codebook |  |
| Where the private key is loaded |  |
| Element for the status badge |  |
| Element for each stat card |  |
| What "Sign & Broadcast" does (a, b or c) |  |
| Source of the stat numbers (sim or real) |  |

---

## 10. Out of scope (do not add during this redesign)

- Delivery receipts or any data coming back from phones. The protocol is one-way, so they cannot exist.
- A live coverage map of real phones.
- Drill or test flag inside the packet. This needs a protocol change, so it belongs in a later version.
- Multiple transmitters and user accounts.

---

## 11. Honest limits of this plan

- The first-hop counter is approximate and covers only phones that fetched directly from the laptop.
- The confirmation dialog and preview token make accidental sends less likely. They do not prevent a determined, authorized operator from sending the wrong alert, which is why the audit log exists.
- I wrote this without your source code, so route names and file names are placeholders until Step 0 is done.