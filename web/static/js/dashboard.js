'use strict';

// ── 상수 ──────────────────────────────────────────────────────────────────────

const TIER_COLORS = {
    '화이트': '#9E9E9E',
    '블루':   '#2196F3',
    '그린':   '#4CAF50',
    '퍼플':   '#9C27B0',
    '오렌지': '#FF9800',
    '블랙':   '#757575',
};

const API = {
    status:           '/api/status',
    pnl:              '/api/pnl/today',
    portfolio:        '/api/portfolio',
    marketPrices:     '/api/market/prices',
    investment:       '/api/investment',
    cumulative:       '/api/pnl/cumulative',
    assets:           '/api/assets/exchange',
    manualPrice:      '/api/assets/manual-price',
    strategy:         '/api/strategy',
    strategySettings: '/api/strategy/settings',
    tradeFills:       '/api/trades/fills',
    tradePnl:         '/api/trades/pnl',
    deleteOrders:     '/api/trades/orders',
    start:            '/api/bot/start',
    stop:             '/api/bot/stop',
    botGap:           '/api/bot/gap',
};

const COIN_META = {
    BTC:   { name: '비트코인',  letter: 'BTC',  css: 'cs-coin-BTC'   },
    ETH:   { name: '이더리움',  letter: 'ETH',  css: 'cs-coin-ETH'   },
    XRP:   { name: '엑스알피', letter: 'XRP',  css: 'cs-coin-XRP'   },
    USDT:  { name: '테더',      letter: 'USDT', css: 'cs-coin-USDT'  },
};

const MARKET_COINS = ['BTC', 'ETH', 'USDT', 'XRP'];

const POLL = { realtime: 4000, status: 5000, pnl: 15000, assets: 20000, strategy: 3000 };

// ── 상태 ──────────────────────────────────────────────────────────────────────

let _modal         = null;
let _gapModal      = null;
let _gapSymbol     = null;
let _pendingAction = null;
let _pendingSymbol = null;
let _activePage    = 'home';
let _activeTab     = 'realtime';
let _timer         = null;
const BOT_SYMBOLS  = [];

let _stratData      = null;
let _stratActiveSym = null;
let _stratTableOpen = false;
let _stratModeUserSet   = false;
let _stratInputsUserSet = false;

let _tradesActiveSubTab = 'fills';
let _tradesStart        = '';
let _tradesEnd          = '';
let _tradesSym          = 'all';
let _tradesQuickRange   = 'day';
let _tradesDeleteMode   = false;
let _selectedOrderIds   = new Set();
let _pendingDeleteFn    = null;

// ── 초기화 ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // 심볼 목록 읽기
    const symEl = document.getElementById('cs-symbols');
    if (symEl) {
        symEl.dataset.symbols.split(',').filter(Boolean).forEach(s => BOT_SYMBOLS.push(s.trim()));
    }

    _modal = new bootstrap.Modal(document.getElementById('confirmModal'));
    document.getElementById('confirmBtn').addEventListener('click', _executeAction);

    const gapModalEl = document.getElementById('gapModal');
    if (gapModalEl) {
        _gapModal = new bootstrap.Modal(gapModalEl);
        document.getElementById('gapConfirmBtn').addEventListener('click', _executeGapAction);
    }

    // 이벤트 위임 (시작/중지 버튼)
    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-action][data-symbol]');
        if (btn) _openConfirm(btn.dataset.symbol, btn.dataset.action);
    });

    // 투자현황 정렬
    const invSort = document.getElementById('inv-sort');
    if (invSort) invSort.addEventListener('change', _reorderInvCards);

    // 거래소자산 새로고침 버튼
    const refreshBtn = document.getElementById('assets-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => _fetchAssets());

    // 수동 평균매수가 편집 버튼 (이벤트 위임)
    document.addEventListener('click', e => {
        const editBtn   = e.target.closest('.cs-edit-price-btn');
        const saveBtn   = e.target.closest('.cs-save-price-btn');
        const cancelBtn = e.target.closest('.cs-cancel-price-btn');
        if (editBtn)   _openPriceEdit(editBtn.dataset.symbol);
        if (saveBtn)   _savePriceEdit(saveBtn.dataset.symbol);
        if (cancelBtn) _closePriceEdit(cancelBtn.dataset.symbol);
    });

    // 전략현황: 심볼탭 / 체결내역탭 클릭 (이벤트 위임)
    document.addEventListener('click', e => {
        const symBtn = e.target.closest('#strat-sym-tabs .cs-strat-tab');
        if (symBtn) {
            _stratModeUserSet   = false;
            _stratInputsUserSet = false;
            _stratActiveSym = symBtn.dataset.sym;
            document.querySelectorAll('#strat-sym-tabs .cs-strat-tab').forEach(b =>
                b.classList.toggle('active', b.dataset.sym === _stratActiveSym));
            const symData = _stratData?.symbols?.find(s => s.symbol === _stratActiveSym);
            if (symData) _renderStratSettings(symData);
            if (_stratTableOpen) _calcAndRenderPriceTable();
            return;
        }
    });

    // 전략현황: 트리거 방식 토글
    document.addEventListener('click', e => {
        const modeBtn = e.target.closest('#strat-mode-toggle .cs-mode-btn');
        if (!modeBtn) return;
        _stratModeUserSet = true;
        const mode = modeBtn.dataset.mode;
        document.querySelectorAll('#strat-mode-toggle .cs-mode-btn').forEach(b =>
            b.classList.toggle('active', b.dataset.mode === mode));
        _applyTriggerMode(mode);
        _updateStratCalc();
        if (_stratTableOpen) _calcAndRenderPriceTable();
    });

    // 전략현황: 입력값 변경 → 자동계산/테이블 갱신
    ['strat-input-trigger', 'strat-input-rounds', 'strat-input-amount', 'strat-input-start-price'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => {
            _stratInputsUserSet = true;
            _updateStratCalc();
            if (_stratTableOpen) _calcAndRenderPriceTable();
        });
    });

    // 전략현황: 저장 / 테이블 토글
    document.getElementById('strat-save-btn')?.addEventListener('click', _saveStratSettings);
    document.getElementById('strat-table-btn')?.addEventListener('click', _togglePriceTable);

    _initBottomNav();
    _initHomeTabs();
    _initTradesPage();
    _initSettingsPage();

    // F5 새로고침 후 이전 탭 상태 복원
    const _savedPage      = sessionStorage.getItem('cs-page')       || 'home';
    const _savedTab       = sessionStorage.getItem('cs-tab')        || 'realtime';
    const _savedTradesTab = sessionStorage.getItem('cs-trades-tab') || 'fills';

    if (_savedPage !== 'home') {
        _activePage = _savedPage;
        document.querySelectorAll('.cs-page').forEach(el =>
            el.classList.toggle('active', el.id === `page-${_activePage}`));
        document.querySelectorAll('.cs-nav-item').forEach(el =>
            el.classList.toggle('active', el.dataset.page === _activePage));
    } else if (_savedTab !== 'realtime') {
        _activeTab = _savedTab;
        document.querySelectorAll('.cs-home-tab[data-tab]').forEach(el =>
            el.classList.toggle('active', el.dataset.tab === _savedTab));
        document.querySelectorAll('.cs-tab-panel').forEach(el =>
            el.classList.toggle('active', el.id === `home-tab-${_savedTab}`));
    }

    if (_activePage === 'trades' && _savedTradesTab !== 'fills') {
        _tradesActiveSubTab = _savedTradesTab;
        document.querySelectorAll('#trades-sub-tabs .cs-home-tab').forEach(b =>
            b.classList.toggle('active', b.dataset.tradesTab === _savedTradesTab));
        document.querySelectorAll('.cs-trades-panel').forEach(p =>
            p.classList.toggle('active', p.id === `trades-tab-${_savedTradesTab}`));
    }

    _startPolling();
});

// ── 하단 네비 ─────────────────────────────────────────────────────────────────

function _initBottomNav() {
    document.querySelectorAll('.cs-nav-item').forEach(btn => {
        btn.addEventListener('click', () => _switchPage(btn.dataset.page));
    });
}

function _switchPage(page) {
    if (page === _activePage) return;
    _activePage = page;
    sessionStorage.setItem('cs-page', page);
    document.querySelectorAll('.cs-page').forEach(el =>
        el.classList.toggle('active', el.id === `page-${page}`)
    );
    document.querySelectorAll('.cs-nav-item').forEach(el =>
        el.classList.toggle('active', el.dataset.page === page)
    );
    _restartPolling();
}

// ── 홈 서브탭 ─────────────────────────────────────────────────────────────────

function _initHomeTabs() {
    document.querySelectorAll('.cs-home-tab[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => _switchHomeTab(btn.dataset.tab));
    });
}

