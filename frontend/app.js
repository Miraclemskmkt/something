const BOARDS = {
  summer_camp: {
    id: 'summer_camp',
    label: '夏令营',
    panelId: 'panelSummerCamp',
    resultLabel: '优营名单',
    prefix: 'Summer',
  },
  pre_admission: {
    id: 'pre_admission',
    label: '预推免',
    panelId: 'panelPreAdmission',
    resultLabel: '录取名单',
    prefix: 'Pre',
  },
};

const TIERS = [
  { id: '985', label: '985', css: 'tier-985', btn: '刷新检索985' },
  { id: '211', label: '211', css: 'tier-211', btn: '刷新检索211' },
  { id: '双一流', label: '双一流', css: 'tier-dfc', btn: '刷新检索双一流' },
];

/** 与后端 SOURCE_CATEGORIES 一致 */
const SOURCE_OPTIONS = [
  { id: '学院官网', label: '学院官网', css: 'source-official' },
  { id: '微信公众号', label: '微信公众号', css: 'source-wechat' },
  { id: '保研论坛', label: '保研论坛', css: 'source-forum' },
  { id: '全网检索', label: '全网检索', css: 'source-search' },
  { id: '用户提交', label: '用户提交', css: 'source-submit' },
  { id: '用户补全', label: '用户补全', css: 'source-manual' },
];

const boardState = {
  summer_camp: { tier: '985', status: 'active', collegeType: '', source: '', search: '', data: [], stats: null },
  pre_admission: { tier: '985', status: 'active', collegeType: '', source: '', search: '', data: [], stats: null },
};

const state = {
  panel: 'summer_camp',
  instCollegeType: '',
  instRegion: '',
  instTag: '',
  instSearch: '',
  institutions: null,
  instSubPanel: 'all',
  dfcCollegeType: '',
  dfcRegion: '',
  dfcTag: '',
  dfcSearch: '',
  dfcData: null,
  dfcExpandedOnce: false,
  expandedRegions: new Set(['华北', '华东', '华中']),
  submitColleges: [],
  submitLoading: false,
};

const IS_GITHUB_PAGES = /github\.io$/i.test(window.location.hostname);

const VALID_PANELS = new Set(['summer_camp', 'pre_admission', 'institutions', 'field_enrich']);
const UI_STATE_KEY = 'camp_ui_state';

/** 本地 FastAPI 用 /api；GitHub Pages 仅静态页，无后端 API */
function apiUrl(path) {
  return path.startsWith('/') ? path : `/${path}`;
}

