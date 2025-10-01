/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, useState, useRef } = hooks;
import { registry } from '@web/core/registry';

export class OwlLowPerformersDashboard extends Component {
  // utils
  formatNumber(v, d = 0) {
    const n = Number(v); if (Number.isNaN(n)) return v ?? '';
    return new Intl.NumberFormat('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n);
  }
  digits(s) { return (s || '').replace(/\D/g, ''); }

  renderBarChart(canvas, series, title) {
    if (!canvas || !series) return;
    const labels = series.map(p => p.period);
    const values = series.map(p => p.value);
    // eslint-disable-next-line no-undef
    new window.Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: { labels, datasets: [{ label: title, data: values }] },
        options: { responsive: true, plugins: { legend: { display: false } } }
    });
  }

  setup() {
    this.state = useState({
      loading: true,
      data: {},
      productTypes: [],
      categories: [],
      risks: [{ key: 'High', label: 'High' }, { key: 'Medium', label: 'Medium' }],
      timePeriods: ['This Week','Last Week','Last Month','Last 3 Months'],

      // filters
      selectedProducts: [],
      selectedScores: [],
      selectedCategories: [],   // NOTE: we’ll default to all EXCEPT 'archive' after we fetch categories
      selectedRisks: ['High','Medium'],
      selectedPeriod: 'Last Week',
      startDate: '', endDate: '',
      // UI
      showProducts: false, showScores: false, showCategories: false, showRisks: false, showPeriod: false,
      search: '',
      sortKey: 'name', sortDir: 'asc',
      pageSize: 100, page: 1,

      // modal
      showModal: false, modalDriver: null, showCharts: false,
    });

    this.modalRef = useRef('lp_modal');

    onMounted(async () => {
      const { product_types, categories, risks, time_periods } = await this.env.services.rpc('/fleet_low_performers/filters', {});
      this.state.productTypes = product_types;
      this.state.categories   = categories;
      this.state.risks        = risks || this.state.risks;
      this.state.timePeriods  = time_periods || this.state.timePeriods;
      // default: all categories except 'archive' and 'new'
      const lower = (s) => (s || '').toString().trim().toLowerCase();
      this.state.selectedCategories = categories.filter(c => {
        const x = lower(c);
        return x !== 'archive' && x !== 'new';
      });
      await this._fetchData();
    });

    onPatched(() => {
        if (this.state.showModal && this.state.showCharts && this.state.modalDriver?.series) {
            const s = this.state.modalDriver.series;
            this.renderBarChart(this.el.querySelector('#lp_chart_cash'),   s.cashEarned,      'Gross Revenue');
            this.renderBarChart(this.el.querySelector('#lp_chart_trips'),  s.trips,           'Trips');
            this.renderBarChart(this.el.querySelector('#lp_chart_hours'),  s.supplyHours,     'Hours Online');
            this.renderBarChart(this.el.querySelector('#lp_chart_acc'),    s.acceptanceRate,  'Acceptance Rate %');
            this.renderBarChart(this.el.querySelector('#lp_chart_comp'),   s.completionRate,  'Completion Rate %');
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
      risks:      this.state.selectedRisks,
      start_date: this.state.startDate || undefined,
      end_date:   this.state.endDate   || undefined,
    };
    const resp = await this.env.services.rpc('/fleet_low_performers/data', params);
    this.state.data    = resp.data || {};
    this.state.meta    = resp.meta || null;
    this.state.loading = false;
  }

  // sorting & paging (same style as perf dash)
  _compareByKey(a, b, key) {
    const ax = a?.[key], bx = b?.[key];
    const norm = (v) => (v === undefined || v === null) ? null : (typeof v === 'boolean' ? (v ? 1 : 0) : (typeof v === 'number' ? v : (typeof v === 'string' ? v.trim() : v)));
    let av = norm(ax), bv = norm(bx);
    if (av === null && bv === null) return 0; if (av === null) return 1; if (bv === null) return -1;
    if (key === 'hire_date' || (/^\d{4}-\d{2}-\d{2}/.test(String(av)) && /^\d{4}-\d{2}-\d{2}/.test(String(bv)))) {
      const ta = Date.parse(av), tb = Date.parse(bv); if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb;
    }
    if (typeof av === 'number' && typeof bv === 'number') return av - bv;
    if (typeof av === 'number' && typeof bv !== 'number') return -1;
    if (typeof av !== 'number' && typeof bv === 'number') return 1;
    return String(av).toLocaleLowerCase().localeCompare(String(bv).toLocaleLowerCase(), undefined, { numeric: true, sensitivity: 'base' });
  }
  setSort(key) {
    if (this.state.sortKey === key) this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc';
    else { this.state.sortKey = key; this.state.sortDir = 'asc'; }
    this.state.page = 1;
  }

  sortedFilteredDrivers() {
    const q = (this.state.search || '').trim().toLowerCase();
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
  totalDriverCount() { return this.sortedFilteredDrivers().length; }
  pageCount() { return Math.max(1, Math.ceil(this.totalDriverCount() / this.state.pageSize)); }
  goToPage(p) { const pc = this.pageCount(); this.state.page = Math.min(pc, Math.max(1, Number(p) || 1)); }
  prevPage = () => this.goToPage(this.state.page - 1);
  nextPage = () => this.goToPage(this.state.page + 1);
  pageWindow() {
    const pc = this.pageCount(), p = this.state.page;
    const nums = new Set([1, pc, p-2, p-1, p, p+1, p+2].filter(x => x >= 1 && x <= pc));
    const arr = [...nums].sort((a,b)=>a-b), out=[];
    for (let i=0;i<arr.length;i++){ out.push(arr[i]); if (i < arr.length-1 && arr[i+1] !== arr[i]+1) out.push('…'); }
    return out;
  }
  pagedDrivers() {
    const rows = this.sortedFilteredDrivers();
    const pc = Math.max(1, Math.ceil(rows.length / this.state.pageSize));
    if (this.state.page > pc) this.state.page = pc;
    const start = (this.state.page - 1) * this.state.pageSize;
    return rows.slice(start, start + this.state.pageSize);
  }

  // filter controls
  toggleDropdown(k) { this.state[k] = !this.state[k]; }
  toggleFilter(listName, value) {
    const list = this.state[listName];
    const i = list.indexOf(value); if (i === -1) list.push(value); else list.splice(i, 1);
    this.state.page = 1; this._fetchData();
  }
  changePeriod(p) { this.state.selectedPeriod = p; this.state.showPeriod = false; this.state.page = 1; this._fetchData(); }
  onStartDateChange(ev) { this.state.startDate = ev.target.value; this.state.page = 1; if (this.state.startDate && this.state.endDate) this._fetchData(); }
  onEndDateChange(ev) { this.state.endDate = ev.target.value; this.state.page = 1; if (this.state.startDate && this.state.endDate) this._fetchData(); }
  onSearchChange(ev) { this.state.search = ev.target.value || ''; this.state.page = 1; }

  // modal
  async openDriver(d) {
    this.state.modalDriver = { ...d, cards: null, series: null, issues: [] };
    this.state.showModal = true;
    this.state.showCharts = false;

    // fetch per-driver detail for the current table window from meta
    const meta = this.state.meta || {}; // we’ll store this on fetch
    const start = meta.table_from, end = meta.table_to;
    const res = await this.env.services.rpc('/fleet_low_performers/driver_detail', {
        driver_id: d.id, start_date: start, end_date: end,
    });
    this.state.modalDriver.cards  = res.cards || null;
    this.state.modalDriver.series = res.series || null;
    this.state.modalDriver.issues = res.issues || [];
  }
  closeModal() { this.state.showModal = false; this.state.modalDriver = null; }
  toggleModalView() { this.state.showCharts = !this.state.showCharts; }
}

OwlLowPerformersDashboard.template = 'owl.OwlLowPerformersDashboard';
registry.category('actions').add('owl.low_performers_dashboard', OwlLowPerformersDashboard);