function _switchHomeTab(tab) {
    if (tab === _activeTab) return;
    _activeTab = tab;
    sessionStorage.setItem('cs-tab', tab);
    document.querySelectorAll('.cs-home-tab[data-tab]').forEach(el =>
        el.classList.toggle('active', el.dataset.tab === tab)
    );
    document.querySelectorAll('.cs-tab-panel').forEach(el =>
        el.classList.toggle('active', el.id === `home-tab-${tab}`)
    );
    _restartPolling();
}

// ── 폴링 ──────────────────────────────────────────────────────────────────────

function _startPolling() {
    _fetchActivePage();
    let ms;
    if (_activePage === 'home') ms = POLL[_activeTab] || 5000;
    else if (_activePage === 'strategy') ms = POLL.strategy;
    else return;  // trades 등 수동 조회 페이지는 폴링 없음
    _timer = setInterval(_fetchActivePage, ms);
}

function _restartPolling() {
    if (_timer) { clearInterval(_timer); _timer = null; }
    _startPolling();
}

async function _fetchActivePage() {
    try {
        if (_activePage === 'home') {
            switch (_activeTab) {
                case 'realtime': await _fetchMarketPrices(); break;
                case 'status':   await _fetchInvestment();   break;
                case 'pnl':      await _fetchCumulative();   break;
                case 'assets':   await _fetchAssets();       break;
            }
        } else if (_activePage === 'strategy') {
            await _fetchStrategy();
        } else if (_activePage === 'trades') {
            const listId = _tradesActiveSubTab === 'fills' ? 'trades-fills-list' : 'trades-pnl-list';
            const list   = document.getElementById(listId);
            if (!list || !list.innerHTML.trim()) await _fetchTradesActive();
        } else if (_activePage === 'settings') {
            await _fetchSettings();
        }
        _setEl('last-updated', `갱신: ${new Date().toLocaleTimeString('ko-KR', { hour12: false })}`);
    } catch (err) {
        console.error('[CryptoSplit] fetch error:', err);
    }
}

// ── 탭 1: 실시간 시세 ────────────────────────────────────────────────────────

async function _fetchMarketPrices() {
    const data = await _fetchJson(API.marketPrices);
    _renderMarketPrices(data);
}

function _renderMarketPrices(data) {
    const list = document.getElementById('market-price-list');
    if (!list) return;

    if (list.children.length === 0) {
        MARKET_COINS.forEach(sym => {
            const meta = COIN_META[sym] || { name: sym, letter: sym.slice(0, 3), css: '' };
            const row  = document.createElement('div');
            row.className = 'cs-coin-row';
            row.innerHTML = `
                <div class="cs-coin-icon ${meta.css}">${meta.letter}</div>
                <div class="cs-coin-info">
                    <span class="cs-coin-name">${meta.name}</span>
                    <span class="cs-coin-symbol">${sym}</span>
                </div>
                <div class="cs-coin-price">
                    <span class="cs-coin-price-value" id="mkt-${sym}">-</span>
                </div>`;
            list.appendChild(row);
        });
    }

    Object.entries(data).forEach(([sym, info]) => {
        const el = document.getElementById(`mkt-${sym}`);
        if (el) el.textContent = info.price ? `${Math.round(info.price).toLocaleString()}원` : '-';
    });
}

// ── 탭 2: 투자현황 ────────────────────────────────────────────────────────────

let _invSymbols = [];

async function _fetchInvestment() {
    const data = await _fetchJson(API.investment);
    _renderInvestment(data);
}

function _renderInvestment(data) {
    if (!data) return;
    const s = data.summary || {};

    _setEl('inv-total-buy',  _fmt(s.total_buy));
    _setEl('inv-total-eval', _fmt(s.total_eval));
    _setEl('inv-counts',     `${s.active_count ?? '-'}/${s.total_count ?? '-'}`);

    const pnlEl = document.getElementById('inv-total-pnl');
    if (pnlEl) {
        const pnl = s.unrealized_pnl ?? 0;
        const pct = s.pct ?? 0;
        pnlEl.textContent = `${_sign(pnl)}${Math.round(pnl).toLocaleString()}원(${_sign(pct)}${pct.toFixed(2)}%)`;
        pnlEl.className   = `cs-summary-value ${_colorClass(pnl)}`;
    }

    const list = document.getElementById('investment-symbol-list');
    if (!list) return;

    _invSymbols = data.symbols || [];

    // 빈 상태 처리
    if (_invSymbols.length === 0) {
        list.innerHTML = '<p class="cs-inv-empty">매매 이력이 없습니다</p>';
        return;
    }
    const empty = list.querySelector('.cs-inv-empty');
    if (empty) empty.remove();

    // 더 이상 데이터에 없는 카드 제거
    const activeSymbols = new Set(_invSymbols.map(s => s.symbol));
    list.querySelectorAll('[id^="inv-card-"]').forEach(card => {
        if (!activeSymbols.has(card.id.replace('inv-card-', ''))) card.remove();
    });

    // 카드 생성/업데이트
    _invSymbols.forEach(sym => {
        if (!document.getElementById(`inv-card-${sym.symbol}`)) {
            list.appendChild(_makeSymbolCard(`inv-card-${sym.symbol}`, sym.symbol));
        }
        _updateInvCard(sym);
    });

    _reorderInvCards();
}

function _updateInvCard(sym) {
    const id = `inv-card-${sym.symbol}`;
    _setEl(`${id}-badge-label`, sym.running ? '실행중' : '중지됨');
    const badge = document.getElementById(`${id}-badge`);
    if (badge) badge.className = `cs-badge ${sym.running ? 'cs-badge-running' : 'cs-badge-stopped'}`;
    _setEl(`${id}-buy`,  _fmt(sym.total_buy));
    _setEl(`${id}-eval`, _fmt(sym.total_eval));
    const pnlEl = document.getElementById(`${id}-pnl`);
    if (pnlEl) {
        const p = sym.unrealized_pnl ?? 0;
        const r = sym.pct ?? 0;
        pnlEl.textContent = `${_sign(p)}${Math.round(p).toLocaleString()}원(${_sign(r)}${r.toFixed(2)}%)`;
        pnlEl.className   = `cs-card-value ${_colorClass(p)}`;
    }
}

function _reorderInvCards() {
    const list = document.getElementById('investment-symbol-list');
    if (!list || _invSymbols.length === 0) return;
    const sortVal = document.getElementById('inv-sort')?.value || 'default';
    const sorted  = [..._invSymbols].sort((a, b) => {
        if (sortVal === 'buy')  return b.total_buy - a.total_buy;
        if (sortVal === 'eval') return b.total_eval - a.total_eval;
        if (sortVal === 'pnl')  return b.unrealized_pnl - a.unrealized_pnl;
        return 0;
    });
    sorted.forEach(sym => {
        const card = document.getElementById(`inv-card-${sym.symbol}`);
        if (card) list.appendChild(card);
    });
}

function _makeSymbolCard(id, sym) {
    const meta = COIN_META[sym] || { name: sym, letter: sym.slice(0, 3), css: '' };
    const card = document.createElement('div');
    card.id    = id;
    card.className = 'cs-symbol-card';
    card.innerHTML = `
        <div class="cs-symbol-card-header">
            <div class="cs-coin-icon ${meta.css}">${meta.letter}</div>
            <span class="cs-symbol-card-title">${meta.name}(${sym})</span>
            <span class="cs-badge cs-badge-stopped" id="${id}-badge">
                <span id="${id}-badge-label">-</span>
            </span>
        </div>
        <div class="cs-symbol-card-body">
            <div class="cs-card-row">
                <span class="cs-card-label">총 매수금액</span>
                <span class="cs-card-value" id="${id}-buy">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">총 평가금액</span>
                <span class="cs-card-value" id="${id}-eval">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">평가손익/수익률</span>
                <span class="cs-card-value" id="${id}-pnl">-</span>
            </div>
        </div>`;
    return card;
}

// ── 탭 3: 누적손익 ────────────────────────────────────────────────────────────

async function _fetchCumulative() {
    const data = await _fetchJson(API.cumulative);
    _renderCumulative(data);
}

function _renderCumulative(data) {
    if (!data) return;
    const s = data.summary || {};

    _setEl('pnl-total-buy',  _fmt(s.total_buy));
    _setEl('pnl-total-sell', _fmt(s.total_sell));

    const pnlEl = document.getElementById('pnl-total-pnl');
    if (pnlEl) {
        const p = s.realized_pnl ?? 0;
        const r = s.pct ?? 0;
        pnlEl.textContent = `${_sign(p)}${Math.round(p).toLocaleString()}원(${_sign(r)}${r.toFixed(2)}%)`;
        pnlEl.className   = `cs-summary-value ${_colorClass(p)}`;
    }

    const list = document.getElementById('pnl-symbol-list');
    if (!list) return;

    (data.symbols || []).forEach(sym => {
        let card = document.getElementById(`pnl-card-${sym.symbol}`);
        if (!card) {
            card = _makePnlCard(sym.symbol);
            list.appendChild(card);
        }
        _setEl(`pnl-card-${sym.symbol}-buy`,  _fmt(sym.total_buy));
        _setEl(`pnl-card-${sym.symbol}-sell`, _fmt(sym.total_sell));

        const pnlEl = document.getElementById(`pnl-card-${sym.symbol}-pnl`);
        if (pnlEl) {
            const p = sym.realized_pnl ?? 0;
            const r = sym.pct ?? 0;
            pnlEl.textContent = `${_sign(p)}${Math.round(p).toLocaleString()}원(${_sign(r)}${r.toFixed(2)}%)`;
            pnlEl.className   = `cs-card-value ${_colorClass(p)}`;
        }
    });
}

