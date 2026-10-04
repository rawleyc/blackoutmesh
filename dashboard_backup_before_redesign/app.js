/**
 * BlackoutMesh Dashboard — Main Application Controller
 *
 * Orchestrates map, telemetry, and dispatcher modules.
 */

document.addEventListener('DOMContentLoaded', async () => {
    // Initialize modules
    MeshMap.init('map-container');
    Telemetry.init();
    Dispatcher.init();

    // Load authority key & runtime config
    try {
        const authRes = await fetch('/api/authority');
        const authData = await authRes.json();
        const display = document.getElementById('pubkey-display');
        if (display && authData.pubkey_hex) {
            display.textContent = authData.pubkey_hex.substring(0, 16) + '…';
            display.title = authData.pubkey_hex;
        }
        if (authData.carto_api_key) {
            MeshMap.setApiKey(authData.carto_api_key);
        }
    } catch (e) {
        console.warn('Could not fetch authority key or config:', e);
    }

    // Simulation button
    const runBtn = document.getElementById('run-sim-btn');
    const statusPill = document.getElementById('status-pill');

    runBtn.addEventListener('click', async () => {
        runBtn.disabled = true;
        runBtn.textContent = '⏳ Running…';
        statusPill.classList.add('active');
        statusPill.querySelector('.label').textContent = 'SIMULATING';

        try {
            const response = await fetch('/api/simulate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    num_civilians: parseInt(document.getElementById('sim-civilians').value),
                    duration_s: parseInt(document.getElementById('sim-duration').value),
                    radio_range_m: parseFloat(document.getElementById('sim-range').value),
                    seed: Math.floor(Math.random() * 10000),
                }),
            });

            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            }

            // Update all panels
            Telemetry.update(data);
            Telemetry.updateMetricCards(data);
            MeshMap.updateNodes(data.positions, data.node_types);

            statusPill.querySelector('.label').textContent = 'COMPLETE';
        } catch (err) {
            console.error('Simulation error:', err);
            statusPill.querySelector('.label').textContent = 'ERROR';
            statusPill.classList.remove('active');
            alert(`Simulation failed: ${err.message}`);
        } finally {
            runBtn.disabled = false;
            runBtn.textContent = '▶ Run Simulation';
        }
    });
});
