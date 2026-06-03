'use strict';

// --- state ---
let _state = { branches: [], groups: [] };
let _trash = []; // {sha, short_sha, title, branch}
let _dragSha = null;
let _dragBranch = null;

// --- WebSocket ---
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => setStatus('connected');
ws.onerror = () => setStatus('connection error');
ws.onclose = () => setStatus('disconnected');
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.error) { showError('Server error', msg.error, ''); return; }
  _state = msg;
  render();
};

function setStatus(text) {
  document.getElementById('status').textContent = text;
}

// --- build rows ---
// Returns an array of row objects sorted newest-first.
// Each row: { groupId, colorIndex, cells: {branchName: commit|null}, timestamp }
function buildRows(state) {
  const { branches, groups } = state;

  // sha → {branchName: commit, ...} — handles the same SHA appearing on multiple branches
  const shaByBranch = {};
  for (const b of branches) {
    for (const c of b.commits) {
      if (!shaByBranch[c.sha]) shaByBranch[c.sha] = {};
      shaByBranch[c.sha][b.name] = { ...c, branchName: b.name };
    }
  }

  const usedShas = new Set();
  const rows = [];

  // One row per group
  for (const group of groups) {
    const cells = {};
    let maxTs = 0;
    for (const sha of group.commit_shas) {
      const byBranch = shaByBranch[sha];
      if (byBranch) {
        for (const [branchName, c] of Object.entries(byBranch)) {
          cells[branchName] = c;
          maxTs = Math.max(maxTs, c.timestamp);
        }
        usedShas.add(sha);
      }
    }
    rows.push({ groupId: group.id, colorIndex: group.color_index, cells, timestamp: maxTs });
  }

  // One row per unmatched commit, deduplicated by SHA so shared base commits appear once
  const unmatchedBySha = {};
  for (const b of branches) {
    for (const c of b.commits) {
      if (!usedShas.has(c.sha)) {
        if (!unmatchedBySha[c.sha]) unmatchedBySha[c.sha] = {};
        unmatchedBySha[c.sha][b.name] = { ...c, branchName: b.name };
      }
    }
  }
  for (const byBranch of Object.values(unmatchedBySha)) {
    const maxTs = Math.max(...Object.values(byBranch).map(c => c.timestamp));
    rows.push({ groupId: null, colorIndex: null, cells: byBranch, timestamp: maxTs });
  }

  rows.sort((a, b) => b.timestamp - a.timestamp);
  return rows;
}

// --- render ---
function render() {
  renderGrid();
  renderTrash();
}

function renderGrid() {
  const container = document.getElementById('grid-container');
  const { branches } = _state;
  if (!branches.length) return;

  const n = branches.length;
  const cols = `repeat(${n}, minmax(180px, 1fr))`;
  container.style.gridTemplateColumns = cols;
  container.style.minWidth = `${n * 180}px`;

  container.innerHTML = '';

  // Row 1: branch headers
  for (const b of branches) {
    const h = document.createElement('div');
    h.className = 'branch-header';
    h.textContent = b.name;
    container.appendChild(h);
  }

  // Rows 2+: one CSS grid row per logical row
  const rows = buildRows(_state);
  for (const row of rows) {
    for (const b of branches) {
      const commit = row.cells[b.name];
      const cell = document.createElement('div');
      cell.className = 'grid-cell';
      if (row.colorIndex != null) cell.classList.add(`row-group-${row.colorIndex}`);
      cell.dataset.branch = b.name;
      if (row.groupId) cell.dataset.groupId = row.groupId;

      if (commit) {
        if (commit.hidden) {
          cell.appendChild(makeHiddenStrip(commit, b.name));
        } else {
          const card = makeCommitCard(commit, row);
          cell.appendChild(card);
        }
      } else {
        // Empty cell — drop target for cherry-pick
        cell.classList.add('empty');
        if (row.groupId) setupCherryPickTarget(cell, row, b.name);
      }

      container.appendChild(cell);
    }
  }
}

// --- commit card ---
function makeCommitCard(c, row) {
  const card = document.createElement('div');
  card.className = 'commit-card' + (c.color_index != null ? ` group-${c.color_index}` : '');
  card.dataset.sha = c.sha;
  card.dataset.branch = c.branchName || c.branch;
  card.draggable = true;

  const sha = document.createElement('span');
  sha.className = 'sha';
  sha.textContent = c.short_sha;

  const title = document.createElement('span');
  title.className = 'title' + (c.group_id ? ' has-group' : '');
  title.textContent = c.title;
  title.title = c.author;
  if (c.group_id) {
    title.addEventListener('click', (e) => { e.stopPropagation(); openDiffDialog(c); });
  }

  const actions = document.createElement('span');
  actions.className = 'actions';

  const hideBtn = document.createElement('button');
  hideBtn.className = 'btn-hide';
  hideBtn.textContent = '−';
  hideBtn.title = 'Hide';
  hideBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    postOp({ type: 'hide', sha: c.sha, branch: c.branchName || c.branch });
  });

  const delBtn = document.createElement('button');
  delBtn.className = 'btn-del';
  delBtn.textContent = '✕';
  delBtn.title = 'Delete';
  delBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    _trash.push({ sha: c.sha, short_sha: c.short_sha, title: c.title, branch: c.branchName || c.branch });
    postOp({ type: 'delete', sha: c.sha, branch: c.branchName || c.branch });
  });

  const upBtn = document.createElement('button');
  upBtn.className = 'btn-up';
  upBtn.textContent = '↑';
  upBtn.title = 'Move up (reorder)';
  upBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    moveCommit(c.sha, c.branchName || c.branch, -1);
  });

  const dnBtn = document.createElement('button');
  dnBtn.className = 'btn-dn';
  dnBtn.textContent = '↓';
  dnBtn.title = 'Move down (reorder)';
  dnBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    moveCommit(c.sha, c.branchName || c.branch, +1);
  });

  actions.append(upBtn, dnBtn, hideBtn, delBtn);
  card.append(sha, title, actions);

  // Drag source for cherry-pick
  card.addEventListener('dragstart', (e) => {
    _dragSha = c.sha;
    _dragBranch = c.branchName || c.branch;
    card.classList.add('drag-source');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', c.sha);
  });
  card.addEventListener('dragend', () => {
    _dragSha = null;
    _dragBranch = null;
    card.classList.remove('drag-source');
  });

  return card;
}

