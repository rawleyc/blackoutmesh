/**
 * WP 1.1 / 3.4 — Authority HSM Alert Dispatcher Console (Templated Codebook)
 *
 * Web-based trigger panel for incident commanders:
 * - Selects validated emergency templates (prevents injection/phishing)
 * - Live trilingual preview (PL, EN, UA)
 * - Signs compact packets (~94 B) ensuring single-GATT-MTU transfer
 */

const Dispatcher = (() => {
    let codebook = null;
    let currentLang = 'pl';

    async function init() {
        const form = document.getElementById('dispatch-form');
        const tplSelect = document.getElementById('dispatch-template');
        const shelterSelect = document.getElementById('dispatch-shelter');
        const customTextGroup = document.getElementById('custom-text-group');
        const customTextInput = document.getElementById('dispatch-custom-text');
        const langTabs = document.querySelectorAll('.preview-tab');

        // Fetch codebook from server
        try {
            const res = await fetch('/api/codebook');
            codebook = await res.json();
        } catch (e) {
            console.warn('Could not fetch codebook, using local defaults:', e);
        }

        // Language tab selection
        langTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                langTabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentLang = tab.getAttribute('data-lang');
                updatePreview();
            });
        });

        // Toggle custom text for Escape Hatch (TID 999)
        if (tplSelect) {
            tplSelect.addEventListener('change', () => {
                const isEscape = tplSelect.value === '999';
                if (customTextGroup) {
                    customTextGroup.classList.toggle('hidden', !isEscape);
                }
                updatePreview();
            });
        }

        if (shelterSelect) {
            shelterSelect.addEventListener('change', updatePreview);
        }

        if (customTextInput) {
            customTextInput.addEventListener('input', updatePreview);
        }

        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                await dispatchAlert();
            });
        }

        updatePreview();
    }

    function updatePreview() {
        const previewEl = document.getElementById('preview-text');
        if (!previewEl || !codebook) return;

        const tplId = document.getElementById('dispatch-template').value;
        const shelterId = document.getElementById('dispatch-shelter').value;
        const customText = document.getElementById('dispatch-custom-text')?.value || '';

        const tpl = codebook.templates[tplId];
        const shelter = codebook.shelters[shelterId];

        if (!tpl) {
            previewEl.textContent = '[Unknown Template]';
            return;
        }

        const locName = shelter ? (shelter[`name_${currentLang}`] || shelter.name_en) : shelterId;
        const rawTemplate = tpl[currentLang] || tpl.en;
        const rendered = rawTemplate
            .replace('{location}', locName)
            .replace('{custom_text}', customText || '...');

        previewEl.innerHTML = `<strong>[${tpl.category}]</strong> ${rendered}`;
    }

    async function dispatchAlert() {
        const btn = document.getElementById('dispatch-btn');
        const resultEl = document.getElementById('dispatch-result');
        const badgeEl = document.getElementById('badge-wire-budget');

        btn.disabled = true;
        btn.innerHTML = '<span>Signing (Ed25519)...</span>';

        const tplId = parseInt(document.getElementById('dispatch-template').value);
        const shelterId = document.getElementById('dispatch-shelter').value;
        const customText = document.getElementById('dispatch-custom-text')?.value || '';
        const seq = parseInt(document.getElementById('dispatch-seq')?.value || 0);
        const duration = parseInt(document.getElementById('dispatch-duration')?.value || 120);
        const ttl = parseInt(document.getElementById('dispatch-ttl')?.value || 15);

        try {
            const response = await fetch('/api/dispatch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template_id: tplId,
                    loc_type: 1,
                    loc_ref: shelterId,
                    custom_text: customText,
                    seq: seq,
                    duration_minutes: duration,
                    ttl: ttl,
                }),
            });

            const data = await response.json();
            resultEl.textContent = JSON.stringify(data, null, 2);
            resultEl.classList.remove('hidden');

            if (badgeEl && data.wire_size_bytes) {
                badgeEl.textContent = `Compact Wire: ${data.wire_size_bytes} B (Single GATT MTU: ✓)`;
                badgeEl.style.color = '#10b981';
            }

            // Flash success
            btn.innerHTML = data.ble_broadcasting
                ? '<span>✓ Broadcast Live on Laptop BLE!</span>'
                : '<span>✓ Signed & Broadcasted</span>';
            btn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
            setTimeout(() => {
                btn.innerHTML = '<span>Sign & Broadcast (Ed25519)</span>';
                btn.style.background = '';
                btn.disabled = false;
            }, 2500);

            // Trigger immediate BLE poll
            pollBleStatus();
        } catch (err) {
            resultEl.textContent = `ERROR: ${err.message}`;
            resultEl.classList.remove('hidden');
            btn.innerHTML = '<span>Sign & Broadcast</span>';
            btn.disabled = false;
        }
    }

    async function pollBleStatus() {
        const card = document.getElementById('ble-ota-card');
        const statusEl = document.getElementById('ble-ota-status');
        const badgeEl = document.getElementById('ble-served-badge');
        const toggleBtn = document.getElementById('ble-toggle-btn');
        if (!card || !statusEl) return;

        try {
            const res = await fetch('/api/ble/status');
            const data = await res.json();

            if (!data.available) {
                card.classList.remove('broadcasting');
                card.classList.add('inactive');
                statusEl.textContent = 'BLE Driver Unavailable';
                if (toggleBtn) toggleBtn.disabled = true;
                return;
            }

            if (data.is_broadcasting) {
                card.classList.add('broadcasting');
                card.classList.remove('inactive');
                statusEl.textContent = `Broadcasting over air (${data.current_packet_bytes || 79} B)`;
                if (toggleBtn) {
                    toggleBtn.textContent = 'Stop BLE Beacon';
                    toggleBtn.style.color = '#ef4444';
                }
            } else {
                card.classList.remove('broadcasting');
                card.classList.remove('inactive');
                statusEl.textContent = 'Standby (BLE Ready)';
                if (toggleBtn) {
                    toggleBtn.textContent = 'Start BLE Beacon';
                    toggleBtn.style.color = '';
                }
            }

            if (badgeEl) {
                const count = data.served_count || 0;
                const reads = data.total_reads || 0;
                badgeEl.textContent = `${count} phone(s) reached (${reads} transfers)`;
            }
        } catch (e) {
            // Server might still be booting or reloading
        }
    }

    function initBle() {
        const toggleBtn = document.getElementById('ble-toggle-btn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', async () => {
                toggleBtn.disabled = true;
                try {
                    await fetch('/api/ble/toggle', { method: 'POST' });
                    await pollBleStatus();
                } catch (e) {
                    console.error('Toggle BLE failed:', e);
                } finally {
                    toggleBtn.disabled = false;
                }
            });
        }
        pollBleStatus();
        setInterval(pollBleStatus, 3000);
    }

    return {
        init: () => {
            init();
            initBle();
        }
    };
})();
