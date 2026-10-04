/**
 * WP 3.1 & 8.2 — Kraków Pedestrian Mesh Renderer (Leaflet)
 *
 * Implements:
 * - Kraków TAURON Arena pedestrian mesh visualizer
 * - Dynamic node markers: Seeds, Reached (simulated), Unreached (simulated)
 * - Offline tile fallback detection with notice (WP 8.2)
 * - Seed caption updater (WP 6.5)
 * - Esri licensing attribution retained (Section 3)
 */

const MeshMap = (() => {
    let map = null;
    let nodeLayer = null;
    let esriDarkLayer = null;
    let osmLayer = null;
    let offlineFallbackTriggered = false;

    // Kraków TAURON Arena vicinity
    const CENTER = [50.0647, 19.9650];
    const ZOOM = 15;

    function init(containerId) {
        map = L.map(containerId, {
            zoomControl: false,
            attributionControl: false,
        }).setView(CENTER, ZOOM);

        // Tactical Dark Canvas (Esri Basemap)
        esriDarkLayer = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
            {
                maxZoom: 19,
                attribution: '© Esri, HERE, Garmin | BlackoutMesh Tactical Console',
            }
        );

        // OpenStreetMap Standard fallback
        osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors',
        });

        // Offline detection: listen for tile load failures (WP 8.2)
        esriDarkLayer.on('tileerror', handleTileError);
        osmLayer.on('tileerror', handleTileError);

        esriDarkLayer.addTo(map);

        const baseMaps = {
            "Tactical Dark (Esri)": esriDarkLayer,
            "OpenStreetMap": osmLayer,
        };

        L.control.layers(baseMaps, null, { position: 'topright' }).addTo(map);
        L.control.zoom({ position: 'topright' }).addTo(map);

        // Esri licensing attribution is mandatory
        L.control.attribution({ position: 'bottomright', prefix: false })
            .addAttribution('© Esri, HERE, Garmin | BlackoutMesh')
            .addTo(map);

        nodeLayer = L.layerGroup().addTo(map);

        return map;
    }

    function handleTileError() {
        if (!offlineFallbackTriggered) {
            offlineFallbackTriggered = true;
            const notice = document.getElementById('offline-map-notice');
            if (notice) {
                notice.classList.remove('hidden');
            }
        }
    }

    function updateCaption(seed) {
        const caption = document.getElementById('map-caption');
        if (caption) {
            if (seed !== undefined && seed !== null) {
                caption.textContent = `Kraków Mesh Coverage (simulated example run) — Seed: ${seed}`;
            } else {
                caption.textContent = 'Kraków Mesh Coverage (simulated example run)';
            }
        }
    }

    function updateNodes(positions, types, seed) {
        if (!nodeLayer) return;
        nodeLayer.clearLayers();

        updateCaption(seed);

        if (!positions || positions.length === 0) return;

        const colors = {
            seed: '#ef4444',
            reached: '#10b981',
            unreached: '#64748b',
        };

        for (let i = 0; i < positions.length; i++) {
            const [x, y] = positions[i];
            const type = types[i] || 'unreached';
            const color = colors[type] || colors.unreached;
            const radius = type === 'seed' ? 8 : 5;
            const opacity = type === 'unreached' ? 0.45 : 0.9;

            // Map EPSG:2180 projected coordinates to lat/lon around TAURON Arena
            const lat = CENTER[0] + (y - 5560800) / 111320;
            const lon = CENTER[1] + (x - 421400) / (111320 * Math.cos(CENTER[0] * Math.PI / 180));

            const typeLabel = type === 'seed'
                ? 'SEED (Simulated)'
                : (type === 'reached' ? 'REACHED (Simulated)' : 'UNREACHED (Simulated)');

            const marker = L.circleMarker([lat, lon], {
                radius,
                fillColor: color,
                color: type === 'seed' ? '#fbbf24' : color,
                weight: type === 'seed' ? 2 : 1,
                fillOpacity: opacity,
            });

            marker.bindPopup(
                `<strong>${typeLabel}</strong><br>` +
                `Simulated Node #${i}<br>` +
                `Coordinates: (${x.toFixed(0)}, ${y.toFixed(0)})`
            );

            nodeLayer.addLayer(marker);
        }
    }

    return { init, updateNodes, updateCaption };
})();
