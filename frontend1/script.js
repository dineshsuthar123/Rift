/* =============================================
   STREAMLINE — Backend-Integrated Script
   Wires form → POST /api/analyze → SSE progress
   ============================================= */

const API_BASE = '';

document.addEventListener('DOMContentLoaded', () => {

    /* ---------- Navbar scroll effect ---------- */
    const navbar = document.querySelector('.navbar');
    window.addEventListener('scroll', () => {
        navbar.classList.toggle('scrolled', window.scrollY > 80);
    }, { passive: true });

    /* ---------- Scroll Reveal ---------- */
    const revealTargets = document.querySelectorAll(
        '.problem-section, .tagline-section, .presenting-section, .form-section, .cta-section, .results-section'
    );
    revealTargets.forEach(el => el.classList.add('reveal'));
    const revealObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    revealObserver.unobserve(entry.target);
                }
            });
        },
        { threshold: 0.10 }
    );
    revealTargets.forEach(el => revealObserver.observe(el));

    /* ---------- Smooth scroll links ---------- */
    document.querySelectorAll('a[href^="#"]').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelector(link.getAttribute('href'))?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });

    /* ---------- Nav buttons ---------- */
    document.querySelector('.btn-get-started')?.addEventListener('click', () => {
        document.getElementById('demo')?.scrollIntoView({ behavior: 'smooth' });
    });
    document.querySelector('.btn-login-github')?.addEventListener('click', () => {
        document.getElementById('demo')?.scrollIntoView({ behavior: 'smooth' });
    });
    document.querySelector('.hamburger')?.addEventListener('click', () => {
        const navRight = document.querySelector('.nav-right');
        navRight.style.display = navRight.style.display === 'flex' ? '' : 'flex';
    });

    /* ---------- GitHub arrow btn ---------- */
    document.querySelector('.github-arrow-btn')?.addEventListener('click', () => {
        const url = document.getElementById('githubUrl').value.trim();
        if (url) window.open(url.startsWith('http') ? url : 'https://' + url, '_blank');
    });

    /* =============================================
       MAIN: Form Submit → API → Live Results
       ============================================= */

    const submitBtn = document.getElementById('submitBtn');
    const formCard = document.querySelector('.clean-form-card');
    const resultsSection = document.getElementById('results');

    let currentEventSource = null;
    let iterationCount = 0;
    let maxIter = 50;
    let commitCount = 0;

    submitBtn.addEventListener('click', async () => {
        const teamName = document.getElementById('teamName');
        const leaderName = document.getElementById('leaderName');
        const githubUrl = document.getElementById('githubUrl');

        formCard.querySelectorAll('.clean-input').forEach(el => el.classList.remove('input-error'));

        let valid = true;
        if (!teamName.value.trim()) { teamName.classList.add('input-error'); valid = false; }
        if (!leaderName.value.trim()) { leaderName.classList.add('input-error'); valid = false; }
        if (!githubUrl.value.trim() || !githubUrl.value.includes('github')) {
            githubUrl.classList.add('input-error');
            valid = false;
        }

        if (!valid) {
            formCard.classList.add('shake');
            setTimeout(() => formCard.classList.remove('shake'), 400);
            return;
        }

        if (currentEventSource) { currentEventSource.close(); currentEventSource = null; }

        resultsSection.style.display = 'block';
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        resetResults();
        setStatus('running', 'STARTING');

        submitBtn.disabled = true;
        submitBtn.textContent = 'Running...';
        iterationCount = 0;
        commitCount = 0;

        try {
            const res = await fetch(`${API_BASE}/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_url: githubUrl.value.trim(),
                    team_name: teamName.value.trim(),
                    leader_name: leaderName.value.trim(),
                }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ message: res.statusText }));
                throw new Error(err.message || 'Failed to start analysis');
            }

            const data = await res.json();
            const runId = data.run_id;
            const branchName = data.branch_name;
            maxIter = data.max_iterations || 50;

            set('res-repo', githubUrl.value.trim());
            set('res-team', teamName.value.trim());
            set('res-leader', leaderName.value.trim());
            set('res-branch', branchName || '—');
            set('res-iter', `0 / ${maxIter} iterations`);
            addLog('[01] Analysis started — connecting to live feed...');
            setStatus('running', 'RUNNING');

            currentEventSource = new EventSource(`${API_BASE}/api/status/${runId}`);

            currentEventSource.addEventListener('progress', (e) => {
                const d = JSON.parse(e.data);
                if (d.message) addLog(d.message);
            });

            currentEventSource.addEventListener('iteration', (e) => {
                const d = JSON.parse(e.data);
                updateIteration(d);
            });

            currentEventSource.addEventListener('fix', (e) => {
                const d = JSON.parse(e.data);
                addFixRow(d);
            });

            currentEventSource.addEventListener('complete', (e) => {
                const d = JSON.parse(e.data);
                handleComplete(d, 'passed');
                currentEventSource.close();
            });

            // Named server error events
            currentEventSource.addEventListener('error', (e) => {
                if (!e.data) return;
                try {
                    const d = JSON.parse(e.data);
                    addLog(`❌ ${d.message || 'Agent error'}`);
                    handleComplete(d, 'failed');
                } catch {
                    addLog('❌ Agent reported an error');
                }
                currentEventSource.close();
                resetSubmitBtn();
            });

            // Network / connection errors
            currentEventSource.onerror = () => {
                const state = currentEventSource.readyState;
                if (state === EventSource.CONNECTING) {
                    setStatus('running', 'RECONNECTING');
                    addLog('⟳ Connection dropped — retrying...');
                } else {
                    setStatus('error', 'LOST');
                    addLog('⚠️ Connection closed. Backend may have restarted — check terminal.');
                    resetSubmitBtn();
                }
            };

        } catch (err) {
            addLog(`❌ ${err.message}`);
            setStatus('error', 'ERROR');
            resetSubmitBtn();
        }
    });

    /* ─── Helpers ─────────────────────────────── */

    function set(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val ?? '—';
    }

    function resetSubmitBtn() {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
    }

    function resetResults() {
        // Summary panel
        set('res-repo', '—'); set('res-team', '—'); set('res-leader', '—'); set('res-branch', '—');
        set('res-errors', '—'); set('res-fixes', '—'); set('res-commits', '—');
        set('res-time', 'In progress...'); set('res-iter', `0 / 50 iterations`);
        // Score panel
        set('res-score', '—'); set('bd-base', '0'); set('bd-accuracy', '0% accuracy');
        set('bd-speed', '0'); set('bd-speed-sub', '>= 5 min');
        set('bd-eff', '0'); set('bd-eff-sub', '0 commits');
        document.getElementById('score-bar').style.width = '0%';
        // Timeline
        document.getElementById('timeline-list').innerHTML = '<p class="empty-msg">Waiting for first test iteration...</p>';
        // Log
        document.getElementById('log-list').innerHTML = '';
        // Fixes table
        document.getElementById('fixes-tbody').innerHTML = '';
        document.getElementById('fixes-table').style.display = 'none';
        document.getElementById('fixes-empty').style.display = 'block';
    }

    function setStatus(state, label) {
        const badge = document.getElementById('status-badge');
        badge.textContent = label;
        badge.className = 'status-badge status-' + state;
    }

    function addLog(msg) {
        const li = document.createElement('li');
        li.textContent = msg;
        document.getElementById('log-list').appendChild(li);
        li.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function updateIteration(d) {
        const iter = d.iteration || 0;
        maxIter = d.max_iterations || maxIter;
        const errors = d.errors_remaining ?? d.error_count ?? 0;
        commitCount = d.commits ?? commitCount;

        iterationCount = iter;
        set('res-iter', `${iter} / ${maxIter} iterations`);
        set('res-errors', errors);
        set('res-commits', commitCount);
        setStatus('running', `RUNNING`);

        // Add timeline entry
        const tl = document.getElementById('timeline-list');
        // Remove empty msg on first entry
        const emptyMsg = tl.querySelector('.empty-msg');
        if (emptyMsg) emptyMsg.remove();

        const passed = errors === 0;
        const entry = document.createElement('div');
        entry.className = 'tl-entry';
        entry.innerHTML = `
            <div class="tl-dot ${passed ? 'tl-dot-pass' : 'tl-dot-fail'}"></div>
            <div class="tl-card ${passed ? 'tl-card-pass' : 'tl-card-fail'}">
                <div class="tl-card-top">
                    <div class="tl-left">
                        <span class="tl-badge ${passed ? 'tl-badge-pass' : 'tl-badge-fail'}">${passed ? 'PASS' : 'FAIL'}</span>
                        <span class="tl-label">Iteration ${iter}/${maxIter}</span>
                    </div>
                    <span class="tl-time">${new Date().toLocaleTimeString()}</span>
                </div>
                <p class="tl-sub">${errors} error${errors !== 1 ? 's' : ''} remaining</p>
            </div>`;
        tl.appendChild(entry);
        entry.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    const BUG_COLORS = {
        LINTING: 'badge-yellow', SYNTAX: 'badge-red', LOGIC: 'badge-purple',
        TYPE_ERROR: 'badge-orange', IMPORT: 'badge-blue', INDENTATION: 'badge-teal',
    };

    function addFixRow(fix) {
        document.getElementById('fixes-empty').style.display = 'none';
        const table = document.getElementById('fixes-table');
        table.style.display = 'table';

        const bugType = fix.bug_type || 'UNKNOWN';
        const isFixed = fix.status === 'fixed' || fix.status === 'success' || !fix.status;
        const colorClass = BUG_COLORS[bugType] || 'badge-gray';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="td-mono">${fix.file_path || fix.file || '—'}</td>
            <td><span class="bug-badge ${colorClass}">${bugType}</span></td>
            <td class="td-mono td-dim">${fix.line_number || fix.line || '—'}</td>
            <td class="td-dim td-truncate">${fix.commit_message || fix.fix_description || fix.description || '—'}</td>
            <td>${isFixed ? '<span class="status-fixed">✓ Fixed</span>' : '<span class="status-unfixed">✗ Failed</span>'}</td>`;
        document.getElementById('fixes-tbody').appendChild(tr);

        // Update fixes count
        const count = document.getElementById('fixes-tbody').children.length;
        set('res-fixes', count);
    }

    function handleComplete(d, outcome) {
        const s = d.score || {};
        const baseScore = s.baseScore ?? s.base_score ?? 0;
        const accuracyRate = s.accuracyRate ?? s.accuracy_rate ?? 0;
        const speedBonus = s.speedBonus ?? s.speed_bonus ?? 0;
        const effPenalty = s.efficiencyPenalty ?? s.efficiency_penalty ?? 0;
        const finalScore = s.finalScore ?? s.final_score ?? d.final_score ?? '—';
        const elapsed = d.elapsed_seconds ? `${d.elapsed_seconds.toFixed(1)}s` : '—';
        const errors = d.errors_remaining ?? 0;
        const commits = d.total_commits ?? commitCount;
        const fixes = d.fixes_applied ?? d.successful_fixes ?? 0;

        set('res-score', finalScore);
        set('res-time', elapsed);
        set('res-errors', errors);
        set('res-commits', commits);
        set('res-fixes', fixes);

        // Score breakdown
        set('bd-base', baseScore);
        set('bd-accuracy', `${accuracyRate}% accuracy`);
        set('bd-speed', speedBonus > 0 ? `+${speedBonus}` : '0');
        set('bd-speed-sub', speedBonus > 0 ? '< 5 min' : '>= 5 min');
        set('bd-eff', effPenalty > 0 ? `-${effPenalty}` : '0');
        set('bd-eff-sub', `${commits} commits`);

        // Speed badge color
        const speedEl = document.getElementById('bd-speed');
        speedEl.className = `breakdown-val ${speedBonus > 0 ? 'breakdown-good' : 'breakdown-neutral'}`;
        const effEl = document.getElementById('bd-eff');
        effEl.className = `breakdown-val ${effPenalty > 0 ? 'breakdown-bad' : 'breakdown-good'}`;

        // Score bar (max 110)
        const barPct = Math.min(100, (Number(finalScore) / 110) * 100) || 0;
        document.getElementById('score-bar').style.width = `${barPct}%`;

        setStatus(outcome === 'passed' ? 'passed' : 'failed', outcome === 'passed' ? '✅ PASSED' : '❌ FAILED');
        addLog(`🏁 Run complete — Score: ${finalScore} | Time: ${elapsed} | Fixes: ${fixes}`);
        resetSubmitBtn();
    }

});