function readUiState() {
  const hash = location.hash.replace(/^#/, '');
  let stored = {};
  try {
    const raw = sessionStorage.getItem(UI_STATE_KEY);
    if (raw) stored = JSON.parse(raw);
  } catch (_) { /* ignore */ }
  const panel = VALID_PANELS.has(hash) ? hash
    : (VALID_PANELS.has(stored.panel) ? stored.panel : 'summer_camp');
  return { ...stored, panel };
}

function saveUiState() {
  try {
    sessionStorage.setItem(UI_STATE_KEY, JSON.stringify({
      panel: state.panel,
      tiers: {
        summer_camp: boardState.summer_camp.tier,
        pre_admission: boardState.pre_admission.tier,
      },
      instSubPanel: state.instSubPanel,
    }));
  } catch (_) { /* ignore */ }
  const hash = `#${state.panel}`;
  if (location.hash !== hash) {
    history.replaceState(null, '', hash);
  }
}

function applyStoredUiState(saved) {
  if (saved.tiers?.summer_camp) boardState.summer_camp.tier = saved.tiers.summer_camp;
  if (saved.tiers?.pre_admission) boardState.pre_admission.tier = saved.tiers.pre_admission;
  if (saved.instSubPanel) state.instSubPanel = saved.instSubPanel;
}

async function fetchJSON(url) {
  if (IS_GITHUB_PAGES) {
    throw new Error('GitHub Pages 为静态展示，数据检索请本地运行 start.bat 或自行部署后端');
  }
  const resp = await fetch(apiUrl(url));
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function collegeLink(name, homepage, ok) {
  if (!homepage) return esc(name);
  const badge = ok === false ? ' <span class="link-warn" title="官网暂不可达">!</span>' : '';
  return `<a href="${esc(homepage)}" target="_blank" rel="noopener" class="college-link">${esc(name)}</a>${badge}`;
}

function statusDot(ok) {
  if (ok === true) return '<span class="status-dot ok" title="官网可达"></span>';
  if (ok === false) return '<span class="status-dot fail" title="官网不可达"></span>';
  return '';
}

const LEVEL_TAGS = ['985', '211', '双一流'];

function tagClass(tag) {
  if (tag === '985') return 'tag-985';
  if (tag === '211') return 'tag-211';
  if (tag === '双一流') return 'tag-dfc';
  return 'tag-other';
}

function renderLevelTags(tags) {
  if (!tags || !tags.length) return '';
  const levels = tags.filter(t => LEVEL_TAGS.includes(t));
  if (!levels.length) return '';
  return `<div class="inst-tags">${levels.map(t =>
    `<span class="inst-tag ${tagClass(t)}">${esc(t)}</span>`
  ).join('')}</div>`;
}

function fmtDateTime(val) {
  if (!val) return '—';
  const s = String(val);
  if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(s)) return s;
  if (/^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$/.test(s)) return `${s}:00`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return `${s} 00:00:00`;
  return s;
}

function fmtEvent(val) {
  return val ? String(val) : '—';
}

function fmtFormat(val) {
  if (!val) return '<span class="format-unknown">—</span>';
  const cls = val === '线上' ? 'format-online' : val === '线下' ? 'format-offline' : 'format-hybrid';
  return `<span class="format-badge ${cls}">${esc(val)}</span>`;
}

function isFieldsComplete(item) {
  return !!(item.publish_date && item.deadline && item.event_time && item.event_format);
}

function deadlineCountdown(deadline) {
  if (!deadline) return '';
  const d = new Date(String(deadline).replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return '';
  const days = Math.ceil((d - Date.now()) / 86400000);
  if (days < 0) return '<span class="countdown ended">已截止</span>';
  if (days === 0) return '<span class="countdown urgent">今日截止</span>';
  if (days <= 3) return `<span class="countdown urgent">距截止 ${days} 天</span>`;
  return `<span class="countdown">距截止 ${days} 天</span>`;
}

function openNoticeUrl(url) {
  if (url) window.open(url, '_blank', 'noopener');
}

function parseDeadlineMs(deadline) {
  if (!deadline) return Infinity;
  const d = new Date(String(deadline).replace(' ', 'T'));
  return Number.isNaN(d.getTime()) ? Infinity : d.getTime();
}

function sourceBadge(item) {
  const label = item.source_category || item.source || '学院官网';
  const opt = SOURCE_OPTIONS.find(o => o.id === label) || SOURCE_OPTIONS[0];
  return `<span class="source-badge ${opt.css}">${esc(label)}</span>`;
}

function renderNoticeCards(items, { sortByDeadline = false } = {}) {
  const rows = [...items];
  if (sortByDeadline) {
    rows.sort((a, b) => parseDeadlineMs(a.deadline) - parseDeadlineMs(b.deadline));
  } else {
    rows.sort((a, b) =>
      `${a.university}${a.college}`.localeCompare(`${b.university}${b.college}`, 'zh-CN')
    );
  }
  return `
    <div class="notice-card-grid">
      ${rows.map(item => {
        const st = item.status || 'active';
        const complete = isFieldsComplete(item);
        return `
        <article class="notice-card status-${esc(st)}${complete ? '' : ' fields-incomplete'}" data-url="${esc(item.url)}" title="${esc(item.title)}">
          <header class="notice-card-head">
            <h3 class="notice-card-school">${esc(item.university)} - ${esc(item.college)}${complete ? '' : ' <span class="field-badge warn">缺字段</span>'}</h3>
            <p class="notice-card-title">${esc(item.title)}</p>
          </header>
          <dl class="notice-card-fields">
            <div class="notice-field">
              <dt>开放提交</dt>
              <dd>${esc(fmtDateTime(item.publish_date))}</dd>
            </div>
            <div class="notice-field">
              <dt>截止提交</dt>
              <dd class="${item.deadline ? '' : 'is-empty'}">${esc(fmtDateTime(item.deadline))} ${deadlineCountdown(item.deadline)}</dd>
            </div>
            <div class="notice-field notice-field-wide">
              <dt>举办时间</dt>
              <dd class="${item.event_time ? '' : 'is-empty'}">${esc(fmtEvent(item.event_time))}</dd>
            </div>
            <div class="notice-field notice-field-format">
              <dt>举办形式</dt>
              <dd>${fmtFormat(item.event_format)}</dd>
            </div>
          </dl>
          <footer class="notice-card-foot">
            ${sourceBadge(item)}
            <span class="link-hint">查看原文 →</span>
          </footer>
        </article>`;
      }).join('')}
    </div>`;
}

function renderPendingCards(items) {
  const rows = [...items].sort((a, b) =>
    `${a.university}${a.college}`.localeCompare(`${b.university}${b.college}`, 'zh-CN')
  );
  return `
    <div class="notice-card-grid">
      ${rows.map(item => `
        <article class="notice-card notice-card-pending">
          <header class="notice-card-head">
            <h3 class="notice-card-school">${esc(item.university)} - ${esc(item.college)}</h3>
          </header>
          <dl class="notice-card-fields">
            <div class="notice-field">
              <dt>学院类型</dt>
              <dd>${item.college_type === 'law' ? '法学' : '外语'}</dd>
            </div>
            <div class="notice-field">
              <dt>最后检索</dt>
              <dd>${item.updated_at ? new Date(item.updated_at).toLocaleDateString('zh-CN') : '—'}</dd>
            </div>
          </dl>
          <footer class="notice-card-foot">
            <span class="pending-hint">暂未发布通知</span>
          </footer>
        </article>
      `).join('')}
    </div>`;
}

function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  setTimeout(() => { el.className = 'toast'; }, 3500);
}

function updateCrawlButton(boardId) {
  const bs = boardState[boardId];
  const p = BOARDS[boardId].prefix;
  const tierCfg = TIERS.find(t => t.id === bs.tier) || TIERS[0];
  const btn = document.getElementById(`crawlBtn${p}`);
  if (btn) {
    btn.innerHTML = `<span class="btn-icon crawl-icon" id="crawlIcon${p}">↻</span> ${tierCfg.btn}`;
  }
}

function initBoardUI(boardId) {
  const cfg = BOARDS[boardId];
  const p = cfg.prefix;

  document.getElementById(`tierBar${p}`).innerHTML = `
    <div class="tier-tabs">
      ${TIERS.map(t => `
        <button type="button" class="tier-tab ${t.css}${boardState[boardId].tier === t.id ? ' active' : ''}"
          data-board="${boardId}" data-tier="${t.id}">${t.label}</button>
      `).join('')}
    </div>`;
  updateCrawlButton(boardId);

  document.getElementById(`stats${p}`).innerHTML = `
    <div class="stat-card"><div class="stat-num" id="statTotal${p}">-</div><div class="stat-label">全部</div></div>
    <div class="stat-card active"><div class="stat-num" id="statActive${p}">-</div><div class="stat-label">进行中</div></div>
    <div class="stat-card ended"><div class="stat-num" id="statEnded${p}">-</div><div class="stat-label">已结束</div></div>
    <div class="stat-card excellent"><div class="stat-num" id="statExcellent${p}">-</div><div class="stat-label">${cfg.resultLabel}</div></div>
    <div class="stat-card pending"><div class="stat-num" id="statPending${p}">-</div><div class="stat-label">待定</div></div>
  `;

  document.getElementById(`filters${p}`).innerHTML = `
    <div class="filter-group">
      <label>学院类型</label>
      <div class="chip-group" data-board="${boardId}" data-filter="college">
        <button class="chip active" data-value="">全部</button>
        <button class="chip" data-value="law">法学院</button>
        <button class="chip" data-value="foreign_lang">外国语学院</button>
      </div>
    </div>
    <div class="filter-group">
      <label>数据来源</label>
      <div class="chip-group chip-group-source" data-board="${boardId}" data-filter="source">
        <button class="chip active" data-value="">全部</button>
        ${SOURCE_OPTIONS.map(o => `<button class="chip" data-value="${o.id}">${o.label}</button>`).join('')}
      </div>
    </div>
    <div class="search-box">
      <input type="text" data-board="${boardId}" data-filter="search" placeholder="搜索学校、学院、标题..." />
    </div>
  `;

  document.getElementById(`tabs${p}`).innerHTML = `
    <button class="tab" data-board="${boardId}" data-status="all">全部 <span class="badge" id="badgeAll${p}">0</span></button>
    <button class="tab active" data-board="${boardId}" data-status="active">进行中 <span class="badge" id="badgeActive${p}">0</span></button>
    <button class="tab" data-board="${boardId}" data-status="ended">已结束 <span class="badge" id="badgeEnded${p}">0</span></button>
    <button class="tab" data-board="${boardId}" data-status="excellent_list">${cfg.resultLabel} <span class="badge excellent" id="badgeExcellent${p}">0</span></button>
    <button class="tab" data-board="${boardId}" data-status="pending">待定 <span class="badge pending" id="badgePending${p}">0</span></button>
  `;
}

async function loadBoard(boardId) {
  const cfg = BOARDS[boardId];
  const bs = boardState[boardId];
  const p = cfg.prefix;
  const params = new URLSearchParams({ board: boardId, tier: bs.tier });
  if (bs.status && bs.status !== 'all') params.set('status', bs.status);
  if (bs.collegeType) params.set('college_type', bs.collegeType);
  if (bs.source) params.set('source', bs.source);
  if (bs.search) params.set('search', bs.search);

  const loadingEl = document.getElementById(`loading${p}`);
  const hasData = bs.stats && bs.data.length;
  if (!hasData && loadingEl) loadingEl.style.display = 'block';
  try {
    const payload = await fetchJSON(`/api/board?${params}`);
    bs.stats = payload.stats;
    bs.data = payload.items;
    renderBoard(boardId);
    loadOpsAlerts();
  } finally {
    if (loadingEl) loadingEl.style.display = 'none';
  }
}

function renderBoard(boardId) {
  const cfg = BOARDS[boardId];
  const bs = boardState[boardId];
  const p = cfg.prefix;

  if (bs.stats) {
    document.getElementById(`statTotal${p}`).textContent = bs.stats.total;
    document.getElementById(`statActive${p}`).textContent = bs.stats.active;
    document.getElementById(`statEnded${p}`).textContent = bs.stats.ended;
    document.getElementById(`statExcellent${p}`).textContent = bs.stats.excellent_list;
    document.getElementById(`statPending${p}`).textContent = bs.stats.pending ?? 0;
    document.getElementById(`badgeAll${p}`).textContent = bs.stats.total;
    document.getElementById(`badgeActive${p}`).textContent = bs.stats.active;
    document.getElementById(`badgeEnded${p}`).textContent = bs.stats.ended;
    document.getElementById(`badgeExcellent${p}`).textContent = bs.stats.excellent_list;
    document.getElementById(`badgePending${p}`).textContent = bs.stats.pending ?? 0;
    const lu = document.getElementById(`lastUpdate${p}`);
    lu.textContent = bs.stats.last_crawl
      ? `[${bs.tier}] 上次更新：${new Date(bs.stats.last_crawl).toLocaleString('zh-CN')}`
      : `[${bs.tier}] 尚未检索`;
    const srcEl = document.getElementById(`sourceStats${p}`);
    if (srcEl && bs.stats.source_counts) {
      const parts = SOURCE_OPTIONS
        .map(o => ({ ...o, n: bs.stats.source_counts[o.id] || 0 }))
        .filter(o => o.n > 0)
        .map(o => `<span class="source-stat ${o.css}">${o.label} ${o.n}</span>`);
      srcEl.innerHTML = parts.length ? parts.join('') : '';
    }
  }

  const list = document.getElementById(`list${p}`);
  const empty = document.getElementById(`empty${p}`);
  if (!bs.data.length) {
    list.innerHTML = '';
    empty.style.display = 'block';
    const emptyEl = document.getElementById(`empty${p}`);
    emptyEl.querySelector('p').textContent = bs.status === 'pending'
      ? '暂无待定学院（均已检索到通知或未开始检索）'
      : `暂无${cfg.label}通知`;
    return;
  }
  empty.style.display = 'none';
  const isPending = bs.status === 'pending';
  const sortByDeadline = boardId === 'summer_camp' && bs.status === 'active';
  list.innerHTML = isPending ? renderPendingCards(bs.data) : renderNoticeCards(bs.data, { sortByDeadline });
  list.querySelectorAll('.notice-card[data-url]').forEach(card => {
    card.addEventListener('click', () => openNoticeUrl(card.dataset.url));
  });
}

const crawlPollTimers = {
  summer_camp: null,
  pre_admission: null,
};

function clearCrawlPoll(boardId) {
  if (crawlPollTimers[boardId]) {
    clearTimeout(crawlPollTimers[boardId]);
    crawlPollTimers[boardId] = null;
  }
}

async function pollCrawlStatus(boardId) {
  const cfg = BOARDS[boardId];
  const p = cfg.prefix;
  const tier = boardState[boardId].tier;
  const btn = document.getElementById(`crawlBtn${p}`);
  const icon = document.getElementById(`crawlIcon${p}`);

  try {
    const st = await fetchJSON(
      `/api/crawl/status?board=${boardId}&tier=${encodeURIComponent(tier)}`,
    );
    if (st.running) {
      const lu = document.getElementById(`lastUpdate${p}`);
      if (lu && st.message) lu.textContent = st.message;
      await loadBoard(boardId);
      crawlPollTimers[boardId] = setTimeout(() => pollCrawlStatus(boardId), 1500);
      return;
    }
    clearCrawlPoll(boardId);
    await loadBoard(boardId);
    if (st.last_result && st.last_result.message) {
      showToast(st.last_result.message, 'success');
    } else if (st.message) {
      showToast(st.message, 'success');
    }
    if (btn) btn.disabled = false;
    if (icon) icon.classList.remove('spinning');
  } catch {
    clearCrawlPoll(boardId);
    if (btn) btn.disabled = false;
    if (icon) icon.classList.remove('spinning');
  }
}

async function triggerCrawl(boardId) {
  const cfg = BOARDS[boardId];
  const p = cfg.prefix;
  const tier = boardState[boardId].tier;
  const btn = document.getElementById(`crawlBtn${p}`);
  const icon = document.getElementById(`crawlIcon${p}`);
  clearCrawlPoll(boardId);
  btn.disabled = true;
  if (icon) icon.classList.add('spinning');
  showToast(`正在刷新${cfg.label} · ${tier}（全网检索 + 详情补全）…`);

  try {
    const resp = await fetch(
      apiUrl(
        `/api/crawl?board=${boardId}&tier=${encodeURIComponent(tier)}&refresh=true`,
      ),
      { method: 'POST' },
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.message || '失败');
    }
    showToast(data.message, 'success');
    const lu = document.getElementById(`lastUpdate${p}`);
    if (lu) lu.textContent = data.message;
    crawlPollTimers[boardId] = setTimeout(() => pollCrawlStatus(boardId), 1200);
  } catch (e) {
    showToast(e.message === '失败' ? '更新失败' : e.message, 'error');
    btn.disabled = false;
    if (icon) icon.classList.remove('spinning');
  }
}

