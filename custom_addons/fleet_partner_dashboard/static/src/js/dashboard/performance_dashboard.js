/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, useState, useRef } = hooks;
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
  digits(s) {
    return (s || '').replace(/\D/g, '');
  }

  qualityClass(score) {
    const s = (score || '').toString().trim().toLowerCase();
    if (s === 'low performer') return 'text-danger font-weight-bold fw-bold';
    if (s === 'average performer') return 'text-warning font-weight-bold fw-bold';
    if (s === 'high performer') return 'text-success font-weight-bold fw-bold';
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
  }
  qualityBgStyle(score) {
    const s = (score || '').toString().trim().toLowerCase();
    // Prefer Bootstrap 5 “subtle” background tokens if present, with hex fallbacks
    const common = 'color:#000; font-weight:600;'; // keep text black & bold
    if (s === 'low performer') {
      return `${common} background-color: var(--bs-danger-bg-subtle, #f8d7da);`;
    }
    if (s === 'average performer') {
      return `${common} background-color: var(--bs-warning-bg-subtle, #fff3cd);`;
    }
    if (s === 'high performer') {
      return `${common} background-color: var(--bs-success-bg-subtle, #d4edda);`;
    }
    return '';
  }

  // robust comparator that handles numbers, strings, booleans, and ISO-ish dates
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

    // nulls last
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;

    // try date compare when key looks like a date field or value matches YYYY-MM-DD
    const looksLikeDate = key === 'hire_date' || (/^\d{4}-\d{2}-\d{2}/.test(String(av)) && /^\d{4}-\d{2}-\d{2}/.test(String(bv)));
    if (looksLikeDate) {
      const ta = Date.parse(av);
      const tb = Date.parse(bv);
      if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb;
    }

    // numeric compare
    if (typeof av === 'number' && typeof bv === 'number') {
      return av - bv;
    }

    // boolean already coerced to number above
    if (typeof av === 'number' && typeof bv !== 'number') return -1;
    if (typeof av !== 'number' && typeof bv === 'number') return 1;

    // string-ish compare (case/locale aware & numeric segments)
    return String(av).toLocaleLowerCase().localeCompare(String(bv).toLocaleLowerCase(), undefined, { numeric: true, sensitivity: 'base' });
  }

  // unified getter used by the template
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
    const nums = new Set([1, pc, p-2, p-1, p, p+1, p+2].filter(x => x >= 1 && x <= pc));
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

  setup() {
    this.state = useState({
      loading: true,
      metrics: {},
      series: {
        activeDrivers: [], trips: [], supplyHours: [],
        cashEarned: [], moneyPerHour: [], tripsPerHour: [],
        avgSupplyHoursPerDriver: [], utilisation: [], efficiency: [],
        acceptanceRate: [], completedToRequest: [], completionRate: [],
        serviceFee: [], partnerFee: [],
        cancelledByDriverPct: [],
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
      selectedProducts: [], selectedScores: [], selectedCategories: [], selectedPeriod: 'Last Week',
      startDate: '', endDate: '',
      showProducts: false, showScores: false, showCategories: false, showPeriod: false,
      showCharts: false, search: '', sortKey: 'name', sortDir: 'asc',
      pageSize: 100, page: 1,
    });
    this.chartActive = useRef('chartActive');
    this.chartTrips  = useRef('chartTrips');
    this.chartSupply = useRef('chartSupply');
    this.chartCash   = useRef('chartCash');
    this.chartMoneyPerHour = useRef('chartMoneyPerHour');
    this.chartTripsPerHour = useRef('chartTripsPerHour');
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
    
    this._charts     = {};

    onMounted(async () => {
      const { product_types, categories } = await this.env.services.rpc(
        '/fleet_partner_performance/filters', {}
      );
      this.state.productTypes = product_types;
      this.state.categories   = categories;
      await this._fetchData();
    });

    // whenever the DOM updates, if we're in chart mode, draw the charts
    onPatched(() => {
      if (this.state.showCharts && !this.state.loading) {
        this._renderDistributionCharts();
        this._renderCharts();
      }
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
    const resp = await this.env.services.rpc(
      '/fleet_partner_performance/data', params
    );
    this.state.metrics       = resp.metrics;
    this.state.series        = resp.series;
    this.state.data          = resp.data;
    this.state.allDrivers    = resp.allDrivers;
    this.state.distributions = resp.distributions;
    if (resp.range) {
      this.state.startDate = resp.range.start || '';
      this.state.endDate   = resp.range.end   || '';
    }
    this.state.loading       = false;
    // onPatched() will run next and draw charts if needed
  }

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

    // ★ derive from currently visible rows
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
    const cfg = (label, data) => ({
      type: 'line',
      data: {
        labels: data.map(pt => pt.period),
        datasets: [{ label, data: data.map(pt => pt.value), fill: false }],
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
                const raw = this.getLabelForValue ? this.getLabelForValue(value) : value;
                // simple DD-MMM shortener
                const m = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})/);
                if (!m) return raw;
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return `${m[3]}-${months[+m[2]-1]}`;
              },
            },
          },
          y: { display: true, grid: { display: false, drawBorder: true }, ticks: { autoSkip: true, maxTicksLimit: 6 } },
        },
        plugins: {
          title: { display: true, text: label },
          legend: { display: false },
        },
        elements: { point: { radius: 2, hitRadius: 8 } },
        maintainAspectRatio: false,
      },
    });

    const ctx = (ref) => (ref && ref.el) ? ref.el.getContext('2d') : null;

    // Destroy any existing charts
    [
      'active','trips','supply','cash','moneyPerHour','tripsPerHour',
      'avgSupplyHoursPerDriver','utilisation','efficiency',
      'acceptanceRate','completedToRequest','completionRate',
      'serviceFee','partnerFee','cancelledByDriverPct'
    ].forEach(k => { if (this._charts[k]) { this._charts[k].destroy(); delete this._charts[k]; } });

    // Define all potential charts with their refs + data
    const charts = [
      ['active', ctx(this.chartActive), 'Active Drivers', this.state.series.activeDrivers],
      ['trips', ctx(this.chartTrips), 'Total Trips', this.state.series.trips],
      ['supply', ctx(this.chartSupply), 'Supply Hours', this.state.series.supplyHours],
      ['cash', ctx(this.chartCash), 'Gross Revenue', this.state.series.cashEarned],
      ['moneyPerHour', ctx(this.chartMoneyPerHour), 'Revenue / Hour', this.state.series.moneyPerHour],
      ['tripsPerHour', ctx(this.chartTripsPerHour), 'Trips / Hour', this.state.series.tripsPerHour],
      ['avgSupplyHoursPerDriver', ctx(this.chartAvgSupply), 'SH per Active Driver', this.state.series.avgSupplyHoursPerDriver],
      // commented canvases may return null and will be skipped:
      ['utilisation', ctx(this.chartUtilisation), 'Utilisation %', this.state.series.utilisation],
      ['efficiency', ctx(this.chartEfficiency), 'Efficiency %', this.state.series.efficiency],
      ['acceptanceRate', ctx(this.chartAcceptance), 'Acceptance Rate %', this.state.series.acceptanceRate],
      ['completedToRequest', ctx(this.chartCompletedToRequest), 'Completed to Request %', this.state.series.completedToRequest],
      ['completionRate', ctx(this.chartCompletionRate), 'Completion Rate %', this.state.series.completionRate],
      ['cancelledByDriverPct', ctx(this.chartCancelledByDriver), 'Cancelled by Driver %', this.state.series.cancelledByDriverPct],
      ['serviceFee', ctx(this.chartServiceFee), 'Service Fee', this.state.series.serviceFee],
      ['partnerFee', ctx(this.chartPartnerFee), 'Partner Fee', this.state.series.partnerFee],
    ];

    // Create charts only when the canvas exists
    for (const [key, context, label, data] of charts) {
      if (!context) continue; // canvas not in DOM (e.g., commented out)
      this._charts[key] = new Chart(context, cfg(label, data));
    }
  }

  toggleFilter(listName, value) {
    const list = this.state[listName];
    const idx = list.indexOf(value);
    if (idx === -1) list.push(value);
    else            list.splice(idx, 1);
    this.state.page = 1;
    this._fetchData();
  }
  changePeriod(p) {
    this.state.selectedPeriod = p;
    this.state.showPeriod    = false;
    this.state.page = 1;
    this.state.startDate = '';
    this.state.endDate = '';
    this._fetchData();
  }
  toggleDropdown(f) { this.state[f] = !this.state[f]; }
  toggleView()      { this.state.showCharts = !this.state.showCharts; }
  onStartDateChange(ev) {
    this.state.startDate = ev.target.value;
    this.state.selectedPeriod = 'Custom Range';
    this.state.page = 1;
    if (this.state.startDate && this.state.endDate) this._fetchData();
  }
  onEndDateChange(ev) {
    this.state.endDate = ev.target.value;
    this.state.selectedPeriod = 'Custom Range';
    this.state.page = 1;
    if (this.state.startDate && this.state.endDate) this._fetchData();
  }
  onSearchChange(ev) {
    this.state.search = ev.target.value || '';
    this.state.page = 1;
  }
}

OwlPerformanceDashboard.template = 'owl.OwlPerformanceDashboard';
registry.category('actions')
        .add('owl.performance_dashboard', OwlPerformanceDashboard);