function _makePnlCard(sym) {
    const meta = COIN_META[sym] || { name: sym, letter: sym.slice(0, 3), css: '' };
    const card = document.createElement('div');
    card.id    = `pnl-card-${sym}`;
    card.className = 'cs-symbol-card';
    card.innerHTML = `
        <div class="cs-symbol-card-header">
            <div class="cs-coin-icon ${meta.css}">${meta.letter}</div>
            <span class="cs-symbol-card-title">${meta.name}(${sym})</span>
        </div>
        <div class="cs-symbol-card-body">
            <div class="cs-card-row">
                <span class="cs-card-label">총 매수금액</span>
                <span class="cs-card-value" id="pnl-card-${sym}-buy">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">총 매도금액</span>
                <span class="cs-card-value" id="pnl-card-${sym}-sell">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">실현손익/수익률</span>
                <span class="cs-card-value" id="pnl-card-${sym}-pnl">-</span>
            </div>
        </div>`;
    return card;
}

// ── 탭 4: 거래소자산 ─────────────────────────────────────────────────────────

async function _fetchAssets() {
    const data = await _fetchJson(API.assets);
    _renderAssets(data);
}

function _renderAssets(data) {
    if (!data || data.error) return;

    _setEl('assets-total',     _fmt(data.total_assets));
    _setEl('assets-krw-total', _fmt(data.krw_total));
    _setEl('assets-krw-avail', _fmt(data.krw_available));

    const pts    = data.bithumb_points ?? 0;
    const ptsRow = document.getElementById('assets-points-row');
    if (ptsRow) ptsRow.style.display = pts > 0 ? '' : 'none';
    _setEl('assets-points', pts > 0 ? Math.round(pts).toLocaleString() + ' P' : '-');

    const list = document.getElementById('asset-coin-list');
    if (!list) return;

    (data.coins || []).forEach(coin => {
        const sym  = coin.symbol;
        let card   = document.getElementById(`asset-coin-${sym}`);
        if (!card) {
            card = _makeAssetCoinCard(sym);
            list.appendChild(card);
        }
        _updateCoinCard(coin);
    });
}

function _makeAssetCoinCard(sym) {
    const meta = COIN_META[sym] || { name: sym, letter: sym.slice(0, 3), css: '' };
    const card = document.createElement('div');
    card.id    = `asset-coin-${sym}`;
    card.className = 'cs-asset-coin-card';
    card.innerHTML = `
        <div class="cs-asset-coin-header">
            <div class="cs-coin-icon ${meta.css}">${meta.letter}</div>
            <span class="cs-asset-coin-title">${sym}</span>
        </div>
        <div class="cs-asset-coin-body">
            <div class="cs-card-row">
                <span class="cs-card-label">총 보유수량</span>
                <span class="cs-card-value" id="asset-${sym}-qty">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">매수평균가</span>
                <div class="cs-avg-price-wrapper">
                    <span class="cs-card-value" id="asset-${sym}-avg">-</span>
                    <button class="cs-edit-price-btn" id="asset-${sym}-editbtn" data-symbol="${sym}" title="직접 입력" style="display:none">✏</button>
                </div>
            </div>
            <div class="cs-inline-edit" id="asset-${sym}-edit" style="display:none">
                <input type="number" id="asset-${sym}-input" placeholder="평균매수가 (원)" min="0">
                <button class="cs-save-price-btn" data-symbol="${sym}">저장</button>
                <button class="cs-cancel-price-btn" data-symbol="${sym}">취소</button>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">매수금액</span>
                <span class="cs-card-value" id="asset-${sym}-cost">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">평가금액</span>
                <span class="cs-card-value" id="asset-${sym}-eval">-</span>
            </div>
            <div class="cs-card-row">
                <span class="cs-card-label">평가손익</span>
                <span class="cs-card-value" id="asset-${sym}-pnl">-</span>
            </div>
        </div>`;
    return card;
}

function _updateCoinCard(coin) {
    const sym = coin.symbol;
    _setEl(`asset-${sym}-qty`,  coin.quantity % 1 === 0
        ? coin.quantity.toLocaleString()
        : coin.quantity.toFixed(8).replace(/\.?0+$/, ''));
    _setEl(`asset-${sym}-avg`,  _fmt(coin.avg_buy_price));
    _setEl(`asset-${sym}-cost`, _fmt(coin.cost));
    _setEl(`asset-${sym}-eval`, _fmt(coin.eval));

    const pnlEl = document.getElementById(`asset-${sym}-pnl`);
    if (pnlEl) {
        const p = coin.unrealized_pnl ?? 0;
        const r = coin.pct ?? 0;
        pnlEl.textContent = `${_sign(p)}${Math.round(p).toLocaleString()}원(${_sign(r)}${r.toFixed(2)}%)`;
        pnlEl.className   = `cs-card-value ${_colorClass(p)}`;
    }

    const editBtn = document.getElementById(`asset-${sym}-editbtn`);
    if (editBtn) {
        editBtn.style.display = coin.editable ? '' : 'none';
        editBtn.style.color   = coin.manual_avg_price > 0 ? 'var(--cs-accent)' : '';
        editBtn.dataset.manualPrice = coin.manual_avg_price || 0;
    }
}

function _openPriceEdit(sym) {
    const editEl = document.getElementById(`asset-${sym}-edit`);
    if (!editEl) return;
    editEl.style.display = '';
    const editBtn = document.getElementById(`asset-${sym}-editbtn`);
    const input   = document.getElementById(`asset-${sym}-input`);
    if (input && editBtn && Number(editBtn.dataset.manualPrice) > 0) {
        input.value = editBtn.dataset.manualPrice;
    }
    if (input) { input.focus(); input.select(); }
}

function _closePriceEdit(sym) {
    const editEl = document.getElementById(`asset-${sym}-edit`);
    if (editEl) editEl.style.display = 'none';
}

async function _savePriceEdit(sym) {
    const input = document.getElementById(`asset-${sym}-input`);
    if (!input) return;
    const price = parseFloat(input.value);
    if (!price || price <= 0) return;

    try {
        const resp = await fetch(API.manualPrice, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ symbol: sym, avg_buy_price: price }),
        });
        if (!resp.ok) throw new Error('저장 실패');
        const coin = await resp.json();
        _updateCoinCard(coin);
        _closePriceEdit(sym);
    } catch (err) {
        console.error('[CryptoSplit] manual price save error:', err);
    }
}

// ── 모달 (기존 로직 유지) ─────────────────────────────────────────────────────

function _openConfirm(symbol, action) {
    if (action === 'start_resume') {
        _openGapCheck(symbol, false);
        return;
    }
    if (action === 'start_fresh') {
        _openGapCheck(symbol, true);
        return;
    }
    _pendingAction = action;
    _pendingSymbol = symbol;
    // stop
    _setEl('modalTitle', '자동매매 중지 확인');
    _setEl('modalBody',  `${symbol} 자동매매를 중지하시겠습니까?\n미체결 주문이 자동으로 취소됩니다.`);
    document.getElementById('confirmBtn').className = 'cs-btn cs-btn-danger';
    _modal.show();
}

async function _openGapCheck(symbol, isFresh) {
    const url = `${API.botGap}?symbol=${symbol}`;
    try {
        const data = await _fetchJson(url);
        if (data.gap > 0 && _gapModal) {
            _showGapModal(symbol, data, isFresh);
        } else {
            // 갭 없음 → 일반 확인 모달
            if (isFresh) {
                _pendingAction = 'start_fresh';
                _pendingSymbol = symbol;
                _setEl('modalTitle', '자동매매 시작 확인');
                _setEl('modalBody',  `${symbol} 자동매매를 처음부터 시작하시겠습니까?\n기존 진행 상태(차수/기준가)가 초기화됩니다.`);
            } else {
                _pendingAction = 'start_resume';
                _pendingSymbol = symbol;
                _setEl('modalTitle', '자동매매 재시작 확인');
                _setEl('modalBody',  `${symbol} 자동매매를 재시작하시겠습니까?\n기존 차수와 기준가가 유지됩니다.`);
            }
            document.getElementById('confirmBtn').className = 'cs-btn cs-btn-success';
            _modal.show();
        }
    } catch {
        // 오류 시 일반 확인 모달로 폴백
        if (isFresh) {
            _pendingAction = 'start_fresh';
            _pendingSymbol = symbol;
            _setEl('modalTitle', '자동매매 시작 확인');
            _setEl('modalBody',  `${symbol} 자동매매를 처음부터 시작하시겠습니까?\n기존 진행 상태(차수/기준가)가 초기화됩니다.`);
        } else {
            _pendingAction = 'start_resume';
            _pendingSymbol = symbol;
            _setEl('modalTitle', '자동매매 재시작 확인');
            _setEl('modalBody',  `${symbol} 자동매매를 재시작하시겠습니까?\n기존 차수와 기준가가 유지됩니다.`);
        }
        document.getElementById('confirmBtn').className = 'cs-btn cs-btn-success';
        _modal.show();
    }
}

