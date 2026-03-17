/**
 * Market Analyzer Pro — Shared JavaScript utilities
 */

const API_BASE = '/api';
let ws = null;
let wsReconnectTimer = null;

// ── API Helper ─────────────────────────────────────────────────────────────

async function apiGet(endpoint, params = {}) {
    const url = new URL(API_BASE + endpoint, window.location.origin);
    Object.entries(params).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') {
            url.searchParams.set(k, v);
        }
    });
    const response = await fetch(url.toString());
    if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || `HTTP ${response.status}`);
    }
    return response.json();
}

async function apiPost(endpoint, params = {}) {
    const url = new URL(API_BASE + endpoint, window.location.origin);
    Object.entries(params).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') {
            url.searchParams.set(k, v);
        }
    });
    const response = await fetch(url.toString(), { method: 'POST' });
    if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || `HTTP ${response.status}`);
    }
    return response.json();
}

// ── WebSocket Connection ───────────────────────────────────────────────────

function connectWebSocket(path, onMessage) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${path}`;

    const wsConn = new WebSocket(wsUrl);

    wsConn.onopen = () => {
        console.log('[WS] Connected:', path);
        updateConnectionStatus(true);
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = null;
        }
    };

    wsConn.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type !== 'heartbeat') {
                onMessage(data);
            }
        } catch (e) {
            console.error('[WS] Parse error:', e);
        }
    };

    wsConn.onclose = () => {
        console.log('[WS] Disconnected, reconnecting in 5s...');
        updateConnectionStatus(false);
        wsReconnectTimer = setTimeout(() => {
            connectWebSocket(path, onMessage);
        }, 5000);
    };

    wsConn.onerror = (err) => {
        console.error('[WS] Error:', err);
    };

    return wsConn;
}

function updateConnectionStatus(connected) {
    const dot = document.querySelector('.status-dot');
    const label = document.querySelector('.status-label');
    if (dot) {
        dot.classList.toggle('offline', !connected);
    }
    if (label) {
        label.textContent = connected ? 'Live' : 'Offline';
    }
}

// ── Formatting ──────────────────────────────────────────────────────────────

function getPriceDecimals(symbol, price) {
    if (!symbol && price !== undefined) {
        // Infer from price magnitude
        const p = Math.abs(parseFloat(price));
        if (p >= 1000) return 2;
        if (p >= 10)   return 3;
        if (p >= 1)    return 4;
        return 5;
    }
    const s = (symbol || '').toUpperCase();
    if (s.includes('JPY')) return 3;
    if (s.includes('BTC') || s.includes('ETH') || s.includes('SOL') ||
        s.includes('BNB') || s.includes('XRP') || s.includes('ADA')) {
        const p = Math.abs(parseFloat(price) || 0);
        return p >= 1000 ? 2 : p >= 10 ? 3 : 4;
    }
    if (s.includes('=X') || s.includes('USD') || s.includes('EUR') ||
        s.includes('GBP') || s.includes('AUD') || s.includes('CHF')) return 5;
    // Stocks, indices
    return 2;
}

function formatPrice(price, decimals) {
    if (price === null || price === undefined) return '—';
    const d = decimals !== undefined ? decimals : getPriceDecimals(selectedSymbol, price);
    return parseFloat(price).toFixed(d);
}

function formatNumber(n, decimals = 2) {
    if (n === null || n === undefined) return '—';
    return parseFloat(n).toFixed(decimals);
}

function formatPercent(n) {
    if (n === null || n === undefined) return '—';
    const v = parseFloat(n) * 100;
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toFixed(1)}%`;
}

function formatDate(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function formatScore(score) {
    const s = parseFloat(score);
    const sign = s >= 0 ? '+' : '';
    return `${sign}${s.toFixed(1)}`;
}

// ── Signal Strength Color ──────────────────────────────────────────────────

function getSignalClass(strength) {
    const map = {
        'STRONG_BUY': 'text-green',
        'BUY': 'text-green',
        'HOLD': 'text-secondary',
        'SELL': 'text-red',
        'STRONG_SELL': 'text-red',
    };
    return map[strength] || '';
}

function getDirectionSymbol(direction) {
    return direction === 'LONG' ? '▲' : direction === 'SHORT' ? '▼' : '●';
}

function getDirectionClass(direction) {
    return direction === 'LONG' ? 'text-green' : direction === 'SHORT' ? 'text-red' : 'text-muted';
}

// ── Score Bar Rendering ────────────────────────────────────────────────────

function renderScoreBar(containerId, score) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const s = parseFloat(score) || 0;
    const pct = Math.min(Math.abs(s) / 2, 50); // max 50% fill each side
    const isPositive = s >= 0;
    const colorClass = isPositive ? 'positive' : 'negative';
    const color = isPositive ? 'var(--accent-green)' : 'var(--accent-red)';

    container.innerHTML = `
        <div class="score-bar">
            <div class="score-fill ${colorClass}" style="width: ${pct}%"></div>
        </div>
        <div class="score-value" style="color: ${color}">${formatScore(s)}</div>
    `;
}

// ── Toast Notifications ────────────────────────────────────────────────────

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-left: 4px solid ${type === 'error' ? 'var(--accent-red)' : type === 'success' ? 'var(--accent-green)' : 'var(--accent-blue)'};
        color: var(--text-primary);
        padding: 12px 16px;
        border-radius: 6px;
        font-size: 13px;
        z-index: 9999;
        max-width: 320px;
        animation: slideIn 0.2s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ── DOM Helpers ────────────────────────────────────────────────────────────

function $(selector) {
    return document.querySelector(selector);
}

function $$(selector) {
    return document.querySelectorAll(selector);
}

function setHTML(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setLoading(buttonId, spinnerId, loading) {
    const btn = document.getElementById(buttonId);
    const spinner = document.getElementById(spinnerId);
    if (btn) btn.disabled = loading;
    if (spinner) spinner.classList.toggle('active', loading);
}

// ── Market Tag ────────────────────────────────────────────────────────────

function marketTag(market) {
    return `<span class="tag tag-${market}">${market.toUpperCase()}</span>`;
}

// ── Status Badge ──────────────────────────────────────────────────────────

function statusBadge(status) {
    const colors = {
        'created': '#6e7681',
        'active': '#58a6ff',
        'tracking': '#e3b341',
        'completed': '#3fb950',
        'expired': '#484f58',
        'cancelled': '#484f58',
    };
    const color = colors[status] || '#6e7681';
    return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;color:${color};font-weight:600;text-transform:uppercase">${status}</span>`;
}

// ── Health Check ──────────────────────────────────────────────────────────

async function checkHealth() {
    try {
        const health = await apiGet('/health');
        updateConnectionStatus(true);
        return health;
    } catch (e) {
        updateConnectionStatus(false);
        return null;
    }
}

// ── Export ────────────────────────────────────────────────────────────────

window.MarketAnalyzer = {
    apiGet,
    apiPost,
    connectWebSocket,
    formatPrice,
    formatNumber,
    formatPercent,
    formatDate,
    formatScore,
    getSignalClass,
    getDirectionSymbol,
    getDirectionClass,
    renderScoreBar,
    showToast,
    $, $$,
    setHTML,
    setText,
    setLoading,
    marketTag,
    statusBadge,
    checkHealth,
};