// --- hidden strip (single commit) ---
function makeHiddenStrip(c, branchName) {
  const strip = document.createElement('div');
  strip.className = 'hidden-strip';
  strip.title = c.title;
  strip.addEventListener('click', () => openHiddenDialog([c], branchName));
  return strip;
}

// --- empty cell drag-and-drop (cherry-pick) ---
function setupCherryPickTarget(cell, row, targetBranch) {
  cell.addEventListener('dragover', (e) => {
    if (_dragSha && _dragBranch !== targetBranch) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      cell.classList.add('drag-over');
    }
  });
  cell.addEventListener('dragleave', () => cell.classList.remove('drag-over'));
  cell.addEventListener('drop', (e) => {
    e.preventDefault();
    cell.classList.remove('drag-over');
    if (_dragSha && _dragBranch !== targetBranch) {
      postOp({ type: 'cherrypick', sha: _dragSha, target_branch: targetBranch });
    }
  });
}

// --- reorder within branch via ↑↓ ---
function moveCommit(sha, branch, delta) {
  const b = _state.branches.find(br => br.name === branch);
  if (!b) return;
  const visible = b.commits.filter(c => !c.hidden);
  const idx = visible.findIndex(c => c.sha === sha);
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= visible.length) return;

  const newOrder = visible.map(c => c.sha);
  [newOrder[idx], newOrder[newIdx]] = [newOrder[newIdx], newOrder[idx]];
  postOp({ type: 'reorder', branch, new_order: newOrder });
}

// --- diff overlay ---
async function openDiffDialog(c) {
  const group = _state.groups.find(g => g.id === c.group_id);
  if (!group) return;
  const others = group.commit_shas.filter(s => s !== c.sha);
  if (!others.length) return;

  const sha2 = others[0];
  const res = await fetch(`/api/diff/${c.sha}/${sha2}`);
  const { diff } = await res.json();

  document.getElementById('diff-title').textContent =
    `${c.short_sha} ↔ ${sha2.slice(0, 8)}`;
  document.getElementById('diff-content').innerHTML = colorDiff(diff || '(no diff)');
  document.getElementById('diff-dialog').showModal();
}

function colorDiff(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .split('\n')
    .map(line => {
      if (line.startsWith('+')) return `<span class="diff-add">${line}</span>`;
      if (line.startsWith('-')) return `<span class="diff-del">${line}</span>`;
      return `<span class="diff-ctx">${line}</span>`;
    })
    .join('\n');
}

document.getElementById('diff-close').addEventListener('click', () =>
  document.getElementById('diff-dialog').close());

// --- hidden overlay ---
function openHiddenDialog(commits, branchName) {
  document.getElementById('hidden-title').textContent = `hidden on ${branchName}`;
  const ul = document.getElementById('hidden-list');
  ul.innerHTML = '';
  for (const c of commits) {
    const li = document.createElement('li');
    const span = document.createElement('span');
    span.textContent = `${c.short_sha} ${c.title}`;
    const btn = document.createElement('button');
    btn.textContent = 'show';
    btn.addEventListener('click', () => {
      postOp({ type: 'unhide', sha: c.sha, branch: branchName });
      document.getElementById('hidden-dialog').close();
    });
    li.append(span, btn);
    ul.appendChild(li);
  }
  document.getElementById('hidden-dialog').showModal();
}

document.getElementById('hidden-close').addEventListener('click', () =>
  document.getElementById('hidden-dialog').close());

// --- error overlay ---
function showError(title, output, command) {
  document.getElementById('error-command').textContent = command ? `$ ${command}` : '';
  document.getElementById('error-output').textContent = output;
  document.getElementById('error-dialog').showModal();
}
document.getElementById('error-close').addEventListener('click', () =>
  document.getElementById('error-dialog').close());

// --- trash panel ---
function renderTrash() {
  const list = document.getElementById('trash-list');
  list.innerHTML = '';
  for (const c of _trash) {
    const li = document.createElement('li');
    li.className = 'trash-item';
    const sha = document.createElement('span');
    sha.className = 'sha';
    sha.textContent = c.short_sha;
    const title = document.createElement('span');
    title.textContent = c.title;
    li.append(sha, title);
    list.appendChild(li);
  }
}

// --- flush hidden ---
document.getElementById('flush-hidden-btn').addEventListener('click', async () => {
  await fetch('/api/hidden/flush', { method: 'POST' });
});

// --- API helper ---
async function postOp(body) {
  const res = await fetch('/api/operation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!data.success) {
    showError('Operation failed', data.error || '', data.command || '');
    const s = await fetch('/api/state');
    _state = await s.json();
    render();
  }
  renderTrash();
}