function switchPanel(panel, { skipHash = false } = {}) {
  if (!VALID_PANELS.has(panel)) panel = 'summer_camp';
  state.panel = panel;
  document.querySelectorAll('#mainTabs .main-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.panel === panel);
  });
  document.getElementById('panelSummerCamp').style.display = panel === 'summer_camp' ? 'block' : 'none';
  document.getElementById('panelPreAdmission').style.display = panel === 'pre_admission' ? 'block' : 'none';
  document.getElementById('panelInstitutions').style.display = panel === 'institutions' ? 'block' : 'none';
  const enrichEl = document.getElementById('panelFieldEnrich');
  if (enrichEl) enrichEl.style.display = panel === 'field_enrich' ? 'block' : 'none';
  const submitPanel = document.getElementById('submitNoticePanel');
  if (submitPanel) {
    submitPanel.style.display = (panel === 'summer_camp' || panel === 'pre_admission') ? 'block' : 'none';
  }
  if (panel === 'field_enrich') {
    loadIncompleteList();
    loadMissingExtendedList();
    loadOpsAlerts();
  } else if (panel === 'institutions') {
    switchInstPanel(state.instSubPanel, { skipSave: true });
    if (!state.institutions) loadInstitutions();
  } else if (panel === 'summer_camp' || panel === 'pre_admission') {
    loadBoard(panel).catch(() => {
      showToast('加载失败，请确认服务已启动', 'error');
    });
  }
  if (!skipHash) saveUiState();
}

