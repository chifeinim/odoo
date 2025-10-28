/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, onWillUnmount, useState, useRef } = hooks;
import { registry } from '@web/core/registry';

export class OwlPerformanceDashboard extends Component {
  formatNumber(value, digits = 0) {
    const n = Number(value);
    if (Number.isNaN(n)) return value ?? '';
    return new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  }

  digits(s) { return (s || '').replace(/\D/g, ''); }

  periodButtonLabel() {
    const p = this.state.draftSelectedPeriod || '';
    if (!p) return 'Date';
    if (p === 'Custom Range') return 'Custom Range';
    return p;
  }

  // format labels for per-driver charts in the modal (23-Sep style)
  formatPeriodLabel(p) {
    // Accepts 'YYYY-MM-DD' (daily) or 'YYYY-MM' (monthly). Falls back to raw.
    const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(String(p || ''));
    if (!m) return p;
    const y = +m[1], mo = +m[2], d = m[3] ? +m[3] : 1;
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mon = months[mo - 1] || '';
    return m[3] ? `${d}-${mon}` : `${mon}-${y}`;   // daily -> 23-Sep, monthly -> Sep-2025
  }

  // === sticky helpers ======================================================
  _getScrollContainer(el) {
    // find nearest scrollable ancestor for sticky header visibility calc
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

    const scroller = this._getScrollContainer(host);

    // detach old
    if (this._scrollTarget && this._onRootScroll) {
      this._scrollTarget.removeEventListener('scroll', this._onRootScroll);
    }
    if (this._wrapTarget && this._onWrapScroll) {
      this._wrapTarget.removeEventListener('scroll', this._onWrapScroll);
    }

    // bind new
    this._onRootScroll = () =>
      this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    this._onWrapScroll = () =>
      this._syncStickyHeaderX && this._syncStickyHeaderX();

    this._scrollTarget = scroller;
    this._wrapTarget   = wrap;

    scroller.addEventListener('scroll', this._onRootScroll, { passive: true });
    if (wrap) wrap.addEventListener('scroll', this._onWrapScroll, { passive: true });

    // run immediately
    this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    this._syncStickyHeaderX && this._syncStickyHeaderX();
  }

  qualityClass(score) {
    const s = (score || '').toString().trim().toLowerCase();
    if (s === 'low performer') return 'text-danger font-weight-bold fw-bold';
    if (s === 'average performer') return 'text-warning font-weight-bold fw-bold';
    if (s === 'high performer') return 'text-success font-weight-bold fw-bold';
    return '';
  }

  qualityBgStyle(score) {
    const s = (score || '').toString().trim().toLowerCase();
    const common = 'color:#000; font-weight:600;';
    if (s === 'low performer') return `${common} background-color: var(--bs-danger-bg-subtle, #f8d7da);`;
    if (s === 'average performer') return `${common} background-color: var(--bs-warning-bg-subtle, #fff3cd);`;
    if (s === 'high performer') return `${common} background-color: var(--bs-success-bg-subtle, #d4edda);`;
    return '';
  }