let _gapIsFresh = false;

function _showGapModal(symbol, data, isFresh = false) {
    _gapSymbol  = symbol;
    _gapIsFresh = isFresh;
    const titleVerb = isFresh ? '시작' : '재시작';
    _setEl('gapModalTitle', `${symbol} ${titleVerb} — 가격 갭 감지`);

    const levels   = data.levels || [];
    const preview  = levels.slice(0, 10);
    const extraCnt = levels.length - preview.length;

    const tableRows = preview.map(l =>
        `<tr>
            <td>${l.round_no}차</td>
            <td>${l.grid_price.toLocaleString()}원</td>
            <td>${l.quantity}</td>
            <td>${l.amount.toLocaleString()}원</td>
        </tr>`
    ).join('');
    const moreRow = extraCnt > 0
        ? `<tr><td colspan="4" class="cs-trade-empty">… 외 ${extraCnt}건 더</td></tr>`
        : '';

    document.getElementById('gapModalBody').innerHTML = `
        <div class="cs-gap-summary">
            <div class="cs-gap-row">
                <span>전략 시작가</span>
                <span>${data.start_price.toLocaleString()}원</span>
            </div>
            <div class="cs-gap-row">
                <span>현재가</span>
                <span>${data.current_price.toLocaleString()}원</span>
            </div>
            <div class="cs-gap-row">
                <span>미매수 갭</span>
                <span class="cs-negative">${data.gap}개 차수${data.filled < data.gap ? ` (최대차수 도달로 ${data.filled}개만 처리)` : ''}</span>
            </div>
            <div class="cs-gap-row">
                <span>차수 변화</span>
                <span>${data.current_round}차 → ${data.current_round + data.filled}차</span>
            </div>
        </div>

        <div class="cs-gap-mode-group">
            <label class="cs-gap-mode-btn active" id="gap-opt-virtual">
                <input type="radio" name="gapMode" value="virtual" checked>
                <div>
                    <strong>🔮 가상 채우기<span class="cs-badge-rec">권장</span></strong>
                    <div class="cs-gap-mode-desc">
                        그리드 가격 기준으로 ${data.filled}개 차수 기록을 생성합니다.<br>
                        실제 매수 없이 수량·수익 정합성을 유지합니다.<br>
                        ${isFresh ? '⚠ 가상 차수 매도 시 실제 코인 잔고가 필요합니다.' : ''}
                    </div>
                </div>
            </label>
            <label class="cs-gap-mode-btn" id="gap-opt-real">
                <input type="radio" name="gapMode" value="real">
                <div>
                    <strong>📈 실제 캐치업</strong>
                    <div class="cs-gap-mode-desc">
                        현재가(${data.current_price.toLocaleString()}원)로 실제 매수를 실행합니다.<br>
                        KRW 잔고와 catchup_max 설정이 적용됩니다.<br>
                        ${isFresh ? '코인 잔고 없이 새로 시작하는 경우에 적합합니다.' : ''}
                    </div>
                </div>
            </label>
        </div>

        ${levels.length > 0 ? `
        <div class="cs-gap-preview-title">가상 채우기 예상 내역</div>
        <div class="cs-trades-table-wrap">
            <table class="cs-trade-table">
                <thead>
                    <tr><th>차수</th><th>그리드 매수가</th><th>수량</th><th>금액</th></tr>
                </thead>
                <tbody>${tableRows}${moreRow}</tbody>
            </table>
        </div>` : ''}
    `;

    // 라디오 버튼 active 토글
    document.getElementById('gapModalBody').querySelectorAll('input[name="gapMode"]').forEach(radio => {
        radio.addEventListener('change', () => {
            document.querySelectorAll('.cs-gap-mode-btn').forEach(btn => btn.classList.remove('active'));
            radio.closest('.cs-gap-mode-btn').classList.add('active');
        });
    });

    _gapModal.show();
}

async function _executeGapAction() {
    if (!_gapSymbol) return;
    const mode    = document.querySelector('input[name="gapMode"]:checked')?.value || 'virtual';
    const symbol  = _gapSymbol;
    const isFresh = _gapIsFresh;
    _gapSymbol    = null;
    _gapIsFresh   = false;
    _gapModal.hide();

    const body = { symbol };
    if (isFresh) body.fresh = true;
    if (mode === 'virtual') body.virtual_catchup = true;

    try {
        const resp = await fetch(API.start, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body),
        });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? 'success' : 'warning');
        if (_activePage === 'strategy') await _fetchStrategy();
    } catch {
        _showAlert('요청 처리 중 오류가 발생했습니다.', 'danger');
    }
}

async function _executeAction() {
    // 삭제 확인 처리
    if (_pendingDeleteFn) {
        const fn = _pendingDeleteFn;
        _pendingDeleteFn = null;
        _modal.hide();
        await fn();
        return;
    }

    if (!_pendingAction || !_pendingSymbol) return;
    const action = _pendingAction;
    const symbol = _pendingSymbol;
    _pendingAction = _pendingSymbol = null;
    _modal.hide();

    const isStop = action === 'stop';
    const url    = isStop ? API.stop : API.start;
    const body   = { symbol };
    if (action === 'start_fresh') body.fresh = true;

    try {
        const resp = await fetch(url, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body),
        });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? 'success' : 'warning');
        if (_activeTab === 'status') await _fetchInvestment();
        if (_activePage === 'strategy') await _fetchStrategy();
    } catch {
        _showAlert('요청 처리 중 오류가 발생했습니다.', 'danger');
    }
}

// ── 유틸 ──────────────────────────────────────────────────────────────────────