function setupBoardFilters() {
  document.querySelectorAll('.chip-group[data-filter]').forEach(group => {
    group.querySelectorAll('.chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const boardId = group.dataset.board;
        const filter = group.dataset.filter;
        group.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        boardState[boardId][filter === 'college' ? 'collegeType' : 'source'] = chip.dataset.value;
        loadBoard(boardId);
      });
    });
  });

  document.querySelectorAll('input[data-filter="search"]').forEach(input => {
    let timer;
    input.addEventListener('input', e => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        boardState[input.dataset.board].search = e.target.value.trim();
        loadBoard(input.dataset.board);
      }, 400);
    });
  });

  document.querySelectorAll('.status-tabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const boardId = tab.dataset.board;
      tab.parentElement.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      boardState[boardId].status = tab.dataset.status;
      loadBoard(boardId);
    });
  });
}

/* ---- 院校名录（保留原有逻辑） ---- */
function switchInstPanel(panel, { skipSave = false } = {}) {
  state.instSubPanel = panel;
  document.querySelectorAll('.inst-panel-tabs .chip').forEach(c => {
    c.classList.toggle('active', c.dataset.instPanel === panel);
  });
  document.getElementById('instPanelAll').style.display = panel === 'all' ? 'block' : 'none';
  document.getElementById('instPanelDfc').style.display = panel === 'dfc' ? 'block' : 'none';
  if (panel === 'dfc') loadDfc();
  if (!skipSave) saveUiState();
}