  setSort(key) {
    if (this.state.sortKey === key) {
      this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.state.sortKey = key;
      this.state.sortDir = 'asc';
    }
    this.state.page = 1;

    // also refresh the clone header arrows
    this._updateCloneSortIndicators && this._updateCloneSortIndicators();
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
      this._isSameArray(this.state.draftSelectedProducts, this.state.selectedProducts) &&
      this._isSameArray(this.state.draftSelectedScores, this.state.selectedScores) &&
      this._isSameArray(this.state.draftSelectedCategories, this.state.selectedCategories) &&
      this.state.draftSelectedPeriod === this.state.selectedPeriod &&
      this.state.draftStartDate === this.state.startDate &&
      this.state.draftEndDate === this.state.endDate
    );
  }

  applyFilters = async () => {
    this.state.selectedProducts   = [...this.state.draftSelectedProducts];
    this.state.selectedScores     = [...this.state.draftSelectedScores];
    this.state.selectedCategories = [...this.state.draftSelectedCategories];
    this.state.selectedPeriod     = this.state.draftSelectedPeriod;

    if (this.state.draftSelectedPeriod === 'Custom Range') {
      this.state.startDate = this.state.draftStartDate;
      this.state.endDate   = this.state.draftEndDate;
    } else {
      this.state.startDate = '';
      this.state.endDate   = '';
    }

    await this._fetchData();

    this.state.draftSelectedProducts   = [...this.state.selectedProducts];
    this.state.draftSelectedScores     = [...this.state.selectedScores];
    this.state.draftSelectedCategories = [...this.state.selectedCategories];
    this.state.draftSelectedPeriod     = this.state.selectedPeriod;
    this.state.draftStartDate          = this.state.startDate;
    this.state.draftEndDate            = this.state.endDate;

    this._recomputeStickyHeights && this._recomputeStickyHeights();
    this._buildStickyHeader && this._buildStickyHeader();
    this._attachStickyListeners();

    setTimeout(() => this._buildStickyHeader && this._buildStickyHeader(), 0);
  };

  // comparator for sorting table rows
  _compareByKey(a, b, key) {
    const ax = a?.[key];
    const bx = b?.[key];

    const norm = (v) => {
      if (v === undefined || v === null) return null;
      if (typeof v === 'boolean') return v ? 1 : 0;
      if (typeof v === 'number') return v;
      if (typeof v === 'string') return v.trim();
      return v;
    };

    let av = norm(ax);
    let bv = norm(bx);

    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;

    const looksLikeDate = key === 'hire_date' ||
      (/^\d{4}-\d{2}-\d{2}/.test(String(av)) && /^\d{4}-\d{2}-\d{2}/.test(String(bv)));
    if (looksLikeDate) {
      const ta = Date.parse(av);
      const tb = Date.parse(bv);
      if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb;
    }

    if (typeof av === 'number' && typeof bv === 'number') {
      return av - bv;
    }
    if (typeof av === 'number' && typeof bv !== 'number') return -1;
    if (typeof av !== 'number' && typeof bv === 'number') return 1;

    return String(av)
      .toLocaleLowerCase()
      .localeCompare(
        String(bv).toLocaleLowerCase(),
        undefined,
        { numeric: true, sensitivity: 'base' }
      );
  }

  // data for main table
  sortedFilteredDrivers() {
    const q  = (this.state.search || '').trim().toLowerCase();
    const qd = this.digits(q);

    const rows = Object.values(this.state.data || {}).filter(d => {
      const nameHit  = (d.name || '').toLowerCase().includes(q);
      const phoneHit = qd ? this.digits(d.phone).includes(qd) : false;
      return q ? (nameHit || phoneHit) : true;
    });

    const dir = this.state.sortDir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const res = this._compareByKey(a, b, this.state.sortKey);
      if (res !== 0) return dir * res;
      return this._compareByKey(a, b, 'name');
    });
    return rows;
  }

  _buildDistributions(rows) {
    const countBy = (key) => {
      const m = new Map();
      for (const r of rows) {
        const label = (r?.[key] ?? '').toString() || 'Unknown';
        m.set(label, (m.get(label) || 0) + 1);
      }
      return Array.from(m, ([label, value]) => ({ label, value }));
    };

    return {
      quality:  countBy('quality_score'),
      product:  countBy('product_type'),
      category: countBy('type'),
    };
  }

  _filteredRows() {
    const rows = Object.values(this.state.data || {});
    const q  = (this.state.search || '').trim().toLowerCase();
    if (!q) return rows;
    const qd = this.digits(q);

    return rows.filter(d => {
      const nameHit  = (d.name || '').toLowerCase().includes(q);
      const phoneHit = qd ? this.digits(d.phone).includes(qd) : false;
      return nameHit || phoneHit;
    });
  }

  pagedDrivers() {
    const rows = this.sortedFilteredDrivers();
    const pc = Math.max(1, Math.ceil(rows.length / this.state.pageSize));
    if (this.state.page > pc) this.state.page = pc;
    const start = (this.state.page - 1) * this.state.pageSize;
    return rows.slice(start, start + this.state.pageSize);
  }

  pageWindow() {
    const pc = this.pageCount();
    const p  = this.state.page;
    const nums = new Set(
      [1, pc, p-2, p-1, p, p+1, p+2].filter(x => x >= 1 && x <= pc)
    );
    const arr = [...nums].sort((a,b)=>a-b);
    const out = [];
    for (let i = 0; i < arr.length; i++) {
      out.push(arr[i]);
      if (i < arr.length - 1 && arr[i+1] !== arr[i] + 1) out.push('…');
    }
    return out;
  }

  totalDriverCount() {
    return this.sortedFilteredDrivers().length;
  }

  pageCount() {
    return Math.max(1, Math.ceil(this.sortedFilteredDrivers().length / this.state.pageSize));
  }

  goToPage(p) {
    const pc = this.pageCount();
    this.state.page = Math.min(pc, Math.max(1, p));
  }

  prevPage = () => this.goToPage(this.state.page - 1);
  nextPage = () => this.goToPage(this.state.page + 1);

  // === per-driver modal helpers ============================================

  renderBarChart(canvas, series, title) {
    if (!canvas || !series) return;
    const id = canvas.id || Math.random().toString(36).slice(2);
    canvas.id = id;

    // destroy if it already exists with same id
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
              label: (ctx)   => `${title}: ${ctx.parsed.y}`,
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

  async openDriver(d) {
    // open modal with skeleton data
    this.state.modalDriver = {
      ...d,
      cards: null,
      series: null,
      issues: [],
    };
    this.state.showModal = true;
    this.state.modalShowCharts = false;

    // ask backend for details in the currently selected date window
    // Reuse the low performers endpoint shape:
    //   { cards: {...}, series: {...}, issues: [...] }
    // If you prefer, expose a new route like /fleet_partner_performance/driver_detail
    // that returns the same structure.
    const res = await this.env.services.rpc('/fleet_low_performers/driver_detail', {
      driver_id: d.id,
      start_date: this.state.startDate || undefined,
      end_date:   this.state.endDate   || undefined,
    });

    this.state.modalDriver.cards  = res.cards || null;
    this.state.modalDriver.series = res.series || null;
    this.state.modalDriver.issues = res.issues || [];
  }

  closeModal() {
    this.state.showModal = false;
    this.state.modalDriver = null;

    // clean up modal charts
    Object.entries(this._charts).forEach(([id,ch]) => {
      if (id && id.startsWith('lp_chart_')) {
        try { ch.destroy(); } catch(e){}
        delete this._charts[id];
      }
    });
  }

  toggleModalView() {
    this.state.modalShowCharts = !this.state.modalShowCharts;
    if (!this.state.modalShowCharts) {
      // going back to cards — nuke ONLY the modal's charts
      Object.entries(this._charts).forEach(([id,ch]) => {
        if (id && id.startsWith('lp_chart_')) {
          try { ch.destroy(); } catch(e){}
          delete this._charts[id];
        }
      });
    }
  }

  // === dashboard-wide charts (existing code) ===============================
  _renderDistributionCharts() {
    const makePie = (ctx, title, dist, colors, chartKey) => {
      if (this._charts[chartKey]) this._charts[chartKey].destroy();
      const labels = dist.map(d => d.label);
      const data   = dist.map(d => d.value);

      this._charts[chartKey] = new Chart(ctx, {
        type: 'pie',
        data: {
          labels,
          datasets: [{ label: title, data, backgroundColor: colors.slice(0, labels.length) }],
        },
        options: {
          maintainAspectRatio: false,
          plugins: {
            title: { display: true, text: title, padding: { top: 6, bottom: 6 } },
            legend: { position: 'right', labels: { boxWidth: 12, padding: 12 } },
            tooltip: {
              callbacks: {
                label: (c) => {
                  const val   = c.raw ?? 0;
                  const total = c.dataset.data.reduce((a,b)=>a+b,0) || 1;
                  const pct   = ((val/total)*100).toFixed(1);
                  return `${c.label}: ${val} (${pct}%)`;
                },
              },
            },
          },
        },
      });
    };

    const rows = this._filteredRows();
    const { product, quality, category } = this._buildDistributions(rows);

    const prodColors = ['#FF6384','#36A2EB','#FFCE56','#8E44AD','#2ECC71','#E67E22'];
    const qualColors = ['#4BC0C0','#9966FF','#FF9F40'];
    const catColors  = ['#E7E9ED','#3CBA9F','#F7464A','#46F0F0','#F39C12','#7F8C8D'];

    const qCtx = this.chartQuality.el?.getContext('2d');
    const pCtx = this.chartProduct.el?.getContext('2d');
    const cCtx = this.chartCategory.el?.getContext('2d');
    if (qCtx) makePie(qCtx, 'Drivers by Quality',  quality,  qualColors, 'qualityPie');
    if (pCtx) makePie(pCtx, 'Drivers by Product',  product,  prodColors, 'productPie');
    if (cCtx) makePie(cCtx, 'Drivers by Category', category, catColors,  'categoryPie');
  }

  _renderCharts() {
    // build a Chart.js config for one metric series
    // `seriesData` is [{ period: '2025-10-01', value: 123 }, ...]
    const cfg = (label, seriesData) => {
      const vals = Array.isArray(seriesData)
        ? seriesData.map(pt => (typeof pt.value === 'number' ? pt.value : 0))
        : [];

      // compute min/max from the data
      let minVal = null;
      let maxVal = null;
      for (const v of vals) {
        if (minVal === null || v < minVal) minVal = v;
        if (maxVal === null || v > maxVal) maxVal = v;
      }

      // sane fallbacks if we have no data
      if (minVal === null) minVal = 0;
      if (maxVal === null) maxVal = 0;

      const span = maxVal - minVal;
      const mid  = (maxVal + minVal) / 2;

      let suggestedMin, suggestedMax;
      if (span === 0) {
        // flat series (e.g. all zeros or all same %) -> give ±10% band
        const pad = (Math.abs(maxVal) || 1) * 0.1;
        suggestedMin = maxVal - pad;
        suggestedMax = maxVal + pad;
      } else {
        // double the original range, centered around the midpoint
        // original range = span
        // doubled range = 2 * span
        // so half-range for each side = span
        suggestedMin = mid - span;
        suggestedMax = mid + span;
      }

      const labelsRaw = Array.isArray(seriesData)
        ? seriesData.map(pt => pt.period)
        : [];
      const values    = vals;

      return {
        type: 'line',
        data: {
          labels: labelsRaw,
          datasets: [{
            label,
            data: values,
            fill: false,
          }],
        },
        options: {
          scales: {
            x: {
              display: true,
              grid: { display: false, drawBorder: false },
              ticks: {
                autoSkip: true,
                maxTicksLimit: 8,
                callback: function (value) {
                  // format YYYY-MM-DD -> "DD-Mon"
                  const raw = this.getLabelForValue
                    ? this.getLabelForValue(value)
                    : value;
                  const m = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})/);
                  if (!m) return raw;
                  const months = [
                    'Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec'
                  ];
                  return `${m[3]}-${months[+m[2]-1]}`;
                },
              },
            },
            y: {
              display: true,
              grid: { display: false, drawBorder: true },
              ticks: {
                autoSkip: true,
                maxTicksLimit: 6,
              },
              // here's the new "double the range around the midpoint"
              suggestedMin,
              suggestedMax,
            },
          },
          plugins: {
            title:  { display: true, text: label },
            legend: { display: false },
          },
          elements: {
            point: { radius: 2, hitRadius: 8 },
          },
          maintainAspectRatio: false,
        },
      };
    };

    const ctx = (ref) => (ref && ref.el) ? ref.el.getContext('2d') : null;

    // destroy existing dashboard-level charts only
    [
      'active','trips','supply','cash','moneyPerHour','tripsPerHour',
      'avgSupplyHoursPerDriver','tripsPerActiveDriver','acceptanceRate',
      'cancelledByDriverPct','cancelledByCustomerPct','cancelledDueToNetworkPct',
      'completedToRequest','completionRate','serviceFee','partnerFee'
    ].forEach(k => {
      if (this._charts[k]) {
        try { this._charts[k].destroy(); } catch(e) {}
        delete this._charts[k];
      }
    });

    // same chart list/order you have now in the template
    const charts = [
      // row 1
      ['active', ctx(this.chartActive), 'Active Drivers', this.state.series.activeDrivers],
      ['trips', ctx(this.chartTrips), 'Total Trips', this.state.series.trips],
      ['supply', ctx(this.chartSupply), 'Supply Hours', this.state.series.supplyHours],

      // row 2
      ['cash', ctx(this.chartCash), 'Gross Revenue', this.state.series.cashEarned],
      ['moneyPerHour', ctx(this.chartMoneyPerHour), 'Revenue / Hour', this.state.series.moneyPerHour],
      ['tripsPerHour', ctx(this.chartTripsPerHour), 'Trips / Hour', this.state.series.tripsPerHour],

      // row 3
      ['avgSupplyHoursPerDriver', ctx(this.chartAvgSupply), 'SH per Active Driver', this.state.series.avgSupplyHoursPerDriver],
      ['tripsPerActiveDriver', ctx(this.chartTripsPerActiveDriver), 'Trips per Active Driver', this.state.series.tripsPerActiveDriver],
      ['acceptanceRate', ctx(this.chartAcceptance), 'Acceptance Rate %', this.state.series.acceptanceRate],

      // row 4
      ['cancelledByDriverPct', ctx(this.chartCancelledByDriver), 'Cancelled by Driver %', this.state.series.cancelledByDriverPct],
      ['cancelledByCustomerPct', ctx(this.chartCancelledByCustomer), 'Cancelled by Customer %', this.state.series.cancelledByCustomerPct],
      ['cancelledDueToNetworkPct', ctx(this.chartCancelledDueToNetwork), 'Cancelled due to Network %', this.state.series.cancelledDueToNetworkPct],

      // row 5
      ['completedToRequest', ctx(this.chartCompletedToRequest), 'Completed to Request %', this.state.series.completedToRequest],
      ['completionRate', ctx(this.chartCompletionRate), 'Completion Rate %', this.state.series.completionRate],
      ['serviceFee', ctx(this.chartServiceFee), 'Service Fee', this.state.series.serviceFee],

      // row 6
      ['partnerFee', ctx(this.chartPartnerFee), 'Partner Fee', this.state.series.partnerFee],
    ];

    for (const [key, context, label, data] of charts) {
      if (!context) continue;
      this._charts[key] = new Chart(context, cfg(label, data));
    }
  }

  // filter dropdowns for dashboard
  toggleFilter(listName, value, isDraft = true) {
    const key = isDraft
      ? `draft${listName[0].toUpperCase()}${listName.slice(1)}`
      : listName;
    if (!Array.isArray(this.state[key])) this.state[key] = [];
    const list = this.state[key];
    const idx = list.indexOf(value);
    if (idx === -1) list.push(value);
    else            list.splice(idx, 1);
    this.state.page = 1;
  }

  changePeriod(p) {
    this.state.draftSelectedPeriod = p;
    this.state.showPeriod = false;
    this.state.page = 1;
    this.state.draftStartDate = '';
    this.state.draftEndDate   = '';
  }

  toggleDropdown(f) {
    this.state[f] = !this.state[f];
    setTimeout(() => {
      this._recomputeStickyHeights && this._recomputeStickyHeights();
      this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
    }, 0);
  }

  toggleView() {
    // this toggles dashboard cards vs dashboard charts (not the modal)
    this.state.showCharts = !this.state.showCharts;
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

  setup() {
    this.state = useState({
      loading: true,
      metrics: {},
      series: {
        activeDrivers: [],
        trips: [],
        supplyHours: [],
        cashEarned: [],
        moneyPerHour: [],
        tripsPerHour: [],
        avgSupplyHoursPerDriver: [],
        utilisation: [],
        efficiency: [],
        acceptanceRate: [],
        completedToRequest: [],
        completionRate: [],
        serviceFee: [],
        partnerFee: [],
        cancelledByDriverPct: [],
        // NEW:
        cancelledByCustomerPct: [],
        cancelledDueToNetworkPct: [],
        tripsPerActiveDriver: [],
      },
      allDrivers: 0,
      distributions: { product: [], quality: [], category: [] },
      productTypes: [],
      qualityScores: [
        { key: 'Low Performer',     label: 'Low Performer' },
        { key: 'Average Performer', label: 'Average Performer' },
        { key: 'High Performer',    label: 'High Performer' },
      ],
      categories: [],
      timePeriods: ['Last Week','Last Month','Last 3 Months'],

      selectedProducts: [],
      selectedScores: [],
      selectedCategories: [],
      selectedPeriod: 'Last Week',
      startDate: '',
      endDate: '',

      draftSelectedProducts: [],
      draftSelectedScores: [],
      draftSelectedCategories: [],
      draftSelectedPeriod: 'Last Week',
      draftStartDate: '',
      draftEndDate: '',

      showProducts: false,
      showScores: false,
      showCategories: false,
      showPeriod: false,

      showCharts: false, // dashboard cards vs dashboard charts
      search: '',
      sortKey: 'name',
      sortDir: 'asc',
      pageSize: 100,
      page: 1,

      // modal state
      showModal: false,
      modalDriver: null,
      modalShowCharts: false,
    });

    // sticky refs
    this.filtersBarRef = useRef('filtersBar');
    this.searchBarRef  = useRef('searchBar');
    this.tableWrapRef  = useRef('tableWrap');
    this.tableRef      = useRef('driversTable');
    this.stickyHostRef = useRef('stickyHeader');

    // modal ref
    this.modalRef      = useRef('perf_modal');

    // buildStickyHeader clone logic
    this._buildStickyHeader = () => {
      const table = this.tableRef.el;
      const wrap  = this.tableWrapRef.el;
      const host  = this.stickyHostRef.el;
      if (!table || !wrap || !host) return;

      const thead = table.querySelector('thead');
      const row   = thead && thead.querySelector('tr');
      if (!thead || !row) return;

      const ths = Array.from(row.children);
      const widths = ths.map(th => Math.ceil(th.getBoundingClientRect().width));

      host.innerHTML = '';
      const track = document.createElement('div');
      track.className = 'fp-sticky-track';

      const cloneTable = document.createElement('table');
      const cloneHead  = document.createElement('thead');
      const cloneRow   = document.createElement('tr');

      ths.forEach((th, i) => {
        const cth = document.createElement('th');

        // remove in-header arrows from source th before cloning text
        const tmp = th.cloneNode(true);
        tmp.querySelectorAll('span').forEach(s => s.remove());
        const label = tmp.textContent.replace(/[▲▼]/g, '').trim();

        const w = widths[i] || 80;
        cth.textContent = label;
        cth.title = label;
        cth.style.width = `${w}px`;
        cth.style.minWidth = `${w}px`;
        cth.style.maxWidth = `${w}px`;

        // carry the sort key
        cth.dataset.sortKey = th.dataset.sortKey || '';

        if (cth.dataset.sortKey) {
          cth.style.cursor = 'pointer';
          cth.addEventListener('click', () => this.setSort(cth.dataset.sortKey));
        }
        cloneRow.appendChild(cth);
      });

      cloneHead.appendChild(cloneRow);
      cloneTable.appendChild(cloneHead);
      track.appendChild(cloneTable);
      host.appendChild(track);

      host.style.width = `${Math.ceil(wrap.getBoundingClientRect().width)}px`;

      const syncX = () => {
        track.style.transform = `translateX(${- (wrap.scrollLeft || 0)}px)`;
      };
      const syncVisibility = () => {
        host.classList.toggle(
          'fp-hidden',
          !(thead.getBoundingClientRect().top < host.getBoundingClientRect().top)
        );
      };
      syncX();
      syncVisibility();
      this._syncStickyHeaderX = syncX;
      this._syncStickyHeaderVisibility = syncVisibility;

      // sync the arrow icon (only one arrow)
      this._updateCloneSortIndicators();
    };

    // ensure clone header shows only the active arrow
    this._updateCloneSortIndicators = () => {
      const host = this.stickyHostRef.el;
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
    };

    // write sticky CSS vars for offsets
    const recomputeStickyHeights = () => {
      const f = this.filtersBarRef.el;
      const s = this.searchBarRef.el;
      const fh = f ? f.getBoundingClientRect().height : 0;
      const sh = s ? s.getBoundingClientRect().height : 0;
      if (this.el) {
        this.el.style.setProperty('--sticky-filters-h', `${Math.ceil(fh)}px`);
        this.el.style.setProperty('--sticky-search-h',  `${Math.ceil(sh)}px`);
      }
    };
    this._recomputeStickyHeights = recomputeStickyHeights;

    // chart refs for dashboard charts
    this.chartActive = useRef('chartActive');
    this.chartTrips  = useRef('chartTrips');
    this.chartSupply = useRef('chartSupply');
    this.chartCash   = useRef('chartCash');
    this.chartMoneyPerHour = useRef('chartMoneyPerHour');
    this.chartTripsPerHour = useRef('chartTripsPerHour');
    this.chartTripsPerActiveDriver = useRef('chartTripsPerActiveDriver');
    this.chartAvgSupply = useRef('chartAvgSupply');
    this.chartUtilisation = useRef('chartUtilisation');
    this.chartEfficiency = useRef('chartEfficiency');
    this.chartAcceptance = useRef('chartAcceptance');
    this.chartCompletedToRequest = useRef('chartCompletedToRequest');
    this.chartCompletionRate = useRef('chartCompletionRate');
    this.chartServiceFee = useRef('chartServiceFee');
    this.chartPartnerFee = useRef('chartPartnerFee');
    this.chartQuality  = useRef('chartQuality');
    this.chartProduct  = useRef('chartProduct');
    this.chartCategory = useRef('chartCategory');
    this.chartCancelledByDriver = useRef('chartCancelledByDriver');
    this.chartCancelledByCustomer = useRef('chartCancelledByCustomer');
    this.chartCancelledDueToNetwork = useRef('chartCancelledDueToNetwork');

    this._charts = {};

    onMounted(async () => {
      // sticky sizing / listeners
      recomputeStickyHeights();
      this._onResize = () => recomputeStickyHeights();
      window.addEventListener('resize', this._onResize, { passive: true });

      const { product_types, categories } = await this.env.services.rpc(
        '/fleet_partner_performance/filters', {}
      );
      this.state.productTypes = product_types;
      this.state.categories   = categories;

      await this._fetchData();
      this.state.draftSelectedProducts   = [...this.state.selectedProducts];
      this.state.draftSelectedScores     = [...this.state.selectedScores];
      this.state.draftSelectedCategories = [...this.state.selectedCategories];
      this.state.draftSelectedPeriod     = this.state.selectedPeriod;
      this.state.draftStartDate          = this.state.startDate;
      this.state.draftEndDate            = this.state.endDate;

      recomputeStickyHeights();

      this._buildStickyHeader && this._buildStickyHeader();

      const root = this.el;              // vertical scroll
      const wrap = this.tableWrapRef.el; // horizontal scroll

      this._onRootScroll = () =>
        this._syncStickyHeaderVisibility && this._syncStickyHeaderVisibility();
      this._onWrapScroll = () =>
        this._syncStickyHeaderX && this._syncStickyHeaderX();

      this._onResize2    = () => {
        this._recomputeStickyHeights && this._recomputeStickyHeights();
        this._buildStickyHeader && this._buildStickyHeader();
        this._attachStickyListeners();
      };

      if (root) root.addEventListener('scroll', this._onRootScroll, { passive: true });
      if (wrap) wrap.addEventListener('scroll', this._onWrapScroll, { passive: true });
      window.addEventListener('resize', this._onResize2, { passive: true });
    });

    onPatched(() => {
      // keep sticky header happy
      this._recomputeStickyHeights && this._recomputeStickyHeights();
      this._buildStickyHeader && this._buildStickyHeader();
      this._attachStickyListeners();
      this._updateCloneSortIndicators && this._updateCloneSortIndicators();

      // dashboard charts
      if (this.state.showCharts && !this.state.loading) {
        this._renderDistributionCharts();
        this._renderCharts();
      }

      // per-driver modal charts (bar)
      if (
        this.state.showModal &&
        this.state.modalShowCharts &&
        this.state.modalDriver?.series
      ) {
        const s = this.state.modalDriver.series;
        this.renderBarChart(
          this.el.querySelector('#lp_chart_cash'),
          s.cashEarned,
          'Gross Revenue'
        );
        this.renderBarChart(
          this.el.querySelector('#lp_chart_trips'),
          s.trips,
          'Trips'
        );
        this.renderBarChart(
          this.el.querySelector('#lp_chart_hours'),
          s.supplyHours,
          'Hours Online'
        );
        this.renderBarChart(
          this.el.querySelector('#lp_chart_acc'),
          s.acceptanceRate,
          'Acceptance Rate %'
        );
        this.renderBarChart(
          this.el.querySelector('#lp_chart_comp'),
          s.completionRate,
          'Completion Rate %'
        );
      }
    });

    onWillUnmount(() => {
      if (this._scrollTarget && this._onRootScroll) {
        this._scrollTarget.removeEventListener('scroll', this._onRootScroll);
      }
      if (this._wrapTarget && this._onWrapScroll) {
        this._wrapTarget.removeEventListener('scroll', this._onWrapScroll);
      }
      window.removeEventListener('resize', this._onResize);
      window.removeEventListener('resize', this._onResize2);
    });
  }

  async _fetchData() {
    this.state.loading = true;
    const params = {
      period:     this.state.selectedPeriod,
      products:   this.state.selectedProducts,
      scores:     this.state.selectedScores,
      categories: this.state.selectedCategories,
      start_date: this.state.startDate || undefined,
      end_date:   this.state.endDate   || undefined,
    };
    const resp = await this.env.services.rpc('/fleet_partner_performance/data', params);
    this.state.metrics       = resp.metrics;
    this.state.series        = resp.series;
    this.state.data          = resp.data;
    this.state.allDrivers    = resp.allDrivers;
    this.state.distributions = resp.distributions;
    if (resp.range) {
      this.state.startDate = resp.range.start || '';
      this.state.endDate   = resp.range.end   || '';
    }
    this.state.loading = false;
  }
}

OwlPerformanceDashboard.template = 'owl.OwlPerformanceDashboard';
registry.category('actions')
  .add('owl.performance_dashboard', OwlPerformanceDashboard);
