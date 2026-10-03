/**
 * WP 3.1 — Kraków Pedestrian Mesh Renderer (Leaflet)
 *
 * Renders the TAURON Arena vicinity with dynamic node markers:
 *   Red stars    = Seed devices (emergency squads)
 *   Green dots   = Verified alert reception
 *   Grey dots    = Unreached devices
 */

const MeshMap = (() => {
    let map = null;
    let nodeLayer = null;

    // Kraków TAURON Arena vicinity
    const CENTER = [50.0647, 19.9650];
    const ZOOM = 15;

    function init(containerId) {
        map = L.map(containerId, {
            zoomControl: false,
            attributionControl: false,
        }).setView(CENTER, ZOOM);

        // Dark tile layer
        L.tileLayer(
            'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            {
                maxZoom: 19,
                subdomains: 'abcd',
            }
        ).addTo(map);

        L.control.zoom({ position: 'topright' }).addTo(map);
        L.control.attribution({ position: 'bottomright', prefix: false })
            .addAttribution('© OpenStreetMap | BlackoutMesh')
            .addTo(map);

        nodeLayer = L.layerGroup().addTo(map);

        return map;
    }

    function updateNodes(positions, types) {
        if (!nodeLayer) return;
        nodeLayer.clearLayers();

        // positions are in projected CRS — we need lat/lon
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
            const opacity = type === 'unreached' ? 0.5 : 0.9;

            // Note: positions from simulation are projected meters (EPSG:2180), not lat/lon.
            // For the dashboard visualizer, map from projected coordinates to lat/lon around TAURON Arena.
            const lat = CENTER[0] + (y - 5560800) / 111320;
            const lon = CENTER[1] + (x - 421400) / (111320 * Math.cos(CENTER[0] * Math.PI / 180));

            const marker = L.circleMarker([lat, lon], {
                radius,
                fillColor: color,
                color: type === 'seed' ? '#fbbf24' : color,
                weight: type === 'seed' ? 2 : 1,
                fillOpacity: opacity,
            });

            marker.bindPopup(
                `<strong>${type.toUpperCase()}</strong><br>` +
                `Node #${i}<br>` +
                `Position: (${x.toFixed(0)}, ${y.toFixed(0)})`
            );

            nodeLayer.addLayer(marker);
        }
    }

    return { init, updateNodes };
})();
