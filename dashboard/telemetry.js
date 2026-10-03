/**
 * WP 3.2 — Dual-Mode Live Telemetry Dashboard (Chart.js)
 *
 * Charts:
 *   1. Coverage Over Time — civilians reached vs target
 *   2. Forwarding & Redundancy — broadcasts, duplicates, suppressed
 */

const Telemetry = (() => {
    let coverageChart = null;
    let forwardingChart = null;

    const chartDefaults = {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
            legend: {
                labels: {
                    color: '#94a3b8',
                    font: { family: "'Inter', sans-serif", size: 11 },
                    boxWidth: 12,
                },
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(30, 41, 59, 0.5)' },
                ticks: { color: '#64748b', font: { size: 10 } },
                title: { display: true, text: 'Time (s)', color: '#64748b' },
            },
            y: {
                grid: { color: 'rgba(30, 41, 59, 0.5)' },
                ticks: { color: '#64748b', font: { size: 10 } },
                beginAtZero: true,
            },
        },
    };

    function init() {
        const ctxCov = document.getElementById('chart-coverage').getContext('2d');
        coverageChart = new Chart(ctxCov, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Civilians Reached',
                    data: [],
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }],
            },
            options: { ...chartDefaults },
        });

        const ctxFwd = document.getElementById('chart-forwarding').getContext('2d');
        forwardingChart = new Chart(ctxFwd, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'Broadcasts',
                        data: [],
                        borderColor: '#3b82f6',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.3,
                    },
                    {
                        label: 'Duplicates',
                        data: [],
                        borderColor: '#8b5cf6',
                        borderWidth: 2,
                        borderDash: [4, 4],
                        pointRadius: 0,
                        tension: 0.3,
                    },
                    {
                        label: 'Suppressed',
                        data: [],
                        borderColor: '#f59e0b',
                        borderWidth: 2,
                        borderDash: [8, 4],
                        pointRadius: 0,
                        tension: 0.3,
                    },
                ],
            },
            options: { ...chartDefaults },
        });
    }

    function update(data) {
        const n = data.coverage_history.length;
        const labels = Array.from({ length: n }, (_, i) => i + 1);
        const sampledLabels = labels.filter((_, i) => i % 10 === 0 || i === n - 1);

        // Coverage chart
        coverageChart.data.labels = sampledLabels;
        coverageChart.data.datasets[0].data = data.coverage_history.filter(
            (_, i) => i % 10 === 0 || i === n - 1
        );
        coverageChart.update('none');

        // Forwarding chart
        forwardingChart.data.labels = sampledLabels;
        forwardingChart.data.datasets[0].data = data.tx_history.filter(
            (_, i) => i % 10 === 0 || i === n - 1
        );
        forwardingChart.data.datasets[1].data = data.dup_history.filter(
            (_, i) => i % 10 === 0 || i === n - 1
        );
        forwardingChart.data.datasets[2].data = data.suppressed_history.filter(
            (_, i) => i % 10 === 0 || i === n - 1
        );
        forwardingChart.update('none');
    }

    function updateMetricCards(data) {
        const n = data.num_civilians;
        const reached = data.coverage_history[data.coverage_history.length - 1];
        const pct = ((reached / n) * 100).toFixed(1);

        document.getElementById('mv-coverage').textContent = `${reached}/${n} (${pct}%)`;
        document.getElementById('mv-broadcasts').textContent =
            data.tx_history[data.tx_history.length - 1].toLocaleString();
        document.getElementById('mv-duplicates').textContent =
            data.dup_history[data.dup_history.length - 1].toLocaleString();
        document.getElementById('mv-suppressed').textContent =
            data.suppressed_history[data.suppressed_history.length - 1].toLocaleString();
    }

    return { init, update, updateMetricCards };
})();
