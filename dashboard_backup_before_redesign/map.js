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
    let cartoLayer = null;
    let esriDarkLayer = null;
    let osmLayer = null;
    let currentApiKey = '';

    // Kraków TAURON Arena vicinity
    const CENTER = [50.0647, 19.9650];
    const ZOOM = 15;

    function getCartoUrl(apiKey) {
        if (apiKey) {
            // CARTO Basemaps uses ?key= (not ?api_key=)
            return `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(apiKey)}`;
        }
        return 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
    }

    function init(containerId, apiKey = '') {
        currentApiKey = apiKey;
        map = L.map(containerId, {
            zoomControl: false,
            attributionControl: false,
        }).setView(CENTER, ZOOM);

        // 1. Tactical Dark Canvas (Unwatermarked, high-contrast dark theme)
        esriDarkLayer = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
            {
                maxZoom: 19,
                attribution: '© Esri, HERE, Garmin | BlackoutMesh',
            }
        );

        // 2. CARTO Dark Matter (uses ?key= with Basemap API key)
        cartoLayer = L.tileLayer(getCartoUrl(currentApiKey), {
            maxZoom: 19,
            subdomains: 'abcd',
            attribution: '© CARTO | © OpenStreetMap',
        });

        // 3. OpenStreetMap Standard
        osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors',
        });

        // Default to clean Tactical Dark Canvas
        esriDarkLayer.addTo(map);

        const baseMaps = {
            "Tactical Dark (Clean)": esriDarkLayer,
            "CARTO Dark Matter": cartoLayer,
            "OpenStreetMap": osmLayer,
        };

        L.control.layers(baseMaps, null, { position: 'topright' }).addTo(map);
        L.control.zoom({ position: 'topright' }).addTo(map);
        L.control.attribution({ position: 'bottomright', prefix: false })
            .addAttribution('BlackoutMesh Tactical Console')
            .addTo(map);

        nodeLayer = L.layerGroup().addTo(map);

        return map;
    }

    function setApiKey(apiKey) {
        if (!apiKey || apiKey === currentApiKey) return;
        currentApiKey = apiKey;
        if (cartoLayer) {
            cartoLayer.setUrl(getCartoUrl(currentApiKey));
        }
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

    return { init, updateNodes, setApiKey };
})();