async function loadDfc() {
  const params = new URLSearchParams();
  if (state.dfcCollegeType) params.set('college_type', state.dfcCollegeType);
  if (state.dfcRegion) params.set('region', state.dfcRegion);
  if (state.dfcTag) params.set('tag', state.dfcTag);
  if (state.dfcSearch) params.set('search', state.dfcSearch);
  const list = document.getElementById('dfcList');
  list.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>加载双一流院校...</p></div>';
  try {
    state.dfcData = await fetchJSON(`/api/double-first-class?${params}`);
    if (!state.dfcExpandedOnce) {
      state.dfcData.regions.forEach(r => state.expandedRegions.add(r.region));
      state.dfcExpandedOnce = true;
    }
    renderDfc();
  } catch (e) {
    list.innerHTML = '<p class="empty-state">加载失败，请刷新页面或重启服务后重试</p>';
    showToast('双一流名录加载失败，请确认服务已更新', 'error');
  }
}

function renderDfc() {
  const data = state.dfcData;
  if (!data) return;
  const s = data.summary;
  document.getElementById('dfcSummary').innerHTML = `
    <div class="inst-summary-item"><div class="inst-summary-num">${s.filtered_total ?? s.total}</div><div class="inst-summary-label">${s.filtered_total != null ? '筛选结果' : '双一流'}</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${s.tag_985 || 0}</div><div class="inst-summary-label">985</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${s.tag_211 || 0}</div><div class="inst-summary-label">211</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${s.tag_dfc || s.total}</div><div class="inst-summary-label">双一流</div></div>
  `;
  const list = document.getElementById('dfcList');
  if (!data.regions.length) { list.innerHTML = '<p class="empty-state">无匹配</p>'; return; }
  list.innerHTML = data.regions.map(r => `
    <div class="region-block">
      <div class="region-header" onclick="toggleDfcRegion('${esc(r.region)}')">
        <h3>${esc(r.region)}</h3><span class="region-count">${r.count} 所</span>
      </div>
      <div class="region-body" style="display:${state.expandedRegions.has(r.region) ? 'block' : 'none'}">
        ${r.provinces.map(prov => `
          <div class="province-block">
            <div class="province-title">${esc(prov.province)}（${prov.count}）</div>
            ${prov.universities.map(u => `
              <div class="dfc-uni-card">
                <div class="dfc-uni-name">
                  <a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.name)}</a>
                  ${renderLevelTags(u.tags)}
                </div>
                <div class="dfc-colleges">${(u.colleges || []).map(c => `
                  <span class="dfc-college-item">
                    ${statusDot(c.homepage_ok)}
                    ${collegeLink(c.college, c.homepage, c.homepage_ok)}
                    <span class="college-type-tag">${c.college_type === 'law' ? '法' : '外'}</span>
                  </span>`).join('')}
                </div>
              </div>`).join('')}
          </div>`).join('')}
      </div>
    </div>`).join('');
}

function toggleDfcRegion(region) {
  state.expandedRegions.has(region) ? state.expandedRegions.delete(region) : state.expandedRegions.add(region);
  renderDfc();
}

async function loadInstitutions() {
  const params = new URLSearchParams();
  if (state.instCollegeType) params.set('college_type', state.instCollegeType);
  if (state.instRegion) params.set('region', state.instRegion);
  if (state.instTag) params.set('tag', state.instTag);
  if (state.instSearch) params.set('search', state.instSearch);
  document.getElementById('instLoading').style.display = 'block';
  try {
    state.institutions = await fetchJSON(`/api/institutions?${params}`);
    renderInstitutions();
  } finally {
    document.getElementById('instLoading').style.display = 'none';
  }
}

