'use strict';

// --- state ---
let _state = { branches: [], groups: [] };
let _trash = [];          // deleted commits {sha, short_sha, title, branch}
let _hiddenBySha = {};    // sha → commit data (for overlay)

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

// --- render ---
function render() {
  renderBranches();
}

function renderBranches() {
  const container = document.getElementById('branches');
  const existing = {};
  for (const col of container.querySelectorAll('.branch-col')) {
    existing[col.dataset.branch] = col;
  }

  const rendered = new Set();
  for (const branch of _state.branches) {
    rendered.add(branch.name);
    let col = existing[branch.name];
    if (!col) {
      col = makeBranchColumn(branch.name);
      container.appendChild(col);
    }
    updateBranchColumn(col, branch);
  }

  // remove columns for branches no longer in state
  for (const [name, col] of Object.entries(existing)) {
    if (!rendered.has(name)) col.remove();
  }
}

function makeBranchColumn(branchName) {
  const col = document.createElement('div');
  col.className = 'branch-col';
  col.dataset.branch = branchName;

  const header = document.createElement('div');
  header.className = 'branch-header';
  header.textContent = branchName;
  col.appendChild(header);

  const list = document.createElement('ul');
  list.className = 'commit-list';
  list.dataset.branch = branchName;
  col.appendChild(list);

  Sortable.create(list, {
    group: 'commits',
    animation: 150,
    ghostClass: 'sortable-ghost',
    dragClass: 'sortable-drag',
    onEnd(evt) {
      const sha = evt.item.dataset.sha;
      const fromBranch = evt.from.dataset.branch;
      const toBranch = evt.to.dataset.branch;

      if (toBranch === '__trash__') {
        // delete handled by trash-list onAdd
        return;
      }

      if (fromBranch === toBranch) {
        // reorder within branch
        const newOrder = [...evt.to.querySelectorAll('.commit-card')].map(el => el.dataset.sha);
        postOp({ type: 'reorder', branch: fromBranch, new_order: newOrder });
      } else {
        // cherry-pick to another branch
        postOp({ type: 'cherrypick', sha, target_branch: toBranch });
      }
    }
  });

  return col;
}

function updateBranchColumn(col, branch) {
  const list = col.querySelector('.commit-list');

  // Build hidden runs: consecutive hidden commits get collapsed into a single marker
  const items = [];
  let hiddenRun = [];
  for (const c of branch.commits) {
    if (c.hidden) {
      hiddenRun.push(c);
    } else {
      if (hiddenRun.length > 0) {
        items.push({ type: 'hidden-marker', commits: hiddenRun });
        hiddenRun = [];
      }
      items.push({ type: 'commit', commit: c });
    }
  }
  if (hiddenRun.length > 0) {
    items.push({ type: 'hidden-marker', commits: hiddenRun });
  }

  list.innerHTML = '';
  for (const item of items) {
    if (item.type === 'commit') {
      list.appendChild(makeCommitCard(item.commit));
    } else {
      list.appendChild(makeHiddenMarker(item.commits, branch.name));
    }
  }
}

function makeCommitCard(c) {
  const li = document.createElement('li');
  li.className = 'commit-card' + (c.color_index != null ? ` group-${c.color_index}` : '');
  li.dataset.sha = c.sha;
  li.dataset.branch = c.branch;

  const sha = document.createElement('span');
  sha.className = 'sha';
  sha.textContent = c.short_sha;

  const title = document.createElement('span');
  title.className = 'title' + (c.group_id ? ' has-group' : '');
  title.textContent = c.title;
  title.title = c.author;
  if (c.group_id) {
    title.addEventListener('click', () => openDiffDialog(c));
  }

  const actions = document.createElement('span');
  actions.className = 'actions';

  const hideBtn = document.createElement('button');
  hideBtn.className = 'btn-hide';
  hideBtn.textContent = '−';
  hideBtn.title = 'Hide';
  hideBtn.addEventListener('click', () => postOp({ type: 'hide', sha: c.sha, branch: c.branch }));

  const delBtn = document.createElement('button');
  delBtn.className = 'btn-del';
  delBtn.textContent = '✕';
  delBtn.title = 'Delete';
  delBtn.addEventListener('click', () => {
    _trash.push({ sha: c.sha, short_sha: c.short_sha, title: c.title, branch: c.branch });
    postOp({ type: 'delete', sha: c.sha, branch: c.branch });
  });

  actions.append(hideBtn, delBtn);
  li.append(sha, title, actions);
  return li;
}

