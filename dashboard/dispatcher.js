/**
 * WP 4.0 — Alert Dispatcher Console
 *
 * Implements:
 * - Trilingual codebook rendering (PL, EN, UA stacked simultaneously)
 * - Live packet size calculation & TID 999 UTF-8 byte counting
 * - Client-side validation mirroring Section 5
 * - Two-step Preview -> Review and Send with confirmation modal & preview_id
 * - Anti-double-submission & auto-increment of update number
 */

const Dispatcher = (() => {
    let codebook = null;
    let csrfToken = '';
    let currentPreview = null;
    let highestUpdateSent = 0;

    async function init() {
        const form = document.getElementById('dispatch-form');
        const tplSelect = document.getElementById('dispatch-template');
        const shelterSelect = document.getElementById('dispatch-shelter');
        const customTextInput = document.getElementById('dispatch-custom-text');
        const customTextGroup = document.getElementById('custom-text-group');
        const reviewBtn = document.getElementById('dispatch-btn');
        const seqInput = document.getElementById('dispatch-seq');
        const durationInput = document.getElementById('dispatch-duration');
        const ttlInput = document.getElementById('dispatch-ttl');

        // Fetch CSRF Token
        try {
            const csrfRes = await fetch('/api/csrf');
            const csrfData = await csrfRes.json();
            csrfToken = csrfData.csrf_token || '';
        } catch (e) {
            console.warn('Could not fetch CSRF token:', e);
        }

        // Fetch Codebook
        try {
            const res = await fetch('/api/codebook');
            codebook = await res.json();
        } catch (e) {
            console.warn('Could not fetch codebook:', e);
        }

        // Fetch existing highest update from audit log
        await refreshHighestUpdate();

        // Template change listener
        if (tplSelect) {
            tplSelect.addEventListener('change', () => {
                const isEscape = tplSelect.value === '999';
                if (customTextGroup) {
                    customTextGroup.classList.toggle('hidden', !isEscape);
                }
                const shelterGroup = document.getElementById('shelter-group');
                if (shelterGroup) {
                    shelterGroup.classList.toggle('hidden', isEscape);
                }
                updatePreview();
            });
        }

        if (shelterSelect) shelterSelect.addEventListener('change', updatePreview);
        if (durationInput) durationInput.addEventListener('input', updatePreview);
        if (seqInput) seqInput.addEventListener('input', updatePreview);
        if (ttlInput) ttlInput.addEventListener('input', updatePreview);

        if (customTextInput) {
            customTextInput.addEventListener('input', () => {
                updateByteCounter();
                updatePreview();
            });
        }

        // Review and Send button
        if (reviewBtn) {
            reviewBtn.addEventListener('click', handleReviewAndSend);
        }

        // Modal event handlers
        initModalHandlers();

        updateByteCounter();
        updatePreview();
    }

    function getUtf8ByteLength(str) {
        return new TextEncoder().encode(str).length;
    }

    function updateByteCounter() {
        const input = document.getElementById('dispatch-custom-text');
        const counter = document.getElementById('free-text-byte-counter');
        if (!input || !counter) return;

        const bytes = getUtf8ByteLength(input.value || '');
        const remaining = 60 - bytes;
        counter.textContent = `${remaining} bytes left`;
        if (remaining < 0) {
            counter.classList.add('negative');
        } else {
            counter.classList.remove('negative');
        }
    }

    function estimateWireBytes(tplId, customText, shelterId, duration, seq, ttl) {
        // Canonical payload length + 64 bytes Ed25519 sig + 32 bytes pubkey + 1 byte TTL
        const payloadObj = {
            cb_v: 1,
            custom_text: tplId === 999 ? customText : "",
            dur: duration,
            id: "A7F29B01",
            loc_ref: tplId === 999 ? "" : shelterId,
            loc_type: tplId === 999 ? 0 : 1,
            seq: seq,
            tid: tplId,
            ts: Math.floor(Date.now() / 1000),
            v: 2
        };
        const canonicalStr = JSON.stringify(payloadObj);
        const canonicalBytes = getUtf8ByteLength(canonicalStr);
        return canonicalBytes + 64 + 32 + 1;
    }

    function updatePreview() {
        if (!codebook) return;

        const tplId = parseInt(document.getElementById('dispatch-template')?.value || 101);
        const isEscape = tplId === 999;
        const shelterId = document.getElementById('dispatch-shelter')?.value || 'KRK_TAURON_G3';
        const customText = document.getElementById('dispatch-custom-text')?.value || '';
        const seq = parseInt(document.getElementById('dispatch-seq')?.value || 1);
        const duration = parseInt(document.getElementById('dispatch-duration')?.value || 120);
        const ttl = parseInt(document.getElementById('dispatch-ttl')?.value || 15);

        const tpl = codebook.templates[String(tplId)];
        const shelter = codebook.shelters[shelterId];

        // Wire size label
        const wireBadge = document.getElementById('badge-wire-budget');
        const wireBytes = estimateWireBytes(tplId, customText, shelterId, duration, seq, ttl);
        if (wireBadge) {
            wireBadge.textContent = `Packet size: ${wireBytes} bytes (limit 240)`;
            if (wireBytes > 240) {
                wireBadge.style.color = '#ef4444';
            } else {
                wireBadge.style.color = '#10b981';
            }
        }

        if (!tpl) return;

        // Render Trilingual Stacked Blocks (WP 4.4)
        const renderLang = (lang) => {
            const locName = shelter ? (shelter[`name_${lang}`] || shelter.name_en) : shelterId;
            const rawTemplate = tpl[lang] || tpl.en || '';
            return rawTemplate
                .replace('{location}', locName)
                .replace('{custom_text}', customText || '...');
        };

        const plEl = document.getElementById('preview-pl-text');
        const enEl = document.getElementById('preview-en-text');
        const uaEl = document.getElementById('preview-ua-text');

        if (plEl) plEl.innerHTML = `<strong>[${tpl.category}]</strong> ${renderLang('pl')}`;
        if (enEl) enEl.innerHTML = `<strong>[${tpl.category}]</strong> ${renderLang('en')}`;
        if (uaEl) uaEl.innerHTML = `<strong>[${tpl.category}]</strong> ${renderLang('ua')}`;
    }

    async function refreshHighestUpdate() {
        try {
            const res = await fetch('/api/alerts');
            const data = await res.json();
            if (data.alerts && data.alerts.length > 0) {
                const maxUp = Math.max(...data.alerts.map(a => a.update || 0));
                highestUpdateSent = maxUp;
                const seqInput = document.getElementById('dispatch-seq');
                if (seqInput && parseInt(seqInput.value) <= highestUpdateSent) {
                    seqInput.value = highestUpdateSent + 1;
                }
            }
        } catch (e) {
            // Keep default
        }
    }

    function showBannerError(msg) {
        const banner = document.getElementById('dispatch-error-banner');
        if (banner) {
            banner.textContent = msg;
            banner.classList.remove('hidden');
        }
    }

    function clearBannerError() {
        const banner = document.getElementById('dispatch-error-banner');
        if (banner) {
            banner.textContent = '';
            banner.classList.add('hidden');
        }
    }

    async function handleReviewAndSend() {
        clearBannerError();

        const tplId = parseInt(document.getElementById('dispatch-template').value);
        const shelterId = document.getElementById('dispatch-shelter').value;
        const customText = document.getElementById('dispatch-custom-text')?.value || '';
        const seq = parseInt(document.getElementById('dispatch-seq')?.value || 1);
        const duration = parseInt(document.getElementById('dispatch-duration')?.value || 120);
        const ttl = parseInt(document.getElementById('dispatch-ttl')?.value || 15);

        // Client-side validations (Section 5)
        if (!tplId) {
            showBannerError("Choose a template.");
            return;
        }

        if (tplId !== 999 && !shelterId) {
            showBannerError("Choose a target site for this template.");
            return;
        }

        if (seq <= highestUpdateSent) {
            showBannerError(`Update number must be higher than ${highestUpdateSent}.`);
            return;
        }

        if (duration < 5 || duration > 1440) {
            showBannerError("Duration must be 5 to 1440 minutes.");
            return;
        }

        if (ttl < 1 || ttl > 15) {
            showBannerError("Max hops must be 1 to 15.");
            return;
        }

        if (tplId === 999) {
            const byteLen = getUtf8ByteLength(customText);
            if (!customText.trim()) {
                showBannerError("Free text is required for template 999.");
                return;
            }
            if (byteLen > 60) {
                showBannerError(`Free text is ${byteLen} bytes. Maximum is 60.`);
                return;
            }
        }

        const reviewBtn = document.getElementById('dispatch-btn');
        reviewBtn.disabled = true;
        reviewBtn.textContent = 'Generating preview…';

        try {
            const res = await fetch('/api/alerts/preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken,
                },
                body: JSON.stringify({
                    template: tplId,
                    target: tplId === 999 ? '' : shelterId,
                    update: seq,
                    duration_min: duration,
                    ttl: ttl,
                    free_text: customText,
                }),
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || data.error || 'Preview validation failed');
            }

            currentPreview = data;
            openConfirmModal(data);
        } catch (err) {
            showBannerError(err.message);
        } finally {
            reviewBtn.disabled = false;
            reviewBtn.textContent = 'Review and send';
        }
    }

    function openConfirmModal(previewData) {
        const modal = document.getElementById('confirm-modal');
        if (!modal) return;

        const f = previewData.fields;
        const tplName = (codebook && codebook.templates[String(f.template)])
            ? codebook.templates[String(f.template)].category
            : 'Alert';

        const shelterName = (codebook && codebook.shelters[f.target])
            ? codebook.shelters[f.target].name_en
            : (f.target || 'None');

        document.getElementById('cm-template').textContent = `TID ${f.template} (${tplName})`;
        document.getElementById('cm-target').textContent = shelterName;
        document.getElementById('cm-duration').textContent = f.duration_min;
        document.getElementById('cm-expires').textContent = previewData.expires_at_utc;
        document.getElementById('cm-update').textContent = f.update;
        document.getElementById('cm-ttl').textContent = f.ttl;
        document.getElementById('cm-packet-bytes').textContent = previewData.packet_bytes;

        const keyLabel = document.getElementById('key-label-badge')?.textContent || '[TEST KEY]';
        const keyFp = document.getElementById('pubkey-display')?.textContent || 'key';
        document.getElementById('cm-key').textContent = `${keyLabel} (${keyFp})`;

        const utcStr = document.getElementById('utc-time-display')?.textContent.replace('System time: ', '') || '';
        document.getElementById('cm-system-time').textContent = utcStr;

        // Escape hatch warning (WP 4.7)
        const escWarning = document.getElementById('cm-escape-hatch-warning');
        const confirmBtn = document.getElementById('cm-confirm-btn');
        const checkEscape = document.getElementById('cm-check-free-text');

        if (f.template === 999) {
            escWarning.classList.remove('hidden');
            document.getElementById('cm-free-text-quote').textContent = f.free_text;
            checkEscape.checked = false;
            confirmBtn.disabled = true;

            checkEscape.onchange = () => {
                confirmBtn.disabled = !checkEscape.checked;
            };
        } else {
            escWarning.classList.add('hidden');
            confirmBtn.disabled = false;
        }

        modal.classList.remove('hidden');
    }

    function initModalHandlers() {
        const modal = document.getElementById('confirm-modal');
        const cancelBtn = document.getElementById('cm-cancel-btn');
        const confirmBtn = document.getElementById('cm-confirm-btn');

        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                modal.classList.add('hidden');
                currentPreview = null;
            });
        }

        if (confirmBtn) {
            confirmBtn.addEventListener('click', async () => {
                if (!currentPreview) return;

                // Disable confirm button immediately (WP 4.8 anti-double submit)
                confirmBtn.disabled = true;
                confirmBtn.textContent = 'Signing & Sending…';

                try {
                    const f = currentPreview.fields;
                    const res = await fetch('/api/alerts/send', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-Token': csrfToken,
                        },
                        body: JSON.stringify({
                            template: f.template,
                            target: f.target,
                            update: f.update,
                            duration_min: f.duration_min,
                            ttl: f.ttl,
                            free_text: f.free_text,
                            preview_id: currentPreview.preview_id,
                        }),
                    });

                    const data = await res.json();
                    if (!res.ok) {
                        throw new Error(data.detail || data.error || 'Send failed');
                    }

                    // Success! Close modal
                    modal.classList.add('hidden');
                    currentPreview = null;

                    // Update highest sent and increment field (WP 4.2)
                    highestUpdateSent = f.update;
                    const seqInput = document.getElementById('dispatch-seq');
                    if (seqInput) {
                        seqInput.value = highestUpdateSent + 1;
                    }

                    // Refresh alerts log and status
                    if (window.App && window.App.refreshSystemState) {
                        window.App.refreshSystemState();
                    }
                } catch (err) {
                    alert(`Send error: ${err.message}`);
                } finally {
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = 'Sign and send';
                }
            });
        }
    }

    function setCsrfToken(token) {
        csrfToken = token;
    }

    return {
        init,
        setCsrfToken,
        refreshHighestUpdate,
    };
})();