function renderInstitutions() {
  const data = state.institutions;
  if (!data) return;
  document.getElementById('instSummary').innerHTML = `
    <div class="inst-summary-item"><div class="inst-summary-num">${data.summary.filtered_total ?? data.summary.total}</div><div class="inst-summary-label">${data.summary.filtered_total != null ? '筛选结果' : '学院条目'}</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${data.summary.tag_985 || 0}</div><div class="inst-summary-label">985</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${data.summary.tag_211 || 0}</div><div class="inst-summary-label">211</div></div>
    <div class="inst-summary-item"><div class="inst-summary-num">${data.summary.tag_dfc || 0}</div><div class="inst-summary-label">双一流</div></div>`;
  const list = document.getElementById('instList');
  if (!data.regions.length) { list.innerHTML = '<p class="empty-state">无匹配</p>'; return; }
  list.innerHTML = data.regions.map(region => `
    <div class="region-block">
      <div class="region-header" onclick="toggleRegion('${esc(region.region)}')">
        <h3>${esc(region.region)}</h3><span class="region-count">${region.count} 个</span>
      </div>
      <div class="region-body" style="display:${state.expandedRegions.has(region.region) ? 'block' : 'none'}">
        ${region.provinces.map(prov => `
          <div class="province-block">
            <div class="province-title">${esc(prov.province)}（${prov.count}）</div>
            <div class="inst-grid">${prov.institutions.map(i => `
              <div class="inst-card${i.homepage ? ' has-link' : ''}">
                <div class="inst-name">${esc(i.university)}</div>
                ${renderLevelTags(i.tags)}
                <div class="inst-college">${collegeLink(i.college, i.homepage, i.homepage_ok)}</div>
                ${i.note ? `<div class="inst-note">${esc(i.note)}</div>` : ''}
              </div>`).join('')}
            </div>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

function toggleRegion(region) {
  state.expandedRegions.has(region) ? state.expandedRegions.delete(region) : state.expandedRegions.add(region);
  renderInstitutions();
}

function setupFilters() {
  document.querySelectorAll('#mainTabs .main-tab').forEach(tab => {
    tab.addEventListener('click', () => switchPanel(tab.dataset.panel));
  });
  document.querySelectorAll('.inst-panel-tabs .chip').forEach(chip => {
    chip.addEventListener('click', () => switchInstPanel(chip.dataset.instPanel));
  });
  ['instCollegeFilter', 'instRegionFilter', 'instTagFilter'].forEach(id => {
    document.querySelectorAll(`#${id} .chip`).forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll(`#${id} .chip`).forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        const key = id.includes('College') ? 'instCollegeType'
          : id.includes('Region') ? 'instRegion' : 'instTag';
        state[key] = chip.dataset.value;
        loadInstitutions();
      });
    });
  });
  ['dfcCollegeFilter', 'dfcRegionFilter', 'dfcTagFilter'].forEach(id => {
    document.querySelectorAll(`#${id} .chip`).forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll(`#${id} .chip`).forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        const key = id.includes('College') ? 'dfcCollegeType'
          : id.includes('Region') ? 'dfcRegion' : 'dfcTag';
        state[key] = chip.dataset.value;
        loadDfc();
      });
    });
  });
  let t1, t2;
  document.getElementById('instSearchInput').addEventListener('input', e => {
    clearTimeout(t1); t1 = setTimeout(() => { state.instSearch = e.target.value.trim(); loadInstitutions(); }, 400);
  });
  document.getElementById('dfcSearchInput').addEventListener('input', e => {
    clearTimeout(t2); t2 = setTimeout(() => { state.dfcSearch = e.target.value.trim(); loadDfc(); }, 400);
  });
  setupBoardFilters();
  setupTierTabs();
}

function setupTierTabs() {
  document.querySelectorAll('.tier-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const boardId = tab.dataset.board;
      tab.parentElement.querySelectorAll('.tier-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      boardState[boardId].tier = tab.dataset.tier;
      updateCrawlButton(boardId);
      loadBoard(boardId);
      saveUiState();
    });
  });
}

window.toggleRegion = toggleRegion;
window.toggleDfcRegion = toggleDfcRegion;
window.triggerCrawl = triggerCrawl;

async function loadSubmitColleges() {
  if (state.submitColleges.length) return;
  try {
    state.submitColleges = await fetchJSON('/api/submit/colleges');
    const sel = document.getElementById('submitCollege');
    if (!sel) return;
    sel.innerHTML = '<option value="">请选择学院…</option>' +
      state.submitColleges.map((c, i) =>
        `<option value="${i}">${esc(c.label)}</option>`
      ).join('');
  } catch {
    showToast('学院列表加载失败', 'error');
  }
}

function showSubmitResult(html, type = '') {
  const el = document.getElementById('submitResult');
  if (!el) return;
  el.innerHTML = html;
  el.className = `submit-result show ${type}`;
  el.style.display = 'block';
}