async function _fetchJson(url, opts = null) {
    const res = await fetch(url, opts || {});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

function _setEl(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function _showAlert(msg, type = 'info') {
    const container = document.getElementById('alert-container');
    if (!container) return;
    const div = document.createElement('div');
    div.className = `alert alert-${type} alert-dismissible fade show`;
    div.role = 'alert';
    div.innerHTML = `${msg}<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="닫기"></button>`;
    container.appendChild(div);
    setTimeout(() => div.remove(), 4000);
}

function _fmt(n) {
    if (n === null || n === undefined) return '-';
    return `${Math.round(n).toLocaleString()}원`;
}

function _sign(n) { return n >= 0 ? '+' : ''; }

function _colorClass(n) {
    if (n > 0) return 'cs-positive';
    if (n < 0) return 'cs-negative';
    return 'cs-neutral';
}

// ── 전략현황 ──────────────────────────────────────────────────────────────────

async function _fetchStrategy() {
    const data = await _fetchJson(API.strategy);
    _stratData = data;
    _renderStrategy(data);
}

function _renderStrategy(data) {
    if (!data) return;
    const symbols = data.symbols || [];

    _initStratSymTabs(symbols);
    if (!_stratActiveSym && symbols.length > 0) _stratActiveSym = symbols[0].symbol;

    const symData = symbols.find(s => s.symbol === _stratActiveSym);
    if (symData) _renderStratSettings(symData);

    _renderStratMonitor(symbols);
}

// ── 설정 탭 ───────────────────────────────────────────────────────────────────

function _initStratSymTabs(symbols) {
    const c = document.getElementById('strat-sym-tabs');
    if (!c) return;
    if (c.children.length === 0) {
        symbols.forEach(s => {
            const btn = document.createElement('button');
            btn.className = 'cs-strat-tab';
            btn.dataset.sym = s.symbol;
            btn.textContent = s.symbol;
            c.appendChild(btn);
        });
    }
    c.querySelectorAll('.cs-strat-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.sym === _stratActiveSym));
}

function _renderStratSettings(s) {
    const mode = s.trigger_mode || 'pct';

    if (!_stratModeUserSet) {
        document.querySelectorAll('#strat-mode-toggle .cs-mode-btn').forEach(b =>
            b.classList.toggle('active', b.dataset.mode === mode));
        _applyTriggerMode(mode);
    }

    if (!_stratInputsUserSet) {
        const triggerVal  = mode === 'pct' ? s.trigger_pct : s.trigger_krw;
        const startPriceVal = s.start_price || '';
        const ids = { 'strat-input-trigger': triggerVal,
                      'strat-input-rounds': s.max_rounds,
                      'strat-input-amount': s.order_amount,
                      'strat-input-start-price': startPriceVal };
        Object.entries(ids).forEach(([id, val]) => {
            const el = document.getElementById(id);
            if (el) el.value = val;
        });
    }

    _updateStratCalc();
    if (_stratTableOpen) _calcAndRenderPriceTable();
}

function _applyTriggerMode(mode) {
    const inp  = document.getElementById('strat-input-trigger');
    const unit = document.getElementById('strat-trigger-unit');
    if (!inp) return;
    if (mode === 'pct') {
        inp.step = '0.1'; inp.min = '0.1'; inp.max = '10';
        if (unit) unit.textContent = '%';
    } else {
        inp.step = '1000'; inp.min = '1'; inp.removeAttribute('max');
        if (unit) unit.textContent = '원';
    }
}

function _updateStratCalc() {
    const rounds     = parseInt(document.getElementById('strat-input-rounds')?.value) || 0;
    const amount     = parseInt(document.getElementById('strat-input-amount')?.value) || 0;
    const triggerVal = parseFloat(document.getElementById('strat-input-trigger')?.value) || 0;
    const mode       = document.querySelector('#strat-mode-toggle .cs-mode-btn.active')?.dataset.mode || 'pct';

    const capital = rounds * amount;
    const cover   = mode === 'krw'
        ? `${(triggerVal * rounds).toLocaleString()}원`
        : `${(triggerVal * rounds).toFixed(1)}%`;

    _setEl('strat-calc-capital', capital > 0 ? `${capital.toLocaleString()}원` : '-');
    _setEl('strat-calc-cover',   cover);
}

async function _saveStratSettings() {
    if (!_stratActiveSym) return;
    const mode       = document.querySelector('#strat-mode-toggle .cs-mode-btn.active')?.dataset.mode || 'pct';
    const triggerVal = parseFloat(document.getElementById('strat-input-trigger')?.value || 0);
    const rounds     = parseInt(document.getElementById('strat-input-rounds')?.value || 0);
    const amount     = parseInt(document.getElementById('strat-input-amount')?.value || 0);
    const symData    = _stratData?.symbols?.find(s => s.symbol === _stratActiveSym);
    const pct        = mode === 'pct' ? triggerVal : (symData?.trigger_pct || 0);
    const krw        = mode === 'krw' ? triggerVal : (symData?.trigger_krw || 0);
    const startPrice = parseFloat(document.getElementById('strat-input-start-price')?.value || 0);

    if (rounds <= 0 || amount <= 0) {
        _showAlert('최대 차수와 1회 주문금액을 입력해주세요.', 'warning');
        return;
    }
    try {
        const resp = await fetch(API.strategySettings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: _stratActiveSym, trigger_mode: mode,
                                   trigger_pct: pct, trigger_krw: krw,
                                   max_rounds: rounds, order_amount: amount,
                                   start_price: startPrice }),
        });
        const data = await resp.json();
        if (data.ok) { _stratModeUserSet = false; _stratInputsUserSet = false; }
        _showAlert(data.message, data.ok ? 'success' : 'danger');
    } catch { _showAlert('저장 중 오류가 발생했습니다.', 'danger'); }
}

// ── 차수별 예상가 테이블 ──────────────────────────────────────────────────────

function _togglePriceTable() {
    _stratTableOpen = !_stratTableOpen;
    const wrap = document.getElementById('strat-price-table-wrap');
    const btn  = document.getElementById('strat-table-btn');
    if (wrap) wrap.style.display = _stratTableOpen ? '' : 'none';
    if (btn)  btn.textContent = _stratTableOpen ? '차수별 예상가 숨기기 ▲' : '차수별 예상가 ▼';
    if (_stratTableOpen) _calcAndRenderPriceTable();
}

function _calcAndRenderPriceTable() {
    const tbody  = document.getElementById('strat-price-tbody');
    if (!tbody) return;

    const mode       = document.querySelector('#strat-mode-toggle .cs-mode-btn.active')?.dataset.mode || 'pct';
    const rounds     = parseInt(document.getElementById('strat-input-rounds')?.value) || 50;
    const triggerVal = parseFloat(document.getElementById('strat-input-trigger')?.value);
    const pct        = mode === 'pct' ? (triggerVal || 0.5) : 0;
    const krw        = mode === 'krw' ? (triggerVal || 10000) : 0;

    const startPrice = parseFloat(document.getElementById('strat-input-start-price')?.value) || 0;
    _setEl('strat-ref-label', startPrice > 0 ? `${Math.round(startPrice).toLocaleString()}원` : '-');

    if (!startPrice || startPrice <= 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="cs-trade-empty">시작가를 입력하세요</td></tr>';
        return;
    }

    // 1차: buy = startPrice, sell = startPrice + trigger
    // n차: buy = startPrice - (n-1)*trigger, sell = buy + trigger
    const rows = [];
    for (let n = 1; n <= rounds; n++) {
        let buy, sell;
        if (mode === 'krw') {
            buy  = startPrice - (n - 1) * krw;
            sell = buy + krw;
        } else {
            buy  = startPrice * Math.pow(1 - pct / 100, n - 1);
            sell = buy * (1 + pct / 100);
        }
        const dropPct = ((buy - startPrice) / startPrice * 100).toFixed(1);
        rows.push(`<tr>
            <td>${n}차</td>
            <td>${Math.round(buy).toLocaleString()}원</td>
            <td>${Math.round(sell).toLocaleString()}원</td>
            <td style="color:var(--cs-red)">${dropPct}%</td>
        </tr>`);
    }
    tbody.innerHTML = rows.join('');
}

// ── 전략 모니터링 카드 ────────────────────────────────────────────────────────

function _renderStratMonitor(symbols) {
    const list = document.getElementById('strat-monitor-list');
    if (!list) return;
    symbols.forEach(s => {
        if (!document.getElementById(`strat-card-${s.symbol}`))
            list.appendChild(_makeStratMonitorCard(s.symbol));
        _updateStratMonitorCard(s);
    });
}

function _makeStratMonitorCard(sym) {
    const meta = COIN_META[sym] || { name: sym, letter: sym.slice(0, 4), css: '' };
    const card = document.createElement('div');
    card.id = `strat-card-${sym}`;
    card.className = 'cs-strat-card';
    card.innerHTML = `
        <div class="cs-cb-banner" id="strat-card-${sym}-cb" style="display:none"></div>
        <div class="cs-strat-card-header">
            <div class="cs-coin-icon ${meta.css}">${meta.letter}</div>
            <span class="cs-strat-card-title">${meta.name}(${sym})</span>
            <span class="cs-badge cs-badge-stopped" id="strat-card-${sym}-badge">
                <span id="strat-card-${sym}-badge-label">-</span>
            </span>
        </div>
        <div class="cs-strat-card-body">
            <div class="cs-strat-price-row">
                <span class="cs-strat-price-label">현재가</span>
                <span class="cs-strat-price-val" id="strat-card-${sym}-price">-</span>
            </div>
            <div class="cs-strat-price-row">
                <span class="cs-strat-price-label">기준가</span>
                <span class="cs-strat-price-val" id="strat-card-${sym}-ref">-</span>
            </div>
            <div class="cs-strat-price-row">
                <span class="cs-strat-price-label">시작가</span>
                <span class="cs-strat-price-val" id="strat-card-${sym}-start">-</span>
            </div>
        </div>
        <div class="cs-strat-progress-wrap">
            <div class="cs-strat-progress-bar">
                <div class="cs-strat-progress-fill" id="strat-card-${sym}-bar" style="width:0%"></div>
            </div>
            <span class="cs-strat-progress-label" id="strat-card-${sym}-rounds">-</span>
        </div>
        <div class="cs-strat-card-body cs-strat-triggers">
            <div class="cs-strat-trigger-row">
                <span class="cs-strat-trigger-label cs-negative">매수 트리거</span>
                <span class="cs-strat-trigger-val cs-negative" id="strat-card-${sym}-buy-trig">-</span>
            </div>
            <div class="cs-strat-trigger-row">
                <span class="cs-strat-trigger-label cs-positive">매도 트리거</span>
                <span class="cs-strat-trigger-val cs-positive" id="strat-card-${sym}-sell-trig">-</span>
            </div>
        </div>
        <div id="strat-card-${sym}-orders"></div>
        <div class="cs-strat-card-footer">
            <span class="cs-strat-pnl-label">오늘 순수익</span>
            <span class="cs-strat-pnl-val cs-neutral" id="strat-card-${sym}-pnl">-</span>
            <span class="cs-strat-trades-val" id="strat-card-${sym}-trades">-</span>
        </div>
        <div class="cs-strat-card-point" id="strat-card-${sym}-point"></div>
        <div class="cs-strat-card-actions">
            <button class="cs-btn cs-btn-success cs-btn-sm cs-btn-start-main"   data-action="start_fresh"  data-symbol="${sym}" id="strat-card-${sym}-start-btn"  style="display:none">▶ 자동매매 시작</button>
            <button class="cs-btn cs-btn-secondary cs-btn-sm cs-btn-start-resume" data-action="start_resume" data-symbol="${sym}" id="strat-card-${sym}-resume-btn" style="display:none">↺ 재시작</button>
            <button class="cs-btn cs-btn-danger    cs-btn-sm"                   data-action="stop"         data-symbol="${sym}" id="strat-card-${sym}-stop-btn"   style="display:none">■ 자동매매 중지</button>
        </div>`;
    return card;
}

