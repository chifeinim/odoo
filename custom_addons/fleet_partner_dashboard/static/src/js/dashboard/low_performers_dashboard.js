/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, onWillUnmount, useState, useRef } = hooks;
import { registry } from '@web/core/registry';

export class OwlLowPerformersDashboard extends Component {
  // ===== basic utils ======================================================
  formatNumber(v, d = 0) {
    const n = Number(v); if (Number.isNaN(n)) return v ?? '';
    return new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    }).format(n);
  }
  formatDateYMDToDMY(ymd) {
    if (!ymd) return '';
    const [y,m,d] = ymd.split('-').map(Number);
    if (!y || !m || !d) return ymd;
    return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${y}`;
  }
  digits(s) { return (s || '').replace(/\D/g, ''); }
  periodButtonLabel() {
    const p = this.state.draftSelectedPeriod || '';
    if (!p) return 'Date';
    if (p === 'Custom Range') return 'Custom Range';
    return p;
  }
  formatPeriodLabel(p) {
    const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(String(p || ''));
    if (!m) return p;
    const y = +m[1], mo = +m[2], d = m[3] ? +m[3] : 1;
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mon = months[mo - 1] || '';
    return m[3] ? `${d}-${mon}` : `${mon}-${y}`;
  }

  _isSameArray(a, b) {
    if (a === b) return true;
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    const A = [...a].sort(); const B = [...b].sort();
    return A.every((v, i) => String(v) === String(B[i]));
  }

  isDirty() {
    return !(
      this._isSameArray(this.state.draftSelectedProducts,   this.state.selectedProducts) &&
      this._isSameArray(this.state.draftSelectedScores,     this.state.selectedScores) &&
      this._isSameArray(this.state.draftSelectedCategories, this.state.selectedCategories) &&
      this._isSameArray(this.state.draftSelectedRisks,      this.state.selectedRisks) &&
      this._isSameArray(this.state.draftSelectedIssues,     this.state.selectedIssues) &&
      this.state.draftSelectedPeriod === this.state.selectedPeriod &&
      this.state.draftStartDate === this.state.startDate &&
      this.state.draftEndDate === this.state.endDate
    );
  }

  // ===== scroll helpers (copied from Performance dashboard fix) ===========
  _getScrollContainer(el) {
    // find the closest ancestor that actually scrolls vertically
    const bad = /(auto|scroll)/;
    let n = el;
    while (n && n !== document.body && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      const hasY = bad.test(cs.overflowY) || bad.test(cs.overflow);
      if (hasY && n.scrollHeight > n.clientHeight) return n;
      n = n.parentElement;
    }
    return window;
  }

  _attachStickyListeners() {
    const host = this.stickyHostRef?.el || this.el;
    const wrap = this.tableWrapRef?.el;

    // figure out who is actually scrolling vertically
    const scroller = this._getScrollContainer(host);

    // detach previous listeners if any
    if (this._scrollTarget && this._onRootScroll) {
      this._scrollTarget.removeEventListener('scroll', this._onRootScroll);
    }
    if (this._wrapTarget && this._onWrapScroll) {
      this._wrapTarget.removeEventListener('scroll', this._onWrapScroll);
    }

    // define the fresh listeners
    this._onRootScroll = () => {
      this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    };
    this._onWrapScroll = () => {
      this._syncStickyHeaderX && this._syncStickyHeaderX();
    };

    this._scrollTarget = scroller;
    this._wrapTarget   = wrap;

    scroller.addEventListener('scroll', this._onRootScroll, { passive: true });
    if (wrap) wrap.addEventListener('scroll', this._onWrapScroll, { passive: true });

    // run an initial sync right now
    this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    this._syncStickyHeaderX && this._syncStickyHeaderX();
  }

  _detachStickyListeners() {
    if (this._scrollTarget && this._onRootScroll) {
      this._scrollTarget.removeEventListener('scroll', this._onRootScroll);
      this._scrollTarget = null;
      this._onRootScroll = null;
    }
    if (this._wrapTarget && this._onWrapScroll) {
      this._wrapTarget.removeEventListener('scroll', this._onWrapScroll);
      this._wrapTarget = null;
      this._onWrapScroll = null;
    }
  }

  // ===== sorting / paging logic (same as you had) =========================
  _compareByKey(a, b, key) {
    const ax = a?.[key], bx = b?.[key];

    const norm = (v) => (v === undefined || v === null) ? null
      : (typeof v === 'boolean' ? (v ? 1 : 0)
      : (typeof v === 'number' ? v
      : (typeof v === 'string' ? v.trim()
      : v)));

    let av = norm(ax), bv = norm(bx);

    // nulls last
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;

    // date-ish compare
    if (key === 'hire_date' ||
        (/^\d{4}-\d{2}-\d{2}/.test(String(av)) &&
         /^\d{4}-\d{2}-\d{2}/.test(String(bv)))) {
      const ta = Date.parse(av), tb = Date.parse(bv);
      if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb;
    }

    // numeric compare
    if (typeof av === 'number' && typeof bv === 'number') return av - bv;
    if (typeof av === 'number' && typeof bv !== 'number') return -1;
    if (typeof av !== 'number' && typeof bv === 'number') return 1;

    // string-ish compare
    return String(av).toLocaleLowerCase()
      .localeCompare(String(bv).toLocaleLowerCase(), undefined, {
        numeric: true, sensitivity: 'base',
      });
  }

  setSort(key) {
    if (this.state.sortKey === key) {
      this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.state.sortKey = key;
      this.state.sortDir = 'asc';
    }
    this.state.page = 1;
  }

  sortedFilteredDrivers() {
    const q  = (this.state.search || '').trim().toLowerCase();
    const qd = this.digits(q);
    const issuesSel = this.state.selectedIssues; // [] | ['Yes'] | ['No']

    const rows = Object.values(this.state.data || {}).filter(d => {
      const nameHit  = (d.name || '').toLowerCase().includes(q);
      const phoneHit = qd ? this.digits(d.phone).includes(qd) : false;
      if (q && !(nameHit || phoneHit)) return false;

      if (issuesSel.length) {
        if (!issuesSel.includes(d.issues_reported)) return false;
      }
      return true;
    });

    const dir = this.state.sortDir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const res = this._compareByKey(a, b, this.state.sortKey);
      if (res !== 0) return dir * res;
      return this._compareByKey(a, b, 'name');
    });

    return rows;
  }

  totalDriverCount() { return this.sortedFilteredDrivers().length; }

  pageCount() {
    return Math.max(1, Math.ceil(this.totalDriverCount() / this.state.pageSize));
  }

  goToPage(p) {
    const pc = this.pageCount();
    this.state.page = Math.min(pc, Math.max(1, Number(p) || 1));
  }

  prevPage = () => this.goToPage(this.state.page - 1);
  nextPage = () => this.goToPage(this.state.page + 1);

  pageWindow() {
    const pc = this.pageCount(), p = this.state.page;
    const nums = new Set([1, pc, p-2, p-1, p, p+1, p+2]
      .filter(x => x >= 1 && x <= pc));
    const arr = [...nums].sort((a,b)=>a-b);
    const out=[];
    for (let i=0;i<arr.length;i++){
      out.push(arr[i]);
      if (i < arr.length-1 && arr[i+1] !== arr[i]+1) out.push('…');
    }
    return out;
  }

  pagedDrivers() {
    const rows = this.sortedFilteredDrivers();
    const pc = Math.max(1, Math.ceil(rows.length / this.state.pageSize));
    if (this.state.page > pc) this.state.page = pc;
    const start = (this.state.page - 1) * this.state.pageSize;
    return rows.slice(start, start + this.state.pageSize);
  }

  // ===== sticky layout helpers ===========================================
  _recomputeStickyHeights() {
    const f = this.filtersBarRef?.el;
    const s = this.searchBarRef?.el;
    const fh = f ? f.getBoundingClientRect().height : 0;
    const sh = s ? s.getBoundingClientRect().height : 0;
    if (this.el) {
      this.el.style.setProperty('--sticky-filters-h', `${Math.ceil(fh)}px`);
      this.el.style.setProperty('--sticky-search-h',  `${Math.ceil(sh)}px`);
    }
  }

  _updateCloneSortIndicators() {
    const host = this.stickyHostRef?.el;
    if (!host) return;
    host.querySelectorAll('th').forEach(cth => {
      cth.querySelector('.fp-arrow')?.remove();
      const key = cth.dataset.sortKey;
      if (key && key === this.state.sortKey) {
        const arrow = document.createElement('span');
        arrow.className = 'fp-arrow ms-1';
        arrow.textContent = this.state.sortDir === 'asc' ? '▲' : '▼';
        cth.appendChild(arrow);
      }
    });
  }

  _buildStickyHeader() {
    const table = this.tableRef?.el;
    const wrap  = this.tableWrapRef?.el;
    const host  = this.stickyHostRef?.el;
    if (!table || !wrap || !host) return;

    const thead = table.querySelector('thead');
    const row   = thead && thead.querySelector('tr');
    if (!thead || !row) return;

    // measure current column widths
    const ths    = Array.from(row.children);
    const widths = ths.map(th => Math.ceil(th.getBoundingClientRect().width));

    // rebuild host contents
    host.innerHTML = '';

    const track      = document.createElement('div');
    track.className  = 'fp-sticky-track';

    const cloneTable = document.createElement('table');
    cloneTable.className = 'table table-striped table-hover mb-0';

    const cloneHead  = document.createElement('thead');
    cloneHead.className = 'table-light';

    const cloneRow   = document.createElement('tr');

    ths.forEach((th, i) => {
      const cth = document.createElement('th');
      const w = widths[i] || 80;
      cth.style.width    = `${w}px`;
      cth.style.minWidth = `${w}px`;
      cth.style.maxWidth = `${w}px`;
      cth.style.whiteSpace = 'nowrap';

      // Get a clean label from the real <th>, minus arrow spans
      const tmp = th.cloneNode(true);
      tmp.querySelectorAll('span').forEach(s => s.remove());
      const label = tmp.textContent.replace(/[▲▼]/g, '').trim();
      cth.dataset.sortKey = th.dataset.sortKey || '';

      if (cth.dataset.sortKey) {
        cth.style.cursor = 'pointer';
        // use a button-ish clickable area
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-link p-0 text-start text-nowrap';
        btn.style.textDecoration = 'none';
        btn.style.cursor = 'pointer';
        btn.appendChild(document.createTextNode(label + ' '));
        btn.addEventListener('click', () => this.setSort(cth.dataset.sortKey));
        cth.appendChild(btn);
      } else {
        cth.appendChild(document.createTextNode(label || ''));
      }

      cloneRow.appendChild(cth);
    });

    cloneHead.appendChild(cloneRow);
    cloneTable.appendChild(cloneHead);
    track.appendChild(cloneTable);
    host.appendChild(track);

    // host should match visible width of scroll wrapper
    host.style.width = `${Math.ceil(wrap.getBoundingClientRect().width)}px`;

    // sync horizontal scroll
    const syncX = () => {
      track.style.transform = `translateX(${-(wrap.scrollLeft || 0)}px)`;
    };
    syncX();
    this._syncStickyHeaderX = syncX;

    // sync visibility = show clone only after the real header scrolls past
    const syncVisibility = () => {
      const headerTop = thead.getBoundingClientRect().top;
      const hostTop   = host.getBoundingClientRect().top;
      host.classList.toggle('fp-hidden', !(headerTop < hostTop));
    };
    syncVisibility();
    this._syncStickyHeaderVisibility = syncVisibility;

    // add the current sort arrow to the proper column
    this._updateCloneSortIndicators();
  }

  // ===== modal charts =====================================================
  constructor(...args) {
    super(...args);
    this._charts = {};
    this._modalPlaceholder = null;
    this._modalPortaled = false;
  }

  renderBarChart(canvas, series, title) {
    if (!canvas || !series) return;
    const id = canvas.id || Math.random().toString(36).slice(2);
    canvas.id = id;

    if (this._charts?.[id]) {
      try { this._charts[id].destroy(); } catch(e) {}
    } else {
      this._charts = this._charts || {};
    }

    const labelsRaw = series.map(p => p.period);
    const labelsFmt = labelsRaw.map(p => this.formatPeriodLabel(p));
    const values    = series.map(p => p.value);

    // eslint-disable-next-line no-undef
    this._charts[id] = new window.Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: labelsFmt,
        datasets: [{ label: title, data: values, borderWidth: 0 }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          title:  { display: false },
          tooltip: {
            enabled: true,
            callbacks: {
              title: (items) => items?.length ? items[0].label : '',
              label: (ctx) => `${title}: ${ctx.parsed.y}`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false, drawBorder: false },
            ticks: {
              callback: (val, idx) => labelsFmt[idx],
            },
          },
          y: {
            grid: { display: false, drawBorder: false },
            ticks: { display: true },
          },
        },
        layout: { padding: 0 },
      },
    });
  }

  // ===== OWL setup ========================================================
  setup() {
    this.state = useState({
      loading: true,
      data: {},
      productTypes: [],
      categories: [],
      risks: [
        { key: 1, label: '1' },
        { key: 2, label: '2' },
        { key: 3, label: '3' },
      ],
      timePeriods: ['This Week','Last Week','Last Month','Last 3 Months'],

      // filters
      selectedProducts: [],
      selectedScores: [],
      selectedCategories: [],
      selectedRisks: [1, 2, 3],
      selectedIssues: [],
      selectedPeriod: 'Last Week',
      startDate: '', endDate: '',
      draftSelectedProducts: [],
      draftSelectedScores: [],
      draftSelectedCategories: [],
      draftSelectedIssues: [],
      draftSelectedRisks: [1, 2, 3],
      draftSelectedPeriod: 'Last Week',
      draftStartDate: '',
      draftEndDate: '',

      // UI
      infoText:
        'Priority is computed from the PRIOR WEEK (Mon-Sun), accounting for a 6-day workweek.\n' +
        '1: avg trips/day ≤ 1 OR avg hours/day ≤ 1.\n' +
        '2: avg trips/day ≤ 2 OR avg hours/day ≤ 2.\n' +
        '3: 2 < avg trips/day < 5 OR 2 < avg hours/day < 5.',
      showProducts: false, showScores: false, showCategories: false,
      showRisks: false, showIssues: false, showPeriod: false,

      search: '',
      sortKey: 'name', sortDir: 'asc',
      pageSize: 100, page: 1,

      // modal
      showModal: false, modalDriver: null, showCharts: false,
    });

    // refs for sticky layers + table
    this.filtersBarRef = useRef('filtersBar');
    this.searchBarRef  = useRef('searchBar');
    this.tableWrapRef  = useRef('tableWrap');
    this.tableRef      = useRef('driversTable');
    this.stickyHostRef = useRef('stickyHeader');
    this.modalRef      = useRef('lp_modal');

    // lifecycle
    onMounted(async () => {
      // load filters from backend
      const { product_types, categories, risks, time_periods } =
        await this.env.services.rpc('/fleet_low_performers/filters', {});
      this.state.productTypes = product_types;
      this.state.categories   = categories;
      this.state.risks        = risks || this.state.risks; // expects numeric keys (1/2/3)
      this.state.timePeriods  = time_periods || this.state.timePeriods;

      // default: include most categories except archive/new/churn/other
      const lower = (s) => (s || '').toString().trim().toLowerCase();
      this.state.selectedCategories = categories.filter(c => {
        const x = lower(c);
        return x !== 'archive' && x !== 'new' && x !== 'churn' && x !== 'other';
      });

      // fetch table data
      await this._fetchData();

      // sync drafts to current live filters
      this.state.draftSelectedProducts   = [...this.state.selectedProducts];
      this.state.draftSelectedScores     = [...this.state.selectedScores];
      this.state.draftSelectedCategories = [...this.state.selectedCategories];
      this.state.draftSelectedRisks      = [...this.state.selectedRisks];
      this.state.draftSelectedIssues     = [...this.state.selectedIssues];
      this.state.draftSelectedPeriod     = this.state.selectedPeriod;
      this.state.draftStartDate          = this.state.startDate;
      this.state.draftEndDate            = this.state.endDate;

      // first measure and build sticky clone
      this._recomputeStickyHeights();
      this._buildStickyHeader();
      this._attachStickyListeners();

      // resize listener so sticky offsets + header widths recalc on resize
      this._onResizeWin = () => {
        this._recomputeStickyHeights();
        this._buildStickyHeader();
        this._attachStickyListeners();
      };
      window.addEventListener('resize', this._onResizeWin, { passive: true });
    });

    onPatched(() => {
      // every patch: keep sticky stuff in sync
      this._recomputeStickyHeights();
      this._buildStickyHeader();
      this._attachStickyListeners();
      this._updateCloneSortIndicators();

      // modal charts (if user is looking at charts tab)
      if (this.state.showModal && this.state.showCharts && this.state.modalDriver?.series) {
        const host = (this.modalRef && this.modalRef.el) || document;
        const s = this.state.modalDriver.series;
        this.renderBarChart(host.querySelector('#lp_chart_cash'),   s.cashEarned,      'Gross Revenue');
        this.renderBarChart(host.querySelector('#lp_chart_trips'),  s.trips,           'Trips');
        this.renderBarChart(host.querySelector('#lp_chart_hours'),  s.supplyHours,     'Hours Online');
        this.renderBarChart(host.querySelector('#lp_chart_acc'),    s.acceptanceRate,  'Acceptance Rate %');
        this.renderBarChart(host.querySelector('#lp_chart_comp'),   s.completionRate,  'Completion Rate %');
        this.renderBarChart(this.el.querySelector('#lp_chart_trph'), s.tripsPerHour, 'Trips per Hour');
        this.renderBarChart(this.el.querySelector('#lp_chart_cbd'),  s.cancelledByDriverPct, 'Cancelled by Driver %');
      }

      // Manual portal: if modal is open and not yet portaled, move it to <body>
      if (this.state.showModal && this.modalRef?.el && !this._modalPortaled) {
        const el = this.modalRef.el;
        if (!this._modalPlaceholder) {
          this._modalPlaceholder = document.createComment('lp-modal-anchor');
          el.parentNode && el.parentNode.insertBefore(this._modalPlaceholder, el);
        }
        document.body.appendChild(el);
        this._modalPortaled = true;
        try { document.body.classList.add('lp-modal-open'); } catch(e) {}
      }
      // If modal is closed but still portaled, restore it
      if (!this.state.showModal && this._modalPortaled && this._modalPlaceholder) {
        const el = this.modalRef?.el;
        if (el && this._modalPlaceholder.parentNode) {
          this._modalPlaceholder.parentNode.insertBefore(el, this._modalPlaceholder);
        }
        this._modalPortaled = false;
        try { document.body.classList.remove('lp-modal-open'); } catch(e) {}
      }

    });

    onWillUnmount(() => {
      // clean scroll listeners
      if (this._scrollTarget && this._onRootScroll) {
        this._scrollTarget.removeEventListener('scroll', this._onRootScroll);
      }
      if (this._wrapTarget && this._onWrapScroll) {
        this._wrapTarget.removeEventListener('scroll', this._onWrapScroll);
      }
      // clean resize
      if (this._onResizeWin) {
        window.removeEventListener('resize', this._onResizeWin);
      }
      // destroy charts
      Object.values(this._charts).forEach(ch => { try { ch.destroy(); } catch(e){} });

      // Restore modal to original place if still portaled (safety)
      if (this._modalPortaled && this.modalRef?.el && this._modalPlaceholder?.parentNode) {
        this._modalPlaceholder.parentNode.insertBefore(this.modalRef.el, this._modalPlaceholder);
        this._modalPortaled = false;
      }
      try { document.body.classList.remove('lp-modal-open'); } catch(e) {}
    });
  }

  // ===== data fetch =======================================================
  async _fetchData() {
    this.state.loading = true;

    const params = {
      period:     this.state.selectedPeriod,
      products:   this.state.selectedProducts,
      scores:     this.state.selectedScores,
      categories: this.state.selectedCategories,
      risks:      this.state.selectedRisks,
      start_date: this.state.startDate || undefined,
      end_date:   this.state.endDate   || undefined,
    };

    const resp = await this.env.services.rpc('/fleet_low_performers/data', params);
    this.state.data    = resp.data || {};
    this.state.meta    = resp.meta || null;

    if (this.state.meta?.table_from) this.state.startDate = this.state.meta.table_from;
    if (this.state.meta?.table_to)   this.state.endDate   = this.state.meta.table_to;

    if (resp.range) {
      this.state.startDate = resp.range.start || this.state.startDate;
      this.state.endDate   = resp.range.end   || this.state.endDate;
    }

    this.state.loading = false;
  }

  // ===== filters / dropdowns / inputs =====================================
  async applyFilters() {
    this.state.selectedProducts    = [...this.state.draftSelectedProducts];
    this.state.selectedScores      = [...this.state.draftSelectedScores];
    this.state.selectedCategories  = [...this.state.draftSelectedCategories];
    this.state.selectedRisks       = [...this.state.draftSelectedRisks];
    this.state.selectedIssues      = [...this.state.draftSelectedIssues];
    this.state.selectedPeriod      = this.state.draftSelectedPeriod;
    this.state.startDate           = this.state.draftStartDate;
    this.state.endDate             = this.state.draftEndDate;
    this.state.page = 1;

    await this._fetchData();

    // sync drafts with applied
    this.state.draftSelectedProducts   = [...this.state.selectedProducts];
    this.state.draftSelectedScores     = [...this.state.selectedScores];
    this.state.draftSelectedCategories = [...this.state.selectedCategories];
    this.state.draftSelectedRisks      = [...this.state.selectedRisks];
    this.state.draftSelectedIssues     = [...this.state.selectedIssues];
    this.state.draftSelectedPeriod     = this.state.selectedPeriod;
    this.state.draftStartDate          = this.state.startDate;
    this.state.draftEndDate            = this.state.endDate;

    // after data reload: recompute sticky + rebuild + rebind
    this._recomputeStickyHeights();
    this._buildStickyHeader();
    this._attachStickyListeners();

    // 1 more rebuild on next tick to catch final layout after render
    setTimeout(() => {
      this._buildStickyHeader && this._buildStickyHeader();
    }, 0);
  }

  toggleDropdown(flagName) {
    this.state[flagName] = !this.state[flagName];
    // after dropdown open/close, recalc sticky stuff
    setTimeout(() => {
      this._recomputeStickyHeights();
      this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    }, 0);
  }

  toggleFilter(listName, value, isDraft = true) {
    const key = isDraft
      ? `draft${listName[0].toUpperCase()}${listName.slice(1)}`
      : listName;
    if (!Array.isArray(this.state[key])) this.state[key] = [];
    const list = this.state[key];
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
    this.state.page = 1;
  }

  changePeriod(p) {
    this.state.draftSelectedPeriod = p;
    this.state.showPeriod = false;
    this.state.page = 1;
    this.state.draftStartDate = '';
    this.state.draftEndDate   = '';
  }

  onStartDateChange(ev) {
    this.state.draftStartDate = ev.target.value;
    this.state.draftSelectedPeriod = 'Custom Range';
    this.state.page = 1;
  }

  onEndDateChange(ev) {
    this.state.draftEndDate = ev.target.value;
    this.state.draftSelectedPeriod = 'Custom Range';
    this.state.page = 1;
  }

  onSearchChange(ev) {
    this.state.search = ev.target.value || '';
    this.state.page = 1;
  }

  // ===== modal logic ======================================================
  async openDriver(d) {
    this.state.modalDriver = { ...d, cards: null, series: null, issues: [] };
    this.state.showModal   = true;
    this.state.showCharts  = false;

    // Stop sticky header machinery while modal is open
    this._detachStickyListeners();
    try { document.body.classList.add('lp-modal-open'); } catch(e) {}

    const meta  = this.state.meta || {};
    const start = meta.table_from;
    const end   = meta.table_to;

    const res = await this.env.services.rpc('/fleet_low_performers/driver_detail', {
      driver_id: d.id, start_date: start, end_date: end,
    });
    this.state.modalDriver.cards  = res.cards  || null;
    this.state.modalDriver.series = res.series || null;
    this.state.modalDriver.issues = res.issues || [];
    this.state.modalDriver.metrics_range  = res.metrics_range || null;
    this.state.modalDriver.call_notes = res.call_notes || [];
    this.state.modalDriver.notes_range = res.notes_range || null;
    this.state.modalDriver.issues_range = res.issues_range || null;
    this.state.modalDriver._composeNoteOpen = false;
    this.state.modalDriver._composeNoteText = '';
  }

  _setModalDriver(patch) {
    // shallow immutable update to force re-render
    this.state.modalDriver = { ...(this.state.modalDriver || {}), ...patch };
  }

  openComposeNote() {
    if (!this.state.modalDriver) return;
    this._setModalDriver({
      _composeNoteOpen: true,
      _composeNoteText: '',
    });
  }

  onComposeText(ev) {
    const v = ev?.target?.value ?? '';
    if (!this.state.modalDriver) return;
    this._setModalDriver({ _composeNoteText: v });
  }

  cancelComposeNote() {
    if (!this.state.modalDriver) return;
    this._setModalDriver({
      _composeNoteOpen: false,
      _composeNoteText: '',
    });
  }

  async saveComposeNote() {
    const md = this.state.modalDriver;
    const txt = (md?._composeNoteText || '').trim();
    if (!md || !txt) return;

    // 1) Create on server
    const res = await this.env.services.rpc('/fleet_call_notes/create', {
      driver_id: md.id,
      note: txt,
    });

    const newRow = res?.note;
    if (newRow) {
      // 2) Optimistic + immutable update (newest first)
      const nextNotes = [newRow, ...(md.call_notes || [])];
      this._setModalDriver({
        call_notes: nextNotes,
        _composeNoteOpen: false,
        _composeNoteText: '',
      });
    } else {
      // still close composer even if server didn't return a row
      this._setModalDriver({
        _composeNoteOpen: false,
        _composeNoteText: '',
      });
    }
    try {
      const start = this.state.modalDriver?.notes_range?.start;
      const end   = this.state.modalDriver?.notes_range?.end;
      const ref = await this.env.services.rpc('/fleet_call_notes/list', {
        driver_id: md.id, start_date: start, end_date: end,
      });
      this._setModalDriver({ call_notes: ref?.call_notes || [] });
    } catch(e) {
      // ignore, the optimistic update already updated the UI
    }
  }

  closeModal() {
    this.state.showModal = false;
    this.state.modalDriver = null;

    // Re-enable sticky header machinery
    try { document.body.classList.remove('lp-modal-open'); } catch(e) {}
    this._attachStickyListeners();   // rebuild after the modal goes away
    // one extra rebuild on next tick for good measure
    setTimeout(() => {
      this._buildStickyHeader && this._buildStickyHeader();
    }, 0);
  }

  _drawChartsOnce() {
    const host = this.modalRef?.el;
    const s = this.state.modalDriver?.series;
    if (!host || !s) return;

    const sel = (id) => host.querySelector(id);
    const ensureFreshCanvas = (canvas) => {
      if (!canvas) return null;
      try {
        // hard reset: resets internal state + clears previous drawing buffer
        canvas.width = canvas.clientWidth || canvas.width || 600;
        canvas.height = canvas.clientHeight || 160;
        // also nuke any lingering chart for this canvas id
        const id = canvas.id;
        if (id && this._charts && this._charts[id]) {
          try { this._charts[id].destroy(); } catch(e) {}
          delete this._charts[id];
        }
      } catch(e) {}
      return canvas;
    };

    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_cash')),   s.cashEarned,      'Gross Revenue');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_trips')),  s.trips,           'Trips');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_hours')),  s.supplyHours,     'Hours Online');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_acc')),    s.acceptanceRate,  'Acceptance Rate %');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_comp')),   s.completionRate,  'Completion Rate %');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_trph')), s.tripsPerHour, 'Trips per Hour');
    this.renderBarChart(ensureFreshCanvas(sel('#lp_chart_cbd')),  s.cancelledByDriverPct, 'Cancelled by Driver %');
  }

  toggleModalView() {
    this.state.showCharts = !this.state.showCharts;

    // Leaving Charts → destroy all instances
    if (!this.state.showCharts) {
      Object.values(this._charts || {}).forEach(ch => { try { ch.destroy(); } catch(e) {} });
      this._charts = {};
      return;
    }

    // Entering Charts → wait for DOM/layout to settle, then draw
    // 1st rAF: DOM applied; 2nd rAF: layout finalized; then render
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        this._drawChartsOnce();
      });
    });
  }
}

OwlLowPerformersDashboard.template = 'owl.OwlLowPerformersDashboard';
registry.category('actions').add('owl.low_performers_dashboard', OwlLowPerformersDashboard);
