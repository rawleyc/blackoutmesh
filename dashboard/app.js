/**
 * BlackoutMesh Tactical Dashboard — Main Application Controller
 *
 * Implements:
 * - 2-second status polling with 3-consecutive-check lost contact detector (WP 3.1, Test T3)
 * - Section 4 Status badge states (STANDBY, TRANSMITTING, ERROR with click-to-view reasons)
 * - Key fingerprint shortened with copy button & system UTC clock (WP 2.4)
 * - Live transmitter status, countdown, and BLE start/stop toggling (WP 3.0)
 * - Sent alerts log table rendering with status badges (WP 5.2)
 * - Background simulation job lifecycle (start, progress, cancel, Monte Carlo results) (WP 6.0)
 */

window.App = (() => {
    let failedChecksCount = 0;
    let systemStatus = null;
    let activeJobId = null;
    let jobPollInterval = null;
    let currentCountdownSeconds = 0;
    let countdownTimer = null;

    document.addEventListener('DOMContentLoaded', async () => {
        // Initialize modules
        MeshMap.init('map-container');
        Telemetry.init();
        Dispatcher.init();

        // Setup copy key button
        const copyKeyBtn = document.getElementById('copy-key-btn');
        if (copyKeyBtn) {
            copyKeyBtn.addEventListener('click', () => {
                if (systemStatus && systemStatus.key && systemStatus.key.fingerprint) {
                    navigator.clipboard.writeText(systemStatus.key.fingerprint).then(() => {
                        copyKeyBtn.textContent = '✓';
                        setTimeout(() => { copyKeyBtn.textContent = '📋'; }, 1500);
                    });
                }
            });
        }

        // Setup status pill click for error reasons
        const statusPill = document.getElementById('status-pill');
        if (statusPill) {
            statusPill.addEventListener('click', () => {
                if (systemStatus && systemStatus.reasons && systemStatus.reasons.length > 0) {
                    openErrorReasonsModal(systemStatus.reasons);
                } else if (failedChecksCount >= 3) {
                    openErrorReasonsModal(["Lost contact with the dashboard server. Do not rely on the status shown."]);
                }
            });
        }

        // Setup error modal close button
        const closeErrorBtn = document.getElementById('error-modal-close-btn');
        if (closeErrorBtn) {
            closeErrorBtn.addEventListener('click', () => {
                document.getElementById('error-reasons-modal')?.classList.add('hidden');
            });
        }

        // Setup Transmitter button
        initTransmitterControls();

        // Setup Simulation Job Controls
        initSimControls();

        // Initial fetch of system status & alerts log
        await pollStatus();
        await refreshAlertsLog();

        // Poll status every 2 seconds (WP 3.1)
        setInterval(pollStatus, 2000);

        // Start local UTC clock update every second
        setInterval(updateUtcClock, 1000);
    });

    function formatTimeRemaining(seconds) {
        if (seconds <= 0) return '00:00:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function updateUtcClock() {
        const clockEl = document.getElementById('utc-time-display');
        if (clockEl) {
            const now = new Date();
            const h = String(now.getUTCHours()).padStart(2, '0');
            const m = String(now.getUTCMinutes()).padStart(2, '0');
            const s = String(now.getUTCSeconds()).padStart(2, '0');
            clockEl.textContent = `System time: ${h}:${m}:${s} UTC`;
        }

        // Update transmitter countdown locally between polls
        if (currentCountdownSeconds > 0) {
            currentCountdownSeconds--;
            const countEl = document.getElementById('tx-countdown');
            if (countEl) countEl.textContent = formatTimeRemaining(currentCountdownSeconds);
        }
    }

    async function pollStatus() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) throw new Error('Status poll response not ok');
            const data = await res.json();

            failedChecksCount = 0;
            systemStatus = data;

            if (data.csrf_token && Dispatcher.setCsrfToken) {
                Dispatcher.setCsrfToken(data.csrf_token);
            }

            renderSystemStatus(data);
        } catch (e) {
            failedChecksCount++;
            console.warn(`Status check failed (${failedChecksCount}/3):`, e);

            // Section 4: If 3 checks fail in a row (~6s), turn badge red with lost contact message
            if (failedChecksCount >= 3) {
                renderLostContactState();
            }
        }
    }

    function renderLostContactState() {
        const pill = document.getElementById('status-pill');
        const label = document.getElementById('status-label');
        const subtext = document.getElementById('status-subtext');

        if (pill) {
            pill.className = 'status-pill status-error';
            if (label) label.textContent = 'ERROR';
            if (subtext) subtext.textContent = 'Lost contact';
        }
    }

    function renderSystemStatus(data) {
        const pill = document.getElementById('status-pill');
        const label = document.getElementById('status-label');
        const subtext = document.getElementById('status-subtext');
        const keyDisplay = document.getElementById('pubkey-display');
        const keyLabelBadge = document.getElementById('key-label-badge');
        const dispKeyLabel = document.getElementById('disp-key-label');
        const dispKeyFp = document.getElementById('disp-key-fp');

        // Render Key info
        if (data.key) {
            if (keyDisplay) {
                keyDisplay.textContent = data.key.fingerprint_short || 'unknown';
                keyDisplay.title = data.key.fingerprint || '';
            }
            if (keyLabelBadge) keyLabelBadge.textContent = `[${data.key.label}]`;
            if (dispKeyLabel) dispKeyLabel.textContent = `[${data.key.label}]`;
            if (dispKeyFp) dispKeyFp.textContent = data.key.fingerprint_short;
        }

        // Render Header Status Badge (Section 4)
        if (pill && label && subtext) {
            pill.className = 'status-pill';
            if (data.state === 'ERROR') {
                pill.classList.add('status-error');
                label.textContent = 'ERROR';
                subtext.textContent = `${data.reasons.length} issue(s) — click to view`;
            } else if (data.state === 'TRANSMITTING') {
                pill.classList.add('status-transmitting');
                label.textContent = 'TRANSMITTING';
                subtext.textContent = formatTimeRemaining(data.transmitter.time_remaining_s);
            } else {
                pill.classList.add('status-standby');
                label.textContent = 'STANDBY';
                subtext.textContent = 'Ready';
            }
        }

        // Render Transmitter Panel (WP 3.0)
        renderTransmitterPanel(data.transmitter);
    }

    function renderTransmitterPanel(tx) {
        if (!tx) return;

        const badge = document.getElementById('tx-state-badge');
        const alertIdEl = document.getElementById('tx-alert-id');
        const countEl = document.getElementById('tx-countdown');
        const servedEl = document.getElementById('tx-served-count');
        const toggleBtn = document.getElementById('ble-toggle-btn');

        if (badge) {
            badge.textContent = tx.state;
            badge.className = 'tx-badge';
            if (tx.state === 'TRANSMITTING') badge.classList.add('transmitting');
        }

        if (alertIdEl) {
            alertIdEl.textContent = tx.alert_id || 'None loaded';
        }

        currentCountdownSeconds = tx.time_remaining_s || 0;
        if (countEl) {
            countEl.textContent = formatTimeRemaining(currentCountdownSeconds);
        }

        if (servedEl) {
            servedEl.textContent = `${tx.first_hop_transfers} first-hop transfers (approximate)`;
        }

        if (toggleBtn) {
            const hasAlert = tx.has_loaded_alert;
            const isBroadcasting = tx.is_broadcasting;
            const isExpired = tx.time_remaining_s <= 0;

            if (!hasAlert || isExpired) {
                toggleBtn.disabled = true;
                toggleBtn.textContent = 'Start BLE Beacon';
            } else if (isBroadcasting) {
                toggleBtn.disabled = false;
                toggleBtn.textContent = 'Stop BLE Beacon';
                toggleBtn.style.color = '#f87171';
            } else {
                toggleBtn.disabled = false;
                toggleBtn.textContent = 'Start BLE Beacon';
                toggleBtn.style.color = '';
            }
        }
    }

    function initTransmitterControls() {
        const toggleBtn = document.getElementById('ble-toggle-btn');
        if (!toggleBtn) return;

        toggleBtn.addEventListener('click', async () => {
            toggleBtn.disabled = true;
            const isStopping = toggleBtn.textContent.includes('Stop');
            const endpoint = isStopping ? '/api/transmitter/stop' : '/api/transmitter/start';

            try {
                const res = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': systemStatus ? systemStatus.csrf_token : '',
                    },
                });
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || data.error || 'Action failed');
                }
                await pollStatus();
            } catch (err) {
                alert(`Transmitter error: ${err.message}`);
            } finally {
                toggleBtn.disabled = false;
            }
        });
    }

    async function refreshAlertsLog() {
        try {
            const res = await fetch('/api/alerts');
            const data = await res.json();
            const tbody = document.getElementById('audit-log-tbody');
            if (!tbody) return;

            if (!data.alerts || data.alerts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="empty-log-cell">No alerts dispatched in this session.</td></tr>';
                return;
            }

            tbody.innerHTML = '';
            for (const a of data.alerts) {
                const tr = document.createElement('tr');
                const timeShort = a.time_utc ? a.time_utc.substring(11, 19) : '--:--:--';
                const statusClass = a.status === 'Active'
                    ? 'active'
                    : (a.status === 'Expired' ? 'expired' : 'replaced');

                tr.innerHTML = `
                    <td>${timeShort}</td>
                    <td>#${a.update}</td>
                    <td>TID ${a.template}</td>
                    <td>${a.target || 'None'}</td>
                    <td>${a.duration_min}m</td>
                    <td><span class="status-badge ${statusClass}">${a.status}</span></td>
                `;
                tbody.appendChild(tr);
            }
        } catch (e) {
            console.warn('Failed to refresh alerts log:', e);
        }
    }

    function openErrorReasonsModal(reasons) {
        const modal = document.getElementById('error-reasons-modal');
        const list = document.getElementById('error-reasons-list');
        if (!modal || !list) return;

        list.innerHTML = '';
        for (const r of reasons) {
            const li = document.createElement('li');
            li.textContent = r;
            list.appendChild(li);
        }
        modal.classList.remove('hidden');
    }

    // ── Simulation Job Management (WP 6.0) ────────────────────

    function initSimControls() {
        const runBtn = document.getElementById('run-sim-btn');
        const cancelBtn = document.getElementById('cancel-sim-btn');

        if (runBtn) {
            runBtn.addEventListener('click', async () => {
                const civilians = parseInt(document.getElementById('sim-civilians').value);
                const seeds = parseInt(document.getElementById('sim-seeds').value);
                const runs = parseInt(document.getElementById('sim-runs').value);
                const scenario = document.getElementById('sim-scenario').value;
                const duration = parseInt(document.getElementById('sim-duration').value);
                const range = parseFloat(document.getElementById('sim-range').value);

                runBtn.disabled = true;
                runBtn.textContent = '⏳ Starting…';
                if (cancelBtn) cancelBtn.classList.remove('hidden');

                const progressContainer = document.getElementById('sim-progress-container');
                const progressFill = document.getElementById('sim-progress-fill');
                const progressLabel = document.getElementById('sim-progress-label');
                if (progressContainer) progressContainer.classList.remove('hidden');
                if (progressFill) progressFill.style.width = '0%';
                if (progressLabel) progressLabel.textContent = `Preparing ${runs} run(s)…`;

                try {
                    const res = await fetch('/api/sim/jobs', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': systemStatus ? systemStatus.csrf_token : '',
                        },
                        body: JSON.stringify({
                            civilians: civilians,
                            seeds: seeds,
                            runs: runs,
                            scenario: scenario,
                            duration_s: duration,
                            range_m: range,
                        }),
                    });

                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || data.error || 'Job creation failed');

                    activeJobId = data.job_id;
                    startJobPolling(activeJobId);
                } catch (err) {
                    alert(`Simulation error: ${err.message}`);
                    cleanupSimState();
                }
            });
        }

        if (cancelBtn) {
            cancelBtn.addEventListener('click', async () => {
                if (!activeJobId) return;
                try {
                    await fetch(`/api/sim/jobs/${activeJobId}/cancel`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': systemStatus ? systemStatus.csrf_token : '',
                        },
                    });
                } catch (e) {
                    console.warn('Cancel failed:', e);
                } finally {
                    cleanupSimState();
                    const resultsLine = document.getElementById('sim-results-line');
                    if (resultsLine) resultsLine.textContent = 'Simulation cancelled by operator.';
                }
            });
        }
    }

    function startJobPolling(jobId) {
        if (jobPollInterval) clearInterval(jobPollInterval);

        jobPollInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/sim/jobs/${jobId}`);
                if (!res.ok) throw new Error('Poll failed');
                const job = await res.json();

                const progressFill = document.getElementById('sim-progress-fill');
                const progressLabel = document.getElementById('sim-progress-label');

                if (progressFill) {
                    const pct = Math.round((job.progress || 0) * 100);
                    progressFill.style.width = `${pct}%`;
                }

                if (progressLabel) {
                    progressLabel.textContent = `Run ${job.current_run || 0} of ${job.total_runs || 1} (${Math.round((job.progress || 0) * 100)}%)`;
                }

                if (job.state === 'completed') {
                    clearInterval(jobPollInterval);
                    cleanupSimState();
                    handleSimCompleted(job.results);
                } else if (job.state === 'failed' || job.state === 'cancelled') {
                    clearInterval(jobPollInterval);
                    cleanupSimState();
                    alert(`Simulation ${job.state}: ${job.error || 'stopped'}`);
                }
            } catch (e) {
                console.warn('Error polling sim job:', e);
            }
        }, 800);
    }

    function cleanupSimState() {
        if (jobPollInterval) {
            clearInterval(jobPollInterval);
            jobPollInterval = null;
        }
        activeJobId = null;

        const runBtn = document.getElementById('run-sim-btn');
        const cancelBtn = document.getElementById('cancel-sim-btn');
        const progressContainer = document.getElementById('sim-progress-container');

        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = '▶ Run simulation';
        }
        if (cancelBtn) cancelBtn.classList.add('hidden');
        if (progressContainer) progressContainer.classList.add('hidden');
    }

    function handleSimCompleted(results) {
        if (!results) return;

        // Results line (WP 6.4)
        const resultsLine = document.getElementById('sim-results-line');
        if (resultsLine && results.summary) {
            resultsLine.textContent = results.summary;
        }

        // Example run for Map and Charts (WP 6.5)
        const example = results.example_run;
        if (example) {
            Telemetry.update(example);
            Telemetry.updateMetricCards(example);
            MeshMap.updateNodes(example.positions, example.node_types, example.seed_used);
        }
    }

    async function refreshSystemState() {
        await pollStatus();
        await refreshAlertsLog();
        await Dispatcher.refreshHighestUpdate();
    }

    return {
        refreshSystemState,
    };
})();