function _updateStratMonitorCard(s) {
    const sym = s.symbol;

    const cb = document.getElementById(`strat-card-${sym}-cb`);
    if (cb) {
        if (s.circuit_breaker) {
            const m = Math.floor(s.cb_seconds_left / 60), sec = s.cb_seconds_left % 60;
            const t = m > 0 ? `${m}분 ${sec}초` : `${sec}초`;
            cb.style.display = '';
            cb.textContent = `⚡ 서킷브레이커 작동 중 — 해제까지 ${t}`;
        } else {
            cb.style.display = 'none';
        }
    }

    const badge = document.getElementById(`strat-card-${sym}-badge`);
    if (badge) badge.className = `cs-badge ${s.running ? 'cs-badge-running' : 'cs-badge-stopped'}`;
    _setEl(`strat-card-${sym}-badge-label`, s.running ? '실행중' : '중지됨');

    _setEl(`strat-card-${sym}-price`, s.current_price > 0 ? `${s.current_price.toLocaleString()}원` : '-');
    _setEl(`strat-card-${sym}-ref`,   s.reference_price > 0 ? `${s.reference_price.toLocaleString()}원` : '-');
    _setEl(`strat-card-${sym}-start`, s.start_price > 0 ? `${s.start_price.toLocaleString()}원` : '-');

    const barEl = document.getElementById(`strat-card-${sym}-bar`);
    if (barEl) barEl.style.width = s.max_rounds > 0
        ? `${Math.min(100, s.current_round / s.max_rounds * 100)}%` : '0%';
    _setEl(`strat-card-${sym}-rounds`, `${s.current_round}차 / ${s.max_rounds}차`);

    _setEl(`strat-card-${sym}-buy-trig`,  s.buy_trigger_price > 0
        ? `${s.buy_trigger_price.toLocaleString()}원` : '-');
    _setEl(`strat-card-${sym}-sell-trig`, s.sell_trigger_price > 0
        ? `${s.sell_trigger_price.toLocaleString()}원` : '-');

    const ordersEl = document.getElementById(`strat-card-${sym}-orders`);
    if (ordersEl) {
        ordersEl.innerHTML = (s.open_orders || []).map(o => {
            const elapsed = o.elapsed_sec || 0;
            const m = Math.floor(elapsed / 60), sec = elapsed % 60;
            const t = m > 0 ? `${m}분 ${sec}초` : `${sec}초`;
            return `<div class="cs-pending-order">
                <span class="cs-pending-icon">⏳</span>
                <span class="cs-pending-text">${o.side === 'buy' ? '매수' : '매도'} 대기 ${Math.round(o.price).toLocaleString()}원</span>
                <span class="cs-pending-time">${t} 경과</span>
            </div>`;
        }).join('');
    }

    const pnlEl = document.getElementById(`strat-card-${sym}-pnl`);
    if (pnlEl) {
        const p = s.today_net_pnl ?? 0;
        pnlEl.textContent = `${_sign(p)}${Math.round(p).toLocaleString()}원`;
        pnlEl.className = `cs-strat-pnl-val ${_colorClass(p)}`;
    }
    const pointEl = document.getElementById(`strat-card-${sym}-point`);
    if (pointEl) {
        const pt = s.today_point ?? 0;
        pointEl.textContent = pt > 0 ? `포인트 +${Math.round(pt).toLocaleString()}원` : '';
    }
    _setEl(`strat-card-${sym}-trades`, `체결 ${s.today_trades ?? 0}회`);

    const startBtn  = document.getElementById(`strat-card-${sym}-start-btn`);
    const resumeBtn = document.getElementById(`strat-card-${sym}-resume-btn`);
    const stopBtn   = document.getElementById(`strat-card-${sym}-stop-btn`);
    if (startBtn)  startBtn.style.display  = s.running ? 'none' : '';
    if (resumeBtn) resumeBtn.style.display = s.running ? 'none' : '';
    if (stopBtn)   stopBtn.style.display   = s.running ? '' : 'none';
}

// ── 투자내역 페이지 ───────────────────────────────────────────────────────────

function _initTradesPage() {
    // 퀵 날짜 버튼 초기화 (today 기본값, fetch 없이)
    _applyQuickRange('day', false);

    const sel = document.getElementById('trades-sym-select');
    if (sel) {
        BOT_SYMBOLS.forEach(sym => {
            const opt = document.createElement('option');
            opt.value = sym; opt.textContent = sym;
            sel.appendChild(opt);
        });
    }

    // 퀵 날짜 버튼 클릭
    document.querySelectorAll('.cs-trades-quick-btn').forEach(btn => {
        btn.addEventListener('click', () => _applyQuickRange(btn.dataset.range));
    });

    // 날짜 직접 변경 시 퀵 버튼 활성화 해제
    ['trades-start', 'trades-end'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            _tradesQuickRange = null;
            document.querySelectorAll('.cs-trades-quick-btn').forEach(b => b.classList.remove('active'));
        });
    });

    document.querySelectorAll('#trades-sub-tabs .cs-home-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tradesTab;
            if (tab === _tradesActiveSubTab) return;
            _tradesActiveSubTab = tab;
            sessionStorage.setItem('cs-trades-tab', tab);
            document.querySelectorAll('#trades-sub-tabs .cs-home-tab').forEach(b =>
                b.classList.toggle('active', b.dataset.tradesTab === tab));
            document.querySelectorAll('.cs-trades-panel').forEach(p =>
                p.classList.toggle('active', p.id === `trades-tab-${tab}`));
            _fetchTradesActive();
        });
    });

    document.getElementById('trades-search-btn')?.addEventListener('click', () => {
        _tradesStart = document.getElementById('trades-start')?.value || '';
        _tradesEnd   = document.getElementById('trades-end')?.value   || '';
        _tradesSym   = document.getElementById('trades-sym-select')?.value || 'all';
        _selectedOrderIds.clear();
        _updateDeleteCount();
        _fetchTradesActive();
    });

    document.getElementById('trades-delete-mode-btn')?.addEventListener('click', _toggleDeleteMode);
    document.getElementById('trades-select-all-btn')?.addEventListener('click', _selectAllOrders);
    document.getElementById('trades-delete-selected-btn')?.addEventListener('click', _confirmDeleteSelected);
    document.getElementById('trades-delete-range-btn')?.addEventListener('click', _confirmDeleteRange);

    // 체크박스 변경 이벤트 (이벤트 위임)
    document.addEventListener('change', e => {
        const cb = e.target.closest('.cs-trade-check');
        if (!cb || !_tradesDeleteMode) return;
        const id = parseInt(cb.dataset.orderId);
        if (cb.checked) _selectedOrderIds.add(id);
        else _selectedOrderIds.delete(id);
        cb.closest('tr')?.classList.toggle('cs-trade-row-selected', cb.checked);
        _updateDeleteCount();
    });
}

async function _fetchTradesActive() {
    try {
        if (_tradesActiveSubTab === 'fills') await _fetchTradeFills();
        else                                  await _fetchTradePnl();
    } catch (err) {
        console.error('[CryptoSplit] trades fetch error:', err);
    }
}