async function handleSubmitNotice(e) {
  e.preventDefault();
  if (state.submitLoading) return;

  const sel = document.getElementById('submitCollege');
  const urlInput = document.getElementById('submitUrl');
  const btn = document.getElementById('submitNoticeBtn');
  const idx = sel.value;
  if (idx === '' || !state.submitColleges[idx]) {
    showToast('请选择学院并填写链接', 'error');
    return;
  }
  const { university, college } = state.submitColleges[idx];
  const board = state.panel === 'pre_admission' ? 'pre_admission' : 'summer_camp';

  state.submitLoading = true;
  btn.disabled = true;
  btn.textContent = '校验中…';
  showSubmitResult('<div class="submit-progress"><div class="spinner"></div>正在抓取页面并提取信息…</div>');

  try {
    const resp = await fetch(apiUrl('/api/submit-notice'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: urlInput.value.trim(),
        university,
        college,
        board,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = data.detail;
      const msg = (typeof detail === 'object' && detail?.message) ? detail.message
        : (typeof detail === 'string' ? detail : '提交失败');
      showSubmitResult(`<p class="submit-error">${esc(msg)}</p>`, 'error');
      showToast(msg, 'error');
      return;
    }

    const a = data.announcement;
    showSubmitResult(`
      <p class="submit-success">${esc(data.message)}</p>
      ${a ? `<dl class="submit-preview">
        <div><dt>标题</dt><dd>${esc(a.title)}</dd></div>
        <div><dt>开放提交</dt><dd>${esc(fmtDateTime(a.publish_date))}</dd></div>
        <div><dt>截止提交</dt><dd>${esc(fmtDateTime(a.deadline))}</dd></div>
        <div><dt>举办时间</dt><dd>${esc(fmtEvent(a.event_time))}</dd></div>
        <div><dt>举办形式</dt><dd>${fmtFormat(a.event_format)}</dd></div>
      </dl>` : ''}
    `, 'success');
    showToast(data.message, 'success');
    urlInput.value = '';
    if (state.panel === 'summer_camp' || state.panel === 'pre_admission') {
      await loadBoard(state.panel);
    }
  } catch {
    showSubmitResult('<p class="submit-error">网络错误，请确认服务已启动后重试</p>', 'error');
    showToast('提交失败', 'error');
  } finally {
    state.submitLoading = false;
    btn.disabled = false;
    btn.textContent = '校验并收录';
  }
}

function setupSubmitForm() {
  const form = document.getElementById('submitNoticeForm');
  if (form) form.addEventListener('submit', handleSubmitNotice);
  const sel = document.getElementById('submitCollege');
  if (sel) sel.addEventListener('focus', () => { loadSubmitColleges(); }, { once: true });
  const reloadBtn = document.getElementById('enrichReloadBtn');
  if (reloadBtn) reloadBtn.addEventListener('click', () => {
    loadIncompleteList();
    loadMissingExtendedList();
    loadOpsAlerts();
  });
  const llmBtn = document.getElementById('llmBatchBtn');
  if (llmBtn) llmBtn.addEventListener('click', () => runLlmBatch());
}

async function loadOpsAlerts() {
  const el = document.getElementById('opsAlerts');
  if (!el || IS_GITHUB_PAGES) return;
  try {
    const data = await fetchJSON('/api/ops-health');
    const alerts = data.alerts || [];
    if (!alerts.length) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'flex';
    el.innerHTML = alerts.map(a =>
      `<div class="ops-alert" role="alert">${esc(a.message)}</div>`
    ).join('');
  } catch {
    el.style.display = 'none';
  }
}

async function loadMissingExtendedList() {
  const list = document.getElementById('extendedList');
  const summary = document.getElementById('extendedSummary');
  if (!list) return;
  try {
    const rows = await fetchJSON('/api/missing-extended?limit=20');
    if (summary) {
      summary.textContent = rows.length ? `待处理 ${rows.length} 条` : '';
    }
    if (!rows.length) {
      list.innerHTML = '<p class="enrich-empty">暂无待扩展补全记录</p>';
      return;
    }
    list.innerHTML = rows.map(r => `
      <article class="enrich-card" data-id="${r.id}">
        <header>
          <strong>${esc(r.university)} · ${esc(r.college)}</strong>
          <span><a href="${esc(r.url)}" target="_blank" rel="noopener">原文 ↗</a></span>
        </header>
        <p class="enrich-title">${esc(r.title)}</p>
        <p class="enrich-missing">id=${r.id} · 正文缓存 ${r.summary_len} 字 · ${esc(r.source)}</p>
        <form class="extended-form" data-id="${r.id}">
          <label>通知 URL（可选更新）
            <input name="url" value="${esc(r.url)}" style="width:100%;margin-top:4px" />
          </label>
          <label>粘贴正文（反爬页必填）
            <textarea name="summary" class="enrich-body-input" placeholder="从浏览器复制通知全文粘贴于此…"></textarea>
          </label>
          <div class="enrich-form-actions">
            <button type="submit" class="btn btn-sm">保存正文</button>
            <button type="button" class="btn btn-primary btn-sm ext-enrich-btn" data-id="${r.id}">扩展补全</button>
          </div>
        </form>
      </article>
    `).join('');
    list.querySelectorAll('.extended-form').forEach(form => {
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const id = form.dataset.id;
        const fd = new FormData(form);
        const body = { summary: fd.get('summary'), url: fd.get('url') || undefined };
        const resp = await fetch(apiUrl(`/api/announcements/${id}/fields`), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          showToast('正文已保存', 'success');
          loadMissingExtendedList();
        } else showToast('保存失败', 'error');
      });
    });
    list.querySelectorAll('.ext-enrich-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const resp = await fetch(apiUrl(`/api/announcements/${btn.dataset.id}/extended-enrich`), { method: 'POST' });
          const data = await resp.json().catch(() => ({}));
          showToast(data.message || (resp.ok ? '完成' : '失败'), resp.ok ? 'success' : 'error');
          loadMissingExtendedList();
          loadOpsAlerts();
        } finally { btn.disabled = false; }
      });
    });
  } catch (e) {
    list.innerHTML = `<p class="enrich-empty">${esc(e.message)}</p>`;
  }
}