function makeHiddenMarker(commits, branchName) {
  const div = document.createElement('div');
  div.className = 'hidden-marker';
  div.dataset.branch = branchName;

  const count = document.createElement('span');
  count.className = 'hidden-count';
  count.textContent = commits.length;
  div.appendChild(count);

  div.addEventListener('click', () => openHiddenDialog(commits, branchName));
  return div;
}

// --- diff overlay ---
async function openDiffDialog(c) {
  const group = _state.groups.find(g => g.id === c.group_id);
  if (!group) return;
  const otherShas = group.commit_shas.filter(s => s !== c.sha);
  if (otherShas.length === 0) return;

  const sha2 = otherShas[0];
  const res = await fetch(`/api/diff/${c.sha}/${sha2}`);
  const { diff } = await res.json();

  document.getElementById('diff-title').textContent =
    `${c.short_sha} vs ${sha2.slice(0, 8)}`;
  document.getElementById('diff-content').innerHTML = colorDiff(diff);
  document.getElementById('diff-dialog').showModal();
}

function colorDiff(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .split('\n').map(line => {
      if (line.startsWith('+')) return `<span class="diff-add">${line}</span>`;
      if (line.startsWith('-')) return `<span class="diff-del">${line}</span>`;
      return `<span class="diff-ctx">${line}</span>`;
    }).join('\n');
}

document.getElementById('diff-close').addEventListener('click', () => {
  document.getElementById('diff-dialog').close();
});

// --- hidden overlay ---
function openHiddenDialog(commits, branchName) {
  document.getElementById('hidden-title').textContent =
    `hidden commits on ${branchName}`;
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

document.getElementById('hidden-close').addEventListener('click', () => {
  document.getElementById('hidden-dialog').close();
});

// --- error overlay ---
function showError(title, output, command) {
  document.getElementById('error-command').textContent = command ? `$ ${command}` : '';
  document.getElementById('error-output').textContent = output;
  document.getElementById('error-dialog').showModal();
}
document.getElementById('error-close').addEventListener('click', () => {
  document.getElementById('error-dialog').close();
});

// --- trash panel ---
const trashList = document.getElementById('trash-list');
Sortable.create(trashList, {
  group: { name: 'commits', pull: true, put: true },
  animation: 150,
  onAdd(evt) {
    // A commit was dragged into the trash
    const sha = evt.item.dataset.sha;
    const branch = evt.item.dataset.branch;
    if (sha && branch) {
      const c = findCommit(sha);
      if (c) _trash.push({ sha: c.sha, short_sha: c.short_sha, title: c.title, branch });
      postOp({ type: 'delete', sha, branch });
    }
  }
});

function renderTrash() {
  trashList.innerHTML = '';
  for (const c of _trash) {
    const li = document.createElement('li');
    li.className = 'commit-card';
    li.dataset.sha = c.sha;
    li.dataset.branch = c.branch;
    const sha = document.createElement('span');
    sha.className = 'sha';
    sha.textContent = c.short_sha;
    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = c.title;
    li.append(sha, title);
    trashList.appendChild(li);
  }
}

function findCommit(sha) {
  for (const b of _state.branches) {
    for (const c of b.commits) {
      if (c.sha === sha) return c;
    }
  }
  return null;
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
    // Re-fetch state to undo any optimistic UI change
    const s = await fetch('/api/state');
    _state = await s.json();
    render();
  }
  renderTrash();
}