function _applyQuickRange(range, doFetch = true) {
    const today = new Date();
    const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const todayStr = fmt(today);
    let start;

    switch (range) {
        case 'day':
            start = todayStr;
            break;
        case 'week': {
            const d = new Date(today);
            d.setDate(d.getDate() - 6);
            start = fmt(d);
            break;
        }
        case 'month':
            start = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-01`;
            break;
        case 'all':
        default:
            start = '2026-06-01';
    }

    const startEl = document.getElementById('trades-start');
    const endEl   = document.getElementById('trades-end');
    if (startEl) startEl.value = start;
    if (endEl)   endEl.value   = todayStr;
    _tradesStart    = start;
    _tradesEnd      = todayStr;
    _tradesQuickRange = range;

    document.querySelectorAll('.cs-trades-quick-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.range === range));

    if (doFetch) _fetchTradesActive();
}

function _toggleDeleteMode() {
    _tradesDeleteMode = !_tradesDeleteMode;
    _selectedOrderIds.clear();

    const btn = document.getElementById('trades-delete-mode-btn');
    const bar = document.getElementById('trades-delete-bar');
    if (btn) {
        btn.textContent = _tradesDeleteMode ? '삭제모드 종료' : '삭제모드';
        btn.className   = _tradesDeleteMode
            ? 'cs-btn cs-btn-danger cs-btn-sm'
            : 'cs-btn cs-btn-secondary cs-btn-sm';
    }
    if (bar) bar.style.display = _tradesDeleteMode ? 'flex' : 'none';

    const selAllBtn = document.getElementById('trades-select-all-btn');
    if (selAllBtn) selAllBtn.textContent = '전체 선택';

    _updateDeleteCount();
    _fetchTradesActive();
}

function _updateDeleteCount() {
    const n = _selectedOrderIds.size;
    const countEl  = document.getElementById('trades-delete-count');
    const delBtn   = document.getElementById('trades-delete-selected-btn');
    if (countEl) countEl.textContent = `${n}건 선택`;
    if (delBtn) delBtn.disabled = n === 0;
}

function _selectAllOrders() {
    const allCbs = document.querySelectorAll('.cs-trade-check');
    const anyUnchecked = [...allCbs].some(cb => !cb.checked);
    allCbs.forEach(cb => {
        cb.checked = anyUnchecked;
        const id = parseInt(cb.dataset.orderId);
        if (anyUnchecked) _selectedOrderIds.add(id);
        else _selectedOrderIds.delete(id);
        cb.closest('tr')?.classList.toggle('cs-trade-row-selected', anyUnchecked);
    });
    const btn = document.getElementById('trades-select-all-btn');
    if (btn) btn.textContent = anyUnchecked ? '전체 해제' : '전체 선택';
    _updateDeleteCount();
}

function _confirmDeleteSelected() {
    if (_selectedOrderIds.size === 0) return;
    const n = _selectedOrderIds.size;
    _setEl('modalTitle', '선택 내역 삭제');
    _setEl('modalBody',  `선택한 ${n}건의 내역을 삭제하시겠습니까?\n이 작업은 되돌릴 수 없으며 수익 통계에 영향을 줄 수 있습니다.`);
    document.getElementById('confirmBtn').className = 'cs-btn cs-btn-danger';
    _pendingDeleteFn = _doDeleteSelected;
    _modal.show();
}

function _confirmDeleteRange() {
    const start = _tradesStart || document.getElementById('trades-start')?.value || '';
    const end   = _tradesEnd   || document.getElementById('trades-end')?.value   || '';
    const sym   = _tradesSym || 'all';
    const symLabel = sym === 'all' ? '전체 심볼' : sym;
    _setEl('modalTitle', '기간 전체 삭제');
    _setEl('modalBody',  `${start} ~ ${end} 기간의 ${symLabel} 내역을 모두 삭제하시겠습니까?\n이 작업은 되돌릴 수 없으며 수익 통계에 영향을 줄 수 있습니다.`);
    document.getElementById('confirmBtn').className = 'cs-btn cs-btn-danger';
    _pendingDeleteFn = _doDeleteRange;
    _modal.show();
}

async function _doDeleteSelected() {
    const ids = [..._selectedOrderIds];
    try {
        const resp = await fetch(API.deleteOrders, {
            method:  'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ ids }),
        });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? 'success' : 'danger');
        if (data.ok) {
            _selectedOrderIds.clear();
            _updateDeleteCount();
            document.getElementById('trades-select-all-btn').textContent = '전체 선택';
            await _fetchTradesActive();
        }
    } catch { _showAlert('삭제 중 오류가 발생했습니다.', 'danger'); }
}

async function _doDeleteRange() {
    const start = _tradesStart || document.getElementById('trades-start')?.value || '';
    const end   = _tradesEnd   || document.getElementById('trades-end')?.value   || '';
    const sym   = _tradesSym || 'all';
    try {
        const resp = await fetch(API.deleteOrders, {
            method:  'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ start, end, symbol: sym }),
        });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? 'success' : 'danger');
        if (data.ok) {
            _selectedOrderIds.clear();
            _updateDeleteCount();
            document.getElementById('trades-select-all-btn').textContent = '전체 선택';
            await _fetchTradesActive();
        }
    } catch { _showAlert('삭제 중 오류가 발생했습니다.', 'danger'); }
}

async function _fetchTradeFills() {
    const params = new URLSearchParams({ start: _tradesStart, end: _tradesEnd, symbol: _tradesSym });
    const data   = await _fetchJson(`${API.tradeFills}?${params}`);
    _renderTradeFills(data.fills || []);
}

async function _fetchTradePnl() {
    const params = new URLSearchParams({ start: _tradesStart, end: _tradesEnd, symbol: _tradesSym });
    const data   = await _fetchJson(`${API.tradePnl}?${params}`);
    _renderTradePnl(data.rounds || []);
}

function _renderTradeFills(fills) {
    const list = document.getElementById('trades-fills-list');
    if (!list) return;

    if (fills.length === 0) {
        list.innerHTML = '<p class="cs-trade-empty">조회된 체결 내역이 없습니다</p>';
        return;
    }

    const checkTh = _tradesDeleteMode ? '<th class="cs-trade-check-th"></th>' : '';
    const rows = fills.map(t => {
        const isSell   = t.side === 'sell';
        const sideCls  = isSell ? 'cs-trade-side-sell' : 'cs-trade-side-buy';
        const priceStr = t.filled_price > 0 ? Math.round(t.filled_price).toLocaleString() + '원' : '-';
        const qty      = t.quantity || 0;
        const qtyStr   = qty > 0 ? (qty < 1 ? qty.toFixed(6) : qty.toFixed(4)) : '-';
        const amtStr   = t.amount > 0 ? Math.round(t.amount).toLocaleString() + '원' : '-';
        const timeStr  = t.filled_at ? t.filled_at.slice(0, 19).replace('T', ' ') : '-';
        const checked  = _selectedOrderIds.has(t.id) ? 'checked' : '';
        const selCls   = _selectedOrderIds.has(t.id) ? ' class="cs-trade-row-selected"' : '';
        const checkTd  = _tradesDeleteMode
            ? `<td style="text-align:center"><input type="checkbox" class="cs-trade-check" data-order-id="${t.id}" ${checked}></td>`
            : '';
        return `<tr${selCls}>
            ${checkTd}
            <td>${t.symbol}</td>
            <td class="${sideCls}">${isSell ? '매도' : '매수'}</td>
            <td>${priceStr}</td>
            <td style="color:var(--cs-muted)">${qtyStr}</td>
            <td style="color:var(--cs-muted)">${amtStr}</td>
            <td style="color:var(--cs-muted)">${t.round_no ?? '-'}차</td>
            <td style="color:var(--cs-muted)">${timeStr}</td>
        </tr>`;
    }).join('');

    list.innerHTML = `<div class="cs-trades-table-wrap">
        <table class="cs-trade-table">
            <thead><tr>
                ${checkTh}
                <th>심볼</th><th>구분</th><th>체결가</th>
                <th>수량</th><th>주문금액</th><th>차수</th><th>체결시각</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}

function _renderTradePnl(rounds) {
    const list  = document.getElementById('trades-pnl-list');
    const sumEl = document.getElementById('trades-pnl-summary');
    if (!list) return;

    if (rounds.length === 0) {
        list.innerHTML = '<p class="cs-trade-empty">조회된 손익 내역이 없습니다</p>';
        if (sumEl) sumEl.style.display = 'none';
        return;
    }

    // ── 요약 통계 계산 ────────────────────────────────────────────────────────
    const nd    = new Date();
    const today = `${nd.getFullYear()}-${String(nd.getMonth()+1).padStart(2,'0')}-${String(nd.getDate()).padStart(2,'0')}`;
    let periodPnl = 0, todayPnl = 0, periodPairs = 0, todayPairs = 0, totalAmount = 0;

    for (const r of rounds) {
        if (r.pnl != null) {
            periodPnl   += r.pnl;
            totalAmount += (r.buy_amount || 0) + (r.sell_amount || 0);
            periodPairs++;
            if ((r.filled_at || '').slice(0, 10) === today) {
                todayPnl += r.pnl;
                todayPairs++;
            }
        }
    }

    let hourlyAvg = 0;
    if (_tradesStart && _tradesEnd) {
        const hours = Math.max(1,
            (new Date(_tradesEnd   + 'T23:59:59') -
             new Date(_tradesStart + 'T00:00:00')) / 3600000);
        hourlyAvg = periodPnl / hours;
    }

    if (sumEl) {
        sumEl.style.display = '';
        sumEl.innerHTML = `<div class="cs-summary-section">
            <div class="cs-summary-row">
                <span class="cs-summary-label">기간 누적 손익</span>
                <span class="cs-summary-value ${_colorClass(periodPnl)}">${_sign(periodPnl)}${Math.round(periodPnl).toLocaleString()}원</span>
            </div>
            <div class="cs-summary-row">
                <span class="cs-summary-label">오늘 손익</span>
                <span class="cs-summary-value ${_colorClass(todayPnl)}">${_sign(todayPnl)}${Math.round(todayPnl).toLocaleString()}원</span>
            </div>
            <div class="cs-summary-row">
                <span class="cs-summary-label">시간당 평균 손익</span>
                <span class="cs-summary-value ${_colorClass(hourlyAvg)}">${_sign(hourlyAvg)}${Math.round(hourlyAvg).toLocaleString()}원</span>
            </div>
            <div class="cs-summary-row">
                <span class="cs-summary-label">기간 완료 스플릿</span>
                <span class="cs-summary-value">${periodPairs.toLocaleString()}회</span>
            </div>
            <div class="cs-summary-row">
                <span class="cs-summary-label">오늘 완료 스플릿</span>
                <span class="cs-summary-value">${todayPairs.toLocaleString()}회</span>
            </div>
            <div class="cs-summary-row">
                <span class="cs-summary-label">누적 거래금액</span>
                <span class="cs-summary-value">${Math.round(totalAmount).toLocaleString()}원</span>
            </div>
        </div>`;
    }

    const checkTh = _tradesDeleteMode ? '<th class="cs-trade-check-th"></th>' : '';
    const rows = rounds.map(r => {
        const buyP   = r.buy_price  > 0 ? Math.round(r.buy_price).toLocaleString()  + '원' : '-';
        const sellP  = r.sell_price > 0 ? Math.round(r.sell_price).toLocaleString() + '원' : '-';
        const qty    = r.quantity || 0;
        const qtyStr = qty > 0 ? (qty < 1 ? qty.toFixed(6) : qty.toFixed(4)) : '-';
        const amtStr = r.buy_amount > 0 ? Math.round(r.buy_amount).toLocaleString() + '원' : '-';
        const timeStr = r.filled_at ? r.filled_at.slice(0, 19).replace('T', ' ') : '-';
        let pnlCls = 'cs-pnl-zero', pnlStr = '-';
        if (r.pnl != null) {
            pnlCls = r.pnl > 0 ? 'cs-pnl-positive' : (r.pnl < 0 ? 'cs-pnl-negative' : 'cs-pnl-zero');
            pnlStr = `${_sign(r.pnl)}${Math.round(r.pnl).toLocaleString()}원`;
        }
        const checked  = r.sell_id && _selectedOrderIds.has(r.sell_id) ? 'checked' : '';
        const selCls   = r.sell_id && _selectedOrderIds.has(r.sell_id) ? ' class="cs-trade-row-selected"' : '';
        const checkTd  = _tradesDeleteMode && r.sell_id
            ? `<td style="text-align:center"><input type="checkbox" class="cs-trade-check" data-order-id="${r.sell_id}" ${checked}></td>`
            : (_tradesDeleteMode ? '<td></td>' : '');
        return `<tr${selCls}>
            ${checkTd}
            <td>${r.symbol}</td>
            <td style="color:var(--cs-muted)">${r.round_no ?? '-'}차</td>
            <td class="cs-trade-side-buy">${buyP}</td>
            <td class="cs-trade-side-sell">${sellP}</td>
            <td style="color:var(--cs-muted)">${qtyStr}</td>
            <td style="color:var(--cs-muted)">${amtStr}</td>
            <td class="${pnlCls}">${pnlStr}</td>
            <td style="color:var(--cs-muted)">${timeStr}</td>
        </tr>`;
    }).join('');

    list.innerHTML = `<div class="cs-trades-table-wrap">
        <table class="cs-trade-table">
            <thead><tr>
                ${checkTh}
                <th>심볼</th><th>차수번호</th><th>매수가</th><th>매도가</th>
                <th>수량</th><th>주문금액</th><th>실현손익</th><th>체결시각</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}

// ── 설정 페이지 ───────────────────────────────────────────────────────────────

let _settingsLoaded = false;

function _initSettingsPage() {
    // 비밀번호 표시/숨기기 토글 (이벤트 위임)
    document.addEventListener('click', e => {
        const btn = e.target.closest('.cs-pwd-toggle');
        if (!btn) return;
        const inp = document.getElementById(btn.dataset.target);
        if (!inp) return;
        const showing = inp.type === 'text';
        inp.type = showing ? 'password' : 'text';
        btn.classList.toggle('cs-pwd-active', !showing);
    });

    document.getElementById('settings-exchange-save')
        ?.addEventListener('click', () => _saveSettings({
            access_key: document.getElementById('settings-access-key')?.value,
            secret_key: document.getElementById('settings-secret-key')?.value,
        }));

    document.getElementById('settings-telegram-save')
        ?.addEventListener('click', () => _saveSettings({
            bot_token: document.getElementById('settings-tg-token')?.value,
            chat_id:   document.getElementById('settings-tg-chat-id')?.value,
        }));

    document.getElementById('settings-telegram-test')
        ?.addEventListener('click', _testTelegram);

    document.getElementById('settings-strategy-save')
        ?.addEventListener('click', () => _saveSettings({
            order_timeout:       document.getElementById('settings-order-timeout')?.value,
            price_poll_interval: document.getElementById('settings-poll-interval')?.value,
        }));

    document.getElementById('settings-report-save')
        ?.addEventListener('click', () => _saveSettings({
            report_hour:   document.getElementById('settings-report-hour')?.value,
            report_minute: document.getElementById('settings-report-minute')?.value,
        }));

    document.getElementById('settings-membership-save')
        ?.addEventListener('click', async () => {
            const membership = document.getElementById('settings-membership')?.value;
            if (!membership) return;
            await _saveSettings({ membership });
            _applyTierColor(membership);
            _settingsLoaded = false;  // 다음 탭 진입 시 재로드
        });
}

function _applyTierColor(name) {
    const hex = TIER_COLORS[name];
    if (!hex) return;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    const root = document.documentElement;
    root.style.setProperty('--tier-color',     hex);
    root.style.setProperty('--tier-color-dim', `rgba(${r},${g},${b},0.12)`);
    root.style.setProperty('--tier-color-mid', `rgba(${r},${g},${b},0.35)`);
    const badge = document.getElementById('tier-badge-settings');
    if (badge) badge.textContent = name;
}

async function _fetchSettings() {
    if (_settingsLoaded) return;
    try {
        const data = await _fetchJson('/api/settings');

        // 서버 접속 주소
        const ip   = data.server?.ip   || location.hostname;
        const port = data.server?.port || 5000;
        const url  = `http://${ip}:${port}`;
        const urlEl = document.getElementById('settings-server-url');
        if (urlEl) { urlEl.textContent = url; urlEl.href = url; }

        _setPlaceholder('settings-access-key', data.exchange?.access_key, '미설정');
        _setPlaceholder('settings-secret-key', data.exchange?.secret_key ? '****' : '', '미설정');
        _setPlaceholder('settings-tg-token',   data.telegram?.bot_token,  '미설정');

        const chatEl = document.getElementById('settings-tg-chat-id');
        if (chatEl) chatEl.value = data.telegram?.chat_id ?? '';

        _setNumVal('settings-order-timeout', data.strategy?.order_timeout);
        _setNumVal('settings-poll-interval', data.strategy?.price_poll_interval);
        _setNumVal('settings-report-hour',   data.report?.hour);
        _setNumVal('settings-report-minute', data.report?.minute);

        const memEl = document.getElementById('settings-membership');
        if (memEl && data.exchange?.membership) {
            memEl.value = data.exchange.membership;
            _applyTierColor(data.exchange.membership);
        }

        _settingsLoaded = true;
    } catch { _showAlert('설정을 불러오지 못했습니다.', 'danger'); }
}

function _setPlaceholder(id, val, fallback) {
    const el = document.getElementById(id);
    if (el) el.placeholder = val || fallback || '';
}

function _setNumVal(id, val) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) el.value = val;
}

async function _saveSettings(body) {
    const filtered = {};
    for (const [k, v] of Object.entries(body)) {
        if (v !== '' && v !== null && v !== undefined) filtered[k] = v;
    }
    if (Object.keys(filtered).length === 0) {
        _showAlert('변경된 항목이 없습니다.', 'warning');
        return;
    }
    try {
        const resp = await fetch('/api/settings', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(filtered),
        });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? (data.needs_restart ? 'warning' : 'success') : 'danger');
        if (data.ok) {
            _settingsLoaded = false;
            await _fetchSettings();
        }
    } catch { _showAlert('저장 중 오류가 발생했습니다.', 'danger'); }
}

async function _testTelegram() {
    try {
        const resp = await fetch('/api/settings/telegram/test', { method: 'POST' });
        const data = await resp.json();
        _showAlert(data.message, data.ok ? 'success' : 'danger');
    } catch { _showAlert('전송 중 오류가 발생했습니다.', 'danger'); }
}
