/**
 * WP 3.4 — Authority HSM Alert Dispatcher Console
 *
 * Web-based trigger panel for incident commanders.
 * Signs alerts via the backend API and displays the signed packet.
 */

const Dispatcher = (() => {

    function init() {
        const form = document.getElementById('dispatch-form');
        const ttlSlider = document.getElementById('dispatch-ttl');
        const ttlOutput = document.getElementById('dispatch-ttl-value');

        if (ttlSlider && ttlOutput) {
            ttlSlider.addEventListener('input', () => {
                ttlOutput.textContent = ttlSlider.value;
            });
        }

        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                await dispatchAlert();
            });
        }
    }

    async function dispatchAlert() {
        const btn = document.getElementById('dispatch-btn');
        const resultEl = document.getElementById('dispatch-result');

        btn.disabled = true;
        btn.innerHTML = '<span>Signing...</span>';

        try {
            const response = await fetch('/api/dispatch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    alert_type: document.getElementById('dispatch-type').value,
                    body: document.getElementById('dispatch-body').value ||
                          'Emergency alert from dispatch console.',
                    ttl: parseInt(document.getElementById('dispatch-ttl').value),
                    validity_seconds: parseInt(
                        document.getElementById('dispatch-validity').value
                    ),
                }),
            });

            const packet = await response.json();
            resultEl.textContent = JSON.stringify(packet, null, 2);
            resultEl.classList.remove('hidden');

            // Flash success
            btn.innerHTML = '<span>✓ Signed & Ready</span>';
            btn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
            setTimeout(() => {
                btn.innerHTML = '<span>Sign & Broadcast</span>';
                btn.style.background = '';
                btn.disabled = false;
            }, 2000);
        } catch (err) {
            resultEl.textContent = `ERROR: ${err.message}`;
            resultEl.classList.remove('hidden');
            btn.innerHTML = '<span>Sign & Broadcast</span>';
            btn.disabled = false;
        }
    }

    return { init };
})();