async function loadIncompleteList() {
  const list = document.getElementById('enrichList');
  const loading = document.getElementById('enrichLoading');
  const summary = document.getElementById('enrichSummary');
  if (!list) return;
  loading.style.display = 'block';
  list.innerHTML = '';
  try {
    const rows = await fetchJSON('/api/incomplete');
    const manualCount = rows.filter(r => r.needs_manual).length;
    if (summary) {
      summary.textContent = rows.length
        ? `待补全 ${rows.length} 条${manualCount ? `（其中 ${manualCount} 条需人工）` : ''}`
        : '全部四字段已齐全';
      summary.className = 'enrich-summary';
    }
    if (!rows.length) {
      list.innerHTML = '<p class="enrich-empty">暂无字段不全的通知 🎉</p>';
      return;
    }
    list.innerHTML = rows.map(r => {
      const manual = r.needs_manual;
      const failHint = r.last_llm_failure ? `（LLM: ${r.last_llm_failure}×${r.llm_fail_count}）` : '';
      return `
      <article class="enrich-card${manual ? ' needs-manual' : ''}" data-id="${r.id}">
        <header>
          <strong>${esc(r.university)} · ${esc(r.college)}</strong>
          <span>
            ${manual ? '<span class="enrich-badge">需人工</span> ' : ''}
            <a href="${esc(r.url)}" target="_blank" rel="noopener">原文 ↗</a>
          </span>
        </header>
        <p class="enrich-title">${esc(r.title)}</p>
        <p class="enrich-missing">缺：${esc(r.missing.join('、'))}${failHint ? ' ' + esc(failHint) : ''}</p>
        <form class="enrich-form" data-id="${r.id}">
          <label>开放 <input name="publish_date" value="${esc(r.publish_date || '')}" placeholder="2026-07-01" /></label>
          <label>截止 <input name="deadline" value="${esc(r.deadline || '')}" placeholder="2026-07-15 23:59" /></label>
          <label>举办 <input name="event_time" value="${esc(r.event_time || '')}" placeholder="8月5日至7日" /></label>
          <label>形式 <select name="event_format">
            <option value="">—</option>
            <option value="线上" ${r.event_format === '线上' ? 'selected' : ''}>线上</option>
            <option value="线下" ${r.event_format === '线下' ? 'selected' : ''}>线下</option>
            <option value="线上线下" ${r.event_format === '线上线下' ? 'selected' : ''}>线上线下</option>
          </select></label>
          <div class="enrich-form-actions">
            <button type="submit" class="btn btn-primary btn-sm">保存</button>
            ${manual ? '' : '<button type="button" class="btn btn-sm llm-one-btn" data-id="' + r.id + '">LLM 试一次</button>'}
          </div>
        </form>
      </article>`;
    }).join('');
    list.querySelectorAll('.enrich-form').forEach(form => {
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const id = form.dataset.id;
        const fd = new FormData(form);
        const body = Object.fromEntries(fd.entries());
        const resp = await fetch(apiUrl(`/api/announcements/${id}/fields`), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          showToast('已保存并标记用户补全保护', 'success');
          loadIncompleteList();
        } else showToast('保存失败', 'error');
      });
    });
    list.querySelectorAll('.llm-one-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const resp = await fetch(apiUrl(`/api/announcements/${btn.dataset.id}/llm-enrich`), { method: 'POST' });
          const data = await resp.json().catch(() => ({}));
          if (resp.ok) {
            showToast(data.message || 'LLM 完成', 'success');
            loadIncompleteList();
          } else showToast(data.detail || 'LLM 失败（请检查 API 配置）', 'error');
        } finally { btn.disabled = false; }
      });
    });
  } catch (e) {
    list.innerHTML = `<p class="enrich-empty">${esc(e.message)}</p>`;
  } finally {
    loading.style.display = 'none';
  }
}

async function runLlmBatch() {
  const btn = document.getElementById('llmBatchBtn');
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch(apiUrl('/api/announcements/llm-enrich-batch?limit=30'), { method: 'POST' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      showToast(`已处理 ${data.processed} 条，${data.fields_complete} 条四字段齐全`, 'success');
      loadIncompleteList();
    } else showToast(typeof data.detail === 'string' ? data.detail : 'LLM 未启用', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (IS_GITHUB_PAGES) {
    const banner = document.getElementById('pagesBanner');
    if (banner) banner.style.display = 'block';
  }
  const saved = readUiState();
  applyStoredUiState(saved);
  initBoardUI('summer_camp');
  initBoardUI('pre_admission');
  setupFilters();
  setupSubmitForm();
  switchPanel(saved.panel, { skipHash: true });
  saveUiState();
  loadOpsAlerts();
  window.addEventListener('hashchange', () => {
    const p = location.hash.replace(/^#/, '');
    if (VALID_PANELS.has(p) && p !== state.panel) {
      switchPanel(p, { skipHash: true });
      saveUiState();
    }
  });
  setInterval(() => {
    if (state.panel === 'summer_camp') loadBoard('summer_camp');
    else if (state.panel === 'pre_admission') loadBoard('pre_admission');
  }, 5 * 60 * 1000);
});
