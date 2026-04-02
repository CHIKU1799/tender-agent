/**
 * scope_fix.js — paste this at the BOTTOM of index.html before </body>
 * OR save as static/scope_fix.js and add <script src="/static/scope_fix.js"></script>
 *
 * Fixes:
 *   1. Verifies scope radio value is correctly read before sending
 *   2. Shows scope in the progress log so you can confirm it's working
 *   3. Adds visual indicator showing current scope during scrape
 */

// ── Override startScrape to add scope verification ────────────────────────
const _originalStartScrape = window.startScrape;

window.startScrape = async function() {
    // Read scope — check both name="scope" and any checked radio
    const scopeEl = document.querySelector('input[name="scope"]:checked');
    const scope   = scopeEl ? scopeEl.value : "active";

    // Show scope confirmation in status bar
    const statusEl = document.getElementById('scrape-status');
    if (statusEl) {
        const scopeLabels = {
            active:  '🟢 Active Tenders',
            archive: '📦 Archive / Past',
            awards:  '🏆 Awarded Contracts',
            both:    '📚 Archive + Awards',
            all:     '🌐 Everything (All Time)',
        };
        statusEl.textContent = `Scope: ${scopeLabels[scope] || scope}`;
        statusEl.style.color = scope === 'active' ? '#27ae60' : '#e67e22';
    }

    console.log('[scope_fix] Sending scope:', scope);

    // Call original
    await _originalStartScrape.call(this);
};

// ── Patch streamProgress to show scope in log ─────────────────────────────
const _originalStreamProgress = window.streamProgress;

window.streamProgress = function(taskId, total) {
    const src = new EventSource(`/api/stream/${taskId}`);
    let done  = 0;

    src.onmessage = ev => {
        const data = JSON.parse(ev.data);
        const logEl = document.getElementById('progress-log');
        const bar   = document.getElementById('progress-bar');
        const lbl   = document.getElementById('progress-label');

        if (data.type === 'start') {
            const scopeTag = data.scope ? ` [${data.scope}]` : '';
            if (logEl) logEl.innerHTML += `<div class="log-item start">▶ ${data.name}${scopeTag}</div>`;

        } else if (data.type === 'progress') {
            if (lbl) lbl.textContent = `${data.portal_id} — page ${data.page} (${data.count} tenders)`;

        } else if (data.type === 'complete') {
            done++;
            if (bar) bar.style.width = Math.round(done / total * 100) + '%';
            const icon    = data.errors && data.errors.length ? '⚠' : '✓';
            const cls     = icon === '✓' ? 'ok' : 'warn';
            const scopeTag = data.scope && data.scope !== 'active' ? ` [${data.scope}]` : '';
            if (logEl) {
                logEl.innerHTML += `<div class="log-item ${cls}">${icon} ${data.portal_id}${scopeTag}: ${data.count} tenders</div>`;
                logEl.scrollTop = logEl.scrollHeight;
            }

        } else if (data.type === 'error') {
            if (logEl) logEl.innerHTML += `<div class="log-item warn">✗ Error: ${data.message}</div>`;

        } else if (data.type === 'done') {
            if (bar) bar.style.width = '100%';
            if (lbl) lbl.textContent = `✓ Done — ${data.total} tenders scraped`;
            src.close();
            const btn = document.getElementById('scrape-btn');
            if (btn) { btn.disabled = false; btn.textContent = '▶ Start Scraping'; }
            setTimeout(() => {
                if (window.loadTenders) loadTenders(1);
                if (window.loadStats)   loadStats();
            }, 600);
        }
    };

    src.onerror = () => src.close();
};

console.log('[scope_fix] Loaded — scope routing fix active');
